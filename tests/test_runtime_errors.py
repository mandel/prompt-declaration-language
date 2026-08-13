import errno

import pytest

from pdl.pdl import exec_file, exec_str
from pdl.pdl_interpreter import PDLRuntimeError


def test_jinja_undefined():
    prog_str = """
${ x }
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Error during the evaluation of ${ x }: 'x' is undefined"
    )


def test_jinja_access():
    prog_str = """
${ {}['x'] }
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Error during the evaluation of ${ {}['x'] }: 'dict object' has no attribute 'x'"
    )


def test_jinja_syntax():
    prog_str = """
${ {}[ }
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Syntax error in ${ {}[ }: unexpected '}', expected ']'"
    )


def test_parser_jsonl():
    prog_str = """
text: "{ x: 1 + 1 }"
parser: jsonl
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Attempted to parse ill-formed JSON: JSONDecodeError('Expecting property name enclosed in double quotes: line 1 column 3 (char 2)')"
    )


def test_parser_regex():
    prog_str = """
text: "Hello"
parser:
  regex: "("
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Fail to parse with regex (: error('missing ), unterminated subpattern at position 0')"
    ) or (
        str(exc.value.message)
        == "Fail to parse with regex (: PatternError('missing ), unterminated subpattern at position 0')"
    )


def test_type_result():
    prog_str = """
text: "Hello"
spec: integer
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    # `<program>:3` where this used to say `line 3`; see the same change in
    # `tests/test_fallback.py`. `line N - ` is now reserved for a program with no
    # source at all, such as one built as a dict.
    #
    # The `  in spec` line is phase-3 item 7: `analyze_errors` renders each
    # complaint's block path under its own header. The header line of the
    # enclosing `PDLRuntimeError` carries no path here because the raise site
    # builds it as a bare string; only `generate` adds one, at print time.
    assert (
        str(exc.value.message) == "Type errors during spec checking:\n"
        "<program>:3 - Hello should be of type <class 'int'>\n"
        "  in spec"
    )


def test_get():
    prog_str = """
text:
- "Hello"
- get: x
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Error during the evaluation of ${ x }: 'x' is undefined"
    )


def test_call_undefined():
    prog_str = """
call: "${ f }"
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Error during the evaluation of ${ f }: 'f' is undefined"
    )


def test_call_bad_name():
    prog_str = """
call: ${ ( f }
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Syntax error in ${ ( f }: unexpected '}', expected ')'"
    )


def test_call_bad_args():
    prog_str = """
defs:
    f:
      function:
        x: integer
      return: Hello
call: ${ f }
args:
    x: ${ (x }
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert (
        str(exc.value.message)
        == "Syntax error in ${ (x }: unexpected '}', expected ')'"
    )


# --------------------------------------------------------------------------
# `import:` cannot read its file (E-RUNTIME-002).
#
# The corpus entry pins one branch -- the weakest one, in a directory holding
# nothing else. These pin the branches it cannot reach, and the two properties
# that are invisible in a golden: which exception an SDK caller now catches, and
# that the rendered text carries exactly one location header however the error
# is re-wrapped on the way up.
# --------------------------------------------------------------------------


def flat(text: str) -> str:
    """A diagnostic with its prose wrapping collapsed.

    Assertions about wording must not be assertions about where a line happened
    to break.
    """
    return " ".join(text.split())


def test_import_missing_file_names_what_was_written_and_what_was_opened(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prog.pdl").write_text("import: nosuch\n", encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    text = flat(caught.value.message)
    assert "cannot import `nosuch`: no such file `nosuch.pdl`" in text
    assert "PDL appends `.pdl` to an import path" in text
    # E-CLI-001's sentences are about the command-line argument and must not be
    # reused here: `pdl --help` cannot fix an `import:` inside a program.
    assert "pdl --help" not in text


def test_import_error_carries_one_location_header(tmp_path, monkeypatch):
    """The rendered diagnostic is not prefixed a second time by a re-wrap site."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prog.pdl").write_text(
        "defs:\n  a:\n    import: nosuch\ntext: ok\n", encoding="utf-8"
    )
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    rendered = str(caught.value)
    assert rendered.count("prog.pdl:3 - ") == 1
    assert rendered.startswith("prog.pdl:3 - cannot import `nosuch`")
    assert caught.value.source_exception.diagnostic.as_record()["id"] == "E-RUNTIME-002"


def test_import_near_miss_is_suggested_in_the_written_form(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "helper.pdl").write_text('text: ""\n', encoding="utf-8")
    (tmp_path / "prog.pdl").write_text("import: lib/helpr\n", encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    # The directory part is kept and the suffix is not added: both forms
    # resolve, so the suggestion is a minimal edit to what they wrote.
    assert "did you mean `import: lib/helper`?" in flat(caught.value.message)


def test_import_of_a_data_file_explains_the_suffix_rule(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "prog.pdl").write_text("import: notes.yaml\n", encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    text = flat(caught.value.message)
    assert "`notes.yaml` exists, but `import:` reads only files whose names" in text
    # Conditional on purpose: a rename followed blindly turns a missing-file
    # error into a schema error when the file is data rather than a program.
    assert "if `notes.yaml` is a PDL program, rename it to `notes.pdl`" in text


def test_import_lists_the_directory_it_searched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("a", "b", "c", "d"):
        (tmp_path / f"{name}.pdl").write_text('text: ""\n', encoding="utf-8")
    (tmp_path / "prog.pdl").write_text("import: zzzzzzzzzzzz\n", encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    text = flat(caught.value.message)
    # Sorted and capped, never `set` iteration, and the importing program is not
    # in the list: importing yourself is a cycle, never the fix.
    assert "contains `a.pdl`, `b.pdl`, `c.pdl`, and 1 more." in text
    assert "prog.pdl" not in text.split(" contains ")[1]


def test_nested_import_says_which_directory_it_resolved_from(tmp_path, monkeypatch):
    """`state.cwd` is the top-level program's directory (INVENTORY.md 7.4)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "prog.pdl").write_text("import: lib/a\n", encoding="utf-8")
    (tmp_path / "lib" / "a.pdl").write_text(
        'defs:\n  b:\n    import: b\ntext: ""\n', encoding="utf-8"
    )
    (tmp_path / "lib" / "b.pdl").write_text('text: ""\n', encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    text = flat(caught.value.message)
    # The header is the file holding the failing `import:`, not the entry point.
    assert text.startswith("lib/a.pdl:3 - cannot import `b`")
    assert (
        "note: import paths are resolved from the current directory, the "
        "directory of the program `pdl` was started with, not from the file "
        "that contains this `import:`." in text
    )
    # And nothing suggests importing the entry-point program, which would be a
    # cycle rather than a fix.
    assert "did you mean" not in text
    assert "name one of them" not in text


def test_failed_import_keeps_the_os_error_reachable(tmp_path, monkeypatch):
    """The SDK break is the class, not the information (docs/release-notes.md)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prog.pdl").write_text("import: nosuch\n", encoding="utf-8")
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    original = caught.value.__context__
    assert isinstance(original, FileNotFoundError)
    assert original.errno == errno.ENOENT
    assert original.filename == "nosuch.pdl"
    # `str()` and `repr()` of what the caller catches are both usable.
    assert str(caught.value) == caught.value.text
    assert "cannot import" in repr(caught.value)


def test_non_utf8_import_is_a_diagnostic_not_a_traceback(tmp_path, monkeypatch):
    """`UnicodeDecodeError` is not an `OSError`; it needs its own clause."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prog.pdl").write_text("import: bad\n", encoding="utf-8")
    (tmp_path / "bad.pdl").write_bytes(b'text: "\xff bad"\n')
    with pytest.raises(PDLRuntimeError) as caught:
        exec_file("prog.pdl")
    text = flat(caught.value.message)
    assert "bad.pdl:1:8 - not valid UTF-8" in text
    assert "byte 0xff cannot start a UTF-8 character" in text
