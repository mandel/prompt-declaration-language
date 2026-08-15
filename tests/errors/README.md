# PDL error-reporting corpus

Golden tests for what a user sees when their PDL program is wrong. Companion to
[`docs/error-reporting/INVENTORY.md`](../../docs/error-reporting/INVENTORY.md)
(the taxonomy), [`RUBRIC.md`](../../docs/error-reporting/RUBRIC.md) (how a
diagnostic is scored) and [`BASELINE.md`](../../docs/error-reporting/BASELINE.md)
(where things stand).

## Layout

```
corpus/<ERROR-ID>/
    case.json      how to run it, expected exit code, rubric scores, notes
    prog.pdl       the reproducer (plus any sibling files it needs)
    expected.txt   golden transcript: exit code + stdout + stderr, normalized
```

`<ERROR-ID>` is a taxonomy ID from the inventory, so a corpus entry and its
analysis are always findable from each other. One ID may need more than one
entry, in which case the extras take the ID plus a hyphenated suffix naming the
branch they pin — `E-SCHEMA-006-fallback` is the "the analyzer could not
localise it" branch of `E-SCHEMA-006`, which that ID's first reproducer stopped
reaching once the analyzer learned to localise it. `report.py` counts entries
and covered IDs separately for exactly this reason.

## Running

```bash
pytest tests/errors/                      # everything
pytest tests/errors/ -k E-SCHEMA          # one class
python tests/errors/regen.py E-PARSE      # rewrite goldens after a change
python tests/errors/report.py --write     # refresh BASELINE.md
```

Regenerating is not a rubber stamp: **read the diff**. A golden diff is the
record of the UX change and belongs in the same commit as the code that caused
it.

## What the harness guarantees

**Offline.** `sitecustomize_stub/sitecustomize.py` goes on `PYTHONPATH` and
patches `socket.connect` to raise on any non-loopback address. A corpus entry
that tries to reach the network fails rather than hanging or silently passing in
CI. `E-MODEL-*` entries get their failures from a LiteLLM stub selected by
`PDL_TEST_MODEL`.

**Deterministic.** Scrubbed environment, fixed `TZ`, fixed `PYTHONHASHSEED`, a
private working directory per run, and normalization of paths, addresses and
timings.

**Honest about tracebacks.** Normalization rewrites paths inside a traceback but
never removes the traceback. A traceback in a golden is the visible symptom of an
S0 entry, and it must stay visible so the commit that fixes it shows the
traceback disappearing.

## The two xfail mechanisms

Both exist so that a fix cannot be quietly under-claimed.

`hygiene_traceback_expected` in `case.json` marks an entry that currently leaks a
Python traceback. It makes `test_no_traceback` xfail. When the leak is fixed the
test XPASSes and **the suite fails** until the flag is removed — so the fix has
to be acknowledged.

`hygiene_unstable_order` marks an entry whose message order varies with the hash
seed (`analyze_errors` iterates `set` differences). The harness pins the seed to
keep goldens stable, which would otherwise hide the problem entirely; this flag
keeps a failing test pointed at it. A companion test re-runs the entry across six
seeds and fails if the instability has silently gone away.

## Adding an entry

1. `mkdir corpus/E-THING-001` and write `prog.pdl`.
2. Write `case.json` with `title`, `severity`, `expect_exit`, `rubric` and
   `notes`. Score against `RUBRIC.md` *after* seeing the output, not before.
3. `python tests/errors/regen.py E-THING-001`, then read the golden.
4. `python tests/errors/report.py --write`.

Useful `case.json` keys beyond the obvious: `entry` (`pdl`, `pdl-lint`, or
`python-m-pdl`), `argv`, `env`, `cwd` (a subdirectory to run in — needed when the
tool's behaviour depends on where the project root is), and `skip`. `argv`
entries may contain `{WORKDIR}`, substituted with the run directory, for cases
that need an absolute path.
