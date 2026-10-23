#!/usr/bin/env python3
"""Regenerate golden transcripts for the error corpus.

    python tests/errors/regen.py             # every entry
    python tests/errors/regen.py E-PARSE     # entries whose ID starts with this

Always read the resulting diff before committing. The golden diff is the record
of the UX change and belongs in the same commit as the code that caused it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.errors.harness import load_cases, run_case
else:  # pragma: no cover - exercised only when imported as a module
    from .harness import load_cases, run_case


def main(argv: list[str]) -> int:
    prefixes = argv or [""]
    cases = [c for c in load_cases() if any(c.id.startswith(p) for p in prefixes)]
    if not cases:
        print(f"no corpus entries match {prefixes}", file=sys.stderr)
        return 1

    changed, created, skipped = 0, 0, 0
    for case in cases:
        if case.skip:
            print(f"  skip    {case.id}: {case.skip}")
            skipped += 1
            continue
        with tempfile.TemporaryDirectory() as tmp:
            actual = run_case(case, Path(tmp))
        if not case.golden_path.exists():
            case.golden_path.write_text(actual, encoding="utf-8")
            print(f"  created {case.id}")
            created += 1
        elif case.golden_path.read_text(encoding="utf-8") != actual:
            case.golden_path.write_text(actual, encoding="utf-8")
            print(f"  updated {case.id}")
            changed += 1
        else:
            print(f"  ok      {case.id}")

    print(
        f"\n{len(cases)} entries: {created} created, {changed} updated, "
        f"{skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
