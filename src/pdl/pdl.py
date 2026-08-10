import argparse
import json
import os
import sys
from asyncio import AbstractEventLoop
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from pydantic.json_schema import models_json_schema

from . import pdl_interpreter
from ._version import version
from .pdl_ast import (
    BlockType,
    PdlBlock,
    PDLException,
    PdlLocationType,
    PDLScopeError,
    PdlUsage,
    Program,
    RoleType,
    empty_block_location,
    get_default_model_parameters,
)
from .pdl_diagnostics import (
    ORIGIN_ARGUMENT,
    ORIGIN_DATA_FILE,
    model_defaults_diagnostic,
)
from .pdl_interpreter import InterpreterState, process_prog
from .pdl_interpreter_state import ScopeType
from .pdl_parser import parse_dict, parse_file, parse_str, source_read_error, yaml_error
from .pdl_runner import exec_docker
from .pdl_utils import (  # pylint: disable=unused-import # noqa: F401
    Ref,
    validate_scope,
    write_trace,
)

os.environ["DISABLE_AIOHTTP_TRANSPORT"] = "True"


class InterpreterConfig(TypedDict, total=False):
    """Configuration parameters of the PDL interpreter."""

    yield_result: bool
    """Print incrementally result of the execution.
    """
    yield_background: bool
    """Print the program background messages during the execution.
    """
    batch: int
    """Model inference mode:
         - 0: streaming
         - 1: non-streaming
    """
    role: RoleType
    """Default role.
    """
    cwd: Path
    """Path considered as the current working directory for file reading.
    """
    replay: dict[str, Any]
    """Execute the program reusing some already computed values.
    """
    with_resample: bool
    """Allow the interpreter to raise the `Resample` exception."""
    ignore_factor: bool
    """Do not evaluate the expression associated to the `factor` block but use `0` instead (so resample if `with_resample` is true)."""
    score: float | Ref[float]
    """Initial value of the score."""
    event_loop: AbstractEventLoop
    """Event loop to schedule LLM calls."""
    llm_usage: PdlUsage
    """Data structure where to accumulate LLMs usage."""


class Result(TypedDict):
    result: Any
    scope: dict[str, Any]
    trace: BlockType
    replay: dict[str, Any]
    score: float
    usage: PdlUsage


def exec_program(
    prog: Program,
    config: InterpreterConfig | None = None,
    scope: ScopeType | Mapping[str, Any] | None = None,
    loc: PdlLocationType | None = None,
    output: Literal["result", "all"] = "result",
) -> Any:
    """Execute a PDL program given as a value of type `pdl.pdl_ast.Program`.

    Args:
        prog: Program to execute.
        config: Interpreter configuration. Defaults to None.
        scope: Environment defining the initial variables in scope to execute the program. Defaults to None.
        loc: Source code location mapping. Defaults to None.
        output: Configure the output of the returned value of this function. Defaults to `"result"`

    Returns:
        Return the final result if `output` is set to `"result"`. If set of `all`, it returns a dictionary containing, `result`, `scope`, `trace`, `replay`, and `score`.
    """
    config = config or InterpreterConfig()
    config["replay"] = dict(config.get("replay", {}))
    score = config.get("score")
    if score is not None and not isinstance(score, Ref):
        config["score"] = Ref(score)
    assert config.get("score") is None or isinstance(config.get("score"), Ref)
    state = InterpreterState(**config)  # pyright: ignore
    if not isinstance(scope, ScopeType):
        scope = ScopeType(scope or {})
    loc = loc or empty_block_location
    initial_scope = {"pdl_model_default_parameters": get_default_model_parameters()}
    future_result, _, future_scope, trace = process_prog(
        state, scope | initial_scope, prog, loc
    )
    result = future_result.result()
    match output:
        case "result":
            return result
        case "all":
            result_all: Result = {
                "result": result,
                "scope": future_scope.result(),
                "trace": trace,
                "replay": state.replay,
                "score": state.score.ref,
                "usage": state.llm_usage,
            }
            return result_all
        case _:
            assert False, 'The `output` variable should be "result" or "all"'


def exec_dict(
    prog: dict[str, Any],
    config: InterpreterConfig | None = None,
    scope: ScopeType | Mapping[str, Any] | None = None,
    loc: PdlLocationType | None = None,
    output: Literal["result", "all"] = "result",
) -> Any:
    """Execute a PDL program given as a dictionary.

    Args:
        prog: Program to execute.
        config: Interpreter configuration. Defaults to None.
        scope: Environment defining the initial variables in scope to execute the program. Defaults to None.
        loc: Source code location mapping. Defaults to None.
        output: Configure the output of the returned value of this function. Defaults to `"result"`

    Returns:
        Return the final result.
    """
    program = parse_dict(prog)
    result = exec_program(program, config, scope, loc, output)
    return result


def exec_str(
    prog: str,
    config: InterpreterConfig | None = None,
    scope: ScopeType | Mapping[str, Any] | None = None,
    output: Literal["result", "all"] = "result",
) -> Any:
    """Execute a PDL program given as YAML string.

    Args:
        prog: Program to execute.
        config: Interpreter configuration. Defaults to None.
        scope: Environment defining the initial variables in scope to execute the program. Defaults to None.
        output: Configure the output of the returned value of this function. Defaults to `"result"`

    Returns:
        Return the final result.
    """
    program, loc = parse_str(prog)
    result = exec_program(program, config, scope, loc, output)
    return result


def exec_file(
    prog: str | Path,
    config: InterpreterConfig | None = None,
    scope: ScopeType | Mapping[str, Any] | None = None,
    output: Literal["result", "all"] = "result",
) -> Any:
    """Execute a PDL program given as YAML file.

    Args:
        prog: Program to execute.
        config: Interpreter configuration. Defaults to None.
        scope: Environment defining the initial variables in scope to execute the program. Defaults to None.
        output: Configure the output of the returned value of this function. Defaults to `"result"`

    Returns:
        Return the final result.
    """
    program, loc = parse_file(prog)
    if config is None:
        config = InterpreterConfig()
    if config.get("cwd") is None:
        config["cwd"] = Path(prog).parent
    result = exec_program(program, config, scope, loc, output)
    return result


def pdl(func):
    def pdl_wrapper(scope):
        result = exec_str(prog=func.__doc__, scope=scope)
        return result

    return pdl_wrapper


MODEL_DEFAULTS_KEY = "pdl_model_default_parameters"


def load_initial_scope(
    data_file: str | None,
    data: str | None,
    program: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Build the CLI's initial scope out of the built-in defaults, `-f` and `-d`.

    Shared by `pdl` and `pdl-infer`, which carried byte-for-byte copies of this
    sequence: without the sharing, `pdl-infer` keeps every traceback the `pdl`
    entry point loses.

    Returns the scope along with the origin of `pdl_model_default_parameters`.
    The origin is not guessed -- the three sources are merged in a known order,
    so a membership test on each dict as it is merged names the last one to
    supply the key exactly.
    """
    initial_scope: dict[str, Any] = {MODEL_DEFAULTS_KEY: get_default_model_parameters()}
    origin, origin_file = "builtin", ""

    if data_file is not None:
        path = Path(data_file)
        try:
            with open(path, "r", encoding="utf-8") as scope_fp:
                raw = scope_fp.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
            raise source_read_error(path, exc, data_file=True) from exc
        try:
            # Loaded from a string rather than the stream so that a syntax error
            # gets the whole file as its excerpt: PyYAML truncates `mark.buffer`
            # on the stream path.
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise yaml_error(
                exc,
                raw,
                str(path),
                origin=ORIGIN_DATA_FILE,
                program=program,
                code="E-CLI-003",
            ) from exc
        if isinstance(loaded, dict) and MODEL_DEFAULTS_KEY in loaded:
            origin, origin_file = ORIGIN_DATA_FILE, str(path)
        initial_scope = initial_scope | loaded

    if data is not None:
        try:
            loaded = yaml.safe_load(data)
        except yaml.YAMLError as exc:
            raise yaml_error(
                exc,
                data,
                "--data",
                origin=ORIGIN_ARGUMENT,
                program=program,
                code="E-CLI-003",
            ) from exc
        if isinstance(loaded, dict) and MODEL_DEFAULTS_KEY in loaded:
            origin, origin_file = ORIGIN_ARGUMENT, "--data"
        initial_scope = initial_scope | loaded

    return initial_scope, origin, origin_file


def scope_error_text(
    exc: PDLScopeError, origin: str, origin_file: str, program: str | None
) -> str:
    """Render a scope-validation failure, naming the input that supplied it."""
    if exc.path[:1] == [MODEL_DEFAULTS_KEY]:
        return model_defaults_diagnostic(
            path=exc.path,
            pattern=exc.pattern,
            value=exc.value,
            reason=exc.reason,
            origin=origin,
            origin_file=origin_file,
            program=program,
        ).text
    return exc.text


def main():
    parser = argparse.ArgumentParser("")
    parser.add_argument(
        "--sandbox",
        action=argparse.BooleanOptionalAction,
        help="run the interpreter in a container, a Docker-compatible daemon must be running",
    )
    parser.add_argument(
        "-f",
        "--data-file",
        dest="data_file",
        help="YAML file containing initial values to add to the scope",
    )
    parser.add_argument(
        "-d",
        "--data",
        help="initial values to add to the scope",
    )
    parser.add_argument(
        "--stream",
        choices=["result", "context", "none"],
        default="result",
        help="stream the background context, or nothing on the standard output",
    )
    parser.add_argument(
        "-t",
        "--trace",
        nargs="?",
        const="*_trace.json",
        help="output trace for live document and optionally specify the file name",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="generate PDL JSON Schema and exit",
        default=False,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the version number and exit",
        default=False,
    )

    parser.add_argument("pdl", nargs="?", help="pdl file", type=str)

    args = parser.parse_args()

    # This case must be before `if args.pdl is None:`
    if args.version:
        print(f"PDL {version}")
        return 0

    # This case must be before `if args.pdl is None:`
    if args.schema:
        schema, top_level_schema = models_json_schema(
            [
                (Program, "validation"),
                (PdlBlock, "validation"),
            ],
            title="PDL Schemas",
        )
        top_level_schema["anyOf"] = list(schema.values())
        print(json.dumps(top_level_schema, indent=2))
        return 0

    if args.pdl is None:
        parser.print_help()
        return 0

    if args.sandbox:
        args = sys.argv[1:]
        args.remove("--sandbox")
        exec_docker(*args)
        assert False  # unreachable: exec_docker terminate the execution

    # `-f`, `-d` and `validate_scope` all run before `generate`, so `generate`
    # cannot see them and their failures need their own handler. A failure here
    # is never about the program: it has not been read yet.
    defaults_origin, defaults_file = "builtin", ""
    try:
        initial_scope, defaults_origin, defaults_file = load_initial_scope(
            args.data_file, args.data, program=args.pdl
        )
        validate_scope(initial_scope)
    except PDLScopeError as exc:
        print(
            scope_error_text(exc, defaults_origin, defaults_file, args.pdl),
            file=sys.stderr,
        )
        return 1
    except PDLException as exc:
        print(exc.text, file=sys.stderr)
        return 1

    match args.stream:
        case "result":
            stream_result = True
            stream_background = False
        case "context":
            stream_result = False
            stream_background = True
        case "none":
            stream_result = False
            stream_background = False
        case _:
            assert False

    if stream_result or stream_background:
        batch = 0
    else:
        batch = 1

    pdl_file = Path(args.pdl)
    if args.trace == "*_trace.json":
        trace_file = str(pdl_file.with_suffix("")) + "_trace.json"
    else:
        trace_file = args.trace
    config = InterpreterConfig(
        yield_result=stream_result,
        yield_background=stream_background,
        batch=batch,
        cwd=pdl_file.parent,
    )
    score = config.get("score")
    if score is not None and not isinstance(score, Ref):
        config["score"] = Ref(score)
    assert config.get("score") is None or isinstance(config.get("score"), Ref)

    exit_code = pdl_interpreter.generate(
        pdl_file,
        InterpreterState(**config),  # pyright: ignore
        ScopeType(initial_scope),
        trace_file,
    )
    return exit_code


if __name__ == "__main__":
    main()
