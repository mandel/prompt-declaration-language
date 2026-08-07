# E-CODE-002 — `code:` block finishes without assigning `result`

Upstream: [#386](https://github.com/IBM/prompt-declaration-language/issues/386).
Phase-3 item 3. Pilot spec: the shape used here is the pattern for the rest of the
corpus.

## Today

```
$ exit: 1

--- stdout ---
hi

--- stderr ---
prog.pdl:0 - Python Code error: Traceback (most recent call last):
  File "<REPO>/src/pdl/pdl_interpreter.py", line 2508, in process_call_code
    result = call_python(code_s, execution_scope, state)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<REPO>/src/pdl/pdl_interpreter.py", line 2657, in call_python
    result = my_namespace.result
             ^^^^^^^^^^^^^^^^^^^
AttributeError: 'types.SimpleNamespace' object has no attribute 'result'
```

Rubric: L0 W0 Y0 F0 H0 = 0/15

The only entry in the corpus scoring zero on every dimension. The user's program is
three lines long; every line of the report is about PDL's internals. Nothing names the
`result` contract, and the location shown (`:0`) is the top-level-block defect.

## Target

The reproducer (`lang: python` / `code: |` / `print('hi')`):

```
$ exit: 1

--- stdout ---
hi

--- stderr ---
prog.pdl:2 - code block finished without assigning `result`

  A `code:` block's value is whatever its code assigns to the variable
  `result`. This block assigned nothing.

  note: `print(...)` writes to stdout; it does not set the block's value.
  help: assign the value instead of printing it:  result = 'hi'
```

Rubric: L1 W3 Y3 F3 H3 = 13/15

### The other branches of the same diagnostic

The headline, the rule sentence and the `help:` are computed, not fixed. One diagnostic,
five renderings. The second sentence of the rule paragraph is the *evidence* (what was
found); the `help:` is the *action*.

| What the code did | Evidence sentence | Suggestion |
| --- | --- | --- |
| assigned nothing; exactly one top-level `print(<expr>)` with one positional arg | `This block assigned nothing.` | `note:` print line + `help: assign the value instead of printing it:  result = <expr>` |
| assigned nothing; `print` used, but not a single simple call | `This block assigned nothing.` | `note:` print line + `help: a code block must end by assigning its value, e.g. \`result = ...\`` |
| assigned nothing; no `print` | `This block assigned nothing.` | `help: a code block must end by assigning its value, e.g. \`result = ...\`` |
| assigned one name `total` | `This block assigned \`total\`, but not \`result\`.` | `help: assign it to \`result\`:  result = total` |
| assigned several, one close to `result` (e.g. `resutl`, `Result`, `res`) | `This block assigned \`resutl\`, but not \`result\`.` | `help: did you mean to name it \`result\`?` |
| assigned several, none close | `This block assigned \`a\`, \`b\`, \`c\`, but not \`result\`.` (binding order, first 5, then `, and N more`) | `help: assign one of them to \`result\`:  result = c` (last bound) |

Weakest branch (assigned nothing, no `print`) scores F2 → 12/15. Every branch keeps
W3 Y3 H3.

### After Phase-3 item 0 (foundation) and item 7 (block paths)

Same record, richer rendering. Written out here so this spec is not redone:

```
prog.pdl:2:1 - code block finished without assigning `result`
  in `code`
  |
2 | code: |
3 |   print('hi')
  |
  A `code:` block's value is whatever its code assigns to the variable
  `result`. This block assigned nothing.

  note: `print(...)` writes to stdout; it does not set the block's value.
  help: assign the value instead of printing it:  result = 'hi'
```

Rubric: L3 W3 Y3 F3 H3 = 15/15

Three deltas, all from the foundation, none from this error ID: the column, the source
excerpt, and the `in <block path>` line (`text[2].code` for a nested block; a bare
`code` for this top-level one). The excerpt is capped — first line of the `code:` scalar
plus at most two more, then `...`. There is no caret span: a missing assignment has no
offending token, so rustc's convention applies and the span covers the whole `code:`
scalar rather than pointing inside it. The exact frame characters are item 0's to
choose; the content above is what this spec fixes.

## Structured record

Decision 5.6. Fields exactly as listed there; nothing added.

```json
{
  "id": "E-CODE-002",
  "severity": "error",
  "file": "prog.pdl",
  "span": {"line": 2, "col": null, "end_line": null, "end_col": null},
  "block_path": ["code"],
  "message": "code block finished without assigning `result`",
  "notes": [
    {"kind": "rule",
     "text": "A `code:` block's value is whatever its code assigns to the variable `result`. This block assigned nothing."},
    {"kind": "note",
     "text": "`print(...)` writes to stdout; it does not set the block's value."}
  ],
  "suggestions": [
    {"text": "assign the value instead of printing it", "replacement": "result = 'hi'"}
  ]
}
```

Rendering rules used above, offered to item 0 as the general contract:

- line 1 is `<file>:<line> - <message>`, once, never repeated;
- `kind: "rule"` notes render as an indented paragraph with no prefix;
- `kind: "note"` notes render as `  note: <text>`;
- a suggestion renders as `  help: <text>` and, when `replacement` is present,
  `  help: <text>:  <replacement>`.

`severity` is carried but not rendered: no PDL diagnostic prints a severity token today,
and introducing one for a single message would make this the odd entry out. It becomes an
`error:` prefix when the renderer does it for all ~70 IDs at once.

## Where the data comes from

Raise site: `src/pdl/pdl_interpreter.py:2657` (`call_python`), wrapped at `:2506-2537`
(`process_call_code`, `PythonCodeBlock` case).

| Field | Value | Source | Available today? |
| --- | --- | --- | --- |
| `file` | `prog.pdl` | `loc.file`, set at `pdl_parser.py:32` from `parse_file` (`pdl_parser.py:21`); rendered by `get_loc_string` at `pdl_location_utils.py:97-98` | yes |
| `span.line` | `2` | `get_line(loc.table, loc.path)`, `pdl_location_utils.py:102-107`, with `loc = append(loc, "code")` | yes — see below |
| `span.col`, `end_*` | `null` | not computed anywhere (DROP #1/#2) | **no** — item 0 |
| `block_path` | `["code"]` | `loc.path` after the same `append`; carried to the print site and dropped (DROP #10) | value yes, rendering **no** — item 7 |
| `message` | constant string | new literal at the raise site | yes |
| `notes[0]` evidence | `assigned nothing` / the name list | `my_namespace.__dict__` after `exec` (`pdl_interpreter.py:2649`) minus the names present before it (`:2645`) | namespace yes; the *before* set needs one new local (see below) |
| `notes[1]` print note, `suggestions[0].replacement` | `result = 'hi'` | `ast.parse(code)` over the `code` parameter of `call_python` (`:2644`), which is `code_s` from `:2480` | yes; `ast` is stdlib but **not currently imported** in `pdl_interpreter.py` (imports at `:2-47`) |
| near-miss ranking | `resutl` → `result` | `difflib.get_close_matches("result", assigned, n=1, cutoff=0.6)` | yes; stdlib, **not currently imported** |
| `id` | `E-CODE-002` | no diagnostic-ID registry exists | **no** — item 0 owns it; unrendered until then |
| `severity` | `error` | implicit in `PDLRuntimeError` → exit 1 at `pdl_interpreter.py:246-254` | value yes, unrendered by choice |

### On `prog.pdl:2` — this is not an invented location

`call_python` and `process_call_code` both hold `loc`, the block's location, whose `path`
is `[]` for a top-level block (initialised at `pdl_parser.py:32`; `process_call_code`
receives it unchanged from `process_leaf_block:963`). `get_line` on an empty path returns
`0` at `pdl_location_utils.py:104` — that is the `:0` in today's output and in the
E-CODE-001 sibling, and **this spec does not fix it**.

What it does do is name the field. `append(loc, "code")` gives `path == ["code"]`, and the
line map built by `get_line_map` for this program is `{"['lang']": 1, "['code']": 2}`
(hand-traced through `get_paths`, `pdl_location_utils.py:33-70`; the root segment is
popped for top-level keys, so the key really is `"['code']"`). Lookup hits, and the
diagnostic lands on the `code:` key. `process_block_of(block, "code", ...)` at
`pdl_interpreter.py:2473` already computes exactly this location internally
(`pdl_interpreter.py:1643`), so no new machinery is involved — it is the same one-token
`append` used at `:971` and `:2717`.

If the append ever misses, `get_line` degrades to the block's own line, which is today's
`:0`. The change cannot make the location worse. **Verify the traced line map with a
scratch run before implementing** — I could not execute it.

Location still scores 1, not 2: the line is the enclosing key, there is no column, and
`loc.path` is not rendered. Both remaining points are foundation work, not E-CODE-002
work.

### What has to change at the raise site

Three small things, all inside `pdl_interpreter.py`:

1. `call_python` (`:2644-2659`): capture the pre-`exec` names —
   `bound_before = set(my_namespace.__dict__)` after `:2645` — and move the `result` read
   inside the `try`, reading it with `hasattr`/`getattr` rather than attribute access.
   `hasattr` matters: a PDL variable named `result` already in `scope` is copied into the
   namespace at `:2645`, so today such a block succeeds and returns it. That must keep
   working — **no success-path behaviour change**.
2. The assigned-name list must preserve binding order:
   `[n for n in my_namespace.__dict__ if n not in bound_before and n != "__builtins__"
   and not n.startswith("_")]`, with module objects dropped. Ordered `dict` iteration, no
   `set` iteration — the output must not vary with `PYTHONHASHSEED` (cf. E-SCHEMA-010).
   Known limitation, acceptable: a name that was *already* in the PDL scope and is
   reassigned by the code will not appear in the list.
3. `call_python` has no `loc` and no `block` (signature at `:2644`), so it cannot raise a
   located error. It should raise a **module-private** `_MissingResultError`, caught by a
   new clause placed *before* `except PDLRuntimeExpressionError` at `:2518`, which
   re-raises `PDLRuntimeError(exc.message, loc=append(loc, "code"), trace=...)` with the
   same `trace=` payload as `:2533`. This is what keeps the `f"Python Code error: "`
   prefix at `:2520` off the message — that prefix says the code errored, and this code
   did not. A module-private subclass of `PDLRuntimeExpressionError` in
   `pdl_interpreter.py` avoids touching the exported hierarchy in `pdl_ast.py:1631-1682`.

The diagnostic *content* is computed in `call_python` because that is the only place the
namespace exists. Pre-foundation it travels as a pre-rendered string on the exception;
post-foundation it travels as the record fields above. That seam is where item 0 plugs in.

Free adjacent fix, optional: `sys.path.pop()` at `:2658` is already skipped whenever
`exec` raises, so `sys.path` grows on every failing code block. The new error path must
not add a second instance of that; a `try/finally` around the body settles both.

## Rejected alternatives

**The literal one-line fix** (INVENTORY §6 item 3: move `result = my_namespace.result`
inside the `try`). It stops the crash, but the message becomes the E-CODE-001 shape —
`prog.pdl:0 - Python Code error: <traceback formatted into the message>` — which still
names no PDL rule, still shows the interpreter's own frames, still says "Code error"
about code that ran fine, and still offers no fix. Roughly L1 W0 Y1 F0 H1 = 3/15, and the
entry would have to be reopened later. The extra work over the one-liner is a name-set
diff and an `ast` walk.

**Make a missing `result` evaluate to `None`.** Turns a loud failure into a silent wrong
answer, and per 5.5 a semantics change needs a measured blast radius and a release note.
Issue #386 asks for a better error, not different behaviour.

**List everything in the namespace** ("variables in scope: ..."). That is the whole PDL
scope plus `PDL_SESSION`, `stdlib`, `pdl_context`, `pdl_usage` and the code's own names —
dozens of entries, an E-SCHEMA-007-style wall, and the user's own name is buried in it.
Only names the code *bound* are evidence.

## Risk

- **No public API change. No AST change. No new dependency.** `ast` and `difflib` are
  stdlib; the new exception class is module-private; `PDLRuntimeError`'s signature,
  `pdl__trace` payload and the trace format are untouched.
- **Goldens.** `tests/errors/corpus/E-CODE-002/expected.txt` must be regenerated, and
  `"hygiene_traceback_expected": true` deleted from `case.json` in the same commit —
  leaving it makes `test_no_traceback` XPASS and fails the suite (RUBRIC §"Two hygiene
  sub-flags").
- **No message-asserting test breaks.** `Python Code error` appears only in
  `src/pdl/pdl_interpreter.py:2520,2531`, the two E-CODE goldens, and the docs;
  `tests/test_runtime_errors.py` asserts Jinja and parser messages, not code-block ones.
  `tests/test_line_table.py` does not cover this path.
- **Behaviour change, narrow and intended:** a program whose `code:` block never assigns
  `result` exits 1 with a diagnostic instead of exiting 1 with a traceback. Same exit
  code, so nothing scripted changes. Programs that inherit `result` from the PDL scope
  keep working — that is what the `hasattr` read protects, and it deserves a test.
- **Consistency debt this creates:** E-CODE-001 will still print
  `prog.pdl:0 - Python Code error: <traceback>` next door. Two neighbouring diagnostics
  in different styles is worse than one, so E-CODE-001 should follow with the same header
  shape (`code block raised <ExcType>`, the user's `<code-block>` frame only, interpreter
  frames stripped). Flagged, not solved here.

---

**Expected rubric delta:** 0/15 → **13/15** today (L1 W3 Y3 F3 H3), 15/15 once the
foundation and block-path rendering land. Corpus-wide, this moves Fix off zero for the
first entry that can genuinely earn it.

**One sentence a user takes away:** "A `code:` block's value is whatever it assigns to
`result` — I printed instead of assigning, and it told me the exact line to write."
