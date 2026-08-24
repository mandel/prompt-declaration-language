"""
A tool to lint PDL (Prompt Declaration Language) files.

This linter is designed to help projects with multiple PDL files detect errors at build time.

Configuration:
-------------
The linter can be configured through either `pyproject.toml` or `.pdl-lint` file in your project root.
The `pyproject.toml` configuration takes precedence over `.pdl-lint`.

Example configuration in pyproject.toml:
-------------------------------------
[tool.pdl-lint]
# List of paths to ignore (relative to project root)
ignore = ["tests/", "docs/", "examples/example.pdl"]

# Logging configuration
log_file = "pdl-lint.log"  # Path to log file (optional)
file_log_level = "DEBUG"   # Log level for file: CRITICAL, FATAL, ERROR, WARNING, WARN, INFO, DEBUG, NOTSET
file_log_format = "%(asctime)s %(name)s: %(message)s"  # Format for file logging

# Console logging configuration
console_log_enabled = true  # Whether to log to console
console_log_level = "INFO"  # Log level for console
console_log_format = "%(message)s"  # Format for console logging

# Debug mode
debug = false  # Enable debug-level logging by default

Usage:
------
1. Command Line:
   $ pdl-lint [options] [path...]

   Options:
   -r, --recursive    Lint all PDL files in the directory recursively
   --debug            Enable debug logging
   --no-debug         Disable debug logging
   -l, --log-file     Specify log file path

2. As a Python Module:
   from pdl.pdl_linter import run_linter
   exit_code = run_linter()

Features:
---------
- Automatic project root detection based on common indicators (.git, .hg, pyproject.toml, etc.)
- Configurable file and directory ignore patterns
- Flexible logging configuration for both file and console output
- Support for recursive directory scanning
- Graceful handling of configuration errors
- Detailed error reporting with file locations

The linter will:
- Lint every path named on the command line, whatever its suffix and wherever it
  sits. A path you typed is never skipped; the rules below describe which files a
  *directory* is walked for.
- Skip files not ending in .pdl when walking a directory
- Ignore files and directories specified in the configuration when walking a
  directory
- Report syntax errors and other issues in PDL files
- Provide detailed logging of the linting process

Exit Codes:
----------
0 - All files linted successfully
1 - One or more files failed linting
"""

import argparse
import ast
import logging
import sys
import tomllib
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from pdl.pdl_ast import BlockType, CodeBlock
from pdl.pdl_ast_utils import iter_block_children

# The `code:` gutter, its wrapping and its closing caveat are `pdl_interpreter`'s,
# and they are imported rather than reimplemented on purpose: a `code:` block that
# fails to compile at lint time and one that fails to compile at run time are the
# same failure seen from two tools, and E-CODE-001 already chose how to render it.
# A second, near-identical renderer here would drift -- the caveat's wording most
# of all, which is the one sentence a reader may have already seen from `pdl`.
# Private names, because the interpreter's diagnostic vocabulary is not public API.
# `_wrap` came from here until it acquired this third caller and moved to
# `pdl_diagnostics`, which is where the rest of these belong if they gain one.
from pdl.pdl_diagnostics import _wrap
from pdl.pdl_interpreter import (
    _RAISED_GUTTER_CAVEAT,
    EXPR_START_STRING,
    _gutter,
    _safe_text,
    _syntax_error_rows,
)
from pdl.pdl_parser import PDLParseError
from pdl.pdl_parser import parse_file as parse_pdl_file

logger = logging.getLogger(__name__)


def _guess_project_root_dir(start_path: Path = Path.cwd()) -> Path | None:
    """
    Guess the project root directory starting from the current working directory.

    Returns:
        The project root directory or None if the current working directory couldn't be
        determined to be part of a project.
    """
    path = start_path
    path = path.absolute()

    def is_fs_root(path: Path) -> bool:
        return path == path.parent

    # For cases where a weak indicator is found, we will append the path to this list
    # and pick the last path because it is more likely to be the project root.
    project_root_candidates = []
    while not is_fs_root(path):
        match path:
            case path if path.joinpath(".git").is_dir():
                # .git directory is a strong indicator of a project's root directory
                # NOTE: Git submodules only have a .git file in its top-level directory
                return path
            case path if path.joinpath(".hg").is_dir():
                # .hg directory is a good indicator of a project's root directory
                # NOTE: Mercurial sub-repositories will not interfere
                return path
            case path if path.joinpath("pyproject.toml").is_file():
                # The existence of a pyproject.toml file is a good indicator.
                # However, in a setting where there are multiple 'workspace members' or namespace packages,
                # there will be a pyproject.toml or setup.py file in every namespace package's root directory.
                # See:
                # - https://packaging.python.org/en/latest/guides/packaging-namespace-packages/
                # - https://docs.astral.sh/uv/concepts/projects/workspaces/
                project_root_candidates.append(path)
            case path if path.joinpath("requirements.txt").is_file():
                # The existence of a requirements.txt file is a good indicator because
                # it is a common way to manage dependencies for Python projects.
                # However, there is a chance that the requirements.txt file is not in the project root.
                project_root_candidates.append(path)
            case path if path.joinpath("setup.py").is_file():
                # The existence of a setup.py file is a good indicator.
                # However, in a setting where there are multiple 'workspace members' or namespace packages,
                # there will be a pyproject.toml or setup.py file in every namespace package's root directory.
                # See:
                # - https://packaging.python.org/en/latest/guides/packaging-namespace-packages/
                # - https://docs.astral.sh/uv/concepts/projects/workspaces/
                project_root_candidates.append(path)
            case _:
                pass
        # If no strong indicator is found, move up one level.
        path = path.parent

    # If no strong indicator is found, return the last candidate.
    return project_root_candidates[-1] if project_root_candidates else None


class IgnoreReason(Enum):
    """Why a path was left out of a directory walk.

    `should_ignore` used to collapse four distinct conditions into `True`, and
    the single skip message named the third of them for all four: a file outside
    the project root, or one with a suffix other than `.pdl`, was reported as
    `(in ignore list)` when nothing was in any ignore list. A user who went
    looking for that entry found no such entry, which is the worst shape a
    diagnostic can take -- confidently wrong rather than merely thin.

    The wording of each member is the wording of the `logger.debug` line its
    branch already emitted. Those four strings were correct all along; they were
    just not the ones the user was shown.
    """

    OUTSIDE_PROJECT_ROOT = "not within the project root"
    NOT_A_PDL_FILE = "not a *.pdl file"
    IN_IGNORE_LIST = "in the ignore list"
    IN_IGNORED_DIRECTORY = "in a directory marked to be ignored"

    def describe(self, project_root: Path) -> str:
        """The reason as it is shown to the user.

        Only the first reason needs a parameter, and it needs it badly: "not
        within the project root" invites the question "which root?", and the
        linter's answer is a guess (`_guess_project_root_dir`) that the user has
        no other way to see.
        """
        if self is IgnoreReason.OUTSIDE_PROJECT_ROOT:
            return f"{self.value} {project_root}"
        return self.value


LogLevelLiteral = Literal[
    "CRITICAL",
    "FATAL",
    "ERROR",
    "WARNING",
    "WARN",
    "INFO",
    "DEBUG",
    "NOTSET",
]


class LinterConfig(BaseModel):
    """
    Configuration for the PDL linter.
    """

    project_root: Path = Field(exclude=True)
    """
    The root directory of the project.
    """

    ignore: set[Path] = Field(default_factory=set)
    """
    A list of paths to ignore.
    """

    log_file: Path | None = Field(default=None)
    """
    The file to log to.
    """

    file_log_level: LogLevelLiteral = Field(default="DEBUG")
    """
    The level for logging to a file.
    """

    file_log_format: str = Field(default="%(asctime)s %(name)s: %(message)s")
    """
    The format for logging to a file.
    """

    console_log_enabled: bool = Field(default=True)
    """
    Whether to log to the console.
    """

    console_log_level: LogLevelLiteral = Field(default="INFO")
    """
    The level for logging to the console.
    """

    console_log_format: str = Field(default="%(message)s")
    """
    The format for logging to the console.
    """

    debug: bool = Field(default=False)
    """
    Whether to enable debug-level logging by default.
    """

    model_config = ConfigDict(extra="allow")
    """
    Allow extra fields in the configuration. We shouldn't have to fail a build if extra fields are present.
    Instead, we will notify the user about the extra fields that have no effect on the linter.
    """

    directories_to_ignore: set[Path] = Field(exclude=True, default_factory=set)
    """
    A list of directories to ignore.
    """

    def model_post_init(self, __context: Any) -> None:
        """
        Post-initialize the model.
        """
        valid_paths_to_ignore = set()
        for path in self.ignore:
            if path.is_absolute():
                logger.warning(
                    "⚠️  Ignoring path '%s' because it is an absolute path."
                    " Use a relative path instead.",
                    path,
                )
                continue

            absolute_path = self.project_root / path
            if not absolute_path.exists():
                logger.warning(
                    "⚠️  Ignoring path '%s' because it does not exist.",
                    path,
                )
                continue

            valid_paths_to_ignore.add(path)

            if absolute_path.is_dir():
                self.directories_to_ignore.add(path)

        self.ignore = valid_paths_to_ignore

    def should_ignore(self, path: Path) -> IgnoreReason | None:
        """
        Check if a path should be ignored, and say why.

        Returns the reason, or `None` when the path is good to lint. Every
        `IgnoreReason` is truthy and `None` is falsy, so `if config.should_ignore(p)`
        reads exactly as it did when this returned a `bool`; the caller that
        prints a skip line now has the one piece of information it was missing.

        These four conditions are all about the *scope of a directory walk*. None
        of them says a file is unlintable: a `.pdl` outside the project root parses
        exactly as well as one inside it. That is why a path named explicitly on
        the command line does not consult this at all -- see `_lint_pdl_file`.
        """
        logger.debug("Checking if %s should be ignored.", path)
        match path:
            case path if not path.absolute().is_relative_to(self.project_root):
                logger.debug(" ⏩  Not within the project root %s.", self.project_root)
                return IgnoreReason.OUTSIDE_PROJECT_ROOT

            case path if path.is_file() and path.suffix != ".pdl":
                logger.debug(" ⏩  Not a *.pdl file.")
                return IgnoreReason.NOT_A_PDL_FILE

            case path if path in self.ignore:
                logger.debug(" ⏩  In the ignore list.")
                return IgnoreReason.IN_IGNORE_LIST

            case path if any(
                path.is_relative_to(d) for d in self.directories_to_ignore
            ):
                logger.debug(" ⏩  In a directory marked to be ignored.")
                return IgnoreReason.IN_IGNORED_DIRECTORY

            case _:
                logger.debug(" ✅  Good to lint.")
                return None

    @classmethod
    def load(cls) -> Self:
        """
        Load the linter configuration from pyproject.toml or .pdl-lint.

        Preference will be given to the pyproject.toml file if it contains a [tool.pdl-lint] section.
        The .pdl-lint file will only be used when either the pyproject.toml file is not found,
        or when the pyproject.toml file doesn't have a [tool.pdl-lint] section.
        """
        project_root_dir = _guess_project_root_dir() or Path.cwd()
        pyproject_path = project_root_dir / "pyproject.toml"
        pdl_lint_path = project_root_dir / ".pdl-lint"

        config_data: dict[str, Any] = {}
        config_source: Path | None = None

        # Try pyproject.toml first
        if pyproject_path.is_file():
            try:
                toml_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
                if "tool" in toml_data and "pdl-lint" in toml_data["tool"]:
                    config_data = toml_data["tool"]["pdl-lint"]
                    config_source = pyproject_path
                    logger.debug(
                        "Loading config from %s [tool.pdl-lint]", config_source
                    )
            except tomllib.TOMLDecodeError as e:
                logger.warning(
                    "⚠️  Error reading %s: %s. Skipping.",
                    pyproject_path,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "⚠️  Unexpected error processing %s: %s. Skipping.",
                    pyproject_path,
                    e,
                )

        # If no config found in pyproject.toml, try .pdl-lint
        if not config_source and pdl_lint_path.is_file():
            try:
                toml_data = tomllib.loads(pdl_lint_path.read_text(encoding="utf-8"))
                # .pdl-lint can have the config at the root or under [pdl-lint]
                if "pdl-lint" in toml_data:
                    config_data = toml_data["pdl-lint"]
                elif all(
                    k not in ["tool", "project", "build-system"] for k in toml_data
                ):
                    # Assume root level config if no standard sections are present
                    config_data = toml_data
                config_source = pdl_lint_path
                logger.debug("Loading config from %s", config_source)
            except tomllib.TOMLDecodeError as e:
                logger.warning(
                    "⚠️  Error reading %s: %s. Skipping.",
                    pdl_lint_path,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "⚠️  Unexpected error processing %s: %s. Skipping.",
                    pdl_lint_path,
                    e,
                )

        if not config_source:
            logger.warning(
                "⚠️  No PDL linter configuration file found or section usable in %s."
                " Using default configuration.",
                project_root_dir,
            )

        linter_config = cls.model_validate(
            {"project_root": project_root_dir, **config_data}
        )

        if linter_config.model_extra and config_source:
            logger.warning(
                "⚠️  Unrecognized fields for pdl-lint configuration in %s."
                " These fields will be ignored:",
                config_source,
            )
            for key, value in linter_config.model_extra.items():
                logger.warning("  %s = %s", key, repr(value))
            logger.warning("")  # Add a blank line for readability

        return linter_config


# The linter lists one line per file (` - ✅  x.pdl`, ` - ❌  y.pdl`), and a
# diagnostic sits *under* the file it is about. Five spaces is the gutter the
# first line of a diagnostic has always been printed at; what was missing is that
# the rest of the diagnostic was printed at zero, so the second line of one error
# did not line up with its first.
_DIAGNOSTIC_INDENT = " " * 5


def _indent_diagnostic(text: str) -> str:
    """Put a whole rendered diagnostic under the linter's five-space gutter.

    Uniform, and deliberately dumb: the continuation lines are the rendered text's
    own structure -- the `  in <path>` line, the `N | ...` excerpt gutter and its
    caret, the wrapped rule paragraph, the closing `note:` -- and every one of them
    is positioned relative to the others by the renderer that produced it. Shifting
    the block as a whole preserves all of that; re-wrapping it would not, and would
    desync the linter from `pdl_diagnostics.render` and `pdl_interpreter._wrap`,
    which are what a reader sees when the *same* diagnostic reaches them from
    `pdl`. The two tools must spell one error the same way.

    The cost is measured rather than assumed: the widest line of a diagnostic grows
    by five, so E-LINT-003's rule paragraph reaches 81 columns. That is inside the
    envelope the linter already has -- E-LINT-002's header is 89 columns today and
    is unchanged by this -- and narrowing it means changing the width the
    interpreter wraps to, for both tools at once.

    Blank lines stay blank. Indenting them would make five spaces of trailing
    whitespace on every paragraph break, which the `trailing-whitespace` hook
    would then rewrite out of the goldens underneath this.
    """
    return "\n".join(
        _DIAGNOSTIC_INDENT + line if line.strip() else "" for line in text.split("\n")
    )


class _CodeBlockSyntaxError(Exception):
    """A `code:` block that Python's parser rejected.

    Module-private, and carrying the `SyntaxError` rather than a rendered string:
    the file name belongs in the diagnostic and `_lint_python_code_blocks` does
    not know it, so rendering happens in `_lint_pdl_file`, which does.
    """

    def __init__(self, exc: SyntaxError, code: str):
        super().__init__(exc.msg)
        self.exc = exc
        self.code = code


def _lint_pdl_file(
    file_path: Path, config: LinterConfig, *, explicit: bool = False
) -> bool:
    """
    Lint a PDL file.

    `explicit` means the user named this path on the command line. Such a path is
    always linted: the ignore rules describe which files a *directory walk* picks
    up, and a user who typed a path has already answered that question. This is
    the settled convention elsewhere -- ruff checks files passed directly on the
    command line even when they would normally be excluded, and eslint has
    `--no-ignore` for the same reason.

    It is also the only way to stop the false green. A skipped file used to
    `return True`, so `pdl-lint <path>` reported "All files linted successfully"
    and exited 0 for a file it had never opened; CI that names a path believed it
    was checked. An explicit path can now fail, which means an invocation that
    exits 0 today can exit 1 -- knowingly, because the 0 was false. See
    `docs/release-notes.md`.
    """
    if not explicit:
        reason = config.should_ignore(file_path)
        if reason is not None:
            logger.info(
                " - ℹ️  SKIPPING %s (%s)",
                file_path,
                reason.describe(config.project_root),
            )
            return True

    try:
        prog, _ = parse_pdl_file(file_path)
        _lint_python_code_blocks(prog.root)
        logger.info(" - ✅  %s", file_path)
        return True
    except PDLParseError as e:
        logger.error(" - ❌  %s", file_path)
        # `e.text` is already a rendered diagnostic with its own `file:line:col`
        # header. The class name in front of it was PDL's internal vocabulary
        # leaking into the linter's output -- the `pdl` interpreter prints the
        # same diagnostic without it -- so the two tools now spell one error the
        # same way.
        logger.error("%s", _indent_diagnostic(e.text))
        return False
    except _CodeBlockSyntaxError as e:
        logger.error(" - ❌  %s", file_path)
        logger.error("%s", _indent_diagnostic(_code_syntax_diagnostic(file_path, e)))
        return False
    except Exception:
        logger.exception(" - ❌  %s", file_path)
        return False


_CODE_SYNTAX_RULE = (
    "`pdl-lint` parses every `code:` block with Python's own parser. The block "
    "must be syntactically valid Python even though the linter never runs it."
)


def _code_syntax_diagnostic(file_path: Path, failure: _CodeBlockSyntaxError) -> str:
    """Render a `code:` block's syntax error the way `pdl` renders one.

    The header names the file and no line, which is the whole truth available
    here: `SyntaxError.lineno` counts lines of the *block's code*, and the
    `CodeBlock`'s `pdl__location` -- the only thing that could translate that into
    a `.pdl` line -- is `None` after `parse_file`, because the interpreter is what
    populates it (`process_block_body`). Inventing a `.pdl` line from the code
    line would be a confidently-stated wrong location, which the rubric ranks
    below no location at all. The gutter says where the error is inside the block,
    and the closing note says what those numbers mean.
    """
    lines = [
        f"{file_path} - `code:` block is not valid Python: "
        f"{_safe_text(failure.exc.msg)}",
        "",
    ]
    gutter = _gutter(_syntax_error_rows(failure.exc, failure.code))
    if gutter:
        lines += gutter + [""]
    lines += _wrap(_CODE_SYNTAX_RULE)
    if gutter:
        lines += ["", *_wrap(f"note: {_RAISED_GUTTER_CAVEAT}", subsequent=" " * 8)]
    return "\n".join(lines)


def _lint_python_code_blocks(block: BlockType):
    match block:
        case CodeBlock(lang="python", code=code):
            if isinstance(code, str) and EXPR_START_STRING not in code:
                # Try to parse the Python code if the code block is
                # a string that does not contains a jinja expression
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    # Caught here and nowhere wider: a `SyntaxError` from
                    # `ast.parse` is the *expected* outcome of linting a broken
                    # block, and it used to reach `except Exception` and print a
                    # traceback ending in `File "<unknown>", line 1`. Every other
                    # failure still goes to that handler, which stays as the
                    # catch-all for genuinely unexpected ones.
                    raise _CodeBlockSyntaxError(exc, code) from exc
    iter_block_children(_lint_python_code_blocks, block)


def _lint_pdl_files_in_directory(
    directory: Path, recursive: bool, config: LinterConfig
) -> List[Path]:
    """
    Lint all PDL files in a directory.

    Args:
        directory: The directory containing the PDL files to lint.
        recursive: Whether to lint the PDL files in the directory recursively.
        config: The configuration for the linter.
    Returns:
        A list of files that failed linting.

    Raises:
        NotADirectoryError: If the given path is not a directory.
    """

    if not directory.is_dir():
        raise NotADirectoryError(f"'{directory}' is not a directory")

    # Convert the directory to a path relative to the project root.
    # NOTE: The directory is made absolute to avoid issues with resolving relative paths.
    absolute_path = directory.absolute()
    relative_path = absolute_path.relative_to(config.project_root)
    reason = config.should_ignore(relative_path)
    if reason is not None:
        logger.info(
            " - ℹ️  SKIPPING all files in %s because it is %s.",
            absolute_path,
            reason.describe(config.project_root),
        )
        return []

    pdl_files = list(
        pdl
        for pdl in (
            relative_path.rglob("*.pdl") if recursive else relative_path.glob("*.pdl")
        )
        if pdl.is_file()
    )

    if len(pdl_files) == 0:
        logger.warning("No PDL files found in %s", absolute_path)
        return []

    logger.info(
        "Linting %d PDL files in %s %s...",
        len(pdl_files),
        absolute_path,
        "(recursively)" if recursive else "",
    )

    failed_files = []

    for file in pdl_files:
        if not _lint_pdl_file(file, config):
            failed_files.append(file)

    return failed_files


def _arg_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=(
            "Lint all PDL files in the directory recursively. "
            "NOTE: This is only applicable when linting for files in a directory."
        ),
        required=False,
        default=False,
    )

    debug_flag = parser.add_mutually_exclusive_group(
        required=False,
    )
    debug_flag.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
        dest="debug",
        default=False,
    )
    debug_flag.add_argument(
        "--no-debug",
        action="store_false",
        help="Disable debug logging.",
        dest="debug",
        default=True,
    )

    parser.add_argument(
        "-l",
        "--log-file",
        type=Path,
        help="The file to log to.",
        default=None,
    )

    parser.add_argument(
        "paths",
        type=Path,
        help="The path(s) to lint.",
        nargs="*",  # Allow zero or more paths
        default=[Path.cwd()],  # Default to cwd if no paths provided
    )

    return parser


def _setup_logging(args: argparse.Namespace, config: LinterConfig):
    """
    Setup logging for the linter.
    """
    log_file = args.log_file or config.log_file
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(config.file_log_format))
        file_handler.setLevel(logging.DEBUG if args.debug else config.file_log_level)
        logger.addHandler(file_handler)

    if config.console_log_enabled:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter(config.console_log_format))
        stream_handler.setLevel(
            logging.DEBUG if args.debug else config.console_log_level
        )
        logger.addHandler(stream_handler)

    is_debug = args.debug or config.debug
    if is_debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)


def run_linter() -> int:
    """
    Run the PDL linter with the given arguments.

    Returns:
        The exit code of the linter.
    """
    config = LinterConfig.load()

    parser = _arg_parser()
    args = parser.parse_args()

    _setup_logging(args, config)
    logger.debug("Project root: %s", config.project_root)
    logger.debug("Linter config: %s", config.model_dump_json(indent=2))

    files_that_failed_linting = []

    logger.debug("Paths to lint: %s", args.paths)

    for path in args.paths:
        match path:
            case Path() as file if file.is_file():
                if not _lint_pdl_file(file, config, explicit=True):
                    files_that_failed_linting.append(file)
            case Path() as directory if directory.is_dir():
                files_that_failed_linting.extend(
                    _lint_pdl_files_in_directory(directory, args.recursive, config)
                )
            case _:
                logger.error(
                    "‼️  Error: %s is not a PDL file or directory. SKIPPING...",
                    path,
                )

    logger.info("-" * 100)
    if not files_that_failed_linting:
        logger.info("🎉  All files linted successfully 🎉")
        return 0

    logger.error(
        "😮  Linting failed for %d file(s):",
        len(files_that_failed_linting),
    )
    for file in files_that_failed_linting:
        logger.error(" - %s", file)
    return 1


if __name__ == "__main__":
    sys.exit(run_linter())
