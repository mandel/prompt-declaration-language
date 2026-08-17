"""Source locations: real YAML marks, a source registry, and path rendering.

The model, after decisions 5.1/5.2 of `docs/error-reporting/INVENTORY.md`:

* `load_with_marks` parses a PDL source through PyYAML's own composer and keeps
  the `start_mark`/`end_mark` PyYAML already computes for every node. That is an
  exact line **and** column for every mapping key and every sequence item, from
  the parser that decides what those things are -- replacing a regex line-scan
  that split each line on `":"` and could not be right for a quoted key, a flow
  mapping, a multi-line scalar or a comment (DROP #1 and DROP #2).
* The resulting map is per *file*, so it lives in a per-file `PdlSource` in the
  `SourceRegistry`, not inside every location value. `PdlLocationType` carries
  `(file, line, col, path)` and no table, which is what makes DROP #6 --
  `execute_call` building a location out of the callee's path and the *caller's*
  table -- unrepresentable rather than merely fixed.
* `append` resolves the new path against that file's marks as it descends, so a
  location knows its own line at the moment it is built, from the one file it
  belongs to.
* Every source needs a key, including the ones with no file name. A string
  program is `<program>`; a program a running program produced is named for the
  route to it, `<program:hello.pdl#text[0].code>` (`nested_source_name`).

The registry is process-global because a location is a plain value that travels
into traces and exceptions and must stay cheap to copy; the alternative is
threading a parser handle through the ~40 functions that already thread `loc`,
which is issue #203's complaint rather than a fix for it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

import yaml

from .pdl_ast import PdlLocationType
from .pdl_diagnostics import join_path

UNNAMED_SOURCE = "<program>"
"""What a source parsed from a string with no file name is called.

Not a new invention: `parse_str` already labels such a source `<program>` in
every YAML error it raises, and `tests/test_parse_errors.py` pins that text. It
is reused here because the registry needs a *key*, and the empty string cannot be
it. The empty string already means "no source at all" -- it is
`empty_block_location.file`, carried by every block of a program handed to
`exec_dict`, and by every location built before a file is known. Registering an
anonymous source under `""` makes those two meanings share an entry, and a
program with no source then reports line numbers belonging to whatever string
was parsed most recently. That was observed, not predicted: it turned
`tests/test_errors.py` from `line 0 - ` into `line 8 - ` as soon as another test
in the same process had parsed a string.

So `""` stays the unknown location, is never registered, and never resolves;
`<program>` is a real source with real marks. `get_loc_string` renders the first
as `line N - ` -- no marks, so no column to print -- and the second as
`<program>:N:C - `.

A source that a *running* program produced -- the `code:` of a `lang: pdl` block
-- is not this. It gets a name of its own; see `nested_source_name`.
"""

NESTED_SOURCE_PREFIX = "<program:"
NESTED_SOURCE_SUFFIX = ">"


def is_unnamed(file: str) -> bool:
    """True for a location with no file the user could open.

    Three shapes qualify: `""` (no source at all), `<program>` (a string handed
    to `exec_str`), and `<program:...>` (a program a running program produced).
    The distinction that matters to a caller is not "does this have a name" but
    "may I tell the user to go and edit this path", and for all three the answer
    is no.
    """
    return file in ("", UNNAMED_SOURCE) or (
        file.startswith(NESTED_SOURCE_PREFIX) and file.endswith(NESTED_SOURCE_SUFFIX)
    )


def source_hint(file: str) -> str:
    """The part of `file` that identifies it *inside* a nested source's name.

    A real file name is its own hint. `<program>` has none -- there is only one
    of it, and `<program:<program>#...>` would nest brackets to say nothing. An
    already-nested name contributes its chain, so nesting composes left to right
    instead of one bracket per level.
    """
    if file in ("", UNNAMED_SOURCE):
        return ""
    if file.startswith(NESTED_SOURCE_PREFIX) and file.endswith(NESTED_SOURCE_SUFFIX):
        return file[len(NESTED_SOURCE_PREFIX) : -len(NESTED_SOURCE_SUFFIX)]
    return file


def nested_source_name(loc: PdlLocationType) -> str:
    """The registry key for a source found at `loc` inside another source.

    `loc` is the location of the *field holding the program text* -- the `code:`
    of a `lang: pdl` block -- so the name reads as a route to it:

        <program:hello.pdl#text[0].code>            a file's nested program
        <program:text[0].code>                      a string program's
        <program:hello.pdl#text[0].code#defs.f.code>    two levels down

    Three properties, in the order they were argued for:

    * **Qualified by the containing file, not just the path.** Two different
      `.pdl` files with a `lang: pdl` block at the same path run in one process
      -- one importing the other, say -- would otherwise be handed the same key,
      which is the bug this naming exists to fix, moved rather than removed.
    * **Readable.** It is printed in every diagnostic about the nested program
      and it is what a user has to act on. `text[0].code` is where to go and
      look; a hash or a counter is unique and tells them nothing. The spelling
      is the block path `Diagnostic` already renders (`join_path`), so a
      diagnostic's `  in <path>` line and this name use one syntax.
    * **Not mistakable for a file.** The angle brackets are `<program>`'s, and
      `is_unnamed` covers the whole family, so nothing invites the user to open
      a path that does not exist.

    `#` separates the containing source from the path within it, as in a URL
    fragment, and chains for deeper nesting. It cannot be `:`, which
    `get_loc_string` uses to attach the line number.

    What this does **not** make unique: one site whose text changes between runs
    -- a `lang: pdl` block inside a `for:` loop, whose `code:` interpolates the
    loop variable -- keeps one name for every iteration. See
    `SourceRegistry.register` for what the registry does about that.
    """
    hint = source_hint(loc.file)
    inner = join_path(loc.path)
    chain = f"{hint}#{inner}" if hint and inner else hint or inner
    if not chain:
        return UNNAMED_SOURCE
    return f"{NESTED_SOURCE_PREFIX}{chain}{NESTED_SOURCE_SUFFIX}"


@dataclass(frozen=True)
class SourceMark:
    """Where one node of a PDL source begins and ends. 1-based, like an editor.

    `end_line`/`end_col` are PyYAML's `end_mark`, which for a block collection is
    the start of the token *after* it rather than the last character of the
    collection. Nothing renders an end position yet; it is recorded because the
    caret spans of rubric item 1 need it and recomputing it later would mean
    re-parsing.
    """

    line: int
    col: int
    end_line: int
    end_col: int


def _mark_of(node: yaml.nodes.Node) -> SourceMark:
    return SourceMark(
        line=node.start_mark.line + 1,
        col=node.start_mark.column + 1,
        end_line=node.end_mark.line + 1,
        end_col=node.end_mark.column + 1,
    )


def path_key(path: Sequence[str]) -> str:
    """The key a block path is recorded under. `str(list)`, as `get_line` used."""
    return str(list(path))


def _walk(node: yaml.nodes.Node, path: list[str], out: dict[str, SourceMark]) -> None:
    """Record every child of `node`; `node` itself was recorded by the caller.

    A mapping entry is recorded at its **key**, not at its value: that is the
    position a user points at when asked where `text:` is, it is what the regex
    line map reported, and it is the only one of the two that exists for a field
    whose value is missing. The key's mark is paired with the *value's* end, so
    the span covers the whole entry.

    Non-scalar keys (`? [a, b]: x`) are skipped rather than stringified. They are
    legal YAML that PDL has no path syntax for, and inventing one would produce
    a path no `append` call site can ever look up.
    """
    if isinstance(node, yaml.nodes.MappingNode):
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.nodes.ScalarNode):
                continue
            child = path + [str(key_node.value)]
            start = _mark_of(key_node)
            end = _mark_of(value_node)
            out[path_key(child)] = SourceMark(
                line=start.line,
                col=start.col,
                end_line=end.end_line,
                end_col=end.end_col,
            )
            _walk(value_node, child, out)
    elif isinstance(node, yaml.nodes.SequenceNode):
        for index, item_node in enumerate(node.value):
            child = path + [f"[{index}]"]
            out[path_key(child)] = _mark_of(item_node)
            _walk(item_node, child, out)


def load_with_marks(source: str) -> tuple[Any, dict[str, SourceMark]]:
    """Parse a PDL source, returning the same data as `yaml.safe_load` plus marks.

    Composing the node graph first and constructing from it is what
    `yaml.safe_load` does internally; the only difference here is that the graph
    is kept long enough to read the marks off it. Every `yaml.YAMLError`
    `safe_load` raises is raised from the same places, with the same marks, so
    the parser's boundary handling is unaffected.

    An alias makes the same node reachable by more than one path. Both paths are
    recorded and both point at the anchor's definition site, which is where the
    text actually is.
    """
    loader = yaml.SafeLoader(source)
    try:
        node = loader.get_single_node()
        if node is None:
            return None, {}
        marks = {path_key([]): _mark_of(node)}
        _walk(node, [], marks)
        return loader.construct_document(node), marks
    finally:
        loader.dispose()


@dataclass(frozen=True)
class PdlSource:
    """One parsed PDL source: its text, and where each block path sits in it."""

    file: str
    text: str
    marks: Mapping[str, SourceMark]
    contested: bool = False
    """Some *other* text was registered under this name earlier in the run.

    Set by `SourceRegistry.register`, never cleared. It does not make the marks
    below wrong -- they are this text's -- but it does mean a location built
    before the change names a source whose text has moved underneath it, and
    nothing recorded on a location says which of the two it meant. `text_of`
    therefore stops answering for a contested name; see there.
    """

    def mark(self, path: Sequence[str]) -> SourceMark | None:
        """The mark recorded for exactly this path, or None."""
        return self.marks.get(path_key(path))

    def resolve(self, path: Sequence[str]) -> SourceMark | None:
        """The mark for this path, else the nearest ancestor's, else None.

        The ancestor walk is DROP #4 and it is kept deliberately, with its
        meaning narrowed. It used to be the *normal* case -- the regex map had no
        entry for a flow-sequence element, for the document root, or for any key
        it mis-split, so an ancestor's line was what most lookups returned, with
        nothing to say so. With real marks every key and every item is present,
        so a miss now means the path does not exist in the source at all: a
        synthetic segment, or a block built at runtime. For those, an enclosing
        block's position is the honest answer and the only one available.
        """
        segments = list(path)
        while True:
            found = self.marks.get(path_key(segments))
            if found is not None:
                return found
            if not segments:
                return None
            segments.pop()


class SourceRegistry:
    """Parsed sources by file name, for the data that is per-file, not per-node.

    Keyed by the same string that ends up in `PdlLocationType.file`, so a
    location can always find its own source and can never be resolved against
    another file's -- the shape of DROP #6.

    A key is *not* guaranteed to name one text for the length of a run, and the
    registry is explicit about that rather than pretending otherwise. Two cases
    remain after `nested_source_name` gave every nested program a key of its own
    (both are documented in `docs/error-reporting/INVENTORY.md` 7.7):

    * one nested site whose text changes between iterations -- a `lang: pdl`
      block in a `for:` loop whose `code:` interpolates the loop variable;
    * two threads each running `exec_str`, both of them `<program>`.

    `register` detects the second registration of a *different* text and marks
    the entry `contested`. Line and column are unaffected: they are resolved
    when a location is built, against the marks in force at that moment, and are
    frozen on the location from then on. What a contested key costs is the
    *text*, which a renderer would read much later; see `text_of`.
    """

    def __init__(self) -> None:
        self._sources: dict[str, PdlSource] = {}
        self._lock = threading.Lock()

    def register(
        self, file: str, text: str, marks: Mapping[str, SourceMark]
    ) -> PdlSource:
        """Record a parsed source, or re-assert one already recorded.

        Re-registering the identical text is a no-op that returns the existing
        entry -- marks are a pure function of the text, so there is nothing to
        update, and skipping the rebuild is what makes it cheap enough for
        `parse_str` to call on every hit of its own cache. That call matters:
        without it, a cached re-parse of text A would leave the registry holding
        text B's marks, and every location built during that run of A would
        resolve against them.
        """
        with self._lock:
            existing = self._sources.get(file)
            if existing is not None and existing.text == text:
                return existing
            source = PdlSource(
                file=file,
                text=text,
                marks=dict(marks),
                contested=existing is not None,
            )
            self._sources[file] = source
            return source

    def get(self, file: str) -> PdlSource | None:
        with self._lock:
            return self._sources.get(file)

    def text_of(self, file: str) -> str | None:
        """The source text of a file, for excerpts and carets at render time.

        `None` for a contested name, where the honest answer is that the
        registry no longer knows which text a given location meant. An excerpt
        drawn from the wrong text is not a smaller error than no excerpt: the
        line number is right, the line quoted under it is some other program's,
        and nothing on the page says so. `RUBRIC.md` ranks that below showing
        nothing.

        This is the fallback path. A diagnostic that captures its excerpt when
        it is *built* -- which is what every boundary diagnostic already does,
        by taking the source text as an argument -- is not affected by any of
        this, and is the pattern to prefer.
        """
        source = self.get(file)
        if source is None or source.contested:
            return None
        return source.text

    def mark(self, file: str, path: Sequence[str]) -> SourceMark | None:
        source = self.get(file)
        return None if source is None else source.mark(path)

    def resolve(self, file: str, path: Sequence[str]) -> SourceMark | None:
        source = self.get(file)
        return None if source is None else source.resolve(path)

    def clear(self) -> None:
        with self._lock:
            self._sources.clear()

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._sources))


SOURCES = SourceRegistry()
"""The process-wide registry. `parse_str` fills it; renderers read it."""


def register_source(file: str, text: str, marks: Mapping[str, SourceMark]) -> PdlSource:
    return SOURCES.register(file, text, marks)


def get_source(file: str) -> PdlSource | None:
    return SOURCES.get(file)


def source_text(file: str) -> str | None:
    return SOURCES.text_of(file)


def program_location(file: str, marks: Mapping[str, SourceMark]) -> PdlLocationType:
    """The location of a whole program: its root node, path `[]`.

    The root has a mark like any other node, which is why a top-level block no
    longer reports line 0. `get_line` returned a literal `0` for the empty path
    and `tests/test_line_table.py` asserted it twice.
    """
    root = marks.get(path_key([]))
    return PdlLocationType(
        file=file,
        path=[],
        line=0 if root is None else root.line,
        col=0 if root is None else root.col,
    )


def append(loc: PdlLocationType, seg: str) -> PdlLocationType:
    """Descend one segment, resolving the new position from `loc`'s own file.

    An exact mark or nothing: on a miss the parent's line and column are carried
    down unchanged, which is the same answer `get_line`'s ancestor walk gave and
    is reached here without a second lookup, because the parent's position was
    resolved the same way when it was built.
    """
    path = loc.path + [seg]
    mark = SOURCES.mark(loc.file, path)
    if mark is None:
        return PdlLocationType(file=loc.file, path=path, line=loc.line, col=loc.col)
    return PdlLocationType(file=loc.file, path=path, line=mark.line, col=mark.col)


def get_loc_string(loc: PdlLocationType) -> str:
    """`file:line:col - `, the prefix every legacy diagnostic is built from.

    The column is the one `append` resolved from the YAML mark of the block the
    path names -- the same mark the line comes from, so the two coordinates are
    always the same point and never disagree about which construct is meant.
    Where that construct is coarser than the offending element inside it -- the
    `code:` key of a `code:` block whose failing statement is three lines down --
    the column is the exact position of the key, exactly as the line is the exact
    line of the key. It says no more and no less than the line beside it;
    `docs/error-reporting/INVENTORY.md` 7.9 measured that coarseness, and it is
    neither cured nor worsened by a horizontal coordinate.

    `0` means unknown on `PdlLocationType`, and an unknown column is dropped
    rather than printed as `:0`, which would be a position no 1-based file has.
    That is the rule `pdl_diagnostics._header` already applies to the structured
    diagnostics, so the two renderers spell a location the same way. It is not a
    precision judgement: a *known* column is always rendered, however coarse the
    mark it came from, because a reader cannot be asked to infer PDL's confidence
    from the shape of a header.

    `line N - `, with no file, is reserved for a location with no source at all:
    a program built as a dict, or a block reached before any file was known.
    Those locations have no marks, so their column is unknown and the form is
    unchanged in practice; it takes a column by the same rule if one is ever
    known, since a line without a file is no more useful than a column without
    one. A program parsed from a string has a source and says so, as
    `<program>:N:C - `, which is the label its YAML errors already used.

    The block path is not here either, for a reason that is about shape rather
    than policy: it is rendered on a line of its own (`  in text[0].code`), and a
    prefix cannot place a line after the text it prefixes. `located_message`
    below is the whole header, and is what a diagnostic site should call.
    """
    col = ":" + str(loc.col) if loc.col else ""
    if loc.file == "":
        return "line " + str(loc.line) + col + " - "
    return loc.file + ":" + str(loc.line) + col + " - "


def located_message(loc: PdlLocationType, message: str) -> str:
    """One legacy diagnostic: its `file:line:col - ` header, its `  in <path>` line.

    This is DROP #10. `loc.path` -- the block path the rubric asks for -- was
    computed on every location, carried all the way to the print site, and
    thrown away there, because `get_loc_string` rendered only `file:line`.

    The spelling is not a new one. `pdl_diagnostics.render` has emitted the path
    as `  in <path>` under the header since the boundary diagnostics, using
    `join_path`, and E-CLI-004 and E-RUNTIME-002 show it in the corpus today.
    Legacy diagnostics join that convention rather than inventing a second one,
    so a reader cannot tell from the shape of a location whether the diagnostic
    behind it was built as a `Diagnostic` or as a string.

    The path goes *after the first line* of `message`, not after all of it. A
    legacy message is frequently a whole rendered document -- E-CODE-001's
    excerpt and rule paragraphs arrive here as one string -- and the `in` line
    belongs under the header it qualifies, as it does in `render`, not at the
    foot of the evidence.

    An empty path emits no line. That is a real case and not a degenerate one:
    a diagnostic about the program as a whole has path `[]`, and `  in ` with
    nothing after it would claim a location inside a block that does not exist.

    It lives here rather than in `pdl_diagnostics` because it needs
    `PdlLocationType`, and that module is deliberately free of PDL imports so
    that it cannot form a cycle with the parser it serves. Here it also sits
    next to the prefix it replaces.
    """
    head, newline, rest = message.partition("\n")
    out = get_loc_string(loc) + head
    path = join_path(loc.path)
    if path:
        out += "\n  in " + path
    return out + newline + rest
