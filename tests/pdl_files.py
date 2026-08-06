"""Shared discovery of the `.pdl` files that repo-wide sweeps should cover.

Several tests walk every `.pdl` file in the tree and assert something that holds
for well-formed programs -- that it parses, that the AST iterators reach every
child, that dumping round-trips. Those sweeps need to skip directories whose
whole purpose is to hold broken programs, or every new fixture breaks them.
"""

from __future__ import annotations

import pathlib

EXCLUDED_DIRS = (
    # Deliberately-invalid reproducers backing the error-reporting corpus. Each
    # one is pinned by a golden transcript in tests/errors/; see
    # tests/errors/README.md.
    pathlib.Path("tests")
    / "errors"
    / "corpus",
)


def is_excluded(path: pathlib.Path) -> bool:
    """Whether `path` lives in a directory reserved for invalid programs."""
    return any(excluded in path.parents for excluded in EXCLUDED_DIRS)


def all_pdl_files(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Every `.pdl` file a repo-wide sweep should look at."""
    base = root if root is not None else pathlib.Path(".")
    return [path for path in base.glob("**/*.pdl") if not is_excluded(path)]
