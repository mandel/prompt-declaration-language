# PDL Error Reporting — Phase 0 Inventory

Status: **recon complete; Phase 1 harness built.** No behaviour has been changed.
Every taxonomy entry with a corpus reproducer is now pinned by a golden transcript under
`tests/errors/corpus/`, scored against [`RUBRIC.md`](RUBRIC.md), and tabulated in
[`BASELINE.md`](BASELINE.md). Phase 1 corrected one Phase-0 misreading (DROP #9) and
added two entries that only surfaced once reproducers were run: **E-CLI-005** and
**E-SCHEMA-010**.

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
| `pdl_llms.py` | `:57-70`, `:99` | LiteLLM errors. `generate` prints these correctly; the defect is that `update_end_nanos` (`:99`) re-raises the same exception from an unguarded `concurrent.futures` done-callback, so every model failure is reported twice — once as a diagnostic, once as a traceback. |
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
| E-CLI-005 [obs] | any failure under `python -m pdl.pdl` | the diagnostic is correct, but the process **exits 0**. `src/pdl/pdl.py` ends in a bare `main()` with no `sys.exit`, so the module entry point always reports success; only the setuptools console script wraps it. CI invoking `python -m pdl.pdl` can never fail | file:line | **S0** |

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
| E-SCHEMA-005 [obs] | value not in enum | **reachable since Phase-3 item 10**, and pinned by `tests/errors/corpus/E-SCHEMA-006/`. The branch was unreachable because the analyzer's scalar-union arm set its "matched" flag from an alternative's `type` and stopped there, and `ParserType`'s first alternative carries both `type: string` and the `enum`, so any string passed. Now: ``prog.pdl:2 - `xml` is not a valid value for `parser:` `` with a caret, the accepted values read out of the schema, and a `help:` | file:line, block path, excerpt, caret | S1 |
| E-SCHEMA-006 [obs] | analyzer finds nothing | **Fixed by Phase-3 item 10** for both of the triggers this row named. `parser: xml` is now E-SCHEMA-005 above; `lang: ruby` is now ``prog.pdl:1 - `ruby` is not a language PDL can run``, from `_code_block_tag`'s miss branch, and `tests/test_schema_unions.py` pins that an arbitrary tag (`kind: totally-made-up`, `platform: bedrock`, `lang: [a]`) can never reach a bare table lookup and raise `KeyError`. The `errors == []` branch itself survives, is still reachable, and now says so instead of `The file PDL enum_bad.pdl does not respect the schema.`: it is pinned by `tests/errors/corpus/E-SCHEMA-006-fallback/`, whose reproducer is `retry: {exceptions: 5}` — one arm of `RetryConfiguration.exceptions` renders as the empty schema `{}`, which matches anything, so the analyzer is right by the schema and wrong by the validator. Found by mutating every program under `tests/data` and `examples`: 6 hits in 10 920 mutations, all on that one field | none, deliberately — see the entry's notes | **S1** |
| E-SCHEMA-007 [obs] | dict fails every union branch (`- foo: bar` in a `text`) | **Fixed by Phase-3 item 10.** Was a 1-line, 700-char dump of 24 raw `$ref`s. Now `this is not a PDL block: nothing here says what it does`, a caret on each unrecognised key, the 24 fields that do name a block kind, and either a near-miss or the `data:` rewriting. Reached through the discriminator `pdl_ast` already defines, including where `BlockType` is behind an inline union (`text: {a: 1}`) | file:line, block path, excerpt, caret | **S1** |
| E-SCHEMA-008 [obs] | same, for `contribute` | `contrib_bad.pdl:3 - {'result': 1, 'context': 2} should be of type: {'anyOf': [{'$ref': '#/$defs/ContributeTarget'}, ...]}` | file:line | **S1** |
| E-SCHEMA-009 [obs] | list/mapping shape mismatch — and, on one shape of schema, **a crash inside the analyzer** | **Fixed. Four behaviours, four corpus entries, three arms of `analyze_errors`.** (4) was the S0: `retry: {jitter: [1, 2, 3]}` left the user with `KeyError: 'items'`, 76 lines of stderr, two chained tracebacks and the raw pydantic `ValidationError` the analyzer had been called to translate. The array arm subscripted `schema["items"]` after `is_array` had answered on `type` alone, and `ExpressionFloatOrFloatFloat` (the type of `retry: {jitter:}`) renders one alternative as `type: array` + `prefixItems` and **no `items`**. `analyze_list` now reads `prefixItems`, `items`, `minItems` and `maxItems` as the separate keywords they are: a `prefixItems` schema is a tuple, so the answer is a *length* error — ``the list in `jitter:` should have exactly 2 items, but it has 3`` with a caret on the third — and not a guarded subscript that would have said nothing. `hygiene_traceback_expected` is gone from that entry. (1)–(3) each showed the offending value as `str(data)` — a Python `repr` of parsed YAML for the two structural ones — stated no expectation, and offered no edit. All three now name the construct, say what it takes, and offer a rewrite of the user's own value checked against the schema before it is printed: ``contribute: [result]`` for the scalar, ``defs: {greeting: hi}`` for `defs:` written as a list of definitions, ``fallback: {text: recovering}`` for `fallback:` written as a sequence. `should not be a list` is gone with (3): `discriminated_union` recognises `BlockType` by identity, so the field can be said to take one block. `object` is `mapping` throughout, which is the word the documentation uses. One further false complaint had to go with the crash: `{"type": "number"}` maps to `float` and `isinstance(1, float)` is `False`, so `jitter: [1, 2]` — a program that runs — was accused of holding two things that are not numbers; `is_of_type` reads `number` as JSON Schema and pydantic do | file:line + block path + excerpt + caret; for (2) and (3) the line moved from the key's to the value's, via the first child the marks record (`value_location`) | **S0** (was; the three shape messages alone were S2) |
| E-SCHEMA-010 [obs] | a program with several schema faults | **Open.** The same diagnostics, **in an order that changes between processes**: `analyze_errors`' object arm builds its list from `set(required) - set(data)` and `set(data) - set(properties)`, so message order depends on `PYTHONHASHSEED`. Verified across six seeds, re-verified on every run by `test_order_instability_is_real`. Phase-3 item 10 did **not** fix this; it changed the corpus reproducer, because the old one (`texts`/`foo`/`bar` at the top level) is now answered by one no-block diagnostic and one message cannot come out in the wrong order. The reproducer is that program with the near-miss key corrected — which is what its own `help:` tells the user to do — plus a third unknown field, two not being enough to make the order move | file:line, block path | **S1** |

### E-EXPR — Jinja expression evaluation

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-EXPR-001 [obs] | undefined variable | `jinja_undef.pdl:3 - Error during the evaluation of ${ missing_var }: 'missing_var' is undefined` — no "in scope here: …" listing and no near-miss suggestion | file:line | S2 |
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
| E-RUNTIME-007 [src] | malformed `contribute` entry | `Contributions are expected to be strings or dictionaries of length 1 but got {elem}` — **missing `f`-prefix at `pdl_interpreter.py:1875` and `:1894`; the literal text `{elem}` is printed** | file:line | **S1** |
| E-RUNTIME-008 [src] | `lang:` unsupported at runtime | `Unsupported language: <lang>` — **unreachable from a `.pdl` file**: the schema check rejects an unknown `lang:` first, and since Phase-3 item 10 it does so with a message of its own (see E-SCHEMA-006). Still reachable from a `Block` built in Python | file:line | S2 |
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
| E-CODE-006 [obs] | exception raised inside a function that *another* `code:` block defined — the `PDL_SESSION` idiom, as in `examples/rag/tfidf_rag.pdl` | `prog.pdl:9 - code block raised ZeroDivisionError: division by zero`, the failing block's own line under a caret, then `note: raised inside \`embed\`, line 2 of another \`code:\` block, which this block called.` The other block's frames are named in prose and never indexed into this block's source; `88174ff` fixed the regression that printed an innocent line of the wrong block under a caret. The other block still cannot be named or located | line of the enclosing `code:` key | S2 |

### E-PARSER — output parsers (`parser:`)

**Phase-3 item 9 is delivered** (spec `specs/E-PARSER.md`). Every one of these used to be
raised with **`loc=None`**, so `generate` printed a bare message with no file at all — no
location prefix and no `  in <block path>` line — and none showed the offending text
(issue #387). `parse_result` now takes a keyword-only `loc` and the call site passes
`append(loc, "parser")`, threaded *into* the callable because `lazy_apply` defers the
parser and the exception surfaces in `generate`. The series moved **29/90 → 80/90**, and
`E-PARSER-007` is a seventh entry added by the same change.

The rows below describe the diagnostics **after** the fix; `Current message` is what a
user sees today. Three silent failures found while specifying this item were **not** fixed
by it and are recorded in
[7.10](#710-three-silent-failures-in-parse_result--one-partly-closed-two-left-open) — none of
them may become an error without the owner's sign-off under 5.5. The first, malformed CSV,
has since had that sign-off — for the unclosed-quote class only, the rest of it staying
silent by decision; the other two remain open.

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-PARSER-001 [obs] | `parser:` on a value that is not text (the branch **issue #387**'s reproducer reaches) | `` prog.pdl:4 - `parser: json` needs text, but this block produced an integer ``, then the rule and `` help: remove `parser: json`; the block's result is already an integer. `` The type is named in PDL's vocabulary (`integer`, not `int`) and the value is shown inline only when `json.dumps` of it is ≤ 40 characters. Was `TypeError("'int' object is not subscriptable")` — `json_repair` subscripting a non-string, reported as a JSON parse failure | file:line + block path | S1 |
| E-PARSER-002 [obs] | `parser: jsonl` on a line that is not JSON | `` prog.pdl:4 - `parser: jsonl` could not parse line 2 of the block's output ``, an `output:2 \| oops` excerpt row with a caret at `exc.colno` labelled `Expecting value`, the rule, the `output:N` caveat and an action. A second branch detects that the whole output is one JSON document and suggests `parser: json`. Was `Attempted to parse ill-formed JSON: JSONDecodeError('Expecting value: line 1 column 1 (char 0)')` — the wrong parser named, and a position that read `line 1` whichever line failed | file:line + block path | S1 |
| E-PARSER-003 [obs] | `parser: yaml` on ill-formed YAML | `` prog.pdl:2 - `parser: yaml` could not parse the block's output `` with an `output:1` excerpt and a caret at PyYAML's `problem_mark`, labelled with its `problem`. The marks are read directly, as `yaml_diagnostic` already does for `.pdl` files; PyYAML's own snippet renderer — which names a nonexistent file, `in "<unicode string>"` — is never used. Was `repr(exc)`, printing the two `Mark` objects as memory addresses that differed on every run | file:line + block path | S1 |
| E-PARSER-004 [obs] | `parser: csv` over the field-size limit | `` prog.pdl:2 - `parser: csv` cannot read a field longer than 131072 characters ``, an excerpt row with no caret (a `csv.Error` has a row and no column), a `note:` stating the size seen and why it is a single field, and a conditional action. Was `Attempted to parse ill-formed CSV: Error(...)`, which called a well-formed input `ill-formed` and misdiagnosed a resource limit as a syntax error | file:line + block path | S1 |
| E-PARSER-004 [obs] **unclosed quote** — the decision-5.5 change of 7.10 finding 1 | `parser: csv` on a quoted field that is never closed. **Exited 0 with a wrong value before**: every remaining line was swallowed into the open field | `` prog.pdl:5 - `parser: csv` found a quoted field that is never closed ``, an `output:2 \| "unterminated,1` excerpt with a caret on the opening quote labelled `this quote is never closed`, a `note:` counting the lines that were swallowed into the field, and a two-clause `help:` whose clauses were both executed. The caret comes from `_unclosed_quote_position`, which uses `csv` as its own oracle rather than `reader.line_num` (the last line *consumed*, and so the wrong line for any multi-line case) or a re-implementation of the dialect. **This class only**: text after a closing `"` is detected and then deliberately tolerated, because rejecting it would break working programs over a trailing space, so it is re-parsed leniently and returned. Pinned by `E-PARSER-004-unterminated-quote`, and the tolerated case by `E-PARSER-004-after-quote` | file:line + block path | S1 |
| E-PARSER-005 [obs] | an invalid regular expression, in **any** of the five modes | `` prog.pdl:3 - `regex:` is not a valid regular expression ``, located at `parser.regex` rather than at `parser:`, with the pattern in a `regex:1 \| (` gutter and a caret from `re.error.lineno`/`.colno`. `` help: close the group, or write `regex: '\('` to match a literal `(`. `` — single quotes, because `\(` is not a valid escape in a double-quoted YAML scalar. Was `Fail to parse with regex (: error('missing ), unterminated subpattern at position 0')`. **`mode: split` and `mode: findall` compiled inside `re.split`/`re.findall` with no handler at all and reached the user as a raw traceback**; `_compiled_regex` makes compilation an explicit step for all five modes, so they get this diagnostic too. That crash was found during implementation and has no corpus entry of its own | file:line of `parser.regex` + block path | S1 |
| E-PARSER-006 [obs] | `spec:` names a group the regex does not define | `` prog.pdl:5 - the `regex:` pattern has no group named `second` ``, located at `parser.spec.second`, with a **file** excerpt and caret — the one entry in the series whose evidence is the file, because the fault is static and nothing about the output is relevant. The pattern's own groups are listed from `m.re.groupindex`, ordered by group number, and the `help:` has four branches (only group / near miss / list of alternatives / no named group). Was `No group named second found by (?P<first>\w+) in hello`, which blamed the matched *text* and never named the group the pattern does define. The neighbouring silent failure — a `regex:` that does not match returns `None` at exit 0 — is untouched; see 7.10 | file:line of `parser.spec.<name>` + block path | S2 |
| E-PARSER-007 [obs] | `parser: {pdl: ...}` — a declared AST node whose implementation is a `TODO` | `` prog.pdl:2 - `parser:` with a `pdl:` sub-program is not implemented ``, the rule, `note: this is a gap in PDL itself, not a mistake in this program.` and the working alternatives. Was `assert False, "TODO"`: an `AssertionError` is neither a `PDLRuntimeError` nor a `PDLParseError`, so it escaped `generate`'s handlers as a **raw traceback** at exit 1. Under `python -O` the assertion is compiled out and the branch falls through to `return result` with `result` unbound — a different and worse failure, which is why the fix is a `raise` and not a better assertion. The diagnostic says the form is not implemented and nothing more: what it *would* do is not knowable from a `TODO` | file:line + block path | **S0** (was) |

### E-MODEL — model / tool calls

| ID | Trigger | Current message | Location | Sev |
| --- | --- | --- | --- | --- |
| E-MODEL-001 [obs] | unknown provider | `prog.pdl:2 - Error during '<id>' model call: litellm.BadRequestError: ...` — **correct, located, and then followed by a two-part traceback**: `exception calling callback for <Future ...>` plus ~20 frames. `generate` does handle it; the duplicate arrives separately from a `concurrent.futures` done-callback in `pdl_llms.py:99` | file:line, then buried | **S0** |
| E-MODEL-002 [obs] | network failure | `prog.pdl:2 - model '<id>' encountered ConnectError(...) trying to POST against <URL>` — genuinely informative, then the same duplicated traceback as E-MODEL-001 | file:line, then buried | **S0** |
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

**Counts:** 18 × S0, 23 × S1, 24 × S2, 3 × S3 — 68 rows. E-SCHEMA-005 was the one
unclassifiable row, on the grounds that its branch was unreachable in practice; Phase-3
item 10 made it reachable, and it is scored S1 with the rest of its class.

These were `14 / 21 / 22 / 4` until they were re-counted: wrong on every number, and
summing to 61 against a table that had 67 rows at the time. Two severity cells carried
trailing prose that also made the column impossible to tally mechanically; the prose has
moved into the message column, so the four numbers above can now be checked by counting
the last column of every `| E-…` row.

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
            └── model backends → printed correctly, then duplicated as a
                                 traceback by a futures callback            ← DROP #9
                    │
   located_message(loc, msg) ──► "file:line - msg" + "\n  in <path>"      ← DROP #10 closed
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

**DROP #8 — output parsers raise with no location. *Fixed by Phase-3 item 9.***
`parse_result` raised `PDLRuntimeParserError(msg, source_exception=exc)` — six sites, none
passing `loc` — and `generate` printed the bare message, so the whole E-PARSER class had
*no file name at all*. It was mechanical only in the sense that the caller had `loc` in
hand: the parser runs through `lazy_apply`, so the exception surfaces at
`future_result.result()` in `generate` and a `try` around the call site would never have
seen it. The location had to travel *into* the callable, as
`partial(parse_result, block.parser, loc=append(loc, "parser"))`, which is the shape the
`spec:` checker four lines below already used.

**DROP #9 — model errors are printed correctly and then dumped again as a traceback.**
Corrected during Phase 1: my first reading of this path was wrong. `generate` *does*
catch the `PDLRuntimeError` and print a properly located diagnostic. The damage is a
*second*, duplicate report: `pdl_llms.py:66` raises inside `async_generate_text` on
`state.event_loop`, and `update_end_nanos` (`pdl_llms.py:99`) calls `future.result()`
from a `concurrent.futures` done-callback that has no handler, so Python prints
`exception calling callback for <Future ...>` followed by ~20 frames. The user sees the
right answer and then a crash report for the same event. Fixing this is about the
callback, not the printer.

**DROP #10 — `path` is computed and then never shown. CLOSED by Phase-3 item 7.**
`get_loc_string` rendered only `file:line`; `loc.path` — the exact block path the rubric
asks for — was carried all the way to the print site and dropped. It is now rendered by
`located_message` (`pdl_location_utils.py`) as a `  in text[2].model.input` line under the
header, the spelling `pdl_diagnostics.render` already used, and every legacy site calls it:
`generate`, the retry banner, `pdl_schema_error_analyzer`'s nine sites,
`pdl_schema_validator`, and `optimize/optimizer_evaluator`. It needed no new plumbing, as
predicted. What it bought is measured in 7.9, and is less than §6 claimed.

### 3.3 What survives today

The **Block path** column is current as of Phase-3 item 7; the other columns are the
Phase-0 snapshot and belong to the items that own them. "in memory" meant the path was
computed and never printed — DROP #10 — and every one of those cells is now a rendered
`  in <path>` line, except where the path is `[]` (a single-block program, whose one block
is the whole document) and nothing is rendered at all.

| Path | File | Line | Column | Block path | Notes |
| --- | --- | --- | --- | --- | --- |
| YAML parse error | ✗ (says `<unicode string>`) | ✓ | ✓ | ✗ | Correct info, unreachable — traceback |
| Schema error, same file | ✓ | ~ | ✗ | ✓ | Approximate line (DROP #2) |
| Runtime error, same file | ✓ | ~ | ✗ | ✓ (`[]` at top level) | `:0` at top level |
| Runtime error via `include` | ✓ | ~ | ✗ | ✓ | Correct file; **no "included from" chain** |
| Runtime error via `import` + `call` | ✓ | **✗** | ✗ | ✓ | **Wrong line** (DROP #6) |
| Inside a function body | ✓ | ~ | ✗ | ✓ | **No call stack** |
| Jinja expression | ✓ | ~ (field-level) | ✗ | ✓ | No offset inside the expression |
| Output parser | **✗** | ✗ | ✗ | ✗ | Nothing — raises with `loc=None` (DROP #8) |
| Model call | ✓ | ~ | ✗ | ✓ | Reaches the user as a traceback |
| Deprecated type warning | ✗ | ✗ | ✗ | ✗ | Nothing |
| `pdl-lint` | ~ (inside a list repr) | ~ | ✗ | ✓ (schema errors) | — |
| Rust interpreter | ✗ | ✗ | ✗ | ✗ | No location type exists |

---

## 4. Known-bad examples

### 4.1 From open upstream issues

| Issue | Title | Class | Status today |
| --- | --- | --- | --- |
| [#203](https://github.com/IBM/prompt-declaration-language/issues/203) | Location tracking | — | Open. "location tracking code is sprinkled throughout the interpreter … would be good to factor [it] out". Confirmed: §3.2 DROP #5. |
| [#202](https://github.com/IBM/prompt-declaration-language/issues/202) | Strengthen the error analyzer | E-SCHEMA-006/007 | Open. "We sometimes observe long error messages … the error analyzer is missing cases". Confirmed: the 700-char `oneOf` dump and the no-op fallback. |
| [#386](https://github.com/IBM/prompt-declaration-language/issues/386) | Python code without `result` | E-CODE-002 [obs] | Open and **regressed**. Filed as `foo.pdl:0 - Code error: AttributeError(...)`; today it is an uncaught traceback. |
| [#387](https://github.com/IBM/prompt-declaration-language/issues/387) | JSON parse errors should report the offending text | E-PARSER-001 [obs] | **Partly addressed by Phase-3 item 9.** Every parser that raises now shows the offending line in an `output:N` gutter with a caret. The issue's own `parser: json`-over-prose case is *not* a raise at all: `json_repair` repairs rather than raises and returns `''` at exit 0 (7.10, finding 3), which is a decision-5.5 semantic change and still open. |
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

## 5. Design decisions

These were open questions at the end of Phase 0 recon. All seven were decided with the
project owner on 2026-08-06; recorded here as the standing contract for Phases 1–3.
Where a decision overrides one of the project's original hard constraints, that is
called out explicitly.

**5.1 + 5.2 — Real YAML marks, and `table` removed from `PdlLocationType`. DECIDED: do
both, no compatibility shim.**
Replace the regex line map (DROP #2) with a `SafeLoader` subclass that records
`start_mark`/`end_mark`, giving exact line **and column** for every node. Move the
per-file line data into a source registry keyed by filename, consulted at render time.
`PdlLocationType` becomes `(file, line, col, path)` with **no `table` field**.

> **This is a breaking public API change**, accepted deliberately. Consequences that are
> in scope and must be delivered, not worked around:
> - `PdlLocationType` is exported from `pdl.pdl_ast` and appears in `exec_program`'s
>   signature — the Python SDK surface changes.
> - `PdlLocationType` is serialised into trace JSON, so the **trace format changes** and
>   `pdl-live-react` needs a matching change plus a version bump.
> - `tests/test_line_table.py` (~30 cases) and any golden files pinning `file:line`
>   strings will need updating in lockstep.
>
> This resolves DROP #1, #2, #4 and #6 at the root, and is the only route to columns and
> caret spans (rubric item 1). It is a prerequisite for E-EXPR-004 and E-EXPR-006.

**5.3 — Schema union errors fixed via the existing discriminator; `pdl-schema.json` is
NOT restructured. DECIDED.**
`BlockType` is a pydantic *discriminated* union: `pdl_ast.py:1463` defines `_block_tag`
and `:1515` defines `_BLOCK_KIND_OF_FIELD`, which together pick a branch from the keys
present. `analyze_errors` calls that to name the intended block kind ("this looks like a
`model` block") and reports against **that one branch** instead of dumping 24 `$ref`s.
Where no branch matches, the message is a *name list* — "no block kind matches these
keys: `foo`; expected one of `model`, `code`, `text`, …" — never a schema dump. Fixes
E-SCHEMA-006 and E-SCHEMA-007 with no change to the generated schema artifact.

**5.4 — The deprecated-type-syntax warning is IN scope. DECIDED.**
The hard constraint "never change success-path output" is read as covering program
**semantics and stdout**. Adding a location to, and de-duplicating, a *stderr* warning is
squarely the project's purpose. E-TYPE-006 gets a file:line and a block path. The same
rule applies to any other success-path stderr warning discovered later.

**5.5 — Duplicate YAML keys and `for:` over a string become ERRORS. DECIDED.**

> **This overrides the "never change program semantics" hard constraint**, knowingly.
> Programs that today exit 0 will exit 1.

Blast radius was measured before deciding, not assumed: across ~~**205 `.pdl` files**~~ —
**265**, see the corrections below — in this repository there are **0 duplicate-key
sites**, and among **39 `for:` blocks** none binds a string literal. Nothing in-tree
breaks. The residual risk is to user programs outside the repository — accepted, and it
should be called out in release notes. Resolves E-PARSE-003 and E-RUNTIME-012, the two
worst entries in the catalogue.

> **E-PARSE-003 has since landed**, with the owner's explicit sign-off: a mapping key
> written more than once is an error, where PyYAML's `construct_mapping` assigned into a
> dict and kept the last value. `text: hello` / `text: world` printed `world` at exit 0
> and said nothing; it now exits 1.
>
> **The file count was stale twice over and is corrected here for the third time.** The
> 205 above missed files, the csv note below raised it to **263**, and a re-scan on the
> tree this change landed on finds **265** `.pdl` files: 261 parse, and 4 are corpus
> reproducers that are deliberately broken YAML. The measurement was re-run rather than
> carried over, with a duplicate-detecting `SafeLoader` over every one of them, and it
> finds **1** duplicate-key site — `tests/errors/corpus/E-PARSE-003/prog.pdl`, the
> reproducer that exists to demonstrate the bug. The only duplicate key in the repository
> is the one documenting that duplicate keys are wrong — which is also the whole of the
> difference from the **0** claimed above, measured before the corpus existed. No `.pdl`
> under `examples/` or `tests/data/` changes its exit code: all **170** were run twice before the change to
> screen for self-nondeterminism, none was, and none moved after it.
>
> *(Re-running that scan on the tree **after** the change now finds four sites, not one:
> the three new corpus reproducers `E-PARSE-003`, `-nested` and `-repeated` are the
> difference, and they are meant to be there. The blast-radius figure is the pre-change
> one, which is the tree a user's programs were written against.)*
>
> **Two things about the shape of the change are worth keeping.** The check is in
> `load_with_marks` and not in the constructor, because that is the last moment at which
> *both* occurrences exist as nodes with marks of their own — which is what lets the
> diagnostic say "this one, and the earlier one whose value it replaces" instead of
> "there is a duplicate". And the exception is deliberately **not** a `yaml.YAMLError`
> and its message never says "not valid YAML": PyYAML parses the document without
> complaint, so a user who checked the same file with another YAML tool would be told it
> is fine, and a diagnostic contradicting the tool beside it teaches distrust. It is a
> `PDLParseError`, so every existing handler keeps matching.
>
> **Scope excludes data files, deliberately.** `pdl.py:253` and `:269` call
> `yaml.safe_load` for `--data` and `-f` scope files, and they are untouched: those carry
> data values rather than program text, and extending the rule to them is a separate
> decision with a blast radius of its own that nobody has measured. The asymmetry is
> stated in the release note rather than left to be discovered.
>
> `for:` over a string (E-RUNTIME-012) remains the one part of this decision not yet
> implemented.

> **A third change has since landed under this decision**, with the owner's explicit
> sign-off: `parser: csv` rejects a quoted field that is never closed, instead of
> swallowing the rest of the output into it and returning a wrong parse at exit 0.
>
> Its blast radius was measured the same way and is smaller than the two above. Across
> **263 `.pdl` files** in this repository — the figure that supersedes the 205 quoted
> above, which was measured earlier and on a `--include=*.pdl` grep that also missed
> non-`.pdl` call sites — exactly **one** uses `parser: csv`, plus **one** inline program
> in `tests/test_parser.py:183`. Both are unaffected. Nothing in-tree breaks; the residual
> risk is to user programs outside the repository, accepted and called out in the release
> note.
>
> **The scope was cut during implementation and that is the part worth reading**: the first
> version also rejected text after a closing `"`, which `strict` cannot be asked to
> separate, and which rejects a *trailing space* — breaking working programs to fix a parse
> that was usually right. The owner narrowed it to the unterminated-quote class alone, at
> the cost of PDL returning a parse the standard library flagged. Both the ruling and its
> cost are in [7.10](#closing-finding-1s-worst-class-what-changed-and-what-was-left-alone).
> Findings 2 and 3 of 7.10 are the remaining candidates and have **not** been decided.

**5.6 — Structured diagnostic records, with a renderer on top. DECIDED.**
Every diagnostic becomes a record — `id`, `severity`, `file`, `span`, `block path`,
`message`, `notes`, `suggestions` — and a renderer turns it into the human text. Three
consequences that reshape the earlier plan:
- **Phase 1 changes.** Golden files diff the **structured record** as well as the
  rendered stderr, so rewording a message does not churn every golden.
- Fixes E-GUI-002 (the viewer currently drops error blocks:
  `view/timeline/model.ts:157`, `// TODO show errors in trace`).
- Gives the Rust interpreter a concrete target to converge on if it is ever brought in.

The trace contract is already being opened by 5.2, so this rides along on the same
version bump rather than costing a second one. *(Superseded in part: the trace format
did not change and there is no version bump — 7.6 and 7.8.)*

**5.7 — The Rust interpreter is OUT of scope. DECIDED.**
`pdl-live-react/src-tauri/src/pdl/interpreter.rs` stays as-is: 23 ad-hoc `format!` error
strings, no location type. Documented as a known second implementation at parity zero
(E-RUST-001), to revisit once the Python side is done. No Rust role in the Phase-2 team,
no Rust toolchain in the harness.

**5.8 — Exit code stays `1`. DECIDED.**
No per-class codes; no opt-in flag. The rubric's "stable exit code" requirement is
satisfied by a stronger invariant instead:

> **Invariant: no Python traceback ever reaches the user.** Every failure exits `1` with
> a formatted diagnostic. The Phase-1 harness asserts this globally, for every corpus
> entry, independently of the per-entry golden.

This is the acceptance test for all 14 S0 traceback entries and costs nothing for anyone
scripting `pdl`.

---

## 6. Phase-3 priority order

Revised to reflect §5. Ordered by (severity × blast radius), respecting dependencies.

**Item 0 is new and is a direct consequence of 5.1/5.2/5.6.** The structured-record
type, the renderer, the source registry and the YAML-marks loader are one coherent
foundation that every later item builds on. It does not fit "one error ID per commit"
because it belongs to no single error ID. It should land first, on its own branch, as an
explicitly-flagged public-API/trace-format change, with `tests/test_line_table.py` and
the viewer updated in the same series.

0. **Foundation** — diagnostic record + renderer (5.6); YAML-marks `SafeLoader` and
   source registry, `PdlLocationType` → `(file, line, col, path)` (5.1/5.2); the matching
   `pdl-live-react` change. Everything below assumes it. *No trace format bump: the trace
   format turned out not to change, and a version field was declined — see 7.8.*

Then, independent of each other and parallelisable across worktrees:

1. **E-CLI-001/002/003/005, E-PARSE-001/002/005** — catch at the boundary. Kills 5 × S0
   in a contained change to `pdl.py` + `pdl_parser.py`; PyYAML's marks are already there.
2. **E-MODEL-001** — the off-thread traceback in `pdl_llms.py:66`. S0, and it is what
   makes the whole E-MODEL class reachable by the formatter at all.
3. **E-CODE-002** — move `result = my_namespace.result` inside the `try` at
   `pdl_interpreter.py:2657`. One line, closes issue #386, S0.
4. **E-RUNTIME-001/002** — catch `OSError` around `include`/`import`. S0.
5. **E-LINT-002/003/004** — the linter's false green and its tracebacks. S0.
6. **E-RUNTIME-007** — the missing `f`-prefix at `pdl_interpreter.py:1875` and `:1894`.
   Two characters.
7. **Block paths everywhere** (DROP #10) — `get_loc_string` renders only `file:line` and
   discards `loc.path`. Under the foundation this is a renderer change. *Delivered.*
   ~~It satisfies half of rubric item 1 across all ~70 IDs at once.~~ **That claim was
   wrong and is struck; the measured effect is in 7.9.** It cannot touch "all ~70 IDs":
   19 of the 49 corpus entries never render a location prefix, and 6 of the 37 prefixes
   that do have an empty path. It is not "half of rubric item 1" either — Location 2
   needs an *accurate* line as well as a path, and a third of the entries it changed are
   at 1 for a coarse line that no path can fix.

Then, serialised on the foundation:

8. **E-EXPR-004 and E-EXPR-006** — the cross-file wrong-line bug and the comment-shift
   bug, both fixed by 5.1/5.2; this item is the regression tests proving it.
9. **E-PARSER-001…006** — thread location into `parse_result` (six raise sites
   passing `loc=None`), and include the offending text (issue #387). *Delivered; spec
   `specs/E-PARSER.md`.* Three corrections from pinning the series: `jsonl` reported
   `JSON` and a per-line position that always read `line 1`; `yaml` used `repr(exc)` and
   so discarded PyYAML's own readable report; and the `csv` branch's `except` is
   **unreachable on malformed CSV**, which parses to nonsense and exits 0, so the real
   csv defect is a silent failure rather than a bad message. Two things the estimate
   missed, both found by running it: `parser: {pdl: ...}` was a raw traceback, now
   **E-PARSER-007**; and an invalid `regex:` under `mode: split`/`findall` was a raw
   traceback too, fixed by the same explicit compile step. The three silent failures the
   item is *not* allowed to fix are in 7.10.
10. **E-SCHEMA-006/007** via 5.3, then **E-SCHEMA-002** ("did you mean") and
    **E-EXPR-001** ("in scope here"). *E-SCHEMA-006/007 delivered; spec
    `specs/E-SCHEMA-UNION.md`.* Four things the estimate missed, all found by running
    it. The E-SCHEMA-006 defect is not a missing analyzer case but a **dead** one — the
    `enum` test could never fire because a `type` test above it had already said yes —
    and the same loop *assigned* its flag from a `$ref` alternative rather than
    accumulating, so a later member could reset an earlier match. `BlockType` is reached
    through an inline `anyOf` for `text:`/`lastOf:`/`sequence:` and so is not recognised
    by identity on `$defs`; that needed a second, narrower dispatch. `parser:`, `lang:`
    and `mode:` are lower-cased by a `BeforeValidator` that JSON Schema cannot express,
    so a stricter analyzer starts contradicting the validator about `parser: JSON` unless
    it is told. And the fallback the item was named for is still reachable (7.11), which
    the spec predicted it would not be.
11. **E-PARSE-003 and E-RUNTIME-012** — the two semantic changes from 5.5. Deliberately
    last: they are the only items that can break a working user program, so they land
    after everything else is green and get their own release note. *E-PARSE-003
    delivered; the re-measured blast radius and the two shape decisions are in the
    5.5 blockquote. E-RUNTIME-012 is still open.* One thing the estimate missed, found
    by running it: `<<:` had to be exempted outright, because PyYAML's `flatten_mapping`
    honours **every** merge key in a mapping rather than the last, so two of them are a
    union that loses nothing — a naive check would have rejected a working program, and
    that is the case `E-PARSE-003-merge-key` now pins.
12. **E-TYPE-006** — locate and de-duplicate the deprecation warning (5.4).

---

## 7. Decisions taken after §5

Recorded so they survive between sessions. Everything in §5 was settled at the end of
Phase 0; this section is for questions that only surfaced during implementation.

### 7.1 `E-PARSE-005` — what a non-UTF-8 `.pdl` file raises from the SDK

**Status: DECIDED 2026-08-10 by the project owner — option 1, raise `PDLParseError`.**
Implemented as `PDLUnicodeDecodeError(PDLLocatedParseError)`, which is *not* a
`UnicodeDecodeError`; `except UnicodeDecodeError` around `exec_file` therefore stops
matching. That is the single deliberate SDK break of the boundary work and it is
announced in `docs/release-notes.md`, together with the one narrowing left over from the
other six (`except yaml.MarkedYAMLError`, which `PDLYamlError` does not satisfy).

Two conditions were attached to the choice and both are met. The decode payload is
carried onto the new exception — `encoding`, `object`, `start`, `end`, `reason` — so a
caller rewriting the clause to `except PDLParseError` finds the same attributes on the
object it catches; `start` and `end` are improved to file offsets. And `str(exc)` is the
rendered diagnostic rather than a list repr, inherited from `PDLLocatedParseError`.
Both are pinned by tests in `tests/test_parse_errors.py`, because matching the class and
using the caught object are two different questions.

The reasoning below is preserved as the record of what was weighed. One clause of it was
overstated and is corrected here: `UnicodeDecodeError.start` is chunk-relative in
general, but `parse_file` calls `read()` on a fresh handle, and `TextIOWrapper.read()`
decodes in one piece, so today's number *is* a file offset. Reading the same file line by
line is what reports an offset thousands of bytes short. The position is recomputed from
`Path.read_bytes()` anyway, for two reasons that survive the correction: a location must
not rest on an undocumented implementation detail of `TextIOWrapper`, and the raw bytes
are needed for the excerpt regardless.

The other boundary entries keep every existing SDK `except` clause working, because a
shim can inherit the concrete exception type:

```python
class PDLFileNotFoundError(PDLParseError, FileNotFoundError): ...   # verified
class PDLIsADirectoryError(PDLParseError, IsADirectoryError): ...   # verified
class PDLYamlError(PDLParseError, yaml.YAMLError): ...              # verified
```

`UnicodeDecodeError` cannot be treated the same way. The class layout is accepted, but
construction is not — it requires exactly five arguments, and neither a one-arg call nor
`__new__` followed by an explicit `__init__` satisfies it:

```
TypeError: function takes exactly 5 arguments (1 given)
```

So a decode failure cannot both carry a PDL message and remain catchable as
`UnicodeDecodeError`. The options are mutually exclusive:

1. **Raise `PDLParseError`.** `except UnicodeDecodeError` around `exec_file` stops
   matching. Rare in practice, and it buys the largest diagnostic gain in the group: a
   real line, column, source excerpt and caret, recomputed from `Path.read_bytes()`.
   Needs a release note.
2. **Format at the CLI, re-raise the original.** Zero breakage, but the CLI and SDK paths
   diverge — a library caller receives none of the new information.
3. **Chain** (`raise PDLParseError from exc`). `isinstance` is unaffected by chaining, so
   `except UnicodeDecodeError` still stops matching; only forensic value is preserved.

Recommendation: option 1 with a release note, as the single documented SDK change in the
boundary work. *Chosen; see the status line above.*

Related and worth knowing when this is picked up: `UnicodeDecodeError.start` is
**chunk-relative**, because `parse_file` reads through a `TextIOWrapper`. Today's
reported byte offset is therefore not reliably a file offset, and a correct location has
to be recomputed from the raw bytes on the failure path. *Overstated for this code path
— see the correction in the status above.*

### 7.2 Non-UTF-8 inputs that are still uncovered

Not decided, because nothing forced it yet. Two siblings raised a bare
`UnicodeDecodeError`; one is now closed and would each need their own corpus entry and
golden:

- `load_initial_scope` (`pdl.py:245-246`), the `-f` data file. **Still open.** One
  `except` clause away from the same treatment, but it is a second error ID, not this
  one.
- `process_import`, which opens the imported file itself instead of calling `parse_file`.
  **Closed** by the `E-RUNTIME-002` commit: its `except UnicodeDecodeError` routes through
  `undecodable_source_error` and carries the resulting diagnostic out on the runtime-error
  path. Deliberately left without a corpus entry, since "a non-UTF-8 *imported* file" has
  no taxonomy ID of its own and inventing one to score a golden would claim coverage no
  spec designed; the E-PARSE-005 golden pins the text this now reuses.

### 7.3 The corpus tests `--stream none`, which is not the CLI default

**Status: open. A coverage gap in the harness, not a defect in PDL.**

Every corpus entry runs with `--stream none` (`tests/errors/harness.py`, the default
`argv`). That was chosen so goldens would not carry streamed partial output. The
consequence went unnoticed until E-MODEL-001: **`--stream none` and the CLI default are
different code paths**, and for model calls they are different *functions*.

`InterpreterState.batch` defaults to `1`. `pdl.py:main` sets `batch=0` when streaming:

| invocation | `batch` | model path | on failure |
| --- | --- | --- | --- |
| `pdl prog.pdl` (default, `--stream result`) | 0 | `generate_text_stream` → `litellm.completion` | clean single diagnostic |
| `pdl --stream none prog.pdl` | 1 | `generate_text` → `litellm.acompletion` | diagnostic **+ duplicate traceback** |
| SDK `exec_file(...)` with no config | 1 | same as above | diagnostic **+ duplicate traceback** |

Two things follow, both measured rather than reasoned:

1. **For `E-MODEL-*`, the corpus pins a worse experience than a CLI user gets**, and the
   entries are really covering the **SDK** default rather than the CLI one. That is
   arguably the more important audience for these entries — an embedder cannot suppress
   stderr noise from a failure PDL has already handled — but it is not what the entry
   titles imply, and it is not what a reader of `BASELINE.md` would assume.
2. **The streaming path has no error handling of its own.** `generate_text_stream`
   (`pdl_llms.py`) wraps nothing in a `try`. It happens to produce a clean message today
   because the failure surfaces elsewhere, but nothing pins that, and no corpus entry
   exercises it.

Options, none taken yet:

- Add a `stream` axis to the harness so selected entries are run under both modes, and
  give `E-MODEL-*` a streaming counterpart. Most faithful, and the only one that would
  have caught this; costs a second golden per affected entry.
- Switch the corpus default to `--stream result` to match what users run, and keep
  `--stream none` for entries where streamed output would make the golden unstable.
- Leave it and document the gap per entry.

Recommendation: the first, restricted to `E-MODEL-*` and any future entry whose behaviour
is known to differ by mode. Broad double-running would double the suite's runtime for no
benefit on entries that never reach a model.

**The general lesson, which outlives this entry:** a harness fixes an invocation, and
every fixed invocation is a claim that the chosen one is representative. Where a flag
selects a different code path rather than only different formatting, that claim is false
and the corpus is measuring something other than what its titles say.

### 7.4 `import:` resolves against the top-level program, not the importing file

**Status: open. A semantics observation, not a diagnostic defect — recorded because any
diagnostic about a missing import must not misdescribe it.**

`state.cwd` is bound once, from the parent of the program passed to `exec_file`/`generate`,
and is never rebound when `process_import` or `process_include` recurse. So a nested
`import:` resolves relative to the **top-level program's directory**, not the directory of
the file doing the importing.

Measured with a discriminating fixture — the naive test cannot tell the two readings
apart, because for a top-level program cwd and the file's own directory are the same path:

```
top.pdl          defs: {m: {import: sub/mid}}
sub/mid.pdl      defs: {h: {import: helper}}      # imports a sibling by bare name
```

| where `helper.pdl` lives | result |
| --- | --- |
| `sub/`, beside the importing file | **fails** — looks for `helper.pdl` at the root |
| root, beside the top-level program | succeeds |

The consequence is that **a PDL library directory cannot import its own siblings**. A
helper that works when its directory is the entry point breaks when some other program
imports it, and the failure names a path in a directory the library author never referred
to. Whether that is intended is a language question, not an error-reporting one.

What it means for diagnostics, and why it is recorded here:

- A message must **not** say "resolved relative to this file". It is not.
- Naming the directory searched is still useful, and it is the top-level program's
  directory. Where that differs from the importing file's directory, the diagnostic should
  say so explicitly, because that gap is exactly what will have surprised the user.
- Any "did you mean" must search the directory PDL actually searched, not the importing
  file's neighbours, or it will suggest a file that cannot be imported from there.

A first attempt at measuring this concluded the opposite. The fixture had the importing
file *as* the top-level program, so both readings predicted the same outcome. Recorded as
a reminder that a test which cannot fail under the competing hypothesis has not tested
anything.

### 7.5 Open findings from the E-RUNTIME-002 gates

Recorded rather than fixed, except where noted. None blocks anything.

**`sys.exit()` in a `code:` block is INTENDED — decided 2026-08-11, not a defect.**
A block containing `import sys; sys.exit(42)` exits the whole `pdl` process with **42**,
prints nothing, and the blocks after it never run. `SystemExit` does not derive from
`Exception`, so nothing catches it.

This was first recorded here as a new S0 on the grounds that it broke decision §5.8's
"exit code stays 1". **That framing was wrong and is corrected.** §5.8 constrains the exit
code of a *failure*; a program deliberately choosing its own exit code is not a failure,
and PDL exiting with the code the user asked for is the behaviour the project owner
wants. No taxonomy row, no corpus entry, no change.

Recorded so it is not "fixed" later: a future reader meeting a `code:` block that exits
silently with a non-zero status will recognise it as a defect unless told otherwise. It
is not. The only thing arguably owed is a line in the tutorial's code-block section, which
today documents the `result` contract but says nothing about a block ending the process.

**Fixed here: the near-miss branch could suggest an import cycle.** The gate predicted it
and a run confirmed it — a nested `import: prg` inside `lib/a.pdl` produced
`help: did you mean \`import: prog\`?`, naming the running program. `_import_candidates`
drops the *importing* file, but in a nested import the entry program is not the importing
file. Both suggestion branches now refuse under one shared `would_cycle` condition. It
over-refuses: a genuine near miss in that directory is suppressed too, because nothing at
that point can tell which candidate is the entry program. That costs a Fix point, where
suggesting a cycle is a confidently wrong instruction — which the rubric ranks below
saying nothing. A precise fix needs the entry program's path threaded down.

**The `UnicodeDecodeError`-under-`import:` diagnostic has no import context.** It reuses
`E-PARSE-005`'s record whole, so it names the undecodable file but carries no `in import`
block path and does not mention the program that imported it. Honest and actionable — the
file to fix is named — but weaker than the `OSError` branch beside it, and invisible to
the corpus because that shape has no taxonomy ID.

**A `__cause__` walker self-loops.** `raise exc from exc` at `pdl_interpreter.py:813`
self-assigns `__cause__` on *every* runtime error leaving the retry wrapper, so
`while e.__cause__: e = e.__cause__` never terminates. Pre-existing, unrelated to any
change here, and confirmed on an untouched expression error. The original exception is on
`__context__`, which is what the release note documents.

**Spec `file:line` citations go stale.** *Resolved by anchoring, not by sweeping.* The
drift is larger than it looked: `call_python` has moved from line 2644 to 3506 since
`E-CODE-002.md` was written, and `parse_file` moved 18 → 155 while `E-BOUNDARY.md` was
still being edited. But every citation checked resolved correctly *at the commit its spec
was written against* — they were never wrong, only unanchored.

Re-verifying them on each refactor would be unbounded work with a bad failure mode: a
half-swept spec reads as current while some of it is not. Instead each spec now carries a
blockquote naming its anchor commit, and `specs/README.md` records the convention —
citations are pinned, read them with `git show <anchor>:<path>`, and where a line number
and a symbol name disagree, the symbol name is what was meant.

### 7.6 What Phase-3 item 0 landed, and the one question it could not answer

**Status: DROP #1, #2, #4 and #6 are closed.** `pdl_location_utils.load_with_marks`
composes the YAML node graph and reads PyYAML's `start_mark`/`end_mark` off it, so every
mapping key and every sequence item has an exact line **and column** (#1, #2). The
per-file mark map lives in a `SourceRegistry` keyed by file name, and `PdlLocationType` is
`(file, line, col, path)` with no `table` (5.2), which is what closes #6: `execute_call`'s
`fun_loc` is now `append(closure.pdl__location, "return")` and a location can only ever be
resolved against the file it names. The ancestor walk of #4 survives inside
`PdlSource.resolve` but has changed meaning — with real marks a miss means the path is not
in the source at all (a synthetic segment, a block built at runtime), where before it was
the *normal* case for flow style, for the document root and for any key the regex
mis-split.

Two drops in §3.2 are untouched and remain open: **#5** (manual `loc` threading — the
model is now correct, but `append` is still called by hand at 41 sites) and **#10**
(`loc.path` is carried to the print site and dropped; `get_loc_string` still renders
`file:line - ` only). #10 is Phase-3 item 7 and is now purely a renderer change.

**Columns are recorded but not rendered.** `PdlLocationType.col` is populated, serialised
into traces, and available to `Diagnostic`'s span renderer, which has printed
`file:line:col` for boundary diagnostics since E-BOUNDARY. `get_loc_string` — the prefix
every *runtime* and *schema* diagnostic is still built from — deliberately does not use
it: turning on `:col` there rewrites the header of every entry in the corpus in one step,
and that is a rendering decision belonging to 5.6's renderer, not to the location model.
**Decided since, in 7.9:** the renderer owner took it, `get_loc_string` returns
`file:line:col - `, and the 42 goldens it rewrote are what that paragraph priced. The
location model is unchanged by it — no field, no computation, only the printer.

**How to name a source that has no file name — decided since, in 7.7.** The registry
must be keyed by something, and the empty string cannot be it — `""` is
`empty_block_location.file`, i.e. *no source at all*, carried by every block of an
`exec_dict` program. Registering an anonymous source under `""` makes a program with no
source report line numbers belonging to whatever string was parsed most recently; that was
observed, not predicted (`tests/test_errors.py` went from `line 0 - ` to `line 8 - ` as
soon as another test in the same process had parsed a string). So a string program is now
registered under `<program>`, the label `parse_str` already used in its YAML errors, and
`""` never resolves. What remains unanswered is **multiple** unnamed sources: they all
share the one `<program>` entry, last registration winning, and the reachable case is
`exec_str` of a program containing a `lang: pdl` block. Giving each one a distinct name
would fix it, and that name is printed in diagnostics, so it is a naming decision rather
than an implementation detail. **7.7 takes that decision**; the paragraph above is left as
the statement of the problem it answers.

**Correction to §5.2: `pdl__location` is not in a trace file.** The decision record says
"`PdlLocationType` is serialised into trace JSON, so the trace format changes". It is not:
`location_to_dict` (`pdl_dumper.py`) is dead code, because its only call site — the two
lines that would write `d["pdl__location"]` — is commented out. A trace written by
`pdl --trace` today contains no location at all, verified by running one. What really
changes is `pdl-schema.json`, the viewer's generated `pdl_ast.d.ts`, and `model_dump()` on
any block; the viewer strips `pdl__location` on load (`pdl_code_cleanup.ts:153`) and reads
it nowhere, and the Rust side has no location type at all, so the viewer work was to
regenerate the types and confirm the build. `location_to_dict` was updated in step anyway:
a dead serialiser that dumps a field the model no longer has is a trap for whoever
uncomments those two lines.

**The "trace format bump" of §6 — declined since, in 7.8.** There is no version field
anywhere in `pdl_ast.py` today, and no envelope around a trace to put one in — a trace is
`block_to_dict` of the root block. Adding a version therefore means either wrapping every
trace in a new object (which every trace reader, including the viewer's loader, would have
to be taught) or adding a field to the block model itself (which puts it on every block in
the file). Neither is a contained addition, and inventing a versioning scheme is not a
decision this work should make on its own. The trace *content* change is delivered and
documented in `docs/release-notes.md`.

---

### 7.7 Naming a source that no file contains

**DECIDED: `<program:x>`, where `x` is the route to where the program came from.**

`lang: pdl` runs the text of a `code:` field as a program in its own right. Until this
decision that text was parsed unnamed, so it was registered under `<program>` — the very
key the *containing* string program uses — and evicted it. Measured, not predicted: a
string program with a `lang: pdl` block at `text[0]` and a failing expression on line 7
reported `<program>:2`, because `['text', '[2]']` is absent from the inner source's marks
and `PdlSource.resolve`'s ancestor walk fell back to the inner `['text']`, line 2. The same
program without the `lang: pdl` block reported the right line.

The spelling, in full:

| Source | Name |
| --- | --- |
| a `.pdl` file | its path, unchanged |
| a string handed to `exec_str`, `pdl-infer`, the notebook magic | `<program>`, unchanged |
| a `lang: pdl` block inside a file | `<program:hello.pdl#text[0].code>` |
| a `lang: pdl` block inside a string program | `<program:text[0].code>` |
| one inside another | `<program:hello.pdl#text[0].code#defs.f.code>` |

Four properties, in the order they were argued for:

- **Qualified by the containing file, not by the path alone.** Two `.pdl` files can each
  hold a `lang: pdl` block at the same block path and both be alive in one process — one
  importing the other. Named for the path alone they would share a key, which is this bug
  moved rather than removed.
- **Readable, because it is user-visible.** It is printed in diagnostics about the nested
  program and it lands in `PdlLocationType.file`. `text[0].code` is somewhere to go and
  look; a hash or a counter would be unique and useless. The path is spelled as
  `Diagnostic` already spells a block path (`join_path`), so a diagnostic's `in <path>`
  line and this name use one syntax.
- **Not mistakable for a file.** The angle brackets are `<program>`'s, and `is_unnamed`
  now covers the whole family, so no message invites the user to open a path that does not
  exist.
- **`#`, not `:`.** `get_loc_string` attaches the line number with a colon; a colon inside
  the name would make `<program:x>:7` unreadable at exactly the moment it is read.

**What it closes.** The reachable eviction case — one program running another — is gone,
including for two files that each contain one, and including deeper nesting. Every nested
program now has a key that no other source in the process shares, unless one of the two
cases below applies.

**What it does not close, deliberately.** A key still names one text *at a time*, not one
text for the length of a run:

1. **A nested site whose text changes.** `nested_source_name` is derived from a block
   path, and a `lang: pdl` block inside a `for:` loop whose `code:` interpolates the loop
   variable is one path with a different text per turn. Measured: one path, two texts
   (`text: alpha`, `text: beta`) in a single run. Making the name unique here means
   putting a hash or a counter in it, which loses the one property that makes the name
   worth printing.
2. **Two threads in one `exec_str` each.** Both are top-level and both are `<program>`,
   which is pinned by `tests/test_parse_errors.py` and is what `parse_str`'s YAML errors
   already say. The registry's lock protects the dictionary, not logical ownership.

Two mitigations, because "does not close" must not mean "goes wrong quietly":

- **Lines stay right.** A location resolves its line and column when it is *built*, from
  the marks in force at that moment, and freezes them. `parse_str` therefore re-registers
  on a hit of its own `lru_cache` as well as on a miss: without that, a cached re-parse of
  text A would run with text B's marks still in the registry, and every location built
  during it would resolve against them. That is a wrong line, not merely a wrong excerpt,
  and it is now covered by `tests/test_source_locations.py`.
- **Text stops answering rather than lying.** `SourceRegistry.register` marks a key
  `contested` when a *different* text is registered under it, and `source_text` returns
  `None` for a contested key. This is aimed at 5.6's renderer, which will read text from
  the registry long after the location was built: an excerpt drawn from the wrong text sits
  under a correct line number with nothing on the page to say so, and `RUBRIC.md` ranks
  that below showing nothing. A diagnostic that captures its excerpt when it is *built* —
  what every boundary diagnostic already does — is unaffected, and is the pattern to
  prefer.

The one collision the scheme cannot see is a real file literally named `text[0].code`
sharing a process with a nested program at that path in a string program. Recorded rather
than defended against.

**Where it is visible.** Corpus entry `E-CODE-005` is the only place a CLI user meets the
name today, because every *runtime* failure inside a nested program is caught by
`process_call_code` and interpolated into `PDL Code error: {repr(exc)}`, which discards the
inner location entirely. A nested program that fails to *parse* still shows its name. The
string-program case, which is where the wrong line was reported, is not CLI-reachable at
all and is covered by `tests/test_source_locations.py` instead.

### 7.8 No trace version field

**DECIDED: skip it.** §6 item 0 asked for a "trace format bump" to protect a trace-format
change that this work turned out not to make: `pdl__location` was never serialised, because
`pdl_dumper.py`'s call site is commented out (7.6). There is nothing to version.

Both implementations cost more than the problem. An envelope around the root block breaks
every existing reader, including the viewer's loader, for a field none of them would read.
A field on the block model stamps a version on every block in the file. Inventing a
versioning scheme is a decision about the trace contract as a whole and does not belong to
a change that leaves the contract alone.

The clause is therefore struck from §6 item 0, so the plan stops asking for it.
`docs/release-notes.md` never promised a bump — it says the opposite, that files written by
`pdl --trace` do not change — and needed no correction.

### 7.9 What block paths actually bought, measured

**Item 7 is delivered. §6's estimate of it was wrong, in both directions of "wrong": it
overstated the reach and it misread the rubric.** The numbers below are counted from the
corpus after the change, not projected from it. §6 has now been wrong three times about
this area — it claimed the viewer needed a matching change (it did not, 7.6), it claimed
`PdlLocationType` is serialised into traces (it is not, 7.6), and it claimed this item
"satisfies half of rubric item 1 across all ~70 IDs at once". Estimates in §6 should be
read as intentions, not as findings.

**Reach.** Of 49 corpus entries, **30** render a `get_loc_string`-style prefix at all;
the other **19** never do, so nothing here can reach them. Those 30 entries render **37**
prefixes between them (`E-SCHEMA-010` alone renders five, the three `E-TYPE-*` entries two
each). **6 of the 37 have an empty path** and render no `  in` line: four entries whose
program is one top-level block, so the block *is* the document at path `[]`
(`E-CODE-003`, `E-RUNTIME-001`, `E-RUNTIME-004`, `E-RUNTIME-011`), and two complaints in
`E-SCHEMA-010` that are about the program itself. **31 `  in <path>` lines** are now
rendered, across **26** goldens.

**Score movement: 17 entries move Location 1 → 2. 8 of the entries whose golden changed
stay at 1**, and that is the part §6's "half of rubric item 1" hid. `RUBRIC.md` scores 2
for "accurate `file:line`, **plus** either a column or the block path". A path is one of
two conjuncts, not half of a disjunction: it does nothing for an entry whose *line* is
coarse. The eight are

| Entry | Line points at | Why a path does not lift it |
| --- | --- | --- |
| `E-CODE-001` | the `code:` key, line 2 | offending statement is on file line 3; the path `code` is the same key |
| `E-CODE-002` | the `code:` key | same key again; here there is no single statement to point at |
| `E-CODE-005` | the enclosing `text[0]` | failure is four lines further down, inside the nested program |
| `E-CODE-006` | the second block's `code:` key, line 9 | offending statement is on file line 12 |
| `E-RUNTIME-006` | the `for:` key | the mismatched lists are on lines 2 and 3 |
| `E-SCHEMA-010` | the root, line 1, for two of five | those two have path `[]` and render nothing |
| `E-TYPE-001` | the `spec:` key | names the rule violated, not the block whose result violated it |
| `E-TYPE-003` | `text[0]`, via the ancestor walk | the rendered path `text[0].args` names a block the file does not contain — `args:` is missing, which *is* the error |

The distinction applied throughout, and the one to reuse when rescoring: a location is
accurate when the mark resolved is the mark of the construct the message is about. A
Jinja expression that is the whole value of a mapping entry or the whole of a list item
has no mark of its own, and the entry's mark is its position — one construct, one mark.
It is coarse when the construct is strictly bigger than the offending element inside it,
which is every row of the table above.

**Recommendation, not implemented: rendering `:col` moves nothing on its own.** The
foundation put a real column on every location and `get_loc_string` deliberately does not
render it. Measured against the eight entries still at 1, the column comes off the *same
mark as the coarse line*, so it is a horizontal coordinate for the wrong element:

    E-CODE-001    prog.pdl:2:1   -> `code: |`            (the `c` of `code`)
    E-CODE-002    prog.pdl:2:1   -> `code: |`
    E-CODE-005    prog.pdl:3:3   -> `- lang: pdl`
    E-CODE-006    prog.pdl:9:3   -> `  code: |`
    E-RUNTIME-006 prog.pdl:1:1   -> `for:`
    E-SCHEMA-010  prog.pdl:1:1   -> `description: x`
    E-TYPE-001    prog.pdl:2:1   -> `spec: integer`
    E-TYPE-003    prog.pdl:7:5   -> `  - call: ${ greet }` (the `c` of `call`)

None of those becomes accurate by gaining a column, so **0 entries move 1 → 2**, and 0
move 2 → 3 either, because Location 3 wants the column *together with* an excerpt, a
caret span and the include/call chain. Turning `:col` on alone would rewrite the header
of all 30 prefix-rendering goldens for no score movement at all. The column is worth
having as part of the 2 → 3 package — the caret needs it — and the `Diagnostic` renderer
already prints `file:line:col` where it has a span, which is why `E-PARSE-001`,
`E-PARSE-002`, `E-PARSE-005`, `E-CLI-003` and `E-LINT-002` score Location 3 today. The
decision belongs to whoever owns 5.6's renderer.

**What would move the eight.** Not a coordinate but a finer *element*: for the four
`E-CODE-*` entries, mapping a `code:N` gutter row back onto its file line; for
`E-RUNTIME-006`, naming the offending list (which is also its Why 0); for `E-TYPE-001`,
locating the result rather than the `spec:`. Each is that error ID's own work.

**Decision taken: `:col` is rendered, and this section's arithmetic was half right.**
`get_loc_string` now returns `file:line:col - `. The prediction above is confirmed where
it was measured and wrong where it was inferred, and both halves are worth keeping:

- **The eight coarse entries do not move, exactly as measured.** The column comes off
  the same mark as the coarse line, so it is a horizontal coordinate for the wrong
  element. Confirmed entry by entry against the regenerated goldens.
- **Two entries moved 1 → 2 that this section did not count**, because it counted only
  the coarse eight and not the *empty-path* four. `E-RUNTIME-001` (`include:`) and
  `E-RUNTIME-004` (`read:`) are single-block programs whose path is `[]`: rubric item 1
  wants an accurate line plus a column **or** a path, they had an accurate line and
  neither, and a path is the one thing they can never have. Their own corpus notes said
  so at the time — "the remaining point here is the column" — and the column was it.
  The other two of the four, `E-CODE-003` and `E-RUNTIME-011`, do **not** move, and
  `E-CODE-003`'s note claiming it would has been corrected: both are `code:` blocks
  whose failing statement is inside a multi-line scalar, which is the coarseness above
  and not the missing-coordinate case.
- **Three entries moved 2 → 3**, not zero: `E-PARSER-006`, `E-SCHEMA-007` and
  `E-SCHEMA-009-items-crash`. Each already had the block path, a *file* excerpt and a
  caret, and was held at 2 by the header alone; each carries a note that pre-registered
  the move. None is inside an `include`/`import`/call, so the chain clause does not
  apply to any of them.
- **No entry's Location moved down, and the mechanism is why.** A column cannot make a
  header state a construct the header did not already state: `line` and `col` are read
  from one mark, of the block `loc.path` names, and 38 of the 42 changed goldens print
  that path directly under the header. The failure mode the deferral feared — a
  confidently precise column pointing somewhere the message is not about — would need the
  column to come from a different mark than the line, which `append` cannot produce.

**Measured reach: 42 of 59 goldens, 47 headers.** Where those columns land, counted
rather than characterised: **5** on a value or a list item (a `- ${ nope }` past the
`- `, the `{result: 1, context: 2}` inside a flow sequence, the third item of
`[1, 2, 3]`) and **42** on a key token. A key landing is not by itself a misdirection —
in **26** of the 42 the message names that very key, because the key *is* the fault: a
field no block accepts, the `parser:` whose parse failed, the `jitter:` list of the wrong
length. In the remaining **16** the column is on the construct containing the offending
value (`role: 42`, `return: ${ kaboom }`, `code: |`), which is an upper bound on
"precise-looking about something else" rather than a count of it — four of the 16 name
their subject some other way, `E-SCHEMA-007`'s caret label being the clearest.
**A conditional rendering that told the two apart
is not implementable in this renderer**, which is the argument that settled it rather
than taste: `get_loc_string` receives a `PdlLocationType` and nothing on it distinguishes
"this mark is the offending element" from "this mark is the construct around it" — both
are produced by the identical `SOURCES.mark(file, path)` lookup. The only predicate
available is *known* versus *unknown*, and that one is applied: `col == 0` renders no
column, which is what `pdl_diagnostics._header` has always done with a span that has no
column, and is why `line 0 - ` is unchanged for a program with no source at all.

---

### 7.10 Three silent failures in `parse_result` — one partly closed, two left open

Found while specifying Phase-3 item 9 and **deliberately not fixed by it**. All three are
programs that run to completion, print a wrong answer and exit **0**. Turning any of them
into an error changes the exit code of programs that work today, which is a decision-5.5
semantic change: it needs the owner's sign-off, a blast-radius measurement and a release
note, and it belongs with §6 item 11 rather than with a diagnostics item. Item 9 improved
the messages of the failures that already *were* errors and touched none of these.

**Finding 1 has since been closed for its worst class**, with the owner's explicit
sign-off: a quoted field that is never closed is now an error. The rest of finding 1 was
measured, considered and **deliberately left silent** — that part is not an oversight and
is not scheduled. Findings 2 and 3 are untouched and still open on the same terms. The
measurements the decision rested on, and the ones that cut its scope down, are under the
table.

They are listed together, here, because each was previously buried in the notes of the
corpus entry nearest to it, where a reader looking for "what does PDL silently get wrong"
would not find them.

| # | Trigger | What happens | Why item 9 could not fix it |
| --- | --- | --- | --- |
| 1 ~~open~~ **PARTLY CLOSED** | `parser: csv` on malformed CSV | `csv.reader` accepts an unbalanced quote, ragged rows and embedded NULs. `"a,b,c\n\"unterminated,1\nx,y\n"` parses to `[["a","b","c"], ["unterminated,1\nx,y\n"]]` — a wrong parse, no diagnostic, exit 0. The `csv` branch's `except` was reachable **only** through `csv.field_size_limit()`, which is what `E-PARSER-004` pins | Item 9 could not: nothing raised, so there was no raise site to improve. **The unclosed-quote class is now an error** under decision 5.5, via a `strict=True` reader used as a detector. The rest is still accepted in silence *on purpose*: ragged rows and NULs as before, and text after a closing `"` because rejecting it breaks working programs over a trailing space. See below |
| 2 | a `regex:` parser that does not match | `RegexParser.mode` defaults to `fullmatch`, so a near-miss pattern returns `None` and the program prints `null` at exit 0. A user who wrote `regex: '\('` on the advice of `E-PARSER-005`'s `help:` and whose text is `Hello` lands exactly here — which is why that suggestion is phrased conditionally | Same: the code path returns rather than raises. `m is None` would have to become an error, and a program may legitimately want the `null` |
| 3 | `parser: json` over prose — **the case [#387](https://github.com/IBM/prompt-declaration-language/issues/387) actually names** | `json_repair` *repairs*: its parser returns `""` when it finds no value, so `parser: json` over a model's prose yields `''` at exit 0 rather than the parse error the taxonomy assumed. Measured: `'not json at all'`, `'the answer is 42'`, `''` and `'hello: world'` all return `''`; `'{"a": 1'` repairs to `{'a': 1}`. It raises only for a non-`str` input, which is the branch `E-PARSER-001` pins | The largest of the three by blast radius. `json_repair` is the dependency PDL chose *because* it repairs; making a repaired-to-empty result an error changes what every `parser: json` program does |

#### Closing finding 1's worst class: what changed, and what was left alone

The blast radius §5.5 requires, measured on the tree the change landed on and not assumed.

**What now fails.** One shape, and only one: a quoted field that is never closed.

| Input | Before | Now |
| --- | --- | --- |
| `a,b,c\n"unterminated,1\nx,y\n` | rows of width `[3, 1]`, silent | **error** (`csv.Error: unexpected end of data`) |
| `a,b\n1,"x\n` (quote never closed) | width `[2, 2]`, silent | **error** (same) |

**What it leaves exactly as it was**, including the case that would have made the change
unsafe:

| Input | Before and now |
| --- | --- |
| `a,b\n"line1\nline2",2\n` (legitimate multi-line quoted field) | parses — **no false positive** |
| `a,b,c\n1,2\n3,4,5,6\n` (ragged) | widths `[3,2,4]`, still silent |
| `a,b\n1,va"lue\n` (bare `"` inside an unquoted field) | parses |
| `a,b\n1,\x002\n` (embedded NUL) | parses |
| `"a", "b", "c"\n` (space after the delimiter, so the `"` is not at a field start) | parses |
| `1,"Ada" Lovelace` (text after a closing `"`) | `["1", "Ada Lovelace"]`, still silent — see below |
| `1,"Ada" ` (a trailing space after a closing `"`) | `["1", "Ada "]`, still silent — see below |

#### The narrowing, and the inconsistency it buys

The first version of this change did not have the last two rows in that second table. It
built the reader with `strict=True` and let every `csv.Error` raise, which also rejects
**text after a closing `"`** — and the measurement that settled it is that `strict` rejects
a *whitespace-only* trailing content too. `1,"Ada" ` parses to `["1", "Ada "]` today and
would have become a hard failure. A trailing space turning a working program into an error
is well past "fix the wrong parse", and it is a shape model-generated CSV plausibly emits.
The owner ruled the change down to the unterminated-quote class alone.

`strict` is one flag on `csv.reader` and not two, so the narrowing cannot be done at the
reader. It is done in the handler instead: **the strict reader is a detector, not the
parser of record.** Everything it rejects other than the unclosed-quote class is parsed
again with a default reader and *that* result is returned (`_csv_rows_lenient`,
`pdl_interpreter.py`).

**The cost, stated rather than glossed: PDL returns a parse the standard library flagged,
and says nothing about it.** That is an inconsistency between what PDL accepts and what
`csv` in strict mode accepts, and it was accepted explicitly as the price of not breaking
programs that work today. It is written at the code site, it is in the release note, and
corpus entry `E-PARSER-004-after-quote` pins the tolerated value — `1,"Ada" Lovelace` and
`1,"Ada" ` both parse at exit 0, and the golden records exactly what they produce — so
that a silence that has been deliberately accepted still cannot change unnoticed. That
entry carries `hygiene_silent_failure`, on the same terms as findings 2 and 3 below: the
flag is about the missing diagnostic, not about whether the returned value is defensible.

**Discriminating the class is a match on message text, and the fallback is what makes that
safe.** A `csv.Error` carries no code, no attributes and no position, so its message is the
only discriminator available. `unexpected end of data` was checked on both interpreters
this repository runs — 3.11 and 3.12 emit it identically, as they do `',' expected after
'"'` and `field larger than field limit (131072)`. The rule at the call site is that an
**unrecognised** message falls back to the lenient parse rather than raising, so a wording
change in a future Python costs a diagnostic and never invents a failure
(`csv_error_is_unclosed_quote`, `pdl_diagnostics.py`).

**The field-size limit still raises**, which is the pre-existing `E-PARSER-004` behaviour
and reaches the same `except`. It survives the fallback for a structural reason rather than
by being special-cased: it is a resource limit and not a strictness rule, so the lenient
reader hits it too and raises there instead. `E-PARSER-004`'s golden is byte-identical.

**Blast radius, re-measured.** Across **263 `.pdl` files** in this repository — the count
at the commit this landed on; an earlier figure of 205 was from an older tree, and an
earlier `grep --include=*.pdl` also missed call sites that are not `.pdl` files at all —
exactly **one** uses `parser: csv`: `tests/errors/corpus/E-PARSER-004/prog.pdl`, the
field-size reproducer, unaffected. The site that grep missed is **one inline program in
`tests/test_parser.py:183`** (`test_parser_csv`), which uses `parser: csv` on well-formed
CSV and is also unaffected. Every use of `csv.reader` in `src/` is inside the `csv` branch
of `parse_result` or the diagnostic it raises, so nothing outside `parser: csv` can reach
this code.

Every `.pdl` under `examples/` and `tests/data/` (170 files) was run before and after.
**Not one changed its exit code** — 43 exit 0 and 127 exit 1 on both trees, the failures
being models that are not reachable from here — and **no deterministic file changed its
output**.

The deterministic subset was isolated rather than assumed, because a raw diff over all 170
produces false alarms rather than evidence. Each file was run **twice on the baseline**
first; 167 reproduced their own stdout and 3 did not (`code_python.pdl` calls
`random.choice`, `context_fork.pdl` and `function_empty_context.pdl` call models). Exactly
one of the 167 then differed after the change — and it is a **fourth** nondeterministic
file that two runs were not enough to catch, not a regression:
`examples/skeleton-of-thought/tips.pdl` calls an unreachable model and LiteLLM prints a
banner **to stdout** once per attempt, so the byte count depends on how many attempts get
made. Run five times on one fixed tree it gave banner counts 5, 3, 5, 5, 5. The file
contains no CSV at all, its exit code and stderr are identical on both trees, and the
diff is entirely in that banner. So: 166 files byte-identical, 4 nondeterministic against
themselves, 0 regressions. The residual risk is to user programs outside this repository,
accepted, and called out in the release note.

**Ragged rows stay silent, deliberately.** They are common in the wild, PDL returns a list
of lists which can legitimately be ragged, and `strict=True` does not touch them anyway.
Making raggedness an error is a separate and much larger decision the owner has not taken:
it needs a policy for *which* width is the expected one — first row, modal, declared — and
that is a feature, not a diagnostic. **Still open.**

**The caret needed an oracle, not a heuristic.** A `csv.Error` carries no position, and
`reader.line_num` is the last line *consumed*, which for a quote left open on line 2 of a
four-line output is line 4 — the confidently-stated wrong location §7.9 and `RUBRIC.md`
score below silence. `_unclosed_quote_position` (`pdl_diagnostics.py`) uses `csv` as its
own oracle instead: closing the quote at the end of the text makes the same reader parse,
and the last field of the last row it then yields *is* the content of the never-closed
field, so re-escaping the doubled `""` it came from recovers the offset of the opening
`"`. Every step is checked against the text and the caret is dropped when any check fails.
Verified exact for a field opening at the start of a line, mid-line, spanning three lines,
containing `""`, and under CRLF. This is the pattern to reuse for any diagnostic whose
parser reports no position: **ask the parser a second question rather than re-implement
it.**

`parse_result` also has a fourth gap that is not a silent failure but is not a diagnostic
either: **a parser's `spec:` types are never checked.** `Parser.spec` is documented as
"Expected type of the parsed value", but its only use is as a list of regex group names,
so `spec: {first: integer}` returns a string and nothing complains. That is a
documentation or a validation bug and wants its own item.

### 7.11 What Phase-3 item 10 found, and the two questions it did not take

**The `errors == []` fallback is still reachable, and it needed its own reproducer.**
`specs/E-SCHEMA-UNION.md` predicted that after the discriminator work "nothing in the
corpus reaches it". Measured rather than assumed: every program under `tests/data` and
`examples` was loaded and mutated field-by-field — 10 920 invalid programs — and 6 of
them reach it, all through `retry: {exceptions: ...}`. One arm of
`RetryConfiguration.exceptions` is `type[BaseException]`, which pydantic validates and
JSON Schema renders as the empty schema `{}`; `{}` matches anything, so the analyzer is
correct by the schema and silent about a program the validator rejected. That is exactly
the state the new wording admits to, and `tests/errors/corpus/E-SCHEMA-006-fallback/`
pins it at 7/15 — the ceiling for an honest rendering of "I do not know", and not an
entry that is expected to move. What should move is the number of programs reaching it.

**A pre-existing traceback in the same function, not fixed here.** `analyze_errors`'
array arm reads `schema["items"]` unconditionally. `ExpressionFloatOrFloatFloat` — the
type of `retry: {jitter: ...}` — renders one alternative as `prefixItems` with **no**
`items`, so `retry: {jitter: [1, 2, 3]}` raises `KeyError: 'items'` out of the error
reporter itself and reaches the user as a raw traceback. It is a breach of decision 5.8,
it is present at `f0d91a1` and was not introduced by this item, and it appeared 30 times
in the same 10 920-mutation sweep. It belongs to **E-SCHEMA-009** (list/object shape
mismatch), and it wants a spec and a fix of its own rather than a silent two-line guard
folded into an unrelated commit. It now has the golden it wanted, in
`tests/errors/corpus/E-SCHEMA-009-items-crash/`, which carries
`hygiene_traceback_expected` and scores 2/15 — the lowest of any E-SCHEMA entry. Two
things were measured there rather than assumed: `prog.pdl` is named **nowhere** in the 76
lines of stderr, so the crash costs the user the location as well as the message; and
what they are shown in place of a diagnostic is the raw pydantic `ValidationError` the
analyzer was called to translate, chained under `During handling of the above exception`.

*Closed by the E-SCHEMA-009 work.* The guard was not two lines: `prefixItems` is a
**tuple**, so the honest answer to `[1, 2, 3]` is that three items were written where two
are allowed, and `analyze_list` reads all four array keywords rather than one. The
chained `ValidationError` went with the crash — stderr is now one diagnostic — and
`hygiene_traceback_expected` is deleted, so `test_no_traceback` asserts on that entry.
The 30 sweep hits are the same defect and are covered by the same change; a re-run of the
sweep would be the way to confirm that, and has not been done. One latent false complaint
sat behind the crash and had to go with it: `{"type": "number"}` maps to `float` through
`json_types_convert`, `isinstance(1, float)` is `False`, and with the crash removed the
element walk accused `jitter: [1, 2]` — a program that runs — of holding two things that
are not numbers.

**`:col` is still not rendered, and this item does not take that decision either.** 7.9
declined the column because it came off the same coarse mark as the line and bought
nothing without a caret. E-SCHEMA-005/006/007 all now *have* a caret, which is the
strongest argument yet for taking it — but adding `:col` to `get_loc_string` rewrites the
header of every prefix-rendering golden in the corpus in one step, and that is the
renderer owner's call, not a side effect of a schema item. Recorded here so the argument
does not have to be reconstructed.

*E-SCHEMA-009 strengthens it and still does not take it.* Two of that entry's four
goldens now move the header **line** off the key and onto the value — `value_location`
resolves the value's first child, which is the nearest thing `_walk` records to the
value's own start — while the caret under the excerpt lands on a column those same marks
already give exactly. The header and the caret therefore now disagree about how precisely
PDL knows where the fault is, in the same diagnostic, and the header is the less precise
of the two. `E-SCHEMA-009-items-crash` is the sharpest case: the caret sits on the third
item of a flow sequence, a position no line number can express. Four entries are held at
Location 2 by the missing `:col` alone. Still not taken here, for the reason above.

*Taken since, and the disagreement is closed.* `get_loc_string` renders `file:line:col - `
(§7.9, decision recorded there with the measurements). Where a diagnostic draws a caret,
the header and the caret now name the same column, because both read the same mark; the
header is no longer the less precise half of its own document. Of the four entries this
paragraph counted as held at Location 2 by the missing `:col` alone, **one moved to 3** —
`E-SCHEMA-009-items-crash`, where the column, 18, is the third item of `[1, 2, 3]` and
says without the excerpt what the line number cannot. The other three did not, and the
count was optimistic in the way §7.9's was: `E-SCHEMA-009`, `-not-a-list` and `-object`
draw their caret under the *key*, or under the value's first child, while the message is
about the value's type, so the caret is not under the offending token and rubric item 1's
fourth conjunct is still unmet. `E-SCHEMA-006` is the same shape. Sharpening
those carets, not the header, is what would move them; that is E-SCHEMA-009's own work.
`E-SCHEMA-007` moved to 3 in the same pass, which this paragraph did not count at all.

**The E-SCHEMA-010 flag was kept by changing the reproducer, not by annotating a stale
one.** `test_order_instability_is_real` re-proves the `hygiene_unstable_order`
annotation across six hash seeds on every run, for the same reason
`hygiene_traceback_expected` fails on XPASS: an entry may not carry a flag for a defect it
no longer demonstrates. The discriminator hides the old reproducer's instability without
fixing it, so the reproducer moved to one that still shows it and the flag stayed true.
The old program's new output — the largest single rubric movement in the item, 4/15 to
14/15 — is the same diagnostic `E-SCHEMA-007`'s golden pins, and its two branches that
entry does not exercise (the near-miss `help:` and a two-caret excerpt with an elision)
are pinned by `tests/test_line_table.py::test_line`, `::test_line4` and `::test_line8`.
