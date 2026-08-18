# Release notes

Changes that need an action from you, newest first. Everything else is in the
[commit history](https://github.com/IBM/prompt-declaration-language/commits/main)
and the [GitHub releases](https://github.com/IBM/prompt-declaration-language/releases).

## Unreleased

### A retry is no longer reported at all

A block with `retry:` that failed and was about to be retried used to print the
whole Python stack, in hardcoded ANSI red whether or not stderr was a terminal —
on a run that then went on to succeed and exit `0`:

```console
$ pdl prog.pdl
[0;31m[Retry 1/1] prog.pdl:1:1 - An error occurred in a PDL block. Error details: Traceback (most recent call last):
  File ".../pdl_interpreter.py", line 794, in process_advance_block_retry
    ... 5 more frames ...
pdl.pdl_ast.PDLRuntimeError: code block raised ValueError: transient failure
[0m
ok on attempt 2
```

Nothing is written now. A retry is a handled event, and a program that recovers
— because a later attempt succeeded, or because `fallback:` produced a result —
runs to its end with an empty standard error:

```console
$ pdl prog.pdl
ok on attempt 2
```

**What to update.** Anything grepping stderr for `[Retry i/n]` or for
`An error occurred in a PDL block`: neither is ever emitted, so a grep for them
now matches nothing rather than matching a new spelling. The blank lines that
surrounded the banner are gone with it.

**What does not change.** No program's semantics, result, stdout or exit code. A
block that runs out of attempts still fails, with the same diagnostic and the
same exit `1`. `trace_error_on_retry: true` still appends the *full* error —
traceback included — to the background context, byte for byte as before: the
model reading that context is a different audience from the person reading the
terminal, and only the latter's copy went away.

**What you lose, in exchange.** Two things, both real, and neither of them a
side effect — this is what suppressing the notice costs.

*A long retry sequence now looks like a hang.* With `delay`/`backoff`, or with
`retry: -1`, those lines were the only sign that PDL was doing anything at all
between the start of a block and its result. A block that is waiting 30 seconds
before its fourth attempt and a block that is stuck are now indistinguishable
from the outside.

*On a failure, only the last attempt's error survives.* When the attempts run
out, the exception from the final one is what propagates and is reported; the
earlier ones were previously on stderr and are now discarded entirely, with no
copy kept anywhere. A block that fails three times for three different reasons
— a timeout, then a bad response, then a parse error — reports the parse error
alone, and the first two cannot be recovered from the output. The only remaining
channel for that history is `trace_error_on_retry: true`, and it is aimed at the
model rather than at you: it appends each attempt's error to the context the
*next attempt* runs with, so a block inside the retried block can read it — it
is not printed, and a successful final attempt leaves no trace of it in `--trace`
output.

### `pdl-lint` now lints every path you name on the command line (**breaking**)

**A `pdl-lint` invocation that exits `0` today can exit `1` after this change.**
No PDL program changes behaviour; what changes is which files the linter agrees
to look at, and therefore its exit code.

`pdl-lint` used to apply its ignore rules to *every* path, including one you
typed. A file outside the detected project root, or one whose suffix was not
`.pdl`, was skipped — and a skipped file counted as a success:

```console
$ pdl-lint ../shared/prompt.pdl
 - ℹ️  SKIPPING ../shared/prompt.pdl (in ignore list)
----------------------------------------------------------------------------
🎉  All files linted successfully 🎉
$ echo $?
0
```

That file was never opened. A CI job that names a path and checks the exit code
was green whether or not the file was valid — and the stated reason was wrong
as well, since nothing was in any ignore list.

A path you name is now always linted:

```console
$ pdl-lint ../shared/prompt.pdl
 - ❌  ../shared/prompt.pdl
     ../shared/prompt.pdl:4:5 - Field not allowed: parameterss
  in text[0].parameterss
----------------------------------------------------------------------------
😮  Linting failed for 1 file(s):
 - ../shared/prompt.pdl
$ echo $?
1
```

This is the settled convention elsewhere: ruff checks files passed directly on
the command line even when they would normally be excluded, and eslint has
`--no-ignore` for the same reason.

**What now fails.** An invocation that names a path which the ignore rules would
have skipped, *and* whose file does not lint clean. If the file is valid, the
exit code is unchanged; the run simply reports `✅` instead of `SKIPPING`.

**What does not change:**

- **Directory walks are unaffected.** `ignore`, `directories_to_ignore`, the
  project-root check and the `*.pdl` suffix filter all still apply when
  `pdl-lint` walks a directory, with or without `-r`. That is the case those
  rules were written for.
- **No PDL program's semantics or output changes.** This is the linter's exit
  code only.

**If you relied on the old behaviour**, name a directory rather than a file, or
drop the path from the command line — a walk still honours your ignore list.

### `pdl-lint` skip messages now name the real reason

Every skipped file used to be reported as `(in ignore list)`, whichever of the
four rules had actually skipped it. The line now says which:

```console
 - ℹ️  SKIPPING vendor/v.pdl (in a directory marked to be ignored)
 - ℹ️  SKIPPING notes.txt (not a *.pdl file)
```

`LinterConfig.should_ignore` returns the reason (an `IgnoreReason`) or `None`,
rather than a `bool`. Every reason is truthy and `None` is falsy, so
`if config.should_ignore(path):` keeps working unchanged.

### `pdl-lint` no longer prints a traceback for a broken `code:` block

A `code:` block that Python's parser rejected used to escape as a raw
`SyntaxError` traceback ending in `File "<unknown>", line 1`. It is now a
diagnostic, in the same shape `pdl` itself uses for a `code:` block:

```console
$ pdl-lint prog.pdl
 - ❌  prog.pdl
     prog.pdl - `code:` block is not valid Python: invalid syntax

code:1 | x = = 1
       |     ^

  `pdl-lint` parses every `code:` block with Python's own parser. The block
  must be syntactically valid Python even though the linter never runs it.

  note: `code:N` line numbers are within the block's code, not the PDL file.
```

The exit code is unchanged: this file failed to lint before and still does.

### `pdl-lint` no longer prefixes a diagnostic with PDL's exception class name

`PDLParseError: ` / `PDLYamlError: ` in front of an already-rendered diagnostic
was PDL's internal vocabulary leaking into the linter's output; the `pdl`
interpreter prints the same diagnostic without it. Anything grepping
`pdl-lint`'s output for a class name needs updating.

### `pdl-lint` indents the whole diagnostic, not just its first line

`pdl-lint` lists one line per file and prints the diagnostic under it. Only the
first line of that diagnostic used to be indented, so the excerpt, the caret, the
wrapped explanation and the `in <path>` line all started in column 0 — the second
line of one error did not line up with its first:

```console
 - ❌  prog.pdl
     prog.pdl:3:6 - not valid YAML: expected the end of the list, but found another value

2 |   - "hello
  |     ^ this double quote opens a string that is never closed on this line
```

```console
 - ❌  prog.pdl
     prog.pdl:3:6 - not valid YAML: expected the end of the list, but found another value

     2 |   - "hello
       |     ^ this double quote opens a string that is never closed on this line
```

The block is shifted, never re-wrapped, so the text is identical to what `pdl`
prints for the same error — only its left margin differs. Anything matching
`pdl-lint`'s output by column, or with an anchored (`^`) regular expression,
needs updating; matching by substring is unaffected.

### `parser: csv` now reports a quoted field that is never closed (**breaking**)

**A program that exits `0` today can exit `1` after this change.** It is one of
the few deliberate semantic changes in this release, and it is here because the
old behaviour did not fail — it answered, wrongly.

Given output with a `"` that is never closed, `parser: csv` used to swallow
everything after it into a single field and hand that back as the result:

```console
$ pdl --stream none csv.pdl        # text: a,b,c / "unterminated,1 / x,y
[["a", "b", "c"], ["unterminated,1\nx,y\n"]]
$ echo $?
0
```

Two rows of real data disappeared into a string, and nothing said so. That is
now an error:

```console
$ pdl --stream none csv.pdl
csv.pdl:5:1 - `parser: csv` found a quoted field that is never closed
  in parser

output:2 | "unterminated,1
         | ^ this quote is never closed

  note: the 1 line below it was read as part of this field rather than as a
        row of its own.
  help: add the closing `"`, or remove the opening one if the field was not
        meant to be quoted.
$ echo $?
1
```

**What now fails.** One shape, and only one: a quoted field with no closing `"`.

| Output | Was | Now |
| --- | --- | --- |
| `1,"x` — a quoted field with no closing `"` | `["1", "x\n"]`, plus every later line swallowed into it | error |

**What does not change**, measured rather than assumed:

- **Legitimate multi-line quoted fields still parse.** A `"` that opens a field
  spanning several lines closes at some point, and only a quote that never
  closes is an error. This is the case that would have made the change unsafe.
- **Text after a closing `"` still parses**, unchanged: `1,"Ada" Lovelace` still
  gives `["1", "Ada Lovelace"]`, and `1,"Ada" ` — with nothing after the quote
  but a space — still gives `["1", "Ada "]`. An earlier version of this change
  rejected both, because Python's `csv` module offers strict parsing as a single
  switch that cannot be asked for one rule and not the other. It was narrowed
  deliberately: those results are usually what the author meant, a model asked
  for CSV really does emit them, and a trailing space is not a reason to stop a
  working program. PDL therefore returns a parse that Python's strict mode would
  have rejected — an inconsistency accepted on purpose, and written down here
  rather than left to be discovered.
- **Ragged rows are still accepted in silence.** `a,b,c` followed by `1,2` is
  not an error and is not planned to become one: PDL returns a list of lists,
  which can legitimately be ragged.
- **Embedded NULs, and a bare `"` in the middle of an *unquoted* field**
  (`1,va"lue`) are still accepted, as they were.
- Well-formed CSV, the field-size limit, and every other `parser:` are untouched.

Nothing in this repository is affected: across its 263 `.pdl` files exactly one
uses `parser: csv`, and it is the reproducer for the field-size limit. Every
`.pdl` under `examples/` and `tests/data/` was run before and after and not one
changed its exit code or its output. **The residual risk is to programs outside
this repository** — if you parse CSV that a model produced, output that used to
return a wrong value now returns an error, which is the point of the change but
is a change all the same.

**If you need the old behaviour**, remove `parser: csv` and parse the text
yourself in a `lang: python` block, where you choose the dialect.

### Every diagnostic header carries a column

The header of a diagnostic built from a source location is now
`file:line:col - ` where it was `file:line - `:

```console
$ pdl prog.pdl
prog.pdl:3:18 - the list in `jitter:` should have exactly 2 items, but it has 3
  in retry.jitter

3 |   jitter: [1, 2, 3]
  |                  ^ one item too many
```

The column has been computed for every location since positions started coming
from the YAML parser's own marks (below); it was the one coordinate the printer
held back. It comes off the *same* mark as the line, so the two always name one
construct — the block, key or list item the `  in <path>` line names — and can
never disagree, and where a diagnostic also draws a caret the header and the
caret now agree. Where PDL knows the fault to the character, the column says so:
which item of a flow sequence is one too many, which entry of a one-line
`spec: {name: string, age: integer}` the result violated. Where PDL knows only
the enclosing construct — a `code:` block whose failing statement is three lines
further down — the column is the exact position of that construct, no more
precise than the line beside it and no less.

Structured diagnostics (`E-CLI-*`, `E-PARSE-*`, a failing `import:`) have printed
`file:line:col - ` since they were introduced, so this makes one header shape
across the whole tool. A column that is genuinely unknown is still omitted rather
than printed as `:0`, which is why a diagnostic about a program with no source at
all still reads `line 0 - `, and why a failing `import:` still reads
`prog.pdl:1 - `: its span carries no column to print.

**If you match on diagnostic text (SDK).** Nothing about a program that runs
changes: same result, same exit code, same success-path output, same exit code
`1` on failure. What moves is stderr, and any regular expression anchored on
`^(\S+):(\d+) - ` needs a third, optional group: `^(\S+):(\d+)(?::(\d+))? - `.
`PDLRuntimeError.message` is unchanged — the header is added by the printer —
but the elements of `PDLParseError.message`, which carry their own header, are
not.

### Diagnostics name the block they are about

Every diagnostic built from a source location now carries the **block path** on a
line of its own, under the header:

```console
$ pdl prog.pdl
sub/imported.pdl:4:5 - Error during the evaluation of ${ kaboom }: 'kaboom' is undefined
  in defs.f.return
```

The path was always computed; it was simply never printed. It is the route from
the top of the file to the block that failed — `text[0].code`, `defs.f.return`,
`contribute[0]` — written the same way everywhere, with dots between names and
no dot before a `[n]`. Where it says something the file and line do not, it says
it: which of two `code:` blocks raised, which function body an expression was
in, that `contribute:` read two list items as one entry.

Boundary diagnostics (`E-CLI-*`, `E-PARSE-*`, a failing `import:`) have printed
this line since they were introduced; the change brings runtime, schema and type
diagnostics into the same shape. A diagnostic about the program as a whole — a
one-block program, or a complaint about the document itself — has an empty path
and prints no such line.

**If you match on diagnostic text (SDK).** Nothing about a program that runs
changes: same result, same exit code, same success-path output, same exit code
`1` on failure. What moves is stderr, and two exception fields:

- `PDLRuntimeError.message` is unchanged. The path is added by the printer, so
  it appears in `pdl`'s output but not in the exception's own `message`.
- `PDLParseError.message` is still a `list[str]`, one element per schema
  complaint, and still readable as prose through `.text`. Each element may now
  **contain a newline**: the complaint, then its `  in <path>` line. An element
  is no longer guaranteed to be a single line. Splitting `.text` on `"\n"` to
  count complaints was never right and is now definitely wrong.
- Type-checking messages (`Type errors during spec checking:` and the function-call
  form) embed the same list, so they gain the same interior newlines.

### Source positions come from the YAML parser — `PdlLocationType` changes shape

Reported line numbers used to be reconstructed by a regex scan over the program
text, which split each line on `":"` to guess what was on it. It counted comment
lines as structure, had no notion of flow style or of a multi-line scalar, and
had nothing to say about a top-level block. Positions now come from the marks
PyYAML computes while parsing, so they are exact, and they include a **column**
for the first time — the column the headers described above now print.

Three things move as a result:

- **An error after a comment, or inside an imported function, now reports the
  right line.** A program with a comment block at the top used to report every
  subsequent error some lines early; an expression that failed inside a function
  defined in an imported file reported the first line of that file.
- **A top-level block is `file:1`, not `file:0`.** `read:`, `code:` and
  `include:` programs of one block each used to report line 0.
- **A program parsed from a string is named `<program>`.** Diagnostics from
  `exec_str` and the notebook magic read `<program>:4:1 - ...` where they used to
  read `line 4 - ...`, matching the label such a program's YAML errors already
  carried. `line N - ` now means what it says: a program with no source text at
  all, such as one built with `exec_dict` — which is also why it is the one
  header that gains no column: there are no marks to take one from.
- **A program that a program produced is named for where it came from.** The
  `code:` of a `lang: pdl` block is a program of its own with no file name;
  diagnostics about it now read
  `<program:prog.pdl#text[0].code>:4:1 - ...`, naming the file that contains the
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
