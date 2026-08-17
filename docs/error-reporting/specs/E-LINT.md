# Spec: the `pdl-lint` group — E-LINT-001, E-LINT-003, E-LINT-004

> Citation anchor: written against HEAD `45b5bb0` on `claude/pdl-error-reporting-hgiosu`,
> tree clean. Every "current" transcript below is quoted verbatim from that commit's
> goldens or from a command run against that tree. E-LINT-002 is already fixed (14/15)
> and is the model this group should converge on; it must not regress.

Phase-3 item 5. Three entries, two of them S0. They live in the same file but do
**not** share a root cause, and one of them turns out not to be a linter defect at
all — see E-LINT-001 below, which changes what should be built.

---

## E-LINT-004 — the false green (2/15, S0) — worst of the group

### Current

```
$ pdl-lint <workdir>/outside.pdl
 - ℹ️  SKIPPING <workdir>/outside.pdl (in ignore list)
----------------------------------------------------------------------------------------------------
🎉  All files linted successfully 🎉
exit 0
```

A file **named explicitly on the command line** is skipped, and the run reports
success. CI that names a file and gets exit 0 will believe it was checked.

### Root cause — worse than the entry records

`LinterConfig.should_ignore` (`pdl_linter.py:244`) collapses **four distinct
reasons** into one boolean:

| # | branch | debug text |
|---|--------|-----------|
| 1 | not under `project_root` | `Not within the project root` |
| 2 | `path.suffix != ".pdl"` | `Not a *.pdl file.` |
| 3 | `path in self.ignore` | `In the ignore list.` |
| 4 | under `directories_to_ignore` | `In a directory marked to be ignored.` |

`_lint_pdl_file` (`pdl_linter.py:366`) then does:

```python
if config.should_ignore(file_path):
    logger.info(" - ℹ️  SKIPPING %s (in ignore list)", file_path)
    return True          # <- "linted successfully"
```

So there are **two** defects, not one:

- **The false green.** `return True` means success. The file is uncounted.
- **The reason is a lie.** `outside.pdl` is skipped for reason **1**, and the
  message names reason **3**. Nothing is in any ignore list. A user would open
  their config, find no such entry, and be stuck.

Reason 2 is a second, independent trigger for the same false green — verified
against this tree:

```
$ pdl-lint notes.txt          # a non-.pdl file, explicitly named
 - ℹ️  SKIPPING notes.txt (in ignore list)
🎉  All files linted successfully 🎉
exit 0
```

The only accurate word in that line is `SKIPPING`.

### The decision

An ignore list is right for a **directory walk** and wrong for an **explicit
argument** — the user named that file. This is settled convention in other
linters: ruff documents that "files passed directly on the command line are
checked even if they would normally be excluded", and eslint's `--no-ignore`
exists for the same reason.

**Recommendation (A): an explicit path is always linted; the ignore rules apply
only to directory traversal.** The skip message disappears for explicit
arguments, which dissolves the wrong-reason problem along with the false green.
Reasons 1–4 are all about *walk scope*, not about a file being unlintable — a
`.pdl` outside the project root parses exactly as well as one inside it.

Fallback **(B)**, if the owner wants a smaller change: still skip, but report the
**true** reason and count it as a failure so the exit code is honest. This keeps
`pdl-lint *.py` cheap but leaves the user to work out why their file was ignored.

I recommend A and note the cost plainly below.

### Cost — this changes an exit code

An invocation that returns 0 today can return 1 after the fix, whenever the named
file is both skipped and actually broken. **That can turn a passing CI red.** It
is a §5.8-adjacent change to the *tool's* exit code, not to any PDL program's
semantics, and the passing was false — but it is a real cost and belongs in the
release note.

Blast radius, measured on this tree: **nothing in `.github/`, `pyproject.toml` or
`.pre-commit-config.yaml` invokes `pdl-lint` on any path.** The repo's own CI is
unaffected; the exposure is external users only.

### Corpus consequence — the reproducer must be repointed

`outside.pdl` currently contains a **valid** program. Under fix A it would simply
be linted and pass, exit 0 — the transcript would look almost unchanged and would
pin nothing. **Repoint the reproducer at a file that is both skipped today and
broken**, so the fix is visible as a caught error rather than an absence.

### Scoring question for the owner

E-LINT-004 is **not** currently flagged `hygiene_silent_failure`, though "reports
success for a file it never checked" reads like the definition of one. The other
three flagged entries are all interpreter-side. Either the flag is under-applied
here or it is deliberately scoped to program evaluation rather than tooling —
`RUBRIC.md` does not say which. Surfacing rather than guessing, per standing
instruction.

---

## E-LINT-003 — raw traceback (3/15, S0)

### Current

```
 - ❌  prog.pdl
Traceback (most recent call last):
  File "<REPO>/src/pdl/pdl_linter.py", line <LINE>, in _lint_pdl_file
    _lint_python_code_blocks(prog.root)
  File "<REPO>/src/pdl/pdl_linter.py", line <LINE>, in _lint_python_code_blocks
    ast.parse(code)
  File "<PYLIB>/ast.py", line <LINE>, in parse
    return compile(source, filename, mode, flags,
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<unknown>", line 1
    x = = 1
    ^
SyntaxError: invalid syntax
```

### Root cause

`_lint_python_code_blocks` (`pdl_linter.py:384`) calls `ast.parse(code)` bare. The
`SyntaxError` escapes to `_lint_pdl_file`'s `except Exception: logger.exception(...)`,
which prints the traceback. The `.pdl` file is named only on the `❌` line above;
the error itself points at `<unknown>`.

### The data is already there

The discarded `SyntaxError` carries full position information — verified on this
tree:

```
'x = = 1'          msg='invalid syntax'   lineno=1 offset=5 text='x = = 1\n'
'def f(:'          msg='invalid syntax'   lineno=1 offset=7 text='def f(:\n'
'if True'          msg="expected ':'"     lineno=1 offset=8 text='if True\n'
```

`msg`, `lineno`, `offset` (1-based column) and `text` (the source line) are
everything a rendered diagnostic needs.

### Target

Those coordinates are **within the code block**, not the `.pdl` file — the exact
problem `E-CODE-001` already solved. Reuse its `code:N` gutter and its closing
note verbatim rather than inventing a second convention:

```
 - ❌  prog.pdl
     PDLLintError: prog.pdl:3:11 - `code:` block is not valid Python: invalid syntax

code:1 | x = = 1
       |     ^

  `pdl-lint` parses every `code:` block with Python's own parser. The block must
  be syntactically valid Python even though the linter never runs it.

  note: `code:N` line numbers are within the block's code, not the PDL file.
```

**To verify at implementation time:** the `.pdl` line:col on the first line
requires the `CodeBlock`'s `pdl__location`, which `_lint_python_code_blocks` has
in hand but currently ignores. If that location proves unavailable or coarse,
drop to naming the file alone — do not invent a line number. Confirm before
writing the golden.

Catch `SyntaxError` specifically. Leave the `except Exception` fallback in place
for genuinely unexpected failures; this fix must not become a blanket swallow.

---

## E-LINT-001 — reframed: not a linter bug (7/15, S1)

**The brief asked for an indentation fix. It should not be built.** E-LINT-002 —
already accepted at 14/15 — renders its excerpt flush left in exactly the same
way:

```
 - ❌  prog.pdl
     PDLYamlError: prog.pdl:3:6 - not valid YAML: expected the end of the list, ...

2 |   - "hello
  |     ^ this double quote opens a string that is never closed on this line
```

So flush-left continuation is the group's **established convention**, not a
defect. Re-indenting E-LINT-001 would leave the two inconsistent and would
rewrite E-LINT-002's golden to no benefit. E-LINT-001's own `notes` field already
says the misalignment "belongs to the E-LINT items, not to this one."

### What is actually missing

The linter prints `e.text` — the rendered diagnostic — so its output is whatever
the interpreter produced. Compare, on this tree:

```
E-SCHEMA-001 (interpreter)     prog.pdl:5:5 - Field not allowed: parameterss
                                 in text[1].parameterss

E-LINT-001   (linter)     PDLParseError: prog.pdl:5:5 - Field not allowed: parameterss
                            in text[1].parameterss
```

Identical but for the class-name prefix. **E-LINT-001's `fix: 0` and `why: 1` are
E-SCHEMA-001's gaps, displayed through the linter.** There is no suggestion
because the interpreter has none; `parameterss` → `parameters` is precisely the
"did you mean" work still open in §6's tail.

Two consequences:

1. **Do not fix E-LINT-001 directly.** Land the schema suggestion once, on
   `PDLParseError`'s rendering, and both entries move together. A linter-local
   patch would duplicate logic that belongs one layer down.
2. The only genuinely linter-local defect is the **`PDLParseError: ` prefix** — an
   internal class name in front of an already-rendered message, which the
   interpreter path does not print. Dropping it is a two-line change and is worth
   doing with this group. `RUBRIC.md` treats leaked internals as a hygiene cost.

One stale detail: the `notes` say "no column", but the golden shows `5:5`. The
`:col` work landed after that sentence was written. Correct it when rescoring.

---

## Predicted rubric movement

Deliberately conservative; this project's predictions have run optimistic, and one
recent item moved 4→5 where its spec said 14.

| entry | now | predicted | depends on |
|---|---|---|---|
| E-LINT-003 | 3/15 | **11/15** | 12 only if the `.pdl` line:col proves available |
| E-LINT-004 | 2/15 | **10/15** | requires the repointed reproducer; higher if the skip-reason work lands too |
| E-LINT-001 | 7/15 | **8/15** | prefix removal alone. **11/15** only once the schema "did you mean" lands — not creditable to this item |

Group total 12/45 → **29/45** on this work alone. The remaining headroom is
E-SCHEMA-002's suggestion work, not the linter's.

## Notes for the implementer

- **The stream split in these goldens is a timing artifact, not a log-level rule —
  do not reason from "errors go to stderr".** `_setup_logging` installs exactly
  one `StreamHandler(sys.stdout)` (`pdl_linter.py:516`), so once it has run,
  **everything** goes to stdout, `logger.error` included. That is why E-LINT-001's
  `❌` and `😮  Linting failed` lines both appear under `--- stdout ---`.

  The one line that reaches stderr — `⚠️  No PDL linter configuration file
  found…` — does so because `LinterConfig.load()` is called on the *first* line of
  `run_linter()`, before `_setup_logging`. With no handler installed yet, Python's
  `logging.lastResort` fallback emits it to stderr.

  So any new diagnostic added inside `_lint_pdl_file` lands on **stdout**. Write
  the golden accordingly, and if a new message is emitted during config loading it
  will land on stderr instead — check which side of `_setup_logging` your code
  runs on.
- `E-LINT-002` must come out byte-identical. Run `regen.py` and confirm `ok`.
- No AST or public-API change is implied, so no schema regeneration.
