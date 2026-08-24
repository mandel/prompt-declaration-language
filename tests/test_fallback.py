import pytest

from pdl.pdl import exec_dict, exec_str
from pdl.pdl_interpreter import PDLRuntimeError

direct_fallback_data = {"model": "raise an error", "fallback": "The error was caught"}


def test_direct_fallback():
    text = exec_dict(direct_fallback_data)
    assert text == "The error was caught"


indirect_fallback_data = {
    "text": [{"model": "raise an error"}],
    "fallback": "The error was caught",
}


def test_indirect_fallback():
    text = exec_dict(indirect_fallback_data)
    assert text == "The error was caught"


error_in_sequence_data = {
    "text": ["Hello ", {"model": "raise an error"}, "Bye!"],
    "fallback": "The error was caught",
}


def test_error_in_sequence():
    text = exec_dict(error_in_sequence_data)
    assert text == "The error was caught"


def test_python_exception():
    prog_str = """
code: "raise Exception()"
lang: python
fallback: "Exception caught"
"""
    result = exec_str(prog_str)
    assert result == "Exception caught"


def test_parse_regex_error():
    prog_str = """
text: "Hello"
parser:
    regex: "(e"
fallback: "Exception caught"
"""
    result = exec_str(prog_str)
    assert result == "Exception caught"


def test_type_checking():
    prog_str = """
text: "Hello"
spec: integer
fallback: 4012
"""
    result = exec_str(prog_str)
    assert result == 4012


def test_type_checking_in_fallback():
    prog_str = """
model: "raise an error"
spec: integer
fallback: "Error"
"""
    with pytest.raises(PDLRuntimeError) as exc:
        _ = exec_str(prog_str)
    # `<program>:4` where this used to say `line 4`: a program parsed from a
    # string now has a named source in the registry, under the same label its
    # YAML errors already used. The line is unchanged.
    #
    # `  in fallback.spec` is phase-3 item 7: the block path is rendered under
    # the complaint's header. It says something the line alone does not -- that
    # the value being type-checked is the *fallback's* result, not the model
    # block's.
    #
    # `:1` is the column, now rendered beside the line (§7.9/§7.11). It is the
    # position of `fallback:`, the construct `fallback.spec` names: the program
    # has no `spec:` under the fallback to point at, so `append` carried the
    # parent's mark down, and the header says exactly what the path says.
    assert (
        str(exc.value.message) == "Type errors during spec checking:\n"
        "<program>:4:1 - Error should be of type <class 'int'>\n"
        "  in fallback.spec"
    )


def test_fallback_and_parser():
    prog_str = """
model: "raise an error"
parser: json
spec: { xxx: string, age: integer}
fallback:
    data: { "xxx": "rosa", "age": 3 }
"""
    result = exec_str(prog_str)
    assert result == {"xxx": "rosa", "age": 3}
