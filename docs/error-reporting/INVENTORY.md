# PDL Error Reporting — Phase 0 Inventory

Status: **recon only**. No behaviour has been changed.

Provenance of the quoted messages: rows marked **[obs]** were captured by running the
current tree (commit `92ba118f6`, `prompt-declaration-language 0.1.dev50+g92ba118f6`)
against a reproducer. Rows marked **[src]** were read off the source and have *not* been
executed — treat their exact wording as unconfirmed until Phase 1 pins them in a golden
file. Where an observed message contradicted the source, the observation wins.

Reproduction environment: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`,
invoked as `pdl --stream none <prog>.pdl`, stdout and stderr captured separately.

---

## 1. Diagnostic construction sites

### 1.1 Python interpreter — `src/pdl/`

| File | Sites | What it does |
| --- | --- | --- |
| `pdl_parser.py` | `parse_str:30`, `parse_dict:38-54` | `yaml.safe_load` is **unguarded** — any YAML error escapes as a raw traceback. Pydantic `ValidationError` is caught and *discarded*, replaced by the output of `analyze_errors`; if that returns `[]`, a single useless fallback string is used. `PDLParseError.message` is a `list[str]`, not a `str` (inconsistent with `PDLException`). |
| `pdl_schema_error_analyzer.py` | `analyze_errors:92-228` | The only structured diagnostic producer. Walks `pdl-schema.json` against the raw `dict` in parallel. Emits `get_loc_string(loc) + <text>`. Eight message shapes, all string concatenation. |
| `pdl_schema_validator.py` | `type_check_args:10`, `type_check_spec:40`, `type_check:47` | Wraps `jsonschema.validate`; on failure re-runs `analyze_errors` for a better message, falls back to `e.message` (raw jsonschema text). |
| `pdl_schema_utils.py` | `:57` | `print(..., file=sys.stderr)` for `Deprecated type syntax: use X instead of Y.` — no location, no block path, fires **on the success path**, and fires once per evaluation (twice for a single 2-arg function in testing). |
| `pdl_location_utils.py` | `get_line_map:73`, `get_loc_string:94`, `get_line:102` | The entire location model. See §3. |
| `pdl_ast.py` | `:1631-1682` | Exception hierarchy: `PDLException` → `PDLRuntimeError` → {`PDLRuntimeExpressionError`, `PDLRuntimeParserError`}; `PDLException` → `PDLRuntimeProcessBlocksError`. Carry `message`, `loc`, `pdl__trace`, `fallback`, `source_exception`. No rendering logic. |
| `pdl_interpreter.py` | **56 raise sites**; `generate:243-254` is the only formatter | `generate` prints `get_loc_string(exc.loc) + exc.message` to stderr and returns 1. Everything not derived from `PDLException` escapes as a traceback. |
| `pdl_llms.py` | `:57-70` | LiteLLM errors. Raised **on the event-loop thread inside a coroutine**; surfaces through a `concurrent.futures` callback, so it never reaches `generate`'s handler. |
| `pdl_openai.py` | `:130`, `:138` | Same message shapes as `pdl_llms.py`. |
| `pdl_granite_io.py` | `:107` | `Error during processor (...) execution: <repr>`. |
| `pdl_context.py` | `:156` | `TypeError(f"'{type(context)}' object is not a valid context")` — leaks into model-call messages (issue #383). |
| `pdl_utils.py` | `:189`, `:218`, `:277` | `ValueError` on malformed `pdl_model_default_parameters` (uncaught at CLI level); `print(f"Failure generating the trace: ...", file=sys.stderr)`. |
| `pdl_runner.py` | `:11`, `:48` | Docker sandbox messages, `print` to stderr. |
| `pdl.py` | `main:213-337` | CLI entry. `argparse` handles flags. **No `try` around anything** — a missing file, a directory, or bad `-d` YAML all produce tracebacks. |
| `pdl_linter.py` | `_lint_pdl_file:362-381`, `run_linter:530-576` | `logger.error("     %s: %s", type(e).__name__, e.message)` prints the raw Python **list repr** of `PDLParseError.message`. Bare `except Exception: logger.exception(...)` dumps a full traceback. |

### 1.2 Rust interpreter — `pdl-live-react/src-tauri/src/pdl/interpreter.rs`

`type PdlError = Box<dyn Error + Send + Sync>`. 23 `format!`-based error constructions,
e.g. `PdlError::from(format!("Call arguments not a map: {:?}", x))`. **No location type
exists at all** — no file, no line, no block path. Debug output goes through
`eprintln!` gated on a debug flag. This is a second, independent, less complete
implementation; it does not share the Python taxonomy.

### 1.3 Viewer — `pdl-live-react/src/`

- `page/Run.tsx:78-82` — `catch (err) { term?.write(String(err)) }`. Whatever the Python
  process wrote, verbatim, including tracebacks.
- `page/ErrorBoundary.tsx` — React crash screen, unrelated to PDL diagnostics.
- `view/detail/kind/Error.tsx` — renders an `ErrorBlock` from a trace.
- `view/timeline/model.ts:157` — `.with({ kind: "error" }, () => [])  // TODO show errors in trace`.
  Error blocks are **silently dropped** from the timeline.

---

## 2. Taxonomy

Severity is *how bad the current UX is*, not how likely the error is:

- **S0 Catastrophic** — Python traceback reaches the user, or the error is silently swallowed.
- **S1 Severe** — no usable location, or the message is unactionable / internally-worded.
- **S2 Poor** — location present but coarse or wrong; message states *what* but not *why* or *how to fix*.
- **S3 Acceptable** — correct location and PDL-vocabulary message; missing excerpt/caret/suggestion.

Location fidelity legend: `file:line` = both; `line-only`; `wrong-line`; `line 0`;
`none`. **No diagnostic anywhere in the codebase emits a column, a source excerpt, a
caret, or a block path.** That is uniform, so it is not repeated per row.

### E-CLI — driver / argument handling

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-CLI-001 [obs] | `pdl nope.pdl` | `FileNotFoundError: [Errno 2] No such file or directory: 'nope.pdl'` + 6-frame traceback | none | **S0** |
| E-CLI-002 [obs] | `pdl .` (directory) | `IsADirectoryError: [Errno 21] Is a directory: '.'` + traceback | none | **S0** |
| E-CLI-003 [obs] | `pdl -d '{a: '` (malformed inline YAML) | raw `yaml.scanner.ScannerError` traceback | none | **S0** |
| E-CLI-004 [obs] | malformed `pdl_model_default_parameters` in `-f` file | `ValueError: invalid defaults ... for model matcher ...` or bare `AssertionError` | none | **S0** |

### E-PARSE — YAML level

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-PARSE-001 [obs] | unterminated quoted scalar | `yaml.parser.ParserError: while parsing a block collection ... expected <block end>, but found '<scalar>'` behind a **13-frame traceback** | PyYAML reports `in "<unicode string>", line 2, column 3` — correct line *and column*, but labelled `<unicode string>` instead of the filename | **S0** |
| E-PARSE-002 [obs] | tab used for indentation | `yaml.scanner.ScannerError: found character '\t' that cannot start any token` + traceback | same as above | **S0** |
| E-PARSE-003 [obs] | duplicate mapping key (`text:` twice) | *none* — last value silently wins, **exit 0** | n/a | **S0** |
| E-PARSE-004 [obs] | empty file | prints `null`, **exit 0** | n/a | S2 |
| E-PARSE-005 [obs] | non-UTF-8 bytes | `UnicodeDecodeError` traceback from `parse_file:19` | none | **S0** |

> Note E-PARSE-001/002: PyYAML *already computes* line **and column** and carries the
> offending snippet. The information exists and is thrown away by not catching the
> exception. This is the single cheapest large win in the project.

### E-SCHEMA — static validation

All produced by `analyze_errors`; all reach the user via `PDLParseError` → `generate:244`.

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-SCHEMA-001 [obs] | unknown key | `unknown_field.pdl:5 - Field not allowed: parameterss` | file:line | S3 |
| E-SCHEMA-002 [obs] | near-miss key (`descrption`) | `nearmiss.pdl:1 - Field not allowed: descrption` — identical to E-SCHEMA-001, **no "did you mean `description`?"** | file:line | S2 |
| E-SCHEMA-003 [obs] | missing required field | `missing_req.pdl:2 - Missing required field: return` | file:line | S3 |
| E-SCHEMA-004 [obs] | scalar type mismatch | `wrong_type.pdl:2 - 42 should be of type <class 'str'>` — **Python `repr` of a type object** in a user-facing message | file:line | S2 |
| E-SCHEMA-005 [src] | value not in enum | see E-SCHEMA-006 — in practice the enum branch is unreachable for block fields | — | — |
| E-SCHEMA-006 [obs] | analyzer finds nothing (e.g. `parser: xml`, `lang: ruby`) | `The file PDL enum_bad.pdl does not respect the schema.` — **no line, no field name, no explanation** | none | **S1** |
| E-SCHEMA-007 [obs] | dict fails every union branch (`- foo: bar` in a `text`) | `union_wall.pdl:3 - {'foo': 'bar'} should be of type: {'oneOf': [{'$ref': '#/$defs/ExpressionBlock'}, ... 24 refs ...]}` — a **1-line, 700-char dump of raw JSON Schema** | file:line | **S1** |
| E-SCHEMA-008 [obs] | same, for `contribute` | `contrib_bad.pdl:3 - {'result': 1, 'context': 2} should be of type: {'anyOf': [{'$ref': '#/$defs/ContributeTarget'}, ...]}` | file:line | **S1** |
| E-SCHEMA-009 [src] | list/object shape mismatch | `<value> should be a list` / `should be an object` / `should not be a list` | file:line | S2 |

### E-EXPR — Jinja expression evaluation

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-EXPR-001 [obs] | undefined variable | `jinja_undef.pdl:3 - Error during the evaluation of ${ missing_var }: 'missing_var' is undefined` | file:line | S2 — no "in scope here: …" and no near-miss suggestion |
| E-EXPR-002 [obs] | Jinja syntax error | `jinja_syntax.pdl:1 - Syntax error in ${ 1 + }: unexpected 'end of print statement'` | file:line; **Jinja's own `lineno`/offset within the template is discarded** | S2 |
| E-EXPR-003 [obs] | attribute/index miss | `Error during the evaluation of ${ {}['x'] }: 'dict object' has no attribute 'x'` — Jinja vocabulary (`'dict object'`), not PDL's | file:line | S2 |
| E-EXPR-004 [obs] | expression fails inside an **imported** file | `sub/imported.pdl:1 - Error during the evaluation of ${ kaboom }: ...` — **line 1, actual line 4** | **wrong-line** | **S1** |
| E-EXPR-005 [obs] | expression fails inside a called function | `call_lib.pdl:5 - ...` — correct line, but **no call stack**: nothing says which `call:` site reached it | file:line | S2 |
| E-EXPR-006 [obs] | error after `#`-comment lines in the file | `comment.pdl:2 - ...` — **actual line 5** | **wrong-line** | **S1** |

### E-TYPE — PDL type / spec checking

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-TYPE-001 [obs] | `spec` mismatch on a block result | `spec_fail.pdl:2 - Type errors during spec checking:` then `spec_fail.pdl:2 - hello should be of type <class 'int'>` — **two lines, both prefixed**, second leaks a Python type repr | file:line | S2 |
| E-TYPE-002 [obs] | argument type mismatch | `call_badargs.pdl:8 - Type errors during function call to ${ greet }:` / `call_badargs.pdl:9 - 42 should be of type <class 'str'>` | file:line | S2 |
| E-TYPE-003 [obs] | missing argument | `call_missingargs.pdl:7 - ... Missing required field: name` — "field" is schema vocabulary; the user wrote a **function argument** | file:line | S2 |
| E-TYPE-004 [src] | too many positional args (Python-embedded call) | `Too many arguments to the call of <name>` | `self.pdl__location` | S2 |
| E-TYPE-005 [src] | function result violates `spec` | `Type errors in result of the function <name>:` + analyzer lines | fun_loc — subject to the E-EXPR-004 bug | S2 |
| E-TYPE-006 [obs] | legacy type name (`str`, `int`, `bool`, `float`, `list`, `obj`) | `Deprecated type syntax: use string instead of str.` — **no file, no line, no block path**, repeated per evaluation, and emitted on **successful** runs | none | **S1** |

### E-RUNTIME — block execution

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-RUNTIME-001 [obs] | `include:` names a missing file | `FileNotFoundError` + **~20-frame traceback** through the interpreter | none | **S0** |
| E-RUNTIME-002 [obs] | `import:` names a missing file | same | none | **S0** |
| E-RUNTIME-003 [src] | included file has a schema error | `Attempting to include invalid yaml: <abs path>\n<inner errors>` — says "yaml" for a *schema* error; embeds an **absolute path** (cf. issue #410) | outer file:line + inner file:line | S2 |
| E-RUNTIME-004 [obs] | `read:` missing file | `bad_read.pdl:0 - file nofile.txt not found` | **line 0** when the block is at top level | S2 |
| E-RUNTIME-005 [src] | `call:` target is not a function | `Type error: <x> is of type <class 'str'> but should be a function.` (+ `You might want to call \`${ x }\`.`) — the only **"did you mean"-style hint in the codebase** | file:line | S3 |
| E-RUNTIME-006 [obs] | `for:` lists of unequal length | `for_len.pdl:1 - Lists inside the For block must be of the same length.` — does not say **which** lists or **what** lengths | file:line | S2 |
| E-RUNTIME-007 [src] | malformed `contribute` entry | `Contributions are expected to be strings or dictionaries of length 1 but got {elem}` — **missing `f`-prefix at `pdl_interpreter.py:1875` and `:1894`; the literal text `{elem}` is printed** | file:line | **S1** (bug) |
| E-RUNTIME-008 [src] | `lang:` unsupported at runtime | `Unsupported language: <lang>` — usually shadowed by E-SCHEMA-006 first | file:line | S2 |
| E-RUNTIME-009 [src] | bad aggregator | `An aggregator was expected but got a value of type <class ...>.` | file:line | S2 |
| E-RUNTIME-010 [src] | Ctrl-D / Ctrl-C | `EOF` / `Keyboard Interrupt` as *errors* with exit 1 | file:line | S2 |
| E-RUNTIME-011 [src] | retry exhausted | `[Retry 1/3] <loc> An error occurred in a PDL block. Error details: <full Python traceback>` in **ANSI red**, to stderr | file:line + traceback | **S1** |
| E-RUNTIME-012 [obs] | `for:` given a string instead of a list | *none* — iterates characters, **exit 0**. (Semantics, not a diagnostic; flagged for the design-questions list.) | n/a | **S0** |

### E-CODE — `code:` blocks

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-CODE-001 [obs] | Python exception in `code:` | `bad_code.pdl:0 - Python Code error: Traceback (most recent call last):` + **the interpreter's own frames** (`pdl_interpreter.py:2649 in call_python`) before the user's `File "<code-block>", line 1` | line 0 | **S1** |
| E-CODE-002 [obs] | `code:` never assigns `result` (**issue #386**) | **regressed since the issue was filed** — now a fully uncaught `AttributeError: 'types.SimpleNamespace' object has no attribute 'result'` traceback, because `result = my_namespace.result` sits *outside* the `try` at `pdl_interpreter.py:2657` | none | **S0** |
| E-CODE-003 [src] | shell command non-zero exit | `ValueError: command exited with non zero code: N` — raw `ValueError`, and `p.stderr` is separately `print`ed | none | **S1** |
| E-CODE-004 [src] | Jinja `lang: jinja` failure | `Jinja Code error: <repr>` | file:line | S2 |
| E-CODE-005 [src] | nested `lang: pdl` failure | `PDL Code error: <repr>` — inner diagnostic is `repr`-wrapped, destroying the inner location | file:line | **S1** |

### E-PARSER — output parsers (`parser:`)

Every one of these is raised with **`loc=None`**, so `generate` prints a bare message
with no file at all.

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-PARSER-001 [obs] | `parser: json` on non-JSON (**issue #387**) | `Attempted to parse ill-formed JSON: TypeError("'int' object is not subscriptable")` — **does not show the offending text**, and reports a Python type error for a *parse* failure | **none** | **S1** |
| E-PARSER-002 [obs] | `parser: jsonl` | `Attempted to parse ill-formed JSON: JSONDecodeError(...)` | **none** | **S1** |
| E-PARSER-003 [src] | `parser: yaml` | `Attempted to parse ill-formed YAML: <repr>` | **none** | **S1** |
| E-PARSER-004 [src] | `parser: csv` | `Attempted to parse ill-formed CSV: <repr>` | **none** | **S1** |
| E-PARSER-005 [obs] | invalid regex | `Fail to parse with regex (: error('missing ), unterminated subpattern at position 0')` — regex position 0 given, PDL position absent | **none** | **S1** |
| E-PARSER-006 [src] | named group absent | `No group named <g> found by <regex> in <text>` | **none** | S2 |

### E-MODEL — model / tool calls

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-MODEL-001 [obs] | unknown provider | **uncaught traceback** ending in `pdl.pdl_ast.PDLRuntimeError: Error during 'not_a_provider/nope' model call: litellm.BadRequestError: ...` — raised on the event-loop thread in `pdl_llms.py:66`, surfaced via a `concurrent.futures` callback, bypassing `generate` entirely | none | **S0** |
| E-MODEL-002 [src] | network failure | `model '<id>' encountered <repr(exc)> trying to <METHOD> against <URL>` | `block.pdl__location` | S2 |
| E-MODEL-003 [src] | OpenAI backend failure | `Error during '<id>' model call: <repr>` | `block.pdl__location` | S2 |
| E-MODEL-004 [src] | granite-io processor failure | `Error during processor (<p>) execution: <repr>` | `block.pdl__location` | S2 |
| E-MODEL-005 [obs] | malformed `input:` message (**issue #383**) | `i383.pdl:2 - Error during '<id>' model call: TypeError("'<class 'str'>' object is not a valid context")` — blames the *model call*; the real fault is a message with no `content`, which PDL could detect **before** dialling out | file:line (of the model block, not the bad message) | **S1** |

### E-LINT — `pdl-lint`

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-LINT-001 [obs] | schema error | `PDLParseError: ['lintprobe/unknown_field.pdl:5 - Field not allowed: parameterss']` — a **Python list repr** leaked into the console, because `PDLParseError.message` is a `list[str]` | file:line inside the repr | **S1** |
| E-LINT-002 [obs] | YAML error | full `yaml.parser.ParserError` traceback via `logger.exception` | none | **S0** |
| E-LINT-003 [obs] | Python syntax error in a `code:` block | full traceback ending in `SyntaxError: invalid syntax` with `File "<unknown>", line 1` — **the `.pdl` file is never named**, only the snippet's internal line | none | **S0** |
| E-LINT-004 [obs] | linting a file outside the detected project root | ` - ℹ️  SKIPPING <path> (in ignore list)` then `🎉  All files linted successfully 🎉`, **exit 0** — a false green, and the stated reason is wrong (it is not in the ignore list; `should_ignore` conflates "outside project root" with "ignored") | n/a | **S0** |

### E-RUST / E-GUI

| ID | Trigger | Current | Sev |
| --- | --- | --- | --- |
| E-RUST-001 [src] | any error in the Rust interpreter | `Box<dyn Error>` from a `format!` string, e.g. `Call arguments not a map: <Debug>` — **no location type exists in the Rust AST** | **S1** |
| E-GUI-001 [src] | any failure in the viewer's Run panel | `String(err)` written verbatim to an xterm, tracebacks included | **S1** |
| E-GUI-002 [src] | error blocks in a loaded trace | dropped from the timeline — `// TODO show errors in trace` (`view/timeline/model.ts:157`) | **S1** |

**Counts:** 14 × S0, 21 × S1, 22 × S2, 4 × S3.

---

## 3. The location-provenance story

This is the crux, as suspected. The short version: **PDL never has real source
positions.** It reconstructs approximate ones from a regex line-scan, keeps them in a
structure that cannot be safely moved between files, and threads them by hand through
every `process_*` function (41 `append()` call sites, plus a `loc` parameter on ~40 functions).

### 3.1 The pipeline

```
 .pdl text
   │
   ├─(a) yaml.safe_load ──────────────► plain dict/list/str   ← DROP #1: node marks discarded
   │
   └─(b) get_line_map(text) ──────────► {"['root','text','[2]']": 5, ...}   ← DROP #2: approximate
                                            │
        PdlLocationType(file, path=[], table)◄┘
                    │
   Program.model_validate(dict) ─────► pydantic AST with **no** position fields  ← DROP #3
                    │
   (on error) analyze_errors(schema, dict, loc) — re-walks the *raw dict*, rebuilding
              `path` with append(), then get_line(table, path)                  ← DROP #4
                    │
   runtime: `loc` passed as an explicit parameter to every process_*(),
            extended by append(loc, "field") at each descent                    ← DROP #5
                    │
            ├── include/import → parse_file() → fresh, correct table
            ├── execute_call   → **mixes callee file+path with caller table**   ← DROP #6 (bug)
            ├── _process_expr  → attaches field-level loc; Jinja's own offset discarded ← DROP #7
            ├── parse_result   → raises with loc=None                           ← DROP #8
            └── model backends → raise off-thread, bypassing the printer        ← DROP #9
                    │
   get_loc_string(loc) ──► "file:line - "   — `path` is **never rendered**      ← DROP #10
```

### 3.2 Each drop, precisely

**DROP #1 — `yaml.safe_load` discards node marks.** `pdl_parser.py:30`. PyYAML's
composer attaches `start_mark`/`end_mark` (line, column, buffer, pointer) to every node
and `safe_load` throws them away. A `SafeLoader` subclass overriding
`construct_mapping`/`construct_sequence` recovers line **and column** for every key and
value, exactly, for free. Nothing downstream currently expects this — which is why it is
the highest-leverage change available and also the one that needs a design decision
(§5.1).

**DROP #2 — the line map is a regex heuristic, not a parse.** `get_line_map`
(`pdl_location_utils.py:73-91`) splits on `\n` and, per line, guesses the field name via
`line.strip().split(":")[0].replace("-","").strip()`, the indent via `^ *`, and
array-ness via `startswith("-")`. Consequences, all reproduced:
- Comment lines are counted as structure. `comment.pdl` reports **line 2** for an error
  on **line 5** (E-EXPR-006). [obs]
- Flow style has no per-element granularity — a `[...]` list produces no entries for its
  items, so every element falls back (DROP #4) to the line of the enclosing key. Harmless
  when the flow sits on one line; wrong as soon as it spans several: a `text: [` opened on
  line 2 with the bad element on line 5 reports **line 2**. [obs]
- Keys are matched by *name only*, and `ret[str(path)] = line` **overwrites**: two
  sibling blocks with the same shape share a key, last one wins. [src]
- No column and no end position are computed at any point.
- `.replace("-","")` mangles any key containing a hyphen.

**DROP #3 — the AST carries no positions.** `pdl_ast.py:538` declares
`pdl__location: OptionalPdlLocationType = None`, and it is populated only at *runtime*,
in `process_block_body` (`pdl_interpreter.py:941`). The commented-out `set_location` /
`set_program_location` machinery at `pdl_parser.py:58-166` is a previous, abandoned
attempt to fix exactly this. Because the AST is position-free, static (schema) errors
cannot be reported off the AST at all — hence `analyze_errors` re-walking the raw dict in
parallel, and hence pydantic's own `ValidationError` (which knows precisely which union
branch and field failed) being discarded at `pdl_parser.py:41`.

**DROP #4 — `get_line` degrades silently.** `pdl_location_utils.py:102-107`: exact path
hit, else strip the last segment and recurse, else `0` for the empty path. So a missing
`append()` anywhere upstream produces a *plausible-looking ancestor line* with no
indication that it is imprecise, and a top-level block produces the literal `file:0`
seen in E-RUNTIME-004 and E-CODE-001.

**DROP #5 — manual threading.** `loc` is an explicit parameter on every `process_*`
function and is extended by `append(loc, seg)` at each descent. 41 `append()` sites across ~40 functions. This is
issue #203's complaint verbatim. Every omitted `append` is an invisible off-by-a-level
error thanks to DROP #4.

**DROP #6 — cross-file corruption in `execute_call` (a live bug).**
`pdl_interpreter.py:2752-2757`:

```python
fun_loc = PdlLocationType(
    file=closure.pdl__location.file,     # callee's file
    path=closure.pdl__location.path + ["return"],
    table=loc.table,                     # ← CALLER's line map
)
```

`PdlLocationType` bundles `(file, path, table)` where `table` is a *per-file* line map.
Building a location with one file's `path` and another file's `table` guarantees a lookup
miss, which DROP #4 then converts into a confident wrong answer. Reproduced: a function
defined at line 4 of `sub/imported.pdl` and called from `main_imp.pdl` reports
**`sub/imported.pdl:1`** (E-EXPR-004). `include` is unaffected because it re-parses and
uses the fresh `new_loc` wholesale.

This is the structural defect behind the whole class: `table` should not live inside a
location value. It belongs in a source registry keyed by filename.

**DROP #7 — Jinja's internal offsets are discarded.** `_process_expr`
(`pdl_interpreter.py:2043-2052`) catches `TemplateSyntaxError` and interpolates only
`{exc}`. `TemplateSyntaxError` carries `.lineno`, `.name`, `.source`. For a multi-line
template in a `|` block scalar, PDL reports the line of the *whole field* and drops the
line *within* it. There is also no mapping from an offset inside the expression string
back to a column in the `.pdl` file — which would need DROP #1 fixed first (the scalar's
start column).

**DROP #8 — output parsers raise with no location.** `parse_result`
(`pdl_interpreter.py:3082-3156`) raises `PDLRuntimeParserError(msg, source_exception=exc)`
— six sites, none passing `loc`. `generate` then prints the bare message. The whole
E-PARSER class has *no file name at all*. The fix is mechanical: `parse_result` is called
from sites that have `loc` in hand.

**DROP #9 — model errors bypass the printer.** `pdl_llms.py:66` raises inside
`async_generate_text`, executed on `state.event_loop` via `run_coroutine_threadsafe`. The
exception is re-raised from a `concurrent.futures` done-callback, on a different stack
from `generate`'s `try`. Result: a traceback, despite the `PDLRuntimeError` being
correctly constructed *with* a location (E-MODEL-001).

**DROP #10 — `path` is computed and then never shown.** `get_loc_string`
(`pdl_location_utils.py:94-99`) renders only `file:line`. `loc.path` — the exact block
path the rubric asks for, e.g. `['root','text','[2]','model','input']` — is carried all
the way to the print site and dropped. Rendering it as `text[2].model.input` requires
**no new plumbing whatsoever**. Cheapest high-value item in the project.

### 3.3 What survives today

| Path | File | Line | Column | Block path | Notes |
| --- | --- | --- | --- | --- | --- |
| YAML parse error | ✗ (says `<unicode string>`) | ✓ | ✓ | ✗ | Correct info, unreachable — traceback |
| Schema error, same file | ✓ | ~ | ✗ | in memory | Approximate line (DROP #2) |
| Runtime error, same file | ✓ | ~ | ✗ | in memory | `:0` at top level |
| Runtime error via `include` | ✓ | ~ | ✗ | in memory | Correct file; **no "included from" chain** |
| Runtime error via `import` + `call` | ✓ | **✗** | ✗ | in memory | **Wrong line** (DROP #6) |
| Inside a function body | ✓ | ~ | ✗ | in memory | **No call stack** |
| Jinja expression | ✓ | ~ (field-level) | ✗ | in memory | No offset inside the expression |
| Output parser | **✗** | ✗ | ✗ | ✗ | Nothing |
| Model call | ✓ | ~ | ✗ | in memory | Reaches the user as a traceback |
| Deprecated type warning | ✗ | ✗ | ✗ | ✗ | Nothing |
| `pdl-lint` | ~ (inside a list repr) | ~ | ✗ | ✗ | — |
| Rust interpreter | ✗ | ✗ | ✗ | ✗ | No location type exists |

---

## 4. Known-bad examples

### 4.1 From open upstream issues

| Issue | Title | Class | Status today |
| --- | --- | --- | --- |
| [#203](https://github.com/IBM/prompt-declaration-language/issues/203) | Location tracking | — | Open. "location tracking code is sprinkled throughout the interpreter … would be good to factor [it] out". Confirmed: §3.2 DROP #5. |
| [#202](https://github.com/IBM/prompt-declaration-language/issues/202) | Strengthen the error analyzer | E-SCHEMA-006/007 | Open. "We sometimes observe long error messages … the error analyzer is missing cases". Confirmed: the 700-char `oneOf` dump and the no-op fallback. |
| [#386](https://github.com/IBM/prompt-declaration-language/issues/386) | Python code without `result` | E-CODE-002 [obs] | Open and **regressed**. Filed as `foo.pdl:0 - Code error: AttributeError(...)`; today it is an uncaught traceback. |
| [#387](https://github.com/IBM/prompt-declaration-language/issues/387) | JSON parse errors should report the offending text | E-PARSER-001 [obs] | Open. Reproduces; message has also drifted to `TypeError("'int' object is not subscriptable")`. |
| [#383](https://github.com/IBM/prompt-declaration-language/issues/383) | `Invalid message type: <class 'str'>` | E-MODEL-005 [obs] | Open. Reproduces with drifted text: `TypeError("'<class 'str'>' object is not a valid context")`. |
| [#410](https://github.com/IBM/prompt-declaration-language/issues/410) | Traces contain absolute, non-normalised paths | E-RUNTIME-003 [src] | Open. Relevant: golden files must normalise paths (Phase 1). |
| [#411](https://github.com/IBM/prompt-declaration-language/issues/411) | ErrorBlocks lack timing information | E-GUI-002 [src] | Open. Adjacent. |

### 4.2 Mutated from `examples/`

| Source | Mutation | Result |
| --- | --- | --- |
| `examples/tutorial/for.pdl` | `for:` → `fro:` | `m_for.pdl:2 - Field not allowed: fro` — correct location, **no "did you mean `for`?"** despite a 1-character edit distance |
| `examples/tutorial/defs.pdl` | reference an undefined def | `Error during the evaluation of ${ … }: '…' is undefined` — no list of what *is* in scope |
| any example | add a leading `#` comment block | every subsequent reported line shifts (E-EXPR-006) |
| any example | `- "unterminated` | 13-frame traceback (E-PARSE-001) |

### 4.3 Existing test coverage to preserve or update

- `tests/test_line_table.py` — 420 lines, ~30 cases, asserts exact `file:line - message`
  strings against `tests/data/line/*.pdl`. **This is the de facto golden suite for
  locations** and will need updating in lockstep with any location fix. Note the first
  case already asserts `hello.pdl:0` twice — the `line 0` defect is currently
  *enshrined in a test*.
- `tests/test_errors.py` — asserts `analyze_errors` output sets directly.
- `tests/test_runtime_errors.py` — asserts exact `PDLRuntimeError.message` strings for
  Jinja and parser errors.
- `tests/test_type_checking.py`, `tests/test_parse.py`, `tests/test_linter.py`.
- `pyproject.toml [tool.pdl-lint].ignore` lists 8 `tests/data/line/*.pdl` files that are
  deliberately broken — the linter would otherwise fail the build on them.

---

## 5. Design questions — for your decision before Phase 2

These are the points where I would have to guess, so I am not guessing.

**5.1 Replace the regex line map with real YAML marks?**
DROPs #1 and #2 are one decision. A `SafeLoader` subclass that records `start_mark`/
`end_mark` per node gives exact line *and column* and kills E-EXPR-006, the flow-style
collapse, and the same-key overwrite in one move — and is the only way to satisfy rubric
item 1 (column + caret span). It changes `PdlLocationType`'s meaning, which the task says
to escalate. It is contained (`pdl_parser.py` + `pdl_location_utils.py`), adds no
dependency, and `get_line_map` can stay as a compatibility shim. **Recommendation: yes,
and it should be the first Phase-3 work item since most other fixes stack on it.**

**5.2 Move `table` out of `PdlLocationType`?**
`table` is per-file state living inside a per-node value; that is what makes DROP #6
possible. The fix is a source registry (`file → line map`) consulted at render time, with
`PdlLocationType` reduced to `(file, path)` — or `(file, line, col, path)` under 5.1.
`PdlLocationType` is exported from `pdl.pdl_ast`, appears in `exec_program`'s public
signature, and is serialised into trace JSON consumed by the viewer. **This is a public
API change.** Options: (a) change it and bump; (b) keep `table` as a deprecated,
optional field; (c) leave it and special-case `execute_call`. **Recommendation: (b)** —
fixes the bug, keeps the SDK and trace format working.

**5.3 Can E-SCHEMA-007 be fixed without restructuring `pdl-schema.json`?**
You anticipated this one. I believe **yes**, without touching the schema. `BlockType` is a
pydantic *discriminated* union — `pdl_ast.py:1601` uses `Discriminator(_block_tag)`. The
discriminator function already knows how to pick a branch from the keys present. So
`analyze_errors` can call it, name the intended block kind ("this looks like a `model`
block"), and report against *that one branch* instead of dumping 24 `$ref`s. Where no
branch matches, the right message is "no block kind matches these keys: `foo`; expected
one of `model`, `code`, `text`, …" — a name list, not a schema dump. **No restructuring
needed; confirm you agree before I commit the team to it.**

**5.4 Is the "Deprecated type syntax" warning in scope?**
It is a user-visible diagnostic (E-TYPE-006) with no location, so by the stated
definition it is in scope. But it fires on **successful** runs, so improving it changes
success-path *stderr*. Your hard constraint says never change success-path output.
**Which wins?** My reading: the constraint is about program semantics and stdout, and
adding a location to a stderr warning is squarely the project's purpose. Confirm.

**5.5 Should silent-acceptance cases become errors?**
E-PARSE-003 (duplicate YAML key, last wins, exit 0) and E-RUNTIME-012 (`for:` over a
string iterates characters, exit 0) are the worst *user* experiences in the inventory —
no diagnostic at all — but fixing them changes semantics, which is forbidden. Options:
warn on stderr and keep exit 0; error; or leave alone and document. **Recommendation:
warn-only, since a warning is a diagnostic and not a semantic change.**

**5.6 Structured diagnostics for the viewer and the Rust interpreter?**
The React timeline drops error blocks entirely (E-GUI-002) and the Rust interpreter has
no location concept (E-RUST-001). Both would benefit from an emitted machine-readable
diagnostic record (id, severity, file, span, path, message, notes) alongside the human
text — which would also make the Phase-1 golden files structural rather than
string-diff. That is a bigger commitment than "improve messages". **Scope in, or defer?**

**5.7 Is the Rust interpreter in scope at all?**
It is a second implementation with 23 ad-hoc error strings and no location type. Bringing
it to parity is a project in itself. **Recommendation: out of scope for now; note the
divergence and revisit.**

**5.8 Exit codes.**
Everything currently exits 1 — but for S0 cases that is Python's default for an uncaught
exception, indistinguishable from a deliberate failure. Rubric item 5 asks for a "stable
exit code". Do you want distinct codes per class (e.g. 2 = parse/schema, 1 = runtime), or
is "always 1, never a traceback" the target? Distinct codes would be a **public
behaviour change** for anyone scripting `pdl`. **Recommendation: keep 1, and treat
"no traceback ever reaches the user" as the invariant.**

---

## 6. Suggested Phase-3 priority order

From the rubric-baseline perspective, ordered by (severity × blast radius) and respecting
dependencies:

1. **E-CLI-001/002, E-PARSE-001/002/005** — catch at the boundary. Kills 5 × S0 in one
   contained change to `pdl.py` + `pdl_parser.py`, and PyYAML hands us line+column free.
2. **E-MODEL-001** — the off-thread traceback. S0, and it makes the whole E-MODEL class
   reachable by the formatter.
3. **E-CODE-002** — move `result = my_namespace.result` inside the `try`. One line,
   closes issue #386, S0.
4. **E-RUNTIME-001/002** — catch `OSError` around `include`/`import`. S0.
5. **E-LINT-002/003/004** — the linter's false green and its tracebacks. S0.
6. **E-RUNTIME-007** — the missing `f`-prefix. Two characters.
7. **DROP #10** — render `loc.path` as a block path. No plumbing, satisfies half of
   rubric item 1 everywhere at once.
8. **§5.1 real YAML marks**, then **§5.2 source registry**, then **E-EXPR-004/006**
   (which those two fix).
9. **E-PARSER-001…006** — thread `loc` into `parse_result`; include the offending text.
10. **E-SCHEMA-006/007** via §5.3, then **E-SCHEMA-002** ("did you mean") and
    **E-EXPR-001** ("in scope here").

Items 1–6 are independent of each other and of the location rework, so they parallelise
cleanly across worktrees. Items 7–10 serialise on the location work.
