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
* A bare ``N |`` gutter means a line of the file named in the header. When the
  excerpt is in some *other* coordinate system -- the output of a block, the
  text of a `regex:` -- `Diagnostic.gutter` labels every row ``<label>:N |`` and
  a ``note:`` says what those lines are counted in. Printing a runtime value
  under a bare ``N |`` row would state a file location that is not true.
* The rule paragraph is indented two spaces with no prefix; ``  note:`` carries
  context and ``  help:`` the action, with continuations aligned under the text.
* No ANSI, no absolute paths, no severity token.

This module imports nothing from PDL, so it cannot participate in an import
cycle with the parser it serves.
"""

from __future__ import annotations

import csv
import difflib
import json
import textwrap
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Sequence

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
    gutter: str = ""
    """Label for a coordinate system that is not the file.

    Empty -- the default, and every diagnostic that existed before the
    `parser:` series -- renders excerpt rows as ``N | <source>``, which reads as
    line N of the `.pdl` file. When it is set, rows read
    ``<gutter>:N | <source>`` and the caller is expected to say in a ``note:``
    what those lines are counted in. The distinction is load-bearing: a bare
    ``2 |`` over a line of a *model's output* is a confidently-stated wrong
    location, which `RUBRIC.md` ranks below showing nothing.

    Setting it also narrows the excerpt's width budget to what is left of
    `WIDTH` after the label, and folds control characters, because a gutter
    excerpt is the only one whose text is a runtime value rather than a file the
    user wrote."""
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
            "gutter": self.gutter or None,
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


def _clip_within(line: str, col: int | None, budget: int) -> tuple[str, int | None]:
    """`_clip`, with the ` ... ` markers counted against the budget.

    `_clip` spends `EXCERPT_MAX` on *source* and then adds up to ten more
    columns of markers, which is fine for a row whose prefix is a two-digit line
    number and is not fine for one prefixed `output:1 | `. Here the budget is
    the whole row minus its prefix, so it has to include the markers or the
    bound it promises is not a bound.

    Separate from `_clip` rather than a flag on it, so that not one byte of an
    existing golden can move.
    """
    if len(line) <= budget:
        return line, col
    marker = " ... "
    room = max(budget - len(marker), 1)
    anchor = max((col or 1) - 1, 0)
    if anchor < room:
        # The caret is inside the head window, so only the tail is cut.
        return line[:room] + marker, col
    keep = max(budget - 2 * len(marker), 1)
    start = max(0, anchor - keep // 2)
    end = min(len(line), start + keep)
    start = max(0, end - keep)
    head = marker if start > 0 else ""
    tail = marker if end < len(line) else ""
    new_col = None if col is None else col - start + len(head)
    return head + line[start:end] + tail, new_col


_CONTROL_NAMES = {
    "\t": "tab",
    "\r": "carriage return",
    "\x00": "NUL byte",
    "\x1b": "escape character",
}


def _control_name(char: str) -> str:
    return _CONTROL_NAMES.get(char) or f"control character U+{ord(char):04X}"


def _is_control(char: str) -> bool:
    """C0 and C1, minus the `\\n` the caller already split on."""
    return char != "\n" and (ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F)


def _fold_controls(line: str) -> str:
    """Every control character as one space, so one character is one column.

    The same contract this module already states for tabs, applied to the whole
    C0/C1 range because a gutter excerpt quotes a *runtime* value: a model's
    output can contain an ANSI escape, and nothing PDL prints may hand one to a
    terminal.
    """
    return "".join(" " if _is_control(ch) else ch for ch in line)


def _control_label(line: str, col: int | None, label: str) -> str:
    """Name a control character sitting under the caret, since it renders blank.

    Silent when the label already names it: `_recognize` labels a YAML tab
    `tab character`, and "tab character; this is a tab" says it twice.
    """
    if col is None or not 1 <= col <= len(line) or not _is_control(line[col - 1]):
        return label
    name = _control_name(line[col - 1])
    if name in label:
        return label
    return f"{label}; this is {_with_article(name)}" if label else f"{name} here"


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

    def _label(line: int) -> str:
        return f"{diag.gutter}:{line}" if diag.gutter else str(line)

    width = max(len(_label(s.line)) for s in spans)
    budget = WIDTH - width - len(" | ")
    out: list[str] = []
    previous: int | None = None
    for span in spans:
        if previous is not None and span.line > previous + 1:
            out.append("...")
        raw = src[span.line - 1]
        if diag.gutter:
            # Named from the *unfolded* line: folding is what makes the
            # character invisible, so the label has to be decided first.
            label = _control_label(raw, span.col, span.label)
            text, col = _clip_within(_fold_controls(raw), span.col, budget)
        else:
            label = span.label
            text, col = _clip(raw.replace("\t", " "), span.col)
        out.append(f"{_label(span.line).rjust(width)} | {text}".rstrip())
        if col is not None and col >= 1:
            caret = " " * width + " | " + " " * (col - 1) + "^"
            if label:
                caret += " " + label
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

    # An import inside an *imported* file that resolves back to the top-level
    # program's own directory. The entry-point program sits there by
    # construction and is not the importing file, so `_import_candidates` does
    # not drop it -- and naming it from a file it imported is a cycle. Both
    # suggestion branches below refuse in this shape; the *facts* they carry are
    # still stated.
    #
    # This over-refuses: a genuine near miss in that directory is suppressed
    # too, because nothing here can tell which candidate is the entry program.
    # That costs a Fix point, where suggesting a cycle would be a confidently
    # wrong instruction -- which the rubric ranks below saying nothing.
    would_cycle = importing is not None and search_dir == cwd != importing.parent

    # 2. A near miss, matched on what the user *wrote* rather than on the
    #    suffixed form: scoring `nosuch.pdl` against `helper.pdl` would credit
    #    every candidate with the four characters PDL itself added.
    names = [p.stem for p in candidates]
    matches = difflib.get_close_matches(Path(written).stem, names, n=1, cutoff=0.7)
    if matches and not would_cycle:
        best = candidates[names.index(matches[0])]
        return (
            headline,
            "Nothing exists at that path.",
            Suggestion(f"did you mean `import: {_import_form(written, best)}`?"),
        )

    if not search_dir.exists():
        return headline, f"{capital} does not exist.", base_help

    if candidates:
        # The listing is a fact and is always shown; only the "name one of them"
        # action is dropped, under the same `would_cycle` condition as the near
        # miss above.
        listing = f"{capital} contains {_dir_listing(candidates)}."
        if would_cycle:
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
    line_phrase: Callable[[str], str] = lambda phrase: phrase,
) -> _Recognized:
    """Map PyYAML's `problem` string onto PDL's vocabulary.

    The set of `problem` strings is small and closed (`yaml/parser.py`,
    `yaml/scanner.py`), so this is a table rather than a parser. The fallback is
    deliberate: PyYAML's own text is preserved when there is nothing better, so
    an unrecognized failure still gets a file, a line, a column and a caret.

    ``line_phrase`` rewrites every "line N" this table puts in a ``help:``. The
    same YAML is parsed from two coordinate systems -- a `.pdl` file, and the
    *output* of a block for `parser: yaml` -- and "line 1" means a different
    thing in each. It takes the rendered phrase rather than the number so that
    the one range case ("lines 1-3") goes through it too; the default is the
    identity, so every existing golden is byte-identical.
    """
    if problem.startswith("found character '\\t'"):
        prefix = lines[problem_line - 1][: (problem_col or 1) - 1] if lines else ""
        if prefix.strip() == "":
            return _Recognized(
                headline="tab character used for indentation",
                primary_label="tab character",
                rule="YAML allows only spaces for indentation.",
                help_text=f"replace the leading tab on {line_phrase(f'line {problem_line}')} with spaces.",
            )
        return _Recognized(
            headline="a tab character cannot start a YAML value",
            primary_label="tab character",
            rule="YAML allows only spaces between tokens.",
            help_text=f"replace the tab on {line_phrase(f'line {problem_line}')} with a space.",
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
            found.help_text = f"close the string on {line_phrase(f'line {index + 1}')}"
            return found
        found.primary_label = "unexpected value"
        found.context_label = f"while parsing the {kind} that starts here"
        span = line_phrase(
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
            help_text=f"close the quote opened on {line_phrase(f'line {context_line or problem_line}')}.",
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
            recognized.help_text = (
                f"complete the value on {line_phrase(f'line {problem_line}')}."
            )
        return recognized

    if problem.startswith("mapping values are not allowed here"):
        return _Recognized(
            headline="unexpected `:` in a value",
            primary_label="this `:` is read as a mapping key separator",
            help_text=f"quote the value on {line_phrase(f'line {problem_line}')}.",
        )

    if problem.startswith("could not find expected ':'"):
        return _Recognized(
            headline="a mapping key has no `:`",
            primary_label="a `:` is expected before here",
            context_label="this key is never given a value",
            help_text=f"add `:` after the key on {line_phrase(f'line {context_line or problem_line}')}.",
        )

    return _Recognized(
        headline=problem,
        help_text=f"check the syntax at {line_phrase(f'line {problem_line}')}.",
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
# Duplicate mapping keys: E-PARSE-003
# --------------------------------------------------------------------------
#
# The one diagnostic in this module whose subject PyYAML did *not* object to.
# `text: a` twice is valid YAML by every reader's reckoning and builds a mapping
# holding `b`; the rule that it may not appear in a PDL program is PDL's own
# (decision 5.5). So the message never says "not valid YAML" -- a reader who
# checked the same file with another YAML tool and was told it parses would be
# right, and a diagnostic that contradicts the tool next to it teaches the user
# to distrust this one.

_KEY_MAX = 40
"""How much of a repeated key is quoted in prose. A key is the user's own text
and is short in every realistic case, but it is still text from outside: YAML
permits a block scalar as a key, so it can be arbitrarily long and can contain
newlines."""

_DUPLICATE_RULE = (
    "A YAML mapping gives each key one value. Most YAML readers accept a key "
    "written more than once and silently keep the last value, discarding the "
    "ones before it; PDL rejects the program instead of choosing between them "
    "for you."
)


def _key_label(key: str) -> str:
    """A repeated key as it may be quoted in prose: one line, bounded length."""
    folded = _fold_controls(key).strip()
    if len(folded) > _KEY_MAX:
        return folded[: _KEY_MAX - 1] + "…"
    return folded


def _and_list(items: Sequence[str]) -> str:
    """``\\`a\\```, ``\\`a\\` and \\`b\\```, ``\\`a\\`, \\`b\\` and \\`c\\```."""
    quoted = [f"`{_key_label(i)}`" for i in items]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def duplicate_key_diagnostic(  # pylint: disable=too-many-arguments
    *,
    key: str,
    file: str,
    source: str,
    first_line: int,
    first_col: int,
    again_line: int,
    again_col: int,
    count: int = 2,
    block_path: Sequence[str] = (),
    siblings: Sequence[str] = (),
    other_mappings: int = 0,
) -> Diagnostic:
    """E-PARSE-003. A mapping key written more than once.

    Both positions are shown, and that is the whole point of building this here
    rather than at the constructor: "there is a duplicate" is a fact the user
    already has, while "this one, and the earlier one whose value it replaces"
    is the edit. The two marks exist only while the node graph does, which is
    why `pdl_location_utils.load_with_marks` is where the check lives.

    Everything the caller passes is true of *one* mapping and one key. Where a
    program has more than one repeat, the surplus is reported as counts in
    ``note:`` lines rather than folded into the headline, so that nothing here
    can overstate which pair the carets are under: the header, the excerpt and
    the ``help:`` all describe the same two lines.
    """
    shown = _key_label(key)
    times = "twice" if count == 2 else f"{count} times"
    notes = [Note("rule", _DUPLICATE_RULE)]
    if count > 2:
        notes.append(
            Note(
                "note",
                f"`{shown}` is written {count} times here; the two shown above "
                "are the first two.",
            )
        )
    if siblings:
        verb = "is" if len(siblings) == 1 else "are"
        notes.append(
            Note(
                "note",
                f"{_and_list(siblings)} {verb} also written more than once in "
                "this mapping.",
            )
        )
    if other_mappings:
        plural = "" if other_mappings == 1 else "s"
        verb = "has" if other_mappings == 1 else "have"
        notes.append(
            Note(
                "note",
                f"{other_mappings} other mapping{plural} in this program "
                f"{verb} a repeated key too.",
            )
        )
    action = "remove one of them" if count == 2 else "remove all but one"
    return Diagnostic(
        code="E-PARSE-003",
        message=f"the key `{shown}` is written {times} in the same mapping",
        file=file,
        spans=[
            Span(line=first_line, col=first_col, label="first written here"),
            Span(
                line=again_line,
                col=again_col,
                label="written again here",
                primary=True,
            ),
        ],
        block_path=list(block_path) if block_path else None,
        source=source,
        notes=notes,
        suggestions=[Suggestion(f"{action}, or merge them into a single `{shown}:`.")],
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


# --------------------------------------------------------------------------
# Output parsers: E-PARSER-001 .. E-PARSER-007
# --------------------------------------------------------------------------
#
# A `parser:` reads a *runtime* value -- usually a model's output -- so the text
# these diagnostics quote is unbounded, may contain control characters, and may
# not be text at all. Every line of it is therefore printed inside an
# `output:N | ` gutter row, never as a bare `N | ` row that would read as a line
# of the `.pdl` file, and the note that says so is emitted exactly when a row
# was printed. E-PARSER-006 is the one entry whose evidence really is a file
# line, and it is the one entry with a bare gutter.

_OUTPUT_GUTTER = "output"
_REGEX_GUTTER = "regex"

_OUTPUT_CAVEAT = "`output:N` counts lines of the block's output, not of the PDL file."
_REGEX_CAVEAT = "`regex:N` counts lines of the pattern, not of the PDL file."
_EMPTY_OUTPUT = "the block's output was empty."

_PARSER_IS_TEXT = "A `parser:` reads the text a block produced."

_VALUE_MAX = 40
"""How much of a non-text value is shown inline. Beyond this the sentence names
the type alone: the whole point of E-PARSER-006's correction is that a runtime
value must not be interpolated raw and untruncated into a message."""

_PATTERN_MAX = 60
"""How long a `regex:` may be before the prose stops quoting it. The pattern is
the user's own text and is short in every realistic case, but it is still text
from outside and still needs a wall."""


_PDL_TYPES: tuple[tuple[type | tuple[type, ...], str], ...] = (
    # Ordered, and `bool` before `int` because it is a subclass of it.
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    ((list, tuple), "array"),
    (dict, "object"),
)


def _pdl_type(value: Any) -> str:
    """The name `spec:` would use for this value's type, or `""` if it has none.

    PDL's own vocabulary (`pdl_ast.BasePdlType`), not Python's: a user who wrote
    `parser: json` has never met `int` and has met `integer`.
    """
    if value is None:
        return "null"
    for kinds, name in _PDL_TYPES:
        if isinstance(value, kinds):
            return name
    return ""


def _with_article(name: str) -> str:
    return ("an " if name[:1] in "aeiou" else "a ") + name


def _inline_value(value: Any) -> str:
    """`json.dumps(value)` when it is short enough to read, else `""`."""
    try:
        dumped = json.dumps(value)
    except (TypeError, ValueError):
        return ""
    return dumped if len(dumped) <= _VALUE_MAX else ""


def _parser_diagnostic(  # pylint: disable=too-many-arguments
    code: str,
    *,
    headline: str,
    rule: str,
    notes: Sequence[str] = (),
    suggestion: Suggestion | None = None,
    gutter: str = "",
    gutter_note: str = "",
    empty_note: str = "",
    source: str | None = None,
    spans: Sequence[Span] = (),
) -> Diagnostic:
    """Assemble one entry of the series. One shape, seven callers.

    The gutter caveat is appended only when a gutter row is really going to be
    printed -- the source is non-empty and at least one span lands inside it --
    so a diagnostic that degrades to no excerpt does not explain a gutter the
    reader cannot see. `render` drops out-of-range spans silently, which is why
    the test is made here rather than assumed.
    """
    body = list(notes)
    shown: Sequence[Span] = ()
    if source:
        height = len(source.split("\n"))
        shown = [s for s in spans if 1 <= s.line <= height]
    if source == "" and empty_note:
        body.append(empty_note)
    if shown and gutter_note:
        body.append(gutter_note)
    return Diagnostic(
        code=code,
        message=headline,
        # No `file` and no `block_path`: the header prefix and the `  in <path>`
        # line are added by `located_message` at print time, from the location
        # threaded into `parse_result`. Setting them here would print each of
        # them twice.
        spans=list(shown),
        source=source,
        gutter=gutter,
        notes=[Note("rule", rule)] + [Note("note", n) for n in body],
        suggestions=[suggestion] if suggestion is not None else [],
    )


def parser_not_text_diagnostic(*, label: str, remove: str, value: Any) -> Diagnostic:
    """E-PARSER-001. A `parser:` applied to a value that is already structured.

    Not a parse failure, and saying so is the whole of the fix: today this
    branch reports `TypeError("'int' object is not subscriptable")`, which is
    `json_repair` subscripting a value that is not a string, described in
    Python's vocabulary and attributed to JSON.
    """
    name = _pdl_type(value)
    if not name:
        produced = "a value that is not text"
        described = "not text"
        already = ""
    elif name == "null":
        produced = "`null`"
        described = "`null`, which is not text"
        already = "`null`"
    else:
        article = _with_article(name)
        produced = article
        inline = _inline_value(value)
        described = (
            f"the {name} `{inline}`, which is not text"
            if inline
            else f"{article}, which is not text"
        )
        already = article

    tail = f"; the block's result is already {already}." if already else "."
    return _parser_diagnostic(
        "E-PARSER-001",
        headline=f"{label} needs text, but this block produced {produced}",
        rule=(
            f"{_PARSER_IS_TEXT} This block's result is {described}, so there is "
            "nothing to parse."
        ),
        suggestion=Suggestion(f"{remove}{tail}"),
    )


def parser_json_diagnostic(
    *, text: str | None, detail: str, line: int | None, col: int | None
) -> Diagnostic:
    """E-PARSER-001, the branch where the output *is* text and JSON rejected it.

    Unreachable through `json_repair`, which repairs rather than raises (see
    INVENTORY 7.10) -- kept because the repairing parser is a dependency choice
    and this is what the diagnostic must be if it ever raises.
    """
    spans = (
        [Span(line=line, col=col, label=detail, primary=True)]
        if line is not None
        else []
    )
    return _parser_diagnostic(
        "E-PARSER-001",
        headline="`parser: json` could not parse the block's output",
        rule="`parser: json` reads the block's output as a single JSON value.",
        notes=[] if spans else [f"the JSON reader reported: {detail}."],
        suggestion=Suggestion(
            "make the output a complete JSON value, or remove the parser to "
            "keep the output as text."
        ),
        gutter=_OUTPUT_GUTTER,
        gutter_note=_OUTPUT_CAVEAT,
        empty_note=_EMPTY_OUTPUT,
        source=text,
        spans=spans,
    )


def parser_jsonl_diagnostic(
    *, text: str, line: int, col: int | None, detail: str, whole_is_json: bool
) -> Diagnostic:
    """E-PARSER-002. One line of the output is not a complete JSON value.

    `line` is the index of the failing line in the block's output, which the
    loop already has and today throws away: every `JSONDecodeError` here reads
    `line 1` because each line is loaded as its own document, so the position
    the user sees is stated confidently and is wrong for every line but the
    first. `col` is `exc.colno`, which *is* correct within that line.

    ``detail`` is `exc.msg` verbatim. That is the JSON parser's vocabulary about
    JSON -- the format the user asked for -- not an internal leak, and it is
    reproduced without transformation so there is no case rule to get wrong.
    """
    suggestion = (
        "this output is one JSON document, not one per line; use `parser: json`."
        if whole_is_json
        else "make every non-empty line a complete JSON value, or remove the "
        "parser to keep the output as text."
    )
    return _parser_diagnostic(
        "E-PARSER-002",
        headline=(f"`parser: jsonl` could not parse line {line} of the block's output"),
        rule=(
            "`parser: jsonl` reads the block's output as one JSON value per "
            "line; every non-empty line must be a complete JSON value on its own."
        ),
        suggestion=Suggestion(suggestion),
        gutter=_OUTPUT_GUTTER,
        gutter_note=_OUTPUT_CAVEAT,
        empty_note=_EMPTY_OUTPUT,
        source=text,
        spans=[Span(line=line, col=col, label=detail, primary=True)],
    )


_PARSER_YAML_RULE = "`parser: yaml` reads the block's output as a single YAML document."


def parser_yaml_diagnostic(exc: yaml.YAMLError, text: str) -> Diagnostic:
    """E-PARSER-003. PyYAML's marks, read directly, in the output's coordinates.

    Today's message is `repr(exc)`, which prints the two `Mark` objects holding
    the position as memory addresses that differ on every run. `str(exc)` would
    recover the position, but renders it as ``in "<unicode string>", line 1,
    column 6`` -- a file that does not exist. So the marks are read the way
    `yaml_diagnostic` already reads them for `.pdl` files, and rendered in a
    gutter that says whose line 1 it is.

    `_recognize` is shared with the file-level YAML diagnostics rather than
    duplicated, with `line_phrase` rewriting its "line N" into "line N of the
    block's output" -- one table of YAML wordings, two coordinate systems.
    """
    if not isinstance(exc, yaml.MarkedYAMLError) or exc.problem_mark is None:
        detail = str(getattr(exc, "problem", "") or exc).strip().replace("\n", " ")
        return _parser_diagnostic(
            "E-PARSER-003",
            headline="`parser: yaml` could not parse the block's output",
            rule=_PARSER_YAML_RULE,
            notes=[f"the YAML reader reported: {detail}."],
            empty_note=_EMPTY_OUTPUT,
            source=text,
        )

    problem_line = exc.problem_mark.line + 1
    problem_col = exc.problem_mark.column + 1
    context_line = exc.context_mark.line + 1 if exc.context_mark is not None else None
    context_col = exc.context_mark.column + 1 if exc.context_mark is not None else None
    recognized = _recognize(
        exc.problem or "",
        exc.context,
        text.split("\n"),
        context_line,
        problem_line,
        problem_col,
        ORIGIN_PROGRAM,
        text,
        line_phrase=lambda phrase: f"{phrase} of the block's output",
    )

    spans = [
        Span(
            line=problem_line,
            col=problem_col,
            # The recognized branches carry a short label and put their own
            # sentence in the headline; the generic arm has no label, and there
            # PyYAML's `problem` string is the most precise thing anyone has.
            label=recognized.primary_label or recognized.headline,
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

    rule = _PARSER_YAML_RULE
    if recognized.rule:
        rule += " " + recognized.rule
    return _parser_diagnostic(
        "E-PARSER-003",
        headline="`parser: yaml` could not parse the block's output",
        rule=rule,
        suggestion=(
            Suggestion(recognized.help_text, recognized.help_replacement)
            if recognized.help_text
            else None
        ),
        gutter=_OUTPUT_GUTTER,
        gutter_note=_OUTPUT_CAVEAT,
        empty_note=_EMPTY_OUTPUT,
        source=text,
        spans=spans,
    )


_CSV_LIMIT_MARKER = "field larger than field limit"
_CSV_UNCLOSED_MARKER = "unexpected end of data"

_PARSER_CSV_RULE = "`parser: csv` reads the block's output as comma-separated rows."

_CSV_QUOTE_RULE = (
    f'{_PARSER_CSV_RULE} A `"` at the start of a field quotes it, and '
    'everything up to the matching `"` is part of that field -- including '
    "commas and line breaks."
)


def csv_error_is_unclosed_quote(detail: str) -> bool:
    """Whether a `csv.Error` is the one class `parser: csv` refuses to parse.

    Matched on the message text, because `csv.Error` offers nothing better: it
    carries no code, no attributes and no position, so its message is the only
    thing separating "a quoted field ran off the end of the output" from every
    other reason the reader can stop. The string is stable and was checked on
    both interpreters this repository runs -- 3.11 and 3.12 both say
    `unexpected end of data`.

    The caller must treat an *unrecognised* message as not-this-class and fall
    back to a lenient parse, never as a failure. That is what makes matching on
    text safe: if a future Python rewords something, the cost is a diagnostic
    PDL no longer produces, not a working program that suddenly exits 1.
    """
    return _CSV_UNCLOSED_MARKER in detail


def _unclosed_quote_position(text: str) -> tuple[int, int] | None:
    """Locate the `"` that opens the field `csv` ran out of data inside.

    `csv.Error` carries no position at all, and `reader.line_num` is the last
    line the reader *consumed* -- for a quote left open on line 2 of a
    four-line output that is line 4, which is a confidently-stated wrong
    location. So the position is not guessed and it is not found by
    re-implementing the dialect's quoting rules either: `csv` itself is used as
    the oracle. Closing the quote at the end of the text makes the same reader
    parse, and the last field of the last row it then yields *is* the content
    of the never-closed field. Re-escaping the doubled `""` that content came
    from gives its length in the original text, and therefore the offset of the
    `"` that opened it.

    Every step is checked against the text -- the re-parse has to succeed, the
    offset has to land inside the text, and the character there has to be a `"`
    -- and `None` is returned when any of them fails, in which case the caller
    prints an excerpt row with no caret. That is the same rule the field-limit
    branch already follows: a caret only where the column is known.
    """
    try:
        rows = list(csv.reader(StringIO(text + '"'), strict=True))
    except Exception:  # pylint: disable=broad-except
        # Broad on purpose: this runs while a diagnostic is being built, so
        # anything raised here would reach the user as a traceback in place of
        # the message, which decision 5.8 forbids. Losing the caret is the
        # correct failure. `KeyboardInterrupt` is a `BaseException` and so
        # still propagates.
        return None
    if not rows or not rows[-1]:
        return None
    content = rows[-1][-1]
    offset = len(text) - len(content.replace('"', '""')) - 1
    if not 0 <= offset < len(text) or text[offset] != '"':
        return None
    line = text.count("\n", 0, offset) + 1
    col = offset - text.rfind("\n", 0, offset)
    return line, col


def parser_csv_diagnostic(
    *, text: str, detail: str, limit: int, row: int, record_line: int = 1
) -> Diagnostic:
    """E-PARSER-004. Two branches: a size limit and an unclosed quote.

    The size-limit branch is the original one, and it is not a syntax error at
    all -- calling a well-formed 131073-character field "ill-formed CSV"
    misdiagnoses a resource limit. The unclosed-quote branch is the whole of
    the decision-5.5 change (INVENTORY 7.10 finding 1): without it a quoted
    field that is never closed swallows the rest of the output and the program
    prints a wrong answer at exit 0.

    Nothing else is diagnosed, because nothing else fails. The `csv` branch of
    `parse_result` re-parses leniently everything a strict reader rejects other
    than this class -- text after a closing `"` above all, which includes a
    lone trailing space -- so ragged rows, embedded NULs, a bare `"` inside an
    unquoted field and `1,"Ada" Lovelace` are all still accepted in silence.
    The generic branch below stays reachable for whatever the lenient reader
    itself cannot survive.
    """
    caret: int | None = None
    if _CSV_LIMIT_MARKER in detail:
        headline = f"`parser: csv` cannot read a field longer than {limit} characters"
        rule = (
            f"{_PARSER_CSV_RULE} Python's `csv` module refuses any single field "
            f"longer than {limit} characters. This is a size limit, not a syntax "
            "error."
        )
        # Stated as a fact only when it *is* one. A field can also run over the
        # limit inside a well-formed multi-row file, and there the honest note
        # is which row it was.
        if "," not in text and "\n" not in text and "\r" not in text:
            note = (
                f"the block's output is {len(text)} characters with no `,` and "
                "no line break, so it is a single field."
            )
            line = 1
        else:
            note = f"the failure is in row {row} of the block's output."
            line = max(row, 1)
        suggestion = Suggestion("if this output is not CSV, remove `parser: csv`.")
        label = ""
    elif _CSV_UNCLOSED_MARKER in detail:
        headline = "`parser: csv` found a quoted field that is never closed"
        rule = (
            f'{_CSV_QUOTE_RULE} This one has no matching `"`, so the reader '
            "ran to the end of the block's output still inside it."
        )
        label = "this quote is never closed"
        position = _unclosed_quote_position(text)
        if position is None:
            # The oracle could not confirm a position, so none is stated. The
            # record the reader was in the middle of is still known exactly:
            # it begins on the line after the last one a completed row ended
            # on, which the interpreter counts as it goes.
            line = max(record_line, 1)
            label = ""
            note = (
                "the reader reached the end of the block's output while still "
                "inside a quoted field."
            )
        else:
            line, caret = position
            remaining = len(text.rstrip("\n").split("\n")) - line
            if remaining == 1:
                # The damage, not a restatement of the rule: the lines below
                # were read into the open field instead of becoming rows of
                # their own, which is the wrong answer this used to return at
                # exit 0.
                note = (
                    "the 1 line below it was read as part of this field "
                    "rather than as a row of its own."
                )
            elif remaining > 1:
                note = (
                    f"the {remaining} lines below it were read as part of "
                    "this field rather than as rows of their own."
                )
            else:
                note = "this field is the last thing in the block's output."
        suggestion = Suggestion(
            'add the closing `"`, or remove the opening one if the field was '
            "not meant to be quoted."
        )
    else:
        headline = "`parser: csv` could not read the block's output"
        rule = _PARSER_CSV_RULE
        note = f"the `csv` reader reported: {detail}."
        line = max(row, 1)
        label = ""
        suggestion = Suggestion(
            "check the block's output, or remove `parser: csv` if it is not CSV."
        )

    return _parser_diagnostic(
        "E-PARSER-004",
        headline=headline,
        rule=rule,
        notes=[note],
        suggestion=suggestion,
        gutter=_OUTPUT_GUTTER,
        gutter_note=_OUTPUT_CAVEAT,
        empty_note=_EMPTY_OUTPUT,
        source=text,
        spans=[Span(line=line, col=caret, label=label, primary=True)],
    )


_UNCLOSED_GROUP = ("missing )", "unbalanced parenthesis")


def parser_regex_diagnostic(
    *, pattern: str, detail: str, pos: int | None, line: int | None, col: int | None
) -> Diagnostic:
    """E-PARSER-005. The pattern is shown, in the pattern's own coordinates.

    Not a caret on the `.pdl` line: the mark recorded for `["parser","regex"]`
    is the *key*'s start and the *value*'s end, so PDL knows where `regex`
    begins and not where `(` begins inside the quoted scalar. Locating the
    pattern's character 0 in the file would mean re-scanning the line through
    YAML's quoting, which is a heuristic. `re`'s own coordinates are exact, so
    they are the ones shown, in a gutter that says which text they index.

    The single quotes in the `missing )` suggestion are the point: `\\(` is not a
    valid escape in a double-quoted YAML scalar, so ``regex: "\\("`` would trade
    a regex error for a YAML error. In a single-quoted scalar the backslash is
    literal and reaches `re` intact. The clause is conditional because `\\(`
    matches a literal `(` and nothing else, which may not be what the user meant.
    """
    if any(marker in detail for marker in _UNCLOSED_GROUP):
        suggestion = Suggestion(
            "close the group, or write `regex: '\\('` to match a literal `(`."
        )
    elif pos is not None:
        suggestion = Suggestion(f"check the pattern at position {pos}.")
    else:
        suggestion = Suggestion("check the pattern.")
    return _parser_diagnostic(
        "E-PARSER-005",
        headline="`regex:` is not a valid regular expression",
        rule=(
            "The `regex:` of a parser is a Python regular expression. It is "
            "compiled before the block's output is read, so the fault is in the "
            "pattern, not in the output."
        ),
        suggestion=suggestion,
        gutter=_REGEX_GUTTER,
        gutter_note=_REGEX_CAVEAT,
        source=pattern,
        spans=(
            [Span(line=line, col=col, label=detail, primary=True)]
            if line is not None
            else []
        ),
    )


def parser_regex_match_diagnostic(*, detail: str) -> Diagnostic:
    """E-PARSER-005, for a pattern that compiles and then fails to run.

    No reproducer in the corpus and none expected: with the pattern compiled up
    front and the input known to be text, what is left is `RecursionError` on a
    deeply nested pattern. It exists so that the one path in `parse_result` that
    could still reach the user as a traceback does not.
    """
    return _parser_diagnostic(
        "E-PARSER-005",
        headline="`regex:` could not be matched against the block's output",
        rule=(
            "The `regex:` of a parser is a Python regular expression. This one "
            "is valid, but matching it against the block's output failed."
        ),
        notes=[f"the `re` module reported: {detail}."],
        suggestion=Suggestion("simplify the pattern."),
    )


def _group_rule(name: str, pattern: str, groups: Sequence[str]) -> str:
    """Why no output could have supplied the group, naming what the pattern has."""
    quoted = (
        f"The pattern `{pattern}` defines"
        if len(pattern) <= _PATTERN_MAX
        else ("The pattern defines")
    )
    if not groups:
        defines = "no named groups,"
    elif len(groups) == 1:
        defines = f"one group, `{groups[0]}`,"
    else:
        listed = ", ".join(f"`{g}`" for g in groups[:-1]) + f" and `{groups[-1]}`"
        defines = f"the groups {listed},"
    return (
        "For a `regex:` parser, each key of `spec:` names a capture group to "
        f"take from the match. {quoted} {defines} so no output could have "
        f"supplied `{name}`."
    )


def _group_suggestion(name: str, groups: Sequence[str]) -> Suggestion:
    """Four branches, chosen by what the pattern defines.

    The near miss is `difflib` over an *ordered* list, never a set, so it cannot
    move with `PYTHONHASHSEED`. It does not fire for `second` against `first`,
    which scores well under the cutoff -- the one-group branch is what the
    corpus reproducer takes.
    """
    if not groups:
        return Suggestion(
            f"name the group in the pattern, e.g. `(?P<{name}>...)`, or remove "
            "`spec:` to get the groups as a list."
        )
    if len(groups) == 1:
        return Suggestion(
            f"rename the key to `{groups[0]}`, the only group this pattern defines."
        )
    near = difflib.get_close_matches(name, list(groups), n=1, cutoff=0.7)
    if near:
        return Suggestion(f"did you mean `{near[0]}`?")
    listed = ", ".join(f"`{g}`" for g in groups[:5])
    return Suggestion(f"use one of the groups the pattern defines: {listed}.")


def parser_group_diagnostic(  # pylint: disable=too-many-arguments
    *,
    name: str,
    pattern: str,
    groups: Sequence[str],
    source: str | None,
    line: int | None,
    col: int | None,
) -> Diagnostic:
    """E-PARSER-006. A static fault, so the evidence is the file and not the output.

    Today's message says the group was not found `in hello` -- in the matched
    *text*, which had nothing to do with it -- and never names the group the
    pattern does define, which is the whole of the fix. Both come from
    `m.re.groupindex`, a dict on the compiled pattern that is in scope at the
    raise site.

    The one entry in the series with a file excerpt and a bare `N |` gutter, and
    the only one that has earned it: the offending construct really is at
    `parser.spec.<name>` in the user's file, and nothing about the block's
    output is relevant.
    """
    return _parser_diagnostic(
        "E-PARSER-006",
        headline=f"the `regex:` pattern has no group named `{name}`",
        rule=_group_rule(name, pattern, groups),
        suggestion=_group_suggestion(name, groups),
        source=source,
        spans=[Span(line=line, col=col, primary=True)] if line is not None else [],
    )


def parser_not_implemented_diagnostic() -> Diagnostic:
    """E-PARSER-007. `parser: {pdl: ...}` reaches `assert False, "TODO"`.

    `PdlParser` is a declared branch of `ParserType`, so the program validates
    and runs, and then the interpreter aborts on an assertion that is neither a
    `PDLRuntimeError` nor a `PDLParseError` and so escapes `generate`'s handlers
    as a raw traceback. Under `python -O` the assertion vanishes and `result` is
    returned unbound instead, which is worse.

    The diagnostic says the parser is not implemented and nothing more. Nothing
    here may invent behaviour for the form: what it would do if it were
    implemented is not knowable from a `TODO`.
    """
    return _parser_diagnostic(
        "E-PARSER-007",
        headline="`parser:` with a `pdl:` sub-program is not implemented",
        rule=(
            "A `parser:` may be `json`, `jsonl`, `yaml`, `csv`, or a `regex:` "
            "parser. The `pdl:` form is part of PDL's schema, so a program using "
            "it loads, but the interpreter has no implementation for it and "
            "cannot run this block."
        ),
        notes=["this is a gap in PDL itself, not a mistake in this program."],
        suggestion=Suggestion(
            "use `json`, `jsonl`, `yaml`, `csv` or a `regex:` parser, or parse "
            "the output in a `code:` block."
        ),
    )


# --------------------------------------------------------------------------
# Block unions: E-SCHEMA-005, E-SCHEMA-006, E-SCHEMA-007
#
# Decision 5.3. `analyze_errors` used to answer a union by counting how many
# field names a candidate branch shared with the data, which says nothing when
# the count is zero (E-SCHEMA-007) and says something false when every branch
# ties on `description:` (E-SCHEMA-010). PDL already carries a discriminator --
# pydantic uses it to validate a program in linear rather than exponential time
# -- so the analyzer asks it instead.
#
# The builders below take their vocabulary as arguments. `pdl_ast` owns the
# lists; this module states in its own docstring that it imports nothing from
# PDL, and an argument is cheaper than the cycle.
# --------------------------------------------------------------------------

_NEAR_MISS_CUTOFF = 0.7
"""`difflib` cutoff, the same one `_near_miss` and `_import_missing` use."""

_BLOCK_KIND_RULE = (
    "Every block is named by the one field that says what it does. This mapping "
    "has none of them: {names}."
)

_NOT_A_BLOCK = "this is not a PDL block: nothing here says what it does"

_NOT_A_FIELD = "{names} are not fields any block accepts."


def _oxford(names: Sequence[Any], conjunction: str = "or") -> str:
    """``\\`a\\`, \\`b\\` or \\`c\\```, from an ordered sequence and never a set.

    The list is part of a diagnostic, so its order has to be a property of the
    program rather than of `PYTHONHASHSEED`; that is the defect E-SCHEMA-010
    exists to record and it must not be reintroduced by the code that fixes it.
    """
    quoted = [f"`{name}`" for name in names]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" {conjunction} " + quoted[-1]


def prefer(names: Sequence[str], preference: Sequence[str]) -> list[str]:
    """`names` in `preference` order, with anything unlisted kept at the end.

    A diagnostic that lists PDL's own vocabulary wants the common words first,
    and a hand-written order rots the moment a field is added to `pdl_ast`.
    Deriving the membership and preferring the order gets both: a new field
    appears at the end of the list on the day it is added, and a test pins that
    the preference names nothing PDL does not have.
    """
    listed = [p for p in preference if p in names]
    return listed + [n for n in names if n not in preference]


def _first_near_miss(
    names: Sequence[str], pool: Sequence[str]
) -> tuple[str, str] | None:
    """The first ``(written, meant)`` pair in `names` with a plausible correction."""
    for name in names:
        close = difflib.get_close_matches(
            name, list(pool), n=1, cutoff=_NEAR_MISS_CUTOFF
        )
        if close:
            return name, close[0]
    return None


def _flow_value(value: Any) -> str:
    """A mapping as a one-line YAML flow value, or `""` if it is too long to show.

    `_VALUE_MAX` is the same wall the `parser:` series puts between a message
    and a value that came from outside it: a suggestion the user is meant to
    type has to fit on the line it is printed on.
    """
    try:
        dumped = yaml.safe_dump(
            value, default_flow_style=True, width=10**6, sort_keys=False
        ).strip()
    except yaml.YAMLError:
        return ""
    if "\n" in dumped or len(dumped) > _VALUE_MAX:
        return ""
    return dumped


def no_block_kind_diagnostic(  # pylint: disable=too-many-arguments
    *,
    value: Any,
    unrecognised: Sequence[tuple[str, int | None, int | None]],
    kind_fields: Sequence[str],
    near_miss_pool: Sequence[str],
    in_list: bool,
    source: str | None,
) -> Diagnostic:
    """E-SCHEMA-007. A mapping that names no kind of block.

    Today this is a 700-character single line of 24 raw `$ref`s -- the union
    printed at the reader instead of read for them. The replacement says the
    rule (a block is named by the field that says what it does), shows which of
    the user's own keys broke it, and lists the twenty-four words that would
    have worked.

    The list is deliberately not truncated. "expected one of `model`, `code`,
    ..." leaves a reader who wanted the twenty-fifth with nowhere to go, and
    this codebase has no documentation URL to send them to.

    `unrecognised` is in **document order** and carries each key's own mark, so
    the caret lands on the key rather than on the block. At most the first two
    are annotated -- `_excerpt` shows two -- and the rest are named in a note.
    """
    keys = [key for key, _, _ in unrecognised]
    spans = [
        Span(
            line=line,
            col=col,
            label=f"`{key}` does not name a block kind",
            primary=i == 0,
        )
        for i, (key, line, col) in enumerate(unrecognised)
        if line is not None
    ][:2]
    if source is not None:
        height = len(source.split("\n"))
        spans = [s for s in spans if 1 <= s.line <= height]

    notes = [Note("rule", _BLOCK_KIND_RULE.format(names=_oxford(kind_fields)))]
    if len(keys) > 1:
        notes.append(Note("note", _NOT_A_FIELD.format(names=_oxford(keys, "and"))))

    near = _first_near_miss(keys, near_miss_pool)
    if near is not None:
        written, meant = near
        suggestion = Suggestion(f"did you mean `{meant}:` instead of `{written}:`?")
    else:
        suggestion = _data_block_suggestion(value, in_list)

    return Diagnostic(
        code="E-SCHEMA-007",
        message=_NOT_A_BLOCK,
        # No `file` and no `block_path`: `located_message` adds the header
        # prefix and the `  in <path>` line at the call site, from the location
        # the analyzer is walking. Setting them here would print both twice.
        spans=spans,
        source=source,
        notes=notes,
        suggestions=[suggestion],
    )


def _data_block_suggestion(value: Any, in_list: bool) -> Suggestion:
    """The edit that turns a mapping the user meant as a value into a block.

    `data:` is the block whose job is to be a value, so this is the one
    rewriting that cannot change what the program means. Dropping the `- `
    instead -- making the mapping a field of the enclosing block -- was
    considered and rejected: applied to a list item it produces a YAML error,
    which trades a schema error for a parse error.
    """
    flow = _flow_value(value)
    subject = "item" if in_list else "block"
    if not flow:
        return Suggestion(
            f"if this {subject} is meant to produce a value rather than run a "
            "block, put it under a `data:` key."
        )
    return Suggestion(
        f"if this {subject} is meant to produce the value `{flow}`, write it as",
        replacement=("- " if in_list else "") + f"data: {flow}",
    )


def unknown_tag_diagnostic(  # pylint: disable=too-many-arguments
    *,
    written: Any,
    key: str,
    headline: str,
    rule: str,
    known: Sequence[str],
    line: int | None,
    col: int | None,
    source: str | None,
) -> Diagnostic:
    """A discriminator field whose value names no branch of its union.

    `_block_tag` returns `v.get("kind")` verbatim, so the tag can be any string
    a user cares to type. A table lookup with no miss branch would be a
    `KeyError` reaching the user as a traceback, which decision 5.8 forbids
    outright; this is that miss branch, and it is also the only thing that says
    `lang: ruby` is not a language rather than saying nothing at all.
    """
    shown = written if isinstance(written, str) else _yaml_scalar(written)
    spans = []
    if line is not None and source is not None and 1 <= line <= len(source.split("\n")):
        spans = [Span(line=line, col=col, primary=True)]
    # A near miss is the only suggestion offered. There is no correct answer to
    # `lang: ruby` -- PDL does not run Ruby -- and "use one of the languages
    # above" would restate the rule paragraph one line further down.
    close = difflib.get_close_matches(
        str(shown), list(known), n=1, cutoff=_NEAR_MISS_CUTOFF
    )
    return Diagnostic(
        code="E-SCHEMA-006",
        message=headline.format(value=shown, key=key),
        spans=spans,
        source=source,
        notes=[Note("rule", rule.format(names=_oxford(known, "and"), key=key))],
        suggestions=(
            [Suggestion(f"did you mean `{key}: {close[0]}`?")] if close else []
        ),
    )


def scalar_value_diagnostic(  # pylint: disable=too-many-arguments
    *,
    value: Any,
    field_name: str | None,
    accepted: Sequence[Any],
    mapping_keys: Sequence[str],
    removal_effect: str,
    line: int | None,
    col: int | None,
    source: str | None,
) -> Diagnostic:
    """E-SCHEMA-005. A scalar that is not one of the values its field accepts.

    INVENTORY records E-SCHEMA-005 as unreachable in practice, and it was: the
    `is_any_of` scalar arm set its "matched" flag from an alternative's `type`
    and then never unset it, so `ParserType`'s first alternative --
    `{"enum": [...], "type": "string"}`, which carries both -- accepted every
    string. The analyzer returned nothing and the caller printed the fallback
    that E-SCHEMA-006 records. This is that check made live.

    The caret carries no label. The mark PDL holds for a mapping entry is its
    **key** (`_walk`), so the caret sits under `parser` while the offending
    token is the value beside it; a label there would name the wrong word. The
    whole entry is on the excerpt line and visible.
    """
    if field_name:
        message = f"`{_scalar_text(value)}` is not a valid value for `{field_name}:`"
        rule_subject = f"`{field_name}:` accepts"
        removal = f"remove `{field_name}:`{removal_effect}, or use"
    else:
        message = f"`{_scalar_text(value)}` is not a valid value here"
        rule_subject = "This field accepts"
        removal = "use"

    rule = f"{rule_subject} {_oxford(accepted)}"
    if mapping_keys:
        # `regex:` with the colon, because that is how the user types it and
        # because a bare `regex` reads as a value rather than as a key.
        rule += f", or a mapping with a {_oxford([k + ':' for k in mapping_keys])} key"
    rule += "."

    close = difflib.get_close_matches(
        str(value), [str(a) for a in accepted], n=1, cutoff=_NEAR_MISS_CUTOFF
    )
    if close and field_name:
        suggestion = Suggestion(f"did you mean `{field_name}: {close[0]}`?")
    elif close:
        suggestion = Suggestion(f"did you mean `{close[0]}`?")
    else:
        suggestion = Suggestion(f"{removal} one of {_oxford(accepted)}.")

    spans = []
    if line is not None and source is not None and 1 <= line <= len(source.split("\n")):
        spans = [Span(line=line, col=col, primary=True)]
    return Diagnostic(
        code="E-SCHEMA-005",
        message=message,
        spans=spans,
        source=source,
        notes=[Note("rule", rule)],
        suggestions=[suggestion],
    )


def _scalar_text(value: Any) -> str:
    """A scalar as the user wrote it, without JSON's quotes around a string."""
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return _yaml_scalar(value)


# --------------------------------------------------------------------------
# Shapes: E-SCHEMA-009
#
# Four diagnostics about the *shape* of a value rather than its contents: a
# list where a mapping belongs, a mapping where a list belongs, a list in a
# field that takes one value, and a list of the wrong length. They share a
# vocabulary and a set of rules, so they share a module section.
#
# Two conventions run through all four.
#
# The offending value is rendered as **YAML**, through `_yaml_value`, and never
# as `str(data)`. What `analyze_errors` holds is `yaml.safe_load`'s output, so
# `str()` on it is a Python `repr`: the user who wrote two indented lines was
# shown `[{'text': 'recovering'}]`, in Python's quoting and Python's brackets.
# That is the leak RUBRIC.md scores Hygiene 1, and `_yaml_value` is the whole of
# the fix -- the same wall (`_VALUE_MAX`) applies, so a large value is described
# rather than dumped.
#
# Every suggestion carrying a `replacement` is a **mechanical rewrite of the
# user's own value**, checked against the schema before it is offered (see
# `_suggestable` in `pdl_schema_error_analyzer`). None of them is a fixed
# example: `contribute: [result]` is the user's `result` in a list,
# `defs: {greeting: hi}` is the user's own list items merged, and
# `jitter: [1, 2]` is the user's own first two items. An illustration invented
# here could not be checked and would be a confidently-stated wrong edit, which
# the rubric ranks below saying nothing.
# --------------------------------------------------------------------------

_SHAPE_CODE = "E-SCHEMA-009"

_YAML_SHAPES: tuple[tuple[type | tuple[type, ...], str], ...] = (
    # Ordered, and `bool` before `int` because it is a subclass of it.
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    ((list, tuple), "list"),
    (dict, "mapping"),
)
"""YAML's shapes in the words PDL's own prose uses for them.

Not `_PDL_TYPES`, which answers `array` and `object` because it names the types
a `spec:` declares. These messages are about the text the user typed, and there
they wrote a list and a mapping; `object` is JSON Schema's noun and appears
nowhere in the PDL documentation a reader would go and look in.
"""


def _yaml_shape(value: Any) -> str:
    if value is None:
        return "null"
    for kinds, name in _YAML_SHAPES:
        if isinstance(value, kinds):
            return name
    return "value"


def yaml_value(value: Any) -> str:
    """The value as YAML, on one line, or `""` when it is too long to show."""
    if isinstance(value, (dict, list, tuple)):
        return _flow_value(value)
    text = _scalar_text(value)
    return text if text and len(text) <= _VALUE_MAX and "\n" not in text else ""


def _subject(field_name: str | None, subject: str) -> str:
    """What the claim is about.

    A field name comes from the location the analyzer is walking and is right
    whenever the schema being walked is PDL's own. It is *not* right for a
    `spec:` or an `args:` check, where the same walk validates a value the
    program produced against a schema the program declared: there `loc.path`
    ends in `spec`, and "`spec:` should be a list" would blame the declaration
    for the result. Those callers pass `subject` instead, and it wins.
    """
    if subject:
        return subject
    if field_name:
        return f"`{field_name}:`"
    return "this value"


def _found(value: Any) -> str:
    """``\\`result\\` is a string``, or the shape alone when the value is too big.

    The subject is not repeated in the fallback. It is already the head of the
    sentence, so `` `defs:` should be a mapping, but `defs:` is a list`` names
    the same thing twice and reads as a claim about the word.
    """
    shown = yaml_value(value)
    shape = _with_article(_yaml_shape(value))
    return f"`{shown}` is {shape}" if shown else f"it is {shape}"


def _shape_diagnostic(
    *,
    headline: str,
    rule: str,
    suggestion: Suggestion | None,
    spans: Sequence[Span],
    source: str | None,
) -> Diagnostic:
    """Assemble one shape diagnostic. One shape, four callers.

    No `file` and no `block_path`: `located_message` adds the header prefix and
    the `  in <path>` line at the call site, as it does for every other
    diagnostic the analyzer builds.
    """
    shown: Sequence[Span] = ()
    if source:
        height = len(source.split("\n"))
        shown = [s for s in spans if 1 <= s.line <= height]
    return Diagnostic(
        code=_SHAPE_CODE,
        message=headline,
        spans=list(shown),
        source=source,
        notes=[Note("rule", rule)] if rule else [],
        suggestions=[suggestion] if suggestion is not None else [],
    )


def list_expected_diagnostic(  # pylint: disable=too-many-arguments
    *,
    field_name: str | None,
    subject: str = "",
    value: Any,
    item_values: Sequence[Any] = (),
    item_values_exhaustive: bool = False,
    wrapped: str = "",
    spans: Sequence[Span] = (),
    source: str | None = None,
) -> Diagnostic:
    """A value where the schema wants a list of them.

    ``wrapped`` is the one-element list the user's own value makes, rendered as
    YAML, and it is set only when the analyzer has checked that it satisfies the
    schema. `contribute: result` is the shape this exists for: the value is a
    perfectly good *item* and the only thing missing is the brackets.

    ``item_values_exhaustive`` is the difference between "is one of" and "may
    be". `ContributeElement` enumerates four targets and *also* admits a mapping,
    so listing the four as the accepted set would be false; naming them as
    possibilities is not.
    """
    subj = _subject(field_name, subject)
    headline = f"{subj} should be a list, but {_found(value)}"

    # The rule paragraph is emitted only when it carries something the headline
    # does not: what the items are, or that a single value still needs brackets.
    # "the block's result is a list." under "the block's result should be a
    # list" is a second copy of the claim, not evidence for it.
    rule = ""
    if wrapped or item_values:
        if subject or field_name:
            rule = f"{subj} is a list"
        else:
            rule = "A list is expected here"
        rule += ", even when it has only one element." if wrapped else "."
        if item_values:
            opener = (
                "Each item is one of" if item_values_exhaustive else "Each item may be"
            )
            rule += f" {opener} {_oxford(item_values)}."

    suggestion = None
    if wrapped and field_name:
        suggestion = Suggestion("put the value in a list:", f"{field_name}: {wrapped}")
    elif wrapped:
        suggestion = Suggestion(f"put the value in a list: `{wrapped}`.")
    return _shape_diagnostic(
        headline=headline,
        rule=rule,
        suggestion=suggestion,
        spans=spans,
        source=source,
    )


def mapping_expected_diagnostic(  # pylint: disable=too-many-arguments
    *,
    field_name: str | None,
    subject: str = "",
    value: Any,
    key_names: Sequence[str] = (),
    open_keys: bool = False,
    merged: str = "",
    spans: Sequence[Span] = (),
    source: str | None = None,
) -> Diagnostic:
    """A list, or a scalar, where the schema wants a mapping.

    ``merged`` is the user's own list of single-entry mappings folded into one
    mapping, rendered as YAML, and set only when the analyzer has checked that
    the result satisfies the schema. Writing `defs:` as a list of definitions is
    the mistake this exists for, and the edit that repairs it is exactly that
    fold -- no key and no value is invented.
    """
    subj = _subject(field_name, subject)
    headline = f"{subj} should be a mapping, but {_found(value)}"

    rule = (
        f"{subj} is a mapping of `key: value` entries"
        if subject or field_name
        else "A mapping of `key: value` entries is expected here"
    )
    if key_names:
        rule += f". Its keys are {_oxford(key_names)}."
    elif open_keys:
        rule += ", and its keys are names you choose."
    else:
        rule += "."

    suggestion = None
    if merged and field_name:
        suggestion = Suggestion(
            "write the entries as one mapping:", f"{field_name}: {merged}"
        )
    elif merged:
        suggestion = Suggestion(f"write the entries as one mapping: `{merged}`.")
    return _shape_diagnostic(
        headline=headline,
        rule=rule,
        suggestion=suggestion,
        spans=spans,
        source=source,
    )


def single_value_diagnostic(  # pylint: disable=too-many-arguments,too-many-branches
    *,
    field_name: str | None,
    subject: str = "",
    value: Any,
    takes_a_block: bool = False,
    accepted: Sequence[Any] = (),
    mapping_keys: Sequence[str] = (),
    only: str = "",
    in_order: str = "",
    spans: Sequence[Span] = (),
    source: str | None = None,
) -> Diagnostic:
    """A list in a field that takes one value.

    The message this replaces said `should not be a list` and stopped there: a
    prohibition with no expectation, which is Why 1 by the dimension's own
    wording. What the field *does* take is knowable in both of the shapes that
    reach here -- a block, when the union is `BlockType`, and otherwise whatever
    the union's members enumerate -- so it is said.

    ``only`` is the list's single element when it has exactly one, and
    ``in_order`` the `text:` block that runs several in sequence. Both are
    rendered YAML and both are set only after the analyzer has checked them
    against the schema.
    """
    subj = _subject(field_name, subject)
    expected = "one block" if takes_a_block else "a single value"
    headline = f"{subj} should be {expected}, but {_found(value)}"

    if takes_a_block:
        rule = (
            f"{subj} is one block, not a list of blocks."
            if subject or field_name
            else "One block is expected here, not a list of blocks."
        )
        if in_order:
            rule += " A block that runs several blocks in order is a `text:` block."
    elif accepted:
        opener = (
            f"{subj} accepts" if subject or field_name else "The accepted values are"
        )
        rule = f"{opener} {_oxford(accepted)}"
        if mapping_keys:
            rule += (
                f", or a mapping with a {_oxford([k + ':' for k in mapping_keys])} key"
            )
        rule += "."
    elif subject or field_name:
        rule = f"{subj} takes a single value, not a list."
    else:
        rule = "A single value is expected here, not a list."

    suggestion = None
    if only and field_name:
        what = "block" if takes_a_block else "value"
        suggestion = Suggestion(
            f"write the one {what} on its own:", f"{field_name}: {only}"
        )
    elif in_order and field_name:
        suggestion = Suggestion(
            "run them in order from one block:", f"{field_name}: {in_order}"
        )
    return _shape_diagnostic(
        headline=headline,
        rule=rule,
        suggestion=suggestion,
        spans=spans,
        source=source,
    )


def list_length_diagnostic(  # pylint: disable=too-many-arguments
    *,
    field_name: str | None,
    subject: str = "",
    count: int,
    minimum: int | None,
    maximum: int | None,
    positions: Sequence[str] = (),
    kept: str = "",
    spans: Sequence[Span] = (),
    source: str | None = None,
) -> Diagnostic:
    """A list whose length the schema constrains. E-SCHEMA-009's S0 entry.

    Reached by `retry: {jitter: [1, 2, 3]}`, which until this diagnostic existed
    crashed the analyzer: `jitter:` is a number or a `[min, max]` pair, pydantic
    renders the pair as `prefixItems` with `minItems` and `maxItems` and **no**
    `items` key, and the array arm subscripted `schema["items"]` for every
    element. A `prefixItems` schema is a tuple -- fixed length, a type per
    position -- so guarding the subscript alone would have answered a length
    error with silence. The length is the error, and it is what is said.

    ``positions`` names the type at each position when the schema fixes them, so
    the rule can show the shape (`[number, number]`) rather than assert a count.
    ``kept`` is the user's own list truncated to the maximum, set only when the
    analyzer has checked that the truncation satisfies the schema.

    The subject is **the list**, never the field. `jitter:` is a number *or* a
    pair, and the pair is the alternative the union arm selected because it is
    the one the user wrote; "`jitter:` is a fixed-length list" would be a claim
    about the field that the schema next door contradicts. What is true, and
    what is said, is that the list they wrote has a length.
    """
    if field_name:
        subj = f"the list in `{field_name}:`"
    elif subject:
        subj = subject
    else:
        subj = "this list"
    if minimum is not None and minimum == maximum:
        want = f"exactly {minimum} items" if minimum != 1 else "exactly one item"
    elif maximum is not None and count > maximum:
        want = f"at most {maximum} items" if maximum != 1 else "at most one item"
    else:
        want = f"at least {minimum} items" if minimum != 1 else "at least one item"
    headline = f"{subj} should have {want}, but it has {count}"

    # Without the positions the rule would be the headline again.
    rule = ""
    if positions:
        shape = "[" + ", ".join(positions) + "]"
        rule = f"A list here has {want}, written `{shape}`."

    suggestion = None
    if kept and field_name and maximum is not None:
        keep = f"the first {maximum} items" if maximum != 1 else "the first item"
        suggestion = Suggestion(f"keep {keep}:", f"{field_name}: {kept}")
    return _shape_diagnostic(
        headline=headline,
        rule=rule,
        suggestion=suggestion,
        spans=spans,
        source=source,
    )


_UNLOCATED_SCHEMA_FAILURE = (
    "the program does not match the PDL schema, and PDL cannot say where"
)
_UNLOCATED_SCHEMA_FAILURE_UNNAMED = (
    "the PDL program does not match the PDL schema, and PDL cannot say where"
)
_UNLOCATED_RULE = (
    "PDL's validator rejected this program, but the analyzer that turns a "
    "rejection into a located message did not recognise the failure, so nothing "
    "more precise can be said about it."
)
_UNLOCATED_NOTE = (
    "reaching this message is a gap in PDL's error reporting rather than extra "
    "information about your program. Reporting the program that produced it is "
    "the only way that gap gets closed."
)
_UNLOCATED_HELP = "remove blocks until the message changes, to find the one at fault."


def unlocated_schema_diagnostic(file: str) -> Diagnostic:
    """E-SCHEMA-006. The validator said no and the analyzer could not say where.

    Today this reads `The file PDL prog.pdl does not respect the schema.`, which
    states the one thing the user already knows and hides the fact that PDL, not
    the program, is what ran out of things to say.

    No span. Claiming line 1 would put a confidently wrong location on a
    diagnostic whose entire content is that no location is known, and
    `RUBRIC.md` ranks that below showing none.
    """
    named = bool(file)
    return Diagnostic(
        code="E-SCHEMA-006",
        message=(
            _UNLOCATED_SCHEMA_FAILURE if named else _UNLOCATED_SCHEMA_FAILURE_UNNAMED
        ),
        file=file,
        notes=[Note("rule", _UNLOCATED_RULE), Note("note", _UNLOCATED_NOTE)],
        suggestions=[Suggestion(_UNLOCATED_HELP)],
    )
