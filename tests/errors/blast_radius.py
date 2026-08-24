#!/usr/bin/env python
"""Measure the blast radius of the §5.5 semantic changes, in one pass.

§5.5 of `docs/error-reporting/INVENTORY.md` authorises three changes that make
previously-exit-0 programs exit 1. Each needs a count of how much in-tree code
it breaks. That count has been wrong three times, each time understating the
scope, because each correction was a fresh ad-hoc grep with a slightly different
idea of what to look at:

    205 .pdl files   ->  263  (`--include=*.pdl` missed files, and missed
                               PDL programs embedded in .py sources)
    263 .pdl files   ->  265  (the corpus itself grew)
    "no `for:` block binds a string literal"  ->  false; one does

So the fix is not another number in the prose. It is this file. Run it and paste
what it prints; re-run it whenever §5.5 is revisited, rather than re-deriving the
figures by hand.

    python tests/errors/blast_radius.py

WHAT IT CANNOT SEE. This is a static scan: it loads each program as YAML and
inspects literal values. It cannot evaluate `${ ... }`, so a binding whose
expression yields a string at runtime is invisible to it, and it reports the
count of expression-valued bindings separately for exactly that reason. Treat
its `for:` figures as a lower bound on affected sites, and the interpreter's own
semantic sweep as the authority.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
EXPR = re.compile(r"\$\{")

# PDL programs also live inside .py sources as string literals; a `*.pdl` glob
# misses them, which is how the 205 figure went wrong.
PY_KEYS = ("parser: csv", "parser: yaml", "parser: json")


class _DupDetectingLoader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """SafeLoader that records duplicate mapping keys instead of dropping them."""


def _install_dup_detector(sink: list[tuple[int, list[Any]]]) -> None:
    def construct_mapping(loader, node, deep=False):  # type: ignore[no-untyped-def]
        keys = [loader.construct_object(k, deep=True) for k, _ in node.value]
        repeated = [k for k, n in Counter(keys).items() if n > 1]
        if repeated:
            sink.append((node.start_mark.line + 1, repeated))
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    _DupDetectingLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )


def _rel(path: Path) -> Path:
    return path.relative_to(REPO)


@dataclass
class Census:
    """Every `.pdl` file, whether it parses, and its duplicate-key sites."""

    parsed: list[tuple[Path, Any]] = field(default_factory=list)
    unparseable: list[Path] = field(default_factory=list)
    dup_sites: list[tuple[Path, int, list[Any]]] = field(default_factory=list)
    total: int = 0


@dataclass
class ForStats:
    """Every `for:` binding in the corpus, split by what it binds."""

    bindings: int = 0
    expression_valued: int = 0
    list_valued: int = 0
    literal_text: list[tuple[Path, str, str]] = field(default_factory=list)
    literal_other: list[tuple[Path, str, str]] = field(default_factory=list)


def _take_census() -> Census:
    census = Census()
    paths = sorted(REPO.rglob("*.pdl"))
    census.total = len(paths)
    for path in paths:
        sink: list[tuple[int, list[Any]]] = []
        _install_dup_detector(sink)
        try:
            data = yaml.load(
                path.read_text(encoding="utf-8"), Loader=_DupDetectingLoader
            )
        except Exception:  # pylint: disable=broad-except
            census.unparseable.append(path)
            continue
        census.parsed.append((path, data))
        for lineno, keys in sink:
            census.dup_sites.append((path, lineno, keys))
    return census


def _walk_for_bindings(node: Any, out: list[tuple[str, Any]]) -> None:
    """Collect every `for:` binding's name and literal value."""
    if isinstance(node, dict):
        for_field = node.get("for")
        if isinstance(for_field, dict):
            for name, value in for_field.items():
                out.append((str(name), value))
        for value in node.values():
            _walk_for_bindings(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_for_bindings(value, out)


def _for_stats(parsed: list[tuple[Path, Any]]) -> ForStats:
    stats = ForStats()
    for path, data in parsed:
        found: list[tuple[str, Any]] = []
        _walk_for_bindings(data, found)
        for name, value in found:
            stats.bindings += 1
            if isinstance(value, str) and EXPR.search(value):
                stats.expression_valued += 1
            elif isinstance(value, (str, bytes)):
                stats.literal_text.append((path, name, repr(value)[:60]))
            elif isinstance(value, list):
                stats.list_valued += 1
            else:
                stats.literal_other.append((path, name, type(value).__name__))
    return stats


def _count_parser_csv(node: Any) -> int:
    total = 0
    if isinstance(node, dict):
        if node.get("parser") == "csv":
            total += 1
        for value in node.values():
            total += _count_parser_csv(value)
    elif isinstance(node, list):
        for value in node:
            total += _count_parser_csv(value)
    return total


def _py_hits() -> tuple[list[str], int]:
    """Embedded PDL programs in .py sources, and implementation hits separately.

    Hits inside `src/pdl/` are the implementation handling these keys, not PDL
    programs; counting them together produced a number that looked alarming and
    meant nothing.
    """
    hits: list[str] = []
    impl = 0
    for py in sorted(REPO.rglob("*.py")):
        if py.name == Path(__file__).name:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:  # pylint: disable=broad-except
            continue
        is_impl = "src/pdl/" in py.as_posix()
        for num, source_line in enumerate(text.splitlines(), 1):
            if any(key in source_line for key in PY_KEYS):
                if is_impl:
                    impl += 1
                else:
                    hits.append(f"{_rel(py)}:{num}")
    return hits, impl


def _print_census(census: Census) -> None:
    print(
        f"census: {census.total} .pdl files "
        f"({len(census.parsed)} parse, {len(census.unparseable)} do not)"
    )
    print("  files that do not parse are corpus reproducers for parse errors:")
    for path in census.unparseable:
        print(f"    {_rel(path)}")

    print(f"\nduplicate mapping keys: {len(census.dup_sites)} site(s)")
    for path, lineno, keys in census.dup_sites:
        print(f"    {_rel(path)}:{lineno} -> {keys}")


def _print_for_stats(stats: ForStats, programs: int) -> None:
    print(f"\n`for:` bindings: {stats.bindings} across {programs} programs")
    print(
        f"    {stats.expression_valued} bind a ${{ ... }} expression "
        f"(runtime value; not visible here)"
    )
    print(f"    {stats.list_valued} bind a literal list")
    print(
        f"    {len(stats.literal_text)} bind a literal string or bytes"
        f"  <- rejected by 5.5"
    )
    for path, name, value in stats.literal_text:
        print(f"        {_rel(path)}  {name}: {value}")
    print(f"    {len(stats.literal_other)} bind some other literal")
    for path, name, kind in stats.literal_other:
        print(f"        {_rel(path)}  {name}: <{kind}>")


def main() -> int:
    census = _take_census()
    _print_census(census)
    _print_for_stats(_for_stats(census.parsed), len(census.parsed))

    csv_sites = sum(_count_parser_csv(d) for _, d in census.parsed)
    hits, impl = _py_hits()
    print(f"\n`parser: csv`: {csv_sites} site(s) in .pdl files")
    print(f"PDL programs embedded in .py test sources: {len(hits)}")
    for hit in hits:
        print(f"    {hit}")
    print(f"    ({impl} further hits in src/pdl/ are the implementation, not programs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
