"""The location model: YAML marks, the source registry, and `append`.

`tests/test_line_table.py` covers the same machinery from the outside, by
asserting the `file:line - message` a user sees. This file covers it from the
inside, and each case here is a shape the regex line map it replaced could not
get right. Where that is the point of the case, the old answer is in the
docstring, measured against `get_line_map` at commit `87958e4` rather than
recalled.
"""

from pdl.pdl_ast import PdlLocationType, empty_block_location
from pdl.pdl_location_utils import (
    UNNAMED_SOURCE,
    append,
    get_loc_string,
    load_with_marks,
    program_location,
    register_source,
)
from pdl.pdl_parser import parse_str


def marks_of(source: str) -> dict[str, tuple[int, int]]:
    _, marks = load_with_marks(source)
    return {key: (mark.line, mark.col) for key, mark in marks.items()}


def test_data_is_what_safe_load_would_have_returned():
    import yaml  # local: the point is the comparison, not the dependency

    source = "text:\n  - a\n  - b: {c: 1}\nanchors: &a [1, 2]\nalias: *a\n"
    data, _ = load_with_marks(source)
    assert data == yaml.safe_load(source)


def test_an_empty_document_has_no_marks():
    assert load_with_marks("") == (None, {})
    assert load_with_marks("# just a comment\n") == (None, {})


def test_the_document_root_has_a_position():
    """`get_line` returned a literal 0 for the empty path.

    That is the `file:0` of E-RUNTIME-004 and E-CODE-001: a one-block program's
    only block was the one thing in the file that could not be located.
    """
    assert marks_of("read: nofile.txt\n")["[]"] == (1, 1)
    assert marks_of("\n\n\nread: nofile.txt\n")["[]"] == (4, 1)


def test_a_comment_is_not_structure():
    """Old: `['text', '[1]']` was line 2. The error is on line 5 (E-EXPR-006)."""
    source = '# a comment: with colon\ntext:\n  # another - comment\n  - "x"\n  - ${ nope }\n'
    marks = marks_of(source)
    assert marks["['text']"] == (2, 1)
    assert marks["['text', '[0]']"] == (4, 5)
    assert marks["['text', '[1]']"] == (5, 5)


def test_a_quoted_key_containing_a_colon_is_one_key():
    """Old: the key was recorded as `"a`, so `a: b` could never be found."""
    marks = marks_of('defs:\n  "a: b": 1\ntext: hello\n')
    assert marks["['defs', 'a: b']"] == (2, 3)


def test_a_hyphenated_key_keeps_its_hyphen():
    """Old: `get_line_map` did `.replace("-", "")`, recording `mydef`."""
    marks = marks_of("defs:\n  my-def:\n    data: 1\ntext: hello\n")
    assert marks["['defs', 'my-def']"] == (2, 3)


def test_flow_style_has_per_element_positions():
    """Old: a flow sequence produced no entries at all for its elements, so
    every element fell back to the line of the enclosing key -- line 1 here."""
    marks = marks_of('text: [\n  "a",\n  "${ nope }"\n]\n')
    assert marks["['text', '[0]']"] == (2, 3)
    assert marks["['text', '[1]']"] == (3, 3)


def test_the_contents_of_a_block_scalar_are_not_paths():
    """Old: `text: |` holding `a: b` invented the path `['text', 'a']`.

    A `code:` block full of colons manufactured entries that shadow real ones;
    the marks map contains only what the YAML parser calls a node.
    """
    marks = marks_of("text: |\n  a: b\n  c: d\ndescription: x\n")
    assert "['text', 'a']" not in marks
    assert marks["['text']"] == (1, 1)
    assert marks["['description']"] == (4, 1)


def test_a_mapping_entry_is_marked_at_its_key():
    """The column is the key's, and the span runs to the end of the value."""
    _, marks = load_with_marks("text:\n  - model: m\n    input: hi\n")
    entry = marks["['text', '[0]', 'input']"]
    assert (entry.line, entry.col) == (3, 5)
    assert (entry.end_line, entry.end_col) == (3, 14)


def test_append_resolves_against_the_file_it_names():
    """A location cannot be resolved against another file's marks.

    This is DROP #6 in miniature: `execute_call` used to build the callee's
    path with the caller's line table, and the resulting miss became a
    confident wrong line. `append` consults exactly one source, the one named
    in the location it is given.
    """
    caller_src = "defs:\n  lib:\n    import: sub\ntext: x\n"
    callee_src = "defs:\n  f:\n    function: {}\n    return: ${ kaboom }\ntext: ''\n"
    _, caller_marks = load_with_marks(caller_src)
    _, callee_marks = load_with_marks(callee_src)
    register_source("caller.pdl", caller_src, caller_marks)
    register_source("callee.pdl", callee_src, callee_marks)
    # `['defs', 'f', 'return']` does not occur in the caller at all; resolved
    # there it would have missed and walked up to `['defs']`, line 1.
    assert "['defs', 'f', 'return']" not in caller_marks
    callee = PdlLocationType(file="callee.pdl", path=["defs", "f"], line=2, col=3)
    assert append(callee, "return").line == 4


def test_append_carries_the_parent_position_when_a_path_is_absent():
    """A path with no mark keeps the enclosing block's position.

    With real marks a miss means the path is not in the source -- a synthetic
    segment, or a block built at runtime -- rather than a shortcoming of the
    scanner, so the enclosing block is the honest answer and the only one
    available.
    """
    _, marks = load_with_marks("text:\n  - a\n")
    register_source("miss.pdl", "", marks)
    loc = PdlLocationType(file="miss.pdl", path=["text"], line=1, col=1)
    invented = append(loc, "not_a_key")
    assert (invented.line, invented.col) == (1, 1)
    assert invented.path == ["text", "not_a_key"]


def test_a_location_with_no_source_stays_unknown():
    """`empty_block_location` must never pick up another program's lines.

    `""` is *no source*, not *an unnamed source*: it is what every block of an
    `exec_dict` program carries. It is never registered, so it never resolves,
    whatever else the process has parsed.
    """
    parse_str("text:\n  - a\n  - b\n  - c\n  - d\n  - e\n  - f\n  - g\n")
    loc = append(append(empty_block_location, "text"), "[6]")
    assert loc.line == 0
    assert get_loc_string(loc) == "line 0 - "


def test_a_program_parsed_from_a_string_is_named_and_located():
    _, loc = parse_str("text:\n  - a\n  - ${ nope }\n")
    assert loc.file == UNNAMED_SOURCE
    assert get_loc_string(append(loc, "text")) == "<program>:1 - "
    assert get_loc_string(append(append(loc, "text"), "[1]")) == "<program>:3 - "


def test_the_registry_answers_with_the_source_text():
    """What a caret and an excerpt are drawn from, at render time."""
    from pdl.pdl_location_utils import source_text

    source = "text: hello\n"
    parse_str(source, file_name="excerpt.pdl")

    assert source_text("excerpt.pdl") == source
    assert source_text("never-parsed.pdl") is None


def test_program_location_of_a_source_with_no_root():
    assert program_location("empty.pdl", {}) == PdlLocationType(
        file="empty.pdl", path=[], line=0, col=0
    )
