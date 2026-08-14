# E-SCHEMA-006 / E-SCHEMA-007 — block-union errors via the existing discriminator

> **Citation anchor: `f0d91a1`** (branch `claude/pdl-error-reporting-hgiosu`). Every
> `file:line` below resolves at that commit; read them with
> `git show f0d91a1:<path>`. Where a line number and a symbol name disagree, the
> symbol name is what was meant (INVENTORY 7.5).

This is §6 item 10, implementing decision §5.3. Two entries, one root area: the
`is_any_of` arm of `analyze_errors` (`src/pdl/pdl_schema_error_analyzer.py:182-238`).
They fail in opposite directions — E-SCHEMA-006 says nothing, E-SCHEMA-007 says
everything — and both are fixed by asking the discriminator PDL already has which
branch the user meant.

**I have no shell in this session.** Every `help:` below is therefore marked
verified-by-reading or **UNVERIFIED**, and §"Verification harness" gives the exact
script that must be run before any of this is implemented. Six wrong suggestions
have been caught on this project by running them; do not skip it.

---

## Today

### E-SCHEMA-006 — `tests/errors/corpus/E-SCHEMA-006/`

`prog.pdl`:

```yaml
text: hi
parser: xml
```

stderr, verbatim from the golden:

```
The file PDL prog.pdl does not respect the schema.
```

Rubric: L0 W0 Y0 F0 H2 = **2/15**.

### E-SCHEMA-007 — `tests/errors/corpus/E-SCHEMA-007/`

`prog.pdl`:

```yaml
text:
  - 12
  - foo: bar
```

stderr, verbatim from the golden (one line, ~700 characters, wrapped here only for
this document — the golden has no line break in it):

```
prog.pdl:3 - {'foo': 'bar'} should be of type: {'oneOf': [{'$ref': '#/$defs/ExpressionBlock'}, {'$ref': '#/$defs/FunctionBlock'}, {'$ref': '#/$defs/CallBlock'}, {'$ref': '#/$defs/ModelBlockType'}, {'$ref': '#/$defs/CodeBlockType'}, {'$ref': '#/$defs/GetBlock'}, {'$ref': '#/$defs/DataBlock'}, {'$ref': '#/$defs/MessageBlock'}, {'$ref': '#/$defs/ReadBlock'}, {'$ref': '#/$defs/FactorBlock'}, {'$ref': '#/$defs/AggregatorBlock'}, {'$ref': '#/$defs/ErrorBlock'}, {'$ref': '#/$defs/EmptyBlock'}, {'$ref': '#/$defs/SequenceBlock'}, {'$ref': '#/$defs/TextBlock'}, {'$ref': '#/$defs/LastOfBlock'}, {'$ref': '#/$defs/ArrayBlock'}, {'$ref': '#/$defs/ObjectBlock'}, {'$ref': '#/$defs/IfBlock'}, {'$ref': '#/$defs/MatchBlock'}, {'$ref': '#/$defs/RepeatBlock'}, {'$ref': '#/$defs/MapBlock'}, {'$ref': '#/$defs/IncludeBlock'}, {'$ref': '#/$defs/ImportBlock'}]}
  in text[1]
```

Rubric: L2 W0 Y2 F0 H1 = **5/15**.

---

## Why each one fails, traced

Both traces were read off the source at `f0d91a1`; neither is inferred from the
message text.

### E-SCHEMA-006: the analyzer accepts an invalid enum value, then the fallback fires

`{"text": "hi", "parser": "xml"}` reaches `analyze_errors` at the `BlockType`
union, picks `TextBlock`, and descends into the `parser` property
(`pdl-schema.json:43-47` → `OptionalParserType` → `ParserType`,
`pdl-schema.json:4968-4986`):

```json
"ParserType": {"anyOf": [{"enum": ["json","jsonl","yaml","csv"], "type": "string"},
                         {"$ref": "#/$defs/PdlParser"},
                         {"$ref": "#/$defs/RegexParser"}]}
```

`"xml"` is not a dict or a list, so control reaches
`pdl_schema_error_analyzer.py:187-209`:

```python
if "type" in item and item["type"] == the_type:
    the_type_exists = True
if "enum" in item and data in item["enum"]:
    the_type_exists = True
```

The first alternative carries **both** `type: string` and an `enum`. The `type`
test fires, sets the flag unconditionally, and `break`s — so the `enum` test below
it can never reject anything. `analyze_errors` returns `[]`, and
`pdl_parser.py:334-341` prints the fallback. **That is the whole of E-SCHEMA-006:
not a missing analyzer case, a check that is dead because a weaker check above it
already said yes.**

Two adjacent defects in the same eight lines, both to be fixed while here:

* `:201` — `the_type_exists = len(errs) == 0` **assigns** rather than accumulates, so
  a later `$ref` alternative can reset an earlier match to `False`. Masked today
  only by the `break` at `:203`.
* The `is_any_of` scalar arm has no `const` handling at all. Not needed after the
  discriminator work below (see "`lang: ruby`"), but worth knowing it is absent.

### E-SCHEMA-007: `match()` scores a union by counting field names

`pdl_schema_error_analyzer.py:86-89`:

```python
def match(ref_type, data):
    all_fields = ref_type.get("properties", {}).keys()
    intersection = list(set(data.keys()) & set(all_fields))
    return len(intersection)
```

`{"foo": "bar"}` intersects no branch's properties, so `highest_match` stays `0`,
`match_ref` stays `{}`, and `:230-235` prints `str(schema)` — the 24 `$ref`s.

The same heuristic is what makes **E-SCHEMA-010** state two falsehoods: for
`{description, texts, foo, bar}` every block branch scores exactly 1 (on
`description`), the first one wins, and the user is told
`Missing required field: function` / `... return` about a program that contains no
function. §5.3's discriminator replaces the counting, which is why 010 moves too;
see "Blast radius".

---

## Target

### E-SCHEMA-006

```
prog.pdl:2 - `xml` is not a valid value for `parser:`
  in parser

2 | parser: xml
  | ^

  `parser:` accepts `json`, `jsonl`, `yaml` or `csv`, or a mapping with a
  `regex:` or `pdl:` key.

  help: remove `parser:` to leave the output as text, or use one of `json`,
        `jsonl`, `yaml` or `csv`.
```

Rubric: L2 W3 Y3 F2 H3 = **13/15**.

*Location 2, not 3:* the header is built by `located_message`
(`pdl_location_utils.py:434`), and `get_loc_string` deliberately does not render
`:col` (7.6, 7.9). The excerpt, the caret and the block path are all there; the
missing point is one colon and a number in the header. **These two entries are the
strongest argument yet for taking 7.9's deferred `:col` decision** — 7.9 declined it
because the column came off the same coarse mark as the line and bought nothing
without a caret. Here there is a caret, and the caret is exactly the 2 → 3 package
the column belongs to. Not taken in this spec: it rewrites the header of all 30
prefix-rendering goldens in one step, which is the renderer owner's call.

*Fix 2, not 3:* `xml` has no correct answer — the honest help is "remove it, or pick
one of four". The near-miss branch (`parser: jsn` → ``help: did you mean
`parser: json`?``) is a 3; this reproducer does not reach it.

*Why the caret carries no label:* the offending token is the value `xml`, and the
mark PDL holds for this entry starts at the **key** (`_walk`,
`pdl_location_utils.py:182-195`, records a mapping entry at its key). A caret on
`parser` labelled "not a valid value" labels the wrong token. The whole entry is
eleven characters and fully visible on the excerpt line, so the caret marks it and
says nothing. Getting the caret onto `xml` needs value marks, which `_walk` does not
record — see "Rejected alternatives".

### E-SCHEMA-007

```
prog.pdl:3 - this is not a PDL block: nothing here says what it does
  in text[1]

3 |   - foo: bar
  |     ^ `foo` does not name a block kind

  Every block is named by the one field that says what it does. This mapping
  has none of them: `model`, `code`, `text`, `data`, `call`, `if`, `repeat`,
  `read`, `get`, `function`, `include`, `import`, `array`, `object`, `lastOf`,
  `sequence`, `match`, `map`, `content`, `args`, `factor`, `aggregator`,
  `platform` or `processor`.

  help: if this item is meant to produce the value `{foo: bar}`, write it as
        - data: {foo: bar}
```

Rubric: L2 W3 Y3 F2 H3 = **13/15**.

The rule paragraph is 24 names — the 25 entries of `_BLOCK_KIND_OF_FIELD`
(`pdl_ast.py:1527-1553`) minus `program`, which selects `ErrorBlock`, a block the
interpreter builds and nobody writes by hand. It is ~330 characters of the user's
own vocabulary across five wrapped lines, against 700 characters of `$ref`s on one
line. That is the trade §5.3 asks for, and it is deliberately **not** truncated:
"expected one of `model`, `code`, `text`, …" with an ellipsis leaves the reader with
no way to discover the rest, and the codebase has no docs-URL precedent to point
them at (grep: the only `ibm.github.io` reference in `src/` is a `<script>` tag in
`pdl_notebook_ext.py:162`). A `see the documentation` line scores Fix **1** by
`RUBRIC.md`; the list scores 2.

The wrapping shown is `_wrap(..., width=78, initial="  ", subsequent="  ")`
(`pdl_diagnostics.py:220-230`) computed by hand. **Confirm it against the
implementation's real output before pinning the golden** — the words, not the line
breaks, are the deliverable.

---

## Structured record

Decision 5.6: the record is the contract, the text is a rendering of it. Both
diagnostics are built as `Diagnostic` (`pdl_diagnostics.py:89-146`) with
`file=""`, `show_location=False`, `block_path=None`, and are then wrapped by
`located_message(loc, diag.text)` — the pattern `_parser_diagnostic` already uses
and documents (`pdl_diagnostics.py:1416-1428`). That is what keeps the header on the
`file:line - ` convention `get_loc_string` owns, keeps `line 0 - ` working for a
program handed to `exec_dict` with `empty_block_location`, and keeps the `  in <path>`
line in the one place that renders it.

### E-SCHEMA-006

```
id:          E-SCHEMA-005
severity:    error
origin:      program
file:        prog.pdl          (from `loc.file`, applied by located_message)
span:        line 2, col 1, primary, no label
block path:  ["parser"]        (from `loc.path`, applied by located_message)
message:     `xml` is not a valid value for `parser:`
notes:       rule: "`parser:` accepts `json`, `jsonl`, `yaml` or `csv`, or a
                    mapping with a `regex:` or `pdl:` key."
suggestions: text: "remove `parser:` to leave the output as text, or use one of
                    `json`, `jsonl`, `yaml` or `csv`."
             replacement: ""
gutter:      ""                (a file excerpt, so a bare `N | ` row is correct)
source:      the text of prog.pdl, from source_text(loc.file)
```

> **`id` is `E-SCHEMA-005`, not `E-SCHEMA-006`, and that is a corpus question for
> the owner.** INVENTORY §2 records E-SCHEMA-005 as "value not in enum — in
> practice the enum branch is unreachable for block fields". This change is exactly
> what makes it reachable, so the taxonomy row stops being unclassifiable and the
> `parser: xml` reproducer stops exercising E-SCHEMA-006's actual defect (the
> empty-return fallback). `Diagnostic.code` is carried and never rendered
> (`pdl_diagnostics.py:93-95`), so no user-visible text depends on this. What does
> depend on it: whether `tests/errors/corpus/E-SCHEMA-006/` is re-keyed, and how the
> fallback gets a reproducer of its own once nothing known reaches it. I am
> surfacing this rather than deciding it.

### E-SCHEMA-007

```
id:          E-SCHEMA-007
severity:    error
origin:      program
file:        prog.pdl          (applied by located_message)
span:        line 3, col 5, primary, label "`foo` does not name a block kind"
block path:  ["text", "[1]"]   (applied by located_message)
message:     this is not a PDL block: nothing here says what it does
notes:       rule: "Every block is named by the one field that says what it
                    does. This mapping has none of them: `model`, `code`, ...
                    `platform` or `processor`."
             note: (emitted only when 2+ keys are unrecognised; absent here)
suggestions: text: "if this item is meant to produce the value `{foo: bar}`,
                    write it as"
             replacement: "- data: {foo: bar}"
gutter:      ""
source:      the text of prog.pdl
```

Spans are placed on the **unrecognised keys**, at most the first two in document
order (`_excerpt` renders `spans[:2]`, `pdl_diagnostics.py:350`), and the remainder
are named in a `note:`. The location handed to `located_message` keeps the
**block's** path — so the `  in` line says `text[1]`, the block the claim is about —
with the **first bad key's** line, so the header and the caret agree. For this
reproducer the two coincide (the item's mark and its first key's mark are both
3:5); for E-SCHEMA-010 they do not, and using the block's line alone would print
`prog.pdl:1` above a caret on line 2.

### The honest fallback (`parse_dict`, `pdl_parser.py:334-341`)

E-SCHEMA-006's *named* defect is this branch, and it survives the change — it is
just no longer reachable by anything known. It must stop lying by omission:

```
prog.pdl - the program does not match the PDL schema, and PDL cannot say where

  PDL's validator rejected this program, but the analyzer that turns a
  rejection into a located message did not recognise the failure, so nothing
  more precise can be said about it.

  note: reaching this message is a gap in PDL's error reporting rather than
        extra information about your program. Reporting the program that
        produced it is the only way that gap gets closed.

  help: remove blocks until the message changes, to find the one at fault.
```

```
id:          E-SCHEMA-006
severity:    error
file:        prog.pdl
show_location: True, spans: []   ->  header renders `prog.pdl - <message>`
span:        none                     (claiming line 1 here would be a
                                       confidently-wrong location, which
                                       RUBRIC.md ranks below no location)
block path:  none
```

Rubric for this branch: L0 W2 Y1 F1 H3 = 7/15 — and **that is the ceiling for an
honest rendering of "I do not know"**. It is not scored as an entry movement,
because after the change nothing in the corpus reaches it. The unnamed-source
branch (`is_unnamed(loc.file)`, `pdl_parser.py:337`) keeps its own wording with the
prefix dropped, exactly as today.

Location stays **0**, not 1: `RUBRIC.md` scores 1 for "file *and* line", and this
branch has no line it can honestly claim. Naming the file is worth having and is
not worth a point.

---

## The design, in the analyzer

### One dispatch table, three discriminators

`pdl_schema_error_analyzer.py:1` already imports from `.pdl_ast`, and `pdl_ast`
imports only `pdl_context`, `pdl_diagnostics` and `pdl_lazy` — **no cycle**,
re-confirmed by reading the import blocks at `f0d91a1`.

In the `isinstance(data, dict)` arm (`:221-238`), before the `match()` loop:

| union def | discriminator | tag → `$def` |
| --- | --- | --- |
| `defs["BlockType"]` (`pdl-schema.json:447`) | `_block_tag` (`pdl_ast.py:1475`) | 24 entries, `BlockType`'s `oneOf` order matches the `Tag(...)` order at `pdl_ast.py:1588-1611` exactly |
| `defs["ModelBlockType"]` (`:4321`) | `_model_block_tag` (`pdl_ast.py:1498`) | `litellm`→`LitellmModelBlock`, `granite-io`→`GraniteioModelBlock`, `openai`→`OpenaiModelBlock` |
| `defs["CodeBlockType"]` (`:677`) | `_code_block_tag` (`pdl_ast.py:1510`) | `python`/`ipython`/`jinja`/`pdl`/`command`/`args` → the matching `$def` |

Recognised by **identity** on the `defs` dict (`schema is defs.get("BlockType")`),
which holds because `analyze_errors` follows `$ref`s by handing `defs[name]`
straight on (`:133-136`, `:73-83`) and never copies. Any other union — including
`ContributeElement`, which E-SCHEMA-008 rides on — falls through to today's code
untouched.

The tag → `$def` tables must be pinned by a test asserting every entry names a key
of `$defs` and that the union's `oneOf` has no member the table omits. They are the
one hand-written thing here and the one thing that can silently rot.

### Telling "no branch matched" from a legitimate `EmptyBlock`

This is the trap, and it is real: `_block_tag` returns `BlockKind.EMPTY` for
`{"foo": "bar"}` **and** for `{"description": "d"}`, and the second is valid PDL. So
`tag == EMPTY` is not a "nothing matched" signal and must never be used as one.

The signal is the **keys**, not the tag:

```
recognised   = EmptyBlock.properties          # pdl-schema.json:1026-1140, 18 keys
unrecognised = [k for k in data if k not in recognised]   # document order, never a set
```

`_BLOCK_KIND_OF_FIELD` holds exactly the fields that select a *non*-empty kind, and
`EmptyBlock.properties` holds exactly the fields every block shares. A key in
neither names nothing at all. Hence:

| `_block_tag(data)` | unrecognised keys | what is emitted |
| --- | --- | --- |
| a known non-empty kind | — | recurse into that one `$def`; unknown keys keep today's `Field not allowed` (E-SCHEMA-001/002) |
| `EMPTY` | none | recurse into `EmptyBlock` — **today's behaviour, unchanged**. A `description:`-only block stays valid and a fault inside it is reported inside it |
| `EMPTY` | one or more | the E-SCHEMA-007 diagnostic, then recurse into `EmptyBlock` over the *recognised* subset so nested faults are not swallowed |
| a string that is no known tag (`kind: txt`, `lang: ruby`, `platform: foo`) | — | "not a kind / language / platform" diagnostic, listing the tags of that union |

The last row is not decoration. `_block_tag` returns `v.get("kind")` **verbatim**
(`pdl_ast.py:1489-1491`) — an arbitrary user string. A table lookup without a miss
branch is a `KeyError`, i.e. a raw traceback, i.e. a breach of §5.8's invariant.

### `lang: ruby` — INVENTORY's other named trigger for E-SCHEMA-006, and it has no golden

INVENTORY §2 lists E-SCHEMA-006's trigger as "`parser: xml`, `lang: ruby`". Traced:
`_block_tag({"lang": "ruby", "code": "x"})` → `CODE`; today `match()` picks
`PythonCodeBlock`, whose `lang` is `{"const": "python", "type": "string"}`;
`is_base_type` (`:6-25`) tests `type` and `enum` and **not** `const`, so `"ruby"`
passes as a string and the analyzer again returns `[]`. A second, independent hole
into the same fallback.

The discriminator route closes it without any `const` support: `_code_block_tag`
returns `"ruby"`, no `CodeBlockType` branch has that tag, and the miss branch says
so:

```
prog.pdl:1 - `ruby` is not a language PDL can run

1 | lang: ruby
  | ^

  The `lang:` of a `code:` block chooses the interpreter. PDL runs `python`,
  `ipython`, `jinja`, `pdl` and `command`.
```

**This shape has no corpus entry and I am not adding one.** It belongs to
E-SCHEMA-006's row and it also shadows E-RUNTIME-008 (`Unsupported language: <lang>`,
which the schema check never lets run). Recommend a reproducer be added with the
implementation; without one, half of the entry's stated trigger is unpinned.

---

## Where the data comes from

Every field, at the raise site, at `f0d91a1`. Nothing below needs data the
interpreter does not already have.

| Field | Source | Available at the raise site? |
| --- | --- | --- |
| the intended branch | `_block_tag` / `_model_block_tag` / `_code_block_tag`, `pdl_ast.py:1475-1517` | Yes — plain functions over the raw dict, which is what `analyze_errors` walks |
| the 25 selector fields | `_BLOCK_KIND_OF_FIELD`, `pdl_ast.py:1527-1553` | Yes |
| the block kinds | `BlockKind`, `pdl_ast.py:76-99` | Yes |
| the common fields (`recognised`) | `defs["EmptyBlock"]["properties"]`, `pdl-schema.json:1026-1140` | Yes — `defs` is a parameter of `analyze_errors` |
| accepted parser values | `defs["ParserType"]["anyOf"][0]["enum"]`, `pdl-schema.json:4968-4986` | Yes. Read from the schema, never hardcoded |
| `regex:` / `pdl:` | the `required` lists of `defs["RegexParser"]` (`:5617-5619`) and `defs["PdlParser"]` (`:5209-5211`) | Yes — the required key of an object alternative is how a user spells it |
| the offending field name | `loc.path[-1]` (`"parser"`) | Yes. Falls back to "here" when the path is empty or ends in `[n]` |
| file | `loc.file`, applied by `located_message` (`pdl_location_utils.py:434-468`) | Yes |
| line / col of a key | `append(loc, key)` (`pdl_location_utils.py:397-409`) → the `SourceMark` recorded by `_walk` (`:169-201`) | Yes. Real PyYAML marks since Phase-3 item 0 (7.6) |
| block path | `loc.path`, rendered by `join_path` (`pdl_diagnostics.py:210-217`) | Yes |
| **source text for the excerpt** | `source_text(loc.file)` → `SourceRegistry.text_of` (`pdl_location_utils.py:328-346, 377-379`) | **Yes** — `_parse_str_cached` calls `register_source` at `pdl_parser.py:293`, *before* `parse_dict` at `:295`, precisely so that "schema errors resolve against exactly this entry". `None` for a contested key or a source-less program, in which case `_excerpt` renders nothing and the diagnostic degrades to header + rule + help |
| near-miss suggestion | `difflib.get_close_matches(key, pool, n=1, cutoff=0.7)`, the same call and cutoff as `_near_miss` (`pdl_diagnostics.py:408-427`) and `_import_missing` (`:664-665`) | Yes. `difflib` is stdlib; pool = the 24 selector fields + `EmptyBlock`'s 11 user-facing properties, a fixed sorted list, so the answer cannot vary between runs |

Two module-boundary notes:

* `pdl_diagnostics` states in its own docstring (`:32-33`) that it "imports nothing
  from PDL, so it cannot participate in an import cycle". The new builders live
  there and take the name lists **as parameters**; the analyzer supplies them from
  `pdl_ast`. Do not import `pdl_ast` into `pdl_diagnostics` to save an argument.
* `_block_tag` and `_BLOCK_KIND_OF_FIELD` are underscore-private. Importing them
  across modules is a style call I am flagging rather than making: either give them
  public aliases in `pdl_ast` or import them with a comment naming §5.3 as the
  reason.

---

## Verification harness

I could not execute anything. Everything in this box is **UNVERIFIED** and must
pass before implementation. Nine of nine passed the last time this procedure was
followed; the point is that it is followed.

```bash
set -e
cd "$(mktemp -d)"

# 1. The two `help:` lines must produce working programs.
printf 'text:\n  - 12\n  - data: {foo: bar}\n' > fix007.pdl
pdl --stream none fix007.pdl; echo "007 help exit=$?"     # expect 0

printf 'text: hi\n' > fix006a.pdl
pdl --stream none fix006a.pdl; echo "006 help/remove exit=$?"   # expect 0

printf 'text: {"a": 1}\nparser: json\n' > fix006b.pdl
pdl --stream none fix006b.pdl; echo "006 help/json exit=$?"     # expect 0

# 2. The near-miss branch. Following it must not land the user on a *worse*
#    error. `texts` -> `text` leaves `foo`/`bar` still unknown, which is a
#    DIFFERENT error, not a silently wrong value -- confirm that is what happens.
printf 'description: x\ntext:\n  - a\nfoo: 1\nbar: 2\n' > fix010.pdl
pdl --stream none fix010.pdl; echo "010 after help exit=$?"     # expect 1, Field not allowed: foo/bar
```

```python
# 3. Which help branch each golden takes. This decides the target text.
import difflib
KIND = ["model","code","text","data","call","if","repeat","read","get","function",
        "include","import","array","object","lastOf","sequence","match","map",
        "content","args","factor","aggregator","platform","processor"]
COMMON = ["description","spec","defs","def","contribute","parser","fallback",
          "retry","trace_error_on_retry","expectations","role"]
pool = sorted(KIND + COMMON)
assert difflib.get_close_matches("foo",   pool, n=1, cutoff=0.7) == []        # E-SCHEMA-007
assert difflib.get_close_matches("texts", pool, n=1, cutoff=0.7) == ["text"]  # E-SCHEMA-010
assert difflib.get_close_matches("xml", ["json","jsonl","yaml","csv"],
                                 n=1, cutoff=0.7) == []                       # E-SCHEMA-006

# 4. The premise, re-measured rather than trusted.
from pdl.pdl_ast import _block_tag, _BLOCK_KIND_OF_FIELD, BlockKind
assert _block_tag({"text": "hi", "parser": "xml"}) is BlockKind.TEXT
assert _block_tag({"foo": "bar"})       is BlockKind.EMPTY
assert _block_tag({"description": "d"}) is BlockKind.EMPTY   # the trap
assert _block_tag({"kind": "txt"}) == "txt"                  # arbitrary string!
assert len(_BLOCK_KIND_OF_FIELD) == 25

# 5. The tag -> $def table covers the unions and names nothing invented.
import json, pathlib, pdl.pdl
defs = json.loads((pathlib.Path(pdl.pdl.__file__).parent / "pdl-schema.json")
                  .read_text())["$defs"]
for union in ("BlockType", "ModelBlockType", "CodeBlockType"):
    refs = {a["$ref"].split("/")[-1] for a in defs[union]["oneOf"]}
    assert refs <= set(defs)
```

```bash
# 6. Nothing else in the corpus moves.
python -m pytest tests/errors -k "E-SCHEMA-001 or E-SCHEMA-002 or E-SCHEMA-003 \
  or E-SCHEMA-004 or E-SCHEMA-008 or E-LINT-001"    # expect all pass, goldens unchanged
```

If step 1's `- data: {foo: bar}` does **not** exit 0, the E-SCHEMA-007 `help:` must
be replaced before anything is written. It is the single suggestion in this spec I
am least able to check by reading: `DataBlock.data` is `ExpressionType[Any]`
(`pdl_ast.py`), the mapping contains no `${...}`, and a data block inside a `text:`
is stringified — but "reads as though it should work" is exactly what went wrong in
E-RUNTIME-007 and E-PARSE-001.

One suggestion was **considered and dropped** on reading, and should stay dropped:
"if `foo:` was meant as a field of the enclosing block, remove the `- `". Applied to
this reproducer it yields

```yaml
text:
  - 12
  foo: bar
```

which is a YAML error, not a fix. It trades a schema error for a parse error — the
E-RUNTIME-007 failure mode exactly.

---

## Blast radius

Measured by tracing every E-SCHEMA reproducer through the new dispatch, not
assumed.

| Entry | Data at the union | New branch | Golden |
| --- | --- | --- | --- |
| E-SCHEMA-001 | `{description, text}` → `TEXT`; `{model, parameterss}` → `MODEL` → `_model_block_tag` → `litellm` | same `$def` `match()` already chose | **unchanged** |
| E-SCHEMA-002 | `{descrption, text}` → `TEXT` | same | **unchanged** |
| E-SCHEMA-003 | `{defs, text}` → `TEXT`; `{function}` → `FUNCTION` | same | **unchanged** |
| E-SCHEMA-004 | `{text, role}` → `TEXT` | same | **unchanged** |
| E-SCHEMA-006 | `parser: xml` — scalar union, not the dict arm | new enum handling | **changes** (target above) |
| E-SCHEMA-007 | `{foo}` → `EMPTY` + unrecognised | new no-block diagnostic | **changes** (target above) |
| E-SCHEMA-008 | `{result, context}` against `ContributeElement` — **not** `defs["BlockType"]` | falls through untouched | **unchanged**, wall intact |
| E-SCHEMA-010 | `{description, texts, foo, bar}` → `EMPTY` + 3 unrecognised | new no-block diagnostic | **changes** — see below |

### E-SCHEMA-010 is collateral and needs the owner's eye

Today: five messages, in an unstable order, two of which are false
(`Missing required field: function` / `return`). Predicted after:

```
prog.pdl:2 - this is not a PDL block: nothing here says what it does

2 | texts:
  | ^ `texts` does not name a block kind
...
4 | foo: 1
  | ^ `foo` does not name a block kind

  Every block is named by the one field that says what it does. This mapping
  has none of them: `model`, `code`, `text`, ... `platform` or `processor`.

  note: `texts`, `foo` and `bar` are not fields any block accepts.

  help: did you mean `text:` instead of `texts:`?
```

Predicted L1→2, W2→3, Y1→3, F0→3, H0→3: **4/15 → 14/15**, the largest single
movement in this change. Two things must not be misread:

1. **The ordering defect is not fixed, only hidden.** It lives in the `set`
   differences at `pdl_schema_error_analyzer.py:152` and `:158`, which this change
   does not touch. Fix `texts` → `text` and the program produces two
   `Field not allowed` messages again, in `PYTHONHASHSEED` order — step 2 of the
   harness demonstrates it. `hygiene_unstable_order` describes a live defect and
   **must not be deleted** because this golden stopped showing it. Either the entry
   keeps a reproducer that still emits several messages, or the flag moves to one
   that does. Owner's call; I am not editing the corpus.
2. The new code must iterate `data.keys()` in **document order** and never a set,
   or it reintroduces the very defect 010 exists to record.

### Other risk

* **`tests/test_errors.py::test_error1` will fail** and must be updated in the same
  commit. It asserts the `FunctionBlock` misselection verbatim
  (`tests/test_errors.py:47-55`): `"line 0 - Missing required field: return"`,
  `"... function"`, `"... Field not allowed: texts\n  in texts"`. Under the new
  dispatch that data is one no-block diagnostic. `test_error2` and `test_error5` are
  unaffected (both reach a real kind). Note the expected strings there start
  `line 0 - `, which the `located_message` wrapping preserves — that is why the
  builders must not set `Diagnostic.file` themselves.
* **`PDLParseError.message` stays a `list[str]`** and each element stays one whole
  diagnostic with its own `  in <path>` line (`1ece9ed`, and the docstring at
  `pdl_schema_error_analyzer.py:92-107`). What changes is that an element may now be
  **multi-line**. `located_message` already handles that — it partitions on the
  first newline and inserts the `in` line under the header (`:463-467`) — and
  E-CODE-001 already ships a multi-line element. Anything that joins or reprs the
  list is worth re-checking; `pdl-lint`'s golden (`E-LINT-001`) uses an
  E-SCHEMA-001-shaped probe and is unaffected.
* **No AST change. No public API change. No new dependency.** `difflib`, `json` and
  `textwrap` are stdlib and all three are already imported by `pdl_diagnostics`.
  `PdlLocationType` is unchanged; the trace format is unchanged.
* `pdl_schema_validator.py` re-runs `analyze_errors` for `spec:` and argument
  checking (E-TYPE-001/002/003). Those schemas are `PdlTypeType`-derived and are not
  `defs["BlockType"]`, so the dict arm does not change for them. The **scalar** arm
  does change, for any user `spec:` that is a union containing an `enum` — today
  such a value is silently accepted when its JSON type matches, which is the same
  bug as E-SCHEMA-006 in a different schema. That is a *fix*, and it can turn a
  program that passes today into one that fails. Small, but it is a semantic change
  in the §5.5 sense and the owner should know before it lands.

---

## Rejected alternatives

**Read pydantic's `ValidationError.errors()` instead of re-walking the schema.**
It is right there in `parse_dict`'s `except` (`pdl_parser.py:325`), and DROP #3 notes
it "knows precisely which union branch and field failed". Rejected on two grounds:
§5.3 names the discriminator, not the exception; and a tagged union's error `loc`
carries the **tag** as a path segment, so a `TextBlock` failure has a `text` segment
that is indistinguishable from the `text` *field* — mapping those back onto a PDL
block path is a guess I cannot verify without running it, and a wrong block path is
a wrong location.

**Emit N × `Field not allowed` against `EmptyBlock` instead of one no-block
diagnostic.** It is the established shape (E-SCHEMA-001 scores S3 with it) and it
needs no new message. Rejected because it presumes the answer to the question that
was actually asked: it tells someone who wrote `- foo: bar` that they wrote an empty
block with a bad field in it. True of the schema, false of their program, and it
throws away the rule — that a block is named by the field that says what it does —
which is the only thing that makes the list of 24 names mean anything.

**Put the E-SCHEMA-006 caret on `xml` by deriving the value's column from the
entry's `end_col`.** `_walk` stores the key's start and the *value's* end
(`pdl_location_utils.py:186-194`), so for a one-line unquoted scalar the value's
start is `end_col - len(value)`. It is arithmetic, it needs no new machinery, and it
is wrong the moment the value is quoted, folded or multi-line — a caret under the
wrong column with nothing on the page to say so. Recording value marks in `_walk` is
the honest version of this and is its own item.

---

## Expected rubric delta

| Entry | Today | Target | Δ |
| --- | --- | --- | --- |
| E-SCHEMA-006 | L0 W0 Y0 F0 H2 = 2 | L2 W3 Y3 F2 H3 = **13** | +11 |
| E-SCHEMA-007 | L2 W0 Y2 F0 H1 = 5 | L2 W3 Y3 F2 H3 = **13** | +8 |
| E-SCHEMA-010 (collateral) | L1 W2 Y1 F0 H0 = 4 | L2 W3 Y3 F3 H3 = **14** | +10, owner sign-off |
| the fallback branch (unpinned) | — | L0 W2 Y1 F1 H3 = 7 | honest ceiling |

Two S1 entries leave the severity table; `E-SCHEMA-005`'s "unreachable in practice"
row becomes false and needs restating.

**The sentence a user takes away:** PDL now tells them which block kind it thought
they were writing, or that they wrote none — in the twenty-four words they used to
write it, under a caret, with the edit that fixes it — instead of a shrug or a
screenful of `$ref`s.
