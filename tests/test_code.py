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


CROSS_BLOCK_PROG = """
text:
- lang: python
  code: |
    def embed(text):
        return 1 / 0
    PDL_SESSION.pinned_embed = embed
    result = "ready"
- lang: python
  code: |
    key = "a string the user is looking at"
    other = key.upper()
    result = PDL_SESSION.pinned_embed(key)
"""


def test_python_raised_frames_of_another_block_are_not_rendered_as_source():
    """A function defined in one `code:` block and called from another.

    The frames of the defining block carry line numbers into *its* source, which
    this diagnostic does not hold. They must not be indexed into the failing
    block's code: doing so printed an innocent line of the wrong block under a
    caret, which is a confidently-stated wrong location -- the one thing the
    `code:N` gutter exists to prevent. They are named in prose instead.

    Pinned because the regression is silent: every line here is a real line of
    some block, so nothing looks wrong until you read the other block.
    """
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(CROSS_BLOCK_PROG)
    assert exc.value.message == (
        "code block raised ZeroDivisionError: division by zero\n"
        "\n"
        "code:3 | result = PDL_SESSION.pinned_embed(key)\n"
        "       |          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        "\n"
        "  Python code in a `code:` block must run to completion; an exception that\n"
        "  escapes it stops the program.\n"
        "\n"
        "  note: raised inside `embed`, line 2 of another `code:` block, which this\n"
        "        block called.\n"
        "  note: `code:N` line numbers are within the block's code, not the PDL file."
    )
    # The wrong-block line and the wrong-block frame label, spelled out: both
    # were printed before, and both are lines of the *defining* block.
    assert "other = key.upper()" not in exc.value.message
    assert "in embed" not in exc.value.message


def test_python_raised_frames_of_a_longer_block_are_not_rendered_as_source():
    """The same defect past the end of the failing block's source.

    When the other block's line number exceeds this block's length, indexing it
    produced a gutter row with an empty source line and a bare `^` under it.
    """
    prog_str = """
text:
- lang: python
  code: |
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    def boom():
        raise ValueError("from the long block")
    PDL_SESSION.pinned_boom = boom
    result = "ok"
- lang: python
  code: |
    result = PDL_SESSION.pinned_boom()
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert exc.value.message == (
        "code block raised ValueError: from the long block\n"
        "\n"
        "code:1 | result = PDL_SESSION.pinned_boom()\n"
        "       |          ^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        "\n"
        "  Python code in a `code:` block must run to completion; an exception that\n"
        "  escapes it stops the program.\n"
        "\n"
        "  note: raised inside `boom`, line 7 of another `code:` block, which this\n"
        "        block called.\n"
        "  note: `code:N` line numbers are within the block's code, not the PDL file."
    )


FORMAT_EXC_PROG = """
lang: python
code: |
  import traceback
  try:
      1 / 0
  except ZeroDivisionError:
      result = traceback.format_exc()
"""


def test_python_compile_filename_is_visible_in_the_block_s_own_result():
    """The `compile` filename is success-path *output*, not just stderr.

    A block that catches its own exception and formats the traceback gets the
    pseudo-filename back as a string, and that string can be its `result`. Exit 0,
    nothing on stderr, and `<code-block>` in stdout. Any scheme that tells blocks
    apart by giving each `compile` a distinct name -- `<code-block-1>`,
    `<code-block-2>`, ... -- changes this byte for byte, which is why the frames
    are told apart by code-object identity instead. Pinned so the counter cannot
    come back.
    """
    result = exec_str(FORMAT_EXC_PROG)
    assert 'File "<code-block>", line 3' in result
    assert "<code-block-" not in result


def test_python_compile_filename_is_the_same_in_every_loop_iteration():
    """Three iterations of one block, three identical strings.

    The second half of the same regression: a per-execution filename made a
    value that used to be loop-invariant vary with the iteration, so a program
    that compares or aggregates it changes behaviour. Nothing about a block's
    output may depend on how many code blocks ran before it.
    """
    prog_str = """
for:
  i: [0, 1, 2]
repeat:
  lang: python
  code: |
    import traceback
    try:
        1 / 0
    except ZeroDivisionError:
        result = traceback.format_exc()
join:
  as: array
"""
    results = exec_str(prog_str)
    assert len(results) == 3
    assert results[0] == results[1] == results[2]
    assert 'File "<code-block>", line 3' in results[0]


def test_python_raised_tab_indented_line_keeps_the_caret_under_its_token():
    """Leading whitespace is stripped and the columns rebased, as CPython does.

    A tab is one character but eight columns; padding the caret line with spaces
    against a tab-rendered source line put the caret about seven columns left of
    its token.
    """
    prog_str = (
        "lang: python\ncode: |\n  def helper():\n  \treturn 1/0\n  result = helper()\n"
    )
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    lines = exc.value.message.splitlines()
    source = lines[lines.index("code:2 | return 1/0")]
    caret = lines[lines.index("code:2 | return 1/0") + 1]
    assert "\t" not in source
    assert caret == "       |        ^^^ in helper"
    assert source.index("1/0") == caret.index("^^^")


def test_python_raised_excerpt_and_caret_are_clipped():
    """The excerpt has a wall of its own.

    `_RAISED_DETAIL_CLIP` bounds the exception's text; nothing bounded the line
    the user wrote, so a 440-character source line printed a 450-character row
    with a 449-character caret under it.
    """
    long_string = "y" * 400
    prog_str = f'lang: python\ncode: |\n  result = "{long_string}".index("zzz")\n'
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    gutter = [
        line
        for line in exc.value.message.splitlines()
        if line.startswith("code:") or line.startswith("       |")
    ]
    assert gutter, exc.value.message
    assert max(len(line) for line in gutter) <= 80
    assert gutter[0].endswith("...")


def test_python_raised_help_line_is_clipped():
    """`textwrap` will not split a long identifier, so wrapping is not a wall."""
    long_name = "q" * 400
    prog_str = f"lang: python\ncode: |\n  result = {long_name}\n"
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert max(len(line) for line in exc.value.message.splitlines()) <= 82
    assert "..." in exc.value.message


def test_python_raised_gutter_note_is_emitted_only_when_there_is_a_gutter():
    """The note explains the `code:N` gutter, so it cannot outlive one.

    It is now appended to the same `notes` list as every other note, where
    dropping the condition is a one-line slip and the result is a diagnostic
    telling the user how to read line numbers it never printed. The one branch
    that renders no gutter row is a `compile` failure with no `lineno` -- a NUL
    byte in the source -- which cannot be written in a YAML scalar, so the
    builder is called directly.
    """
    # pylint: disable=import-outside-toplevel,protected-access
    from pdl import pdl_interpreter

    code = "result = 1\0"
    with pytest.raises(SyntaxError) as raised:
        compile(code, "<code-block>", "exec")
    message = pdl_interpreter._raised_diagnostic(
        raised.value, code, {}, set(), pdl_interpreter.empty_scope, unit={}
    )
    assert message.startswith("code block has a syntax error: ")
    assert not [line for line in message.splitlines() if line.startswith("code:")]
    assert "note:" not in message


def test_python_raised_by_an_exception_that_cannot_be_printed():
    """The builder runs because the user's code already failed; it must not be
    the second failure. `str(exc)` raising used to take it down, and the generic
    handler then reported the *builder's* error at `:0`."""
    prog_str = """
lang: python
code: |
  class Nasty(Exception):
      def __str__(self):
          raise RuntimeError("no string for you")
  raise Nasty()
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert exc.value.message.startswith(
        "code block raised Nasty: <unprintable message>"
    )
    assert "no string for you" not in exc.value.message


def test_python_raised_name_error_whose_name_is_not_a_string():
    """`NameError.name` is a plain writable attribute. A non-string reached
    `difflib.get_close_matches`, which compares by slicing. The suggestion is
    dropped; the diagnostic is not."""
    prog_str = """
lang: python
code: |
  err = NameError("name 'x' is not defined")
  err.name = 42
  raise err
"""
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str(prog_str)
    assert exc.value.message.startswith(
        "code block raised NameError: name 'x' is not defined"
    )
    assert "help:" not in exc.value.message


def test_python_raised_diagnostic_builder_is_total(monkeypatch):
    """The guarantee, not the two known inputs: whatever fails while building
    the message, the user gets a thinner diagnostic about their own error rather
    than one about PDL's."""
    from pdl import pdl_interpreter  # pylint: disable=import-outside-toplevel

    def explode(*_args, **_kwargs):
        raise RuntimeError("the builder is broken")

    monkeypatch.setattr(pdl_interpreter, "_raised_body", explode)
    with pytest.raises(PDLRuntimeError) as exc:
        exec_str("lang: python\ncode: |\n  result = 1/0\n")
    assert exc.value.message == (
        "code block raised ZeroDivisionError\n"
        "\n"
        "  Python code in a `code:` block must run to completion; an exception that\n"
        "  escapes it stops the program."
    )
    assert "the builder is broken" not in exc.value.message


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
