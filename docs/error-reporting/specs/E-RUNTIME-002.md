# E-RUNTIME-002 — `import:` names a file that does not exist

Phase-3 item 4, first half. The sibling `include:` entry (E-RUNTIME-001) is deliberately
**not** folded in; see "Follow-up: `include:`" at the end.

> **Citations point at `60b27f4`**, the tree this spec was written against — not at the
> current tree. Read one with `git show 60b27f4:src/pdl/pdl_interpreter.py`. Symbol names
> survive; line numbers do not.

## Today

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
Traceback (most recent call last):
  File "<VENV>/bin/pdl", line <LINE>, in <module>
    sys.exit(main())
  ... 8 frames through pdl_interpreter.py ...
  File "<REPO>/src/pdl/pdl_interpreter.py", line <LINE>, in process_import
    with open(file, "r", encoding="utf-8") as pdl_fp:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: 'nosuch.pdl'
```

Rubric: L0 W0 Y1 F0 H0 = 1/15

The program is one line, `import: nosuch`. Three separate things are wrong with the last
line of that traceback, and only the first is the traceback itself:

1. It is a Python traceback (H0, §5.8 invariant violated).
2. It names `nosuch.pdl`. The user wrote `nosuch`. Both the `.pdl` and — in the general
   case — the directory prefix are PDL's, not theirs (`pdl_interpreter.py:3089-3091`).
3. It says nothing about *where* PDL looked, which is the one fact that unblocks a user
   who ran `pdl` from a directory other than the one they were thinking of.

Why the boundary work missed it: `process_include` calls `parse_file`
(`pdl_interpreter.py:3061`) and so inherited `PDLFileNotFoundError` for free.
`process_import` does its own `open`/`read` at `pdl_interpreter.py:3093-3094`, because
the cache `state.imported` is keyed on the **source text** `prog_str`
(`:3096`, `:3108`), so no shim is ever constructed.

## Target

The corpus work directory holds only `prog.pdl`, so neither the near-miss branch nor the
listing branch can fire. This is the weakest branch of the diagnostic, shown honestly;
the richer branches are tabulated below it.

```
$ exit: 1

--- stdout ---
(empty)

--- stderr ---
prog.pdl:1 - cannot import `nosuch`: no such file `nosuch.pdl`
  in import

  `import: nosuch` looks for the file `nosuch.pdl`: PDL appends `.pdl` to an
  import path that does not already end in it. Nothing exists at that path,
  and the current directory contains no other `.pdl` files.

  help: check the path; it is resolved relative to the current directory.
```

Rubric: L2 W3 Y3 F1 H3 = 12/15

Location is 2 and not 3 today: `file:line` plus the block path, no column and no excerpt.
Both of the missing halves need item 0 (see "Where the data comes from"). After item 0
the *same record* renders as below, with no re-design and no change to any other field —
Location 3, total 13/15:

```
prog.pdl:1:9 - cannot import `nosuch`: no such file `nosuch.pdl`
  in import

1 | import: nosuch
  |         ^ no file `nosuch.pdl`

  `import: nosuch` looks for the file `nosuch.pdl`: PDL appends `.pdl` to an
  import path that does not already end in it. Nothing exists at that path,
  and the current directory contains no other `.pdl` files.

  help: check the path; it is resolved relative to the current directory.
```

### The headline: what the user wrote, then what PDL opened

One rule covers both distortions the corpus note complains about — the appended `.pdl`
*and* the directory prefix — because both are the same distortion: the string PDL opened
is not the string the user typed.

| Condition | Headline |
| --- | --- |
| resolved path differs from what was written | ``cannot import `nosuch`: no such file `nosuch.pdl` `` |
| resolved path is what was written (suffix given, `state.cwd` is `.`) | ``cannot import `nosuch.pdl`: no such file `` |

The written form always comes first, because that is the token the user will search their
file for. The resolved form is shown once, in back-ticks, and never repeated in the
`help:`. Neither form is ever made absolute (issue #410): `state.cwd` is
`Path(<program>).parent`, so a program given as a relative path yields a relative
resolved path — which is what the traceback already shows today (`sub/nosuch.pdl`) — and a
program given as an absolute path yields an absolute one, in the user's own form.

### The rule sentence: why the two forms differ

First sentence of the rule paragraph, chosen from the two independent reasons:

| wrote `.pdl`? | `state.cwd` is `.`? | First sentence |
| --- | --- | --- |
| no | yes | ``\`import: nosuch\` looks for the file \`nosuch.pdl\`: PDL appends \`.pdl\` to an import path that does not already end in it.`` |
| no | no | same, plus ``It is resolved from \`sub/\`.`` |
| yes | yes | ``\`import:\` reads a PDL program from the file it names.`` |
| yes | no | ``\`import: nosuch.pdl\` is resolved from \`sub/\`.`` |

`` `sub/` `` is `_directory_phrase(search_dir)` (`pdl_diagnostics.py:325-329`), which
already renders `Path(".")` as "the current directory" rather than as a bare dot.

### The evidence sentence and the `help:`

Checked in order, first match wins. Every branch reads only `search_dir`, which is
`(state.cwd / written).parent` — so an `import: lib/helper` looks in `lib/`, not in the
program's directory.

| Condition | Evidence | `help:` | F |
| --- | --- | --- | --- |
| written has no `.pdl` and `state.cwd / written` **is an existing file** | ``\`notes.yaml\` exists, but \`import:\` reads only files whose names end in \`.pdl\`.`` | ``if \`notes.yaml\` is a PDL program, rename it to \`notes.pdl\` and write \`import: notes\`.`` | 3 |
| a `.pdl` file in `search_dir` (excluding the importing file itself) is a close match for the written name | `Nothing exists at that path.` | ``did you mean \`import: lib/helper\`?`` | 3 |
| `search_dir` does not exist | ``The directory \`lib/\` does not exist.`` | as the base branch | 1 |
| `search_dir` holds other `.pdl` files | ``The directory contains \`a.pdl\`, \`b.pdl\`.`` (sorted, first 3, then `, and N more`) | ``name one of them, e.g. \`import: a\`.`` | 2 |
| otherwise (this entry) | ``Nothing exists at that path, and the current directory contains no other \`.pdl\` files.`` | ``check the path; it is resolved relative to the current directory.`` | 1 |

Four details that are design, not accident:

- **The near miss is matched on what the user wrote, not on the suffixed form.** Compare
  `Path(written).stem` against `[p.stem for p in candidates]` with
  `difflib.get_close_matches(..., n=1, cutoff=0.7)`, exactly the tuning
  `_near_miss` already uses (`pdl_diagnostics.py:303-322`). Matching `nosuch.pdl` against
  `helper.pdl` would score every candidate up by the four characters PDL itself added.
- **The suggestion is written in the form the user writes, and in the form they chose.**
  `` `import: lib/helper` ``, not a bare filename, and with the `.pdl` suffix only if
  they wrote one. Both forms resolve (`pdl_interpreter.py:3089-3090`), so the suggestion
  is a minimal edit rather than a style correction. The directory part of the written
  path is preserved: the candidate lives in `search_dir`, and `search_dir` is what the
  user's own path pointed at.
- **The importing file is excluded from the candidate list.** It is always a `.pdl` file
  in `search_dir` when `search_dir` is the program's own directory, and importing yourself
  is a cycle, never the intended fix. This is the difference between this entry's honest
  "contains no other `.pdl` files" and E-RUNTIME-001's current, slightly absurd "The
  directory contains `prog.pdl`."
- **The listing is `sorted()` and capped at three**, reusing `_dir_listing` and
  `_pdl_files` (`pdl_diagnostics.py:288-300`). No `set` iteration reaches the text, so
  `PYTHONHASHSEED` cannot move it.

### The one extra `note:`, and the claim it must not make

`state.cwd` is set once, from the **top-level program's** parent
(`pdl_interpreter.py:237`, `pdl.py:425`, `pdl.py:206-207`, `pdl_infer.py:278`), and is
never rebound: neither `process_import` nor `process_include` calls anything like
`state.with_cwd(...)` before recursing (`:3102-3107`, `:3062-3064`), and `cwd` appears in
`src/pdl/` only at `pdl_interpreter_state.py:41` and those four sites plus the three uses
at `:3008`, `:3059`, `:3091`.

So the message must **not** say "relative to the file containing the `import:`". That is
true for a single-file program and false for an import inside an imported file, and a
confidently-stated wrong claim scores below no claim at all (RUBRIC, "Wrong beats
missing"). The base text names the directory and stops there. One `note:` is added, and
only in the case where the distinction bites — `Path(loc.file).parent != state.cwd`:

```
  note: import paths are resolved from `sub/`, the directory of the program
        `pdl` was started with, not from the file that contains this
        `import:`.
```

**Marked unverified**, and it is the one claim in this spec I would not ship without a
run — see the verification commands. If run 4 below *resolves*, this note is wrong and
must be deleted, and the second table above collapses to the first.

## Structured record

Decision 5.6, in the existing `Diagnostic` shape (`pdl_diagnostics.py:84-125`), which
needs no new fields:

```json
{
  "id": "E-RUNTIME-002",
  "severity": "error",
  "origin": "program",
  "file": "prog.pdl",
  "span": {"line": 1, "col": null, "end_line": null, "end_col": null,
           "label": "no file `nosuch.pdl`", "primary": true},
  "spans": [{"line": 1, "col": null, "end_line": null, "end_col": null,
             "label": "no file `nosuch.pdl`", "primary": true}],
  "block_path": ["import"],
  "message": "cannot import `nosuch`: no such file `nosuch.pdl`",
  "notes": [
    {"kind": "rule",
     "text": "`import: nosuch` looks for the file `nosuch.pdl`: PDL appends `.pdl` to an import path that does not already end in it. Nothing exists at that path, and the current directory contains no other `.pdl` files."}
  ],
  "suggestions": [
    {"text": "check the path; it is resolved relative to the current directory.",
     "replacement": null}
  ]
}
```

Two things about this record are reuse traps and are called out on purpose:

- **`file` is the importing program, not the missing file.** E-CLI-001 does the opposite —
  it puts the missing path in `file` and sets `show_location=False`, because there the
  path *is* the subject. Here the diagnostic is *inside* a program, at a line, so `file`
  and the span are the location and the missing path lives in `message`. Getting this
  backwards is precisely how E-RUNTIME-001 ended up with two stacked claim lines.
- `col` is `null` today and the caret label is carried but unrendered. `_excerpt`
  (`pdl_diagnostics.py:243-275`) emits nothing when `source is None`, so the record is
  already correct for the post-item-0 rendering and the golden is not re-cut twice.

## Where the data comes from

Raise site: the `open` at `pdl_interpreter.py:3093`.

| Field | Source | Available today? |
| --- | --- | --- |
| written path | `block.import_`, `pdl_interpreter.py:3088` | yes |
| resolved path | `state.cwd / path` after the suffix append, `:3089-3091` | yes |
| `search_dir` | `(state.cwd / written).parent` | yes, one local |
| `state.cwd` | `pdl_interpreter_state.py:41`, bound at `pdl_interpreter.py:237` / `pdl.py:425` / `pdl.py:206-207` / `pdl_infer.py:278` | yes |
| `file` (importing program) | `loc.file`, set by `parse_str` at `pdl_parser.py:284`; for the top program from `parse_file`, `pdl_parser.py:167`; for a nested import from `str(file)` at `pdl_interpreter.py:3095` | yes |
| span line | `get_line(loc.table, loc.path + ["import"])`, `pdl_location_utils.py:102-107`, over the table built by `get_line_map` (`:73-91`), whose keys are `str(path)` (`:58`). Use `append(loc, "import")` (`:6-7`) | yes — **this is the whole Location gain**, see below |
| span col, excerpt | — | **no.** `PdlLocationType` carries `(file, path, table)` and no source text or columns. Needs item 0 (5.1/5.2: marks loader + source registry) |
| `block_path` | `loc.path + ["import"]`; `loc.path` exists today and is discarded by `get_loc_string` (`pdl_location_utils.py:94-99`) | yes |
| candidates / listing | `sorted(search_dir.glob("*.pdl"))` — `_pdl_files`, `pdl_diagnostics.py:296-300` | yes |
| near miss | `difflib.get_close_matches`, already imported at `pdl_diagnostics.py:33` | yes |
| directory phrase | `_directory_phrase`, `pdl_diagnostics.py:325-329` | yes |
| rendering | `Diagnostic.text` → `render`, `pdl_diagnostics.py:151-174` | yes |
| exit code 1 | `generate` returns 1 at `pdl_interpreter.py:257` | yes |

**The line number, in detail.** `loc.path` is `[]` for a root-level block — that is why
E-RUNTIME-001 renders `prog.pdl:0`, since `get_line` returns 0 for an empty path
(`pdl_location_utils.py:103-104`). Appending the key changes that for free:
`get_line_map("import: nosuch\n")` produces `{"['import']": 1}`, so
`get_line(table, ["import"])` is 1. For `defs: / l: / import: lib` the table holds
`"['defs', 'l', 'import']"` and the line is exact there too, and for a list item
`- import: x` `get_paths` records `['text', '[0]', 'import']` (`:60-69`). When the key is
absent from the table, `get_line` recurses onto the prefix (`:107`) and degrades to
exactly today's number. No new failure mode.

Two honest caveats. This line comes from the regex line table (DROP #2), so it inherits
E-EXPR-006's comment shift; item 0 replaces the table, and this diagnostic gets the fix
for free because it asks `get_line` rather than computing anything itself. And `:0` for
a root-level block is not fixed in general by this spec — only for `import:`.

### Getting the rendered text out, without a doubled header

`generate` prints `get_loc_string(exc.loc) + exc.message` for a `PDLRuntimeError`
(`pdl_interpreter.py:249-257`). If the message is already a rendered diagnostic, that
prefix produces `prog.pdl:1 - prog.pdl:1 - cannot import ...`. Passing `loc=None` does
not avoid it: every re-wrap site substitutes the enclosing block's location
(`exc.loc or loc`, `:1652`, `:1689`, `:2963`, `:3078`, `:3126`), so a nested import gets
the prefix back on the way up.

Use the channel that already survives re-wrapping. `PDLRuntimeError.__init__` collapses
`source_exception` to the innermost one (`pdl_ast.py:1689-1692`), so a carrier exception
holding the record reaches `generate` intact however many times the error is re-wrapped:

```python
class PDLImportError(PDLException):        # new, in pdl_ast.py
    def __init__(self, diagnostic):
        super().__init__(diagnostic.text)
        self.diagnostic = diagnostic
```

`process_import` guards only the two lines that read the file, leaving `prog_str` — and
therefore the cache key at `:3096`/`:3108` — exactly where it is:

```python
try:
    with open(file, "r", encoding="utf-8") as pdl_fp:
        prog_str = pdl_fp.read()
except OSError as exc:
    diagnostic = import_read_diagnostic(...)          # new, in pdl_diagnostics.py
    carrier = PDLImportError(diagnostic)
    raise PDLRuntimeError(
        diagnostic.text,
        loc=append(loc, "import"),
        trace=ErrorBlock(msg=diagnostic.text, program=block.model_copy()),
        source_exception=carrier,
    ) from exc
```

and `generate` gains one branch:

```python
except PDLRuntimeError as exc:
    diagnostic = getattr(exc.source_exception, "diagnostic", None)
    if diagnostic is not None:
        print(diagnostic.text, file=sys.stderr)
    else:
        <the existing two lines, unchanged>
```

That is one edit in `generate`, one new builder, one carrier class, and no edits at the
five re-wrap sites — a missed re-wrap site is the failure mode that would reintroduce the
doubled header, and this design removes the possibility rather than relying on review.
`UnicodeDecodeError` is not an `OSError`; a non-UTF-8 imported file still leaks a
traceback today and is closed in the same commit by routing it through the existing
`undecodable_source_error` (`pdl_parser.py:194-234`) and wrapping the resulting
`.diagnostic` in the same carrier. It has no corpus entry, so it is not scored here, but
leaving it open would violate §5.8 for a case one keystroke away from this one.

## Verification

I have no shell. Every golden below must be produced by a run before it is committed;
the two marked **claim** decide design, not just wording.

```sh
# 1. base branch — the golden for this entry
mkdir -p /tmp/e2/a && cd /tmp/e2/a && printf 'import: nosuch\n' > prog.pdl
pdl --stream none prog.pdl

# 2. near-miss branch — expect: did you mean `import: lib/helper`?
#    and the header `prog.pdl:3` with `in defs.a.import`
mkdir -p /tmp/e2/b/lib && cd /tmp/e2/b
printf 'defs:\n  a:\n    import: lib/helpr\ntext: ok\n' > prog.pdl
printf 'text: ""\n' > lib/helper.pdl
pdl --stream none prog.pdl

# 3. claim: `import:` cannot read a non-`.pdl` file, and looks for `notes.yaml.pdl`
mkdir -p /tmp/e2/c && cd /tmp/e2/c
printf 'defs:\n  n:\n    import: notes.yaml\ntext: ok\n' > prog.pdl
printf 'a: 1\n' > notes.yaml
pdl --stream none prog.pdl

# 4. claim: `state.cwd` does not follow the importing file.
#    EXPECT THIS TO FAIL today, looking for `b.pdl` in /tmp/e2/d rather than in lib/.
#    If it succeeds, delete the `note:` in "The one extra note" and its table rows.
mkdir -p /tmp/e2/d/lib && cd /tmp/e2/d
printf 'defs:\n  a:\n    import: lib/a\ntext: ok\n' > prog.pdl
printf 'defs:\n  b:\n    import: b\ntext: ""\n' > lib/a.pdl
printf 'text: ""\n' > lib/b.pdl
pdl --stream none prog.pdl
```

Run 3 also decides the wording of the suffix-trap `help:`. It is phrased conditionally —
"if `notes.yaml` is a PDL program, rename it" — because a rename followed blindly turns a
missing-file error into a schema error when the file is data rather than a program. That
is the `E-RUNTIME-007` / `E-PARSE-001` lesson applied ahead of time: the branch may not
promise an outcome it cannot check. If run 3 shows the suffix trap is rarer than it
looks, dropping that row costs nothing.

## Rejected alternatives

**Route `process_import` through `parse_file`.** It is the two-line change and it would
have inherited the whole boundary fix. It cannot be done as-is: `parse_file` returns
`(Program, PdlLocationType)` and discards the source text, while `state.imported` is keyed
on `prog_str` (`pdl_interpreter.py:3096`, `:3108`). Keeping the cache would need either a
second read of the same file — two syscalls and a TOCTOU window between them, for a
cache — or re-keying `state.imported` on the resolved path, which is a semantic change:
today two different paths with identical contents share one cache entry and are executed
once, and re-keying would execute the second one. Neither is worth it when the actual
reuse target is one level down: `parse_file` is a *user* of `pdl_diagnostics`, and so is
`process_import`.

**Reuse `source_read_diagnostic` unchanged** (`pdl_diagnostics.py:424-437`). This is the
genuinely one-line fix — call it in the new `except`, wrap, done — and it kills the
traceback. It also produces, verbatim, ``cannot read `nosuch.pdl`: no such file`` /
``` `pdl` takes the path of a PDL program file. ``` / ``help: check the path, or run
`pdl --help` ... ``. All three lines are wrong here: it names a file the user never typed
with no explanation of where it came from, it states the rule for the *command-line
argument* about a construct written *inside a program*, and it offers a `--help` page that
cannot fix an `import:`. That is exactly the defect E-RUNTIME-001's case note records
today, arriving a second time by the same route. The correct reuse boundary is the record
and the renderer, plus the four small helpers (`_pdl_files`, `_dir_listing`,
`_near_miss`'s technique, `_directory_phrase`) — not the CLI's sentences.

## Risk

**No AST change. No public API change to any existing signature. No new dependency**
(`difflib`, `pathlib` already imported in `pdl_diagnostics.py`).

- **SDK behaviour change, deliberate and worth naming.** `exec_file` on a program with a
  bad `import:` today raises a bare `FileNotFoundError`; afterwards it raises
  `PDLRuntimeError`, so `except FileNotFoundError` around `exec_file` stops matching for
  this case. The errno-shim trick that made the boundary work additive
  (INVENTORY §7.1) cannot be applied: what the caller receives is produced by the
  interpreter's runtime-error path, and `PDLRuntimeError` cannot inherit
  `FileNotFoundError` for its other ~40 uses. The change aligns `import:` with `include:`,
  which has always raised `PDLRuntimeError` for this. One line in the release note.
- **`ErrorBlock.msg` in the trace becomes multi-line rendered text** rather than a single
  sentence. The trace *format* does not change — `msg` is already a string — but its
  content does, and `pdl-live-react` renders it. Low blast radius: the viewer currently
  drops error blocks entirely (`view/timeline/model.ts:157`, E-GUI-002). `process_include`
  already puts multi-line text there since the boundary work.
- **`generate`'s new branch is reachable by any future carrier.** It fires only when
  `source_exception` has a `.diagnostic`, which nothing else sets yet. Anything that sets
  one later must be a fully rendered diagnostic, not a fragment.
- **Message-asserting tests: none break.** Nothing in `tests/` asserts on
  `"Attempting to import"` (only `pdl_interpreter.py:3116` and the two docs files
  mention it), and `tests/test_include.py` exercises only imports that resolve.
  `tests/test_parse_errors.py` pins the parse-boundary shims and does not touch
  `process_import`.
- **Golden churn: one file.** `tests/errors/corpus/E-RUNTIME-002/expected.txt`, plus the
  deletion of `hygiene_traceback_expected` from its `case.json` — mandatory, or
  `test_no_traceback` XPASSes and fails the suite (RUBRIC, "Two hygiene sub-flags").
- **Behaviour question surfaced, not answered:** that a nested `import:` resolves against
  the top-level program's directory rather than its own file is arguably a bug, and
  `include:` shares it. This spec documents it in a `note:` and changes nothing. If it is
  ever fixed, that `note:` and one table row are deleted; nothing else in the design
  depends on it.

## Follow-up: `include:` (E-RUNTIME-001), separately

`process_include` (`pdl_interpreter.py:3053-3074`) now renders, but with wording
E-BOUNDARY inherited rather than designed:
``prog.pdl:0 - Attempting to include invalid yaml: does_not_exist.pdl`` followed by the
E-CLI-001 text — "yaml" about a file that does not exist, `:0`, two stacked claim lines,
and the `pdl --help` suggestion again. The design here transfers almost whole: same
carrier, same `generate` branch, same `append(loc, "include")` for the line, same
near-miss and listing branches, and the same "one diagnostic, one location prefix" rule
replacing the `f"Attempting to include..."` prefix.

**One difference must not be copied across.** `include:` does **not** append `.pdl`
(`:3059` is a bare `state.cwd / block.include`), so the suffix sentence and the
suffix-trap branch must not fire for it; conversely, an `include: helper` that fails while
`helper.pdl` exists is an *excellent* near-miss suggestion for `include:` and an
impossible one for `import:`. That asymmetry is why the two get one shared builder with an
explicit keyword parameter and two goldens, not one message — and why E-RUNTIME-001 keeps
its own spec, its own corpus entry and its own rubric row.

---

**Expected rubric delta:** 1/15 → **12/15** (L0→2, W0→3, Y1→3, F0→1, H0→3), reaching
13/15 after item 0 delivers columns and the source registry, and 14/15 on any run where
the near-miss branch fires. One of the last three traceback leaks in the corpus closes
here; `hygiene_traceback_expected` is deleted in the same commit.

**One sentence a user takes away:** "It told me that PDL turned my `nosuch` into
`nosuch.pdl`, which directory it looked in, and which line of my program to fix."
