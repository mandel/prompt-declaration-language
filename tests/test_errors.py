import json
from pathlib import Path

from pydantic import ValidationError

import pdl.pdl
from pdl.pdl_ast import Program, empty_block_location
from pdl.pdl_interpreter import InterpreterState, empty_scope, process_prog
from pdl.pdl_schema_error_analyzer import analyze_errors


def error(raw_data, assertion):
    state = InterpreterState()
    try:
        data = Program.model_validate(raw_data)
        _, _, _, _ = process_prog(state, empty_scope, data)
    except ValidationError:
        pdl_schema_file = Path(pdl.pdl.__file__).parent / "pdl-schema.json"
        with open(pdl_schema_file, "r", encoding="utf-8") as schemafile:
            schema = json.load(schemafile)
            defs = schema["$defs"]
            errors = analyze_errors(
                defs, schema["$defs"]["PdlBlock"], raw_data, empty_block_location
            )
            assert set(errors) == set(assertion)


# `line 0 - `, not `<file>:N - `: `empty_block_location` is a location with no
# source at all, which is what a program handed to the interpreter as a dict
# has. Nothing can be resolved against it, so the line stays 0 for every
# complaint.
#
# The `  in <path>` second line is phase-3 item 7 (DROP #10). It is worth having
# here precisely *because* there is no file and no line: the path is then the
# only thing that says which block the complaint is about, and it is derived
# from the data structure rather than from any source text. A complaint about
# the program itself has path `[]` and gets no such line -- which is why the two
# "missing required field" entries below have none.


error1 = {
    "description": "Hello world!",
    "texts": ["Hello, world!\n", "This is your first prompt descriptor!\n"],
}


# Decision 5.3. This used to assert three messages, two of them false: the
# analyzer scored the union by counting shared field names, `description` is
# shared by every branch, `FunctionBlock` won the tie, and a program containing
# no function was told it was missing `function:` and `return:`. One message
# now, from the discriminator pydantic already uses.
#
# It is also the one place the degraded rendering is pinned. `error()` validates
# a dict, so there is no source text and no marks: `empty_block_location` keeps
# the `line 0 - ` header, no excerpt is drawn, and the rule and the help carry
# the diagnostic on their own.
def test_error1():
    error(
        error1,
        [
            "line 0 - this is not a PDL block: nothing here says what it does\n"
            "\n"
            "  Every block is named by the one field that says what it does. "
            "This mapping\n"
            "  has none of them: `model`, `code`, `text`, `data`, `call`, `if`, "
            "`repeat`,\n"
            "  `read`, `get`, `function`, `include`, `import`, `array`, "
            "`object`, `lastOf`,\n"
            "  `sequence`, `match`, `map`, `content`, `args`, `factor`, "
            "`aggregator`,\n"
            "  `platform` or `processor`.\n"
            "\n"
            "  help: did you mean `text:` instead of `texts:`?",
        ],
    )


error2 = {
    "description": "Hello world with a variable to call into a model",
    "text": [
        "Hello,",
        {
            "model": "watsonx/ibm/granite-20b-code-instruct",
            "parameterss": {
                "decoding_method": "greedy",
                "stop_sequences": ["!"],
                "include_stop_sequence": False,
            },
        },
        "!\n",
    ],
}


def test_error2():
    error(
        error2,
        [
            # The `help:` line is the schema near miss. It reaches this test
            # unchanged by the absence of a source file: the candidates are the
            # properties of the block being checked, which come from the schema
            # rather than from anything `empty_block_location` could resolve.
            "line 0 - Field not allowed: parameterss\n  in text[1].parameterss"
            "\n\n  help: did you mean `parameters:` instead of `parameterss:`?",
        ],
    )


# error3 = {
#     "description": "Hello world with a variable to call into a model",
#     "text": [
#         "Hello,",
#         {
#             "model": "watsonx/ibm/granite-20b-code-instruct",
#             "parameters": {
#                 "decoding_methods": "greedy",
#                 "stop_sequences": ["!"],
#                 "include_stop_sequence": False,
#             },
#         },
#         "!\n",
#     ],
# }


# def test_error3():
#     error(
#         error3,
#         [
#             ":0 - Field not allowed: decoding_methods",
#         ],
#     )


# error4 = {
#     "description": "Hello world with a variable to call into a model",
#     "text": [
#         "Hello,",
#         {
#             "model": "watsonx/ibm/granite-20b-code-instruct",
#             "parameters": {
#                 "decoding_methods": "greedy",
#                 "stop_sequencess": ["!"],
#                 "include_stop_sequence": False,
#             },
#         },
#         "!\n",
#     ],
# }


# def test_error4():
#     error(
#         error4,
#         [
#             ":0 - Field not allowed: decoding_methods",
#             ":0 - Field not allowed: stop_sequencess",
#         ],
#     )


error5 = {
    "description": "Hello world showing call out to python code",
    "text": [
        "Hello, ",
        {
            "lans": "python",
            "code": {
                "text": ["import random\n", "import string\n", "result = 'Tracy'"]
            },
        },
        "!\n",
    ],
}


def test_error5():
    error(
        error5,
        [
            "line 0 - Field not allowed: lans\n  in text[1].lans"
            "\n\n  help: did you mean `lang:` instead of `lans:`?",
        ],
    )


error6 = {
    "description": "Hello world showing call out to python code",
    "text": [
        "Hello, ",
        {
            "lans": "python",
            "codes": {
                "text": ["import random\n", "import string\n", "result = 'Tracy'"]
            },
        },
        "!\n",
    ],
}
