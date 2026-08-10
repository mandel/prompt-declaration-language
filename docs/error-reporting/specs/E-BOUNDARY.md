# E-BOUNDARY — catching errors at the CLI/parse boundary

Covers **E-CLI-001, E-CLI-002, E-CLI-003, E-CLI-004, E-PARSE-001, E-PARSE-002,
E-PARSE-005**. Phase-3 item 1.

Seven corpus entries, one root cause: nothing between `argparse` and the first PDL block
is inside a `try`. `main` (`pdl.py:213-337`) catches nothing at all; `parse_file`
(`pdl_parser.py:18-21`) opens and decodes a file with no handler; `parse_str`
(`pdl_parser.py:30`) calls `yaml.safe_load` with no handler. Seven S0 tracebacks fall out
of three unguarded statements, so they get one spec and should get one commit.

The other half of the story is that the information needed to fix five of the seven is
**already computed and discarded**. PyYAML raises `MarkedYAMLError` carrying `problem`,
`problem_mark`, `context`, `context_mark`, and each `Mark` carries `name`, `line`,
`column`, `buffer` and `pointer` (`yaml/error.py:4-43`). Because `parse_str` hands
`yaml.safe_load` a `str`, the reader stores the **entire program** in `mark.buffer`
(`yaml/reader.py:72-75`), so a source excerpt and a caret are available for free. This is
the only item in Phase 3 that reaches **Location 3 without depending on item 0**.

---

## Where the catch belongs

Three tiers, and the split matters more than the wording.

**Tier 1 — `pdl_parser.py`. Everything about reading and YAML-parsing a PDL source.**
Two new subclasses of the existing `PDLParseError` (`pdl_parser.py:14-15`):

| Class | Raised by | Wraps |
| --- | --- | --- |
| `PDLSourceError(PDLParseError, OSError)` | `parse_file` around `open`/`read` (`:19-20`) | `FileNotFoundError`, `IsADirectoryError`, `PermissionError`, other `OSError`, `UnicodeDecodeError` |
| `PDLYamlError(PDLParseError)` | `parse_str` around `yaml.safe_load` (`:30`) | `yaml.MarkedYAMLError` and, as a fallback, `yaml.YAMLError` (chiefly the unmarked `ReaderError`, `yaml/reader.py:24-43`) |

Subclassing `PDLParseError` is what makes this cheap: `generate` already handles
`PDLParseError` at `pdl_interpreter.py:246-248` with `print("\n".join(exc.message))` and
`return 1`. **E-PARSE-001/002/005 therefore need no change in `pdl.py` at all** — only
`pdl_parser.py`. The same clause is what `process_include` (`:3011`) and the linter
(`pdl_linter.py:375`) already use, so those two entry points improve at the same time
(see Risk).

`message` stays a `list[str]`, one element, holding the fully rendered diagnostic. That
keeps `"\n".join` correct and `PDLParseError`'s constructor untouched. The structured
record rides along as an added attribute (`exc.diagnostic`), and the original exception
stays reachable through `__cause__` (`raise ... from exc`).

**Tier 2 — `pdl.py:main`. Everything that is not the program.** `-f`/`-d` scope data
(`:290-294`) and `validate_scope` (`:295`) run *before* `generate`, so `generate` cannot
see them. `main` wraps the block from `initial_scope = ...` through the
`pdl_interpreter.generate(...)` call in one `try`:

```
except PDLException as exc:      # PDLParseError, PDLScopeError, and future siblings
    print(exc.text, file=sys.stderr); return 1
except Exception as exc:         # last resort — the §5.8 invariant, not a nicety
    print(<internal-error diagnostic>, file=sys.stderr); return 1
```

The last-resort clause is the difference between "these seven no longer crash" and "no
traceback ever reaches the user". It prints an internal-error diagnostic that names the
exception type, says it is a PDL bug, and points at the issue tracker; the traceback is
printed only when `PDL_TRACEBACK=1` is set. `KeyboardInterrupt` and `SystemExit` are
`BaseException` and are not caught. `--sandbox` (`:283-287`) returns before the block.

`pdl_infer.py:257-263` is a byte-for-byte copy of the `-f`/`-d`/`validate_scope` sequence
and must use the same helper, or `pdl-infer` keeps every traceback this item removes.

**Tier 3 — `pdl_utils.validate_pdl_model_defaults` (`:211-221`) raises a located error
instead of `ValueError` / bare `assert`.** New `PDLScopeError(PDLException, ValueError)`
carrying `origin` (which of built-in defaults, `-f <file>`, `--data` supplied the key),
`path` (`pdl_model_default_parameters[0]["*"]`) and the offending value. Inheriting
`ValueError` as well as `PDLException` keeps any existing `except ValueError` around
`validate_scope` working — `validate_scope` is re-exported from `pdl.pdl`
(`pdl.py:29-33`), so it is informally public. The two bare `assert`s at
`pdl_utils.py:215` and `:221` become real checks; `assert` is stripped under `-O` and one
of them is the "bare `AssertionError`" noted in INVENTORY §2.

**The SDK (§5.8).** `exec_file`/`exec_str`/`exec_dict` (`pdl.py:133-202`) gain no `try`.
They keep propagating exceptions, and those exceptions become *more* useful: today a
caller gets `FileNotFoundError` or `yaml.parser.ParserError` with no PDL context, after
this they get a `PDLParseError` subclass with `.message`, `.diagnostic` (file, line,
col, spans, suggestions), `.text` (the rendered form) and `__cause__` pointing at the
original. Callers already have to handle `PDLParseError` for schema errors, so the
handling surface does not grow. One incompatibility, flagged in Risk: `except
FileNotFoundError` around `exec_file` stops matching unless the dual-inheritance trick
holds.

**Getting the real filename into the mark.** `parse_str` receives `file_name`
(`pdl_parser.py:25-29`) but `yaml.safe_load` is given a bare `str`, so
`Reader.__init__` sets `self.name = "<unicode string>"` (`yaml/reader.py:72-73`). Two
options, and the design uses both:

1. *Do not depend on it.* The renderer builds the header from `file_name` and the excerpt
   from `pdl_str`, which `parse_str` already holds. `mark.name` is never printed.
2. *Fix it anyway*, in the handler: `Mark` is a plain mutable object, so
   `exc.problem_mark.name = file_name or "<program>"` (and the same for `context_mark`)
   costs two lines and makes `str(exc)` correct for any SDK caller, for `pdl-lint`, and
   for anything that logs the cause.

Wrapping the string in a named `io.StringIO` also works (`yaml/reader.py:81-82` reads
`stream.name`) but is *worse*: the stream path reads in chunks and truncates
`mark.buffer`, which is exactly the data the excerpt needs. Keep passing the string.

---

## The rendering contract

Same shape as `E-CODE-002.md`, extended with the excerpt that PyYAML makes possible.

- Line 1 is `<origin>:<line>:<col> - <message>`, once. `:col` is dropped when unknown,
  `:line` too. The **prefix is omitted entirely** when the diagnostic is *about* the
  origin rather than *inside* it — E-CLI-001 and E-CLI-002 name the path in the message,
  so a `no_such_file.pdl - ` prefix would be noise.
- `<origin>` is `prog.pdl` for the program, the path for a `-f` data file, and the
  literal `--data` for a `-d` argument. Anything that is not a file cannot be mistaken
  for one.
- Optional `  in <path>` line, for the scope/block path.
- Excerpt lines are `<lineno> | <source>`; annotation lines are `  | ` plus spaces plus
  `^` plus an optional label. At most two annotated lines (PyYAML gives at most two
  marks); non-adjacent lines are elided with `...`. Tabs in an excerpt are rendered as a
  single space, so caret arithmetic stays trivial — the message names the tab explicitly
  when a tab is the problem.
- Rule paragraph: indented two spaces, no prefix. `  note:` for context, `  help:` for
  the action. Continuation lines align under the text.
- No ANSI, no absolute paths, no severity token (as in E-CODE-002 — a severity prefix is
  introduced for all ~70 IDs at once or not at all).

---

## E-CLI-001 — file does not exist

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  File "<REPO>/src/pdl/pdl.py", line <LINE>, in main
    exit_code = pdl_interpreter.generate(
  File "<REPO>/src/pdl/pdl_interpreter.py", line <LINE>, in generate
    prog, loc = parse_file(pdl_file)
  File "<REPO>/src/pdl/pdl_parser.py", line <LINE>, in parse_file
    with open(pdl_file, "r", encoding="utf-8") as pdl_fp:
FileNotFoundError: [Errno 2] No such file or directory: 'no_such_file.pdl'
```

Rubric: L0 W0 Y1 F0 H0 = 1/15

### Target

The corpus work directory for this entry contains no other files, so the near-miss branch
cannot fire; this is the weakest branch of the diagnostic, shown honestly.

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
cannot read `no_such_file.pdl`: no such file

  `pdl` takes the path of a PDL program file. Nothing exists at that path, and
  the current directory contains no `.pdl` files.

  help: check the path, or run `pdl --help` for the expected arguments.
```

Rubric: L1 W3 Y3 F1 H3 = 11/15

Location is capped at 1 and that is not a defect to fix later: the error is about the path
itself, so there is no line, column or excerpt that could exist. Fix is the branch's
weakness, and the other branches earn it back:

| Condition | Second sentence | Suggestion |
| --- | --- | --- |
| given path has no suffix and `<path>.pdl` exists | `Nothing exists at that path.` | `help: did you mean \`hello.pdl\`?` |
| a sibling name matches within `difflib.get_close_matches(..., n=1, cutoff=0.7)` over `parent.iterdir()` | `Nothing exists at that path.` | `help: did you mean \`hello.pdl\`?` |
| parent directory does not exist | `The directory \`out/\` does not exist either.` | `help: check the path, or run \`pdl --help\` …` |
| parent exists, holds `.pdl` files, none close | `The directory contains \`a.pdl\`, \`b.pdl\`.` (sorted, first 3, then `, and N more`) | `help: check the path, or run \`pdl --help\` …` |
| otherwise (this entry) | as shown | as shown |

Near-miss branches score F3 → 13/15. Directory listings are `sorted()`, never `set`
iteration — the output must not vary between runs (RUBRIC, hygiene 0).

---

## E-CLI-002 — path is a directory

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  File "<REPO>/src/pdl/pdl.py", line <LINE>, in main
    exit_code = pdl_interpreter.generate(
  File "<REPO>/src/pdl/pdl_interpreter.py", line <LINE>, in generate
    prog, loc = parse_file(pdl_file)
  File "<REPO>/src/pdl/pdl_parser.py", line <LINE>, in parse_file
    with open(pdl_file, "r", encoding="utf-8") as pdl_fp:
IsADirectoryError: [Errno 21] Is a directory: 'sub'
```

Rubric: L0 W0 Y1 F0 H0 = 1/15

### Target

`sub/` contains only `keep.txt`, so there is no `.pdl` file to suggest.

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
cannot read `sub`: it is a directory, not a PDL program file

  `pdl` takes the path of one PDL program file, usually with a `.pdl` suffix.
  `sub` contains no `.pdl` files.

  help: give the path of a program file, e.g. `pdl sub/main.pdl`.
```

Rubric: L1 W3 Y3 F2 H3 = 12/15

**Why this is not the same diagnostic as E-CLI-001.** "The path does not resolve to a
readable program" is the shared *cause*; the *next action* is different. For a missing
file the user probably mistyped a name — the useful reply is a name. For a directory the
user gave a container — the useful reply is what is inside it:

| Condition | Second sentence | Suggestion |
| --- | --- | --- |
| exactly one `.pdl` inside | `\`sub\` contains one PDL program.` | `help: did you mean \`pdl sub/main.pdl\`?` → F3 |
| 2–3 `.pdl` inside | `\`sub\` contains \`a.pdl\`, \`b.pdl\`.` | `help: name one of them, e.g. \`pdl sub/a.pdl\`.` |
| more | first 3 then `, and N more` | same |
| none (this entry) | as shown | as shown |

Classify on `Path.is_dir()` inside the `OSError` handler rather than on `errno`: Windows
raises `PermissionError` for `open()` on a directory, and errno-only classification would
send that user to the permissions branch.

---

## E-CLI-003 — malformed inline YAML in `-d`

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  File "<REPO>/src/pdl/pdl.py", line <LINE>, in main
    initial_scope = initial_scope | yaml.safe_load(args.data)
  ... 11 more frames through yaml/ ...
yaml.parser.ParserError: while parsing a flow node
expected the node content, but found '<stream end>'
  in "<unicode string>", line 1, column 5:
    {a:
        ^
```

Rubric: L0 W0 Y1 F0 H0 = 1/15

The caret is right there, under a heading that says `<unicode string>` — the one label
guaranteed not to tell the user which of their two inputs is broken.

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
--data:1:5 - not valid YAML: expected a value, but the input ended

1 | {a:
  |     ^ a value is expected here

  `--data` (`-d`) is read as a YAML mapping of variable names to values.

  note: this is the command-line argument, not `prog.pdl`; the program was
        never read.
  help: give the key a value and close the brace, e.g. -d '{a: 1}'
```

Rubric: L3 W3 Y3 F2 H3 = 14/15

Point 3 of the brief is settled by two devices used together, because either alone is
missable: the origin token in the location prefix is `--data`, which cannot be read as a
filename, and the `note:` states in words that `prog.pdl` was not involved. The claim is
true — `main` parses `-d` at `:294`, before `generate` at `:331`, so the program really
has not been opened yet.

The trailing space of the argument `'{a: '` is stripped from the excerpt line by
`harness.normalize` (`tests/errors/harness.py:275`); the golden shows `1 | {a:`. The
excerpt is capped at 75 characters around the caret, as `Mark.get_snippet` does
(`yaml/error.py:14-35`), with ` ... ` elision — a `-d` argument can be arbitrarily long
and must not become a wall.

`-f` data files use the identical renderer with the file path as origin and
`note: read as scope data because of \`-f\`.`, so a YAML error in a data file is never
mistaken for one in the program either.

---

## E-CLI-004 — malformed `pdl_model_default_parameters`

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  File "<REPO>/src/pdl/pdl.py", line <LINE>, in main
    validate_scope(initial_scope)
  File "<REPO>/src/pdl/pdl_utils.py", line <LINE>, in validate_scope
    validate_pdl_model_defaults(scope["pdl_model_default_parameters"])
  File "<REPO>/src/pdl/pdl_utils.py", line <LINE>, in validate_pdl_model_defaults
    raise ValueError(
ValueError: invalid defaults 3 for model matcher *
```

Rubric: L0 W1 Y2 F0 H0 = 3/15

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
--data - malformed `pdl_model_default_parameters`
  in pdl_model_default_parameters[0]

  Each entry maps a model-name pattern to a table of parameters. Here the
  pattern `*` is mapped to `3`, which is not a table.

  note: this value comes from the `--data` command-line argument, not from
        `prog.pdl`.
  help: wrap the parameters in a table, e.g.
        -d '{pdl_model_default_parameters: [{"*": {temperature: 3}}]}'
```

Rubric: L1 W3 Y3 F3 H3 = 13/15

Location is 1 and stays 1 until item 0: the offending value's line and column inside the
`-d` string require a marks-recording loader, which is item 0's `SafeLoader` subclass
(§5.1/5.2). The record already carries `path`, so that upgrade is a rendering change and
this spec is not redone — after item 0 the header becomes `--data:1:33` with an excerpt
and caret, and Location goes to 3.

`origin` is not guessed. `main` knows which source last supplied the key, because it
merges them in order — built-in defaults (`pdl.py:289`), then `-f` (`:290-292`), then
`-d` (`:293-294`) — so a membership test on each dict as it is merged gives the origin
exactly. The three renderings of the `note:` are "the built-in model defaults" (which
would be a PDL bug, and should say so), "the data file `scope.yaml` given with `-f`", and
the one above.

The same diagnostic covers the sibling shapes that today produce a bare `AssertionError`:
`pdl_model_default_parameters` not a list, and an entry that is not a mapping.

---

## E-PARSE-001 — unterminated quoted scalar

Reproducer:

```yaml
text:
  - "hello
  - "world"
```

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  ... 13 frames, 6 of them inside yaml/ ...
yaml.parser.ParserError: while parsing a block collection
  in "<unicode string>", line 2, column 3:
      - "hello
      ^
expected <block end>, but found '<scalar>'
  in "<unicode string>", line 3, column 6:
      - "world"
         ^
```

Rubric: L1 W0 Y2 F0 H0 = 3/15

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:3:6 - not valid YAML: expected the end of the list, but found another value

2 |   - "hello
  |     ^ this double quote opens a string that is never closed on this line
3 |   - "world"
  |      ^ so YAML read everything up to here as one value

  A PDL program is a YAML document: it must parse as YAML before any PDL rule
  is checked.

  help: close the string on line 2, or escape the quote as \"
```

Rubric: L3 W3 Y3 F3 H3 = 15/15

**Read that 15 narrowly.** It is available here only because PyYAML computed both marks
and kept the buffer; it is not the class average, and the *generic* branch below is 12.
Location scores 3 with no block path because a document that did not parse has no blocks
— there is no `loc.path` to render, and inventing one would be worse.

The two caret labels come from different places, and the spec is explicit about which is
which:

- Line 3, column 6 is `problem_mark`, verbatim from PyYAML. Always shown.
- Line 2 is `context_mark` (line 2, **column 3**, the `-` that opens the block
  collection). The caret is moved to **column 5** only when the unterminated-quote
  recognizer fires: scanning the lines from `context_mark.line` to `problem_mark.line`
  for the first line with an odd number of `"` or `'` after removing `\"` and `\'`
  escapes. This is the one heuristic in the design, it is cheap, and when it does not
  fire the diagnostic degrades to the generic branch instead of guessing:

```
prog.pdl:3:6 - not valid YAML: expected the end of the list, but found another value

2 |   - "hello
  |   ^ while parsing the list that starts here
3 |   - "world"
  |      ^ unexpected value

  A PDL program is a YAML document: it must parse as YAML before any PDL rule
  is checked.

  help: check the indentation and the quoting of lines 2-3.
```

Generic branch: L3 W2 Y3 F1 H3 = 12/15.

**Recognizers.** The headline and the `help:` are computed from PyYAML's `problem`
string, which is a small closed set of literals in `yaml/parser.py` and
`yaml/scanner.py`. Five recognizers, plus the generic fallback, cover everything the
corpus and `examples/` can produce:

| PyYAML `problem` | Headline | `help:` |
| --- | --- | --- |
| `found character '\t' that cannot start any token` | `tab character used for indentation` | replace the leading tab with spaces |
| `expected <block end>, but found ...` | `expected the end of the list/mapping, but found another value` | quote/indent, per the recognizer above |
| `found unexpected end of stream` (while scanning a quoted scalar) | `a quoted string is never closed` | close the quote opened on line N |
| `mapping values are not allowed here` | `unexpected \`:\` in a value` | quote the value |
| `could not find expected ':'` | `a mapping key has no \`:\`` | add `:` after the key |
| anything else | `not valid YAML: <problem, verbatim>` | check the syntax at line N |

The fallback is deliberate: PyYAML's own text is preserved when we have nothing better,
so an unrecognized failure still gets file, line, column, excerpt and caret. Adding a
recognizer later is a table row, not a redesign.

---

## E-PARSE-002 — tab used for indentation

Reproducer: `text:` then a line indented with one tab, `- hello`.

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  ... 13 frames ...
yaml.scanner.ScannerError: while scanning for the next token
found character '\t' that cannot start any token
  in "<unicode string>", line 2, column 1:
    	- hello
    ^
```

Rubric: L1 W1 Y1 F0 H0 = 3/15

PyYAML's text is unusually good here. All it needed was to not be the 58th line of a
traceback.

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2:1 - not valid YAML: tab character used for indentation

2 |  - hello
  | ^ tab character

  YAML allows only spaces for indentation.

  help: replace the leading tab on line 2 with spaces.
```

Rubric: L3 W3 Y3 F3 H3 = 15/15

The tab is rendered as **one space** in the excerpt so that the caret column equals the
source column with no tab-stop arithmetic and no terminal-width assumptions. The tab is
invisible in the excerpt by construction, which is why the caret label and the headline
both name it in words. The alternative — expanding to 4 or 8 columns — makes the caret
depend on a tab-stop convention the user's editor may not share, and the alternative of
printing a literal `\t` shifts every column on the line by one.

---

## E-PARSE-005 — non-UTF-8 bytes in the file

### Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  File "<REPO>/src/pdl/pdl.py", line <LINE>, in main
    exit_code = pdl_interpreter.generate(
  File "<REPO>/src/pdl/pdl_interpreter.py", line <LINE>, in generate
    prog, loc = parse_file(pdl_file)
  File "<REPO>/src/pdl/pdl_parser.py", line <LINE>, in parse_file
    prog_str = pdl_fp.read()
  File "<frozen codecs>", line 322, in decode
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 7: invalid start byte
```

Rubric: L0 W0 Y1 F0 H0 = 1/15

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:1:8 - not valid UTF-8: byte 0xff cannot start a UTF-8 character

1 | text: "�� bad"
  |        ^ here

  A PDL program must be UTF-8 encoded text. This file is not, so it cannot be
  read at all.

  help: re-save the file as UTF-8.
```

Rubric: L3 W3 Y3 F2 H3 = 14/15

**On the byte offset (point 4 of the brief).** `position 7` in today's message is *not
reliably a file offset*. `parse_file` reads through a `TextIOWrapper`
(`pdl_parser.py:19-20`), so `UnicodeDecodeError.object` is the decoder's current chunk
and `.start` is an offset within that chunk. For this 15-byte file the chunk is the whole
file and the number happens to be right; for a 50 KB file it would be confidently wrong,
which the rubric scores below saying nothing.

So the offset is recomputed rather than trusted, and it is cheap:

```
raw = Path(pdl_file).read_bytes()          # one extra read, only on the failure path
raw.decode("utf-8")                        # re-raises with a true file offset in .start
line     = raw.count(b"\n", 0, e.start) + 1
line_beg = raw.rfind(b"\n", 0, e.start) + 1
col      = len(raw[line_beg:e.start].decode("utf-8", "replace")) + 1
excerpt  = raw[line_beg:raw.find(b"\n", e.start)].decode("utf-8", "replace")
```

Decoding the prefix with `errors="replace"` is what makes the column exact rather than
approximate: each undecodable byte becomes exactly one `U+FFFD`, and every decodable
character keeps its width, so character columns in the excerpt and in the caret line
agree. What is honestly reportable is therefore: file, line, character column, the
offending byte value, PyYAML-free, plus the line with the bad bytes shown as `�`. What is
**not** reportable is what the bytes were meant to be — the message must not guess an
encoding it did not detect.

One detected case does earn a better message, and it is the common one:

| Condition | Headline | `help:` |
| --- | --- | --- |
| `raw[:2] in (b"\xff\xfe", b"\xfe\xff")` | `the file is UTF-16, not UTF-8` | re-save as UTF-8 (this file begins with a UTF-16 byte-order mark) → F3 |
| otherwise (this entry) | as shown | as shown |

Verify the exact excerpt against a scratch run before regenerating the golden: the
reproducer's second bad byte was not readable from this workstation, so the number of `�`
characters in the target above is my reading of the file and not a certainty.

---

## Structured record

Decision 5.6. Two additions to the field list in `E-CODE-002.md`, both needed by this
item and both worth having generally — item 0 owns the final shape:

- `origin: "program" | "argument" | "data-file"` alongside `file`. `file` is the display
  token (`prog.pdl`, `--data`, `scope.yaml`); `origin` is what lets the renderer decide
  whether to emit the "this is not your program" note. Without it, a `-d` diagnostic and
  a program diagnostic are indistinguishable in the record.
- `spans: [...]` — an ordered list of `{line, col, end_line, end_col, label, primary}`
  replacing the single `span`, because `MarkedYAMLError` carries two marks and the whole
  point of E-PARSE-001 is showing both. `span` remains as the primary for compatibility
  with records that have one.

E-PARSE-001, the richest:

```json
{
  "id": "E-PARSE-001",
  "severity": "error",
  "origin": "program",
  "file": "prog.pdl",
  "spans": [
    {"line": 3, "col": 6, "primary": true,
     "label": "so YAML read everything up to here as one value"},
    {"line": 2, "col": 5, "primary": false,
     "label": "this double quote opens a string that is never closed on this line"}
  ],
  "block_path": null,
  "message": "not valid YAML: expected the end of the list, but found another value",
  "notes": [
    {"kind": "rule",
     "text": "A PDL program is a YAML document: it must parse as YAML before any PDL rule is checked."}
  ],
  "suggestions": [
    {"text": "close the string on line 2, or escape the quote as \\\""}
  ]
}
```

E-CLI-001, the sparsest:

```json
{
  "id": "E-CLI-001",
  "severity": "error",
  "origin": "program",
  "file": "no_such_file.pdl",
  "spans": [],
  "block_path": null,
  "message": "cannot read `no_such_file.pdl`: no such file",
  "notes": [
    {"kind": "rule",
     "text": "`pdl` takes the path of a PDL program file. Nothing exists at that path, and the current directory contains no `.pdl` files."}
  ],
  "suggestions": [
    {"text": "check the path, or run `pdl --help` for the expected arguments."}
  ]
}
```

The other five, in fields that differ:

| ID | `origin` | `file` | primary span | notes | suggestion `replacement` |
| --- | --- | --- | --- | --- | --- |
| E-CLI-002 | `program` | `sub` | — | rule | — |
| E-CLI-003 | `argument` | `--data` | 1:5 | rule + note("command-line argument, not prog.pdl") | `-d '{a: 1}'` |
| E-CLI-004 | `argument` | `--data` | — (`block_path: ["pdl_model_default_parameters", "[0]"]`) | rule + note(origin) | `-d '{pdl_model_default_parameters: [{"*": {temperature: 3}}]}'` |
| E-PARSE-002 | `program` | `prog.pdl` | 2:1 label `tab character` | rule | — |
| E-PARSE-005 | `program` | `prog.pdl` | 1:8 label `here` | rule | — |

`severity` is carried and not rendered, for the reason given in E-CODE-002: no PDL
diagnostic prints a severity token today.

---

## Where the data comes from

Raise sites: `pdl_parser.py:19` (`open`), `:20` (`read`), `:30` (`yaml.safe_load`);
`pdl.py:291-292` (`-f`), `:294` (`-d`), `:295` (`validate_scope`);
`pdl_utils.py:218` (the `ValueError`).

| Field | Source | Available today? |
| --- | --- | --- |
| `file` (program) | `file_name` parameter of `parse_str`, `pdl_parser.py:25-29`, set by `parse_file` at `:21` | yes |
| `file` (`-d`) | constant `--data`; the value is `args.data`, `pdl.py:294` | yes |
| `file` (`-f`) | `args.data_file`, `pdl.py:290-292` | yes |
| `origin` | which branch of `main` is executing; for E-CLI-004, a membership test on each merged dict at `pdl.py:289/292/294` | yes, one new local |
| span line/col (E-PARSE-001/002) | `exc.problem_mark.line/.column` and `exc.context_mark.line/.column`, `yaml/error.py:4-12`; 0-based, `+1` for display exactly as `Mark.__str__` does at `:39-40` | **yes — computed today and discarded** |
| span line/col (E-CLI-003) | same, over `yaml.safe_load(args.data)` | yes |
| span line/col (E-PARSE-005) | recomputed from `Path.read_bytes()` on the failure path; `UnicodeDecodeError.start` is chunk-relative through `TextIOWrapper` and must not be used as-is | needs the re-read shown above |
| excerpt (program) | `pdl_str`, the parameter of `parse_str` at `:25`; equivalently `mark.buffer`, whole-program because a `str` was passed (`yaml/reader.py:72-75`) | yes |
| excerpt (`-d`) | `args.data` | yes |
| `problem` / `context` text | `MarkedYAMLError.problem`, `.context`, `yaml/error.py:50-56` | yes |
| headline + `help:` | recognizer table keyed on `problem` | new literals |
| near-miss filename | `difflib.get_close_matches` over `Path(p).parent.iterdir()` | stdlib, not currently imported in `pdl_parser.py` (imports at `:1-11`) |
| directory listing | `sorted(p.glob("*.pdl"))` | yes |
| E-CLI-004 path + value | new fields on `PDLScopeError`, raised at `pdl_utils.py:218` where `model_glob` and `glob_defaults` are both in hand (`:216-220`); the index needs `enumerate` on the loop at `:214` | value yes, one-line change |
| exit code 1 | `generate` returns 1 at `pdl_interpreter.py:248`; `main` returns it at `pdl.py:337` | yes |
| `id` | no diagnostic-ID registry exists — item 0 owns it | **no**, unrendered until then |

Nothing in this item needs data the interpreter does not have. The only new computation
anywhere is the re-read in E-PARSE-005 and the quote-parity scan in E-PARSE-001, both on
the failure path only, and no new dependency: `difflib`, `pathlib` and `io` are stdlib.

Two mechanical notes for the implementer. `parse_str` is `@lru_cache`d
(`pdl_parser.py:24`); exceptions are not cached by `lru_cache`, so a failing program
re-parses on every call — no behaviour change, but do not "optimize" by caching the
error. And `PDLParseError` needs a `text` property (`"\n".join(m) if isinstance(m, list)
else str(m)`); three existing sites interpolate `.message` directly and would otherwise
print a Python list repr — see Risk.

---

## Rejected alternatives

**Catch only in `main`.** One `try` around the body of `main` kills all seven tracebacks
and touches one file. Rejected because the boundary is not `main`: `parse_file` is
reached from four entry points — the `pdl` CLI, `pdl-infer`, `pdl-lint`
(`pdl_linter.py:371`) and the SDK's `exec_file` (`pdl.py:196`) — plus `include:`
(`pdl_interpreter.py:3005`). Catching in `main` fixes one of five and leaves E-LINT-002
and E-RUNTIME-001 as S0 tracebacks that a later item has to solve again, differently.
Catching in `pdl_parser.py` fixes all five at once and is the reason this item is
"contained" at all.

**Fix `mark.name` and print `str(exc)`.** Two lines: set the mark's name from
`file_name`, catch, print. It removes the traceback and keeps PyYAML's own caret. The
result is `while parsing a block collection / in "prog.pdl", line 2, column 3: / ... /
expected <block end>, but found '<scalar>' / in "prog.pdl", line 3, column 6: / ...` —
two location prefixes for one logical error (hygiene 2 at best), `<block end>` and
`<scalar>` and "flow node" as user-facing vocabulary (What 1), and no next action at all
(Fix 0). Roughly L2 W1 Y2 F0 H2 = 7/15, and it caps the whole E-PARSE class there, so
every entry would be reopened when the renderer lands. The extra work over the two-liner
is a recognizer table and a gutter formatter.

---

## Risk

**Public API / behaviour change — stop-and-report.** Two items need a human decision
before implementation:

1. `exec_file("nope.pdl")` today raises `FileNotFoundError`; after this it raises
   `PDLSourceError`. The proposal is `class PDLSourceError(PDLParseError, OSError)` so
   that `except OSError` and `except FileNotFoundError`… — note that the second one
   still breaks: a subclass of `OSError` is not a subclass of `FileNotFoundError`. The
   dual inheritance also needs a scratch-run check that CPython accepts the layout
   (`class E(PDLParseError, OSError)`); it should, since `PDLParseError` has the plain
   exception layout, but it must be verified, not assumed. The `UnicodeDecodeError` case
   is worse: `except UnicodeDecodeError` around `exec_file` stops matching outright.
   Decision needed: dual inheritance plus a release note, or plain `PDLParseError` plus a
   release note. Either way it is a documented SDK change, and §5.8 is satisfied on the
   substantive point — the exception a caller receives carries strictly more usable
   information than the one it replaces.
2. `PDL_TRACEBACK=1` is a new environment variable (the escape hatch for the last-resort
   handler). Additive, no CLI surface change; a `--debug` flag was the alternative and
   would be a real CLI addition.

No AST change. No trace-format change. No new dependency.

**Goldens that change as a direct side effect, outside the seven.** These are not
optional and must land in the same commit:

- `E-RUNTIME-001` (include of a missing file): `process_include` already catches
  `PDLParseError` at `pdl_interpreter.py:3011`, so the traceback in its golden
  disappears the moment `parse_file` raises `PDLSourceError`. Its
  `hygiene_traceback_expected` flag must be deleted or `test_no_traceback` XPASSes and
  fails the suite. The *wording* stays wrong — `f"Attempting to include invalid yaml:
  {str(file)}"` says "yaml" about a missing file and embeds an absolute path (issue
  #410) — and belongs to item 4. Say so in the commit rather than silently half-fixing
  it.
- `E-RUNTIME-002` (`import:`) does **not** change: `process_import` opens the file itself
  at `:3037-3038` instead of calling `parse_file`. Routing it through the same helper is
  a two-line change that would fix it here; recommended, but item 4 formally owns it.
- `E-LINT-002` (YAML error under `pdl-lint`): `pdl_linter.py:375` already catches
  `PDLParseError`, so its traceback also disappears — into `logger.error("%s: %s",
  type(e).__name__, e.message)` at `:377`, which prints a **Python list repr**. That is
  E-LINT-001's defect, and this item would spread it. Mitigation is the `.text` property
  plus three one-line edits at `pdl_linter.py:377`, `pdl_interpreter.py:3012` and `:3060`.
  Include them.

**Message-asserting tests.** None break. `tests/test_runtime_errors.py` asserts Jinja,
JSON-parser and regex messages only; `tests/test_examples_parse.py:26`,
`tests/test_dump.py:45,76`, `tests/test_ast_utils.py:73` and
`tests/test_type_checking.py:347` match on the `PDLParseError` *type*, which the new
subclasses satisfy; `tests/test_linter.py:85,701` construct `PDLParseError` with a plain
string, which the `.text` property must keep tolerating. `tests/test_line_table.py` does
not touch this path.

**The seven goldens** are regenerated and the seven `hygiene_traceback_expected: true`
flags deleted in the same commit (RUBRIC, "Two hygiene sub-flags").

**Determinism.** Two of the new messages read the filesystem (the near-miss and the
directory listing). Both use `sorted()` and both cap their output; no `set` iteration
reaches the text, so `PYTHONHASHSEED` cannot move it.

**Adjacent, one line, and it silently voids this item under one entry point:**
`pdl.py:340-341` is `if __name__ == "__main__": main()` with no `sys.exit`, so
`python -m pdl.pdl` returns 0 for every one of these failures (E-CLI-005, already pinned
in the corpus). It is not in this spec's seven, but a reviewer testing this work with
`python -m` will see exit 0 and conclude the fix does not work. Fix it in the same series.

---

**Expected rubric delta:** 13/105 → **94/105** across the seven
(E-CLI-001 1→11, E-CLI-002 1→12, E-CLI-003 1→14, E-CLI-004 3→13, E-PARSE-001 3→15,
E-PARSE-002 3→15, E-PARSE-005 1→14), plus E-RUNTIME-001 and E-LINT-002 losing their
tracebacks as a side effect. Seven of the corpus's fourteen S0 traceback entries close
here, and Location reaches 3 for the first time anywhere in the corpus — before item 0,
because PyYAML had already done the work.

**One sentence a user takes away:** "It told me which of my two inputs was broken, pointed
at the character, and said what to type instead."

---

## Addendum — verified exception-layout findings

Added by the orchestrator after running the checks the Risk section called for.
These correct the proposal above; implementation is held pending a human
decision on the one genuine break.

### The `OSError` proposal was worse than necessary

`PDLSourceError(PDLParseError, OSError)` silently breaks `except
FileNotFoundError`, as the Risk section noted. It is avoidable: inheriting the
**specific** errno subclass works, and CPython accepts the layout.

```python
class PDLFileNotFoundError(PDLParseError, FileNotFoundError): ...
class PDLIsADirectoryError(PDLParseError, IsADirectoryError): ...
```

Verified by construction and `isinstance`:

| Shim | `FileNotFoundError` | `IsADirectoryError` | `OSError` | `PDLParseError` |
| --- | --- | --- | --- | --- |
| `PDLFileNotFoundError` | yes | no | yes | yes |
| `PDLIsADirectoryError` | no | yes | yes | yes |

Every existing SDK `except` clause keeps matching, and callers additionally gain
`.message`. This is **purely additive** — no release note needed, and no
stop-and-report, because nothing breaks. Cost is one small class per concrete
errno rather than one shared class.

### The YAML case is also zero-breakage

`class PDLYamlError(PDLParseError, yaml.YAMLError)` — layout accepted,
construction works, `except yaml.YAMLError` and `except PDLParseError` both
match. Note it is **not** a `MarkedYAMLError`, so any caller narrow enough to
catch that specifically would break; that is a far less common clause than
`yaml.YAMLError`.

### `UnicodeDecodeError` cannot be shimmed — this is the real decision

The class layout is accepted, but construction is not: `UnicodeDecodeError`
requires exactly five arguments, and neither a one-arg call nor
`__new__` + explicit `__init__` satisfies it.

```
TypeError: function takes exactly 5 arguments (1 given)
```

So E-PARSE-005 cannot both carry a PDL message and remain catchable as
`UnicodeDecodeError`. The options are genuinely exclusive:

1. **Raise `PDLParseError`.** `except UnicodeDecodeError` around `exec_file`
   stops matching. Rare in practice, and the diagnostic gain is the largest in
   the group — a real line, column, excerpt and caret recomputed from
   `read_bytes`. Needs a release note.
2. **Re-raise the original after formatting at the CLI.** Zero breakage, but the
   CLI and SDK paths diverge: a library caller receives none of the new
   information.
3. **Chain**: raise `PDLParseError from exc`. `isinstance` is unaffected by
   chaining, so `except UnicodeDecodeError` still stops matching; only forensic
   value is preserved.

**Recommendation:** per-errno shims and the YAML shim for the other six entries,
which break nothing and need no approval; option 1 for `UnicodeDecodeError`,
with a release note, as the single documented SDK change in this item.

**Status: not implemented.** The six non-breaking entries are unblocked; the
decode entry awaits a decision.

---

## Correction — the addendum's "purely additive" claim was wrong

`regression-guard` failed the first implementation of this item and was right to.
The addendum above concluded that per-errno shims were "purely additive — no
release note needed, and no stop-and-report, because nothing breaks". Matching
the class is necessary but not sufficient, and two things did break:

1. **The `OSError` payload was dropped.** `OSError.__init__` never runs with the
   original arguments when a shim is constructed, so `errno`, `strerror`,
   `filename` and `filename2` all read `None`. A caller doing
   `except OSError as e: log(e.filename)` or branching on `e.errno` around
   `exec_file` silently got nothing. Fixed by copying the payload onto the shim
   in `source_read_error`.
2. **`str(exc)` rendered a Python list repr.** `PDLParseError.message` is a
   `list[str]`, so the inherited `__str__` produced a bracketed, quoted,
   `\n`-escaped list — the same defect `.text` was introduced to fix at the CLI
   sites, left in place on the library path. An embedder calling `print(exc)` or
   `logging.exception(...)` hit it directly. Fixed with a `__str__` on
   `PDLLocatedParseError`.

Neither was visible to any test: `test_shims_keep_every_except_clause_matching`
checks `__mro__` only. Both are now pinned by
`test_shims_preserve_the_oserror_payload` and
`test_shimmed_exceptions_stringify_as_prose`.

**The honest conclusion**, replacing the addendum's: the shims are additive *in
type*, and after the two fixes above they are additive *in payload and rendering*
too. One narrowing remains and belongs in a release note — `except
yaml.MarkedYAMLError` around `exec_file` stops matching, because `PDLYamlError`
derives from `yaml.YAMLError` rather than its marked subclass. That is rarer than
`except yaml.YAMLError`, but it is real.

The general lesson for the rest of the project: "does `except X` still match" is
one question, and "is the caught object still a usable X" is a second one. Ask
both.

## Correction — why the last-resort handler is still missing

The implementation notes recorded that a last-resort `except Exception` in `main`
was skipped because it would swallow the `UnicodeDecodeError` that E-PARSE-005
must keep leaking. That reasoning is wrong for the code that was actually
written. It would hold for the spec's Tier 2, which wraps everything through the
`generate(...)` call — but the implemented `try` covers only `load_initial_scope`
and `validate_scope`, and `generate` sits outside it. A last-resort clause on
that block could not have reached E-PARSE-005 at all.

What its absence actually leaves is a Tier-2 input that is neither a
`PDLException` nor a corpus entry still dumping a raw traceback — for example an
empty `-f` file, which fails on `dict | None`. Unchanged from before this item,
so not a regression, but decision §5.8 is not satisfied for that block and the
reason it was deferred should be recorded accurately: it needs its own commit and
its own corpus entries, not that it was impossible here.
