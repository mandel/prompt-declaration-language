import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .pdl_ast import PDLException, PdlLocationType, Program, empty_block_location
from .pdl_diagnostics import (
    ORIGIN_PROGRAM,
    Diagnostic,
    source_read_diagnostic,
    undecodable_diagnostic,
    yaml_diagnostic,
)
from .pdl_location_utils import (
    UNNAMED_SOURCE,
    is_unnamed,
    load_with_marks,
    program_location,
    register_source,
)
from .pdl_schema_error_analyzer import analyze_errors


class PDLParseError(PDLException):
    pass


# Reading and YAML-parsing a PDL source is the parser's boundary, so the catch
# belongs here rather than in `main`: `parse_file` is reached from the `pdl`
# CLI, `pdl-infer`, `pdl-lint`, the SDK's `exec_file` and `include:`, and every
# one of those already handles `PDLParseError`.
#
# One small class per concrete errno, rather than one shared `OSError` subclass.
# It costs three declarations and it is the whole reason this is not an SDK
# break: a subclass of `OSError` is *not* a subclass of `FileNotFoundError`, so
# a shared class would silently stop `except FileNotFoundError` from matching
# around `exec_file`. Inheriting the specific errno class instead keeps
# `except FileNotFoundError`, `except IsADirectoryError`, `except
# PermissionError`, `except OSError` and `except PDLParseError` all matching,
# and callers additionally gain `.message` and `.diagnostic`.
# `tests/test_parse_errors.py::test_shims_keep_every_except_clause_matching`
# pins that, because nothing else does.
#
# Any *other* `OSError` is deliberately re-raised untouched (see `parse_file`).
#
# `UnicodeDecodeError` is the one exception to the shim rule, and the one
# documented SDK break in this group: it cannot be subclassed usefully, because
# its constructor requires exactly five arguments, so no shim can both carry a
# PDL message and stay catchable as a `UnicodeDecodeError`. INVENTORY.md 7.1
# decided to raise `PDLUnicodeDecodeError` anyway and take the break, because a
# decode failure is the one entry here whose diagnostic gain -- a real line,
# column, excerpt and caret -- is largest. The decode payload is carried across
# so the caught object is still usable; see the class.


class PDLLocatedParseError(PDLParseError):
    """A `PDLParseError` that carries the structured record behind its text."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__([diagnostic.text])
        self.diagnostic = diagnostic

    def __str__(self) -> str:
        """Render the diagnostic, not the message list.

        `PDLParseError.message` is a `list[str]`, so the inherited `__str__`
        gives a bracketed, quoted, backslash-escaped list repr. That is the same
        defect `.text` exists to fix at the CLI sites, and an embedder calling
        `print(exc)` or `logging.exception(...)` hits it just as squarely.
        """
        return self.diagnostic.text


class PDLOSParseError(PDLLocatedParseError, OSError):
    """Base for the errno shims: a located parse error that is also an `OSError`.

    Exists so the shim table can be typed as carrying both halves. Without it,
    the second element is a `type[PDLLocatedParseError]` and copying `errno`
    onto it does not type-check, even though every member really is an
    `OSError`.
    """


# The three shims below each report 8 ancestors against pylint's limit of 7.
# That depth is the design, not an accident: `PDLException` -> `PDLParseError`
# -> `PDLLocatedParseError` -> `PDLOSParseError` is the PDL half, and
# `FileNotFoundError` -> `OSError` is the half that keeps `except
# FileNotFoundError` matching. Neither half can be shortened without giving up
# something the SDK contract depends on.
class PDLFileNotFoundError(  # pylint: disable=too-many-ancestors
    PDLOSParseError, FileNotFoundError
):
    """`open` failed with ENOENT. Also a `FileNotFoundError`, on purpose."""


class PDLIsADirectoryError(  # pylint: disable=too-many-ancestors
    PDLOSParseError, IsADirectoryError
):
    """`open` failed with EISDIR. Also an `IsADirectoryError`, on purpose."""


class PDLPermissionError(  # pylint: disable=too-many-ancestors
    PDLOSParseError, PermissionError
):
    """`open` failed with EACCES. Also a `PermissionError`, on purpose.

    Needed for more than the obvious case: Windows raises `PermissionError` for
    `open()` on a directory, which is why the triage in `source_read_diagnostic`
    classifies on `Path.is_dir()` rather than on the errno.
    """


class PDLUnicodeDecodeError(PDLLocatedParseError):
    """The source file is not UTF-8. **Not** a `UnicodeDecodeError`.

    It cannot be one: `UnicodeDecodeError.__init__` requires exactly five
    arguments, so a subclass carrying a PDL message cannot be constructed at
    all. `except UnicodeDecodeError` around `exec_file`/`parse_file` therefore
    stops matching -- the single deliberate SDK break of the boundary work,
    decided in `docs/error-reporting/INVENTORY.md` 7.1 and written up in
    `docs/release-notes.md`.

    Matching the class is only half of a migration, so the decode data is
    carried across verbatim: `encoding`, `object`, `start`, `end` and `reason`
    all read as they would on the exception this replaces, and
    `except PDLParseError as e: e.start` is the mechanical rewrite. The one
    difference is an improvement: whenever the file could be re-read,
    `start`/`end` index the **file**, rather than whatever the decoder happened
    to be handed (see `undecodable_source_error`). The exception the codec
    itself raised stays reachable through `__cause__`.
    """

    def __init__(self, diagnostic: Diagnostic, exc: UnicodeDecodeError):
        super().__init__(diagnostic)
        self.encoding = exc.encoding
        self.object = exc.object
        self.start = exc.start
        self.end = exc.end
        self.reason = exc.reason


class PDLYamlError(PDLLocatedParseError, yaml.YAMLError):
    """`yaml.safe_load` failed. Also a `yaml.YAMLError`, on purpose.

    Not a `MarkedYAMLError`: a caller narrow enough to catch that specifically
    stops matching. `except yaml.YAMLError` is the far more common clause and
    keeps working.
    """


SHIMMED_OS_ERRORS: tuple[tuple[type[OSError], type[PDLOSParseError]], ...] = (
    (FileNotFoundError, PDLFileNotFoundError),
    (IsADirectoryError, PDLIsADirectoryError),
    (PermissionError, PDLPermissionError),
)


def parse_file(pdl_file: str | Path) -> tuple[Program, PdlLocationType]:
    try:
        with open(pdl_file, "r", encoding="utf-8") as pdl_fp:
            prog_str = pdl_fp.read()
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        raise source_read_error(Path(pdl_file), exc) from exc
    except UnicodeDecodeError as exc:
        raise undecodable_source_error(Path(pdl_file), exc) from exc
    # Every other `OSError` propagates unchanged. Narrowing a shim to each
    # concrete errno is what makes those additive, and there is no honest way to
    # extend that to an open set of errno classes without breaking
    # `except <SpecificError>` for the ones not named above.
    return parse_str(prog_str, file_name=str(pdl_file))


def source_read_error(
    path: Path, exc: OSError, *, data_file: bool = False
) -> PDLOSParseError:
    """Wrap a failed `open` in the shim matching its own errno."""
    diagnostic = source_read_diagnostic(path, exc, data_file=data_file)
    # The shim is chosen from the type of the exception actually raised, never
    # from the branch the diagnostic took: on Windows a directory raises
    # `PermissionError`, and it must stay catchable as one.
    for cls, shim in SHIMMED_OS_ERRORS:
        if isinstance(exc, cls):
            wrapped = shim(diagnostic)
            # Keeping the class catchable is not enough: `except OSError as e`
            # reaching for `e.errno`, `e.strerror` or `e.filename` would
            # otherwise find `None`, because `OSError.__init__` never ran with
            # the original arguments. Carry the payload across so the caught
            # object is a drop-in for the one it replaces.
            wrapped.errno = exc.errno
            wrapped.strerror = exc.strerror
            wrapped.filename = exc.filename
            wrapped.filename2 = exc.filename2
            return wrapped
    raise exc  # pragma: no cover - `parse_file` catches only the three above


def undecodable_source_error(
    path: Path, exc: UnicodeDecodeError
) -> PDLUnicodeDecodeError:
    """Wrap a decode failure, recomputing its position from the raw bytes.

    `exc.start` is an offset into whatever the decoder was handed, which
    through a `TextIOWrapper` is not promised to be the whole file. `read()` on
    a fresh handle decodes in one piece today, so the number happens to be a
    file offset; reading the same file line by line reports an offset thousands
    of bytes short. That is an implementation detail of `TextIOWrapper`, and a
    reported line should not rest on one -- a location that is silently wrong is
    worse than no location, and this is cheap to compute honestly.

    So the file is read again as bytes and decoded again in one piece, which
    raises the same failure with an offset that is a file offset by
    construction, and which yields the bytes the excerpt is built from. That is
    one extra read, on the failure path only. If the second read does not
    reproduce the failure -- the file was deleted, or replaced between the two
    reads -- the original exception is kept and no position is claimed.
    """
    payload = exc
    raw: bytes | None
    try:
        raw = path.read_bytes()
    except OSError:  # pragma: no cover - readable a moment ago, gone now
        raw = None
    if raw is not None:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as file_exc:
            payload = file_exc
        else:  # pragma: no cover - the file changed between the two reads
            raw = None
    diagnostic = undecodable_diagnostic(
        str(path),
        raw,
        payload.start if raw is not None else None,
        payload.end,
        payload.reason,
    )
    return PDLUnicodeDecodeError(diagnostic, payload)


def yaml_error(  # pylint: disable=too-many-arguments
    exc: yaml.YAMLError,
    source: str,
    file_name: str,
    *,
    origin: str = ORIGIN_PROGRAM,
    program: str | None = None,
    code: str = "E-PARSE-001",
) -> PDLYamlError:
    """Wrap a PyYAML failure, and give its marks the real filename.

    The renderer builds the header from `file_name` and the excerpt from
    `source`, so it never reads `mark.name`. Setting the name anyway costs two
    lines and makes `str(exc)` correct for any SDK caller, for `pdl-lint`, and
    for anything that logs `__cause__`, all of which otherwise say
    `"<unicode string>"` -- the one label guaranteed not to tell the user which
    of their inputs is broken. Wrapping the source in a named `io.StringIO` would
    also fix the name, but the stream path reads in chunks and truncates
    `mark.buffer`, which is exactly the excerpt data.
    """
    if isinstance(exc, yaml.MarkedYAMLError):
        for mark in (exc.problem_mark, exc.context_mark):
            if mark is not None:
                mark.name = file_name
    return PDLYamlError(
        yaml_diagnostic(
            exc,
            source,
            origin=origin,
            file=file_name,
            program=program,
            code=code,
        )
    )


@lru_cache(maxsize=128)
def parse_str(
    pdl_str: str, file_name: str | None = None
) -> tuple[Program, PdlLocationType]:
    if file_name is None:
        file_name = UNNAMED_SOURCE
    try:
        prog_dict, marks = load_with_marks(pdl_str)
    except yaml.YAMLError as exc:
        raise yaml_error(exc, pdl_str, file_name or "<program>") from exc
    # The source is registered before anything can fail on it: `parse_dict`
    # reports schema errors through `append`, which resolves against exactly
    # this entry, and a diagnostic about a file PDL could not find its text for
    # is the failure mode this registry exists to remove.
    register_source(file_name, pdl_str, marks)
    loc = program_location(file_name, marks)
    prog = parse_dict(prog_dict, loc)
    return prog, loc


def parse_dict(pdl_dict: dict[str, Any], loc: PdlLocationType | None = None) -> Program:
    try:
        prog = Program.model_validate(pdl_dict)
        # set_program_location(prog, pdl_str)
    except ValidationError as exc:
        pdl_schema_file = Path(__file__).parent / "pdl-schema.json"
        with open(pdl_schema_file, "r", encoding="utf-8") as schema_fp:
            schema = json.load(schema_fp)
        defs = schema["$defs"]
        if loc is None:
            loc = empty_block_location
        errors = analyze_errors(defs, defs["Program"], pdl_dict, loc)
        if errors == []:
            # `<program>` is a display name, not a file name: a fallback reading
            # `The file PDL <program> does not respect the schema.` would invite
            # the user to go and look for it.
            if is_unnamed(loc.file):
                errors = ["The PDL program does not respect the schema."]
            else:
                errors = [f"The file PDL {loc.file} does not respect the schema."]
        raise PDLParseError(errors) from exc
    return prog


# def set_program_location(prog: Program, pdl_str: str, file_name: str = ""):
#     loc = strictyaml.dirty_load(pdl_str, allow_flow_style=True)
#     set_location(prog.root, loc)


# def set_location(
#     pdl: Any,
#     loc: YamlSource,
# ):
#     if hasattr(pdl, "pdl_yaml_src"):
#         pdl.pdl_yaml_src = loc
#     if isinstance(loc.data, dict):
#         for x, v in loc.items():
#             if hasattr(pdl, x.data):
#                 set_location(getattr(pdl, x.data), v)
#     elif isinstance(pdl, list) and isinstance(loc.data, list):
#         for data_i, loc_i in zip(pdl, loc):
#             set_location(data_i, loc_i)


# def set_program_location(prog: Program, pdl_str: str, file_name: str = ""):
#     line_table = get_line_map(pdl_str)
#     loc = LocationType(path=[], file=file_name, table=line_table)
#     return Program(set_blocks_location(prog.root, loc))

# def set_blocks_location(
#     blocks: BlocksType,
#     loc: YAML,
# ):
#     if is_block_list(blocks):
#         return [set_block_location(block, append(loc, f"[{i}]")) for i, block in enumerate(blocks)]
#     return set_block_location(blocks, loc)


# def set_block_location(
#     block: BlocksType,
#     loc: LocationType,
# ):
#     if not isinstance(block, Block):
#         return DataBlock(data=block, location=loc)
#     block = block.model_copy(update={"location": loc})
#     defs_loc = append(loc, "defs")
#     block.defs = {x: set_block_location(b, append(defs_loc, x)) for x, b in block.defs }
#     if block.parser is not None:
#         block.parser = set_parser_location(block.parser)
#     if block.fallback is not None:
#         block.fallback = set_block_location(block.fallback, append(loc, "fallback"))
#     match block:
#         case FunctionBlock():
#             block.return_ = set_blocks_location(block.return_, append(loc, "return"))
#         case CallBlock():
#             block.args = {x: set_expr_location(expr) for x, expr in block.args.items()}
#         case ModelBlock():
#             if block.input is not None:
#                 iter_blocks(f, block.input)
#         case CodeBlock():
#             iter_blocks(f, block.code)
#         case GetBlock():
#             pass
#         case DataBlock():
#             pass
#         case TextBlock():
#             iter_blocks(f, block.text)
#         case LastOfBlock():
#             iter_blocks(f, block.lastOf)
#         case ArrayBlock():
#             iter_blocks(f, block.array)
#         case ObjectBlock():
#             if isinstance(block.object, dict):
#                 body = list(block.object.values())
#             else:
#                 body = block.object
#             iter_blocks(f, body)
#         case MessageBlock():
#             iter_blocks(f, block.content)
#         case IfBlock():
#             iter_blocks(f, block.then)
#             if block.else_ is not None:
#                 iter_blocks(f, block.else_)
#         case RepeatBlock():
#             iter_blocks(f, block.repeat)
#             if block.pdl__trace is not None:
#                 for trace in block.pdl__trace:
#                     iter_blocks(f, trace)
#         case MapBlock():
#             iter_blocks(f, block.map)
#             if block.pdl__trace is not None:
#                 for trace in block.pdl__trace:
#                     iter_blocks(f, trace)
#         case ErrorBlock():
#             iter_blocks(f, block.program)
#         case ReadBlock():
#             pass
#         case IncludeBlock():
#             if block.pdl__trace is not None:
#                 iter_blocks(f, block.pdl__trace)
#         case EmptyBlock():
#             pass
#         case _:
#             assert (
#                 False
#             ), f"Internal error (missing case iter_block_children({type(block)}))"
#     match (block.parser):
#         case "json" | "yaml" | RegexParser():
#             pass
#         case PdlParser():
#             iter_blocks(f, block.parser.pdl)
#     if block.fallback is not None:
#         iter_blocks(f, block.fallback)
