# Release notes

Changes that need an action from you, newest first. Everything else is in the
[commit history](https://github.com/IBM/prompt-declaration-language/commits/main)
and the [GitHub releases](https://github.com/IBM/prompt-declaration-language/releases).

## Unreleased

### Source positions come from the YAML parser — `PdlLocationType` changes shape

Reported line numbers used to be reconstructed by a regex scan over the program
text, which split each line on `":"` to guess what was on it. It counted comment
lines as structure, had no notion of flow style or of a multi-line scalar, and
had nothing to say about a top-level block. Positions now come from the marks
PyYAML computes while parsing, so they are exact, and they include a **column**
for the first time.

Three things move as a result:

- **An error after a comment, or inside an imported function, now reports the
  right line.** A program with a comment block at the top used to report every
  subsequent error some lines early; an expression that failed inside a function
  defined in an imported file reported the first line of that file.
- **A top-level block is `file:1`, not `file:0`.** `read:`, `code:` and
  `include:` programs of one block each used to report line 0.
- **A program parsed from a string is named `<program>`.** Diagnostics from
  `exec_str` and the notebook magic read `<program>:4 - ...` where they used to
  read `line 4 - ...`, matching the label such a program's YAML errors already
  carried. `line N - ` now means what it says: a program with no source text at
  all, such as one built with `exec_dict`.
- **A program that a program produced is named for where it came from.** The
  `code:` of a `lang: pdl` block is a program of its own with no file name;
  diagnostics about it now read
  `<program:prog.pdl#text[0].code>:2 - ...`, naming the file that contains the
  block and the block path of the `code:` field, instead of `<program>`. It also
  stops such a block from taking the name of the program that ran it: a string
  program containing one used to report line numbers belonging to the nested
  code from that point on.

**`PdlLocationType` loses `table` and gains `line` and `col` (SDK).** The type is
`(file, line, col, path)`. The per-file line data it used to carry moved into a
registry keyed by file name, which is what makes the imported-function bug above
unrepresentable rather than merely fixed: a location can only ever be resolved
against the file it names. Code constructing a `PdlLocationType` by hand — the
only field that was ever useful to pass is `file` — should drop `table=`; code
reading `.table` has no replacement and almost certainly wanted `.line`.

`pdl-schema.json` and the viewer's generated types change with it, and so does
`model_dump()` on any block. Files written by `pdl --trace` do **not**: the trace
writer has never emitted `pdl__location` (`pdl_dumper.py:387-388` is commented
out), and the viewer strips the field on load in any case.

Nothing about a program that runs today changes: same result, same exit code,
same output on the success path.

### A failing `import:` reports a diagnostic, and raises `PDLRuntimeError` (SDK)

An `import:` naming a file that cannot be read used to end in a Python traceback.
It now prints a diagnostic that leads with the path you wrote, names the file
PDL actually opened — `import:` appends `.pdl` and resolves the path from the
directory of the program `pdl` was started with — and says what is in that
directory:

```console
$ pdl prog.pdl
prog.pdl:1 - cannot import `nosuch`: no such file `nosuch.pdl`
  in import

  `import: nosuch` looks for the file `nosuch.pdl`: PDL appends `.pdl` to an
  import path that does not already end in it. Nothing exists at that path,
  and the current directory contains no other `.pdl` files.

  help: check the path; it is resolved relative to the current directory.
```

**One `except` clause stops matching.** `exec_file`/`exec_program` on a program
whose `import:` names a missing file used to raise the bare `FileNotFoundError`
(or `UnicodeDecodeError`, for an imported file that is not UTF-8) from the
interpreter's own `open`. It now raises `pdl.pdl_ast.PDLRuntimeError`, like every
other failure inside a running program and like `include:` has always done. The
errno shims that keep `except FileNotFoundError` working around `parse_file`
cannot be applied here, because `PDLRuntimeError` is shared with about forty
other runtime failures. Catch `PDLRuntimeError` instead; `str(exc)` and
`exc.message` are the rendered diagnostic, and the original `OSError`, `errno`
and `filename` included, is still reachable through `__context__`. A
`retry:` configured with `exceptions: FileNotFoundError` around the `import:`
keeps matching, because the retry filter looks through the wrapping.

Nothing about a program that runs today changes: same result, same exit code,
same output on the success path.

### Reading a program now fails with a PDL diagnostic instead of a traceback

`pdl` used to end in a Python traceback when the program file was missing, was a
directory, was not valid YAML, or was not valid UTF-8. It now prints a
diagnostic naming the file, the line, the column and, where there is one, the
offending text with a caret under it, and exits `1` as before:

```console
$ pdl prog.pdl
prog.pdl:1:8 - not valid UTF-8: byte 0xff cannot start a UTF-8 character

1 | text: "�� bad"
  |        ^ here

  A PDL program must be UTF-8 encoded text. This file is not, so it cannot be
  read at all.

  help: re-save the file as UTF-8.
```

Nothing about a program that runs today changes: same result, same exit code,
same output on the success path.

### Two `except` clauses stop matching around `exec_file` (SDK)

The exceptions raised while reading and parsing a program are now subclasses of
`pdl.pdl_parser.PDLParseError`, so they carry `.text` (the rendered diagnostic)
and `.diagnostic` (the structured record). Use `.text` for display: `.message`
is also present but is a `list[str]`, so printing it gives a bracketed list.

Almost every `except` clause you have already written keeps working, on purpose:
`FileNotFoundError`, `IsADirectoryError`, `PermissionError`, `OSError` and
`yaml.YAMLError` all still match, with `errno`, `strerror` and `filename`
carried across unchanged.

**Two do not.** If your code has either of these around `exec_file`, `exec_str`
or `parse_file`, it needs an edit:

| Clause | Now raised | Why it could not be kept |
| --- | --- | --- |
| `except UnicodeDecodeError` | `pdl.pdl_parser.PDLUnicodeDecodeError` | `UnicodeDecodeError.__init__` requires exactly five arguments, so no subclass of it can also carry a PDL message. |
| `except yaml.MarkedYAMLError` | `pdl.pdl_parser.PDLYamlError` | It derives from `yaml.YAMLError`, the broader and far more common clause, rather than from its marked subclass. |

The replacement in both cases is `except PDLParseError`, or the specific class if
you want to tell the two apart:

```python
from pdl.pdl import exec_file
from pdl.pdl_parser import PDLParseError

try:
    result = exec_file("prog.pdl")
except PDLParseError as exc:
    print(exc.text)      # the rendered diagnostic
    raise
```

The object you catch is still usable as the one it replaces. `PDLUnicodeDecodeError`
carries `encoding`, `object`, `start`, `end` and `reason`, so
`except UnicodeDecodeError as e: e.start` becomes
`except PDLParseError as e: e.start` and nothing else changes — except that
`start` and `end` are now *guaranteed* to be offsets into the file rather than into whatever the
decoder happened to be handed. `PDLYamlError` keeps PyYAML's own exception,
marks included, on `__cause__`. `str(exc)` is the rendered diagnostic in both
cases.
