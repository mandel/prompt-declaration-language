"""The location model: YAML marks, the source registry, and `append`.

`tests/test_line_table.py` covers the same machinery from the outside, by
asserting the `file:line - message` a user sees. This file covers it from the
inside, and each case here is a shape the regex line map it replaced could not
get right. Where that is the point of the case, the old answer is in the
docstring, measured against `get_line_map` at commit `87958e4` rather than
recalled.
"""

from pdl.pdl import exec_file, exec_str
from pdl.pdl_ast import PdlLocationType, empty_block_location
from pdl.pdl_interpreter import PDLRuntimeError
from pdl.pdl_location_utils import (
    SOURCES,
    UNNAMED_SOURCE,
    append,
    get_loc_string,
    get_source,
    is_unnamed,
    load_with_marks,
    nested_source_name,
    program_location,
    register_source,
    source_text,
)
from pdl.pdl_parser import parse_str


def marks_of(source: str) -> dict[str, tuple[int, int]]:
    _, marks = load_with_marks(source)
    return {key: (mark.line, mark.col) for key, mark in marks.items()}


def test_data_is_what_safe_load_would_have_returned():
    # local: the point is the comparison, not the dependency
    import yaml  # pylint: disable=import-outside-toplevel

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
    source = "text: hello\n"
    parse_str(source, file_name="excerpt.pdl")

    assert source_text("excerpt.pdl") == source
    assert source_text("never-parsed.pdl") is None


def test_program_location_of_a_source_with_no_root():
    assert program_location("empty.pdl", {}) == PdlLocationType(
        file="empty.pdl", path=[], line=0, col=0
    )


# --- Naming a source that a running program produced -------------------------
#
# `lang: pdl` runs the text of a `code:` field as a program of its own. That text
# has no file name, so until this change it was parsed as `<program>` -- the same
# key a string program uses -- and registering it evicted the *containing*
# program from the registry. See `nested_source_name` for the scheme, and
# `docs/error-reporting/INVENTORY.md` 7.7 for what it does and does not close.


def loc_at(file: str, *path: str) -> PdlLocationType:
    return PdlLocationType(file=file, path=list(path), line=1, col=1)


def test_a_nested_program_is_named_for_the_route_to_it():
    assert (
        nested_source_name(loc_at(UNNAMED_SOURCE, "text", "[0]", "code"))
        == "<program:text[0].code>"
    )
    assert (
        nested_source_name(loc_at("hello.pdl", "text", "[0]", "code"))
        == "<program:hello.pdl#text[0].code>"
    )


def test_a_nested_name_is_qualified_by_the_file_that_contains_it():
    """Two files, one path: the path alone is not a key.

    Two `.pdl` files can each hold a `lang: pdl` block at the same block path,
    and one process can have both alive -- one importing the other. Named for
    the path alone they would share a key, which is the bug this naming exists
    to remove, moved rather than removed.
    """
    a = nested_source_name(loc_at("a.pdl", "text", "[0]", "code"))
    b = nested_source_name(loc_at("sub/b.pdl", "text", "[0]", "code"))
    assert a != b
    assert (a, b) == (
        "<program:a.pdl#text[0].code>",
        "<program:sub/b.pdl#text[0].code>",
    )


def test_two_files_with_a_nested_program_at_the_same_path(tmp_path):
    """The same case, run: two nested programs at one path, both alive at once.

    `a.pdl` includes `b.pdl` and each has a `lang: pdl` block at `text[0]`.
    Named for the path alone both would be `<program:text[0].code>`, and `b`'s
    would take `a`'s entry as it registered.
    """
    (tmp_path / "b.pdl").write_text(
        "text:\n- lang: pdl\n  code: |\n    text: from b\n", encoding="utf-8"
    )
    (tmp_path / "a.pdl").write_text(
        "text:\n- lang: pdl\n  code: |\n    text: from a\n- include: b.pdl\n",
        encoding="utf-8",
    )
    assert exec_file(str(tmp_path / "a.pdl")) == "from afrom b"

    from_a = get_source(f"<program:{tmp_path}/a.pdl#text[0].code>")
    from_b = get_source(f"<program:{tmp_path}/b.pdl#text[0].code>")
    assert from_a is not None and from_a.text == "text: from a\n"
    assert from_b is not None and from_b.text == "text: from b\n"
    assert not from_a.contested and not from_b.contested


def test_nesting_composes_instead_of_stacking_brackets():
    """A program produced by a program produced by a file: one bracket pair."""
    inner = nested_source_name(loc_at("hello.pdl", "text", "[0]", "code"))
    assert (
        nested_source_name(loc_at(inner, "defs", "f", "code"))
        == "<program:hello.pdl#text[0].code#defs.f.code>"
    )


def test_a_nested_name_is_not_a_file_the_user_could_open():
    """`is_unnamed` covers the whole `<...>` family, not just `<program>`.

    It is what decides whether a schema failure says "the file PDL X does not
    respect the schema", i.e. whether the user is sent looking for a path.
    """
    assert is_unnamed("<program:hello.pdl#text[0].code>")
    assert is_unnamed(UNNAMED_SOURCE)
    assert is_unnamed("")
    assert not is_unnamed("hello.pdl")


NESTED_PROGRAM = """\
description: outer
text:
- lang: pdl
  code: |
    text: inner ran
- "\\n"
- ${ undefined_var }
"""


def test_a_nested_program_does_not_evict_the_program_that_ran_it():
    """The regression test for the bug this naming fixes.

    Before: `<program>:2`. The `lang: pdl` block re-registered `<program>` with
    its own two-line source, and the failing expression on line 7 was resolved
    against *those* marks: `['text', '[2]']` is not in them, so the ancestor
    walk fell back to `['text']` -- line 2 of the inner source, printed as line
    2 of the outer one.
    """
    assert get_loc_string(failure_of(NESTED_PROGRAM).loc) == "<program>:7 - "


def test_the_registry_keeps_both_the_container_and_what_it_ran():
    failure_of(NESTED_PROGRAM)
    outer = get_source(UNNAMED_SOURCE)
    inner = get_source("<program:text[0].code>")
    assert outer is not None and outer.text == NESTED_PROGRAM
    assert inner is not None and inner.text == "text: inner ran\n"


def failure_of(program: str) -> PDLRuntimeError:
    """Run a program that must fail, and hand back what it failed with.

    Plain `try`/`except` rather than `pytest.raises`, because this module
    imports no test framework and there is no reason for it to start.
    """
    try:
        exec_str(program)
    except PDLRuntimeError as exc:
        return exc
    raise AssertionError("the program was expected to fail")


# --- One key, two texts: what is left, and what the registry does about it ----


def test_re_registering_the_same_text_is_a_no_op():
    source = "text:\n  - a\n"
    _, marks = load_with_marks(source)
    first = register_source("stable.pdl", source, marks)
    assert register_source("stable.pdl", source, marks) is first
    assert not first.contested
    assert source_text("stable.pdl") == source


def test_a_key_that_held_two_texts_stops_answering_with_either():
    """A `for:` loop over a `lang: pdl` block keeps one name for every turn.

    The line numbers stay right -- they are resolved when a location is built,
    against the marks in force then, and frozen on it. The *text* cannot be:
    read back later it is whichever turn ran last, and an excerpt drawn from it
    would sit under a correct line number saying nothing about being from
    another program. `RUBRIC.md` ranks a confidently wrong excerpt below no
    excerpt, so the registry stops answering.
    """
    alpha, beta = "text: alpha\n", "text: beta\n"
    register_source("looped.pdl", alpha, load_with_marks(alpha)[1])
    assert source_text("looped.pdl") == alpha

    contested = register_source("looped.pdl", beta, load_with_marks(beta)[1])
    assert contested.contested
    assert contested.text == beta  # the entry still knows its own text
    assert source_text("looped.pdl") is None  # it just will not vouch for it

    # Sticky: going back to the first text does not make the name unambiguous
    # again, because a location built during the second is still out there.
    assert register_source("looped.pdl", alpha, load_with_marks(alpha)[1]).contested


def test_a_cached_re_parse_puts_its_own_marks_back():
    """`parse_str` caches, and a cache hit must still own the registry.

    Two texts under one name, then the first one again -- a `for:` loop that
    comes back round to a value it has already run. The third call is a cache
    hit, so nothing in the parse runs; without a re-registration the registry
    would still hold the second text's marks and every location built during
    that run would resolve against them. That is a wrong line, not a wrong
    excerpt.
    """
    short = "text:\n  - a\n"
    tall = "description: d\ntext:\n  - a\n  - b\n  - c\n"
    name = "<program:cached#text[0].code>"

    _, short_loc = parse_str(short, file_name=name)
    parse_str(tall, file_name=name)
    _, again = parse_str(short, file_name=name)  # cache hit

    assert again is short_loc  # it really is the cached parse
    assert SOURCES.mark(name, ["text", "[0]"]) is not None
    assert append(append(again, "text"), "[0]").line == 2
    # `['text', '[2]']` exists only in the taller text, so against the right
    # marks it misses and keeps its parent's line 1. Against the taller text's
    # it would have resolved, confidently, to line 5.
    assert append(append(again, "text"), "[2]").line == 1
