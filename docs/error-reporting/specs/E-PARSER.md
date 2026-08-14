# E-PARSER-001…006 — output parsers (`parser:`)

Phase-3 item 9. One spec for the whole series, because the six share a raise site, a
missing location, and — after this change — a single shape. Written to stand next to
[`E-CODE-001.md`](E-CODE-001.md) and [`E-CODE-002.md`](E-CODE-002.md) without looking like
a different tool wrote it: same header, same one-location-prefix rule, same
rule/`note:`/`help:` vocabulary, same `append(loc, …)` location, and the same
`<label>:N |` gutter for a coordinate system that is not the `.pdl` file.

> **Citations point at `c3be8d9`**, the tree this spec was written against — not at the
> current tree. Read one with `git show c3be8d9:src/pdl/pdl_interpreter.py`. Symbol names
> survive; line numbers do not.

> **I had no shell for this session.** Everything below is read from the source and from
> the committed goldens. Every claim that needed execution is listed under
> [Unverified, with the exact commands](#unverified-with-the-exact-commands), including
> **every `help:` line**. None of them may ship unrun.

---

## The shared defect

All six diagnostics are raised from `parse_result` (`pdl_interpreter.py:4060-4161`) with
**no `loc`**, so `generate` takes the `exc.loc is None` branch (`:264-265`) and prints a
bare sentence: no file, no line, no block path. All six score **Location 0**. None shows
the offending text (issue #387). Four of the six interpolate `repr(exc)`.

**`parse_result` has exactly one caller** — `pdl_interpreter.py:715`,
`partial(parse_result, block.parser)`, applied through `lazy_apply` at `:716`. `loc` is in
scope there (parameter of `process_advance_block_retry`, `:614`), and four lines below,
`result_with_type_checking` is already given `loc=append(loc, "spec")` (`:719-727`). That
is the pattern this change copies, and it must be copied rather than replaced by a
`try/except` at the call site: the parser runs **lazily**, so the exception surfaces at
`future_result.result()` in `generate` (`:246`), not inside `process_advance_block_retry`.
The location has to travel *into* the callable.

**Confirming the coordinator's reading of §7.9.** `append(loc, "parser")` resolves the
`parser:` key: line 4 in E-PARSER-001 and -002, line 2 in -003, -004 and -006, line 2 in
-005. INVENTORY §7.9's criterion is that *a location is accurate when the mark resolved is
the mark of the construct the message is about*. The message is about the parser — "`parser:
json` could not read this output" — and `parser:` is also the one construct the user can
edit to make it stop. So it is accurate, not coarse, and the series moves **Location 0 → 2**
(accurate `file:line` **plus** the block path). Pointing at the block that *produced* the
output was considered and rejected below.

Two entries do better, because their fault is entirely static and lives at a deeper key:
E-PARSER-005 points at `parser.regex` (line 3) and E-PARSER-006 at `parser.spec.second`
(line 5). `_walk` records every nested mapping key with a scalar name
(`pdl_location_utils.py:169-195`), so both marks exist.

---

## Today

Verbatim from the goldens, all six at `c3be8d9`. Exit code and stdout are identical in
every case (`$ exit: 1`, stdout `(empty)`) and are elided after the first.

**E-PARSER-001** (`tests/errors/corpus/E-PARSER-001/expected.txt`)

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Attempted to parse ill-formed JSON: TypeError("'int' object is not subscriptable")
```

Rubric: L0 W1 Y1 F0 H2 = 4/15

**E-PARSER-002**

```
Attempted to parse ill-formed JSON: JSONDecodeError('Expecting value: line 1 column 1 (char 0)')
```

Rubric: L0 W1 Y1 F0 H2 = 4/15

**E-PARSER-003**

```
Attempted to parse ill-formed YAML: ParserError('while parsing a flow sequence', <yaml.error.Mark object at 0xADDR>, "expected ',' or ']', but got '<stream end>'", <yaml.error.Mark object at 0xADDR>)
```

Rubric: L0 W1 Y1 F0 H1 = 3/15 (`0xADDR` is the harness normalizer; the real output differs
on every run)

**E-PARSER-004**

```
Attempted to parse ill-formed CSV: Error('field larger than field limit (131072)')
```

Rubric: L0 W1 Y1 F0 H2 = 4/15

**E-PARSER-005**

```
Fail to parse with regex (: error('missing ), unterminated subpattern at position 0')
```

Rubric: L0 W2 Y2 F1 H2 = 7/15

**E-PARSER-006**

```
No group named second found by (?P<first>\w+) in hello
```

Rubric: L0 W2 Y2 F0 H3 = 7/15

**Series total: 29/90.**

---

## Target

### E-PARSER-001 — `parser: json` on a value that is not text

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:4 - `parser: json` needs text, but this block produced an integer
  in parser

  A `parser:` reads the text a block produced. This block's result is the
  integer `3`, which is not text, so there is nothing to parse.

  help: remove `parser: json`; the block's result is already an integer.
```

Rubric: L2 W3 Y3 F3 H3 = **14/15**

The reproducer is `lang: python` / `code: result = 3` / `parser: json`, and the value
handed to `parse_result` is the Python `int` 3 — which is why today's message is a
`TypeError` about subscripting rather than anything about JSON. The corrected diagnosis is
therefore not "ill-formed JSON" at all; it is that a `parser:` was applied to a value that
is already structured. The type is named in **PDL's** vocabulary — `null`, `boolean`,
`string`, `number`, `integer`, `array`, `object`, the names `spec:` uses
(`pdl_ast.py:254-260`) — not Python's.

The value is shown inline only when `json.dumps` of it is **40 characters or fewer**;
beyond that the sentence names the type alone. That wall is the whole reason E-PARSER-006's
`in hello` defect cannot recur here.

> **This branch is where issue #387's own reproducer actually lands, and it is not a
> parse failure.** See [Flagged, not designed](#flagged-not-designed) #3: `json_repair`
> *repairs* rather than raises, so `parser: json` over genuine prose returns a wrong value
> at exit 0. The design below still covers the string branch — if `json_repair` does raise,
> the headline is `` `parser: json` could not parse the block's output `` with an
> `output:N` excerpt — but the branch the corpus pins is the non-text one.

### E-PARSER-002 — `parser: jsonl` on a line that is not JSON

```
prog.pdl:4 - `parser: jsonl` could not parse line 2 of the block's output
  in parser

output:2 | oops
         | ^ Expecting value

  `parser: jsonl` reads the block's output as one JSON value per line; every
  non-empty line must be a complete JSON value on its own.

  note: `output:N` counts lines of the block's output, not of the PDL file.
  help: make every non-empty line a complete JSON value, or remove the parser
        to keep the output as text.
```

Rubric: L2 W3 Y3 F2 H3 = **13/15**

**The confidently-wrong `line 1 column 1` is corrected, not dropped.** It reads `line 1`
today because each line is loaded as its own document (`:4080-4083`), so
`JSONDecodeError.lineno` is always 1. The real line number is the loop index, which the
code already has and throws away; the real column is `exc.colno`, which is correct
*within* that line. Reporting `output:2` with a caret at `exc.colno` is exact, and it is
strictly better than dropping the position: RUBRIC ranks a wrong position below none, not
below a right one.

The caret label is `exc.msg` verbatim — `Expecting value`, `Expecting ',' delimiter`,
`Unterminated string starting at`. That is the JSON parser's vocabulary about JSON, which
is the format the *user* asked for; it is not an internal leak, and no case transformation
is applied so there is no rule to get wrong.

**One extra branch, worth its `help:`.** When the *whole* output parses with `json.loads`,
the user wrote `jsonl` for a single pretty-printed document, and the suggestion is exact:

```
  help: this output is one JSON document, not one per line; use `parser: json`.
```

One extra `json.loads` on the failure path only; `json` is already imported (`:36`).

### E-PARSER-003 — `parser: yaml` on ill-formed YAML

```
prog.pdl:2 - `parser: yaml` could not parse the block's output
  in parser

output:1 | [1, 2
         |      ^ expected ',' or ']', but got '<stream end>'

  `parser: yaml` reads the block's output as a single YAML document.

  note: `output:N` counts lines of the block's output, not of the PDL file.
  help: check the syntax at line 1 of the block's output.
```

Rubric: L2 W3 Y3 F1 H3 = **12/15**

`repr(exc)` is what discards PyYAML's position; `str(exc)` would recover it, and the
coordinator is right that switching is nearly free. But `str(exc)` renders
`in "<unicode string>", line 1, column 6:` — a **file that does not exist**, naming a
string PyYAML was handed. So the marks are read *directly* (`exc.problem`,
`exc.problem_mark.line/column`, `exc.context`, `exc.context_mark`) exactly as
`yaml_diagnostic` already does for `.pdl` files (`pdl_diagnostics.py:1030-1033`), and the
position is rendered in the `output:N` gutter, which says whose line 1 it is. PyYAML's own
snippet renderer is never used.

**F stays at 1 for this reproducer, and that is the honest score.** `_recognize`
(`pdl_diagnostics.py:863-975`) has no branch for `expected ',' or ']'`, so this falls to
its generic arm: PyYAML's problem string as the caret label and "check the syntax at line
N" as the action. The `'<stream end>'` token is the one wart left on the page. Adding a
flow-collection branch to `_recognize` would fix both this entry and file-level
E-PARSE-001 — it is listed under [Adjacent work](#adjacent-work-not-in-this-item) rather
than folded in, because I cannot run PyYAML to pin the exact `problem`/`context` strings
and a `help:` built on an unverified string is exactly what this project has caught six
times.

**Reuse, not a second dialect.** `_recognize`'s `help:` texts say "line {N}", which here
would mean a line of the *output* while reading as a line of the file. It gains one
parameter, `line_phrase`, defaulting to `lambda n: f"line {n}"` — every existing golden is
byte-identical — and the parser flavour passes `lambda n: f"line {n} of the block's
output"`. One table of YAML wordings, two callers.

### E-PARSER-004 — `parser: csv` over the field-size limit

```
prog.pdl:2 - `parser: csv` cannot read a field longer than 131072 characters
  in parser

output:1 | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ...

  `parser: csv` reads the block's output as comma-separated rows. Python's
  `csv` module refuses any single field longer than 131072 characters. This is
  a size limit, not a syntax error.

  note: the block's output is 131073 characters with no `,` and no line break,
        so it is a single field.
  note: `output:N` counts lines of the block's output, not of the PDL file.
  help: if this output is not CSV, remove `parser: csv`.
```

Rubric: L2 W3 Y3 F2 H3 = **13/15**

The taxonomy row was wrong and is corrected in the corpus notes: malformed CSV does not
raise, so `csv.field_size_limit()` is the only way into this `except`. Calling a
well-formed 131073-character field "ill-formed CSV" misdiagnoses a resource limit as a
syntax error, and the headline is rewritten to say what actually happened.

**No caret.** There is no column in a `csv.Error`; `reader.line_num` gives the row and
nothing gives the offset. A caret at column 1 would be a coordinate PDL invented, so the
excerpt row is printed with `col=None` and `_excerpt` emits no annotation line
(`pdl_diagnostics.py:269`). The first `note:` is emitted **only** when the output really
contains no `,` and no line break — then "it is a single field" is a fact, not a guess.
Otherwise that note is replaced by `` note: the failure is in row N of the block's
output. `` from `reader.line_num`, and the excerpt row is that row.

The excerpt is 62 characters of `a` followed by ` ... `: see
[Showing the offending text](#showing-the-offending-text-issue-387) for the arithmetic.

### E-PARSER-005 — an invalid regular expression

```
prog.pdl:3 - `regex:` is not a valid regular expression
  in parser.regex

regex:1 | (
        | ^ missing ), unterminated subpattern

  The `regex:` of a parser is a Python regular expression. It is compiled
  before the block's output is read, so the fault is in the pattern, not in
  the output.

  note: `regex:N` counts lines of the pattern, not of the PDL file.
  help: close the group, or write `regex: '\('` to match a literal `(`.
```

Rubric: L2 W3 Y3 F3 H3 = **14/15**

The location is `parser.regex` — line 3, the pattern itself, not the `parser:` key above
it. `re.error` carries `.msg`, `.pos`, `.lineno`, `.colno` and `.pattern`, so the caret
lands on the offending character of the pattern; today's `at position 0` is the same
information with no way to see what is at position 0.

**Why a `regex:N` gutter and not a caret on file line 3.** The mark recorded for
`["parser","regex"]` is the **key**'s start plus the **value**'s end (`_walk`,
`pdl_location_utils.py:186-194`), so PDL knows where `regex` begins but not where `(`
begins inside the quoted scalar. Locating the pattern's character 0 in the file would mean
re-scanning the line for the pattern text, through YAML's quoting — a heuristic, and the
kind that E-CODE-001 rejected. The pattern's own coordinates are exact and are the ones
`re` reports, so they are the ones shown, in a gutter that says so. Recording the value
node's start mark in `_walk` would remove the obstacle for every scalar in PDL at once;
that is flagged in [Adjacent work](#adjacent-work-not-in-this-item), not done here.

**The `help:` is branch-specific and the YAML quoting is the point.** It fires only for
`missing )` / `unbalanced parenthesis`. `` regex: "\(" `` would be wrong — `\(` is not a
valid escape in a double-quoted YAML scalar, so following it trades a regex error for a
YAML error, which is the trap `E-PARSE-001` fell into. In a **single**-quoted YAML scalar
the backslash is literal, so `'\('` reaches `re` as `\(`. Every other `re.error` message
gets `help: check the pattern at position N.` and F1.

### E-PARSER-006 — `spec:` names a group the pattern does not define

```
prog.pdl:5 - the `regex:` pattern has no group named `second`
  in parser.spec.second

5 |     second: str
  |     ^

  For a `regex:` parser, each key of `spec:` names a capture group to take
  from the match. The pattern `(?P<first>\w+)` defines one group, `first`, so
  no output could have supplied `second`.

  help: rename the key to `first`, the only group this pattern defines.
```

Rubric: L2 W3 Y3 F3 H3 = **14/15**

Both of today's defects are corrected. `in hello` blamed the matched *text*; the sentence
now says the pattern could never have supplied the group whatever the text was. And the
group the pattern **does** define is named, which is the whole of the fix — `m.re.groupindex`
is a `dict` on the compiled pattern and `m` is in scope at the raise site (`:4143-4147`),
so the list is reachable without touching the AST.

**This is the one entry with a *file* excerpt and no output excerpt**, and both halves are
deliberate. Nothing about the block's output is relevant — the error is static — so
printing it would repeat today's mistake. The offending construct *is* in the file, at
`parser.spec.second`, and `PdlLocationType.col` has been populated since Phase-3 item 0
(INVENTORY §7.6), so a bare `5 |` gutter with a caret is honest file-relative evidence. The
convention holds throughout the series: **a bare `N |` gutter means file lines; a
`<label>:N |` gutter means some other text.**

Three `help:` branches, chosen by what the pattern defines:

| Pattern defines | `help:` |
| --- | --- |
| exactly one named group | ``rename the key to `first`, the only group this pattern defines.`` |
| several named groups, one a near miss | ``did you mean `first`?`` |
| several, no near miss | ``use one of the groups the pattern defines: `a`, `b`, `c`.`` (sorted by group number, capped at five) |
| no named group | ``name the group in the pattern, e.g. `(?P<second>…)`, or remove `spec:` to get the groups as a list.`` |

The near-miss branch is `difflib.get_close_matches(name, groups, n=1, cutoff=0.7)` over an
**ordered** list, never a `set`, so it cannot move with `PYTHONHASHSEED` — E-CODE-002's
argument, unchanged. It does **not** fire for this reproducer: `second` against `first`
scores well under 0.7, which is why the golden shows the "only group" branch. That is worth
stating because it is the sort of thing a spec gets wrong by assuming. The last row's
"remove `spec:`" claim is read from `:4148-4149`, where a `RegexParser` with no dict `spec`
returns `list(m.groups())`.

**Series total: 80/90**, from 29/90.

---

## The shared shape

One diagnostic, one location prefix, four parts:

```
<file>:<line> - <headline>
  in <block path>

<evidence: at most two excerpt rows, each with an optional caret line>

  <rule paragraph: what a `parser:` is and which rule this broke>

  note: <branch fact, if any>
  note: `<gutter>:N` counts lines of …, not of the PDL file.   <- only if a gutter printed
  help: <the action>
```

Rendered by `pdl_diagnostics.render` (`:151-174`) into a body, which travels on
`PDLRuntimeParserError.message` and is printed by `generate` through
`located_message(exc.loc, exc.message)` (`pdl_interpreter.py:267`). `located_message`
inserts the `  in <path>` line after the **first** line of the message
(`pdl_location_utils.py:463-468`), which is exactly the shape above; E-CODE-001 already
relies on this.

Note ordering is E-CODE-001's, unchanged: the branch's own fact first, the gutter caveat
last among notes, the `help:` last of all. The caveat is emitted **only** when a
`<label>:N` gutter was printed, so E-PARSER-001 (no excerpt) and E-PARSER-006 (file
excerpt) do not carry it.

### Showing the offending text (issue #387)

The text is a runtime value — usually a model's output — so it is unbounded, may contain
newlines, control characters and ANSI escapes, and may not be text at all. The rules,
precisely:

1. **Delimiting.** Every line of the value is inside a gutter row: `output:N | `. Nothing
   of the program's data is ever adjacent to PDL's prose, and the label says which text it
   is. A bare `N |` row would read as a `.pdl` line — the confidently-wrong location the
   rubric ranks below saying nothing, and the reason E-CODE-001 invented `code:N`.
2. **How much.** At most **one** row for these diagnostics (`_excerpt` already caps at two
   spans, `pdl_diagnostics.py:257`). Never the whole value, never more than the failing
   line.
3. **Truncation.** `_clip` (`:224-240`) windows the row around the caret, keeping the
   caret visible, with ` ... ` on whichever side was cut. Its budget is `EXCERPT_MAX` (75)
   today; when a gutter label is set it becomes `WIDTH - len(prefix)` **including** the
   markers, so no row exceeds 78 columns. For `output:1 | ` that is `78 - 11 = 67`, i.e.
   62 characters of value plus ` ... ` — the E-PARSER-004 row above.
4. **Control characters.** Every C0/C1 character other than the `\n` used to split renders
   as a **single space**, which is what the module already does for tabs
   (`pdl_diagnostics.py:267`, and the contract in its docstring, `:20-22`). One character
   in, one column out, so caret arithmetic stays trivial and no ANSI escape can reach a
   terminal. When the character *at* the caret is one of them, the label names it in words
   (`^ carriage return here`), which is the same rule the docstring states for tabs.
5. **Empty output.** No row at all — an `output:1 | ` row with nothing after it looks like
   a rendering bug. Instead: `` note: the block's output was empty. ``
6. **Not text.** No row, and a different diagnostic: the E-PARSER-001 shape, which names
   the PDL type and shows the value only if `json.dumps` of it is ≤ 40 characters.

Reuse, not invention: `_excerpt`, `_clip` and `_wrap` do all of the above. What they gain
is one field (`gutter`) and one narrowed budget.

---

## Structured record

Decision 5.6. E-PARSER-002, the richest of the six:

```json
{
  "id": "E-PARSER-002",
  "severity": "error",
  "origin": "program",
  "file": "prog.pdl",
  "span": {"line": 4, "col": null, "end_line": null, "end_col": null},
  "block_path": ["parser"],
  "message": "`parser: jsonl` could not parse line 2 of the block's output",
  "notes": [
    {"kind": "rule",
     "text": "`parser: jsonl` reads the block's output as one JSON value per line; every non-empty line must be a complete JSON value on its own."},
    {"kind": "note",
     "text": "`output:N` counts lines of the block's output, not of the PDL file."}
  ],
  "suggestions": [
    {"text": "make every non-empty line a complete JSON value, or remove the parser to keep the output as text.", "replacement": null}
  ],
  "gutter": "output",
  "source": "{\"a\": 1}\noops\n",
  "spans": [{"line": 2, "col": 1, "label": "Expecting value", "primary": true}]
}
```

The other five differ only in their field values:

| Entry | `block_path` | `span.line` | `gutter` | `source` | excerpt span |
| --- | --- | --- | --- | --- | --- |
| 001 | `["parser"]` | 4 | — | — | none |
| 002 | `["parser"]` | 4 | `output` | block output | line 2, col `exc.colno` |
| 003 | `["parser"]` | 2 | `output` | block output | `problem_mark` +1/+1 |
| 004 | `["parser"]` | 2 | `output` | block output | `reader.line_num`, col `null` |
| 005 | `["parser","regex"]` | 3 | `regex` | `parser.regex` | `exc.lineno`, `exc.colno` |
| 006 | `["parser","spec","second"]` | 5 | — (file) | `source_text(loc.file)` | `loc.line`, `loc.col` |

**Two fields are not on `pdl_diagnostics.Diagnostic` today.** `span`/`spans` and `source`
exist (`:95`, `:99`); `gutter` is new, **additive, defaulting to `""`**, and consumed by
nothing else, so no existing diagnostic changes shape. It is the same problem E-CODE-001's
`frames` field solves — spans in a coordinate system that is not the file — solved once for
a single-row case; the two compose rather than compete, and `gutter` is the field that
should absorb `frames`'s row prefix if anyone unifies them later.

Pre-renderer, as with E-CODE-001 and E-CODE-002, the body is a pre-rendered string
travelling on the exception; the record is the contract for when 5.6's renderer absorbs it.

---

## Where the data comes from

Raise sites, all in `parse_result` (`pdl_interpreter.py:4060-4161`):
json `:4072-4076`, jsonl `:4086-4090`, yaml `:4096-4100`, csv `:4109-4113`,
regex compile `:4131-4133`, missing group `:4145-4147`. Call site `:715`. Printed at
`:256-271`.

| Field | Example | Source at `c3be8d9` | Available today? |
| --- | --- | --- | --- |
| `file` | `prog.pdl` | `loc.file`, set by `parse_file` and rendered by `get_loc_string` (`pdl_location_utils.py:412-431`) | yes |
| `span.line` | `4` | `append(loc, "parser")` (`pdl_location_utils.py:397-409`), from the mark `_walk` recorded for the `parser:` key (`:182-195`) | yes — but `loc` **is not passed to `parse_result` today**; that is the change |
| `span.col` | `null` | populated on the location (§7.6) but not rendered by `get_loc_string`, deliberately | value yes, rendering no |
| `block_path` | `["parser"]` | `loc.path` after the same `append`; rendered by `located_message` (`:434-468`) | yes |
| deeper paths | `["parser","spec","second"]` | `append` twice more; `_walk` records every nested scalar key | yes — **guard required**, see below |
| offending text | `{"a": 1}\noops\n` | the `text` parameter of `parse_result` (`:4060`) | yes |
| PDL type of a non-text value | `integer` | `type(text)` mapped to the `spec:` names at `pdl_ast.py:254-260` | yes |
| jsonl failing line | `2` | the loop at `:4080-4083` — `enumerate` the `split("\n")` it already performs | yes, currently discarded |
| jsonl column + detail | `1`, `Expecting value` | `JSONDecodeError.colno`, `.msg` | yes |
| "is the whole output one JSON document?" | bool | one `json.loads(text)` on the failure path; `json` imported `:36` | yes |
| yaml position + problem | `1`, `6`, `expected ',' …` | `exc.problem`, `exc.problem_mark.line/column`, `exc.context_mark`; same fields `yaml_diagnostic` reads (`pdl_diagnostics.py:1030-1033`) | yes |
| csv limit | `131072` | `csv.field_size_limit()`; `csv` imported `:20` | yes |
| csv row | `1` | `reader.line_num` — `reader` is bound at `:4104` and still in scope in the `except` | yes (**verify V5**) |
| csv "single field" test | `,`/`\n` counts of `text` | the text itself | yes |
| regex position | `0`, `1`, `1` | `re.error.pos`, `.lineno`, `.colno`, `.msg` | yes |
| group names | `{"first": 1}` | `m.re.groupindex`, `m.re.groups`; `m` bound at `:4128` | yes |
| near miss | — | `difflib.get_close_matches`, already imported in `pdl_diagnostics` (`:33`) | yes |
| file excerpt (006) | `    second: str` | `source_text(loc.file)` (`pdl_location_utils.py:377`) | yes — `None` for a contested source, which degrades to no excerpt |
| `id` | `E-PARSER-002` | `Diagnostic.code`; carried, not rendered — no registry exists (`pdl_diagnostics.py:88-90`) | carried only |

### What has to change at the raise site

1. **`pdl_interpreter.py:715`** — `partial(parse_result, block.parser, loc=append(loc, "parser"))`,
   mirroring `:719-727`. `parse_result` gains a keyword-only `loc: PdlLocationType | None = None`
   (default keeps every existing caller and any SDK import working) and passes it to all six
   `PDLRuntimeParserError(...)` constructions. Nothing else in the file changes shape.
2. **A non-text pre-check**, at the top of `parse_result` for `json`/`jsonl`/`yaml`/`csv`
   and after the pattern is compiled for `regex`: `not isinstance(text, (str, bytes))`
   raises the E-PARSER-001 diagnostic. `bytes` is exempt on purpose — `yaml.safe_load`
   accepts it today, and turning a working program into an error is a semantic change this
   item is not allowed to make.
3. **`re.compile(parser.regex)` becomes an explicit step** before the match, so
   E-PARSER-005's diagnostic is raised for the pattern and E-PARSER-001's for the input,
   in that order. `re` caches compiled patterns, so the cost is nil.
4. **A path guard.** `append` carries the parent's line on a mark miss
   (`pdl_location_utils.py:407-408`) but still extends `loc.path`, which would render
   `  in parser.spec.second` for a program whose source has no such key — the E-TYPE-003
   defect from §7.9. A helper (`append_if_marked`, using `SOURCES.mark`, `:348-350`)
   descends only where a mark exists and otherwise stays put. Needed because `spec:` may be
   written as `spec: {object: {…}}`, in which case `parser.spec.second` is not in the file.
5. **`pdl_diagnostics`**: `Diagnostic.gutter` (additive, `""`); `_clip`'s budget narrowed
   only when a gutter is set; control-character folding on gutter excerpts; `_recognize`
   gains `line_phrase` with a golden-preserving default; six small builders
   (`parser_*_diagnostic`). The module still imports nothing from PDL.

### Nothing here needs data the interpreter does not have

The one thing that does not exist is the file column of a **value** scalar — where `(`
sits inside `regex: "("`. That is why E-PARSER-005 uses a `regex:N` gutter rather than a
file caret. Everything else above is already in scope at the raise site or one `append`
away.

---

## Unverified, with the exact commands

I have no shell. These must all be run before implementation, and **V8 is not optional**:
every `help:` in this spec is a claim about what happens when a user follows it.

**V1 — the locations.** Expect `["parser"]` at line 4 / 4 / 2 / 2 / 2 / 2 for 001…006, plus
`["parser","regex"]` at line 3 col 3 for 005, and `["parser","spec","second"]` at line 5
col 5 for 006.

```
python3 - <<'EOF'
from pathlib import Path
from pdl.pdl_location_utils import load_with_marks, path_key
cases = {"E-PARSER-001": [["parser"]], "E-PARSER-002": [["parser"]],
         "E-PARSER-003": [["parser"]], "E-PARSER-004": [["parser"]],
         "E-PARSER-005": [["parser"], ["parser", "regex"]],
         "E-PARSER-006": [["parser"], ["parser", "spec"], ["parser", "spec", "second"]]}
for entry, paths in cases.items():
    _, marks = load_with_marks(Path(f"tests/errors/corpus/{entry}/prog.pdl").read_text())
    for p in paths:
        print(entry, p, marks.get(path_key(p)))
EOF
```

**V2 — `json_repair` repairs rather than raises.** Expect no exception for any string (so
the `json` branch is unreachable from text, which is finding #3) and a `TypeError` for `3`.

```
python3 -c "
import json_repair
for s in ['oops', '', 'Sure! {\"a\": 1}', '[1, 2']:
    print(repr(s), '->', repr(json_repair.loads(s)))
try: json_repair.loads(3)
except Exception as e: print(repr(e))
"
```

**V3 — jsonl positions.** Expect `Expecting value 1 1 0`, and that the whole-text
`json.loads` of a pretty-printed document succeeds (the `use parser: json` branch).

```
python3 -c "
import json
try: json.loads('oops')
except json.JSONDecodeError as e: print(e.msg, e.lineno, e.colno, e.pos)
print(json.loads('{\n  \"a\": 1\n}'))
"
```

**V4 — PyYAML's marks for `[1, 2`.** Expect problem `expected ',' or ']', but got
'<stream end>'` at line 1 column 6, context `while parsing a flow sequence` at 1/1. The
context strings also decide whether the `_recognize` branch in
[Adjacent work](#adjacent-work-not-in-this-item) is worth writing.

```
python3 -c "
import yaml
try: yaml.safe_load('[1, 2')
except yaml.MarkedYAMLError as e:
    print(repr(e.problem), e.problem_mark.line + 1, e.problem_mark.column + 1)
    print(repr(e.context), e.context_mark.line + 1, e.context_mark.column + 1)
    print('---'); print(str(e))
"
```

**V5 — the csv limit and `reader.line_num`.** Expect `131072`, the `field larger than
field limit` error, and a usable `line_num`.

```
python3 -c "
import csv
from io import StringIO
print(csv.field_size_limit())
r = csv.reader(StringIO('a' * 131073))
try:
    for row in r: pass
except csv.Error as e: print(repr(str(e)), r.line_num)
"
```

**V6 — `re.error` attributes.** Expect `'missing ), unterminated subpattern' 0 1 1 '('`.

```
python3 -c "
import re
try: re.compile('(')
except re.error as e: print(repr(e.msg), e.pos, e.lineno, e.colno, repr(e.pattern))
"
```

**V7 — group names and the near miss.** Expect `{'first': 1} 1`, then `[]` — confirming
that E-PARSER-006's golden takes the "only group" branch and **not** `did you mean`.

```
python3 -c "
import re, difflib
m = re.fullmatch(r'(?P<first>\w+)', 'hello')
print(dict(m.re.groupindex), m.re.groups)
print(difflib.get_close_matches('second', list(m.re.groupindex), n=1, cutoff=0.7))
try: m.group('second')
except IndexError as e: print(repr(e))
"
```

**V8 — every `help:`, executed.** All nine must exit 0. The files are written from Python
so that no shell quoting can corrupt a backslash — the failure mode that produced two of
this project's six caught-before-shipping wrong suggestions.

```
python3 - <<'EOF'
import pathlib, subprocess, tempfile
d = pathlib.Path(tempfile.mkdtemp())
progs = {
 # E-PARSER-001: "remove `parser: json`"
 "a.pdl": 'lang: python\ncode: |\n  result = 3\n',
 # E-PARSER-002, branch A: "use `parser: json`"
 "b.pdl": 'text: |\n  {\n    "a": 1\n  }\nparser: json\n',
 # E-PARSER-002, branch B first clause: every line a complete JSON value
 "c.pdl": 'text: |\n  {"a": 1}\n  "oops"\nparser: jsonl\n',
 # E-PARSER-002, branch B second clause: "remove the parser"
 "d.pdl": 'text: |\n  {"a": 1}\n  oops\n',
 # E-PARSER-004: "remove `parser: csv`"
 "e.pdl": "text: \"${ 'a' * 131073 }\"\n",
 # E-PARSER-005: "close the group"
 "f.pdl": 'text: "Hello"\nparser:\n  regex: "(.*)"\n',
 # E-PARSER-005: "write `regex: '\\('`" -- the single quotes are the point
 "g.pdl": "text: \"(\"\nparser:\n  regex: '\\('\n",
 # E-PARSER-006: "rename the key to `first`"
 "h.pdl": 'text: "hello"\nparser:\n  regex: "(?P<first>\\\\w+)"\n  spec:\n    first: str\n',
 # E-PARSER-006, no-named-group branch: "remove `spec:`"
 "i.pdl": 'text: "hello"\nparser:\n  regex: "(\\\\w+)"\n',
}
for name, text in progs.items():
    (d / name).write_text(text)
    p = subprocess.run(["pdl", "--stream", "none", name], cwd=d,
                       capture_output=True, text=True)
    print(name, "exit", p.returncode, "|", p.stdout.strip()[:60], "|", p.stderr.strip()[:80])
EOF
```

`g.pdl` deserves a second look when it runs: `\(` fullmatches `(`, so it exits 0 with
`[]`, but on the *corpus* reproducer (`text: "Hello"`) the same pattern simply does not
match and PDL prints `null` at exit 0 — finding #2. The `help:` is phrased conditionally
("or write … to match a literal `(`") for that reason and must stay so.

**V9 — end to end.** After implementation, each of the six Target blocks must be
reproduced byte for byte by `pdl --stream none tests/errors/corpus/E-PARSER-00N/prog.pdl`,
including the wrap points. The line breaks written above are my hand-simulation of `_wrap`
at `WIDTH = 78`; where a break disagrees, `regen.py`'s output is authoritative and the
*wording* is what this spec pins.

---

## Rejected alternatives

**Point the location at the block that produced the output, not at `parser:`.** It is
arguably where the bad text came from — a `model:` call, a `code:` block. Rejected on
§7.9's criterion: the message is about the parser, the parser is the construct the user
edits to fix it, and the producing block may be many lines away or (for a model) not the
author's fault at all. `parser:` is also the *narrower* mark of the two, which is the
direction the rubric rewards.

**Switch `repr(exc)` to `str(exc)` for the yaml branch and stop there.** Two characters,
recovers PyYAML's line, column and caret, and removes the memory addresses — it would take
E-PARSER-003 from 3/15 to about 8/15 on its own. Rejected as the *whole* answer because
PyYAML's report says `in "<unicode string>", line 1, column 6`, naming a file that does not
exist, and because it would leave the other five untouched and the series in two dialects.
The marks it would have surfaced are read directly instead, so nothing is lost.

**Make the `csv` and `regex-no-match` silent failures errors while the file is open.**
Tempting — the code is right there and both are worse bugs than anything in this spec.
Rejected under §5.5: they change the exit code of programs that work today, they need the
owner's sign-off and a blast-radius measurement, and item 9 is a diagnostics item. Both are
described below and left.

**One combined `parser` diagnostic builder with a `kind` string.** Fewer functions, but the
branches share almost nothing: what the offending text is, whether a caret exists, and what
the action is all differ per parser. Six small builders with one shared shape reads better
than one function with a five-way `match` inside every clause.

---

## Risk

- **No new dependency.** `json`, `csv`, `re`, `yaml`, `difflib`, `textwrap` are all
  imported already (`pdl_interpreter.py:20,36,…`; `pdl_diagnostics.py:33-40`).
- **No AST change, no public API break.** `PDLRuntimeParserError` keeps its class and its
  constructor (`pdl_ast.py:1735`); `parse_result` gains a defaulted keyword-only parameter;
  `Diagnostic` gains a defaulted field. `PdlLocationType` is untouched.
- **Two message-asserting tests must change in the same commit.**
  `tests/test_runtime_errors.py:45-55` (`test_parser_jsonl`) and `:58-72`
  (`test_parser_regex`) assert the old strings verbatim. Both use `exec_str`, so their new
  headers read `<program>:3 - `. `tests/test_parser.py:51-53` asserts only
  `pytest.raises(PDLRuntimeError)` and survives — and it is a `spec:` failure, not a parser
  failure, despite its name.
- **All six goldens regenerate, and all six `case.json` rubric blocks change.** No
  hygiene sub-flags are set on any of them, so nothing XPASSes.
- **The message becomes multi-line, and it travels.** `PDLRuntimeError.message` reaches
  `ErrorBlock.msg`, the trace JSON and the viewer wherever an enclosing handler copies it.
  Already true since E-CODE-002; this widens the set of programs it happens for.
- **The retry path prints a traceback around it.** With `retry:` configured, a parser
  failure is forced inside the `try` (`:729`) and reported at `:815-824` as
  `An error occurred in a PDL block. Error details: {traceback.format_exc()}` — an existing
  leak that will now wrap a multi-line diagnostic. Not this item's to fix; recorded so that
  whoever meets it knows it predates this change.
- **No trace is written for a parser failure today** (`exc.pdl__trace is None`, `:269-270`)
  and this change does not add one. Passing `trace=ErrorBlock(...)` as
  `result_with_type_checking` does would change what `pdl --trace` writes for a failing
  program; deliberately not done here.
- **Exit code, stdout and the success path are untouched.** Still exit 1, still nothing on
  stdout. The `bytes` exemption in change (2) exists precisely so that no working program
  starts failing.

---

## Flagged, not designed

Five findings that are **not** part of item 9. The first two were named by the coordinator;
the last three were found while specifying and are new.

1. **Malformed CSV parses to nonsense at exit 0.** `csv.reader` accepts an unbalanced
   quote, ragged rows and embedded NULs; the coordinator reproduced
   `[["a","b","c"], ["unterminated,1\nx,y\n"]]`. Making it an error is a **semantic change**
   under §5.5 and needs the owner's sign-off.
2. **A `regex:` parser that does not match returns `None` silently.** `RegexParser.mode`
   defaults to `fullmatch` (`pdl_ast.py:354-357`), so a near-miss pattern prints `null` at
   exit 0 (`:4134-4135`). Same category as #1.
3. **`parser: json` on prose is a third silent failure — and it is the case issue #387
   names.** `json_repair` *repairs*: its parser returns `""` when it finds no value
   (`json_repair/json_parser.py:229-230`), so `parser: json` over a model's prose yields a
   wrong value at exit 0 rather than the error the taxonomy assumed. This is why the corpus
   reproducer had to use an `int`. Confirm with **V2**, then decide with the owner: it is
   the same §5.5 question as #1 and #2, and it is the largest of the three by blast radius.
4. **`parser: {pdl: …}` crashes with a traceback.** `case PdlParser(): assert False, "TODO"`
   (`:4114-4115`) is reachable from user YAML — `PdlParser` is a branch of `ParserType`
   (`pdl_ast.py:342-362`) — and `AssertionError` is neither a `PDLRuntimeError` nor a
   `PDLParseError`, so it escapes `generate`'s handlers (`:253-271`) as a raw traceback.
   Under `python -O` the assert vanishes and `result` is unbound instead. That is an S0 with
   no taxonomy row and no corpus entry; it wants **E-PARSER-007**.
5. **A parser's `spec:` types are never checked.** `Parser.spec` is documented as "Expected
   type of the parsed value" (`pdl_ast.py:337-339`), but its only use is as a list of regex
   group names (`:4136-4143`); `spec: {first: integer}` returns a string and nothing
   complains. A documentation or a validation bug, not a diagnostic one.

## Adjacent work, not in this item

- **A `_recognize` branch for unclosed flow collections** (`expected ',' or ']'` /
  `'}'`), which would give both E-PARSE-001 and E-PARSER-003 a headline like "a `[` is
  never closed", a context caret on the opening bracket, and `help: close the `[` opened on
  line 1`. Needs V4's exact strings first, so it is listed rather than specified.
- **Record the value node's start mark in `_walk`** (`pdl_location_utils.py:186-194`),
  which currently keeps the key's start and the value's end. It would let every diagnostic
  about the *contents* of a scalar — this series' `regex:`, and more besides — put a caret
  on a file line instead of in a private gutter. Additive to `SourceMark`; foundation work,
  not item 9.
- **Rendering `:col` in the header.** E-PARSER-005 and -006 are the first two entries whose
  excerpt and caret are ready for Location 3 and whose only missing piece is the column in
  the header line. §7.9 leaves that decision to whoever owns 5.6's renderer; when it lands,
  both go to **15/15** with no change to this design.

---

**Expected rubric delta:** the series moves **29/90 → 80/90** (001 4→14, 002 4→13, 003
3→12, 004 4→13, 005 7→14, 006 7→14). Every entry gains Location 2 from one `append`, and
Fix — the corpus's weakest dimension — goes from 1 point across six entries to 15.

**One sentence a user takes away:** "It told me which `parser:` failed, on which line of my
program, showed me the line of the block's own output that broke it with a caret on the
character, and told me what to write instead."
