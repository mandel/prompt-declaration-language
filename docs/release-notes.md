# Release notes

Changes that need an action from you, newest first. Everything else is in the
[commit history](https://github.com/IBM/prompt-declaration-language/commits/main)
and the [GitHub releases](https://github.com/IBM/prompt-declaration-language/releases).

## Unreleased

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
`pdl.pdl_parser.PDLParseError`, so they carry `.message`, `.text` (the rendered
diagnostic) and `.diagnostic` (the structured record). Almost every `except`
clause you have already written keeps working, on purpose:
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
`start` and `end` are now offsets into the file rather than into whatever the
decoder happened to be handed. `PDLYamlError` keeps PyYAML's own exception,
marks included, on `__cause__`. `str(exc)` is the rendered diagnostic in both
cases.
