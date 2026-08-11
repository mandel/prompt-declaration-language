# E-CODE-001 — a Python exception escapes a `code:` block

Phase-3 item 3, second half. Sibling of [`E-CODE-002.md`](E-CODE-002.md), which shipped;
that spec's Risk section names this entry as the consistency debt it created. Everything
below is designed to stand next to the E-CODE-002 output without looking like a different
tool wrote it: same header shape, same one-location-prefix rule, same rule/`note:`/`help:`
vocabulary, same `append(loc, "code")` location.

**All `pdl_interpreter.py` line numbers in this spec were re-read at the end of the
session.** The file moved by 14–16 lines while this was being written, so anything cited
from an earlier reading (including E-CODE-002.md's own citations) is stale.

## Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:0 - Python Code error: Traceback (most recent call last):
  File "<REPO>/src/pdl/pdl_interpreter.py", line <LINE>, in call_python
    exec(c, my_namespace.__dict__)  # nosec B102
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<code-block>", line 1, in <module>
ZeroDivisionError: division by zero
```

Rubric: L1 W1 Y2 F0 H1 = 5/15

Five lines of report for a two-line program. The first three are PDL's own plumbing; the
line the user actually wrote (`result = 1/0`) appears nowhere, because `linecache` cannot
resolve `<code-block>` and so the traceback prints the frame without its source. `:0` is
the top-level-block defect (DROP #4). `Python Code error:` is a category label, not a
rule. This is not a crash — `call_python` catches the exception and formats
`traceback.format_exc()` **into** the message at `pdl_interpreter.py:2886`, so the leak is
by construction, and `hygiene_traceback_expected` is set in `case.json` accordingly.

## Target

The reproducer (`lang: python` / `code: |` / `result = 1/0`):

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2 - code block raised ZeroDivisionError: division by zero

code:1 | result = 1/0
       |          ^^^

  Python code in a `code:` block must run to completion; an exception that
  escapes it stops the program. Line numbers above are within the block's
  code, not the PDL file.
```

Rubric: L1 W3 Y3 **F0** H3 = 10/15

Fix stays at 0 **for this reproducer, on purpose**. There is nothing true and useful to
say to someone who divided by zero, and the rubric ranks a vacuous `help:` below none.
The diagnostic does earn F3 — on the branches where a suggestion can be checked (see the
branch table). If the corpus should pin those, they want their own entries rather than a
changed reproducer here; see "Adjacent entries this design creates".

### Reading the new output

- **`code:1 |`** is the gutter. The number is a line of *the code the block ran*, not of
  `prog.pdl`, and the `code:` prefix says so at a glance. A bare `1 |` would read as file
  line 1 — which is `lang: python` — i.e. a confidently-stated wrong location, the one
  thing the rubric scores below saying nothing.
- The **source line** is supplied from the `code` string, not from `linecache`. This is
  why today's traceback shows no source under the `<code-block>` frame and the target
  does.
- The **caret span** comes from CPython's own position table (`FrameSummary.colno` /
  `end_colno`, 3.11+), so it lands exactly on `1/0` and not on the whole statement. It is
  the one dimension of this diagnostic that does *not* wait on Phase-3 item 0: the
  coordinates are inside a string the interpreter is holding.

### Which frames survive: `filename == "<code-block>"`, exactly

Measured, not reasoned (coordinator's run). For

```
lang: python
code: |
  def helper():
      return 1/0
  result = helper()
```

today's frame list is `pdl_interpreter.py:2880 in call_python`, `<code-block>:3 in
<module>`, `<code-block>:2 in helper`. `compile(code, "<code-block>", "exec")`
(`pdl_interpreter.py:2879`) stamps that filename on every frame executing the block's own
source — including frames inside functions the block itself defined. So the filter is
mechanical and needs no heuristic:

> Keep frames whose `filename` is `<code-block>`. Drop every other frame.

That keeps both user frames above and drops PDL's `exec`. It renders as:

```
prog.pdl:2 - code block raised ZeroDivisionError: division by zero

code:3 | result = helper()
       |          ^^^^^^^^
code:2 |     return 1/0
       |            ^^^ in helper

  Python code in a `code:` block must run to completion; an exception that
  escapes it stops the program. Line numbers above are within the block's
  code, not the PDL file.
```

Outermost first, innermost last — Python's order, because that is the order the reader
already knows. The caret label carries the function name for any frame that is not
`<module>`; when no column is available the label goes with the caret line.

**Frames in libraries the user imported are dropped too**, and that is deliberate: they
are not text the user can edit, and a `json/decoder.py` frame chain is unbounded. But
dropping them silently would leave `result = json.loads("{")` looking like the raising
line when it is not, so when the *innermost* frame of the whole traceback is not
`<code-block>`, one bounded line says where it really came from:

```
prog.pdl:2 - code block raised JSONDecodeError

code:2 | result = json.loads("{")
       |          ^^^^^^^^^^^^^^^

  JSONDecodeError: Expecting property name enclosed in double quotes: line 1
  column 2 (char 1)

  Python code in a `code:` block must run to completion; an exception that
  escapes it stops the program. Line numbers above are within the block's
  code, not the PDL file.

  note: raised inside `decoder.py`, line 355, in `raw_decode`, which this
        block called.
```

Basename only, never the absolute path (hygiene: no absolute paths, and it keeps the
golden machine-independent). The same line fires when the innermost frame is in the
user's *own* module next to the program — which is right: `helpers.py` is as informative
a name as PDL can give without leaking a path.

**Recursion and long chains are capped.** At most three `<code-block>` frames are shown;
beyond that it is the outermost, then `... N more frames`, then the innermost. A
`RecursionError` prints five lines, not a thousand.

### Where the exception text goes

Line 1 carries `<ExcType>: <detail>` because that is the string a user greps for, with
`detail` = the first line of `str(exc)` clipped to 60 characters. If it was clipped or was
multi-line, the full text is repeated as an indented paragraph after the frames, wrapped
at 78 and capped at five lines with `... (N more lines)` — the `JSONDecodeError` example
above is the clipped case. This is the only conditional in the layout, and it exists so
that a 2000-character exception message from inside user code cannot become an
E-SCHEMA-007-style wall in either position.

The header cannot be length-tested exactly, because `call_python` builds the message body
and `generate` prepends `get_loc_string(...)` later (`pdl_interpreter.py:267`). The
60-character clip is a fixed budget chosen for that reason, not a measured fit.

### The branch table

One diagnostic, several renderings. The header verb, the evidence and the `help:` are
computed; the rule paragraph is constant.

| Situation | Header | Extra evidence | Suggestion |
| --- | --- | --- | --- |
| exception in the block's own code, short single-line message | `code block raised ZeroDivisionError: division by zero` | frames | none |
| message multi-line or over 60 chars | `code block raised JSONDecodeError` | full text, capped at 5 lines | none |
| innermost frame below the block | either of the above | + `note: raised inside \`decoder.py\`, line 355, in \`raw_decode\`, which this block called.` | none |
| `NameError` with a near miss among visible names | `code block raised NameError: name 'valeu' is not defined` | frames, caret on the name | `help: did you mean \`value\`?` |
| `NameError`, no near miss | same | frames | `note:` PDL variables in scope are usable by name (+ the list, capped at 5) / `help: define \`foo\` in the code, or define it earlier with \`def:\`.` |
| `ModuleNotFoundError` | `code block raised ModuleNotFoundError: No module named 'numpy'` | frames | `note:` a `code:` block runs in the same Python environment as `pdl` itself, with the program's directory on `sys.path`. / `help: install \`numpy\` in that environment.` |
| `SyntaxError` from `compile` (no `<code-block>` frame) | `code block has a syntax error: invalid syntax` | `exc.text` at `code:<lineno>`, caret at `exc.offset` | none |
| `__cause__` or unsuppressed `__context__` present | unchanged | + `note: caused by \`KeyError: 'x'\`` (one line, clipped) | unchanged |
| no frames, not a `SyntaxError` | `code block raised <ExcType>: ...` | none | none |

Only three branches produce a `help:`, and each is checkable:

- **`did you mean`** — `difflib.get_close_matches(exc.name, candidates, n=1, cutoff=0.7)`
  over the names the code bound, the PDL variables in scope, and `dir(builtins)`. Same
  helper and the same determinism argument as E-CODE-002's near-miss branch (ordered
  iteration, never a `set`, so it does not move with `PYTHONHASHSEED`). Deliberately
  computed here rather than taken from CPython's own `Did you mean:` suffix, which is
  produced by `traceback.format_exception_only` and whose heuristics change between
  Python versions — the golden must not.
- **`define it earlier with \`def:\``** — not `scope:`. `scope:` *replaces* the block's
  execution scope (`pdl_interpreter.py:2560-2561`), so telling a user to reach for it to
  add one name would silently drop every other variable they were relying on. `def:` /
  `defs:` adds to the scope, and a PDL variable in scope is visible in the code by name
  because the namespace is seeded from it (`pdl_interpreter.py:2875`). Existing tests pin
  both halves: `tests/test_code.py:343-353` (`scope:` replaces) and
  `tests/test_code.py:29-43` (a scope variable is visible in the block).
- **`install \`numpy\` in that environment`** — the note's claim about `sys.path` is
  `sys.path.append(str(state.cwd))` at `pdl_interpreter.py:2877`, with
  `state.cwd = Path(pdl_file).parent` at `pdl_interpreter.py:244`. Read from the code,
  **not executed**; V4 below confirms it.

Everything else stays silent. `ZeroDivisionError`, `TypeError`, `KeyError`, `IndexError`
and an explicit `raise` in the user's own code get no `help:` line at all.

### After Phase-3 item 0 (foundation) and item 7 (block paths)

Written out so this spec is not redone:

```
prog.pdl:3:12 - code block raised ZeroDivisionError: division by zero
  in code

3 |   result = 1/0
  |            ^^^

  Python code in a `code:` block must run to completion; an exception that
  escapes it stops the program.
```

Rubric: L3 W3 Y3 F0 H3 = 12/15 (15/15 on the branches that carry a `help:`).

Four deltas, all from the foundation: the header line becomes the real file line and
column, `in code` renders `loc.path` (`text[2].code` when nested), the gutter becomes file
line numbers, and the numbering caveat sentence disappears with them. The arithmetic is
`file_line = scalar_start + code_line - 1`, `file_col = scalar_indent + code_col`, exact
for a block scalar because item 0's YAML marks give the *value* node's start.

**The `code:N` gutter must survive as a fallback even after item 0.** `code:` need not be
a literal scalar — `tests/test_code.py:13-21` passes `code: {text: [...]}`, a nested block
whose text is assembled at runtime, and `code: >` folds lines so that the correspondence
is not linear. Where the executed code is not a literal block scalar of the source, no
honest file line exists and the code-relative gutter is the correct answer, not a
degraded one.

## Structured record

Decision 5.6.

```json
{
  "id": "E-CODE-001",
  "severity": "error",
  "file": "prog.pdl",
  "span": {"line": 2, "col": null, "end_line": null, "end_col": null},
  "block_path": ["code"],
  "message": "code block raised ZeroDivisionError: division by zero",
  "notes": [
    {"kind": "rule",
     "text": "Python code in a `code:` block must run to completion; an exception that escapes it stops the program. Line numbers above are within the block's code, not the PDL file."}
  ],
  "suggestions": [],
  "source": "result = 1/0\n",
  "frames": [
    {"line": 1, "col": 10, "end_col": 13, "func": "<module>"}
  ]
}
```

Rendering rules are E-CODE-002's, unchanged, plus one: a `frames` list renders as
`code:<line> | <source>` with a caret line beneath, at most three entries, elided in the
middle.

`frames` is the one field this diagnostic needs that the record type does not have. It
cannot be folded into the existing `spans` list on `pdl_diagnostics.Diagnostic`
(`src/pdl/pdl_diagnostics.py:95`), because `span` is file-relative while these coordinates
are relative to `source`, and one list carrying two coordinate systems is a trap for the
first machine consumer that reads it. The field is **additive and defaults to empty**, so
no existing diagnostic changes shape. Pre-item-0 none of this matters: as with E-CODE-002,
the body is a pre-rendered string travelling on the exception, and the record is the
contract for when item 0 absorbs it.

## Where the data comes from

Raise site: `src/pdl/pdl_interpreter.py:2885-2887` (`call_python`'s `except Exception`;
the `traceback.format_exc()` is `:2886`). Wrapped at `:2610-2618`
(`process_call_code`, `PythonCodeBlock` case, the `except PDLRuntimeExpressionError`
clause), printed at `:267` (`generate`).

| Field | Value | Source | Available today? |
| --- | --- | --- | --- |
| `file` | `prog.pdl` | `loc.file`, set at `pdl_parser.py:284` via `parse_file` (`:167`); rendered by `get_loc_string`, `pdl_location_utils.py:94-99` | yes |
| `span.line` | `2` | `get_line(loc.table, loc.path)`, `pdl_location_utils.py:102-107`, with `loc = append(loc, "code")` | yes — **measured**, not traced: the shipped `tests/errors/corpus/E-CODE-002/expected.txt:7` reads `prog.pdl:2` for an identically-shaped program |
| `span.col`, `end_*` | `null` | not computed anywhere (DROP #1/#2) | **no** — item 0 |
| `block_path` | `["code"]` | `loc.path` after the same `append`; carried to the print site and dropped (DROP #10) | value yes, rendering **no** — item 7 |
| `message` type + detail | `ZeroDivisionError`, `division by zero` | `type(exc).__name__`, `str(exc)`, from the `exc` bound at `pdl_interpreter.py:2885` | yes |
| `frames[*].line`, `.func` | `1`, `<module>` | `traceback.extract_tb(exc.__traceback__)` filtered on `filename == "<code-block>"`, the name given to `compile` at `:2879`; `traceback` imported at `:14` | yes |
| `frames[*].col`, `.end_col` | `10`, `13` | `FrameSummary.colno` / `end_colno`, populated from `co_positions` | yes on CPython ≥3.11, which `pyproject.toml:32` already requires — but **undocumented before 3.13**, so read with `getattr(f, "colno", None)`, and `None` under `-X no_debug_ranges` / `PYTHONNODEBUGRANGES=1` |
| `source` line text | `result = 1/0` | the `code` parameter of `call_python` (`:2874`), which is `code_s` from `:2559`. `linecache` cannot supply it — that is why today's `<code-block>` frame prints bare | yes |
| `SyntaxError` position | `lineno`, `offset`, `text` | the exception itself; `compile` (`:2879`) fills them because it was handed the source | yes |
| near-miss candidates | `value`, … | `_assigned_names(my_namespace.__dict__, bound_before)` (`:2765` region, shipped with E-CODE-002) + `scope` keys + `dir(builtins)`; `difflib` at `:5`, `builtins` at `:3` | yes |
| in-scope name list | user variables only | `scope` (param of `:2874`) minus `set(empty_scope)` (`:179-186`), `stdlib` / `pdl_usage` (`:308`) and `PDL_SESSION` (`:2875`) — derived from the code, not a hand-typed denylist | yes |
| `exc.name` | `valeu`, `numpy` | `NameError.name` / `ImportError.name`, CPython ≥3.10 | yes |
| `id` | `E-CODE-001` | no diagnostic-ID registry exists | **no** — item 0 owns it; unrendered until then |

### The one thing that is not available: the code's file lines

`get_line` reaches the `code:` **key** (file line 2). The first *content* line of the
scalar is one line further down for `|`, the same line for a plain `code: result = 1/0`,
and not linearly related at all for `>` or for a nested `code:` block. Nothing in the
location table records it: `get_line_map` (`pdl_location_utils.py:73-91`) maps key paths
to lines and nothing else, and the YAML node marks that would give the value's start are
discarded by `yaml.safe_load` (DROP #1).

The two numbering systems therefore have to be **reported separately today**, which is
what the `code:N` gutter does, and reconciled by item 0, which is where the scalar's start
mark arrives. Re-reading `loc.file` at diagnostic time to sniff the scalar style was
considered and rejected below.

### What has to change at the raise site

All inside `pdl_interpreter.py`; the shape is E-CODE-002's, one step further.

1. `call_python:2885-2887` — replace `traceback.format_exc()` with a
   `_raised_diagnostic(exc, code)` body and raise a module-private
   `_CodeBlockRaised(PDLRuntimeExpressionError)`, exactly as `_MissingResultError`
   (`:2736`) is raised at `:2893`. `call_python` has neither `loc` nor `block`
   (signature at `:2874`), so it still cannot raise a located error itself; it has `code`
   and the live namespace, which is the only place the evidence exists.
2. `process_call_code` — add `except _CodeBlockRaised as exc:` **before** the
   `except PDLRuntimeExpressionError` clause at `:2610`, re-raising
   `PDLRuntimeError(exc.message, loc=append(loc, "code"), trace=..., source_exception=exc)`
   with the same `trace=` payload as `:2605-2607`. This is what keeps the
   `Python Code error:` prefix (`:2612`) off the message and moves `:0` to `:2`.
3. Narrow the generic `except Exception` at `:2621-2629` from
   `f"Python Code error: {traceback.format_exc()}"` to `f"Python Code error: {exc!r}"`.
   After (1) it is near-unreachable — it can only catch a failure in the
   `SingletonContext`/`PdlDict` construction at `:2588-2596` — but as written it is a
   second, latent traceback leak sitting in the same `match` arm, and leaving it would
   make `test_no_traceback` pass for a reason that is one refactor away from being false.
4. Non-ASCII correctness: `FrameSummary.colno` is a **UTF-8 byte** offset, not a character
   offset. Converting is one line
   (`len(line.encode("utf-8")[:colno].decode("utf-8", "replace"))`) and CPython's own
   `traceback` module does the same thing internally; without it, a caret under a line
   containing accented text lands to the right of the token. V1 below demonstrates it.

`sys.path` is already safe: the `finally` at `:2899-2900` came with E-CODE-002.

### Unverified, with the exact commands

I have no shell. Everything below is read from the source; these confirm it.

**V1 — frames, columns, byte-offset trap.** Expect: exactly one leading non-`<code-block>`
frame in each case; `<code-block>` linenos `1` / `3,2` / `2` / `1`; `colno`/`end_colno`
non-`None`; and for `résultat = 1/0` a `colno` of 12 against a character column of 11,
which is the conversion in change (4).

```
python3 - <<'EOF'
import traceback
for src in ["result = 1/0\n",
            "def helper():\n    return 1/0\nresult = helper()\n",
            "import json\nresult = json.loads('{')\n",
            "résultat = 1/0\n"]:
    try:
        exec(compile(src, "<code-block>", "exec"), {})
    except Exception as e:
        print(repr(src), "->", type(e).__name__, repr(str(e)))
        for f in traceback.extract_tb(e.__traceback__):
            print("   ", f.filename, f.lineno,
                  getattr(f, "colno", None), getattr(f, "end_colno", None), f.name)
EOF
```

**V2 — a compile-time `SyntaxError` has no `<code-block>` frame**, which is why the branch
is selected by type and empty-frame-list rather than by frames alone. Expect a frame list
naming only the caller, and `lineno`/`offset`/`text` populated.

```
python3 -c "
import traceback
try:
    compile('x = (\n', '<code-block>', 'exec')
except SyntaxError as e:
    print([f.filename for f in traceback.extract_tb(e.__traceback__)])
    print(e.lineno, e.offset, repr(e.text), e.msg)
"
```

**V3 — the near-miss cutoff.** Expect `['value']` for the first and `[]` for the second,
i.e. 0.7 suggests on a transposition but not on an unrelated name.

```
python3 -c "
import difflib
print(difflib.get_close_matches('valeu', ['value','greeting','print'], n=1, cutoff=0.7))
print(difflib.get_close_matches('foo',   ['value','greeting','print'], n=1, cutoff=0.7))
"
```

**V4 — the `sys.path` claim in the `ModuleNotFoundError` note.** Expect `7`: a module
dropped beside the program is importable from a `code:` block.

```
mkdir -p /tmp/e-code-001 && printf 'X = 7\n' > /tmp/e-code-001/side_mod.py
printf 'lang: python\ncode: |\n  import side_mod\n  result = side_mod.X\n' > /tmp/e-code-001/p.pdl
pdl /tmp/e-code-001/p.pdl
```

**V5 — end to end, after implementation.** `pdl tests/errors/corpus/E-CODE-001/prog.pdl`
must reproduce the Target block byte for byte, including the caret column.

## Rejected alternatives

**Strip only PDL's frames and print the rest of `traceback.format_exc()` as-is.** The
cheapest change that removes the leak's worst half, and it is wrong on all five
dimensions: it keeps the `Traceback (most recent call last):` banner and the
`File "<code-block>", line 1` vocabulary (What stays at 1), it still cannot show the
source line, it is unbounded for recursion, and the harness's global no-traceback
invariant (5.8) exists precisely to reject output of that shape. It also leaves the header
at `prog.pdl:0`.

**Fall back to `repr(exc)`, matching E-CODE-003's `Shell Code error: ValueError(...)`.**
One line, no frame machinery, no traceback. But it throws away the offending source line
and the position, which is the whole of the Why dimension — it would land at about
L1 W2 Y1 F0 H2 = 6/15, the same score as the shell entry it copied, and it would set the
corpus's shape backwards a week after E-CODE-002 set it forwards.

**Re-read `loc.file` at diagnostic time to find where the `code:` scalar starts**, so the
gutter could carry real file lines today. It would work for the common `code: |` case (one
regex on the key line), and it is a trap: the file may have changed since parsing, the
program may have come from `parse_str` with `file == ""` (`pdl_parser.py:277-278`), a
nested `code:` block has no scalar to find, and a wrong file line is scored below no file
line. Item 0 gets this right by construction; two months of a heuristic is not worth it.

**Suggest `fallback:` for any failing code block.** It is the one PDL-shaped action that
applies to every branch, which is exactly what makes it bad advice: for a genuine bug it
recommends swallowing the error, and the rubric puts a wrong suggestion below no
suggestion. Rejected outright rather than gated.

## Risk

- **No AST change. No public API change. No new dependency.** `traceback`, `difflib`,
  `builtins`, `ast`, `textwrap` are all already imported (`pdl_interpreter.py:2-14`); the
  new exception class is module-private, like `_MissingResultError`; `PDLRuntimeError`'s
  signature and the trace format are untouched.
- **One additive record field.** `frames` on the diagnostic record (and eventually on
  `pdl_diagnostics.Diagnostic`) is new, defaulted to empty, consumed by nothing today. It
  is not needed for the pre-item-0 implementation at all. Called out here rather than
  buried because it touches the 5.6 contract, but it changes no existing diagnostic.
- **Goldens.** `tests/errors/corpus/E-CODE-001/expected.txt` is regenerated and
  `"hygiene_traceback_expected": true` must be **deleted from `case.json` in the same
  commit** — leaving it makes `test_no_traceback` XPASS and fails the suite. The golden
  churns a second time when item 0 renumbers the gutter; E-CODE-002 accepted the same
  two-step and the post-foundation rendering is written out above so the second step is
  mechanical.
- **No message-asserting test breaks.** `Python Code error` appears only at
  `pdl_interpreter.py:2612` and `:2623`, in the two E-CODE goldens and in docs.
  `tests/test_code.py:108-117` exercises a *raising* block (`raise ValueError('boom')`) but
  asserts only `pytest.raises(PDLRuntimeError)` and the `sys.path` invariant, both of which
  survive. No test in `tests/` asserts the text of a Python code-block failure.
- **The message becomes multi-line, and it travels.** `PDLRuntimeError.message` is copied
  into `ErrorBlock.msg` by the enclosing block handlers (e.g. `pdl_interpreter.py:995`)
  and so into the trace JSON and the viewer. Already true since E-CODE-002 shipped; this
  widens the set of programs it happens for.
- **Environment sensitivity of the caret.** Under `PYTHONNODEBUGRANGES=1` or
  `-X no_debug_ranges`, `colno` is `None` and the caret lines vanish — output stays valid
  but the golden differs. The corpus harness must not set either. Stated because it is the
  only part of the output that depends on how the interpreter was launched.
- **Exit code, stdout and the success path are untouched.** Still exit 1, still nothing on
  stdout for this program.
- **Out of scope, found while specifying, worth its own look:** `sys.exit()` inside a
  `code:` block raises `SystemExit`, a `BaseException`, which neither `call_python:2885`
  nor `generate:256` catches — a code block can end the whole program with an arbitrary
  exit code and no diagnostic. That is a `hygiene_silent_failure` candidate, not part of
  this change.

## Other `lang:` values — this design does not generalise

Measured, not reasoned (coordinator's run):

| `lang:` | Today | Shape |
| --- | --- | --- |
| `python` | `prog.pdl:0 - Python Code error: <traceback>` | this spec |
| `jinja` | `j.pdl:0 - Jinja Code error: ZeroDivisionError('division by zero')` | `repr`, no traceback, `:0` — E-CODE-003's shape, **no corpus entry** |
| `command` | `prog.pdl:0 - Shell Code error: ValueError('command exited with non zero code: 7')` | E-CODE-003, 6/15, plus the command's own stderr printed detached |
| `pdl` | `p.pdl:2 - Error during the evaluation of text: ${ nope }: 'nope' is undefined` | routes through expression evaluation; real line; E-EXPR class |
| `ipython` | `Code error: <repr>` (`pdl_interpreter.py:2650` region) | **no corpus entry** |

Only `python` has frames to filter, so only `python` gets the body designed here. What
*does* generalise is the header shape (`<file>:<line> - code block <verb> <detail>`, no
category label) and the `append(loc, "code")` location fix, which would move all five off
`:0` in one small change. Flagged, not folded in — each wants its own entry, and the jinja
one has no corpus reproducer yet.

## Adjacent entries this design creates

Three branches of this diagnostic are worth pinning with goldens of their own rather than
by rewriting this reproducer, which is the one the inventory recorded:

- **`NameError` in a `code:` block** — the only branch that earns F3 and the one most
  users hit. Reproducer: `code: |` / `result = valeu * 2` with `defs: value: 3`.
- **`SyntaxError` in a `code:` block** — a different header verb and a different position
  source (`exc.offset`, not a frame). Specified above because it flows through the same
  `except`, but untested by the corpus today.
- **`lang: jinja` failure** — `:0` plus a `repr`, currently unrepresented.

---

**Expected rubric delta:** 5/15 → **10/15** for this reproducer (L1 W3 Y3 F0 H3), 12/15
after item 0 and item 7; 13/15 today and 15/15 after, on the `NameError` and
`ModuleNotFoundError` branches. It clears one of the last three traceback leaks in the
corpus, and it does it without inventing a suggestion for a division by zero.

**One sentence a user takes away:** "It showed me my own line, with a caret on the
expression that blew up, and none of PDL's plumbing."
