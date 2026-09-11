"""The discriminator tables in `pdl_schema_error_analyzer` cannot silently rot.

Decision 5.3 has the error analyzer answer "which branch of this union did the
user mean?" with the same discriminator pydantic uses, through a hand-written
tag -> `$def` table. The table is the one hand-written thing in that file and
the one thing that can drift away from `pdl_ast` without anything failing, so it
is pinned here from both ends: against the generated schema, and against the
`Tag(...)` order in the union definitions themselves.

A failure here is not a bug in this file. It means a block kind, a model
platform, a code language or a block-selecting field was added to `pdl_ast` and
the analyzer does not know about it, which would send the reader of a
diagnostic to the wrong branch or, for a tag with no table entry, to the miss
message.
"""

import json
from pathlib import Path

import pytest

import pdl.pdl
from pdl.pdl_ast import (  # noqa: PLC2701
    _BLOCK_KIND_OF_FIELD,
    BlockKind,
    _block_tag,
    _code_block_tag,
    _model_block_tag,
    empty_block_location,
)
from pdl.pdl_schema_error_analyzer import (
    BLOCK_KIND_FIELDS,
    BLOCK_TAG_DEFS,
    CODE_TAG_DEFS,
    DISCRIMINATED_UNIONS,
    LOWERCASED_FIELDS,
    MODEL_TAG_DEFS,
    analyze_errors,
    discriminated_union,
    near_miss_pool,
)

DEFS = json.loads(
    (Path(pdl.pdl.__file__).parent / "pdl-schema.json").read_text(encoding="utf-8")
)["$defs"]

TABLES = {
    "BlockType": BLOCK_TAG_DEFS,
    "ModelBlockType": MODEL_TAG_DEFS,
    "CodeBlockType": CODE_TAG_DEFS,
}


@pytest.mark.parametrize("union", sorted(TABLES))
def test_table_matches_the_union_exactly(union):
    """Same members, same order, as the `oneOf` the schema generates."""
    refs = [item["$ref"].split("/")[-1] for item in DEFS[union]["oneOf"]]
    assert list(TABLES[union].values()) == refs


@pytest.mark.parametrize("union", sorted(TABLES))
def test_table_names_only_real_defs(union):
    assert set(TABLES[union].values()) <= set(DEFS)


@pytest.mark.parametrize("union", sorted(TABLES))
def test_tags_are_plain_strings(union):
    """`analyze_discriminated` looks the tag up in a dict, so an unhashable or
    non-string tag must never reach it. The guard is `isinstance(tag, str)`, and
    it only works if every table key really is one."""
    for tag in TABLES[union]:
        assert isinstance(tag, str)


def test_every_union_in_the_dispatch_is_recognised_by_identity():
    """`discriminated_union` matches on `is`, which holds only because
    `analyze_errors` hands `defs[name]` straight on and never copies it."""
    for union in DISCRIMINATED_UNIONS:
        assert discriminated_union(DEFS, DEFS[union.name]) is union
    assert discriminated_union(DEFS, DEFS["ContributeElement"]) is None
    assert discriminated_union(DEFS, dict(DEFS["BlockType"])) is None


def test_named_tags_are_table_entries():
    """The rule paragraph may leave a tag out -- `args` is not a language -- but
    it may never name one the union does not have."""
    for union in DISCRIMINATED_UNIONS:
        assert set(union.named) <= set(union.table)


def test_every_selector_field_reaches_a_table_entry():
    """`_block_tag` answers with a `BlockKind` for each of these, and every one
    of those answers has to be a branch the analyzer can descend into."""
    for field, _ in _BLOCK_KIND_OF_FIELD:
        tag = _block_tag({field: "x"})
        assert tag in BLOCK_TAG_DEFS, f"`{field}:` selects {tag!r}, which has no $def"


def test_block_kind_fields_cover_pdl_ast():
    """The 24 names the E-SCHEMA-007 rule paragraph lists.

    `program` is excluded because it selects `ErrorBlock`, which the interpreter
    builds around a failed block and nobody writes by hand. Everything else in
    `_BLOCK_KIND_OF_FIELD` is a word a user can type and must therefore appear.
    """
    expected = {field for field, _ in _BLOCK_KIND_OF_FIELD} - {"program"}
    assert set(BLOCK_KIND_FIELDS) == expected
    assert len(BLOCK_KIND_FIELDS) == len(set(BLOCK_KIND_FIELDS))
    # A field added to `pdl_ast` and not to the preference order still appears;
    # it lands at the end rather than vanishing from the message.
    assert BLOCK_KIND_FIELDS[0] == "model"


def test_empty_tag_is_not_a_no_match_signal():
    """The trap the E-SCHEMA-007 implementation has to avoid.

    `_block_tag` gives the same answer for a mapping that is not a block and for
    one that is a perfectly good empty block, so the tag cannot be read as
    "nothing matched" and the keys have to be.
    """
    assert _block_tag({"foo": "bar"}) is BlockKind.EMPTY
    assert _block_tag({"description": "d"}) is BlockKind.EMPTY
    assert (
        analyze_errors(
            DEFS, DEFS["BlockType"], {"description": "d"}, empty_block_location
        )
        == []
    )


def test_arbitrary_tags_do_not_raise():
    """`_block_tag` returns `v.get("kind")` verbatim, so the tag is whatever the
    user typed. A table lookup with no miss branch would be a `KeyError` reaching
    the user as a traceback, which decision 5.8 forbids outright."""
    assert _block_tag({"kind": "totally-made-up"}) == "totally-made-up"
    for data in (
        {"kind": "totally-made-up"},
        {"kind": 5},
        {"kind": ["a"]},
        {"kind": {"a": 1}},
        {"code": "x", "lang": "ruby"},
        {"code": "x", "lang": ["a"]},
        {"model": "m", "platform": "bedrock"},
        {"model": "m", "platform": {"a": 1}},
    ):
        errors = analyze_errors(DEFS, DEFS["BlockType"], data, empty_block_location)
        assert len(errors) == 1, data
    assert _code_block_tag({"code": "x", "lang": "RUBY"}) == "ruby"
    assert _model_block_tag({"model": "m", "platform": "BEDROCK"}) == "bedrock"


def test_near_miss_pool_is_ordered_and_excludes_bookkeeping():
    pool = near_miss_pool(DEFS)
    assert pool == sorted(pool)
    assert not [name for name in pool if name.startswith("pdl__")]
    assert "kind" not in pool, "written by the dumper, not by hand"
    assert "description" in pool and "text" in pool


@pytest.mark.parametrize(
    "data",
    [
        {"text": "a", "parser": "JSON", "bogus": 1},
        {"code": "x", "lang": "PYTHON", "bogus": 1},
        {"text": "a", "parser": {"regex": "x", "mode": "SEARCH"}, "bogus": 1},
    ],
)
def test_lowercased_fields_are_not_reported(data):
    """PDL lower-cases these before validating (`BeforeValidator(_ensure_lower)`),
    and JSON Schema cannot say so. The analyzer must not contradict the
    validator: each of these programs is rejected for `bogus`, and for nothing
    else."""
    errors = analyze_errors(DEFS, DEFS["BlockType"], data, empty_block_location)
    assert len(errors) == 1 and "bogus" in errors[0], errors


def test_lowercasing_is_not_applied_to_every_enum():
    """`platform:` has no such validator, so `LITELLM` really is wrong and the
    blanket case-insensitive comparison that would silence it is not taken."""
    assert "platform" not in LOWERCASED_FIELDS
    errors = analyze_errors(
        DEFS,
        DEFS["BlockType"],
        {"model": "m", "platform": "LITELLM"},
        empty_block_location,
    )
    assert any("litellm" in e for e in errors), errors
