"""The CLI/parse boundary: what `parse_file` and `parse_str` raise, and why.

The first test in this file is the load-bearing one. The whole reason the
boundary errors are per-errno subclasses rather than one shared `PDLSourceError`
is that every `except` clause an SDK caller already wrote must keep matching --
and nothing else in the tree pins that. If `PDLFileNotFoundError` stopped being a
`FileNotFoundError`, every other test here would still pass while
`except FileNotFoundError: ...` around `exec_file` silently stopped firing.
"""

import errno
import io
from pathlib import Path

import pytest
import yaml

from pdl.pdl import exec_file
from pdl.pdl_ast import PDLException, PDLScopeError
from pdl.pdl_parser import (
    PDLFileNotFoundError,
    PDLIsADirectoryError,
    PDLParseError,
    PDLPermissionError,
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


def test_undecodable_file_still_raises_unicode_decode_error(tmp_path):
    """`UnicodeDecodeError` is held, deliberately.

    Its constructor requires exactly five arguments, so it cannot be given a PDL
    message and stay catchable as itself. Until that trade-off is decided by a
    human, the decode error propagates exactly as it always has -- which is also
    what keeps corpus entry E-PARSE-005 honest about still leaking a traceback.
    """
    program = tmp_path / "prog.pdl"
    program.write_bytes(b'text: "\xff\xfe bad"\n')
    with pytest.raises(UnicodeDecodeError) as caught:
        parse_file(program)
    assert not isinstance(caught.value, PDLParseError)


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
