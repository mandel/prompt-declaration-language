"""Golden tests for PDL diagnostics.

Each corpus entry is run in a private working directory with a scrubbed,
offline environment and its transcript is diffed against ``expected.txt``.

Regenerate goldens after an intentional diagnostic change::

    python tests/errors/regen.py            # every entry
    python tests/errors/regen.py E-PARSE    # entries whose ID starts with this

Then read the diff. A golden diff is the point of this suite: it is the record
of the UX delta, and it belongs in the same commit as the code change.
"""

from __future__ import annotations

import pytest

from .harness import Case, has_traceback, load_cases, run_case

CASES = load_cases()
CASE_IDS = [c.id for c in CASES]


def _run(case: Case, tmp_path, hash_seed: str = "0") -> str:
    if case.skip:
        pytest.skip(case.skip)
    return run_case(case, tmp_path, hash_seed=hash_seed)


def test_corpus_is_not_empty():
    assert CASES, "no corpus entries found under tests/errors/corpus/"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_golden(case: Case, tmp_path):
    """The diagnostic matches its recorded golden transcript."""
    actual = _run(case, tmp_path)
    if not case.golden_path.exists():
        pytest.fail(
            f"{case.id}: no golden yet. Run `python tests/errors/regen.py {case.id}`"
            f" and review the result.\n\n{actual}"
        )
    expected = case.golden_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{case.id} diagnostic changed.\n"
        f"If this is intentional, run `python tests/errors/regen.py {case.id}`"
        f" and commit the golden alongside the code change."
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_exit_code(case: Case, tmp_path):
    """The entry exits with the code its case declares."""
    if case.expect_exit is None:
        pytest.skip("case does not pin an exit code")
    actual = _run(case, tmp_path)
    assert f"$ exit: {case.expect_exit}\n" in actual


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_traceback(case: Case, tmp_path):
    """No diagnostic exposes a Python traceback to the user.

    This is the project-wide invariant from decision 5.8, and it is the
    acceptance test for every S0 entry in the taxonomy. It fails loudly at
    baseline -- that is intended. Entries known to violate it are marked
    ``xfail`` through their rubric hygiene score of 0 plus an explicit
    ``expect_traceback`` note, so that fixing one turns the xfail into an
    XPASS and forces the marker to be removed.
    """
    actual = _run(case, tmp_path)
    leaked = has_traceback(actual)
    if case.rubric.get("hygiene_traceback_expected"):
        if not leaked:
            pytest.fail(
                f"{case.id} no longer leaks a traceback. Remove "
                f'"hygiene_traceback_expected" from its case.json.'
            )
        pytest.xfail(f"{case.id} is a known traceback leak (see INVENTORY.md)")
    assert not leaked, f"{case.id} exposed a Python traceback to the user:\n\n{actual}"


# Entries whose message text is assembled from unordered set operations. Pinned
# separately because the harness fixes PYTHONHASHSEED to keep goldens stable,
# which would otherwise hide the instability from this suite entirely.
_ORDER_SENSITIVE = [c for c in CASES if c.rubric.get("hygiene_unstable_order")]


@pytest.mark.parametrize("case", _ORDER_SENSITIVE, ids=[c.id for c in _ORDER_SENSITIVE])
def test_output_is_hash_seed_independent(case: Case, tmp_path):
    """Diagnostics do not reorder when Python's hash seed changes.

    ``analyze_errors`` builds its message list by iterating ``set`` differences,
    so a program with several schema faults reports them in an order that varies
    between processes. Known-failing at baseline.
    """
    pytest.xfail(
        f"{case.id} reorders under a different hash seed "
        "(analyze_errors iterates set differences)"
    )


@pytest.mark.parametrize("case", _ORDER_SENSITIVE, ids=[c.id for c in _ORDER_SENSITIVE])
def test_order_instability_is_real(case: Case, tmp_path):
    """Proves the instability above is real rather than a stale annotation."""
    seen = set()
    for seed in range(6):
        rundir = tmp_path / f"seed{seed}"
        rundir.mkdir()
        seen.add(_run(case, rundir, hash_seed=str(seed)))
    assert len(seen) > 1, (
        f"{case.id} is annotated hygiene_unstable_order but produced identical "
        "output across six hash seeds; drop the annotation."
    )
