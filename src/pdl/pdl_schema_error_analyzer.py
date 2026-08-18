from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

# `_block_tag`, `_code_block_tag`, `_model_block_tag` and `_BLOCK_KIND_OF_FIELD`
# are underscore-private to `pdl_ast` because nothing outside the union
# definitions had a use for them. Decision 5.3 gives them one: the analyzer
# answers "which branch did the user mean?" with the same discriminator
# pydantic uses, rather than with a second, weaker guess of its own.
from .pdl_ast import (  # noqa: PLC2701
    _BLOCK_KIND_OF_FIELD,
    BlockKind,
    PdlLocationType,
    _block_tag,
    _code_block_tag,
    _model_block_tag,
)
from .pdl_diagnostics import (
    Span,
    field_not_allowed_diagnostic,
    list_expected_diagnostic,
    list_length_diagnostic,
    mapping_expected_diagnostic,
    no_block_kind_diagnostic,
    prefer,
    scalar_value_diagnostic,
    single_value_diagnostic,
    unknown_tag_diagnostic,
    yaml_value,
)
from .pdl_location_utils import append, get_source, located_message, source_text
from .pdl_schema_utils import convert_to_json_type, json_types_convert


def is_base_type(schema):
    if "type" in schema:
        the_type = schema["type"]
        if the_type in set(
            [
                "null",
                "string",
                "boolean",
                "integer",
                "number",
                "str",
                "bool",
                "int",
                "float",
            ]
        ):
            return True
    if "enum" in schema:
        return True
    if "const" in schema:
        # A one-value enum, spelled the way pydantic spells a `Literal`. Absent
        # here until now, which is the second of E-SCHEMA-006's two routes into
        # the "analyzer returned nothing" fallback: `lang: ruby` was checked
        # against `PythonCodeBlock`, whose `lang` is `{"const": "python",
        # "type": "string"}`, and passed because it is a string.
        return True
    return False


def is_of_type(the_type, data) -> bool:
    """Whether `data` is of a JSON Schema base type, as pydantic reads it.

    One relaxation over a bare `isinstance`, and it is a relaxation only:
    JSON Schema's `number` admits an integer, `1` and `1.0` are the same number,
    and pydantic coerces the one to the other, so `isinstance(1, float)` is the
    wrong question. Nothing reached this before -- `ExpressionFloatOrFloatFloat`
    is the only `number` a program is likely to write and the arm that leads to
    it crashed -- and with the crash fixed, `jitter: [1, 2]`, a program that
    runs, would otherwise be told each of its items is not a number. A false
    complaint standing beside a true one is worse than the true one alone.

    `bool` stays excluded, as `isinstance(True, float)` already excluded it:
    Python makes `bool` a subclass of `int`, YAML does not, and admitting it
    here would be a new complaint's worth of change rather than one fewer.
    """
    if the_type is None:
        # `json_types_convert["null"]` is `None`, which `isinstance` cannot take
        # as a class. The caller's `or` short-circuits before reaching here for
        # every value but `None` itself, so this is unreachable today and is
        # written out rather than left as a `TypeError` waiting in the reporter.
        return data is None
    if the_type is float:
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    return isinstance(data, the_type)


def is_array(schema):
    """Whether `schema` describes a list.

    `prefixItems` counts even without a `type`, because a tuple schema is an
    array schema whether or not the generator wrote the keyword. Pydantic does
    write it, so nothing in `pdl-schema.json` depends on this clause today; a
    `spec:` carrying a hand-written JSON Schema can, and answering "no" there
    would send a list down the union arm to be told it is not allowed at all.
    """
    if "type" in schema:
        return schema["type"] == "array"
    return "prefixItems" in schema


def is_object(schema):
    if "type" in schema:
        return schema["type"] == "object"
    return False


def alternatives(schema):
    """Members of a union schema, spelled either `anyOf` or `oneOf`."""
    return schema.get("anyOf", schema.get("oneOf"))


def is_any_of(schema):
    return alternatives(schema) is not None


def nullable(schema):
    for item in alternatives(schema) or []:
        if "type" in item and item["type"] == "null":
            return True
    return False


def get_non_null_type(schema):
    items = alternatives(schema)
    if items is not None and len(items) == 2:
        for item in items:
            if "type" not in item or "type" in item and item["type"] != "null":
                return item
    return None


def object_alternatives(defs, schema, seen=None):
    """Members of a union schema, following `$ref`s and unions nested in them.

    `BlockType` is a union whose `model` and `code` members are themselves
    unions, so the alternatives a block can be reported against are not all at
    the same level.
    """
    seen = set() if seen is None else seen
    for item in alternatives(schema) or []:
        if "$ref" in item:
            ref_string = item["$ref"].split("/")[2]
            if ref_string in seen:
                continue
            seen.add(ref_string)
            item = defs[ref_string]
        if is_any_of(item):
            yield from object_alternatives(defs, item, seen)
        else:
            yield item


def match(ref_type, data):
    all_fields = ref_type.get("properties", {}).keys()
    intersection = list(set(data.keys()) & set(all_fields))
    return len(intersection)


LOWERCASED_FIELDS = frozenset({"parser", "mode", "lang"})
"""Fields PDL lower-cases before validating them.

`pdl_ast` annotates each of these `BeforeValidator(_ensure_lower)`, so
`parser: JSON` and `lang: PYTHON` are accepted programs. JSON Schema has no way
to say that, so the schema's `enum` and `const` for these fields read
stricter than the validator is. Comparing without this would make the analyzer
contradict the validator and complain about a field that is not at fault --
which only ever happens in a program already being rejected for some other
reason, and a false complaint beside a true one is worse than the true one
alone.

Deliberately not applied to every enum: `ContributeTarget` and `platform:` have
no such validator, and `contribute: [Result]` really is wrong.
"""


def as_validated(data, loc: PdlLocationType):
    """`data` as pydantic will have seen it, for the fields PDL normalises."""
    if isinstance(data, str) and loc.path and loc.path[-1] in LOWERCASED_FIELDS:
        return data.lower()
    return data


# --------------------------------------------------------------------------
# Discriminated unions (decision 5.3)
# --------------------------------------------------------------------------

BLOCK_KIND_FIELDS: tuple[str, ...] = tuple(
    prefer(
        [field for field, _ in _BLOCK_KIND_OF_FIELD if field != "program"],
        # Most-written first, because this list is read by someone who has just
        # been told their mapping names no block. `program` is excluded above:
        # it selects `ErrorBlock`, which the interpreter builds around a failed
        # block and nobody writes by hand.
        (
            "model",
            "code",
            "text",
            "data",
            "call",
            "if",
            "repeat",
            "read",
            "get",
            "function",
            "include",
            "import",
            "array",
            "object",
            "lastOf",
            "sequence",
            "match",
            "map",
            "content",
            "args",
            "factor",
            "aggregator",
            "platform",
            "processor",
        ),
    )
)
"""The fields that name a kind of block, in the order a diagnostic lists them."""


BLOCK_TAG_DEFS: dict[str, str] = {
    "expression": "ExpressionBlock",
    BlockKind.FUNCTION: "FunctionBlock",
    BlockKind.CALL: "CallBlock",
    BlockKind.MODEL: "ModelBlockType",
    BlockKind.CODE: "CodeBlockType",
    BlockKind.GET: "GetBlock",
    BlockKind.DATA: "DataBlock",
    BlockKind.MESSAGE: "MessageBlock",
    BlockKind.READ: "ReadBlock",
    BlockKind.FACTOR: "FactorBlock",
    BlockKind.AGGREGATOR: "AggregatorBlock",
    BlockKind.ERROR: "ErrorBlock",
    BlockKind.EMPTY: "EmptyBlock",
    BlockKind.SEQUENCE: "SequenceBlock",
    BlockKind.TEXT: "TextBlock",
    BlockKind.LASTOF: "LastOfBlock",
    BlockKind.ARRAY: "ArrayBlock",
    BlockKind.OBJECT: "ObjectBlock",
    BlockKind.IF: "IfBlock",
    BlockKind.MATCH: "MatchBlock",
    BlockKind.REPEAT: "RepeatBlock",
    BlockKind.MAP: "MapBlock",
    BlockKind.INCLUDE: "IncludeBlock",
    BlockKind.IMPORT: "ImportBlock",
}
"""`_block_tag`'s answer to the `$def` it selects, in `BlockType`'s `oneOf` order.

The one hand-written thing in this file and the one thing that can silently
rot, so `tests/test_schema_unions.py` pins it against both `pdl-schema.json`
and the `Tag(...)` order in `pdl_ast`.
"""

MODEL_TAG_DEFS: dict[str, str] = {
    "litellm": "LitellmModelBlock",
    "granite-io": "GraniteioModelBlock",
    "openai": "OpenaiModelBlock",
}

CODE_TAG_DEFS: dict[str, str] = {
    "python": "PythonCodeBlock",
    "ipython": "IPythonCodeBlock",
    "jinja": "JinjaCodeBlock",
    "pdl": "PdlCodeBlock",
    "command": "CommandCodeBlock",
    "args": "ArgsBlock",
}


@dataclass(frozen=True)
class DiscriminatedUnion:
    """One union PDL already discriminates, and what to say when nothing matches."""

    name: str
    tag_of: Callable[[Any], Any]
    table: Mapping[str, str]
    tag_key: str
    """The field the tag is read from when it is not one PDL knows. A miss is
    only reachable through an explicit value there, so the caret has somewhere
    to land."""
    headline: str
    rule: str
    named: tuple[str, ...]
    """The tags the rule spells out, which is not always every key of `table`:
    `args` is a `CodeBlockType` branch and is not a language."""


DISCRIMINATED_UNIONS: tuple[DiscriminatedUnion, ...] = (
    DiscriminatedUnion(
        name="BlockType",
        tag_of=_block_tag,
        table=BLOCK_TAG_DEFS,
        tag_key="kind",
        headline="`{value}` is not a kind of PDL block",
        rule=(
            "An explicit `kind:` names the sort of block PDL should read this "
            "as. PDL's kinds are {names}."
        ),
        named=tuple(
            tag for tag in BLOCK_TAG_DEFS if tag not in ("expression", BlockKind.ERROR)
        ),
    ),
    DiscriminatedUnion(
        name="ModelBlockType",
        tag_of=_model_block_tag,
        table=MODEL_TAG_DEFS,
        tag_key="platform",
        headline="`{value}` is not a model platform PDL knows",
        rule=(
            "The `platform:` of a `model:` block chooses how PDL calls the "
            "model. PDL knows {names}."
        ),
        named=tuple(MODEL_TAG_DEFS),
    ),
    DiscriminatedUnion(
        name="CodeBlockType",
        tag_of=_code_block_tag,
        table=CODE_TAG_DEFS,
        tag_key="lang",
        headline="`{value}` is not a language PDL can run",
        rule=(
            "The `lang:` of a `code:` block chooses the interpreter. PDL runs "
            "{names}."
        ),
        named=tuple(tag for tag in CODE_TAG_DEFS if tag != "args"),
    ),
)


def discriminated_union(defs, schema) -> DiscriminatedUnion | None:
    """The union `schema` *is*, recognised by identity on the `$defs` entry.

    Identity rather than equality, and it holds because `analyze_errors` follows
    a `$ref` by handing `defs[name]` straight on and never copies. Any other
    union -- `ContributeElement`, on which E-SCHEMA-008 rides, or a user's own
    `spec:` -- fails the test and takes the existing path untouched.
    """
    for union in DISCRIMINATED_UNIONS:
        if schema is defs.get(union.name):
            return union
    return None


def can_hold_a_mapping(item) -> bool:
    """Whether a union member could accept a mapping at all.

    Only a declared scalar or array `type` rules it out. Anything else -- an
    object, a `$ref`, the empty schema -- might, and the caller has to stay on
    the existing path when more than one member might.
    """
    return "$ref" in item or item.get("type") in (None, "object")


def deferred_union(defs, schema) -> "DiscriminatedUnion | None":
    """The discriminated union a *containing* union defers a mapping to.

    `text:` is `BlockOrBlocksType` -- `anyOf[BlockType, array of BlockType]` --
    written inline in the property rather than as a `$def` of its own, so the
    identity test does not see it, and `text: {a: 1}` would otherwise still be
    answered with the wall of `$ref`s that E-SCHEMA-007 is about. A mapping
    there can only have been meant as the block, so the discriminator gives the
    same answer by the same route.

    Both conditions matter. Exactly one member may be a discriminated union, and
    no other member may be able to hold a mapping -- which is what keeps
    `ContributeElement`, whose members include a second object shape, on the
    existing path with E-SCHEMA-008's golden untouched.
    """
    found: list[DiscriminatedUnion] = []
    others = []
    for item in alternatives(schema) or []:
        union = None
        if "$ref" in item:
            target = defs.get(item["$ref"].split("/")[2])
            union = None if target is None else discriminated_union(defs, target)
        if union is not None:
            if union not in found:
                found.append(union)
        else:
            others.append(item)
    if len(found) != 1 or any(can_hold_a_mapping(item) for item in others):
        return None
    return found[0]


def empty_block_fields(defs) -> Sequence[str]:
    """The fields every block shares, read from the schema and never hardcoded."""
    return list(defs.get("EmptyBlock", {}).get("properties", {}))


def near_miss_pool(defs) -> list[str]:
    """Field names a mistyped key is compared against.

    A fixed, ordered list: the fields that select a kind plus the ones every
    block accepts, minus PDL's own bookkeeping (`pdl__*`) and `kind`, which the
    dumper writes and a user does not. Ordered because `difflib` ties are broken
    by position and a diagnostic may not depend on `PYTHONHASHSEED`.
    """
    common = [
        field
        for field in empty_block_fields(defs)
        if not field.startswith("pdl__") and field != "kind"
    ]
    return sorted(set(BLOCK_KIND_FIELDS) | set(common))


def _correction_pool(
    defs, all_fields: Sequence[str], written: str, guessed: bool
) -> list[str]:
    """The field names `written` may be offered as a misspelling of, or `[]`.

    The candidate set is the properties of **the schema being checked**, and
    that is the whole of why the suggestion is worth anything: `parameters` is
    not a field of `TextBlock` and `model` is not either, so testing
    `parameterss` against the wrong branch of the block union answers nothing.
    It is also why the three guards below exist, each of which turns a confident
    wrong answer into silence.

    **A guessed branch suggests nothing.** For a block PDL answers the union
    with the discriminator pydantic uses, so the properties really are the ones
    that key would be read against. Four unions have no discriminator --
    `JoinType`, `ParserType`, `PatternType`, `PdlTypeType` -- and there
    `analyze_errors` picks the branch sharing the most field names with the
    data, which for `join: {as: array, wth: ", "}` is a **tie** between
    `JoinText` and `JoinArray` broken by document order. `JoinText` has `with:`;
    the user wrote `as: array` and meant `JoinArray`, which does not. "Did you
    mean `with:`?" would there be a correction to a field that is still not
    allowed where they wrote it. This costs a real suggestion too -- a typo of
    `regex:` inside `parser:` arrives by the same route -- and that is the trade
    RUBRIC.md asks for: silence outranks a confidently-stated wrong edit.

    **A name PDL knows is not a misspelling.** `content:` is a real PDL field,
    just not one `TextBlock` takes, and it is within `difflib`'s reach of
    `context:`. Writing a word PDL has is not mistyping one, and telling that
    user they meant a different word is worse than the message alone.

    **`pdl__*` is not a correction.** Those are the bookkeeping fields the
    dumper writes into a trace and nobody writes by hand -- `result:` is one
    edit from `pdl__result` -- and `near_miss_pool` excludes them from
    E-SCHEMA-007's pool for the same reason.

    The result keeps `schema["properties"]`' own document order, never a set:
    `difflib` breaks ties by position and a diagnostic may not depend on
    `PYTHONHASHSEED`.
    """
    if guessed:
        return []
    if written in near_miss_pool(defs):
        return []
    return [name for name in all_fields if not name.startswith("pdl__")]


def scalar_matches(defs, item, data, loc) -> bool:
    """Whether a scalar satisfies one member of a union.

    Every constraint the member carries has to hold, not merely the first one
    that happens to. The previous spelling set a "matched" flag from `type` and
    then set it again from `enum`, so an alternative carrying both --
    `ParserType`'s first, `{"enum": ["json", ...], "type": "string"}` -- said
    yes to any string at all and the `enum` below it was dead code. That is the
    whole of E-SCHEMA-006's first route: not a missing case, a check that a
    weaker check above it had already answered.

    The `$ref` arm also *assigned* the flag rather than accumulating it, so a
    later alternative could reset an earlier match to `False`; folding the loop
    into `any()` removes that as a shape rather than as a fix.
    """
    if item == {}:
        return True
    if "$ref" in item:
        ref_type = defs[item["$ref"].split("/")[2]]
        return not analyze_errors(defs, ref_type, data, loc)
    constrained = False
    if "type" in item:
        constrained = True
        if item["type"] != convert_to_json_type(type(data)):
            return False
    if "enum" in item:
        constrained = True
        if as_validated(data, loc) not in item["enum"]:
            return False
    if "const" in item:
        constrained = True
        if as_validated(data, loc) != item["const"]:
            return False
    return constrained


def _field_name(loc: PdlLocationType) -> str | None:
    """The field a location is *inside*, or None for a list item or the root."""
    if not loc.path:
        return None
    last = loc.path[-1]
    return None if last.startswith("[") else last


_REMOVAL_EFFECT = {"ParserType": " to leave the output as text"}
"""What dropping the field would leave behind, for the unions where that is
sayable. Keyed by `$def` name and matched by identity, like the block unions."""


def union_accepts(defs, schema) -> tuple[list[Any], list[str]]:
    """The spellings a union accepts: enumerated values, and one-key mappings.

    Shared by the scalar arm and the list arm so that the two cannot drift into
    describing the same union in two different vocabularies.
    """
    accepted: list[Any] = []
    mapping_keys: list[str] = []
    for item in alternatives(schema) or []:
        for value in item.get("enum", []):
            if value not in accepted:
                accepted.append(value)
        if "const" in item and item["const"] not in accepted:
            accepted.append(item["const"])
        if "$ref" in item:
            ref_type = defs.get(item["$ref"].split("/")[2], {})
            required = ref_type.get("required") or []
            if len(required) == 1 and required[0] not in mapping_keys:
                mapping_keys.append(required[0])
    return accepted, mapping_keys


def scalar_union_message(defs, schema, data, loc: PdlLocationType) -> str:
    """The message for a scalar that matched no member of its union.

    Falls back to the old `should be of type <schema>` dump when the union has
    no enumerated values, because then there is no list of accepted spellings to
    offer and naming the field alone would be less, not more, than the schema.
    """
    accepted, mapping_keys = union_accepts(defs, schema)
    if not accepted:
        return located_message(loc, str(data) + " should be of type " + str(schema))

    effect = ""
    for name, phrase in _REMOVAL_EFFECT.items():
        if schema is defs.get(name):
            effect = phrase
    diag = scalar_value_diagnostic(
        value=data,
        field_name=_field_name(loc),
        accepted=accepted,
        mapping_keys=prefer(mapping_keys, ("regex", "pdl")),
        removal_effect=effect,
        line=loc.line,
        col=loc.col,
        source=source_text(loc.file),
    )
    return located_message(loc, diag.text)


# --------------------------------------------------------------------------
# Shapes (E-SCHEMA-009): a list where a mapping belongs, and the reverse
# --------------------------------------------------------------------------

_SHAPE_WORDS = {
    "array": "list",
    "object": "mapping",
    "number": "number",
    "integer": "integer",
    "string": "string",
    "boolean": "boolean",
    "null": "null",
}
"""JSON Schema's `type` in the words PDL's own prose uses. See `_YAML_SHAPES`."""


def type_word(defs, schema) -> str:
    """The name of the one type `schema` admits, or `""` if it admits several."""
    if not isinstance(schema, dict):
        return ""
    if "$ref" in schema:
        ref_string = schema["$ref"].split("/")[2]
        return "" if ref_string not in defs else type_word(defs, defs[ref_string])
    declared = schema.get("type")
    return _SHAPE_WORDS.get(declared, "") if isinstance(declared, str) else ""


def satisfies(defs, schema, data, loc: PdlLocationType) -> bool:
    """Whether `data` satisfies `schema`, for deciding whether to offer an edit.

    Every `help:` in this section is a rewrite of the user's own value, and this
    is what stops one being offered unchecked. The exception swallowing is
    deliberate and narrow: the only consequence of answering `False` is that a
    suggestion is withheld, whereas an exception raised here would be a crash
    *inside the error reporter*, which is the whole of what E-SCHEMA-009's S0
    entry records and decision 5.8 forbids.
    """
    try:
        return not analyze_errors(defs, schema, data, loc)
    except (KeyError, IndexError, TypeError, RecursionError):  # pragma: no cover
        return False


def _listed_values(schema) -> tuple[list, bool]:
    """The values a non-union schema spells out, and whether it spells out all."""
    if "enum" in schema:
        return list(schema["enum"]), True
    if "const" in schema:
        return [schema["const"]], True
    return [], False


def enumerated(defs, schema, seen: frozenset[str] = frozenset()) -> tuple[list, bool]:
    """The values a schema accepts, and whether they are the whole of them.

    The second half is the load-bearing one. `ContributeElement` enumerates four
    targets *and* admits a mapping, so a diagnostic that printed the four as the
    accepted set would be stating something false about a schema it had just
    read; `analyze_errors` would then reject a program the message called legal.
    """
    if not isinstance(schema, dict) or not schema:
        return [], False
    if "$ref" in schema:
        ref_string = schema["$ref"].split("/")[2]
        if ref_string in seen or ref_string not in defs:
            return [], False
        return enumerated(defs, defs[ref_string], seen | {ref_string})
    members = alternatives(schema)
    if members is None:
        return _listed_values(schema)
    values: list = []
    exhaustive = True
    for item in members:
        found, whole = enumerated(defs, item, seen)
        for value in found:
            if value not in values:
                values.append(value)
        exhaustive = exhaustive and whole
    return values, exhaustive


def fold_entries(data) -> dict | None:
    """A list of single-entry mappings as the one mapping it was meant to be.

    `defs:` written as a list of definitions is the mistake; folding is the
    edit, and it invents nothing. `None` when the fold would lose something: a
    non-mapping item, or a key written twice, where merging would silently drop
    one of the user's own definitions.
    """
    if not isinstance(data, list) or not data:
        return None
    folded: dict = {}
    for item in data:
        if not isinstance(item, dict):
            return None
        for key, value in item.items():
            if key in folded:
                return None
            folded[key] = value
    return folded


def _child_segment(data) -> str | None:
    """The path segment of the first thing *inside* `data`, if it has one."""
    if isinstance(data, list) and data:
        return "[0]"
    if isinstance(data, dict) and data:
        first = next(iter(data))
        return first if isinstance(first, str) else None
    return None


def value_location(loc: PdlLocationType, data) -> PdlLocationType:
    """`loc`, moved to the line the offending value itself starts on.

    `_walk` records a mapping entry at its **key**, deliberately, so `loc` for
    `defs:` is the line `defs` is written on while the list the complaint is
    about may begin on the next one. The value's own node is not recorded, but
    its first child is, and in block style that child is on the line the value
    begins on -- which is why this asks the registry for the child rather than
    promising a position nothing recorded.

    The path is left alone. It names the block the claim is *about*, and
    `defs.greeting` would attribute a complaint about `defs:` to one entry of
    it; `_no_block_message` splits the two the same way.

    `append` carries the parent's position down on a miss, so a value with
    nothing inside it, or one whose child shares the key's line, keeps the key's
    line rather than moving to an invented one.
    """
    segment = _child_segment(data)
    if segment is None:
        return loc
    inner = append(loc, segment)
    if inner.line == loc.line:
        return loc
    return PdlLocationType(file=loc.file, path=loc.path, line=inner.line, col=inner.col)


def _shape_spans(loc: PdlLocationType, label: str = "") -> list[Span]:
    if not loc.line or loc.line < 1:
        return []
    return [Span(line=loc.line, col=loc.col or None, label=label, primary=True)]


def _shape_source(loc: PdlLocationType, subject: str) -> str | None:
    """The text an excerpt is drawn from, or None when there is nothing to quote.

    A `subject` means the value being validated is one the program *produced* --
    a block's result against its `spec:` -- while `loc` names the `spec:` that
    declared the type. Quoting that line under a claim about a runtime value
    would put a caret on text that is not the offending value, which `RUBRIC.md`
    ranks below showing no excerpt at all.
    """
    return None if subject else source_text(loc.file)


def _writable_field(loc: PdlLocationType, subject: str) -> str | None:
    """The field name, when the value at `loc` is one the user can go and edit.

    A `subject` means it is not: the value is one the program produced and the
    field the location names is the `spec:` that declared its type. Every
    suggestion in this section is a rewrite of the offending value, so with a
    subject there is none to offer -- `put the value in a list: spec: [Hello]`
    would be an instruction to write the block's own output into its type
    declaration.
    """
    return None if subject else _field_name(loc)


def not_a_list_message(defs, schema, data, loc: PdlLocationType, subject: str) -> str:
    """A value where the schema wants a list of them. `contribute: result`."""
    values, exhaustive = enumerated(defs, schema.get("items"))
    field = _writable_field(loc, subject)
    wrapped = ""
    if field is not None and satisfies(defs, schema, [data], loc):
        wrapped = yaml_value([data])
    diag = list_expected_diagnostic(
        field_name=field,
        subject=subject,
        value=data,
        item_values=values,
        item_values_exhaustive=exhaustive,
        wrapped=wrapped,
        spans=_shape_spans(loc),
        source=_shape_source(loc, subject),
    )
    return located_message(loc, diag.text)


def not_a_mapping_message(
    defs, schema, data, loc: PdlLocationType, subject: str
) -> str:
    """A list, or a scalar, where the schema wants a mapping. `defs:` as a list."""
    properties = list(schema.get("properties") or {})
    additional = schema.get("additionalProperties")
    field = _writable_field(loc, subject)
    merged = ""
    folded = fold_entries(data) if field is not None else None
    if folded is not None and satisfies(defs, schema, folded, loc):
        merged = yaml_value(folded)
    where = value_location(loc, data)
    diag = mapping_expected_diagnostic(
        field_name=field,
        subject=subject,
        value=data,
        # A long list of field names is a wall rather than a rule; past this the
        # shape is what the reader needs and the names are noise.
        key_names=properties if len(properties) <= 6 else [],
        open_keys=additional is not None and not isinstance(additional, bool),
        merged=merged,
        spans=_shape_spans(where),
        source=_shape_source(loc, subject),
    )
    return located_message(where, diag.text)


def no_array_member_message(
    defs, schema, data, loc: PdlLocationType, subject: str
) -> str:
    """A list in a union that has no array member. `fallback:` as a sequence.

    `is_array` reads a literal `type: array`, so an array behind a `$ref` is
    invisible to the scan that reached here -- but every alternative of
    `BlockType` is a `$ref` and none of them is an array, so for the case this
    is written for the scan's answer is right and the field really does take one
    value. What it takes is knowable, and the message this replaces
    (`should not be a list`) said only what it does not.
    """
    union = discriminated_union(defs, schema)
    takes_a_block = union is not None and union.name == "BlockType"
    accepted, mapping_keys = union_accepts(defs, schema)
    field = _writable_field(loc, subject)
    only = ""
    in_order = ""
    if field is None:
        pass
    elif len(data) == 1 and satisfies(defs, schema, data[0], loc):
        only = yaml_value(data[0])
    elif takes_a_block and len(data) > 1:
        sequence = {"text": data}
        if satisfies(defs, schema, sequence, loc):
            in_order = yaml_value(sequence)
    where = value_location(loc, data)
    diag = single_value_diagnostic(
        field_name=field,
        subject=subject,
        value=data,
        takes_a_block=takes_a_block,
        accepted=accepted,
        mapping_keys=prefer(mapping_keys, ("regex", "pdl")),
        only=only,
        in_order=in_order,
        spans=_shape_spans(where),
        source=_shape_source(loc, subject),
    )
    return located_message(where, diag.text)


def list_length_message(defs, schema, data, loc: PdlLocationType, subject: str) -> str:
    """A list the schema fixes the length of. The S0 entry of E-SCHEMA-009.

    `retry: {jitter: [1, 2, 3]}` crashed the analyzer here until this existed:
    `ExpressionFloatOrFloatFloat` renders its pair alternative as `prefixItems`
    with `minItems`/`maxItems` and **no `items`**, and the array arm subscripted
    `schema["items"]` for every element. Guarding the subscript alone would have
    answered a length error with silence -- three items where two are allowed is
    what is actually wrong, and it is what is said.
    """
    prefix = schema.get("prefixItems") or []
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    positions = [type_word(defs, item) for item in prefix]
    if not all(positions):
        # One unnameable position and the shape cannot be written out; a partly
        # filled `[number, ]` would be a form the user cannot copy.
        positions = []

    field = _writable_field(loc, subject)
    kept = ""
    where = loc
    label = ""
    if maximum is not None and len(data) > maximum:
        over = len(data) - maximum
        label = "one item too many" if over == 1 else f"{over} items too many"
        first_extra = append(loc, "[" + str(maximum) + "]")
        if first_extra.line:
            where = PdlLocationType(
                file=loc.file,
                path=loc.path,
                line=first_extra.line,
                col=first_extra.col,
            )
        if field is not None and satisfies(defs, schema, data[:maximum], loc):
            kept = yaml_value(data[:maximum])
    diag = list_length_diagnostic(
        field_name=field,
        subject=subject,
        count=len(data),
        minimum=minimum,
        maximum=maximum,
        positions=positions,
        kept=kept,
        spans=_shape_spans(where, label),
        source=_shape_source(loc, subject),
    )
    return located_message(where, diag.text)


def analyze_list(  # pylint: disable=too-many-arguments
    defs, schema, data, loc: PdlLocationType, subject: str, *, guessed: bool = False
) -> list[str]:
    """Every way a list fails an array schema: its length, then its elements.

    Three keywords, and `items` is only one of them. `prefixItems` gives a type
    per position and stops; `items` covers every position `prefixItems` does not
    and is **absent** for a fixed-length tuple, where it would have nothing to
    say. An absent `items` constrains nothing, which is why the loop skips
    rather than complains -- reading it unconditionally is the defect.
    """
    ret: list[str] = []
    prefix = schema.get("prefixItems") or []
    rest = schema.get("items")
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if (minimum is not None and len(data) < minimum) or (
        maximum is not None and len(data) > maximum
    ):
        ret.append(list_length_message(defs, schema, data, loc, subject))
    for index, item in enumerate(data):
        item_schema = prefix[index] if index < len(prefix) else rest
        if not isinstance(item_schema, dict):
            # Absent, or the JSON Schema booleans. `false` forbids the element
            # outright, which is what `maxItems` says above in every schema PDL
            # generates; saying it twice would be two complaints about one item.
            continue
        newloc = append(loc, "[" + str(index) + "]")
        ret += analyze_errors(defs, item_schema, item, newloc, guessed=guessed)
    return ret


def analyze_discriminated(
    defs,
    union: DiscriminatedUnion,
    data: dict,
    loc: PdlLocationType,
    *,
    guessed: bool = False,
) -> list[str]:
    """Validate `data` against the one branch its discriminator selects.

    Three outcomes, and the middle one is the trap. `_block_tag` answers
    `BlockKind.EMPTY` both for `{"foo": "bar"}`, which is not a block, and for
    `{"description": "d"}`, which is a perfectly good one, so the tag cannot be
    read as "nothing matched". The keys can: `_BLOCK_KIND_OF_FIELD` holds
    exactly the fields that select a non-empty kind and `EmptyBlock` holds
    exactly the fields every block shares, so a key in neither names nothing at
    all.
    """
    tag = union.tag_of(data)
    if union.name == "BlockType" and tag == BlockKind.EMPTY:
        recognised = empty_block_fields(defs)
        # Document order, never a set: this is the code that fixes
        # E-SCHEMA-010's unstable ordering and it must not reintroduce it.
        unrecognised = [key for key in data if key not in recognised]
        if unrecognised:
            kept = {key: value for key, value in data.items() if key in recognised}
            return [_no_block_message(defs, data, unrecognised, loc)] + analyze_errors(
                defs, defs["EmptyBlock"], kept, loc, guessed=guessed
            )
        return analyze_errors(defs, defs["EmptyBlock"], data, loc, guessed=guessed)

    ref_string = union.table.get(tag) if isinstance(tag, str) else None
    if ref_string is None:
        return [_unknown_tag_message(union, data, loc)]
    return analyze_errors(defs, defs[ref_string], data, loc, guessed=guessed)


def _no_block_message(defs, data, unrecognised, loc: PdlLocationType) -> str:
    """E-SCHEMA-007, located at the first offending key and pathed at the block.

    The two differ: the header has to agree with the caret, so it takes the
    key's line, while the `  in <path>` line names the block the claim is about.
    For `- foo: bar` they coincide; for E-SCHEMA-010 the block is the whole
    document and the key is on line 2.
    """
    source = get_source(loc.file)
    keys: list[tuple[str, int | None, int | None]] = []
    for key in unrecognised:
        # The registry is asked directly rather than through `append`, which
        # carries the parent's position down on a miss. Here that would put a
        # caret on the block and label it with the name of a key.
        mark = None if source is None else source.mark(loc.path + [key])
        keys.append((key, None, None) if mark is None else (key, mark.line, mark.col))
    diag = no_block_kind_diagnostic(
        value=data,
        unrecognised=keys,
        kind_fields=BLOCK_KIND_FIELDS,
        near_miss_pool=near_miss_pool(defs),
        in_list=bool(loc.path) and loc.path[-1].startswith("["),
        source=source_text(loc.file),
    )
    # Both coordinates or neither: a `keys` entry is `(key, None, None)` on a
    # registry miss and `(key, mark.line, mark.col)` otherwise. Testing both is
    # what the tuple actually promises, and it is what lets `PdlLocationType`
    # take a plain `int` for each.
    first = next(
        ((line, col) for _, line, col in keys if line is not None and col is not None),
        None,
    )
    header_loc = (
        loc
        if first is None
        else PdlLocationType(file=loc.file, path=loc.path, line=first[0], col=first[1])
    )
    return located_message(header_loc, diag.text)


def _unknown_tag_message(
    union: DiscriminatedUnion, data: dict, loc: PdlLocationType
) -> str:
    """The miss branch of a tag lookup.

    Not decoration: `_block_tag` returns `v.get("kind")` verbatim, so without
    this a program saying `kind: totally-made-up` raises `KeyError` out of the
    error reporter itself, which decision 5.8 forbids.
    """
    key_loc = append(loc, union.tag_key)
    diag = unknown_tag_diagnostic(
        written=data.get(union.tag_key),
        key=union.tag_key,
        headline=union.headline,
        rule=union.rule,
        known=union.named,
        line=key_loc.line,
        col=key_loc.col,
        source=source_text(loc.file),
    )
    return located_message(key_loc, diag.text)


def analyze_errors(  # noqa: C901  # pylint: disable=too-many-arguments
    defs,
    schema,
    data,
    loc: PdlLocationType,
    subject: str = "",
    *,
    guessed: bool = False,
) -> list[str]:
    """Every way `data` fails `schema`, one independently-located message each.

    `guessed` records that `schema` was **chosen** for this data rather than
    determined by it -- see `_correction_pool`. Unlike `subject` it propagates
    all the way down: a property's schema is read out of the guessed branch's
    `properties`, so everything below a guess is equally a guess.

    `subject` names what is being validated, for the callers where the field the
    location points at is not it. `pdl_schema_validator` walks a block's
    *result* against the schema a `spec:` declared, with a location whose path
    ends in `spec`; a shape message that read the field name off that location
    would blame the declaration for the value. Everything else leaves it empty
    and the field name is used. It travels only as far as the node it describes:
    a `$ref`, a nullable unwrap and a union branch are the same node and keep
    it, while descending into a property or an element does not, because there
    the field name is the right subject again.

    The return type is the awkward part of rendering block paths here (DROP #10).
    Each element is a whole diagnostic with a location of its own -- E-SCHEMA-010
    produces five, at four different lines -- and `PDLParseError.message` *is*
    this list, which `docs/release-notes.md` documents. So each element carries
    its own `  in <path>` line, as `located_message` builds it, and the list stays
    a list.

    The alternative, one `in` line for the group, was rejected on the same
    evidence: the recursion descends into `append`ed sub-locations, so the paths
    within one call genuinely differ, and a single line would attribute one
    block's path to complaints about several. Joining the elements into one
    string was not available -- the list shape is public.
    """
    ret = []
    if schema == {}:
        return []  # anything matches type Any

    if is_base_type(schema):
        if "type" in schema:
            the_type = json_types_convert[schema["type"]]
            if the_type is None and data is not None or not is_of_type(the_type, data):
                ret.append(
                    located_message(
                        loc, str(data) + " should be of type " + str(the_type)
                    )
                )
        if "enum" in schema:
            if as_validated(data, loc) not in schema["enum"]:
                ret.append(
                    located_message(
                        loc, str(data) + " should be one of: " + str(schema["enum"])
                    )
                )
        if "const" in schema:
            if as_validated(data, loc) != schema["const"]:
                ret.append(
                    located_message(
                        loc, str(data) + " should be: " + str(schema["const"])
                    )
                )

    elif "$ref" in schema:
        ref_string = schema["$ref"].split("/")[2]
        ref_type = defs[ref_string]
        ret += analyze_errors(defs, ref_type, data, loc, subject, guessed=guessed)

    elif is_array(schema):
        if not isinstance(data, list):
            ret.append(not_a_list_message(defs, schema, data, loc, subject))
        else:
            ret += analyze_list(defs, schema, data, loc, subject, guessed=guessed)

    elif is_object(schema):
        if not isinstance(data, dict):
            ret.append(not_a_mapping_message(defs, schema, data, loc, subject))
        else:
            if "required" in schema.keys():
                required_fields = schema["required"]
                for missing in list(set(required_fields) - set(data.keys())):
                    ret.append(
                        located_message(loc, "Missing required field: " + missing)
                    )
            if "properties" in schema.keys():
                all_fields = schema["properties"].keys()
                extras = list(set(data.keys()) - set(all_fields))
                if (
                    "additionalProperties" in schema
                    and schema["additionalProperties"] is False
                ):
                    for field in extras:
                        nloc = append(loc, field)
                        diag = field_not_allowed_diagnostic(
                            written=field,
                            candidates=_correction_pool(
                                defs, all_fields, field, guessed
                            ),
                        )
                        ret.append(located_message(nloc, diag.text))

                valid_fields = list(set(all_fields) & set(data.keys()))
                for field in valid_fields:
                    newloc = append(loc, field)
                    ret += analyze_errors(
                        defs,
                        schema["properties"][field],
                        data[field],
                        newloc,
                        guessed=guessed,
                    )
            if "additionalProperties" in schema.keys() and not isinstance(
                schema["additionalProperties"], bool
            ):
                for key, value in data.items():
                    nloc = append(loc, key)
                    ret += analyze_errors(
                        defs,
                        schema["additionalProperties"],
                        value,
                        nloc,
                        guessed=guessed,
                    )

    elif is_any_of(schema):
        schema_alternatives = alternatives(schema)
        if len(schema_alternatives) == 2 and nullable(schema):
            ret += analyze_errors(
                defs, get_non_null_type(schema), data, loc, subject, guessed=guessed
            )

        elif not isinstance(data, dict) and not isinstance(data, list):
            if not any(
                scalar_matches(defs, item, data, loc) for item in schema_alternatives
            ):
                ret.append(scalar_union_message(defs, schema, data, loc))

        elif isinstance(data, list):
            found = None
            for item in schema_alternatives:
                if is_array(item):
                    found = item
            if found is not None:
                ret += analyze_errors(defs, found, data, loc, subject, guessed=guessed)
            else:
                ret.append(no_array_member_message(defs, schema, data, loc, subject))

        elif isinstance(data, dict):
            union = discriminated_union(defs, schema) or deferred_union(defs, schema)
            if union is not None:
                return ret + analyze_discriminated(
                    defs, union, data, loc, guessed=guessed
                )

            match_ref = {}
            highest_match = 0
            for item in object_alternatives(defs, schema):
                field_matches = match(item, data)
                if field_matches > highest_match:
                    highest_match = field_matches
                    match_ref = item

            if match_ref == {}:
                ret.append(
                    located_message(
                        loc, str(data) + " should be of type: " + str(schema)
                    )
                )

            else:
                # `guessed=True`, and it is not an accident of this call: nothing
                # in the data *selected* `match_ref`, a field count did, and the
                # count is frequently a tie. See `_correction_pool`.
                ret += analyze_errors(defs, match_ref, data, loc, subject, guessed=True)
    return ret
