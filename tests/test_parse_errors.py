"""The CLI/parse boundary: what `parse_file` and `parse_str` raise, and why.

The first test in this file is the load-bearing one. The whole reason the
boundary errors are per-errno subclasses rather than one shared `PDLSourceError`
is that every `except` clause an SDK caller already wrote must keep matching --
and nothing else in the tree pins that. If `PDLFileNotFoundError` stopped being a
`FileNotFoundError`, every other test here would still pass while
`except FileNotFoundError: ...` around `exec_file` silently stopped firing.

Matching the class is only half of it, and the other half broke once already:
`except OSError as e: e.errno` found `None`, and `print(exc)` rendered a Python
list. So each shim is pinned twice here -- once for the clause that catches it,
once for the object the clause receives.

One clause does *not* keep matching, deliberately: `except UnicodeDecodeError`.
That break is decided in `docs/error-reporting/INVENTORY.md` 7.1, announced in
`docs/release-notes.md`, and asserted below rather than merely described.
"""

import errno
import io
from pathlib import Path

import pytest
import yaml

from pdl.pdl import exec_file
from pdl.pdl_ast import PDLException, PDLScopeError
from pdl.pdl_parser import (
    PDLDuplicateKeyError,
    PDLFileNotFoundError,
    PDLIsADirectoryError,
    PDLParseError,
    PDLPermissionError,
    PDLUnicodeDecodeError,
    PDLYamlError,
    parse_file,
    parse_str,
)
from pdl.pdl_utils import validate_pdl_model_defaults


def flat(text: str) -> str:
    """A diagnostic with its prose wrapping collapsed.

    Assertions about wording must not be assertions about where a line happened
    to break: the wrap column moves with the length of a `tmp_path`.
    """
    return " ".join(text.split())


def test_shims_keep_every_except_clause_matching():
    """Each shim is a subclass of exactly the type it replaces, and no other.

    The negative half matters as much as the positive: a `PDLFileNotFoundError`
    must not be an `IsADirectoryError`, or the shims would be a shared class
    wearing three names.
    """
    not_found = PDLFileNotFoundError.__mro__
    is_dir = PDLIsADirectoryError.__mro__
    denied = PDLPermissionError.__mro__

    assert FileNotFoundError in not_found and IsADirectoryError not in not_found
    assert IsADirectoryError in is_dir and FileNotFoundError not in is_dir
    assert PermissionError in denied and FileNotFoundError not in denied
    for mro in (not_found, is_dir, denied):
        assert OSError in mro
        assert PDLParseError in mro and PDLException in mro

    assert yaml.YAMLError in PDLYamlError.__mro__
    assert PDLParseError in PDLYamlError.__mro__
    # Deliberately *not* a MarkedYAMLError: a caller narrow enough to catch that
    # would break, and it is a far rarer clause than `except yaml.YAMLError`.
    assert yaml.MarkedYAMLError not in PDLYamlError.__mro__

    # The one clause that stops matching, and the reason it cannot be shimmed:
    # `UnicodeDecodeError.__init__` takes exactly five arguments.
    assert UnicodeDecodeError not in PDLUnicodeDecodeError.__mro__
    assert PDLParseError in PDLUnicodeDecodeError.__mro__
    with pytest.raises(TypeError):
        # pylint: disable-next=pointless-exception-statement
        UnicodeDecodeError("a diagnostic")  # type: ignore[call-arg]


def test_missing_file_is_still_a_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        parse_file(tmp_path / "nope.pdl")
    assert isinstance(caught.value, PDLParseError)
    assert isinstance(caught.value, OSError)
    assert "no such file" in caught.value.text


def test_directory_is_still_an_is_a_directory_error(tmp_path):
    with pytest.raises(IsADirectoryError) as caught:
        parse_file(tmp_path)
    assert isinstance(caught.value, PDLParseError)
    assert "it is a directory" in caught.value.text


def test_yaml_failure_is_still_a_yaml_error(tmp_path):
    program = tmp_path / "prog.pdl"
    program.write_text('text:\n  - "hello\n  - "world"\n', encoding="utf-8")
    with pytest.raises(yaml.YAMLError) as caught:
        parse_file(program)
    assert isinstance(caught.value, PDLParseError)
    assert isinstance(caught.value.__cause__, yaml.MarkedYAMLError)


def test_exec_file_on_a_missing_file_still_raises_file_not_found(tmp_path):
    """The SDK contract, stated as the caller writes it."""
    try:
        exec_file(tmp_path / "nope.pdl")
    except FileNotFoundError as exc:
        assert isinstance(exc, PDLParseError)
        assert exc.text.startswith("cannot read ")  # pylint: disable=no-member
    else:  # pragma: no cover
        pytest.fail("exec_file did not raise FileNotFoundError")


def test_undecodable_file_raises_a_parse_error_carrying_the_decode_data(tmp_path):
    """The one deliberate SDK break, and the payload that makes it survivable.

    `UnicodeDecodeError` cannot be shimmed: its constructor requires exactly
    five arguments, so no subclass can carry a PDL message. INVENTORY.md 7.1
    chose to raise `PDLUnicodeDecodeError` regardless, which means
    `except UnicodeDecodeError` around `exec_file`/`parse_file` stops matching.

    Matching the class is one question and using the caught object is another,
    so both halves are pinned here: the clause that breaks, and the five
    attributes a caller migrating off it reaches for. `start` and `end` are file
    offsets, not the chunk-relative ones the raw exception reports.
    """
    program = tmp_path / "prog.pdl"
    program.write_bytes(b'text: "\xff\xfe bad"\n')

    with pytest.raises(PDLParseError) as caught:
        parse_file(program)
    exc = caught.value
    assert isinstance(exc, PDLUnicodeDecodeError)
    # The break, stated as the caller wrote it. Asserted rather than described:
    # a shim that quietly restored the match would make this file lie.
    assert not isinstance(exc, UnicodeDecodeError)

    assert exc.encoding == "utf-8"
    assert exc.object == b'text: "\xff\xfe bad"\n'
    assert (exc.start, exc.end) == (7, 8)
    assert exc.reason == "invalid start byte"
    assert exc.object[exc.start] == 0xFF
    # The original is still reachable, chunk-relative offsets and all.
    assert isinstance(exc.__cause__, UnicodeDecodeError)


def test_undecodable_file_stringifies_as_a_located_diagnostic(tmp_path):
    """`str()` is prose, not a list repr, and it points at the byte."""
    program = tmp_path / "prog.pdl"
    program.write_bytes(b'text: "\xff\xfe bad"\n')
    with pytest.raises(PDLUnicodeDecodeError) as caught:
        parse_file(program)
    rendered = str(caught.value)
    assert rendered == caught.value.text
    assert not rendered.startswith("[")
    assert rendered.startswith(
        f"{program}:1:8 - not valid UTF-8: byte 0xff cannot start a UTF-8 character"
    )
    assert '1 | text: "�� bad"' in rendered
    assert "  |        ^ here" in rendered
    assert "re-save the file as UTF-8." in flat(rendered)


def test_decode_position_is_a_file_offset_on_a_file_larger_than_a_read_chunk(tmp_path):
    """Line, column and offset are recomputed from the bytes, not taken on trust.

    `UnicodeDecodeError.start` is an offset into whatever the decoder was
    handed, and through a `TextIOWrapper` that is not promised to be the whole
    file: reading the *same* file line by line reports an offset thousands of
    bytes short, and the line number it implies is wrong. `parse_file` happens
    to call `read()`, which decodes in one piece today -- an implementation
    detail of `TextIOWrapper`, not a contract, and not something a location
    should rest on.

    So this pins the property that matters and not the mechanism: on a file far
    larger than any read chunk, the offset indexes the file, and the line
    number counts from its first byte.
    """
    filler = "\n".join(f"# padding line {i}" for i in range(4000)).encode("utf-8")
    program = tmp_path / "big.pdl"
    program.write_bytes(filler + b'\ntext: "\xff bad"\n')

    with pytest.raises(PDLUnicodeDecodeError) as caught:
        parse_file(program)
    exc = caught.value
    assert exc.object == program.read_bytes()
    assert exc.start == len(filler) + 8
    assert exc.object[exc.start] == 0xFF
    assert exc.text.startswith(f"{program}:4001:8 - not valid UTF-8")
    assert '4001 | text: "� bad"' in exc.text

    # The reading that would be wrong, so the assertions above cannot pass by
    # accident: the codec's own offset depends on how much it was handed.
    with pytest.raises(UnicodeDecodeError) as chunked:
        with open(program, "r", encoding="utf-8") as handle:
            for _ in handle:
                pass
    assert chunked.value.start < exc.start


def test_utf16_file_is_named_as_utf16(tmp_path):
    """The one detected encoding earns its own sentence, and no bogus excerpt.

    A UTF-16 file has an undecodable byte at offset 0 and NUL bytes throughout;
    an excerpt of it would be noise, so this branch names the file instead of
    pointing inside it.
    """
    program = tmp_path / "prog.pdl"
    program.write_bytes("text: hi\n".encode("utf-16"))
    with pytest.raises(PDLUnicodeDecodeError) as caught:
        parse_file(program)
    text = flat(caught.value.text)
    assert f"cannot read `{program}`: it is UTF-16, not UTF-8" in text
    assert "begins with a UTF-16 byte-order mark" in text
    assert "\x00" not in caught.value.text


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"text: hi\nb\xe2(\xa1\n", "byte 0x28 cannot continue the UTF-8 character"),
        (b"text: hi\nx\xe2\x82", "the file ends in the middle of a UTF-8 character"),
    ],
)
def test_other_decode_failures_are_named_in_words(tmp_path, data, expected):
    """The codec's `reason` strings are a closed set; none of them reaches the user."""
    program = tmp_path / "prog.pdl"
    program.write_bytes(data)
    with pytest.raises(PDLUnicodeDecodeError) as caught:
        parse_file(program)
    assert expected in caught.value.text
    assert "codec can't decode" not in caught.value.text


def test_marks_carry_the_real_filename(tmp_path):
    """`str(exc)` must not say `<unicode string>` to an SDK or linter caller."""
    program = tmp_path / "prog.pdl"
    program.write_text("text:\n\t- hello\n", encoding="utf-8")
    with pytest.raises(PDLYamlError) as caught:
        parse_file(program)
    cause = caught.value.__cause__
    assert isinstance(cause, yaml.MarkedYAMLError)
    assert "<unicode string>" not in str(cause)
    assert str(program) in str(cause)


def test_parse_str_without_a_file_name_says_program():
    with pytest.raises(PDLYamlError) as caught:
        parse_str("text:\n\t- hello\n")
    assert caught.value.text.startswith("<program>:2:1 - not valid YAML")


def test_excerpt_is_read_from_the_source_not_a_truncated_stream(tmp_path):
    """The string path keeps `mark.buffer`; a named `StringIO` would truncate it.

    Pinned because the tempting fix for the filename -- wrapping the source in an
    `io.StringIO` with a `name` -- silently costs the excerpt on any program
    larger than PyYAML's read chunk.
    """
    filler = "\n".join(f"# padding line {i}" for i in range(400))
    program = tmp_path / "big.pdl"
    program.write_text(f'{filler}\ntext:\n  - "hello\n  - "world"\n', encoding="utf-8")
    with pytest.raises(PDLYamlError) as caught:
        parse_file(program)
    assert '- "hello' in caught.value.text
    assert '- "world"' in caught.value.text

    # The mechanism, so the assertion above cannot pass by accident.
    stream = io.StringIO(program.read_text(encoding="utf-8"))
    stream.name = str(program)
    with pytest.raises(yaml.MarkedYAMLError) as raw:
        yaml.safe_load(stream)
    assert raw.value.problem_mark is not None
    assert len(raw.value.problem_mark.buffer or "") < len(
        program.read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------
# Branches of the two filesystem diagnostics that the corpus cannot reach.
# Each corpus entry pins one branch; these pin the rest.
# --------------------------------------------------------------------------


def test_missing_suffix_suggests_the_pdl_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.pdl").write_text("text: hi\n", encoding="utf-8")
    with pytest.raises(PDLFileNotFoundError) as caught:
        parse_file("hello")
    assert "did you mean `hello.pdl`?" in flat(caught.value.text)


def test_near_miss_name_is_suggested(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.pdl").write_text("text: hi\n", encoding="utf-8")
    with pytest.raises(PDLFileNotFoundError) as caught:
        parse_file("helo.pdl")
    assert "did you mean `hello.pdl`?" in flat(caught.value.text)


def test_missing_parent_directory_is_named(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PDLFileNotFoundError) as caught:
        parse_file("out/prog.pdl")
    assert "The directory `out/` does not exist either." in flat(caught.value.text)


def test_sibling_programs_are_listed_and_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("e", "d", "c", "b", "a"):
        (tmp_path / f"{name}.pdl").write_text("text: hi\n", encoding="utf-8")
    with pytest.raises(PDLFileNotFoundError) as caught:
        parse_file("zzzzzzzzzzzz.pdl")
    # Sorted and capped, never `set` iteration: the text must not move between
    # runs (RUBRIC, hygiene 0).
    assert "`a.pdl`, `b.pdl`, `c.pdl`, and 2 more" in flat(caught.value.text)


def test_directory_holding_one_program_suggests_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "main.pdl").write_text("text: hi\n", encoding="utf-8")
    with pytest.raises(PDLIsADirectoryError) as caught:
        parse_file("sub")
    assert "contains one PDL program." in flat(caught.value.text)
    assert "did you mean `pdl sub/main.pdl`?" in flat(caught.value.text)


def test_directory_holding_several_programs_names_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    for name in ("a", "b"):
        (tmp_path / "sub" / f"{name}.pdl").write_text("text: hi\n", encoding="utf-8")
    with pytest.raises(PDLIsADirectoryError) as caught:
        parse_file("sub")
    assert "`a.pdl`, `b.pdl`." in flat(caught.value.text)
    assert "name one of them, e.g." in flat(caught.value.text)


def test_unreadable_file_reports_permission_denied(tmp_path):
    program = tmp_path / "prog.pdl"
    program.write_text("text: hi\n", encoding="utf-8")
    program.chmod(0o000)
    try:
        parse_file(program)
    except PDLPermissionError as exc:
        assert "permission denied" in flat(exc.text)
        assert isinstance(exc, PermissionError)
    except PDLParseError:  # pragma: no cover
        pytest.fail("permission failure did not use the PermissionError shim")
    else:  # pragma: no cover - root ignores the mode bits
        pytest.skip("this user can read a mode-000 file")
    finally:
        program.chmod(0o644)


# --------------------------------------------------------------------------
# Recognizers that the corpus does not reach.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("text: 'never closed\n", "not valid YAML: a quoted string is never closed"),
        ("text: a: b\n", "not valid YAML: unexpected `:` in a value"),
        ("{x:\n", "not valid YAML: expected a value, but the input ended"),
        ("? a\nb\n", "not valid YAML: a mapping key has no `:`"),
    ],
)
def test_recognizers(source, expected):
    with pytest.raises(PDLYamlError) as caught:
        parse_str(source, file_name="prog.pdl")
    assert expected in caught.value.text


def test_unrecognized_problem_keeps_pyyaml_wording_and_still_locates_it():
    """The fallback is deliberate: unrecognized still gets file, line and caret."""
    with pytest.raises(PDLYamlError) as caught:
        parse_str("text: *nope\n", file_name="prog.pdl")
    text = caught.value.text
    assert text.startswith("prog.pdl:1:")
    assert "not valid YAML: found undefined alias" in text
    assert "1 | text: *nope" in text


def test_generic_block_end_branch_when_no_quote_is_unpaired():
    """When the one heuristic does not fire, the diagnostic degrades rather than guesses."""
    with pytest.raises(PDLYamlError) as caught:
        parse_str("text:\n  - a\n b\n", file_name="prog.pdl")
    text = caught.value.text
    assert "opens a string that is never closed" not in text
    assert "check the indentation and the quoting of" in flat(text)


# --------------------------------------------------------------------------
# Scope validation.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "defaults,reason",
    [
        (3, "not-a-list"),
        ([3], "entry-not-a-mapping"),
        ([{"*": 3}], "value-not-a-table"),
    ],
)
def test_malformed_model_defaults_raise_a_located_scope_error(defaults, reason):
    with pytest.raises(PDLScopeError) as caught:
        validate_pdl_model_defaults(defaults)
    assert caught.value.reason == reason
    assert caught.value.path[0] == "pdl_model_default_parameters"


def test_scope_error_is_still_a_value_error():
    """`validate_scope` is re-exported from `pdl.pdl`, so it is informally public."""
    with pytest.raises(ValueError):
        validate_pdl_model_defaults([{"*": 3}])  # type: ignore[list-item]


def test_well_formed_model_defaults_are_accepted():
    validate_pdl_model_defaults([{"*": {"temperature": 0.7}}, {"gpt-*": {}}])


# --------------------------------------------------------------------------
# `.text`, the property three sites needed.
# --------------------------------------------------------------------------


def test_text_renders_both_message_shapes():
    assert PDLParseError(["a", "b"]).text == "a\nb"
    assert PDLParseError("plain").text == "plain"


def test_shims_preserve_the_oserror_payload(tmp_path: Path):
    """`except OSError as e` must still find errno, strerror and filename.

    Matching the class is not the whole contract. `OSError.__init__` never runs
    with the original arguments when the shim is constructed, so without an
    explicit copy these read `None` and a caller branching on `e.errno` changes
    behaviour silently. `__mro__` checks do not catch that.
    """
    missing = tmp_path / "nope.pdl"
    with pytest.raises(OSError) as caught:
        exec_file(str(missing))
    exc = caught.value
    assert exc.errno == errno.ENOENT
    assert exc.strerror == "No such file or directory"
    assert exc.filename == str(missing)


def test_shimmed_exceptions_stringify_as_prose(tmp_path: Path):
    """`print(exc)` and `logging.exception` must not emit a list repr.

    `PDLParseError.message` is a `list[str]`, so the inherited `__str__` renders
    a bracketed, escaped list. That is the defect `.text` fixes at the CLI
    sites; the library path reaches it through `str()` instead.
    """
    bad_yaml = tmp_path / "bad.pdl"
    bad_yaml.write_text('text:\n  - "hello\n  - "world"\n', encoding="utf-8")
    for path in (tmp_path / "nope.pdl", bad_yaml):
        with pytest.raises(PDLParseError) as caught:
            exec_file(str(path))
        rendered = str(caught.value)
        assert not rendered.startswith("["), rendered
        assert "\\n" not in rendered, rendered
        assert rendered == caught.value.text


# --------------------------------------------------------------------------
# Duplicate mapping keys (E-PARSE-003, decision 5.5)
#
# The one parse failure PyYAML does not object to. Everything below is about the
# *type* rather than the wording, which the corpus pins: what an SDK caller
# catches, and what it must not be told.
# --------------------------------------------------------------------------


def test_a_duplicate_key_is_not_a_yaml_error():
    """PyYAML parses the document, so nothing may claim it is malformed YAML.

    The negative half is the point. `except yaml.YAMLError` around `parse_file`
    means "this document is not YAML", and a user who checks the same file with
    another YAML tool will be told that it is. The rule is PDL's, so the class
    is PDL's alone.
    """
    assert issubclass(PDLDuplicateKeyError, PDLParseError)
    assert not issubclass(PDLDuplicateKeyError, yaml.YAMLError)
    # And the document really does load, without the rule.
    assert yaml.safe_load("text: hello\ntext: world\n") == {"text": "world"}


def test_a_duplicate_key_raises_and_names_both_lines():
    with pytest.raises(PDLDuplicateKeyError) as caught:
        parse_str("text: hello\ntext: world\n", file_name="prog.pdl")
    rendered = flat(caught.value.text)
    assert rendered.startswith("prog.pdl:2:1 - the key `text` is written twice")
    assert "1 | text: hello | ^ first written here" in rendered
    assert "2 | text: world | ^ written again here" in rendered
    assert "not valid YAML" not in rendered


def test_a_duplicate_key_is_caught_by_the_clause_every_caller_wrote(tmp_path: Path):
    """`except PDLParseError` is what the CLI, `pdl-lint`, `include:` and
    `import:` all use, so the new failure reaches the user formatted."""
    program = tmp_path / "dup.pdl"
    program.write_text("text: hello\ntext: world\n", encoding="utf-8")
    with pytest.raises(PDLParseError) as caught:
        exec_file(str(program))
    assert str(caught.value) == caught.value.text
    assert not str(caught.value).startswith("[")


def test_a_duplicate_key_diagnostic_carries_its_structured_record():
    with pytest.raises(PDLDuplicateKeyError) as caught:
        parse_str("text:\n  - model: m\n    input: a\n    input: b\n", "prog.pdl")
    record = caught.value.diagnostic.as_record()
    assert record["id"] == "E-PARSE-003"
    assert record["block_path"] == ["text", "[0]"]
    assert record["span"] == {
        "line": 4,
        "col": 5,
        "end_line": None,
        "end_col": None,
        "label": "written again here",
        "primary": True,
    }
    assert [s["line"] for s in record["spans"]] == [3, 4]


def test_a_data_file_may_still_repeat_a_key(tmp_path: Path):
    """Deliberately out of scope: `-f` and `-d` carry data, not program text.

    Extending the rule there is a separate decision with a blast radius of its
    own. Pinned so the asymmetry is a choice on the record rather than an
    oversight, and so that widening it later has to be done on purpose.
    """
    data_file = tmp_path / "scope.yaml"
    data_file.write_text("a: 1\na: 2\n", encoding="utf-8")
    assert yaml.safe_load(data_file.read_text(encoding="utf-8")) == {"a": 2}
