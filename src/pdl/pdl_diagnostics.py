"""Structured diagnostics and their rendering.

A diagnostic is a small document, not a sentence: an origin, a location, a claim,
the evidence for the claim, and a next action. `Diagnostic` is that document as
data; `render` turns it into the text a user sees. Keeping the two apart is what
lets the same record feed the CLI today and a machine-readable channel later
(decision 5.6).

The rendering contract, from `docs/error-reporting/specs/E-BOUNDARY.md`:

* Line 1 is ``<origin>:<line>:<col> - <message>``, once. ``:col`` is dropped when
  unknown, ``:line`` too, and the whole prefix is dropped when the diagnostic is
  *about* the origin rather than *inside* it (a missing file names its path in
  the message; a ``no_such_file.pdl - `` prefix would be noise).
* ``<origin>`` is the display token: ``prog.pdl`` for a program, the path for a
  ``-f`` data file, and the literal ``--data`` for a ``-d`` argument. Anything
  that is not a file cannot be mistaken for one.
* An optional ``  in <path>`` line carries the block/scope path.
* Excerpt lines are ``<lineno> | <source>``; annotation lines are ``  | `` plus
  spaces plus ``^`` plus an optional label. Non-adjacent lines are elided with
  ``...``. Tabs are rendered as a single space so caret arithmetic stays trivial,
  and a message about a tab therefore names it in words.
* The rule paragraph is indented two spaces with no prefix; ``  note:`` carries
  context and ``  help:`` the action, with continuations aligned under the text.
* No ANSI, no absolute paths, no severity token.

This module imports nothing from PDL, so it cannot participate in an import
cycle with the parser it serves.
"""

from __future__ import annotations

import difflib
import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

WIDTH = 78
"""Wrap column for prose. Excerpt and suggestion *code* are never wrapped: a
broken command line is worse than a long one."""

EXCERPT_MAX = 75
"""Characters of source shown around a caret, matching PyYAML's own
``Mark.get_snippet``. A ``-d`` argument can be arbitrarily long and must not
become a wall of text."""

ORIGIN_PROGRAM = "program"
ORIGIN_ARGUMENT = "argument"
ORIGIN_DATA_FILE = "data-file"


@dataclass(frozen=True)
class Span:
    """One annotated point in the source. Lines and columns are 1-based."""

    line: int
    col: int | None = None
    end_line: int | None = None
    end_col: int | None = None
    label: str = ""
    primary: bool = False


@dataclass(frozen=True)
class Note:
    """``kind`` is ``"rule"`` for the paragraph, ``"note"`` for a ``note:`` line."""

    kind: str
    text: str


@dataclass(frozen=True)
class Suggestion:
    """``replacement`` is literal text the user can type; it is never wrapped."""

    text: str
    replacement: str = ""


@dataclass
class Diagnostic:  # pylint: disable=too-many-instance-attributes
    """One rendered-once diagnostic. A data container, so the field count is the point."""

    code: str
    """Taxonomy ID. Carried and not rendered: no diagnostic-ID registry exists
    yet, and inventing one here would fix its shape before the registry owns it."""
    message: str
    origin: str = ORIGIN_PROGRAM
    file: str = ""
    severity: str = "error"
    spans: list[Span] = field(default_factory=list)
    block_path: list[str] | None = None
    notes: list[Note] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    source: str | None = None
    """The text the spans point into. Not part of the record."""
    show_location: bool = True
    """False when the diagnostic is about the origin rather than inside it."""

    @property
    def text(self) -> str:
        return render(self)

    def as_record(self) -> dict[str, Any]:
        """The structured form of decision 5.6. Nothing consumes it yet."""
        primary = _primary(self)
        return {
            "id": self.code,
            "severity": self.severity,
            "origin": self.origin,
            "file": self.file,
            "span": _span_record(primary) if primary else None,
            "spans": [_span_record(s) for s in self.spans],
            "block_path": list(self.block_path) if self.block_path else None,
            "message": self.message,
            "notes": [{"kind": n.kind, "text": n.text} for n in self.notes],
            "suggestions": [
                {"text": s.text, "replacement": s.replacement or None}
                for s in self.suggestions
            ],
        }


def _span_record(span: Span) -> dict[str, Any]:
    return {
        "line": span.line,
        "col": span.col,
        "end_line": span.end_line,
        "end_col": span.end_col,
        "label": span.label or None,
        "primary": span.primary,
    }


def _primary(diag: Diagnostic) -> Span | None:
    for span in diag.spans:
        if span.primary:
            return span
    return diag.spans[0] if diag.spans else None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render(diag: Diagnostic) -> str:
    """Render a diagnostic as the text the user sees. No trailing newline."""
    lines: list[str] = [_header(diag)]
    if diag.block_path:
        lines.append("  in " + join_path(diag.block_path))

    excerpt = _excerpt(diag)
    if excerpt:
        lines.append("")
        lines.extend(excerpt)

    for note in diag.notes:
        if note.kind == "rule":
            lines.append("")
            lines.extend(_wrap(note.text, "  ", "  "))

    tail = [n for n in diag.notes if n.kind != "rule"]
    if tail or diag.suggestions:
        lines.append("")
        for note in tail:
            lines.extend(_wrap(note.text, "  note: ", " " * 8))
        for suggestion in diag.suggestions:
            lines.extend(_suggestion_lines(suggestion))
    return "\n".join(lines)


def _header(diag: Diagnostic) -> str:
    if not diag.show_location or not diag.file:
        return diag.message
    prefix = diag.file
    primary = _primary(diag)
    if primary is not None:
        prefix += f":{primary.line}"
        if primary.col is not None:
            prefix += f":{primary.col}"
    return f"{prefix} - {diag.message}"


def join_path(path: Sequence[str]) -> str:
    """Render a block/scope path the way `pdl_location_utils` does."""
    out = ""
    for segment in path:
        if out and not segment.startswith("["):
            out += "."
        out += segment
    return out


def _wrap(text: str, initial: str, subsequent: str) -> list[str]:
    if not text:
        return []
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=initial,
        subsequent_indent=subsequent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _suggestion_lines(suggestion: Suggestion) -> list[str]:
    single = suggestion.text
    if suggestion.replacement:
        single = f"{single} {suggestion.replacement}".strip()
    if len(single) + len("  help: ") <= WIDTH:
        return ["  help: " + single]
    lines = _wrap(suggestion.text, "  help: ", " " * 8)
    if suggestion.replacement:
        lines.append(" " * 8 + suggestion.replacement)
    return lines


def _clip(line: str, col: int | None) -> tuple[str, int | None]:
    """Trim a long source line to a window around the caret, as PyYAML does."""
    if len(line) <= EXCERPT_MAX:
        return line, col
    anchor = (col or 1) - 1
    start = max(0, anchor - EXCERPT_MAX // 2)
    end = min(len(line), start + EXCERPT_MAX)
    start = max(0, end - EXCERPT_MAX)
    clipped = line[start:end]
    new_col = None if col is None else col - start
    if start > 0:
        clipped = " ... " + clipped
        if new_col is not None:
            new_col += 5
    if end < len(line):
        clipped = clipped + " ... "
    return clipped, new_col


def _excerpt(diag: Diagnostic) -> list[str]:
    """Source lines plus caret lines, in source order, at most two annotated."""
    if diag.source is None or not diag.spans:
        return []
    src = diag.source.split("\n")
    seen: set[tuple[int, int | None]] = set()
    spans: list[Span] = []
    for span in sorted(diag.spans, key=lambda s: (s.line, s.col or 0)):
        if not 1 <= span.line <= len(src):
            continue
        if (span.line, span.col) in seen:
            continue
        seen.add((span.line, span.col))
        spans.append(span)
    spans = spans[:2]
    if not spans:
        return []

    gutter = max(len(str(s.line)) for s in spans)
    out: list[str] = []
    previous: int | None = None
    for span in spans:
        if previous is not None and span.line > previous + 1:
            out.append("...")
        text, col = _clip(src[span.line - 1].replace("\t", " "), span.col)
        out.append(f"{str(span.line).rjust(gutter)} | {text}".rstrip())
        if col is not None and col >= 1:
            caret = " " * gutter + " | " + " " * (col - 1) + "^"
            if span.label:
                caret += " " + span.label
            out.append(caret.rstrip())
        previous = span.line
    return out


# --------------------------------------------------------------------------
# Reading a source file: E-CLI-001, E-CLI-002
# --------------------------------------------------------------------------

_PROGRAM_RULE = "`pdl` takes the path of a PDL program file."
_DATA_FILE_RULE = "`-f` takes the path of a YAML file of initial scope values."
_CHECK_PATH = "check the path, or run `pdl --help` for the expected arguments."
_CHECK_DATA_PATH = "check the path given to `-f`."


def _dir_listing(paths: Sequence[Path], limit: int = 3) -> str:
    """``\\`a.pdl\\`, \\`b.pdl\\`, and 4 more``. Always from a sorted sequence."""
    shown = ", ".join(f"`{p.name}`" for p in paths[:limit])
    if len(paths) > limit:
        shown += f", and {len(paths) - limit} more"
    return shown


def _pdl_files(directory: Path) -> list[Path]:
    try:
        return sorted(directory.glob("*.pdl"))
    except OSError:  # pragma: no cover - unreadable directory
        return []


def _near_miss(path: Path) -> str | None:
    """A sibling filename the user plausibly meant, or None.

    Two branches, cheapest first: a missing `.pdl` suffix, then a close spelling.
    `difflib` is stdlib and the candidate list comes from `sorted(iterdir())`, so
    the answer cannot vary between runs.
    """
    parent = path.parent
    if not path.suffix:
        candidate = parent / (path.name + ".pdl")
        if candidate.is_file():
            return str(candidate)
    try:
        names = sorted(p.name for p in parent.iterdir())
    except OSError:
        return None
    matches = difflib.get_close_matches(path.name, names, n=1, cutoff=0.7)
    if matches:
        return str(parent / matches[0])
    return None


def _directory_phrase(directory: Path) -> tuple[str, str]:
    """(lower-case, capitalised) ways to name a directory in prose."""
    if directory in (Path("."), Path("")):
        return "the current directory", "The directory"
    return f"`{directory}/`", f"The directory `{directory}/`"


def missing_file_diagnostic(path: Path, *, data_file: bool = False) -> Diagnostic:
    """E-CLI-001. The path itself is the subject, so it carries no location prefix."""
    display = str(path)
    parent = path.parent
    lower, capital = _directory_phrase(parent)

    suggestion = Suggestion(_CHECK_DATA_PATH if data_file else _CHECK_PATH)
    rule = _DATA_FILE_RULE if data_file else _PROGRAM_RULE

    guess = None if data_file else _near_miss(path)
    if guess is not None:
        evidence = "Nothing exists at that path."
        suggestion = Suggestion(f"did you mean `{guess}`?")
    elif not parent.exists():
        evidence = f"The directory `{parent}/` does not exist either."
    elif data_file:
        evidence = "Nothing exists at that path."
    else:
        siblings = _pdl_files(parent)
        if siblings:
            evidence = f"{capital} contains {_dir_listing(siblings)}."
        else:
            evidence = (
                f"Nothing exists at that path, and {lower} contains no `.pdl` files."
            )

    return Diagnostic(
        code="E-CLI-001",
        message=f"cannot read `{display}`: no such file",
        origin=ORIGIN_DATA_FILE if data_file else ORIGIN_PROGRAM,
        file=display,
        show_location=False,
        notes=[Note("rule", f"{rule} {evidence}")],
        suggestions=[suggestion],
    )


def directory_diagnostic(path: Path, *, data_file: bool = False) -> Diagnostic:
    """E-CLI-002. The next action is what is *inside* the directory, not a name."""
    display = str(path)
    inside = _pdl_files(path)
    if data_file:
        evidence = "A directory cannot be read as scope data."
        suggestion = Suggestion(_CHECK_DATA_PATH)
    elif len(inside) == 1:
        evidence = f"`{display}` contains one PDL program."
        suggestion = Suggestion(f"did you mean `pdl {inside[0]}`?")
    elif inside:
        evidence = f"`{display}` contains {_dir_listing(inside)}."
        suggestion = Suggestion(f"name one of them, e.g. `pdl {inside[0]}`.")
    else:
        evidence = f"`{display}` contains no `.pdl` files."
        # A placeholder, not a path inside `display`: the branch above already
        # established there is no program there, so naming one would be a
        # copy-pasteable command that cannot work.
        suggestion = Suggestion(
            "give the path of a program file, e.g. `pdl path/to/program.pdl`."
        )
    rule = (
        _DATA_FILE_RULE
        if data_file
        else "`pdl` takes the path of one PDL program file, usually with a `.pdl` suffix."
    )
    return Diagnostic(
        code="E-CLI-002",
        message=f"cannot read `{display}`: it is a directory, not a "
        + ("YAML data file" if data_file else "PDL program file"),
        origin=ORIGIN_DATA_FILE if data_file else ORIGIN_PROGRAM,
        file=display,
        show_location=False,
        notes=[Note("rule", f"{rule} {evidence}")],
        suggestions=[suggestion],
    )


def permission_diagnostic(path: Path, *, data_file: bool = False) -> Diagnostic:
    """The third shimmed errno. Nothing about the file's contents is knowable."""
    display = str(path)
    rule = _DATA_FILE_RULE if data_file else _PROGRAM_RULE
    return Diagnostic(
        code="E-CLI-006",
        message=f"cannot read `{display}`: permission denied",
        origin=ORIGIN_DATA_FILE if data_file else ORIGIN_PROGRAM,
        file=display,
        show_location=False,
        notes=[Note("rule", f"{rule} The file exists, but this user cannot read it.")],
        suggestions=[
            Suggestion(f"check the file's permissions, e.g. `ls -l {display}`.")
        ],
    )


def source_read_diagnostic(
    path: Path, exc: OSError, *, data_file: bool = False
) -> Diagnostic:
    """Triage a failed `open`.

    Classification is on `Path.is_dir()` rather than on `errno`: Windows raises
    `PermissionError` for `open()` on a directory, and errno-only classification
    would send that user to the permissions branch.
    """
    if path.is_dir():
        return directory_diagnostic(path, data_file=data_file)
    if isinstance(exc, FileNotFoundError):
        return missing_file_diagnostic(path, data_file=data_file)
    return permission_diagnostic(path, data_file=data_file)


# --------------------------------------------------------------------------
# Reading an imported program: E-RUNTIME-002
# --------------------------------------------------------------------------


def _import_form(written: str, candidate: Path) -> str:
    """Name ``candidate`` the way the user writes an ``import:`` path.

    Two things are preserved from what they wrote: the directory part, because
    the candidate was found in the directory their own path pointed at, and
    their choice about the ``.pdl`` suffix, because both forms resolve. The
    suggestion is then a minimal edit rather than a style correction.

    The directory part is taken from the string rather than from
    ``Path(written).parent``, so that it comes back in the user's own spelling
    -- a written `lib/` keeps its separator instead of being normalised away.
    """
    name = candidate.name if written.endswith(".pdl") else candidate.stem
    return written[: written.rfind("/") + 1] + name


def _import_candidates(search_dir: Path, importing_file: Path | None) -> list[Path]:
    """The `.pdl` files in ``search_dir`` an `import:` could plausibly name.

    The importing file is dropped. It is always in the list when the import
    points at the program's own directory, and importing yourself is a cycle,
    never the intended fix -- suggesting it, or counting it towards "the
    directory contains", is worse than saying nothing.
    """
    candidates = _pdl_files(search_dir)
    if importing_file is None:
        return candidates
    try:
        importing = importing_file.resolve()
        return [p for p in candidates if p.resolve() != importing]
    except OSError:  # pragma: no cover - unresolvable path
        return candidates


def _import_rule(written: str, display: str, cwd: Path) -> str:
    """Why the file PDL opened is not the string the user typed.

    Two independent reasons, either, both or neither: the appended ``.pdl``
    suffix and the directory PDL resolves from. Naming only the ones that
    actually apply keeps the paragraph from explaining a transformation that did
    not happen.
    """
    from_cwd = cwd not in (Path("."), Path(""))
    if not written.endswith(".pdl"):
        rule = (
            f"`import: {written}` looks for the file `{display}`: PDL appends "
            "`.pdl` to an import path that does not already end in it."
        )
        if from_cwd:
            rule += f" It is resolved from `{cwd}/`."
        return rule
    if from_cwd:
        return f"`import: {written}` is resolved from `{cwd}/`."
    return "`import:` reads a PDL program from the file it names."


def _import_missing(
    written: str, resolved: Path, cwd: Path, importing: Path | None
) -> tuple[str, str, Suggestion]:
    """Headline, evidence and next action for an `import:` that found nothing.

    Branches are checked in order and the first match wins. Every one of them
    reads ``search_dir``, which is ``resolved.parent`` -- the directory PDL
    actually opened in, so an `import: lib/helper` searches `lib/` and not the
    program's own directory, and nothing here can claim to have looked somewhere
    PDL did not.
    """
    display = str(resolved)
    headline = f"cannot import `{written}`: no such file"
    if display != written:
        headline += f" `{display}`"

    search_dir = resolved.parent
    lower, capital = _directory_phrase(search_dir)
    cwd_lower, _ = _directory_phrase(cwd)
    base_help = Suggestion(f"check the path; it is resolved relative to {cwd_lower}.")

    # 1. The suffix trap: the file the user named is right there, and `import:`
    #    cannot read it. Phrased conditionally, because a rename followed
    #    blindly turns this error into a schema error when the file is data.
    unsuffixed = cwd / written
    if not written.endswith(".pdl") and unsuffixed.is_file():
        evidence = (
            f"`{unsuffixed}` exists, but `import:` reads only files whose names "
            "end in `.pdl`."
        )
        renamed = Path(written).with_suffix(".pdl")
        if Path(written).suffix:
            action = (
                f"if `{unsuffixed}` is a PDL program, rename it to `{renamed}` "
                f"and write `import: {Path(written).with_suffix('')}`."
            )
        else:
            action = f"if `{unsuffixed}` is a PDL program, rename it to `{renamed}`."
        return headline, evidence, Suggestion(action)

    candidates = _import_candidates(search_dir, importing)

    # 2. A near miss, matched on what the user *wrote* rather than on the
    #    suffixed form: scoring `nosuch.pdl` against `helper.pdl` would credit
    #    every candidate with the four characters PDL itself added.
    names = [p.stem for p in candidates]
    matches = difflib.get_close_matches(Path(written).stem, names, n=1, cutoff=0.7)
    if matches:
        best = candidates[names.index(matches[0])]
        return (
            headline,
            "Nothing exists at that path.",
            Suggestion(f"did you mean `import: {_import_form(written, best)}`?"),
        )

    if not search_dir.exists():
        return headline, f"{capital} does not exist.", base_help

    if candidates:
        # The listing is a fact and is always shown. Turning it into "name one of
        # them" is dropped in the one shape where it would misfire: an import
        # inside an *imported* file that resolves back to the top-level
        # program's own directory. The entry-point program is in that directory
        # by construction, it is not the importing file so it is not excluded
        # above, and importing it from a file it imported is a cycle.
        listing = f"{capital} contains {_dir_listing(candidates)}."
        if importing is not None and search_dir == cwd != importing.parent:
            return headline, listing, base_help
        return (
            headline,
            listing,
            Suggestion(
                f"name one of them, e.g. `import: {_import_form(written, candidates[0])}`."
            ),
        )

    return (
        headline,
        f"Nothing exists at that path, and {lower} contains no other `.pdl` files.",
        base_help,
    )


def import_read_diagnostic(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    written: str,
    resolved: Path,
    cwd: Path,
    exc: OSError,
    file: str = "",
    line: int | None = None,
    block_path: Sequence[str] | None = None,
) -> Diagnostic:
    """E-RUNTIME-002. An `import:` inside a program named a file that cannot be read.

    Unlike E-CLI-001 this diagnostic is *inside* a program, at a line, so
    ``file`` and the span are the location of the `import:` and the path that
    could not be read lives in the message. Getting that backwards is what gives
    a diagnostic two stacked claim lines.

    ``written`` is what the user typed and ``resolved`` is what PDL opened; the
    two differ whenever PDL appended `.pdl` or prefixed the program's directory,
    and the whole first paragraph exists to account for that difference.

    Classification is on ``resolved.is_dir()`` rather than on ``errno``, for the
    same reason as `source_read_diagnostic`: Windows raises `PermissionError` for
    `open()` on a directory.
    """
    display = str(resolved)
    importing = Path(file) if file else None
    rule = _import_rule(written, display, cwd)
    cwd_lower, _ = _directory_phrase(cwd)

    if resolved.is_dir():
        headline = f"cannot import `{written}`: `{display}` is a directory, not a file"
        if display == written:
            headline = f"cannot import `{written}`: it is a directory, not a file"
        evidence = "A directory cannot be imported."
        inside = _pdl_files(resolved)
        if inside:
            suggestion = Suggestion(
                "name a program inside it, e.g. "
                f"`import: {Path(written) / inside[0].stem}`."
            )
        else:
            suggestion = Suggestion("give the path of a PDL program file.")
    elif isinstance(exc, FileNotFoundError):
        headline, evidence, suggestion = _import_missing(
            written, resolved, cwd, importing
        )
    elif isinstance(exc, PermissionError):
        headline = f"cannot import `{written}`: permission denied reading `{display}`"
        evidence = "The file exists, but this user cannot read it."
        suggestion = Suggestion(
            f"check the file's permissions, e.g. `ls -l {display}`."
        )
    else:
        detail = exc.strerror or str(exc)
        headline = f"cannot import `{written}`: cannot read `{display}` ({detail})"
        evidence = "The file could not be opened."
        suggestion = Suggestion(
            f"check the path; it is resolved relative to {cwd_lower}."
        )

    notes = [Note("rule", f"{rule} {evidence}")]
    # Said only where the distinction bites. `state.cwd` is bound once, from the
    # top-level program's parent, and never rebound when an import recurses, so
    # an `import:` inside an imported file does *not* resolve from that file's
    # directory (INVENTORY.md 7.4). Claiming otherwise would be confidently
    # wrong, and stating it always would be noise for the single-file case where
    # the two directories are the same.
    if importing is not None and importing.parent != cwd:
        notes.append(
            Note(
                "note",
                f"import paths are resolved from {cwd_lower}, the directory of "
                "the program `pdl` was started with, not from the file that "
                "contains this `import:`.",
            )
        )

    spans = []
    if line is not None and line > 0:
        spans.append(Span(line=line, label=f"no file `{display}`", primary=True))
    return Diagnostic(
        code="E-RUNTIME-002",
        message=headline,
        file=file,
        spans=spans,
        block_path=list(block_path) if block_path else None,
        notes=notes,
        suggestions=[suggestion],
    )


# --------------------------------------------------------------------------
# Decoding a source file: E-PARSE-005
# --------------------------------------------------------------------------

_UTF8_RULE = (
    "A PDL program must be UTF-8 encoded text. This file is not, so it cannot be "
    "read at all."
)
_UTF16_RULE = (
    "A PDL program must be UTF-8 encoded text. This file begins with a UTF-16 "
    "byte-order mark, so it cannot be read at all."
)
_UTF8_HELP = "re-save the file as UTF-8."
_REPLACEMENT_NOTE = (
    "each � in the excerpt above stands for one byte that is not valid "
    "UTF-8; it is not a character in your file."
)

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
"""The two UTF-16 byte-order marks. Both begin with a byte that cannot start a
UTF-8 character, so this branch is reached before anything else in the file
matters -- and it is the one encoding mistake common enough to earn its own
sentence."""


def _decode_headline(raw: bytes, start: int, end: int, reason: str) -> str:
    """Name the offending byte in PDL's words, not the codec's.

    ``reason`` comes from CPython's UTF-8 decoder and its values are a small
    closed set. The byte at ``start`` is the *first* byte of the malformed
    sequence, which is not the same as the byte the decoder choked on: for
    ``invalid continuation byte`` the caret belongs on the character that starts
    the sequence and the culprit is the byte at ``end``.
    """
    lead = raw[start] if start < len(raw) else None
    match reason:
        case "invalid start byte" if lead is not None:
            return f"byte 0x{lead:02x} cannot start a UTF-8 character"
        case "invalid continuation byte" if end < len(raw):
            return (
                f"byte 0x{raw[end]:02x} cannot continue the UTF-8 character "
                "that starts here"
            )
        case "unexpected end of data":
            return "the file ends in the middle of a UTF-8 character"
        case _:
            return f"byte 0x{lead:02x}: {reason}" if lead is not None else reason


def undecodable_diagnostic(
    display: str, raw: bytes | None, start: int | None, end: int, reason: str
) -> Diagnostic:
    """E-PARSE-005. The position is recomputed from the file's own bytes.

    ``UnicodeDecodeError.start`` is an offset into whatever the decoder was
    handed, which through a ``TextIOWrapper`` is not promised to be the whole
    file. The caller re-reads the file and re-decodes it in one piece, so that
    ``raw`` and ``start`` here are a file's bytes and an offset into them by
    construction. Both are ``None`` when the re-read did not reproduce the
    failure, in which case no position is claimed at all.

    The prefix is decoded with ``errors="replace"`` so that the column is exact
    rather than approximate: each undecodable byte becomes exactly one
    ``U+FFFD`` and every decodable character keeps its width, so the character
    columns of the excerpt and of the caret agree.
    """
    if raw is not None and raw[:2] in _UTF16_BOMS:
        return Diagnostic(
            code="E-PARSE-005",
            message=f"cannot read `{display}`: it is UTF-16, not UTF-8",
            file=display,
            show_location=False,
            notes=[Note("rule", _UTF16_RULE)],
            suggestions=[Suggestion(_UTF8_HELP)],
        )

    if raw is None or start is None:
        # The file was readable a moment ago and is not now, or its bytes have
        # changed. Everything below would be a guess, so none of it is said.
        return Diagnostic(
            code="E-PARSE-005",
            message=f"cannot read `{display}`: it is not valid UTF-8 ({reason})",
            file=display,
            show_location=False,
            notes=[Note("rule", _UTF8_RULE)],
            suggestions=[Suggestion(_UTF8_HELP)],
        )

    line_begin = raw.rfind(b"\n", 0, start) + 1
    line = raw.count(b"\n", 0, start) + 1
    col = len(raw[line_begin:start].decode("utf-8", "replace")) + 1
    return Diagnostic(
        code="E-PARSE-005",
        message=f"not valid UTF-8: {_decode_headline(raw, start, end, reason)}",
        file=display,
        spans=[Span(line=line, col=col, label="here", primary=True)],
        source=raw.decode("utf-8", "replace"),
        notes=[
            Note("rule", _UTF8_RULE),
            # Without this the excerpt is quietly dishonest: the reader has no
            # way to tell a `U+FFFD` this renderer substituted from one their
            # file really contains, and only the first bad byte is named in the
            # headline even when a run of them failed.
            Note("note", _REPLACEMENT_NOTE),
        ],
        suggestions=[Suggestion(_UTF8_HELP)],
    )


# --------------------------------------------------------------------------
# YAML: E-PARSE-001, E-PARSE-002, E-CLI-003
# --------------------------------------------------------------------------

_YAML_IS_PDL = (
    "A PDL program is a YAML document: it must parse as YAML before any PDL "
    "rule is checked."
)
_YAML_IS_DATA = "`--data` (`-d`) is read as a YAML mapping of variable names to values."
_YAML_IS_DATA_FILE = (
    "A `-f` data file is read as a YAML mapping of variable names to values."
)


@dataclass
class _Recognized:  # pylint: disable=too-many-instance-attributes
    """What one PyYAML `problem` string was recognized as."""

    headline: str
    primary_label: str = ""
    context_label: str = ""
    context_line: int | None = None
    context_col: int | None = None
    rule: str = ""
    help_text: str = ""
    help_replacement: str = ""


def _mask_escapes(line: str) -> str:
    return line.replace('\\"', "..").replace("\\'", "..")


def _unclosed_quote(
    lines: list[str], first: int, last: int
) -> tuple[int, int, str] | None:
    """The first line in ``[first, last]`` holding an unpaired quote.

    Quotes pair from the left, so when the count is odd the *last* occurrence is
    the one that opened the string nothing closes. Lines are 0-based here.

    This is the one heuristic in the design. When it does not fire the caller
    degrades to the generic branch rather than guessing.
    """
    for index in range(max(first, 0), min(last, len(lines) - 1) + 1):
        masked = _mask_escapes(lines[index])
        for quote, name in (('"', "double quote"), ("'", "single quote")):
            if masked.count(quote) % 2 == 1:
                return index, masked.rfind(quote), name
    return None


def _complete_flow_mapping(source: str) -> tuple[str, str] | None:
    """Turn ``{a: `` into ``{a: 1}``, or give up.

    Only fires when the input really does end at a key with no value, which is
    the shape that produces ``expected the node content``.
    """
    stripped = source.strip()
    if not stripped.endswith(":"):
        return None
    unclosed = stripped.count("{") - stripped.count("}")
    if unclosed > 0:
        return stripped + " 1" + "}" * unclosed, "close the brace"
    if unclosed == 0 and "{" not in stripped:
        return stripped + " 1", ""
    return None


def _recognize(  # pylint: disable=too-many-return-statements,too-many-branches,too-many-arguments,too-many-positional-arguments
    problem: str,
    context: str | None,
    lines: list[str],
    context_line: int | None,
    problem_line: int,
    problem_col: int | None,
    origin: str,
    source: str,
) -> _Recognized:
    """Map PyYAML's `problem` string onto PDL's vocabulary.

    The set of `problem` strings is small and closed (`yaml/parser.py`,
    `yaml/scanner.py`), so this is a table rather than a parser. The fallback is
    deliberate: PyYAML's own text is preserved when there is nothing better, so
    an unrecognized failure still gets a file, a line, a column and a caret.
    """
    if problem.startswith("found character '\\t'"):
        prefix = lines[problem_line - 1][: (problem_col or 1) - 1] if lines else ""
        if prefix.strip() == "":
            return _Recognized(
                headline="tab character used for indentation",
                primary_label="tab character",
                rule="YAML allows only spaces for indentation.",
                help_text=f"replace the leading tab on line {problem_line} with spaces.",
            )
        return _Recognized(
            headline="a tab character cannot start a YAML value",
            primary_label="tab character",
            rule="YAML allows only spaces between tokens.",
            help_text=f"replace the tab on line {problem_line} with a space.",
        )

    if problem.startswith("expected <block end>, but found"):
        kind = "list" if "collection" in (context or "") else "mapping"
        found = _Recognized(
            headline=f"expected the end of the {kind}, but found another value",
            primary_label="so YAML read everything up to here as one value",
        )
        quote = _unclosed_quote(
            lines, (context_line or problem_line) - 1, problem_line - 1
        )
        if quote is not None:
            index, column, name = quote
            found.context_line = index + 1
            found.context_col = column + 1
            found.context_label = (
                f"this {name} opens a string that is never closed on this line"
            )
            # Only the close-the-string advice. An "or escape it as \\\"" clause
            # was dropped: it is right inside an already-quoted string, but a
            # reader applying it to the line as it stands gets `- \"hello`,
            # which YAML accepts as a plain scalar beginning with a backslash.
            # That trades a parse error for a silently wrong value, which is
            # worse than the error it replaces.
            found.help_text = f"close the string on line {index + 1}"
            return found
        found.primary_label = "unexpected value"
        found.context_label = f"while parsing the {kind} that starts here"
        span = (
            f"lines {context_line}-{problem_line}"
            if context_line and context_line != problem_line
            else f"line {problem_line}"
        )
        found.help_text = f"check the indentation and the quoting of {span}."
        return found

    if problem == "found unexpected end of stream":
        return _Recognized(
            headline="a quoted string is never closed",
            primary_label="the input ends here, with the string still open",
            context_label="this quote is never closed",
            help_text=f"close the quote opened on line {context_line or problem_line}.",
        )

    if problem == "expected the node content, but found '<stream end>'":
        recognized = _Recognized(
            headline="expected a value, but the input ended",
            primary_label="a value is expected here",
        )
        completion = _complete_flow_mapping(source)
        if completion is not None and origin == ORIGIN_ARGUMENT:
            completed, brace = completion
            tail = " and close the brace" if brace else ""
            recognized.help_text = f"give the key a value{tail}, e.g."
            recognized.help_replacement = f"-d '{completed}'"
        elif completion is not None:
            completed, brace = completion
            tail = " and close the brace" if brace else ""
            recognized.help_text = f"give the key a value{tail}, e.g. {completed}"
        else:
            recognized.help_text = f"complete the value on line {problem_line}."
        return recognized

    if problem.startswith("mapping values are not allowed here"):
        return _Recognized(
            headline="unexpected `:` in a value",
            primary_label="this `:` is read as a mapping key separator",
            help_text=f"quote the value on line {problem_line}.",
        )

    if problem.startswith("could not find expected ':'"):
        return _Recognized(
            headline="a mapping key has no `:`",
            primary_label="a `:` is expected before here",
            context_label="this key is never given a value",
            help_text=f"add `:` after the key on line {context_line or problem_line}.",
        )

    return _Recognized(
        headline=problem,
        help_text=f"check the syntax at line {problem_line}.",
    )


def _origin_rule(origin: str) -> str:
    if origin == ORIGIN_ARGUMENT:
        return _YAML_IS_DATA
    if origin == ORIGIN_DATA_FILE:
        return _YAML_IS_DATA_FILE
    return _YAML_IS_PDL


def _origin_note(origin: str, program: str | None) -> Note | None:
    """The sentence that says which of the user's two inputs is broken.

    The origin token in the header already cannot be read as a filename; this
    says it again in words, because either device alone is missable.
    """
    if origin == ORIGIN_ARGUMENT:
        named = f", not `{program}`; the program was never read" if program else ""
        return Note("note", f"this is the command-line argument{named}.")
    if origin == ORIGIN_DATA_FILE:
        return Note("note", "read as scope data because of `-f`.")
    return None


def yaml_diagnostic(  # pylint: disable=too-many-arguments
    exc: yaml.YAMLError,
    source: str,
    *,
    origin: str = ORIGIN_PROGRAM,
    file: str = "",
    program: str | None = None,
    code: str = "E-PARSE-001",
) -> Diagnostic:
    """Build a diagnostic from a PyYAML error over `source`.

    PyYAML already computed everything needed and today it is all discarded:
    `problem`, `context` and up to two `Mark`s carrying line and column. The
    excerpt comes from `source` rather than from `mark.buffer` so that the same
    code works whether or not the caller handed PyYAML a string.
    """
    lines = source.split("\n")
    if not isinstance(exc, yaml.MarkedYAMLError) or exc.problem_mark is None:
        # Chiefly `ReaderError`, which carries no marks at all.
        detail = str(getattr(exc, "problem", "") or exc).strip().replace("\n", " ")
        return Diagnostic(
            code=code,
            message=f"not valid YAML: {detail}",
            origin=origin,
            file=file,
            source=source,
            notes=[Note("rule", _origin_rule(origin))]
            + [n for n in [_origin_note(origin, program)] if n is not None],
        )

    problem_line = exc.problem_mark.line + 1
    problem_col = exc.problem_mark.column + 1
    context_line = exc.context_mark.line + 1 if exc.context_mark is not None else None
    context_col = exc.context_mark.column + 1 if exc.context_mark is not None else None

    recognized = _recognize(
        exc.problem or "",
        exc.context,
        lines,
        context_line,
        problem_line,
        problem_col,
        origin,
        source,
    )

    spans = [
        Span(
            line=problem_line,
            col=problem_col,
            label=recognized.primary_label,
            primary=True,
        )
    ]
    if context_line is not None and recognized.context_label:
        spans.append(
            Span(
                line=recognized.context_line or context_line,
                col=recognized.context_col or context_col,
                label=recognized.context_label,
            )
        )

    notes = [Note("rule", recognized.rule or _origin_rule(origin))]
    origin_note = _origin_note(origin, program)
    if origin_note is not None:
        notes.append(origin_note)

    suggestions = []
    if recognized.help_text:
        suggestions.append(
            Suggestion(recognized.help_text, recognized.help_replacement)
        )

    return Diagnostic(
        code=code,
        message=f"not valid YAML: {recognized.headline}",
        origin=origin,
        file=file,
        spans=spans,
        source=source,
        notes=notes,
        suggestions=suggestions,
    )


# --------------------------------------------------------------------------
# Scope validation: E-CLI-004
# --------------------------------------------------------------------------

_DEFAULTS_KEY = "pdl_model_default_parameters"
_DEFAULTS_RULE = "Each entry maps a model-name pattern to a table of parameters."
_DEFAULTS_ADVICE = {
    "not-a-list": "give a list of entries, e.g.",
    "entry-not-a-mapping": "make each entry a mapping of pattern to parameters, e.g.",
    "value-not-a-table": "wrap the parameters in a table, e.g.",
}


def _yaml_scalar(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)


def _defaults_example(pattern: Any, value: Any, origin: str, reason: str) -> str:
    """The shape the user meant, written the way the origin would write it.

    `temperature` is an illustration: the parameter name the user intended is
    exactly what is missing, so it cannot be recovered. The offending value is
    reused only when it really was meant to be a parameter value, so the example
    reads as the user's own edit rather than as a value plucked from elsewhere.
    """
    if reason == "value-not-a-table" and isinstance(value, (bool, int, float, str)):
        parameter = _yaml_scalar(value)
    else:
        parameter = "0.7"
    key = _yaml_scalar(pattern) if isinstance(pattern, str) else '"*"'
    table = "[{" + key + ": {temperature: " + parameter + "}}]"
    if origin == ORIGIN_ARGUMENT:
        return "-d '{" + _DEFAULTS_KEY + ": " + table + "}'"
    return _DEFAULTS_KEY + ": " + table


def model_defaults_diagnostic(  # pylint: disable=too-many-arguments
    *,
    path: Sequence[str],
    pattern: Any,
    value: Any,
    reason: str,
    origin: str,
    origin_file: str,
    program: str | None,
) -> Diagnostic:
    """E-CLI-004, plus the two shapes that produce a bare `AssertionError` today.

    `origin` is not guessed: `main` merges the built-in defaults, then `-f`, then
    `-d`, so a membership test on each dict as it is merged names the source
    exactly.
    """
    match reason:
        case "not-a-list":
            evidence = (
                f"`{_DEFAULTS_KEY}` is a list of entries. "
                f"Here it is `{_yaml_scalar(value)}`, which is not a list."
            )
        case "entry-not-a-mapping":
            evidence = (
                f"{_DEFAULTS_RULE} Here the entry is `{_yaml_scalar(value)}`, "
                "which is not a mapping."
            )
        case _:
            evidence = (
                f"{_DEFAULTS_RULE} Here the pattern `{pattern}` is mapped to "
                f"`{_yaml_scalar(value)}`, which is not a table."
            )

    notes = [Note("rule", evidence)]
    named = f", not from `{program}`" if program else ""
    if origin == ORIGIN_ARGUMENT:
        notes.append(
            Note(
                "note",
                f"this value comes from the `--data` command-line argument{named}.",
            )
        )
    elif origin == ORIGIN_DATA_FILE:
        notes.append(
            Note(
                "note",
                f"this value comes from the data file `{origin_file}` given with "
                f"`-f`{named}.",
            )
        )
    else:
        notes.append(
            Note(
                "note",
                "this value comes from PDL's built-in model defaults, which is a "
                "bug in PDL itself rather than in anything you wrote.",
            )
        )

    return Diagnostic(
        code="E-CLI-004",
        message=f"malformed `{_DEFAULTS_KEY}`",
        origin=origin,
        file=origin_file,
        show_location=bool(origin_file),
        block_path=list(path),
        notes=notes,
        suggestions=[
            Suggestion(
                _DEFAULTS_ADVICE.get(reason, _DEFAULTS_ADVICE["value-not-a-table"]),
                _defaults_example(pattern, value, origin, reason),
            )
        ],
    )
