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
as `line N - ` and the second as `<program>:N - `.
"""


def is_unnamed(file: str) -> bool:
    """True for a location with no user-facing file name, of either kind."""
    return file in ("", UNNAMED_SOURCE)


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

    Every source parsed without a file name is registered under the single key
    `UNNAMED_SOURCE`, so two *different* unnamed sources alive at once share one
    entry, last registration winning. The reachable case is `exec_str` of a
    program that itself contains a `lang: pdl` block: the inner code is parsed
    unnamed too, and locations built in the outer program *after* that point
    resolve against the inner source's marks. Both are unnamed, so the file name
    in the message is right either way; the line can be wrong.

    Fixing it means giving each unnamed source a distinct name, and that name is
    user-visible -- it is printed in diagnostics and serialised into traces --
    so it is a naming decision, not an implementation detail. It is recorded
    here rather than invented.
    """

    def __init__(self) -> None:
        self._sources: dict[str, PdlSource] = {}
        self._lock = threading.Lock()

    def register(
        self, file: str, text: str, marks: Mapping[str, SourceMark]
    ) -> PdlSource:
        source = PdlSource(file=file, text=text, marks=dict(marks))
        with self._lock:
            self._sources[file] = source
        return source

    def get(self, file: str) -> PdlSource | None:
        with self._lock:
            return self._sources.get(file)

    def text_of(self, file: str) -> str | None:
        """The source text of a file, for excerpts and carets at render time."""
        source = self.get(file)
        return None if source is None else source.text

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
    """`file:line - `, the prefix every legacy diagnostic is built from.

    The column is carried on the location but not rendered here. Putting it in
    this prefix would rewrite the header of every diagnostic in the corpus in one
    step, which is a renderer decision (5.6) and not part of the location model.

    `line N - `, with no file, is now reserved for a location with no source at
    all: a program built as a dict, or a block reached before any file was
    known. A program parsed from a string has a source and says so, as
    `<program>:N - `, which is the label its YAML errors already used.
    """
    if loc.file == "":
        return "line " + str(loc.line) + " - "
    return loc.file + ":" + str(loc.line) + " - "
