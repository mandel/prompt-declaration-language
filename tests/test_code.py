import sys

import pytest

from pdl.pdl import exec_dict, exec_str
from pdl.pdl_context import SerializeMode
from pdl.pdl_interpreter import PDLRuntimeError

python_data = {
    "description": "Hello world showing call out to python code",
    "text": [
        "Hello, ",
        {
            "lang": "python",
            "code": {
                "text": ["import random\n", "import string\n", "result = 'Tracy'"]
            },
        },
        "!\n",
    ],
}


def test_python():
    text = exec_dict(python_data)
    assert text == "Hello, Tracy!\n"


def test_python_result_inherited_from_scope():
    """A `result` already in scope is the block's value even if the code never
    assigns one. The code block's namespace is seeded from the PDL scope, so
    this has always worked and the missing-`result` diagnostic must not break
    it."""
    prog_str = """
defs:
  result:
    data: 42
lastOf:
- lang: python
  code: |
    print('side effect')
"""
    assert exec_str(prog_str) == 42


def test_python_missing_result_printed_value():
    prog_str = """
lang: python
code: |
  print('hi')
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert exc.value.message == (
        "code block finished without assigning `result`\n"
        "\n"
        "  A `code:` block's value is whatever its code assigns to the variable\n"
        "  `result`. This block assigned nothing.\n"
        "\n"
        "  note: `print(...)` writes to stdout; it does not set the block's value.\n"
        "  help: assign the value instead of printing it:  result = 'hi'"
    )


def test_python_missing_result_one_assigned_name():
    prog_str = """
lang: python
code: |
  total = 1 + 2
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert "This block assigned `total`, but not `result`." in exc.value.message
    assert exc.value.message.endswith("help: assign it to `result`:  result = total")


def test_python_missing_result_near_miss():
    prog_str = """
lang: python
code: |
  resutl = 1
  other = 2
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert "This block assigned `resutl`, but not `result`." in exc.value.message
    assert exc.value.message.endswith("help: did you mean to name it `result`?")


def test_python_missing_result_several_names_in_binding_order():
    """The name list comes from ordered `dict` iteration, so it does not vary
    with `PYTHONHASHSEED`, and the suggestion picks the last name bound."""
    prog_str = """
lang: python
code: |
  a = 1
  b = 2
  c = 3
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert "This block assigned `a`, `b`, `c`, but not `result`." in exc.value.message
    assert exc.value.message.endswith(
        "help: assign one of them to `result`:  result = c"
    )


def test_python_failing_block_does_not_grow_sys_path():
    """`call_python` pushes the program's directory onto `sys.path`; the pop
    used to be skipped whenever the block failed."""
    before = list(sys.path)
    with pytest.raises(PDLRuntimeError):
        exec_str("lang: python\ncode: |\n  raise ValueError('boom')\n")
    assert sys.path == before
    with pytest.raises(PDLRuntimeError):
        exec_str("lang: python\ncode: |\n  pass\n")
    assert sys.path == before


def test_python_missing_result_no_print():
    prog_str = """
lang: python
code: |
  import os
  os.getcwd()
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert "note:" not in exc.value.message
    assert exc.value.message.endswith(
        "help: a code block must end by assigning its value, e.g. `result = ...`"
    )


def show_result_data(show):
    return {
        "description": "Using a weather API and LLM to make a small weather app",
        "text": [
            {
                "def": "QUERY",
                "text": {"lang": "python", "code": "result = 'How can I help you?: '"},
                "contribute": show,
            }
        ],
    }


def test_contribute_result():
    text = exec_dict(show_result_data(["result"]))
    assert text == "How can I help you?: "


def test_contribute_context():
    result = exec_dict(show_result_data(["context"]), output="all")
    assert result["scope"]["pdl_context"].serialize(SerializeMode.LITELLM) == [
        {
            "role": "user",
            "content": "How can I help you?: ",
            "pdl__defsite": "text.0.text.code",
        }
    ]


def test_contribute_false():
    text = exec_dict(show_result_data([]))
    assert text == ""


command_data = {
    "lastOf": [
        {"def": "world", "lang": "command", "code": "echo -n World", "contribute": []},
        "Hello ${ world }!",
    ]
}

command_data_args = {
    "lastOf": [
        {
            "def": "world1",
            "lang": "command",
            "code": "echo -n \\'World\\'",  # test nested quotes
        },
        {
            "def": "world",
            "args": [
                "echo",
                "-n",
                "${ world1 }",  # and jinja expansion of nested quotes
            ],
            "contribute": [],
        },
        "Hello ${ world }!",
    ]
}


def test_command():
    result = exec_dict(command_data, output="all")
    document = result["result"]
    scope = result["scope"]
    assert document == "Hello World!"
    assert scope["world"] == "World"


def test_command_args():
    result = exec_dict(command_data_args, output="all")
    document = result["result"]
    scope = result["scope"]
    assert document == "Hello 'World'!"
    assert scope["world1"] == "'World'"
    assert scope["world"] == "'World'"


def test_jinja1():
    prog_str = """
defs:
  world: "World"
lang: jinja
code: |
  Hello {{ world }}!
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_jinja2():
    prog_str = """
defs:
  world: "World"
lang: jinja
code: |
  Hello ${ world }!
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_jinja3():
    prog_str = """
defs:
  scores:
    array:
    - 10
    - 90
    - 50
    - 60
    - 100
lang: jinja
code: |
    {% for score in scores %}
        {% if score > 80 %}good{% else %}bad{% endif %}{% endfor %}
"""
    result = exec_str(prog_str)
    assert (
        result
        == """
    bad
    good
    bad
    bad
    good"""
    )


def test_jinja4():
    prog_str = """
defs:
  name: World
lang: jinja
code: |
    Hello ${ "${" } name ${ "}" }!
parameters:
  variable_start_string:  ${ "${" }
  variable_end_string: ${ "}" }
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_pdl1():
    prog_str = """
lang: pdl
code: |
  description: Hello world
  text:
  - "Hello World!"
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_pdl2():
    prog_str = """
defs:
  w: World
lang: pdl
code: |
  description: Hello world
  text:
  - "Hello ${w}!"
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_pdl3():
    prog_str = """
defs:
  x:
    code: "result = print"
    lang: python
lang: pdl
code: |
  data: ${x}
"""
    result = exec_str(prog_str)
    assert result == "<built-in function print>"


def test_pdl4():
    prog_str = """
defs:
  x:
    code: "result = print"
    lang: python
lang: pdl
code: |
  data: ${ "${" }x ${ "}" }
"""
    result = exec_str(prog_str)
    assert result == print  # pylint: disable=comparison-with-callable


def test_lang_casing():
    prog_str = """
lang: Python
code: result = "Hello World!"
"""
    result = exec_str(prog_str)
    assert result == "Hello World!"


def test_scope1():
    prog_str = """
lang: python
scope:
  x: 10
  y: 20
code: |
  result = x + y
"""
    result = exec_str(prog_str)
    assert result == 30


def test_scope2():
    prog_str = """
lang: jinja
scope:
  name: "Alice"
  age: 30
code: |
  Hello, my name is {{ name }} and I am {{ age }} years old.
"""
    result = exec_str(prog_str)
    assert result == "Hello, my name is Alice and I am 30 years old."


def test_scope3():
    prog_str = """
lang: pdl
scope:
  greeting: "Bonjour"
code: |
  text: ${ "${" } greeting ${ "}" }, World!
"""
    result = exec_str(prog_str)
    assert result == "Bonjour, World!"
