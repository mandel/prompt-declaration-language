"""Execution and normalization for the PDL error-reporting corpus.

Every corpus entry is a directory under ``corpus/`` named after a taxonomy ID
from ``docs/error-reporting/INVENTORY.md``. It contains

``case.json``
    How to run the reproducer, what exit code to expect, and the rubric scores
    for the diagnostic as it stands today (see ``docs/error-reporting/RUBRIC.md``).
``prog.pdl``
    The reproducer. Any sibling files in the directory are copied alongside it,
    which is how ``include``/``import`` cases get their second file.
``expected.txt``
    The golden. A normalized transcript of exit code, stdout and stderr.

The run is deliberately hostile to nondeterminism: a fixed hash seed, a fixed
``TZ``, a scrubbed environment, a private working directory, and no network. See
``sitecustomize_stub/sitecustomize.py`` for the offline enforcement.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HARNESS_DIR = Path(__file__).parent
CORPUS_DIR = HARNESS_DIR / "corpus"
STUB_DIR = HARNESS_DIR / "sitecustomize_stub"
REPO_ROOT = HARNESS_DIR.parent.parent

TIMEOUT_SECONDS = 120

# Rubric dimensions, in the order they are reported. See RUBRIC.md.
RUBRIC_DIMENSIONS = ("location", "what", "why", "fix", "hygiene")


@dataclass(frozen=True)
class Case:  # pylint: disable=too-many-instance-attributes
    """A single corpus entry. A data container, so the field count is the point."""

    id: str
    directory: Path
    title: str
    entry: str = "pdl"
    argv: tuple[str, ...] = ("--stream", "none", "prog.pdl")
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    """Subdirectory of the work directory to run in. Needed by entries that turn
    on where the tool thinks the project root is (see ``E-LINT-004``)."""
    expect_exit: int | None = 1
    severity: str = ""
    rubric: dict[str, int] = field(default_factory=dict)
    notes: str = ""
    skip: str = ""

    @property
    def golden_path(self) -> Path:
        return self.directory / "expected.txt"

    @property
    def rubric_total(self) -> int:
        return sum(self.rubric.get(d, 0) for d in RUBRIC_DIMENSIONS)


def load_cases() -> list[Case]:
    """Load every corpus entry, ordered by ID."""
    cases = []
    for case_file in sorted(CORPUS_DIR.glob("*/case.json")):
        spec: dict[str, Any] = json.loads(case_file.read_text(encoding="utf-8"))
        directory = case_file.parent
        cases.append(
            Case(
                id=spec.get("id", directory.name),
                directory=directory,
                title=spec["title"],
                entry=spec.get("entry", "pdl"),
                argv=tuple(spec.get("argv", ["--stream", "none", "prog.pdl"])),
                env=spec.get("env", {}),
                cwd=spec.get("cwd", ""),
                expect_exit=spec.get("expect_exit", 1),
                severity=spec.get("severity", ""),
                rubric=spec.get("rubric", {}),
                notes=spec.get("notes", ""),
                skip=spec.get("skip", ""),
            )
        )
    return cases


def _entry_point(entry: str) -> list[str]:
    """Resolve a console-script name to an argv prefix.

    The installed console script is used rather than ``python -m``, because that
    is what a user runs and the two do not agree: ``src/pdl/pdl.py`` ends in a
    bare ``main()`` with no ``sys.exit``, so ``python -m pdl.pdl`` reports
    success even when the program failed. Corpus entry ``E-CLI-005`` pins that
    discrepancy; everything else must not inherit it.
    """
    match entry:
        case "pdl" | "pdl-lint":
            script = Path(sys.executable).parent / entry
            if script.exists():
                return [str(script)]
            resolved = shutil.which(entry)
            if resolved is None:
                raise RuntimeError(
                    f"console script {entry!r} not found; "
                    "install the package with `pip install -e .` first"
                )
            return [resolved]
        case "python-m-pdl":
            return [sys.executable, "-m", "pdl.pdl"]
        case _:
            raise ValueError(f"unknown corpus entry point: {entry!r}")


def build_env(case: Case, hash_seed: str = "0") -> dict[str, str]:
    """A minimal, reproducible environment for one run.

    Everything that could reach the network or vary between machines is either
    dropped or pinned. ``PYTHONPATH`` points at the stub directory so that
    ``sitecustomize`` is imported before any PDL code runs.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONPATH": str(STUB_DIR),
        "PYTHONHASHSEED": hash_seed,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Keep provider SDKs from finding real credentials even if the guard
        # were bypassed; also keeps litellm quiet and deterministic.
        "LITELLM_LOG": "ERROR",
        "NO_COLOR": "1",
        "PDL_ERROR_CORPUS": "1",
    }
    env.update(case.env)
    return env


def run_case(case: Case, workdir: Path, hash_seed: str = "0") -> str:
    """Run one corpus entry in ``workdir`` and return its normalized transcript."""
    for src in sorted(case.directory.iterdir()):
        if src.name in ("case.json", "expected.txt"):
            continue
        if src.is_dir():
            shutil.copytree(src, workdir / src.name)
        else:
            shutil.copy2(src, workdir / src.name)

    rundir = workdir / case.cwd if case.cwd else workdir
    # `{WORKDIR}` lets a case name an absolute path without hard-coding one.
    # E-LINT-004 needs it: the linter's "outside the project root" branch is
    # only reachable via an absolute path, because `Path.absolute()` does not
    # resolve `..` and so every relative path looks like it is inside the root.
    resolved_argv = [
        arg.replace("{WORKDIR}", str(workdir.resolve())) for arg in case.argv
    ]
    argv = _entry_point(case.entry) + resolved_argv
    try:
        proc = subprocess.run(  # nosec B603
            argv,
            cwd=rundir,
            env=build_env(case, hash_seed),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        exit_code: int | str = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = f"TIMEOUT after {TIMEOUT_SECONDS}s"
        stdout = exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        stderr = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""

    return transcript(exit_code, stdout, stderr, workdir)


def transcript(exit_code: int | str, stdout: str, stderr: str, workdir: Path) -> str:
    """Render exit code, stdout and stderr as one normalized golden document."""
    sections = [
        f"$ exit: {exit_code}",
        "",
        "--- stdout ---",
        normalize(stdout, workdir),
        "--- stderr ---",
        normalize(stderr, workdir),
    ]
    return "\n".join(sections).rstrip() + "\n"


_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Python object addresses.
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "0xADDR"),
    # Durations and timings, in whatever unit they appear.
    (re.compile(r"\b\d+\.\d+(s|ms|us|ns)\b"), "<DURATION>"),
    (re.compile(r"\b\d{10,}\b"), "<NANOS>"),
    # Temporary directories that escaped the workdir substitution.
    (re.compile(r"/tmp/[A-Za-z0-9_./-]+"), "<TMP>"),
    # LiteLLM appends a provider-documentation paragraph whose wording drifts
    # between releases; the first line carries the actual diagnosis.
    (
        re.compile(r"\n ?Pass model as E\.g\..*?(?=\n\S|\n*\Z)", re.DOTALL),
        "\n<LITELLM PROVIDER HELP>",
    ),
    # Traceback frames pointing into the harness's own model stub. The stub is
    # test scaffolding, not part of the UX under measurement, so editing it must
    # not churn goldens. Line numbers in *PDL's* frames are deliberately kept:
    # they are evidence about where a diagnostic escaped.
    (
        re.compile(r'(File "<REPO>/tests/errors/sitecustomize_stub/[^"]+", line )\d+'),
        r"\1<STUBLINE>",
    ),
)


def normalize(text: str, workdir: Path) -> str:
    """Strip everything machine-, path- or clock-specific from captured output.

    Tracebacks are normalized but *not* removed. A traceback in a golden file is
    the visible symptom of an S0 entry, and it has to stay visible so that the
    diff in the commit that fixes it shows the traceback disappearing.
    """
    if not text:
        return "(empty)\n"

    # Longest paths first so that nested prefixes do not shadow each other.
    replacements = [
        (str(workdir.resolve()), "<WORKDIR>"),
        (str(workdir), "<WORKDIR>"),
        (str(Path(sys.prefix).resolve()), "<VENV>"),
        (str(REPO_ROOT.resolve()), "<REPO>"),
    ]
    for path in sorted({p for p, _ in replacements}, key=len, reverse=True):
        token = next(t for p, t in replacements if p == path)
        text = text.replace(path, token)

    # site-packages and stdlib live under the tokens substituted above, but a
    # differently-rooted interpreter (system python, tox) needs its own pass.
    text = re.sub(r"/[^\s\"']*/site-packages", "<SITE>", text)
    text = re.sub(r"/usr/lib/python3\.\d+", "<PYLIB>", text)
    text = re.sub(r"python3\.\d+", "python3.X", text)

    for pattern, token in _SUBSTITUTIONS:
        text = pattern.sub(token, text)

    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    "During handling of the above exception",
    "The above exception was the direct cause",
)


def has_traceback(text: str) -> bool:
    """Whether a transcript exposes a Python traceback to the user.

    This backs the project-wide invariant from decision 5.8: every failure must
    be a formatted diagnostic, never a crash.
    """
    return any(marker in text for marker in TRACEBACK_MARKERS)
