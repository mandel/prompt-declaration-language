# PDL Diagnostic Rubric

Every diagnostic is scored 0–3 on five dimensions, for a maximum of **15**. Scores live
in `tests/errors/corpus/<ERROR-ID>/case.json` next to the reproducer they describe, so a
score can never drift away from the output it was assigned to.

The scores are a *baseline instrument*, not a target. A 15 is rarely worth chasing; the
work is in moving the many 0–4s into the 8–12 range.

Models: rustc's diagnostic guidelines and the Elm compiler's error messages. Both treat a
diagnostic as a short document with a location, a claim, evidence, and a next action —
not as a sentence.

---

## 1. Location

*Where is the problem?*

| Score | Criterion |
| --- | --- |
| **0** | No location at all, **or a location that is wrong** — a confidently-stated incorrect line is worse than none, so it scores here rather than at 1. |
| **1** | File and line, but coarse or approximate: points at an ancestor block, at `:0`, or at the enclosing key rather than the offending element. |
| **2** | Accurate `file:line`, plus **either** a column **or** the block path (e.g. `text[2].model.input`). |
| **3** | `file:line:col`, the block path, a source excerpt, and a caret span under the offending token. For an error inside an `include`/`import`/function call, also the chain that reached it. |

## 2. What

*Which rule was violated, in PDL's own vocabulary?*

| Score | Criterion |
| --- | --- |
| **0** | No rule stated, or the message is an internal exception repr — `TypeError(...)`, a Pydantic `ValidationError`, a Jinja `UndefinedError`. |
| **1** | A rule is implied but phrased in implementation vocabulary: `should be of type <class 'str'>`, `'dict object' has no attribute`. |
| **2** | Stated in PDL vocabulary, but generic or imprecise — names the problem without naming the construct. |
| **3** | Names the PDL construct and the rule it violated, in the language the documentation uses. A reader who knows PDL but not its implementation understands it. |

## 3. Why

*What was found, and what was expected?*

| Score | Criterion |
| --- | --- |
| **0** | Neither the offending value nor the expectation is shown. |
| **1** | One of the two. |
| **2** | Both, but raw, unformatted, or badly truncated — e.g. a 700-character schema blob technically containing the expectation. |
| **3** | Both, formatted for reading, with enough surrounding context to see the mismatch. For a parse failure, the offending text itself (issue #387). |

## 4. Fix

*What should the user do next?*

| Score | Criterion |
| --- | --- |
| **0** | No suggestion. |
| **1** | A generic pointer — "see the documentation", "check the schema". |
| **2** | Names the valid alternatives: the permitted keys, the expected block kinds, the variables in scope. |
| **3** | A specific, actionable suggestion: `did you mean \`description\`?` for a near-miss key, a concrete edit, or the exact form the construct should take. |

## 5. Hygiene

*Is this a well-formed diagnostic?*

| Score | Criterion |
| --- | --- |
| **0** | A Python traceback reaches the user, **or** the output is nondeterministic between runs. |
| **1** | No traceback, but a raw internal dump: a JSON-Schema blob, a `repr` chain, a wall of union branches, a Python list repr. |
| **2** | Clean prose with cosmetic defects — a location prefix repeated on every line of one diagnostic, absolute paths, ANSI codes on a non-tty, a duplicated warning. |
| **3** | A single well-formed diagnostic. Stable across runs, correct exit code, no internal vocabulary, no leakage. |

---

## Scoring conventions

**Score what the user sees.** The transcript in `expected.txt` is the evidence — exit
code, stdout and stderr together. If a diagnostic is correct but arrives behind a
traceback, Hygiene is 0 and Location is scored on what is actually legible.

**Wrong beats missing, downward.** A confidently-stated wrong location scores Location 0,
below a diagnostic with no location at all. Misdirection costs a user more than silence.

The two entries this convention was written for — E-EXPR-004, whose cross-file error
reported the importing file's line, and E-EXPR-006, whose line was shifted by comments —
both score **2** now: Phase-3 item 0 replaced the regex line scanner with real YAML marks
and the wrong lines went with it. They are kept named here because the convention is what
made them worth fixing first, and because nothing in the corpus currently scores Location
0 for wrongness: the remaining 0s are entries with no location at all. If a future change
makes a header confidently wrong again — the risk that deferred `:col` twice — this is the
rule that applies.

**Don't credit accidents.** Where a coarse location happens to be right because the
construct is one line long, score the mechanism, not the luck.

**Provider text is out of scope for What/Why.** For `E-MODEL-*`, score how PDL *frames*
the failure — location, whether it is wrapped or leaked, whether the block is named — not
the wording LiteLLM or a provider chose. That text is stubbed in the corpus precisely so
it cannot drift the score.

**Three hygiene sub-flags** are recorded in `case.json` alongside the scores, because they
drive tests and counts rather than arithmetic:

- `hygiene_traceback_expected` — this entry currently leaks a traceback. Marks
  `test_no_traceback` xfail. Removing the leak makes the test XPASS and *fails the
  suite* until the flag is deleted, so a fix cannot be silently under-claimed.
- `hygiene_unstable_order` — this entry's message order varies with `PYTHONHASHSEED`.
  The harness pins the seed so goldens stay stable; these flags keep the instability
  visible rather than papered over.
- `hygiene_silent_failure` — this entry produces **no diagnostic at all**: a broken
  program that exits 0 and says nothing. It drives the "fail silently" count in
  `BASELINE.md`. It is a flag rather than a derived predicate because the count must
  self-heal when an entry is fixed, and `report.py` reads only `case.json` — it never
  runs PDL, so it cannot inspect a golden's stderr. An earlier version derived the count
  from `expect_exit == 0 and severity == "S0"` and miscounted three entries: two whose
  defect was a *wrong* message rather than a missing one, and one that the fix had
  already repaired.

---

## Aggregate baseline

Generated by `python tests/errors/report.py`; the committed table is in
[`BASELINE.md`](BASELINE.md). Re-run it after any Phase-3 change and commit the delta —
the movement is the deliverable, not the absolute number.
