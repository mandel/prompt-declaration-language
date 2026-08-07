# The error-reporting team

Six agents, defined in [`.claude/agents/`](../../.claude/agents/). They exist to
move PDL's diagnostics from the Phase-1 baseline of **190/660 rubric points** to
something a user can act on.

Read [`INVENTORY.md`](INVENTORY.md) for the taxonomy and the standing design
decisions, [`RUBRIC.md`](RUBRIC.md) for how a diagnostic is scored, and
[`BASELINE.md`](BASELINE.md) for where things stand.

---

## Roles and tool grants

| Agent | Tools | May write | Owns |
| --- | --- | --- | --- |
| **corpus-author** | Read, Grep, Glob, Write, Edit, Bash | `tests/errors/corpus/`, inventory rows | Reproducers, goldens, baseline scores |
| **diagnostic-designer** | Read, Grep, Glob, Write | `docs/error-reporting/specs/` | The target text and structured record for one error ID |
| **location-tracker** | Read, Grep, Glob, Edit, Write, Bash | Location subsystem + record/renderer | Source provenance, end to end |
| **implementer** | Read, Grep, Glob, Edit, Write, Bash | `src/`, `pdl-live-react/`, message-asserting tests | Turning a spec into code |
| **critic** | Read, Grep, Glob | *nothing* | Scoring the output a user sees |
| **regression-guard** | Read, Grep, Glob, Bash | *nothing* | Ruling on whether anything broke |

Only **implementer** and **location-tracker** can touch `src/`, and their file
ownership does not overlap — see the deviation note below.

---

## The Phase-3 loop

One error ID at a time, in the priority order in `INVENTORY.md` §6.

```
corpus-author ──▶ diagnostic-designer ──▶ implementer ──▶ critic ──▶ regression-guard ──▶ commit
   (if no                 spec              code +          score        full check
    coverage)                               golden          ACCEPT/       PASS/FAIL
                                                            REJECT
                             ▲                  │              │              │
                             └──── REJECT ──────┴──────────────┘              │
                                                                              │
                             └──────────── FAIL: back to implementer ─────────┘
```

Rules that make the loop work:

- **One error ID per commit, on its own branch or worktree.** `pdl_interpreter.py`
  has 56 raise sites; parallel work collides there otherwise.
- **The golden lands in the same commit as the code.** The golden diff *is* the
  record of the UX change. A behaviour change without one is incomplete.
- **`critic` can reject, and rejection sends work back** — to the designer if the
  target was wrong, to the implementer if the execution was.
- **Stop and report to the human** on any AST change, public-API change, or new
  runtime dependency.

Items 1–7 of the Phase-3 order are independent and parallelise across worktrees.
Item 0 (the foundation) and items 8–10 serialise on it.

---

## Deviations from the originally-suggested team

Four changes, each forced by something recon or Phase 1 turned up.

**1. `location-tracker` owns the whole foundation, not just locations.**
Decisions §5.1 (YAML marks), §5.2 (source registry) and §5.6 (structured records)
are one coupled change: the renderer is what finally makes `loc.path` visible, and
the record cannot be designed independently of the location model. Splitting them
across two agents guarantees collisions in `pdl_parser.py` and
`pdl_location_utils.py`, the two files everything else depends on. Single owner,
single branch.

**2. `implementer` extends to `pdl-live-react/`.**
The suggested team had no TypeScript coverage, but §5.2 changes the trace format,
which forces a matching viewer change and `npm run types`. Rather than add a
seventh role for a handful of files, the implementer's remit covers it explicitly.

**3. `diagnostic-designer` produces a structured record, not prose.**
Direct consequence of §5.6. The spec now carries the record — id, severity, file,
span, block path, message, notes, suggestions — with the human text as a rendering
of it. This also sharpens `critic`, which can score fields rather than infer
structure from a string.

**4. No Rust role.** §5.7 puts the Rust interpreter out of scope. It stays
documented at parity zero (`E-RUST-001`) and gets revisited after the Python side.

And one correction to the brief itself: `regression-guard` was specified with the
ability to update message-asserting tests, which requires write access — in
tension with "only the implementer gets Edit/Write on src/". A gate that can
repair what it inspects is not a gate. Test updates moved to **implementer**, who
changed the message and knows what it should now say; `regression-guard` is
read-only and rules instead.

---

## What is enforced, and what is not

**Enforced by tooling.** `critic` and `regression-guard` have no `Edit` or
`Write`, so they cannot modify anything. `diagnostic-designer` has `Write` but no
`Edit` or `Bash`, so it cannot alter existing files or run anything.

**Enforced only by instruction — know this.** Agent frontmatter grants *tools*,
not *paths*. `critic` is told not to read `src/`, and its `Read`/`Grep` can reach
`src/` anyway. Its independence is a convention, not a sandbox. The same applies
to `corpus-author` being told to leave the harness alone and to the
implementer/location-tracker file split.

Two consequences worth planning around: if `critic` starts justifying a message
by how it is produced, its verdict is compromised and should be re-run fresh; and
the file-ownership split between `implementer` and `location-tracker` needs
respecting in scheduling, not just in prompts — don't run both against
`pdl_parser.py` at once.

**Enforced by the harness**, which is the strongest guarantee available and the
reason Phase 1 came first: goldens catch any message change, `test_no_traceback`
catches a leak, the `hygiene_traceback_expected` flag fails the suite when a leak
is fixed but not acknowledged, and the offline socket guard means no agent can
accidentally write a test that depends on a live model.
