# E-MODEL-DUPLICATE — the model-call failure reported twice

Covers **E-MODEL-001** and **E-MODEL-002**. Phase-3 item 2.

> **Citations point at `2517fc0`**, the tree this spec was written against — not at the
> current tree. Read one with `git show 2517fc0:src/pdl/pdl_llms.py`. Symbol names
> survive; line numbers do not. The three citations added later, in `89281b0`, resolve
> at that commit too: neither file moved in between.

Two corpus entries, one root cause, one fix site duplicated across two files. Unlike every
other spec in this directory, **the message is already right**. `async_generate_text`
wraps the provider failure in a `PDLRuntimeError` carrying `block.pdl__location`
(`pdl_llms.py:55-70`), the main thread raises it out of the lazy value, and `generate`
prints `prog.pdl:2 - <message>` and returns 1 (`pdl_interpreter.py:249-257`). That part
needs no change.

The defect is that the *same* failure is reported a second time, off-thread, as
`exception calling callback for <Future ...>` plus ~20 frames. `update_end_nanos`
(registered at `pdl_llms.py:141`) calls `future.result()[1]` at `:99` inside a
`concurrent.futures` done-callback. When the call failed, `.result()` re-raises, the
callback has no handler, and `concurrent.futures._base._invoke_callbacks` logs the
traceback to stderr. **The identical function is duplicated verbatim in
`src/pdl/pdl_openai.py`** (`update_end_nanos`, `future.result()[1]` at `:171`, registered
at `:213`), so the OpenAI backend leaks the same wall for the same reason. Both are fixed
here; fixing only `pdl_llms.py` would leave E-MODEL-003 (OpenAI backend failure, `[src]`,
no corpus entry yet) leaking on the day someone writes its reproducer.

So this spec is not "what should the message say". It is three questions about the second
report, settled below: what the callback does on a failed future, whether anything is
still reported, and what happens to the measured race.

---

## E-MODEL-001 — unrecognised model provider

### Today

Verbatim from `tests/errors/corpus/E-MODEL-001/expected.txt`, with the two tracebacks
abbreviated at the marked points (the golden carries every frame):

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2 - Error during 'not_a_provider/nope' model call: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=not_a_provider/nope
exception calling callback for <Future at 0xADDR state=finished raised PDLRuntimeError>
Traceback (most recent call last):
  File "<REPO>/src/pdl/pdl_llms.py", line <LINE>, in async_generate_text
    response = await acompletion(
               ^^^^^^^^^^^^^^^^^^
  ... 2 frames elided ...
litellm.BadRequestError: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=not_a_provider/nope

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<PYLIB>/concurrent/futures/_base.py", line <LINE>, in _invoke_callbacks
    callback(self)
  File "<REPO>/src/pdl/pdl_llms.py", line <LINE>, in update_end_nanos
    result = future.result()[1]
             ^^^^^^^^^^^^^^^
  ... 4 frames elided ...
pdl.pdl_ast.PDLRuntimeError: Error during 'not_a_provider/nope' model call: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=not_a_provider/nope
```

Rubric: L0 W1 Y2 F1 H0 = 4/15

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2 - Error during 'not_a_provider/nope' model call: litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM provider you are trying to call. You passed model=not_a_provider/nope
```

Rubric: L1 W1 Y2 F1 H3 = 8/15

Line 1 is byte-identical to today's line 1. Everything after it is deleted.

Two notes on the scoring, because both are re-scores rather than new evidence and a
scorer should confirm them at regen rather than take them from me:

- **L 0 → 1** is not an improvement in the location, it is the removal of the burial.
  RUBRIC's convention is "if a diagnostic is correct but arrives behind a traceback,
  Hygiene is 0 and Location is scored on what is actually legible". E-MODEL-002 prints
  the *identical* location form (`prog.pdl:2 - `) and is scored L1 today. Once the
  traceback is gone the two entries cannot differ on Location, so both are L1.
- **H 0 → 3, with a dissent worth recording.** After the fix this is one line, one
  location prefix, stable across runs, exit 1. The one argument for H2 is that
  `litellm.BadRequestError:` is a Python exception class name reaching the user. RUBRIC
  rules provider text out of scope *for What/Why* and says nothing about Hygiene. I score
  it 3 and note the alternative: if the scorer counts the class-name prefix as leakage,
  this entry is H2 = 7/15 and E-MODEL-002 is unaffected. Nothing in the fix depends on
  which way that goes.

W stays at 1 and F stays at 1 deliberately. Raising them means rewriting the message
(PDL vocabulary for "this provider name is not one LiteLLM knows", a `help:` listing
`provider/model` forms), which needs the provider-name knowledge PDL does not have at
this raise site — `model_id` is an opaque string it passed through. That is a separate
item; see "Not in this spec".

---

## E-MODEL-002 — model endpoint unreachable

### Today

Verbatim, abbreviated at the same two points:

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2 - model 'ollama/granite' encountered ConnectError('[Errno 111] Connection refused') trying to POST against http://localhost:11434/api/chat
exception calling callback for <Future at 0xADDR state=finished raised PDLRuntimeError>
Traceback (most recent call last):
  File "<REPO>/src/pdl/pdl_llms.py", line <LINE>, in async_generate_text
    response = await acompletion(
               ^^^^^^^^^^^^^^^^^^
  ... 2 frames elided ...
httpx.ConnectError: [Errno 111] Connection refused

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<PYLIB>/concurrent/futures/_base.py", line <LINE>, in _invoke_callbacks
    callback(self)
  File "<REPO>/src/pdl/pdl_llms.py", line <LINE>, in update_end_nanos
    result = future.result()[1]
             ^^^^^^^^^^^^^^^
  ... 4 frames elided ...
pdl.pdl_ast.PDLRuntimeError: model 'ollama/granite' encountered ConnectError('[Errno 111] Connection refused') trying to POST against http://localhost:11434/api/chat
```

Rubric: L1 W2 Y2 F1 H0 = 6/15

### Target

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:2 - model 'ollama/granite' encountered ConnectError('[Errno 111] Connection refused') trying to POST against http://localhost:11434/api/chat
```

Rubric: L1 W2 Y2 F1 H3 = 9/15

Same shape: line 1 unchanged, the rest deleted. This entry keeps the method and the URL,
which is the most useful thing in the whole E-MODEL class and is why W is 2 rather than 1.

---

## The decision: what the callback does

### 1. The discriminator is `future.exception()`, not `try/except` around `.result()`

The callback's only job is bookkeeping: usage counters and an end timestamp. A failed
call has no usage. So the failed case is not an error *in* the callback, it is a case the
callback has nothing to do for, and it should be recognised as such before any work
starts:

```python
def update_end_nanos(future):
    import time

    if future.cancelled():
        return
    if future.exception() is not None:
        # The model call failed. There is no usage to record, and the failure is
        # reported by whoever forces the result on the main thread -- see below.
        return
    try:
        result = future.result()[1]
        ...                       # the existing body, unchanged
    except Exception as exc:      # pylint: disable=broad-except
        _report_recording_bug(block, model_id, exc)
```

Why this shape and not `except Exception: pass` around the whole body — the trap the
brief names, and it is a real one. A blanket handler cannot tell these two apart:

| Event | What it means | Right response |
| --- | --- | --- |
| the *future* holds an exception | the provider call failed; there is nothing to record | do nothing, silently |
| the *body* raises | `result["usage"]` is missing a key, a counter is not an int, `state.add_usage` is broken — a bug in PDL | say so, once, and keep going |

`Future.exception()` separates them exactly, and it is the natural discriminator because
it is the one accessor that *reports* the stored exception instead of raising it.
Two properties matter and both are worth stating because the code depends on them:

- Inside a done-callback the future is already done, so `exception()` returns
  immediately; it cannot deadlock the event-loop thread the way a `.result()` with a
  timeout could.
- `exception()` raises `CancelledError` for a cancelled future, which is why
  `future.cancelled()` is tested first rather than relying on the exception test alone.
  A cancelled call has no usage either, so it takes the same early return.

**Unverified — I have no shell.** Confirm both properties, and that a guarded callback
prints nothing, with:

```
python - <<'EOF'
from concurrent.futures import Future
seen = []
f = Future(); f.set_running_or_notify_cancel()
f.add_done_callback(lambda fut: seen.append((fut.cancelled(), type(fut.exception()).__name__)))
f.set_exception(ValueError("boom"))
g = Future()
g.add_done_callback(lambda fut: seen.append(("cancelled", fut.cancelled())))
g.cancel()
print(seen)
EOF
```

Expected: `[(False, 'ValueError'), ('cancelled', True)]` and **no** `exception calling
callback` text on stderr.

The `except Exception` around the body is narrow in scope but broad in type on purpose:
its job is to stop a bookkeeping bug from becoming a 20-frame off-thread wall, which is
the same §5.8 violation this whole item exists to remove. It does not swallow the bug —
it renames it. `_report_recording_bug` prints, once, to stderr:

```
prog.pdl:2 - internal error while recording usage for the model call to 'ollama/granite': KeyError('usage')

  This is a bug in PDL, not in your program. The model call itself succeeded and
  its result is unaffected; only the usage and timing bookkeeping failed.

  help: please report it, with your program, at
        https://github.com/IBM/prompt-declaration-language/issues
        Set PDL_TRACEBACK=1 to print the stack trace of this internal error.
```

Three points about that diagnostic:

- It can only fire on a **successful** model call, because the failed future returned
  early. So it never competes with a real PDL diagnostic for the same block.
- It prints from the event-loop thread, so its position relative to other stderr output
  is not deterministic. I am accepting that here, and only here: it is a
  should-never-happen path, no corpus entry produces it, and therefore no golden can
  flake on it. If an entry is ever written for it, the deterministic form is to append
  the record to a list on `InterpreterState` and flush it on the main thread; that is a
  new public field on a pydantic model and is not worth paying for a path that indicates
  a bug.
- `PDL_TRACEBACK=1` does not exist yet — `E-BOUNDARY.md:61` reserved the name and
  `INVENTORY.md` records that the last-resort handler that would have introduced it was
  deferred. Grepping the tree finds it only in that spec. So this commit makes it real
  *for this site*: `if environ.get("PDL_TRACEBACK"): traceback.print_exc()`, two lines,
  `traceback` and `os.environ` are stdlib and `environ` is already imported
  (`pdl_llms.py:3`, `pdl_openai.py:3`). The `help:` line above is therefore true when
  followed. If a reviewer would rather not introduce the variable in this commit, delete
  that one line of the `help:` — the rest of the diagnostic stands alone. Do not ship the
  line without the two lines of code; a `help:` that does nothing is the failure mode
  this project has already hit twice.

### 2. Nothing else is reported, and that is now measured rather than argued

The case for still saying *something* on the failed-future path is that a report you can
see beats a report you cannot. The case against is stronger, on three grounds:

1. **The main thread always reports.** On every path where the provider is actually
   contacted, the `PDLRuntimeError` reaches `generate` and is printed with a location.
2. **A second report is sometimes not merely noise but wrong.** `fallback:` and `retry:`
   catch the exception on the main thread (`pdl_interpreter.py:786-820`) and carry on. A
   program with a `fallback:` on its model block today prints a ~20-frame traceback and
   then **exits 0**. There is no reading of that output in which the traceback is helping.
3. **The callback cannot know whether the main thread will report.** It runs at the
   moment the future completes, strictly before the main thread has finished unwinding.
   Any "report only if nobody else did" rule needs state the callback does not have at
   the time it runs, so any conditional print is a guess.

Point 1 was the one that could have made this a correctness bug rather than a cosmetic
one, and it was checked, not assumed. I had identified two programs that looked like a
model call whose failure nobody would ever force — with `contribute: []` the block's
result is replaced by `PdlConst("")` at `pdl_interpreter.py:478-479`, and a `defs:` entry
that is never referenced is never forced either:

```yaml
text:
  - model: ollama/granite
    input: hi
    contribute: []
```

The coordinator instrumented the LiteLLM stub to write a marker file the moment
`acompletion` is entered and ran both against a positive control:

| program | provider contacted | exit |
| --- | --- | --- |
| result used (control) | **yes** | 1 |
| `contribute: []` | **no** | 0 |
| unused `defs:` | **no** | 0 |

The provider is never dialled in the discarded cases, so there is no failure and nothing
to report; exit 0 is correct. (Mechanically this is consistent with the event loop being
a **daemon** thread — `pdl_scheduler.py:22` — which the process does not wait for. Either
way the measurement is what settles it: no report is lost.)

**Conclusion: silence in the callback is safe.** Not "quieter" — safe.

### 3. The race is removed, not narrowed

Both `case.json` files carry the same note: 8/8 measured runs printed the diagnostic
first, and the reverse order was observed once during a regeneration. The mechanism is
two writers to one stream. `Future.set_exception` releases the waiter on the main thread
and then invokes the done-callbacks on the event-loop thread; from that instant both
threads are runnable, one heading for `print(message, file=sys.stderr)` in `generate` and
the other for `_invoke_callbacks`' logging of a traceback. Which lands first is up to the
interpreter's scheduler.

After this fix the failed-future path has **exactly one writer to stderr**: the main
thread. Not "the second writer is less likely" — the second writer does not run. That is
what makes this a removal rather than a narrowing, and it is the property to check in
review: after the guard, is there any `print`/logging reachable from the callback when
`future.exception() is not None`? There must not be. The other off-thread writers in the
same function (`pdl_llms.py:124-139`, `pdl_openai.py:196-211`) are inside
`if "PDL_VERBOSE_ASYNC" in environ`, on the success path only, and the corpus harness
does not set that variable.

**Goldens after the fix.** Both entries become the seven-line transcript shown under
Target: header, empty stdout, one stderr line. In the same commit:

- delete `"hygiene_traceback_expected": true` from both `case.json` files, or
  `test_no_traceback` XPASSes and fails the suite (`tests/errors/test_corpus.py:74-80`);
- update both rubric blocks to the Target scores;
- rewrite the `notes` field of both: the KNOWN FLAKE paragraph is now false and must not
  survive the commit that makes it false. Replace it with one sentence recording that the
  duplicate report was removed at `pdl_llms.py`/`pdl_openai.py` and that the entries are
  now single-writer.
- regenerate with `python tests/errors/regen.py E-MODEL` and read the diff; the whole
  diff should be deletions plus the golden's trailing traceback block disappearing.

A cheap standing check that the race is gone, since it cannot be proven by one run:
run the entry 20 times and assert one distinct transcript. `test_order_instability_is_real`
(`tests/errors/test_corpus.py:105`) is the existing precedent for that style, though it is
keyed on `hygiene_unstable_order`, which these entries do not carry — their instability
was thread interleaving, not hash seed. Do not add the flag; add the repeat-run check to
the commit message as the evidence, or leave it out. Do not paper it over by pinning
order.

---

## Structured record

Decision 5.6. The record for the two corpus entries is the one the existing
`PDLRuntimeError` already implies; this item changes no field of it. It is written out
here because the contract is the record, and because it shows exactly which fields the
renderer is still throwing away (item 7).

```json
{
  "id": "E-MODEL-002",
  "severity": "error",
  "origin": "program",
  "file": "prog.pdl",
  "spans": [{"line": 2, "col": null, "primary": true}],
  "block_path": ["text", "[0]"],
  "message": "model 'ollama/granite' encountered ConnectError('[Errno 111] Connection refused') trying to POST against http://localhost:11434/api/chat",
  "notes": [],
  "suggestions": []
}
```

E-MODEL-001 is the same with `message` from the `except Exception` branch
(`pdl_llms.py:64`). `block_path` is carried and **not rendered today**: `get_loc_string`
(`pdl_location_utils.py:94-99`) builds `file:line - ` and discards `loc.path`. That is
item 7's job across all ~70 IDs, and when it lands these two entries gain
`  in text[0]` with no change to this spec.

The internal-error path is a second, new record. It has no ID registry to draw on — item
0 owns that — so the id below is provisional:

```json
{
  "id": "E-INTERNAL-USAGE-RECORDING",
  "severity": "error",
  "origin": "program",
  "file": "prog.pdl",
  "spans": [{"line": 2, "col": null, "primary": true}],
  "block_path": ["text", "[0]"],
  "message": "internal error while recording usage for the model call to 'ollama/granite': KeyError('usage')",
  "notes": [
    {"kind": "rule",
     "text": "This is a bug in PDL, not in your program. The model call itself succeeded and its result is unaffected; only the usage and timing bookkeeping failed."}
  ],
  "suggestions": [
    {"text": "please report it, with your program, at https://github.com/IBM/prompt-declaration-language/issues"},
    {"text": "Set PDL_TRACEBACK=1 to print the stack trace of this internal error."}
  ]
}
```

---

## Where the data comes from

Fix sites: `src/pdl/pdl_llms.py:96-141` and `src/pdl/pdl_openai.py:168-213` — the same
function, twice.

| Field | Source | Available at the raise site? |
| --- | --- | --- |
| "did the call fail" | `future.exception()`, `future.cancelled()` on the callback's own argument | yes |
| `file`, `line` | `block.pdl__location`, rendered by `get_loc_string` (`pdl_location_utils.py:94`) — the same `loc` `async_generate_text` already passes to `PDLRuntimeError` at `pdl_llms.py:57`/`:65` | yes; `pdl_llms.py` does not import `get_loc_string` today, so import it inside the failure branch as the file already does for `time` and `termcolor` — that also sidesteps any import-cycle question |
| `block_path` | `block.pdl__location.path` | yes, carried, unrendered until item 7 |
| `model_id` (internal-error text) | the `model_id` parameter of `generate_text`, closed over by the callback (`pdl_llms.py:76`, `pdl_openai.py:148`) | yes |
| the recording bug itself | the `except Exception as exc` binding | yes |
| `PDL_TRACEBACK` | `environ`, already imported at `pdl_llms.py:3` and `pdl_openai.py:3`; `traceback` is stdlib and not yet imported in either file | new import, stdlib |
| the *reported* diagnostic for the failure | unchanged: `PDLRuntimeError` → `generate`, `pdl_interpreter.py:249-257` | yes |

Nothing here needs data the interpreter does not have. No new dependency: `concurrent.futures`,
`os.environ` and `traceback` are stdlib.

**One structural recommendation.** The two callbacks are byte-identical apart from
`result["usage"]` vs `result.get("usage")` (`pdl_llms.py:101-104` vs
`pdl_openai.py:174-176`). Fixing them by copy-paste produces two copies of the guard,
which is how this defect got duplicated in the first place. Factor one
`make_model_call_done_callback(state, block, model_id)` and have both call it. It belongs
in `pdl_scheduler.py`, which imports only `pdl_ast` and `pdl_utils` (`:1-8`) and is
already a transitive import of both files through `pdl_interpreter_state`, so there is no
cycle. Note while you are there that `pdl_granite_io.py:117-127` registers **no**
callback at all — so granite-io model calls record neither usage nor timing. That is a
pre-existing gap, not a regression, and it is out of scope; do not "fix" it by adding an
unguarded copy of the callback.

**Optional, recommended, separable — stamp `end_nanos` on the failure path.** Today
`future.result()` raises at `pdl_llms.py:99`, *before* the timing block at `:113`, so a
failed call gets no end timestamp at all. A failed call has a real duration, and issue
**#411** ("ErrorBlocks lack timing information", INVENTORY §4) is exactly this complaint.
The early return can set `block.pdl__timing.end_nanos = time.time_ns()` before returning
— but it must not enter the `PDL_VERBOSE_ASYNC` branch, which would print "completed in
Xms" for a call that did not complete. No test pins timing fields (`end_nanos` and
`pdl__timing` appear nowhere under `tests/` except inside these two goldens' tracebacks),
so the risk is a trace-content change with no golden behind it. It does not close #411 —
the `ErrorBlock` itself still has no timing; only the `program=block` it wraps gains one.
Land it as a second commit so the hygiene fix stays a pure deletion.

---

## Rejected alternatives

**Catch and print a one-line note from the callback, e.g. `note: the model call to 'X'
failed; see the message above`.** Attractive because it keeps *some* report on any path.
Rejected on the measurement in §2: there is no path where it would be the only report, so
it is guaranteed-redundant text. Worse, it is written from the event-loop thread, so it
reintroduces the exact interleaving nondeterminism this item exists to remove — "see the
message above" would sometimes print above the message. A diagnostic that can lie about
its own ordering is worse than no diagnostic.

**Wrap the whole callback body in `try/except Exception: pass`.** Two lines, removes both
tracebacks, passes `test_no_traceback`. Rejected because it makes the two cases in §1
indistinguishable: a `KeyError` on the usage dict — a real PDL bug, and this dict comes
from a provider whose response shape is not under PDL's control, so it is a *plausible*
bug — becomes permanently invisible. Usage counters would silently read zero with nothing
anywhere saying why. The guard costs three more lines and keeps that debuggable.

**Attach the location to the callback's report and let both print.** Rejected on the
"one diagnostic, one location prefix" rule: the same failure with the same location
printed twice is the defect, and prettifying the duplicate does not make it one
diagnostic.

---

## Risk

**No AST change. No public API change. No trace-format change** (unless the optional
`end_nanos` stamp is taken, which adds a field to a trace that previously omitted it and
is why it is proposed as a separate commit). **No new dependency.** `PDL_TRACEBACK` is
additive and reuses the name `E-BOUNDARY.md` reserved.

**Message-asserting tests.** None break: the message text is unchanged, and both fix
sites are inside a done-callback that no test calls directly. `tests/test_runtime_errors.py`
asserts Jinja, JSON-parser and regex messages; nothing asserts on model-call text. The
only files that mention `update_end_nanos` outside `src/` are these two goldens, and they
mention it inside the traceback that is being deleted.

**The two goldens and their flags**, as listed in §3, must land in the same commit. Do not
regenerate the whole corpus for this change — `regen.py E-MODEL` touches exactly the two
entries.

**A behaviour change that is not visible in the corpus but is visible to users**: a
program whose model block has `fallback:` or `retry:` currently prints a traceback and
exits 0. After this fix it prints nothing extra and still exits 0. That is the intended
outcome, but it is the one case where output *disappears from a successful run*, and
anyone grepping stderr for evidence that a fallback fired will notice. There is no corpus
entry for it; if the reviewer wants one, `fallback:` over a failing model is a three-line
program and would be a good addition to the corpus in this commit — it pins the
"successful run is now silent" contract that this fix establishes.

**Measured, and it does not happen.** The corpus runs these entries with `--stream none`
(`tests/errors/harness.py:51`), and this item worried that the CLI *default*
(`--stream result`) might print a doubly-wrapped message, because
`generate_client_response_single` forces `message.result()` at
`pdl_interpreter.py:2491`, inside `process_call_model`'s `try`, whose `except Exception`
at `:2285` re-wraps into `f"Error during '{model_id}' model call: {repr(exc)}"` — and
`PDLRuntimeError` derives from `PDLException(Exception)` (`pdl_ast.py:1631`, `:1675`), so
nothing type-wise stops that clause from matching.

Both modes were run, with the corpus's own stub and scrubbed environment, before and
after this fix. Each prints exactly one clean located line and exits 1, and the two lines
differ only because the two modes call different LiteLLM entry points:

```
$ pdl prog.pdl                # batch=0 -> generate_text_stream -> litellm.completion
prog.pdl:2 - Error during 'ollama/granite' model call: RuntimeError('synchronous litellm.completion is not stubbed')
$ pdl --stream none prog.pdl  # batch=1 -> generate_text -> litellm.acompletion
prog.pdl:2 - model 'ollama/granite' encountered ConnectError('[Errno 111] Connection refused') trying to POST against http://localhost:11434/api/chat
```

The double wrap cannot occur: the message a `PDLRuntimeError` would be wrapped *in*
only exists on the async path, and the default streams instead, so no `Future` and no
`PDLRuntimeError` reach `:2285` at all. Nothing to chase, and no second corpus entry is
owed. (The stub deliberately does not implement the synchronous `completion`; that is why
the default mode's text names a `RuntimeError` rather than the connection failure. It is
still a single wrap, which is the point at issue.)

---

## E-MODEL-005 — out of scope, and genuinely unaffected

E-MODEL-005 (upstream issue #383, a malformed `input:` message reported as a model-call
failure) is in the same class and the brief asks whether this fix moves it. **It does
not**, and the reason is structural rather than incidental.

Its `TypeError("'<class 'str'>' object is not a valid context")` comes from
`ensure_context` (`pdl_context.py:156`), which is called at
`pdl_interpreter.py:2221` — on the **main thread**, while building the model input,
*before* `generate_client_response` is reached at `:2258`. It is caught by
`process_call_model`'s own `except Exception` at `:2285` and re-raised as
`PDLRuntimeError`. No coroutine is scheduled, no `Future` is created, no done-callback is
registered. There is nothing for this fix to touch.

E-MODEL-005 has no corpus entry yet (`tests/errors/corpus/` contains only E-MODEL-001 and
E-MODEL-002), so there is no golden to check this claim against. When one is written it
should show a single located line and no traceback both before and after this commit.

---

## Not in this spec

The message text. Under items 0 and 7 these two entries gain `file:line:col`, a source
excerpt with a caret under the `model:` value, and `  in text[0]` — from the record
fields they already carry, with no change here. Raising **What** and **Fix** is a separate
question about how PDL frames a provider failure it does not understand
(`E-MODEL-001` wants "did you mean `ollama/...`?"-class help keyed on the provider prefix;
`E-MODEL-002` wants "is the server running?"-class help for `ConnectError` specifically).
Both need a recognizer table over provider errors, the E-PARSE-001 pattern applied to a
different vocabulary, and neither should be smuggled into a hygiene commit.

---

**Expected rubric delta:** 10/30 → **17/30** across the two (E-MODEL-001 4→8,
E-MODEL-002 6→9; 4→7 and 10→16 if the scorer counts the LiteLLM class-name prefix as a
hygiene leak). Two of the corpus's remaining S0 traceback entries close here, the
`update_end_nanos` duplicate in `pdl_openai.py` is fixed before it gets its own entry, and
the two KNOWN FLAKE annotations are deleted rather than tolerated.

**One sentence a user takes away:** "It told me my model call failed, once, on the line
that made it — and stopped pasting PDL's own stack into my terminal."
