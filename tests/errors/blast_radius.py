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
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
EXPR = re.compile(r"\$\{")

# PDL programs also live inside .py sources as string literals; a `*.pdl` glob
# misses them, which is how the 205 figure went wrong.
PY_KEYS = ("parser: csv", "parser: yaml", "parser: json")


class _DupDetectingLoader(yaml.SafeLoader):
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


def main() -> int:
    pdl_files = sorted(REPO.rglob("*.pdl"))
    parsed: list[tuple[Path, Any]] = []
    unparseable: list[Path] = []
    dup_sites: list[tuple[Path, int, list[Any]]] = []

    for path in pdl_files:
        sink: list[tuple[int, list[Any]]] = []
        _install_dup_detector(sink)
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=_DupDetectingLoader)
        except Exception:  # pylint: disable=broad-except
            unparseable.append(path)
            continue
        parsed.append((path, data))
        for line, keys in sink:
            dup_sites.append((path, line, keys))

    literal_str: list[tuple[Path, str, str]] = []
    literal_other: list[tuple[Path, str, str]] = []
    expression_valued = 0
    list_valued = 0
    bindings = 0

    for path, data in parsed:
        found: list[tuple[str, Any]] = []
        _walk_for_bindings(data, found)
        for name, value in found:
            bindings += 1
            if isinstance(value, str) and EXPR.search(value):
                expression_valued += 1
            elif isinstance(value, (str, bytes)):
                literal_str.append((path, name, repr(value)[:60]))
            elif isinstance(value, list):
                list_valued += 1
            else:
                literal_other.append((path, name, type(value).__name__))

    csv_sites = sum(_count_parser_csv(d) for _, d in parsed)

    # Hits inside `src/pdl/` are the implementation handling these keys, not PDL
    # programs; counting them together with embedded test programs produced a
    # number that looked alarming and meant nothing. Split them.
    py_hits: list[str] = []
    impl_hits = 0
    for py in sorted(REPO.rglob("*.py")):
        if py.name == Path(__file__).name:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:  # pylint: disable=broad-except
            continue
        is_impl = "src/pdl/" in py.as_posix()
        for num, line in enumerate(text.splitlines(), 1):
            if any(key in line for key in PY_KEYS):
                if is_impl:
                    impl_hits += 1
                else:
                    py_hits.append(f"{py.relative_to(REPO)}:{num}")

    rel = lambda p: p.relative_to(REPO)  # noqa: E731

    print(f"census: {len(pdl_files)} .pdl files "
          f"({len(parsed)} parse, {len(unparseable)} do not)")
    print("  files that do not parse are corpus reproducers for parse errors:")
    for path in unparseable:
        print(f"    {rel(path)}")

    print(f"\nduplicate mapping keys: {len(dup_sites)} site(s)")
    for path, line, keys in dup_sites:
        print(f"    {rel(path)}:{line} -> {keys}")

    print(f"\n`for:` bindings: {bindings} across {len(parsed)} programs")
    print(f"    {expression_valued} bind a ${{ ... }} expression (runtime value; not visible here)")
    print(f"    {list_valued} bind a literal list")
    print(f"    {len(literal_str)} bind a literal string or bytes  <- rejected by 5.5")
    for path, name, value in literal_str:
        print(f"        {rel(path)}  {name}: {value}")
    print(f"    {len(literal_other)} bind some other literal")
    for path, name, kind in literal_other:
        print(f"        {rel(path)}  {name}: <{kind}>")

    print(f"\n`parser: csv`: {csv_sites} site(s) in .pdl files")
    print(f"PDL programs embedded in .py test sources: {len(py_hits)}")
    for hit in py_hits:
        print(f"    {hit}")
    print(f"    ({impl_hits} further hits in src/pdl/ are the implementation, not programs)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
