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
    no_block_kind_diagnostic,
    prefer,
    scalar_value_diagnostic,
    unknown_tag_diagnostic,
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


def is_array(schema):
    if "type" in schema:
        return schema["type"] == "array"
    return False


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


def scalar_union_message(defs, schema, data, loc: PdlLocationType) -> str:
    """The message for a scalar that matched no member of its union.

    Falls back to the old `should be of type <schema>` dump when the union has
    no enumerated values, because then there is no list of accepted spellings to
    offer and naming the field alone would be less, not more, than the schema.
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


def analyze_discriminated(
    defs, union: DiscriminatedUnion, data: dict, loc: PdlLocationType
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
                defs, defs["EmptyBlock"], kept, loc
            )
        return analyze_errors(defs, defs["EmptyBlock"], data, loc)

    ref_string = union.table.get(tag) if isinstance(tag, str) else None
    if ref_string is None:
        return [_unknown_tag_message(union, data, loc)]
    return analyze_errors(defs, defs[ref_string], data, loc)


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
    first = next(((line, col) for _, line, col in keys if line is not None), None)
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


def analyze_errors(defs, schema, data, loc: PdlLocationType) -> list[str]:  # noqa: C901
    """Every way `data` fails `schema`, one independently-located message each.

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
            if (
                the_type is None
                and data is not None
                or not isinstance(data, the_type)  # type: ignore
            ):
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
        ret += analyze_errors(defs, ref_type, data, loc)

    elif is_array(schema):
        if not isinstance(data, list):
            ret.append(located_message(loc, str(data) + " should be a list"))
        else:
            for i, item in enumerate(data):
                newloc = append(loc, "[" + str(i) + "]")
                ret += analyze_errors(defs, schema["items"], item, newloc)

    elif is_object(schema):
        if not isinstance(data, dict):
            ret.append(located_message(loc, str(data) + " should be an object"))
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
                        ret.append(located_message(nloc, "Field not allowed: " + field))

                valid_fields = list(set(all_fields) & set(data.keys()))
                for field in valid_fields:
                    newloc = append(loc, field)
                    ret += analyze_errors(
                        defs, schema["properties"][field], data[field], newloc
                    )
            if "additionalProperties" in schema.keys() and not isinstance(
                schema["additionalProperties"], bool
            ):
                for key, value in data.items():
                    nloc = append(loc, key)
                    ret += analyze_errors(
                        defs, schema["additionalProperties"], value, nloc
                    )

    elif is_any_of(schema):
        schema_alternatives = alternatives(schema)
        if len(schema_alternatives) == 2 and nullable(schema):
            ret += analyze_errors(defs, get_non_null_type(schema), data, loc)

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
                ret += analyze_errors(defs, found, data, loc)
            else:
                ret.append(located_message(loc, str(data) + " should not be a list"))

        elif isinstance(data, dict):
            union = discriminated_union(defs, schema) or deferred_union(defs, schema)
            if union is not None:
                return ret + analyze_discriminated(defs, union, data, loc)

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
                ret += analyze_errors(defs, match_ref, data, loc)
    return ret
