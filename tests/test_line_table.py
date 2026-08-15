from pytest import CaptureFixture

from pdl.pdl_interpreter import empty_scope, generate


def do_test(t, capsys):
    generate(t["file"], None, empty_scope, None)
    captured = capsys.readouterr()
    output_string = captured.out + "\n" + captured.err
    output = output_string.split("\n")
    print(output)
    assert set(output) == set(t["errors"])


# The `  in <path>` lines are phase-3 item 7 (DROP #10): every diagnostic built
# from a location now renders `loc.path` under its header, in the `  in
# text[0].code` form `pdl_diagnostics.render` has always used. A diagnostic
# whose path is empty -- the whole program, at path `[]` -- gets no such line,
# which is why the two "missing required field" errors just below have none.
#
# `do_test` compares *sets* of lines, so a path line that repeats one already
# expected (two complaints against the same location) appears once here.
#
# The two "missing required field" lines this file used to assert here are
# gone, and their disappearance is decision 5.3 (E-SCHEMA-007). `analyze_errors`
# used to answer a block union by counting shared field names; `{description,
# texts}` shares `description` with every branch, the first one won, and the
# reader was told a program with no function in it was missing `function:` and
# `return:`. The analyzer now asks the same discriminator pydantic uses, which
# answers `empty` -- and `texts` is a field no block has, so there is one
# complaint about one thing.
line = {
    "file": "tests/data/line/hello.pdl",
    "errors": [
        "",
        "tests/data/line/hello.pdl:2 - this is not a PDL block: nothing here "
        "says what it does",
        "",
        "2 | texts:",
        "  | ^ `texts` does not name a block kind",
        "",
        "  Every block is named by the one field that says what it does. This mapping",
        "  has none of them: `model`, `code`, `text`, `data`, `call`, `if`, `repeat`,",
        "  `read`, `get`, `function`, `include`, `import`, `array`, `object`, `lastOf`,",
        "  `sequence`, `match`, `map`, `content`, `args`, `factor`, `aggregator`,",
        "  `platform` or `processor`.",
        "  help: did you mean `text:` instead of `texts:`?",
    ],
}


def test_line(capsys: CaptureFixture[str]):
    do_test(line, capsys)


line1 = {
    "file": "tests/data/line/hello1.pdl",
    "errors": [
        "",
        "tests/data/line/hello1.pdl:7 - Field not allowed: num_iterations",
        "  in text[2].num_iterations",
    ],
}


def test_line1(capsys: CaptureFixture[str]):
    do_test(line1, capsys)


line3 = {
    "file": "tests/data/line/hello3.pdl",
    "errors": [
        "",
        "tests/data/line/hello3.pdl:7 - Type errors during spec checking:",
        "  in text[1].spec",
        "tests/data/line/hello3.pdl:7 -  World! should be of type <class 'int'>",
    ],
}


def test_line3(capsys: CaptureFixture[str]):
    do_test(line3, capsys)


# Two unrecognised keys, so two carets and a `note:` naming both. The elision
# `...` between them is `render`'s, for lines that are not adjacent.
line4 = {
    "file": "tests/data/line/hello4.pdl",
    "errors": [
        "",
        "tests/data/line/hello4.pdl:5 - this is not a PDL block: nothing here "
        "says what it does",
        "  in text[2]",
        "",
        "5 |     - repeats:",
        "  |       ^ `repeats` does not name a block kind",
        "...",
        "7 |       maxIterations: 3",
        "  |       ^ `maxIterations` does not name a block kind",
        "  Every block is named by the one field that says what it does. This mapping",
        "  has none of them: `model`, `code`, `text`, `data`, `call`, `if`, `repeat`,",
        "  `read`, `get`, `function`, `include`, `import`, `array`, `object`, `lastOf`,",
        "  `sequence`, `match`, `map`, `content`, `args`, `factor`, `aggregator`,",
        "  `platform` or `processor`.",
        "  note: `repeats` and `maxIterations` are not fields any block accepts.",
        "  help: did you mean `repeat:` instead of `repeats:`?",
    ],
}


def test_line4(capsys: CaptureFixture[str]):
    do_test(line4, capsys)


line7 = {
    "file": "tests/data/line/hello7.pdl",
    "errors": [
        "",
        "tests/data/line/hello7.pdl:4 - Field not allowed: lans",
        "  in text[1].lans",
    ],
}


def test_line7(capsys: CaptureFixture[str]):
    do_test(line7, capsys)


# `lang:` on its own does not make a code block -- `code:` does -- so both keys
# of this item are unrecognised and the item names no kind at all.
line8 = {
    "file": "tests/data/line/hello8.pdl",
    "errors": [
        "",
        "tests/data/line/hello8.pdl:4 - this is not a PDL block: nothing here "
        "says what it does",
        "  in text[1]",
        "",
        "4 | - lang: python",
        "  |   ^ `lang` does not name a block kind",
        "5 |   codea: |",
        "  |   ^ `codea` does not name a block kind",
        "  Every block is named by the one field that says what it does. This mapping",
        "  has none of them: `model`, `code`, `text`, `data`, `call`, `if`, `repeat`,",
        "  `read`, `get`, `function`, `include`, `import`, `array`, `object`, `lastOf`,",
        "  `sequence`, `match`, `map`, `content`, `args`, `factor`, `aggregator`,",
        "  `platform` or `processor`.",
        "  note: `lang` and `codea` are not fields any block accepts.",
        "  help: did you mean `code:` instead of `codea:`?",
    ],
}


def test_line8(capsys: CaptureFixture[str]):
    do_test(line8, capsys)


line9 = {
    "file": "tests/data/line/hello9.pdl",
    "errors": [
        "",
        "tests/data/line/hello9.pdl:4 - Type errors during spec checking:",
        "  in text[0].spec",
        "tests/data/line/hello9.pdl:4 - hello should be of type <class 'int'>",
    ],
}


def test_line9(capsys: CaptureFixture[str]):
    do_test(line9, capsys)


# E-SCHEMA-009. `QUESTION should be an object` said the JSON-Schema noun and
# nothing else; `mapping` is the word PDL's own prose and documentation use, and
# the rule below it says what a `defs:` mapping is made of. No `help:`: the
# value here is a bare string, so there is no list of entries to fold into a
# mapping and any concrete edit would have to invent the definition.
line10 = {
    "file": "tests/data/line/hello10.pdl",
    "errors": [
        "",
        "tests/data/line/hello10.pdl:7 - `defs:` should be a mapping, but "
        "`QUESTION` is a string",
        "  in text[1].defs",
        "7 |   defs: QUESTION",
        "  |   ^",
        "  `defs:` is a mapping of `key: value` entries, and its keys are names you",
        "  choose.",
    ],
}


def test_line10(capsys: CaptureFixture[str]):
    do_test(line10, capsys)


line11 = {
    "file": "tests/data/line/hello11.pdl",
    "errors": [
        "",
        "tests/data/line/hello11.pdl:7 - Field not allowed: defss",
        "  in text[1].defss",
    ],
}


def test_line11(capsys: CaptureFixture[str]):
    do_test(line11, capsys)


line12 = {
    "file": "tests/data/line/hello12.pdl",
    "errors": [
        "",
        "tests/data/line/hello12.pdl:11 - Type errors during spec checking:",
        "  in text[2].spec",
        "tests/data/line/hello12.pdl:11 - How are you? should be of type <class 'bool'>",
    ],
}


def test_line12(capsys: CaptureFixture[str]):
    do_test(line12, capsys)


line13 = {
    "file": "tests/data/line/hello13.pdl",
    "errors": [
        "",
        "tests/data/line/hello13.pdl:12 - Type errors during spec checking:",
        "  in text[2].repeat.text[0].spec",
        "tests/data/line/hello13.pdl:12 - 1 should be of type <class 'str'>",
    ],
}


def test_line13(capsys: CaptureFixture[str]):
    do_test(line13, capsys)


line14 = {
    "file": "tests/data/line/hello14.pdl",
    "errors": [
        "",
        "tests/data/line/hello14.pdl:16 - Type errors in result of the function translate:",
        "  in text[2].return",
        "tests/data/line/hello14.pdl:16 - Bonjour le monde! should be of type <class 'int'>",
    ],
}


def test_line14(capsys: CaptureFixture[str]):
    do_test(line14, capsys)


line15 = {
    "file": "tests/data/line/hello15.pdl",
    "errors": [
        "",
        "tests/data/line/hello15.pdl:7 - Error during the evaluation of ${ boolean }: 'boolean' is undefined",
        "  in text[0].return.lastOf[0].get",
    ],
}


def test_line15(capsys: CaptureFixture[str]):
    do_test(line15, capsys)


line16 = {
    "file": "tests/data/line/hello16.pdl",
    "errors": [
        "",
        "tests/data/line/hello16.pdl:10 - Type errors during spec checking:",
        "  in text[1].spec",
        "tests/data/line/hello16.pdl:10 - 30 should be of type <class 'str'>",
        "  in text[1].spec.carol",
    ],
}


def test_line16(capsys: CaptureFixture[str]):
    do_test(line16, capsys)


line17 = {
    "file": "tests/data/line/hello17.pdl",
    "errors": [
        "",
        "tests/data/line/hello17.pdl:4 - Type errors during spec checking:",
        "  in text[0].spec",
        "tests/data/line/hello17.pdl:4 - hello should be of type <class 'int'>",
    ],
}


def test_line17(capsys: CaptureFixture[str]):
    do_test(line17, capsys)


line18 = {
    "file": "tests/data/line/hello18.pdl",
    "errors": [
        "",
        "tests/data/line/hello18.pdl:13 - Error during the evaluation of ${ J == 5 }: 'J' is undefined",
        "  in text[2].until",
    ],
}


def test_line18(capsys: CaptureFixture[str]):
    do_test(line18, capsys)


line19 = {
    "file": "tests/data/line/hello19.pdl",
    "errors": [
        "",
        "tests/data/line/hello19.pdl:6 - Error during the evaluation of ${ models }: 'models' is undefined",
        "  in text[1].model",
        # "tests/data/line/hello19.pdl:6 - Type errors during spec checking:",
        # "tests/data/line/hello19.pdl:6 -  should be of type <class 'int'>",
    ],
}


def test_line19(capsys: CaptureFixture[str]):
    do_test(line19, capsys)


line20 = {
    "file": "tests/data/line/hello20.pdl",
    "errors": [
        "",
        "tests/data/line/hello20.pdl:3 - Error during the evaluation of Who is${ NAME }?: 'NAME' is undefined",
        "  in text[0]",
    ],
}


def test_line20(capsys: CaptureFixture[str]):
    do_test(line20, capsys)


line21 = {
    "file": "tests/data/line/hello21.pdl",
    "errors": [
        "",
        "tests/data/line/hello21.pdl:3 - Error during the evaluation of ${ QUESTION }: 'QUESTION' is undefined",
        "  in text[0].if",
    ],
}


def test_line21(capsys: CaptureFixture[str]):
    do_test(line21, capsys)


line22 = {
    "file": "tests/data/line/hello22.pdl",
    "errors": [
        "",
        "tests/data/line/hello22.pdl:4 - Error during the evaluation of ${ I }: 'I' is undefined",
        "  in text[0].then",
    ],
}


def test_line22(capsys: CaptureFixture[str]):
    do_test(line22, capsys)


line23 = {
    "file": "tests/data/line/hello23.pdl",
    "errors": [
        "",
        "tests/data/line/hello23.pdl:5 - Error during the evaluation of ${ I }: 'I' is undefined",
        "  in text[0].else",
    ],
}


def test_line23(capsys: CaptureFixture[str]):
    do_test(line23, capsys)


line24 = {
    "file": "tests/data/line/hello24.pdl",
    "errors": [
        "",
        "tests/data/line/hello24.pdl:25 - Error during the evaluation of Hello,${ GEN1 }: 'GEN1' is undefined",
        "  in text[3].args.sentence",
    ],
}


def test_line24(capsys: CaptureFixture[str]):
    do_test(line24, capsys)


line25 = {
    "file": "tests/data/line/hello25.pdl",
    "errors": [
        "",
        "Hello, World!",
        "tests/data/line/hello25.pdl:15 - 'sentence1' is undefined",
        "${ translateText(sentence2) }",
    ],
}

# Leaving this out for now, since we can't mock the model result
# def test_line25(capsys):
#    do_test(line25, capsys)


line26 = {
    "file": "tests/data/line/hello26.pdl",
    "errors": [
        "",
        "tests/data/line/hello26.pdl:12 - Lists inside the For block must be of the same length.",
        "  in text[1].input.text[0].for",
    ],
}


def test_line26(capsys: CaptureFixture[str]):
    do_test(line26, capsys)


line27 = {
    "file": "tests/data/line/hello27.pdl",
    "errors": [
        "",
        "tests/data/line/hello27.pdl:12 - Lists inside the For block must be of the same length.",
        "  in text[1].input.text[0].for",
    ],
}


def test_line27(capsys: CaptureFixture[str]):
    do_test(line27, capsys)


line28 = {
    "file": "tests/data/line/hello28.pdl",
    "errors": [
        "",
        "tests/data/line/hello28.pdl:9 - Error during the evaluation of ${ QUESTION1 }: 'QUESTION1' is undefined",
        "  in text[2]",
    ],
}


def test_line28(capsys: CaptureFixture[str]):
    do_test(line28, capsys)


line29 = {
    "file": "tests/data/line/hello29.pdl",
    "errors": [
        "",
        "tests/data/line/hello29.pdl:10 - Error during the evaluation of ${ QUESTION1 }: 'QUESTION1' is undefined",
        "  in text[2].data.x",
    ],
}


def test_line29(capsys: CaptureFixture[str]):
    do_test(line29, capsys)


line30 = {
    "file": "tests/data/line/hello30.pdl",
    "errors": [
        "",
        "tests/data/line/hello30.pdl:6 - Values inside the For block must be lists but got <class 'int'>.",
        "  in for.k",
    ],
}


def test_line30(capsys: CaptureFixture[str]):
    do_test(line30, capsys)


line31 = {
    "file": "tests/data/line/hello31.pdl",
    "errors": [
        "",
        # The two `Missing required field` lines this used to assert were the
        # same `FunctionBlock` misselection as `test_line`: a program whose root
        # is `{defs: ...}` shares nothing with any block but `defs`, which every
        # block has. The discriminator answers `empty`, every key is one a block
        # accepts, and the only real fault is the one inside `defs`.
        "tests/data/line/hello31.pdl:9 - Field not allowed: show_result",
        "  in defs.get_current_weather.return.show_result",
    ],
}


def test_line31(capsys: CaptureFixture[str]):
    do_test(line31, capsys)


line32 = {
    "file": "tests/data/line/hello32.pdl",
    "errors": [
        "",
        "tests/data/line/hello32.pdl:4 - Type errors during spec checking:",
        "  in defs.x.spec",
        "tests/data/line/hello32.pdl:4 - 1 should be of type <class 'str'>",
    ],
}


def test_line32(capsys: CaptureFixture[str]):
    do_test(line32, capsys)
