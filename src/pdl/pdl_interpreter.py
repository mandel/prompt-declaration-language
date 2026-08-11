# pylint: disable=import-outside-toplevel
import ast
import builtins
import csv
import difflib
import json
import random
import re
import shlex
import subprocess  # nosec
import sys
import textwrap
import time
import traceback
import types

# TODO: temporarily disabling warnings to mute a pydantic warning from liteLLM
import warnings
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from functools import partial, reduce
from io import StringIO
from itertools import count
from os import getenv
from pathlib import Path
from typing import (
    IO,
    Any,
    Generator,
    Iterable,
    Sequence,
    Tuple,
    TypeVar,
)

import httpx
import json_repair
import yaml
from jinja2 import (
    Environment,
    StrictUndefined,
    Template,
    TemplateSyntaxError,
    UndefinedError,
    meta,
)
from jinja2.nodes import TemplateData
from jinja2.runtime import Undefined
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from . import pdl_ast
from .pdl_ast import (
    AdvancedBlockType,
    AggregatorBlock,
    AnyPattern,
    ArgsBlock,
    ArrayBlock,
    ArrayPattern,
    Block,
    BlockKind,
    BlockType,
    CallBlock,
    CodeBlock,
    CommandCodeBlock,
    ContributeElement,
    ContributeTarget,
    ContributeValue,
    DataBlock,
    EmptyBlock,
    ErrorBlock,
    ExpressionBlock,
    ExpressionType,
    FactorBlock,
    FileAggregatorConfig,
    FunctionBlock,
    GetBlock,
    GraniteioModelBlock,
    GraniteioProcessor,
    IfBlock,
    ImportBlock,
    IncludeBlock,
    IndependentEnum,
    IPythonCodeBlock,
    JinjaCodeBlock,
    JoinArray,
    JoinLastOf,
    JoinObject,
    JoinReduce,
    JoinText,
    JoinType,
    LastOfBlock,
    LazyMessage,
    LazyMessages,
    LeafBlock,
    LeafBlockType,
    LitellmModelBlock,
    LitellmParameters,
    LocalizedExpression,
    MapBlock,
    MatchBlock,
    MessageBlock,
    ModelBlock,
    ModelInput,
    ModelPlatform,
    ObjectBlock,
    ObjectPattern,
    ObjectPdlType,
    OpenaiModelBlock,
    OpenaiParameters,
    OrPattern,
    ParserType,
    Pattern,
    PatternType,
    PdlCodeBlock,
    PDLImportError,
    PdlLocationType,
    PdlParser,
    PDLRuntimeError,
    PDLRuntimeExpressionError,
    PDLRuntimeParserError,
    PDLRuntimeProcessBlocksError,
    PdlTiming,
    PdlUsage,
    Program,
    PythonCodeBlock,
    ReadBlock,
    RegexParser,
    RepeatBlock,
    RetryConfiguration,
    RoleType,
    SequenceBlock,
    StructuredBlock,
    StructuredBlockType,
    TextBlock,
    empty_block_location,
)
from .pdl_context import (
    DependentContext,
    IndependentContext,
    PDLContext,
    SerializeMode,
    SingletonContext,
    add_done_callback,
    deserialize,
    ensure_context,
)
from .pdl_diagnostics import Diagnostic, import_read_diagnostic
from .pdl_interpreter_state import InterpreterState, ScopeType
from .pdl_lazy import PdlConst, PdlDict, PdlLazy, PdlList, lazy_apply
from .pdl_llms import LitellmModel
from .pdl_location_utils import append, get_line, get_loc_string
from .pdl_parser import (
    PDLParseError,
    parse_file,
    parse_str,
    undecodable_source_error,
)
from .pdl_python_repl import PythonREPL
from .pdl_scheduler import (
    yield_background,
    yield_result,
)
from .pdl_schema_utils import get_json_schema
from .pdl_schema_validator import type_check_args, type_check_spec
from .pdl_utils import (
    GeneratorWrapper,
    Resample,
    apply_defaults,
    get_contribute_context_value,
    replace_contribute_value,
    stringify,
    value_of_expr,
    write_trace,
)

warnings.filterwarnings("ignore", "Valid config keys have changed in V2")

empty_scope = ScopeType(
    {
        "pdl_context": DependentContext([]),
        "pdl_particle_id": 0,
        "pdl_llm_as_judge": "watsonx/openai/gpt-oss-120b",
        "pdl_llm_context_transformer": "watsonx/openai/gpt-oss-120b",
    }
)


class ClosureBlock(FunctionBlock):
    pdl__scope: SkipJsonSchema[ScopeType | None] = Field(repr=False)
    pdl__state: SkipJsonSchema[InterpreterState] = Field(repr=False)
    pdl__instance_id: SkipJsonSchema[int] = Field(repr=False, default=0)

    def __call__(self, *args, **kwargs):
        state = self.pdl__state.with_yield_result(False).with_yield_background(False)
        state = state.with_id(f"instance{self.pdl__instance_id}")
        self.pdl__instance_id += 1
        current_context = state.current_pdl_context.ref
        if len(args) > 0:
            keys = self.function.keys() if self.function is not None else {}
            if len(keys) < len(args):
                if (
                    self.signature is not None
                    and self.signature["function"].get("name", "") != ""
                ):
                    err = f"Too many arguments to the call of {self.signature['function']['name']}"
                else:
                    err = "Too many arguments to the call"
                raise PDLRuntimeError(
                    err,
                    loc=self.pdl__location,
                    trace=self.model_copy(),
                )
            kwargs = dict(zip(keys, args)) | kwargs
        result, _, _ = execute_call(
            state, current_context, self, kwargs, self.pdl__location
        )
        return result.result()


ClosureBlock.model_rebuild()


def generate(
    pdl_file: str | Path,
    state: InterpreterState | None,
    initial_scope: ScopeType,
    trace_file: str | Path | None,
) -> int:
    """Execute the PDL program defined in `pdl_file`.

    Args:
        pdl_file: Program to execute.
        initial_scope: Environment defining the variables in scope to execute the program.
        state: Initial state of the interpreter.
        trace_file: Indicate if the execution trace must be produced and the file to save it.

    Returns:
        Returns the exit code: `0` for success, `1` for failure
    """
    try:
        prog, loc = parse_file(pdl_file)
        if state is None:
            state = InterpreterState(cwd=Path(pdl_file).parent)
        future_result, _, _, trace = process_prog(state, initial_scope, prog, loc)
        result = future_result.result()
        if not state.yield_background and not state.yield_result:
            print(stringify(result))
        else:
            print()
        if trace_file:
            write_trace(trace_file, trace)
    except PDLParseError as exc:
        print(exc.text, file=sys.stderr)
        return 1
    except PDLRuntimeError as exc:
        # A carried diagnostic is already rendered, location line included, so
        # it is printed as it stands. The test is the carrier class rather than
        # the presence of a `.diagnostic` attribute: the parse-error shims carry
        # one too, and they reach here wrapped in prose that a bare attribute
        # test would silently drop.
        if isinstance(exc.source_exception, PDLImportError):
            message = exc.source_exception.diagnostic.text
        elif exc.loc is None:
            message = exc.message
        else:
            message = get_loc_string(exc.loc) + exc.message
        print(message, file=sys.stderr)
        if trace_file and exc.pdl__trace is not None:
            write_trace(trace_file, exc.pdl__trace)
        return 1
    return 0


def process_prog(
    state: InterpreterState,
    scope: ScopeType,
    prog: Program,
    loc: PdlLocationType = empty_block_location,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]:
    """Execute a PDL program.

    Args:
        state: Initial state of the interpreter.
        scope: Environment defining the variables in scope to execute the program.
        prog: Program to execute.
        loc: Source code location mapping. Defaults to empty_block_location.

    Returns:
        Return the final result, the background messages, the final variable mapping, and the execution trace.

    Raises:
        PDLRuntimeError: If the program raises an error.
    """
    scope = empty_scope | scope

    # Process stdlib
    stdlib_file = Path(__file__).parent / "pdl_stdlib.pdl"
    stdlib, _ = parse_file(stdlib_file)
    _, _, stdlib_dict, _ = process_block(
        state.with_yield_background(False).with_yield_result(False).with_id("stdlib"),
        empty_scope,
        stdlib.root,
        loc,
    )

    stdlib_scope = scope | PdlDict(
        {"stdlib": stdlib_dict, "pdl_usage": state.llm_usage}
    )

    try:
        result, document, final_scope, trace = process_block(
            state, stdlib_scope, block=prog.root, loc=loc
        )
        return result, document, final_scope, trace
    finally:
        # Close all opened files
        for fp in state.opened_files:
            try:
                if not fp.closed:
                    fp.close()
            except Exception:
                # Ignore errors during cleanup
                pass  # nosec B110


def process_block(
    state: InterpreterState, scope: ScopeType, block: BlockType, loc: PdlLocationType
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]:
    result: PdlLazy[Any]
    background: LazyMessages
    trace: BlockType
    try:
        state.current_pdl_context.ref = scope["pdl_context"]  # type: ignore
        if not isinstance(block, Block):
            result, background, scope, trace = process_expression_block(
                state, scope, block, loc
            )

        else:
            result, background, scope, trace = process_advanced_block_timed(
                state, scope, block, loc
            )
    except EOFError as exc:
        raise PDLRuntimeError(
            "EOF",
            loc=loc,
            trace=ErrorBlock(msg="EOF", pdl__location=loc, program=block),
            source_exception=exc,
        ) from exc
    except KeyboardInterrupt as exc:
        raise PDLRuntimeError(
            "Keyboard Interrupt",
            loc=loc,
            trace=ErrorBlock(
                msg="Keyboard Interrupt", pdl__location=loc, program=block
            ),
            source_exception=exc,
        ) from exc
    scope = scope | {"pdl_context": background}
    return result, background, scope, trace


def process_expression_block(
    state: InterpreterState,
    scope: ScopeType,
    block: ExpressionBlock,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]:
    start = time.time_ns()
    state = state.with_id("data")
    block_id = ".".join(state.id_stack)
    try:
        v, expr = process_expr(scope, block, loc)
    except PDLRuntimeExpressionError as exc:
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
            source_exception=exc,
        ) from exc
    result = PdlConst(v)
    background = SingletonContext(
        PdlDict({"role": state.role, "content": result, "pdl__defsite": block_id})
    )
    trace = DataBlock(
        data=expr,
        pdl__result=result,
        pdl__timing=PdlTiming(start_nanos=start, end_nanos=time.time_ns()),
        pdl__id=block_id,
    )
    if state.yield_background:
        yield_background(background)
    if state.yield_result:
        yield_result(result.result(), BlockKind.DATA)
    return result, background, scope, trace


# A start-end time wrapper around `process_advanced_block`
def process_advanced_block_timed(
    state: InterpreterState,
    scope: ScopeType,
    block: AdvancedBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]:
    state = state.with_id(str(block.kind))
    block.pdl__timing = PdlTiming()
    block.pdl__timing.start_nanos = time.time_ns()
    result, background, scope, trace = process_advanced_block(state, scope, block, loc)
    block.pdl__timing.end_nanos = time.time_ns()
    match trace:
        case LitellmModelBlock() | GraniteioModelBlock() | OpenaiModelBlock():
            mode: SerializeMode
            if trace.platform == ModelPlatform.LITELLM:
                mode = SerializeMode.LITELLM
            elif trace.platform == ModelPlatform.GRANITEIO:
                mode = SerializeMode.GRANITEIO
            else:
                mode = SerializeMode.OPENAI
            trace = trace.model_copy(
                update={
                    "pdl__context": lazy_apply(
                        lambda s: s["pdl_context"].serialize(mode),  # TODO
                        scope,
                    ),
                }
            )
    return result, background, scope, trace


def id_with_set_first_use_nanos(timing):
    def identity(result):
        if timing.first_use_nanos is None:
            timing.first_use_nanos = time.time_ns()
        return result

    return identity


def set_error_to_scope_for_retry(
    scope: ScopeType, error, block_id: str | None = ""
) -> ScopeType:
    repeating_same_error = False
    pdl_context: PDLContext | None = scope.get("pdl_context")
    if pdl_context is None:
        return scope
    if pdl_context:
        last_msg = pdl_context[-1]
        last_error = last_msg["content"]  # type: ignore
        if last_error.endswith(error):
            repeating_same_error = True
    if repeating_same_error:
        error = "The previous error occurs multiple times."
    err_msg = {
        "role": "assistant",
        "content": error,
        "pdl__defsite": block_id,
    }
    scope = scope | {
        "pdl_context": DependentContext(
            [pdl_context, SingletonContext(PdlDict(err_msg))]
        )
    }
    return scope


def process_advanced_block(  # noqa: C901
    state: InterpreterState,
    scope: ScopeType,
    block: AdvancedBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]:
    if block.role is not None:
        state = state.with_role(block.role)
    if len(block.defs) > 0:
        scope, defs_trace = process_defs(state, scope, block.defs, loc)
        block = block.model_copy(update={"defs": defs_trace})

    result, background, new_scope, trace = process_advance_block_retry(
        state, scope, block, loc
    )
    if block.def_ is not None:
        var = block.def_
        new_scope = new_scope | PdlDict({var: result})
    new_scope, trace = process_contribute(trace, result, new_scope, loc)
    if ContributeTarget.CONTEXT.value not in block.contribute:
        background = DependentContext([])
    else:
        contribute_value, trace = process_contribute_context(trace, new_scope, loc)
        if contribute_value is not None:
            background = DependentContext([contribute_value])
    if ContributeTarget.RESULT.value not in block.contribute:
        result = PdlConst("")
    return result, background, new_scope, trace


def calculate_retry_delay(
    retry_config: RetryConfiguration,
    trial_idx: int,
) -> float:
    """Calculate the delay before the next retry attempt.

    Args:
        retry_config: The retry configuration object
        trial_idx: The current trial index (0-based)

    Returns:
        The delay in seconds to wait before the next retry
    """
    # Start with the base delay
    delay = retry_config.delay
    assert isinstance(delay, (int, float)), f"delay must be numeric, got {type(delay)}"

    # Apply exponential backoff: delay * (backoff ** trial_idx)
    backoff = retry_config.backoff
    assert isinstance(
        backoff, (int, float)
    ), f"backoff must be numeric, got {type(backoff)}"
    if backoff != 1.0:
        delay = delay * (backoff**trial_idx)

    # Cap at max_delay if specified
    max_delay = retry_config.max_delay
    if max_delay is not None:
        assert isinstance(
            max_delay, (int, float)
        ), f"max_delay must be numeric, got {type(max_delay)}"
        if delay > max_delay:
            delay = float(max_delay)

    # Add jitter
    jitter = retry_config.jitter
    if isinstance(jitter, (list, tuple)) and len(jitter) == 2:
        # Random jitter in range [min, max]
        delay += random.uniform(jitter[0], jitter[1])  # nosec B311
        # [B311:blacklist] Standard pseudo-random generators are not suitable for security/cryptographic purposes.
        # We are not using this random number for cryptography purpose.
    elif isinstance(jitter, (int, float)):
        # Fixed jitter
        delay += jitter

    assert isinstance(
        delay, (int, float)
    ), f"delay must be numeric at return, got {type(delay)}"
    return float(delay)


def resolve_exception_types(
    exceptions: Any,
    loc: PdlLocationType,
) -> tuple[type[BaseException], ...]:
    """Resolve the `exceptions` field of a retry configuration into exception classes.

    Each exception can be given either as a Python exception class or as the
    name of a builtin exception or of a PDL exception.

    Args:
        exceptions: A single exception or a list of exceptions.
        loc: Location of the retry configuration, used to report errors.

    Returns:
        The exception classes to catch, as a tuple suitable for `isinstance`.
    """
    if isinstance(exceptions, (list, tuple)):
        exception_list = list(exceptions)
    else:
        exception_list = [exceptions]

    resolved: list[type[BaseException]] = []
    for exception in exception_list:
        if isinstance(exception, str):
            candidate = getattr(builtins, exception, None)
            if candidate is None:
                candidate = getattr(pdl_ast, exception, None)
        else:
            candidate = exception
        if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
            raise PDLRuntimeError(
                f"Invalid exception in the retry configuration: {exception!r}",
                loc=loc,
            )
        resolved.append(candidate)
    return tuple(resolved)


def exception_matches(
    exc: BaseException,
    exception_types: tuple[type[BaseException], ...],
) -> bool:
    """Check whether an exception, or any exception it wraps, matches `exception_types`.

    PDL wraps the exceptions raised by a program into `PDLRuntimeError`s as they
    propagate, so the exception the user wrote in the retry configuration is
    typically not the one caught here but one of its causes.
    """
    seen: set[int] = set()
    todo: list[BaseException | None] = [exc]
    while todo:
        current = todo.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, exception_types):
            return True
        todo.append(getattr(current, "source_exception", None))
        todo.append(current.__cause__)
    return False


def process_advance_block_retry(  # noqa: C901
    state: InterpreterState,
    scope: ScopeType,
    block: AdvancedBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, AdvancedBlockType]:
    result: PdlLazy[Any] = PdlConst(None)
    background: LazyMessages = DependentContext([])
    new_scope = ScopeType({})
    trace: AdvancedBlockType = EmptyBlock()

    init_state = state
    state = state.with_yield_result(
        state.yield_result
        and ContributeTarget.RESULT.value in block.contribute
        and block.parser is None
    )
    state = state.with_yield_background(
        state.yield_background and context_in_contribute(block)
    )

    # Extract and evaluate retry configuration
    retry_config = None
    evaluated_retry_config = None
    retry_exception_types: tuple[type[BaseException], ...] | None = None
    if isinstance(block.retry, RetryConfiguration):
        # Evaluate each field of the retry configuration
        tries, tries_trace = process_expr(
            scope, block.retry.tries, append(loc, "retry.tries")
        )
        delay, delay_trace = process_expr(
            scope, block.retry.delay, append(loc, "retry.delay")
        )
        # Handle optional max_delay
        if block.retry.max_delay is not None:
            max_delay, max_delay_trace = process_expr(
                scope, block.retry.max_delay, append(loc, "retry.max_delay")
            )
        else:
            max_delay = None
            max_delay_trace = None
        backoff, backoff_trace = process_expr(
            scope, block.retry.backoff, append(loc, "retry.backoff")
        )
        jitter, jitter_trace = process_expr(  # type: ignore[arg-type]
            scope, block.retry.jitter, append(loc, "retry.jitter")
        )
        exceptions, exceptions_trace = process_expr(
            scope, block.retry.exceptions, append(loc, "retry.exceptions")
        )
        # Resolve the exceptions eagerly so that an invalid exception is
        # reported even if the block does not fail
        retry_exception_types = resolve_exception_types(
            exceptions, append(loc, "retry.exceptions")
        )

        # Create evaluated retry configuration for use
        retry_config = RetryConfiguration(
            tries=tries,
            delay=delay,
            max_delay=max_delay,
            backoff=backoff,
            jitter=jitter,  # type: ignore[arg-type] # pyright: ignore
            exceptions=exceptions,  # type: ignore[arg-type] # pyright: ignore
        )

        # Create traced retry configuration for saving in trace
        evaluated_retry_config = RetryConfiguration(
            tries=tries_trace,
            delay=delay_trace,
            max_delay=max_delay_trace,
            backoff=backoff_trace,
            jitter=jitter_trace,  # type: ignore[arg-type] # pyright: ignore
            exceptions=exceptions_trace,  # type: ignore[arg-type] # pyright: ignore
        )

        max_retry = tries
    else:
        max_retry = block.retry if block.retry is not None else 0

    # For infinite retries (-1), we'll use a while loop condition
    infinite_retries = max_retry < 0
    trial_total = max_retry + 1 if not infinite_retries else float("inf")
    score: float = 0.0
    trial_idx = 0
    while trial_idx < trial_total:  # pylint: disable=too-many-nested-blocks
        try:
            if block.retry is not None:
                iteration_state = state.with_id(f"retry{trial_idx}")
            else:
                iteration_state = state
            result, background, new_scope, trace = process_block_body_with_replay(
                iteration_state, scope, block, loc
            )

            # Update trace with evaluated retry configuration if present
            if evaluated_retry_config is not None:
                trace = trace.model_copy(update={"retry": evaluated_retry_config})

            result = lazy_apply(id_with_set_first_use_nanos(block.pdl__timing), result)
            add_done_callback(
                id_with_set_first_use_nanos(block.pdl__timing), background
            )
            trace = trace.model_copy(update={"pdl__result": result})
            if block.parser is not None:
                parser_func = partial(parse_result, block.parser)
                result = lazy_apply(parser_func, result)
                if init_state.yield_result:
                    yield_result(result, block.kind)
            if block.spec is not None and not isinstance(block, FunctionBlock):
                checker = partial(
                    result_with_type_checking,
                    spec=block.spec,
                    msg="Type errors during spec checking:",
                    loc=append(loc, "spec"),
                    trace=trace,
                )
                result = lazy_apply(checker, result)
            if block.fallback is not None:
                result.result()
            if block.expectations != []:
                expectations_satisfied = True
                for req in block.expectations:
                    evaluate = getattr(req, "feedback", None)
                    stdlib_dict: Any = scope["stdlib"]
                    if evaluate is None:
                        evaluate_closure = stdlib_dict["expectations"]["feedback"]
                    else:
                        evaluate_closure, _ = process_expr(scope, evaluate, loc)
                    expectation, _ = process_expr(scope, getattr(req, "expect"), loc)
                    args = {"expectation": expectation, "response": result.result()}
                    keys = evaluate_closure.signature["function"]["parameters"][
                        "properties"
                    ].keys()
                    if "pdl_llm_as_judge" in keys:
                        args = args | {"pdl_llm_as_judge": scope["pdl_llm_as_judge"]}
                    if "pdl_llm_context_transformer" in keys:
                        args = args | {
                            "pdl_llm_context_transformer": scope[
                                "pdl_llm_context_transformer"
                            ]
                        }
                    call_block = CallBlock(
                        call=evaluate_closure,
                        args=args,
                    )
                    feedback, _, _, _ = process_call(
                        iteration_state.with_yield_result(False).with_yield_background(
                            False
                        ),
                        scope,
                        call_block,
                        append(loc, "feedback"),
                    )
                    feedback_result = feedback.result()
                    if feedback_result is not None:
                        if isinstance(feedback_result, str):
                            instruction = feedback_result
                        elif isinstance(feedback_result, float):
                            score = feedback_result
                            instruction = ""
                        elif isinstance(feedback_result, list):
                            score = feedback_result[0]
                            instruction = feedback_result[1]
                        else:
                            instruction = ""

                        if trial_idx < max_retry and instruction != "":
                            scope = scope | {
                                "pdl_context": DependentContext(
                                    [scope["pdl_context"], {"role": "user", "content": instruction}]  # type: ignore
                                )
                            }
                            expectations_satisfied = False

                if (
                    expectations_satisfied is False
                ):  # This is needed, otherwise we don't retry
                    # Apply retry delay if configured and more retries remain
                    if retry_config is not None and trial_idx + 1 < trial_total:
                        delay = calculate_retry_delay(retry_config, trial_idx)
                        if delay > 0:
                            time.sleep(delay)
                    trial_idx += 1
                    continue
            break
        except KeyboardInterrupt as exc:
            raise exc from exc
        except Resample as exc:
            raise exc from exc
        except Exception as exc:
            # Check if the exception matches the configured exception types
            if retry_exception_types is None:
                should_retry_exception = True
            else:
                should_retry_exception = exception_matches(exc, retry_exception_types)

            # Determine if we should retry based on exception match and retry availability
            do_retry = (
                block.retry and trial_idx + 1 < trial_total and should_retry_exception
            )

            if block.fallback is None and not do_retry:
                raise exc from exc
            if do_retry:
                err_msg = traceback.format_exc()
                error = f"An error occurred in a PDL block. Error details: {err_msg}"
                if loc is None:
                    message = error
                else:
                    message = get_loc_string(loc) + error
                print(
                    f"\n\033[0;31m[Retry {trial_idx+1}/{max_retry}] {message}\033[0m\n",
                    file=sys.stderr,
                )
                if block.trace_error_on_retry:
                    scope = set_error_to_scope_for_retry(scope, error, block.pdl__id)

                # Apply retry delay if configured
                if retry_config is not None:
                    delay = calculate_retry_delay(retry_config, trial_idx)
                    if delay > 0:
                        time.sleep(delay)
                trial_idx += 1
                continue
            state = state.with_yield_result(
                init_state.yield_result and ContributeTarget.RESULT in block.contribute
            )
            (
                result,
                background,
                new_scope,
                trace,
            ) = process_block_of(
                block,
                "fallback",
                state,
                scope,
                loc=loc,
            )
            if block.spec is not None and not isinstance(block, FunctionBlock):
                fallback_loc = append(loc, "fallback")
                # Use partial to create a function with fixed arguments
                checker = partial(
                    result_with_type_checking,
                    spec=block.spec,
                    msg="Type errors during spec checking:",
                    loc=append(fallback_loc, "spec"),
                    trace=trace,
                )
                result = lazy_apply(checker, result)
            # The fallback has been executed, no need to try again
            break
        trial_idx += 1
    state.score.ref += score
    return result, background, new_scope, trace


def context_in_contribute(block: AdvancedBlockType) -> bool:
    if ContributeTarget.CONTEXT.value in block.contribute:
        return True
    if get_contribute_context_value(block.contribute) is not None:
        return True
    return False


ResultWithTypeCheckingT = TypeVar("ResultWithTypeCheckingT")


def result_with_type_checking(
    result: ResultWithTypeCheckingT,
    spec,
    msg: str,
    loc: PdlLocationType,
    trace: BlockType,
) -> ResultWithTypeCheckingT:
    errors = type_check_spec(result, spec, loc)
    if len(errors) > 0:
        message = msg + "\n" + "\n".join(errors)
        raise PDLRuntimeError(
            message,
            loc=loc,
            trace=ErrorBlock(msg=message, program=trace),
            fallback=result,
        )
    return result


def process_block_body_with_replay(
    state: InterpreterState,
    scope: ScopeType,
    block: AdvancedBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, AdvancedBlockType]:
    assert state.id_stack is not None
    block_id = ".".join(state.id_stack)
    block.pdl__id = block_id
    if isinstance(block, LeafBlock) and not isinstance(
        block, (FunctionBlock, CallBlock, AggregatorBlock)
    ):
        assert isinstance(block_id, str)
        if block_id not in state.replay:
            try:
                result, background, scope, trace = process_block_body(
                    state, scope, block, loc
                )
                state.replay[block_id] = {"value": result}
            except Resample as exc:
                raise exc from exc
            except Exception as exc:
                state.replay[block_id] = {"exception": exc}
                raise exc from exc
        else:
            match state.replay[block_id]:
                case {"value": v}:
                    result = v
                case {"exception": exc}:
                    raise exc
                case _:
                    raise ValueError(
                        f"Invalid replay value for {block_id}: {state.replay[block_id]}"
                    )
            background = SingletonContext(
                PdlDict({"role": state.role, "content": result})
            )
            if state.yield_result:
                yield_result(result.result(), block.kind)
            if state.yield_background:
                yield_background(background)
            trace = block
            # Special case
            match block:
                case ModelBlock():
                    if block.modelResponse is not None:
                        assert block.pdl__id is not None
                        raw_result = state.replay[block.pdl__id + ".modelResponse"]
                        scope = scope | {block.modelResponse: raw_result}
    else:
        result, background, scope, trace = process_block_body(state, scope, block, loc)
    return result, background, scope, trace


def process_block_body(
    state: InterpreterState,
    scope: ScopeType,
    block: AdvancedBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, AdvancedBlockType]:
    block.pdl__location = loc
    if isinstance(block, LeafBlock):
        return process_leaf_block(state, scope, block, loc)
    assert isinstance(block, StructuredBlock)
    return process_structured_block(state, scope, block, loc)


def process_leaf_block(
    state: InterpreterState,
    scope: ScopeType,
    block: LeafBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, AdvancedBlockType]:
    result: Any
    background: LazyMessages
    trace: AdvancedBlockType
    match block:
        case ModelBlock():
            result, background, scope, trace = process_call_model(
                state, scope, block, loc
            )
        case ArgsBlock() | CodeBlock():
            result, background, scope, trace = process_call_code(
                state, scope, block, loc
            )
            if state.yield_result:
                yield_result(result.result(), block.kind)
            if state.yield_background:
                yield_background(background)
        case GetBlock(get=var):
            block.pdl__location = append(loc, "get")
            try:
                result = PdlConst(get_var(var, scope, block.pdl__location))
            except PDLRuntimeExpressionError as exc:
                raise PDLRuntimeError(
                    exc.message,
                    loc=exc.loc or loc,
                    trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
                    source_exception=exc,
                ) from exc
            background = SingletonContext(
                PdlDict({"role": state.role, "content": result})
            )
            trace = block.model_copy()
            if state.yield_result:
                yield_result(result.result(), block.kind)
            if state.yield_background:
                yield_background(background)
        case DataBlock(data=v):
            block.pdl__location = append(loc, "data")
            if block.raw:
                result = PdlConst(v)
                trace = block.model_copy()
            else:
                v, trace = process_expr_of(block, "data", scope, loc)
                result = PdlConst(v)
            background = SingletonContext(
                PdlDict({"role": state.role, "content": result})
            )
            if state.yield_result:
                yield_result(result.result(), block.kind)
            if state.yield_background:
                yield_background(background)
        case MessageBlock():
            content, _, _, trace = process_block_of(
                block,
                "content",
                state,
                scope,
                loc,
            )
            message = {
                "role": state.role,
                "content": content,
                "pdl__defsite": block.pdl__id,
            }
            if block.name is not None:
                name, block = process_expr_of(block, "name", scope, loc)
                message["name"] = name
            if block.tool_call_id is not None:
                tool_call_id, block = process_expr_of(block, "tool_call_id", scope, loc)
                message["tool_call_id"] = tool_call_id
            if block.tool_calls is not None:
                tool_calls, block = process_expr_of(block, "tool_calls", scope, loc)
                message["tool_calls"] = tool_calls
            result = PdlConst(SingletonContext(PdlDict(message)))
            background = SingletonContext(PdlDict(message))
        case ReadBlock():
            result, background, scope, trace = process_input(state, scope, block, loc)
            if state.yield_result:
                yield_result(result.result(), block.kind)
            if state.yield_background:
                yield_background(background)

        case AggregatorBlock():
            result, background, scope, trace = process_aggregator(
                state, scope, block, loc
            )

        case FunctionBlock():
            closure = ClosureBlock(  # pyright: ignore
                description=block.description,
                spec=block.spec,
                defs=block.defs,
                def_=block.def_,  # pyright: ignore
                contribute=block.contribute,
                parser=block.parser,
                fallback=block.fallback,
                retry=block.retry,
                trace_error_on_retry=block.trace_error_on_retry,
                role=block.role,
                function=block.function,
                return_=block.return_,  # pyright: ignore
                pdl__location=loc,
                pdl__scope=None,
                pdl__state=state,
            )
            if block.def_ is not None:
                scope = scope | {block.def_: closure}
            closure.pdl__scope = scope
            _signature: dict[str, Any] = {}
            if block.def_ is not None:
                _signature["name"] = block.def_
            if block.description is not None:
                _signature["description"] = block.description
            if block.function is not None:
                _signature["parameters"] = get_json_schema(block.function, False) or {}
            else:
                _signature["parameters"] = {}
            signature: dict[str, Any] = {"type": "function", "function": _signature}
            closure.signature = signature
            result = PdlConst(closure)
            background = DependentContext([])
            trace = closure.model_copy(update={})
        case CallBlock():
            result, background, scope, trace = process_call(state, scope, block, loc)
        case FactorBlock():
            if state.ignore_factor:
                weight = 0.0
                trace = block.model_copy()
            else:
                weight, trace = process_expr_of(
                    block, "factor", scope, append(loc, "factor")
                )
            trace.pdl__score = weight
            state.score.ref += weight
            result = PdlConst("")
            background = DependentContext([])
            assert block.pdl__id is not None
            state.replay[block.pdl__id] = {"value": result}
            if state.with_resample and block.resample:
                raise Resample(state.replay, state.score.ref)
        case EmptyBlock():
            result = PdlConst("")
            background = DependentContext([])
            trace = block.model_copy()

        case _:
            assert False, f"Internal error: unsupported type ({type(block)})"
    return result, background, scope, trace


def process_structured_block(
    state: InterpreterState,
    scope: ScopeType,
    block: StructuredBlockType,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, AdvancedBlockType]:
    scope_init = scope
    result: Any
    background: LazyMessages
    trace: AdvancedBlockType
    match block:
        case SequenceBlock():
            result, background, scope, trace = process_blocks_of(
                block,
                "sequence",
                block.join,
                state,
                scope,
                loc,
            )
        case TextBlock():
            result, background, scope, trace = process_blocks_of(
                block,
                "text",
                JoinText(),
                state,
                scope,
                loc,
            )
        case LastOfBlock():
            result, background, scope, trace = process_blocks_of(
                block,
                "lastOf",
                JoinLastOf(as_="lastOf"),  # pyright: ignore
                state,
                scope,
                loc,
            )
        case ArrayBlock():
            result, background, scope, trace = process_blocks_of(
                block,
                "array",
                JoinArray(as_="array"),  # pyright: ignore
                state,
                scope,
                loc,
            )
        case ObjectBlock():
            iteration_state = state.with_yield_result(False)
            if isinstance(block.object, dict):
                background = DependentContext([])
                values = []
                values_trace = []
                try:
                    pdl_context_init = scope_init.data["pdl_context"]
                    obj_loc = append(loc, "object")
                    for k, value_blocks in block.object.items():
                        context = IndependentEnum.DEPENDENT
                        if isinstance(value_blocks, StructuredBlock):
                            context = value_blocks.context
                        value, value_background, scope, value_trace = process_blocks(
                            JoinLastOf(as_="lastOf"),  # pyright: ignore
                            context,
                            iteration_state.with_id(k),
                            scope,
                            value_blocks,
                            block.kind,
                            append(obj_loc, k),
                        )
                        if block.context == IndependentEnum.DEPENDENT:
                            background = DependentContext(
                                [background, value_background]
                            )
                        else:
                            background = IndependentContext(
                                [background, value_background]
                            )
                        if (
                            block.context is IndependentEnum.INDEPENDENT
                        ):  # reset pdl_context
                            scope = scope | {"pdl_context": pdl_context_init}
                        values.append(value)
                        values_trace.append(value_trace)
                except PDLRuntimeProcessBlocksError as exc:
                    obj = dict(zip(block.object.keys(), exc.blocks))
                    trace = block.model_copy(update={"object": obj})
                    raise PDLRuntimeError(
                        exc.message,
                        loc=exc.loc or loc,
                        trace=trace,
                        source_exception=exc,
                    ) from exc
                result = PdlDict(dict(zip(block.object.keys(), values)))
                object_trace = dict(zip(block.object.keys(), values_trace))
                trace = block.model_copy(update={"object": object_trace})
            else:
                result, background, scope, trace = process_blocks_of(
                    block,
                    "object",
                    JoinObject(as_="object"),  # pyright: ignore
                    iteration_state,
                    scope,
                    loc,
                )
            if state.yield_result and not iteration_state.yield_result:
                yield_result(result, block.kind)
        case IfBlock():
            b, if_trace = process_condition_of(block, "condition", scope, loc, "if")
            if b:
                result, background, scope, trace = process_block_of(
                    block, "then", state, scope, loc
                )
            elif block.else_ is not None:
                result, background, scope, trace = process_block_of(
                    block, "else_", state, scope, loc, "else"
                )
            else:
                result = PdlConst("")
                background = DependentContext([])
                trace = block
            trace = trace.model_copy(
                update={
                    "condition": if_trace,
                }
            )
        case MatchBlock():
            match_v, block = process_expr_of(block, "match_", scope, loc, "match")
            cases = []
            matched = False
            result = PdlConst("")
            background = DependentContext([])
            for i, match_case in enumerate(block.with_):
                if matched:
                    cases.append(match_case)
                    continue
                loc_i = append(loc, "[" + str(i) + "]")
                if "case" in match_case.model_fields_set:
                    new_scope = is_matching(match_v, match_case.case, scope)
                    if new_scope is None:
                        match_case = match_case.model_copy(
                            update={"pdl__case_result": False, "pdl__matched": False}
                        )
                        cases.append(match_case)
                        continue
                    match_case = match_case.model_copy(
                        update={"pdl__case_result": True}
                    )
                else:
                    new_scope = scope
                b = True
                if "if_" in match_case.model_fields_set and match_case.if_ is not None:
                    loc_if = append(loc_i, "if")
                    try:
                        b, if_trace = process_expr(new_scope, match_case.if_, loc_if)
                        match_case = match_case.model_copy(update={"if_": if_trace})
                    except PDLRuntimeExpressionError as exc:
                        cases.append(match_case)
                        block.with_ = cases
                        raise PDLRuntimeError(
                            exc.message,
                            loc=exc.loc or loc_if,
                            trace=ErrorBlock(
                                msg=exc.message, pdl__location=loc, program=block
                            ),
                            source_exception=exc,
                        ) from exc
                if not b:
                    match_case.pdl__if_result = False
                    match_case.pdl__matched = False
                    cases.append(match_case)
                    continue
                match_case.pdl__if_result = True
                match_case.pdl__matched = True
                matched = True
                try:
                    result, background, scope, then_trace = process_block(
                        state,
                        new_scope,
                        match_case.then,
                        append(loc_i, "then"),
                    )
                except PDLRuntimeError as exc:
                    match_case_trace = match_case.model_copy(
                        update={"then": exc.pdl__trace}
                    )
                    cases.append(match_case_trace)
                    block.with_ = cases
                    raise PDLRuntimeError(
                        exc.message,
                        loc=exc.loc or loc,
                        trace=block,
                        source_exception=exc,
                    ) from exc
                match_case_trace = match_case.model_copy(update={"then": then_trace})
                cases.append(match_case_trace)
            block.with_ = cases
            trace = block
        case RepeatBlock():
            results: list[PdlLazy[Any]] = []
            background = DependentContext([])
            iter_trace: list[BlockType] = []
            pdl_context_init = scope_init.data["pdl_context"]
            iteration_state_init = state.with_yield_result(
                state.yield_result and isinstance(block.join, JoinText)
            )
            iteration_state = iteration_state_init
            block, items, length = _evaluate_for_field(scope, block, loc)
            block, max_iterations = _evaluate_max_iterations_field(scope, block, loc)
            block = _evaluate_join_field(scope, block, loc)
            repeat_loc = append(loc, "repeat")
            iidx = 0
            try:
                first = True
                saved_background: LazyMessages = DependentContext([])
                while True:
                    if block.index is not None:
                        scope = scope | {block.index: iidx}
                    if max_iterations is not None and iidx >= max_iterations:
                        break
                    if length is not None and iidx >= length:
                        break
                    stay, _ = process_condition_of(block, "while_", scope, loc, "while")
                    if not stay:
                        break
                    iteration_state = iteration_state_init.with_iter(iidx)
                    if first:
                        first = False
                    elif isinstance(block.join, JoinText):
                        join_string = block.join.with_
                        if iteration_state.yield_result:
                            yield_result(join_string, block.kind)
                        if iteration_state.yield_background:
                            yield_background(
                                [
                                    {
                                        "role": block.role,
                                        "content": join_string,
                                        "pdl__defsite": block.pdl__id,
                                    }
                                ]
                            )
                    scope = scope | {
                        "pdl_context": DependentContext([pdl_context_init, background])
                    }
                    if items is not None:
                        for k, lst in items.items():
                            scope = scope | {k: lst[iidx]}
                    (
                        iteration_result,
                        iteration_background,
                        scope,
                        body_trace,
                    ) = process_block(
                        iteration_state,
                        scope,
                        block.repeat,
                        repeat_loc,
                    )
                    if block.context is IndependentEnum.DEPENDENT:
                        saved_background = DependentContext(
                            [saved_background, iteration_background]
                        )
                        background = saved_background
                    else:
                        saved_background = IndependentContext(
                            [saved_background, iteration_background]
                        )
                    results.append(iteration_result)
                    iter_trace.append(body_trace)
                    stop, _ = process_condition_of(block, "until", scope, loc)
                    iidx = iidx + 1
                    if stop:
                        break
            except PDLRuntimeError as exc:
                iter_trace.append(exc.pdl__trace)
                trace = block.model_copy(update={"pdl__trace": iter_trace})
                raise PDLRuntimeError(
                    exc.message,
                    loc=exc.loc or repeat_loc,
                    trace=trace,
                    source_exception=exc,
                ) from exc
            result = combine_results(block.join, results)
            if block.context is IndependentEnum.INDEPENDENT:
                background = saved_background
            if state.yield_result and not iteration_state.yield_result:
                yield_result(result.result(), block.kind)
            trace = block.model_copy(update={"pdl__trace": iter_trace})
        case MapBlock():
            background = DependentContext([])
            iteration_state = state.with_yield_result(False)
            block, items, length = _evaluate_for_field(scope, block, loc)
            block, max_iterations = _evaluate_max_iterations_field(scope, block, loc)
            block = _evaluate_join_field(scope, block, loc)
            map_loc = append(loc, "map")
            try:
                if max_iterations is not None:
                    index_iterator: Any = range(max_iterations)
                else:
                    index_iterator = count()
                if items is not None and length is not None:
                    items_iterator = (
                        {k: elems[i] for k, elems in items.items()}
                        for i in range(length)
                    )
                else:
                    items_iterator = ({} for _ in count())

                def loop_body(iidx, items):
                    iteration_scope = scope_init
                    if block.index is not None:
                        iteration_scope = iteration_scope | {block.index: iidx}
                    iteration_scope = iteration_scope | items
                    return process_block(
                        iteration_state.with_iter(iidx),
                        iteration_scope,
                        block.map,
                        map_loc,
                    )

                map_output: Iterable[
                    Tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]
                ]
                if block.maxWorkers == 0:
                    map_output = map(  # pylint: disable=bad-builtin
                        loop_body, index_iterator, items_iterator
                    )
                else:
                    with ThreadPoolExecutor(block.maxWorkers) as executor:
                        map_output = executor.map(
                            loop_body, index_iterator, items_iterator
                        )
                results, _, _, traces = _split_map_output(map_output)
                # saved_background = IndependentContext(backgrounds)
            except PDLRuntimeError as exc:
                traces = [exc.pdl__trace]  # type: ignore
                trace = block.model_copy(update={"pdl__trace": traces})
                raise PDLRuntimeError(
                    exc.message,
                    loc=exc.loc or map_loc,
                    trace=trace,
                    source_exception=exc,
                ) from exc
            result = combine_results(block.join, results)
            # background = saved_background  # commented because the block do not contribute to the background
            if state.yield_result and not iteration_state.yield_result:
                yield_result(result.result(), block.kind)
            trace = block.model_copy(update={"pdl__trace": traces})
        case IncludeBlock():
            result, background, scope, trace = process_include(state, scope, block, loc)

        case ImportBlock():
            result, background, scope, trace = process_import(state, scope, block, loc)

        case _:
            assert False, f"Internal error: unsupported type ({type(block)})"
    return result, background, scope, trace


def _split_map_output(
    map_output: Iterable[Tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType]],
) -> Tuple[list[PdlLazy[Any]], list[LazyMessages], list[ScopeType], list[BlockType]]:
    results = []
    backgrounds = []
    scopes = []
    traces = []
    for result, background, scope, trace in map_output:
        results.append(result)
        backgrounds.append(background)
        scopes.append(scope)
        traces.append(trace)
    return results, backgrounds, scopes, traces


BlockTVarEvalFor = TypeVar("BlockTVarEvalFor", bound=RepeatBlock | MapBlock)


def _evaluate_for_field(
    scope: ScopeType, block: BlockTVarEvalFor, loc: PdlLocationType
) -> Tuple[BlockTVarEvalFor, dict[str, list] | None, int | None]:
    if block.for_ is None:
        items_res = None
        length = None
    else:
        items, block = process_expr_of(block, "for_", scope, loc, "for")
        lengths = []
        items_res = {}
        for idx, lst in items.items():
            if not isinstance(lst, Iterable):
                msg = f"Values inside the For block must be lists but got {type(lst)}."
                lst_loc = append(
                    append(block.pdl__location or empty_block_location, "for"),
                    idx,
                )
                raise PDLRuntimeError(
                    message=msg,
                    loc=lst_loc,
                    trace=ErrorBlock(msg=msg, pdl__location=lst_loc, program=block),
                    fallback=[],
                )
            lst = list(lst)
            items_res[idx] = lst
            lengths.append(len(lst))
        if len(set(lengths)) != 1:  # Not all the lists are of the same length
            msg = "Lists inside the For block must be of the same length."
            for_loc = append(block.pdl__location or empty_block_location, "for")
            raise PDLRuntimeError(
                msg,
                loc=for_loc,
                trace=ErrorBlock(msg=msg, pdl__location=for_loc, program=block),
                fallback=[],
            )
        length = lengths[0]
    return block, items_res, length


BlockTVarEvalMaxIter = TypeVar("BlockTVarEvalMaxIter", bound=RepeatBlock | MapBlock)


def _evaluate_max_iterations_field(
    scope: ScopeType, block: BlockTVarEvalMaxIter, loc: PdlLocationType
) -> Tuple[BlockTVarEvalMaxIter, int | None]:
    if block.maxIterations is None:
        max_iterations = None
    else:
        max_iterations, block = process_expr_of(block, "maxIterations", scope, loc)
    return block, max_iterations


BlockTVarEvalJoin = TypeVar("BlockTVarEvalJoin", bound=RepeatBlock | MapBlock)


def _evaluate_join_field(
    scope: ScopeType, block: BlockTVarEvalJoin, loc: PdlLocationType
) -> BlockTVarEvalJoin:
    match block.join:
        case JoinText() | JoinArray() | JoinObject() | JoinLastOf():
            pass
        case JoinReduce():
            loc = append(loc, "reduce")
            _, expr = process_expr(scope, block.join.reduce, loc)
            join = block.join.model_copy(update={"reduce": expr})
            block = block.model_copy(update={"join": join})
    return block


def is_matching(  # pylint: disable=too-many-return-statements
    value: Any, pattern: PatternType, scope: ScopeType
) -> ScopeType | None:
    """The function test if `value` matches the pattern `match` and returns the scope updated with the new variables bound by the matching.

    Args:
        value: Value to match.
        pattern: Pattern to match.
        scope: Current variable binding.

    Returns:
        The function returns `None` if the value is not matched by the pattern and a copy of the updated scope otherwise.
    """
    new_scope: ScopeType | None
    match pattern:
        case OrPattern():
            new_scope = None
            for p in pattern.anyOf:
                new_scope = is_matching(value, p, scope)
                if new_scope:
                    break
        case ArrayPattern():
            if not isinstance(value, Sequence) or len(pattern.array) != len(value):
                return None
            new_scope = scope
            for v, p in zip(value, pattern.array):
                new_scope = is_matching(v, p, new_scope)
                if new_scope is None:
                    return None
        case ObjectPattern():
            if not isinstance(value, dict):
                return None
            new_scope = scope
            for k, p in pattern.object.items():
                if k not in value:
                    return None
                new_scope = is_matching(value[k], p, new_scope)
                if new_scope is None:
                    return None
        case AnyPattern():
            new_scope = scope
        case _:
            assert not isinstance(pattern, Pattern)
            if value != pattern:
                return None
            new_scope = scope
    if new_scope is None:
        return None
    if isinstance(pattern, Pattern) and pattern.def_ is not None:
        new_scope = new_scope | {pattern.def_: value}
    return new_scope


def process_defs(
    state: InterpreterState,
    scope: ScopeType,
    defs: dict[str, BlockType],
    loc: PdlLocationType,
) -> tuple[ScopeType, dict[str, BlockType]]:
    defs_trace: dict[str, BlockType] = {}
    defloc = append(loc, "defs")
    state = state.with_id("defs")
    state = state.with_yield_result(False)
    state = state.with_yield_background(False)
    for x, block in defs.items():
        newloc = append(defloc, x)
        if isinstance(block, FunctionBlock) and block.def_ is None:
            block = block.model_copy(update={"def_": x})
        result, _, _, block_trace = process_block(
            state.with_id(x), scope, block, newloc
        )
        scope = scope | PdlDict({x: result})
        defs_trace[x] = block_trace
    return scope, defs_trace


BlockTypeTVarProcessBlockOf = TypeVar(
    "BlockTypeTVarProcessBlockOf", bound=AdvancedBlockType
)


def process_block_of(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    block: BlockTypeTVarProcessBlockOf,
    field: str,
    state: InterpreterState,
    scope: ScopeType,
    loc: PdlLocationType,
    field_alias: str | None = None,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockTypeTVarProcessBlockOf]:
    try:
        result, background, scope, child_trace = process_block(
            state.with_id(field),
            scope,
            getattr(block, field),
            append(loc, field_alias or field),
        )
    except PDLRuntimeError as exc:
        trace = block.model_copy(update={field: exc.pdl__trace})
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=trace,
            source_exception=exc,
        ) from exc
    trace = block.model_copy(update={field: child_trace})
    return result, background, scope, trace


BlockTypeTVarProcessBlocksOf = TypeVar(
    "BlockTypeTVarProcessBlocksOf", bound=AdvancedBlockType
)


def process_blocks_of(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    block: BlockTypeTVarProcessBlocksOf,
    field: str,
    join_type: JoinType,
    state: InterpreterState,
    scope: ScopeType,
    loc: PdlLocationType,
    field_alias: str | None = None,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockTypeTVarProcessBlocksOf]:
    try:
        context: IndependentEnum = IndependentEnum.DEPENDENT
        if isinstance(block, StructuredBlock):
            context = block.context
        result, background, scope, blocks = process_blocks(
            join_type,
            context,
            state,
            scope,
            getattr(block, field),
            block.kind,
            append(loc, field_alias or field),
        )
    except PDLRuntimeProcessBlocksError as exc:
        trace = block.model_copy(update={field: exc.blocks})
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=trace,
            source_exception=exc,
        ) from exc
    trace = block.model_copy(update={field: blocks})
    return result, background, scope, trace


def process_blocks(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    join_type: JoinType,
    context: IndependentEnum,
    state: InterpreterState,
    scope: ScopeType,
    blocks: BlockType | list[BlockType],
    block_kind: BlockKind,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Any], LazyMessages, ScopeType, BlockType | list[BlockType]]:
    result: Any
    background: LazyMessages
    trace: BlockType | list[BlockType]
    results = []
    if not isinstance(blocks, str) and isinstance(blocks, Sequence):
        # Is a list of blocks
        iteration_state_init = state.with_yield_result(
            state.yield_result and isinstance(join_type, (JoinLastOf, JoinText))
        )
        iteration_state = iteration_state_init
        new_loc = None
        background = DependentContext([])
        saved_background: LazyMessages = DependentContext([])
        trace = []
        pdl_context_init: LazyMessages = scope.data["pdl_context"]
        is_last_of = isinstance(join_type, JoinLastOf)
        try:
            for i, block in enumerate(blocks):
                iteration_state = iteration_state_init.with_iter(i)
                scope = scope | {
                    "pdl_context": DependentContext([pdl_context_init, background])
                }
                new_loc = append(loc, "[" + str(i) + "]")
                if is_last_of and state.yield_result:
                    iteration_state = state.with_yield_result(i + 1 == len(blocks))
                (
                    iteration_result,
                    iteration_background,
                    scope,
                    t,
                ) = process_block(iteration_state, scope, block, new_loc)
                results.append(iteration_result)
                if context == IndependentEnum.DEPENDENT:
                    saved_background = DependentContext(
                        [saved_background, iteration_background]
                    )
                else:
                    saved_background = IndependentContext(
                        [saved_background, iteration_background]
                    )

                if context == IndependentEnum.DEPENDENT:
                    background = saved_background
                trace.append(t)  # type: ignore
            if context == IndependentEnum.INDEPENDENT:
                background = saved_background
        except PDLRuntimeError as exc:
            trace.append(exc.pdl__trace)  # type: ignore
            raise PDLRuntimeProcessBlocksError(
                message=exc.message,
                blocks=trace,
                loc=exc.loc or new_loc,
                source_exception=exc,
            ) from exc
    else:
        iteration_state = state.with_yield_result(
            state.yield_result and not isinstance(join_type, JoinArray)
        )
        block_result, background, scope, trace = process_block(
            iteration_state, scope, blocks, loc
        )
        results.append(block_result)
    result = combine_results(join_type, results)
    if state.yield_result and not iteration_state.yield_result:
        yield_result(result, block_kind)
    return result, background, scope, trace


def combine_results(join_type: JoinType, results: list[PdlLazy[Any]]):
    result: Any
    match join_type:
        case JoinArray():
            result = PdlList(results)
        case JoinObject():
            result = PdlDict({})
            for d in results:
                result = result | d
        case JoinLastOf():
            if len(results) > 0:
                result = results[-1]
            else:
                result = None
        case JoinText():
            join_str = join_type.with_
            result = lazy_apply(
                (lambda _: join_str.join([stringify(r.result()) for r in results])),
                PdlConst(()),
            )
        case JoinReduce():
            result = lazy_apply(
                (
                    lambda _: reduce(
                        value_of_expr(join_type.reduce),
                        [r.result() for r in results],
                    )
                ),
                PdlConst(()),
            )
        case _:
            assert False
    return result


BlockTypeTVarProcessContributeOld = TypeVar(
    "BlockTypeTVarProcessContributeOld", bound=AdvancedBlockType
)


def process_contribute_context(
    block: BlockTypeTVarProcessContributeOld, scope: ScopeType, loc: PdlLocationType
) -> tuple[Any, BlockTypeTVarProcessContributeOld]:
    result: list[ContributeElement]
    value_trace: LocalizedExpression[list[ContributeElement]]
    value = get_contribute_context_value(block.contribute)
    if value is None:
        return None, block
    loc = append(loc, "contribute")
    try:
        result, value_trace = process_expr(scope, value, loc)
    except PDLRuntimeExpressionError as exc:
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
            source_exception=exc,
        ) from exc
    replace = replace_contribute_value(
        block.contribute, ContributeValue(value=value_trace)
    )
    trace = block.model_copy(update={"contribute": replace})
    return result, trace


BlockTypeTVarProcessContribute = TypeVar(
    "BlockTypeTVarProcessContribute", bound=AdvancedBlockType
)


def process_contribute(
    block: BlockTypeTVarProcessContribute,
    result: Any,
    scope: ScopeType,
    loc: PdlLocationType,
) -> tuple[ScopeType, BlockTypeTVarProcessContribute]:
    loc = append(loc, "contribute")
    contribute = []
    for i, elem in enumerate(block.contribute):
        scope, elem = process_contribution(
            block, elem, result, scope, append(loc, "[" + str(i) + "]")
        )
        contribute.append(elem)
    trace = block.model_copy(update={"contribute": contribute})
    return scope, trace


_CONTRIBUTE_RULE = (
    "A `contribute` entry is either `result` or `context`, or a mapping with a "
    "single key naming where to contribute."
)


# Keyed on the exact type rather than tested with isinstance, so that `bool`
# resolves to "a boolean" instead of being caught by its `int` base class.
_PDL_TYPE_NAMES: dict[type, str] = {
    bool: "a boolean",
    int: "a number",
    float: "a number",
    str: "a string",
    list: "a list",
    dict: "a mapping",
    type(None): "null",
}


def _pdl_type_name(value: Any) -> str:
    """Name a value's type the way the PDL documentation does."""
    return _PDL_TYPE_NAMES.get(type(value), f"a {type(value).__name__}")


def _bad_contribution_message(elem: Any) -> str:
    """Explain why a `contribute` entry is not usable.

    Reports the keys the user wrote rather than the parsed value: by this point
    a mapping's values are `ContributeValue` models, whose repr is PDL's
    internals rather than anything the user typed.
    """
    keys = list(elem) if isinstance(elem, dict) else []
    if not isinstance(elem, dict):
        headline = (
            f"contribute entry must be a string or a mapping, "
            f"but got {_pdl_type_name(elem)}"
        )
        evidence = f"This one is {elem!r}."
        suggestion = ""
    elif not keys:
        headline = "contribute entry is an empty mapping"
        evidence = "A mapping entry needs exactly one key; this one has none."
        suggestion = ""
    else:
        headline = f"contribute entry has {len(keys)} keys, expected exactly 1"
        named = ", ".join(f"`{k}`" for k in keys)
        evidence = f"This one maps {named}."
        # Two list items written at one indent level collapse into a single
        # mapping, which is the usual way to arrive here. Name the user's own
        # keys: a generic example would point at the wrong shape, since
        # `result` and `context` are spelled as bare strings, not mappings.
        items = " then ".join(f"`- {k}:`" for k in keys)
        suggestion = f"\n\n  help: give each key its own entry in the list: {items}"
    body = textwrap.fill(
        f"{_CONTRIBUTE_RULE} {evidence}",
        width=76,
        initial_indent="  ",
        subsequent_indent="  ",
    )
    return f"{headline}\n\n{body}{suggestion}"


def process_contribution(
    block: AdvancedBlockType,
    elem: ContributeElement,
    result: Any,
    scope: ScopeType,
    loc: PdlLocationType,
) -> tuple[ScopeType, ContributeElement]:
    target: ContributeTarget | str
    match elem:
        case ContributeTarget.RESULT | "result" | ContributeTarget.CONTEXT | "context":
            return scope, elem
        case ContributeTarget() | str():
            target = elem
        case dict():
            if len(elem) != 1:
                msg = _bad_contribution_message(elem)
                raise PDLRuntimeError(
                    msg,
                    loc=loc,
                    trace=ErrorBlock(msg=msg, pdl__location=loc, program=block),
                    fallback=[],
                )
            target, contribute_value = list(elem.items()).pop()
            try:
                result, value_trace = process_expr(scope, contribute_value.value, loc)
            except PDLRuntimeExpressionError as exc:
                raise PDLRuntimeError(
                    exc.message,
                    loc=exc.loc or loc,
                    trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
                    source_exception=exc,
                ) from exc
            elem = {target: ContributeValue(value=value_trace)}
        case _:
            msg = _bad_contribution_message(elem)
            raise PDLRuntimeError(
                msg,
                loc=loc,
                trace=ErrorBlock(msg=msg, pdl__location=loc, program=block),
                fallback=[],
            )
    aggregator = get_contribute_aggregator(block, target, scope, loc)
    aggregator = aggregator.contribute(result, block.role, loc, block)
    scope = scope | {target: aggregator}
    return scope, elem


BlockTypeTVarProcessExprOf = TypeVar(
    "BlockTypeTVarProcessExprOf", bound=AdvancedBlockType
)


def process_expr_of(
    block: BlockTypeTVarProcessExprOf,
    field: str,
    scope: ScopeType,
    loc: PdlLocationType,
    field_alias: str | None = None,
) -> tuple[Any, BlockTypeTVarProcessExprOf]:
    result: Any
    expr_trace: LocalizedExpression[Any]
    expr = getattr(block, field)
    loc = append(loc, field_alias or field)
    try:
        result, expr_trace = process_expr(scope, expr, loc)
    except PDLRuntimeExpressionError as exc:
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
            source_exception=exc,
        ) from exc
    trace = block.model_copy(update={field: expr_trace})
    return result, trace


def process_condition_of(
    block: AdvancedBlockType,
    field: str,
    scope: ScopeType,
    loc: PdlLocationType,
    field_alias: str | None = None,
) -> tuple[bool, LocalizedExpression[bool]]:
    result: bool
    expr_trace: LocalizedExpression[bool]
    expr = getattr(block, field)
    loc = append(loc, field_alias or field)
    try:
        result, expr_trace = process_expr(scope, expr, loc)
    except PDLRuntimeExpressionError as exc:
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or loc,
            trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
            source_exception=exc,
        ) from exc
    return result, expr_trace


EXPR_START_STRING = "${"
EXPR_END_STRING = "}"

ProcessExprT = TypeVar("ProcessExprT")


def process_expr(  # pylint: disable=too-many-return-statements
    scope: ScopeType, expr: ExpressionType[ProcessExprT], loc: PdlLocationType
) -> tuple[ProcessExprT, LocalizedExpression[ProcessExprT]]:
    result: ProcessExprT
    if isinstance(expr, LocalizedExpression):
        result = _process_expr(scope, expr.pdl__expr, loc)
        trace = expr.model_copy(update={"pdl__result": result})
    else:
        result = _process_expr(scope, expr, loc)
        trace = LocalizedExpression(
            pdl__expr=expr, pdl__result=result, pdl__location=loc
        )
    return (result, trace)


_ProcessExprT = TypeVar("_ProcessExprT")


def _process_expr(  # pylint: disable=too-many-return-statements
    scope: ScopeType, expr: ExpressionType[_ProcessExprT], loc: PdlLocationType
) -> _ProcessExprT:
    result: _ProcessExprT
    if isinstance(expr, LocalizedExpression):
        return _process_expr(scope, expr.pdl__expr, loc)
    if isinstance(expr, str):
        try:
            env = Environment(  # nosec B701
                # [B701:jinja2_autoescape_false] By default, jinja2 sets autoescape to False. Consider using autoescape=True or use the select_autoescape function to mitigate XSS vulnerabilities.
                # This is safe because autoescape is not needed since we do not generate HTML
                block_start_string="{%%%%%PDL%%%%%%%%%%",
                block_end_string="%%%%%PDL%%%%%%%%%%}",
                variable_start_string=EXPR_START_STRING,
                variable_end_string=EXPR_END_STRING,
                undefined=StrictUndefined,
            )
            expr_ast = env.parse(expr)
            if len(expr_ast.body) == 1:
                expr_ast_nodes = getattr(expr_ast.body[0], "nodes", [])
            else:
                expr_ast_nodes = []
            if len(expr_ast_nodes) == 1:
                # `expr` is either a single jinja expression or a string without expression
                if expr.startswith(EXPR_START_STRING) and expr.endswith(
                    EXPR_END_STRING
                ):
                    # `expr` has the shape `${ ... }`: it is a single jinja expression
                    free_vars = meta.find_undeclared_variables(expr_ast)
                    result = env.compile_expression(  # pyright: ignore
                        expr[2:-1], undefined_to_none=False
                    )({x: scope[x] for x in free_vars if x in scope})
                    if isinstance(result, Undefined):
                        raise UndefinedError(str(result))
                    return result
                if isinstance(expr_ast_nodes[0], TemplateData):
                    # `expr` is a string that do not include jinja expression
                    return expr  # type: ignore
            # `expr` is not a single jinja expression
            template = Template(
                expr,
                keep_trailing_newline=True,
                block_start_string="{%%%%%PDL%%%%%%%%%%",
                block_end_string="%%%%%PDL%%%%%%%%%%}",
                variable_start_string=EXPR_START_STRING,
                variable_end_string=EXPR_END_STRING,
                # comment_start_string="",
                # comment_end_string="",
                autoescape=False,
                undefined=StrictUndefined,
            )
            free_vars = meta.find_undeclared_variables(expr_ast)
            result = template.render(
                {x: scope[x] for x in free_vars if x in scope}
            )  # pyright: ignore
            return result
        except KeyboardInterrupt as exc:
            raise exc from exc
        except PDLRuntimeError as exc:
            raise exc from exc
        except TemplateSyntaxError as exc:
            raise PDLRuntimeExpressionError(
                f"Syntax error in {expr}: {exc}", loc, source_exception=exc
            ) from exc
        except Exception as exc:
            raise PDLRuntimeExpressionError(
                f"Error during the evaluation of {expr}: {exc}",
                loc,
                source_exception=exc,
            ) from exc

    if isinstance(expr, list):
        result_list: list[Any] = []
        for index, x in enumerate(expr):
            res: Any = _process_expr(scope, x, append(loc, "[" + str(index) + "]"))
            result_list.append(res)
        return result_list  # type: ignore
    if isinstance(expr, dict):
        result_dict: dict[str, Any] = {}
        for k, v in expr.items():
            k_loc = append(loc, k)
            k_res: str = _process_expr(scope, k, k_loc)
            v_res: Any = _process_expr(scope, v, k_loc)
            result_dict[k_res] = v_res
        return result_dict  # type: ignore
    return expr


BlockTypeTVarProcessCallModel = TypeVar(
    "BlockTypeTVarProcessCallModel", bound=ModelBlock
)


def process_call_model(
    state: InterpreterState,
    scope: ScopeType,
    block: BlockTypeTVarProcessCallModel,
    loc: PdlLocationType,
) -> tuple[
    Any,
    LazyMessages,
    ScopeType,
    BlockTypeTVarProcessCallModel,
]:
    # evaluate model params
    match block:
        case LitellmModelBlock():
            # evaluate model name
            model_id, concrete_block = process_expr_of(
                block, "model", scope, loc  # pyright: ignore
            )  # pyright: ignore
            if isinstance(concrete_block.parameters, LitellmParameters):
                concrete_block = concrete_block.model_copy(
                    update={"parameters": concrete_block.parameters.model_dump()}
                )

            _, concrete_block = process_expr_of(
                concrete_block, "parameters", scope, loc
            )

        case GraniteioModelBlock():
            match block.processor:
                case GraniteioProcessor():
                    proc_loc = append(loc, "processor")
                    processor = block.processor.model_copy()
                    model_id, processor.backend = process_expr(
                        scope, processor.backend, append(proc_loc, "backend")
                    )
                    if processor.type is not None:
                        _, processor.type = process_expr(
                            scope, processor.type, append(proc_loc, "type")
                        )
                    if processor.model is not None:
                        model_id, processor.model = process_expr(
                            scope, processor.model, append(proc_loc, "model")
                        )
                    concrete_block = block.model_copy(update={"processor": processor})
                case _:
                    model_id, concrete_block = process_expr_of(
                        block, "processor", scope, loc
                    )
            if concrete_block.parameters is not None:
                _, concrete_block = process_expr_of(
                    concrete_block, "parameters", scope, loc
                )
        case OpenaiModelBlock():
            # evaluate model name
            model_id, concrete_block = process_expr_of(
                block, "model", scope, loc  # pyright: ignore
            )  # pyright: ignore
            if isinstance(concrete_block.parameters, OpenaiParameters):
                concrete_block = concrete_block.model_copy(
                    update={"parameters": concrete_block.parameters.model_dump()}
                )

            _, concrete_block = process_expr_of(
                concrete_block, "parameters", scope, loc
            )
        case _:
            assert False
    # evaluate input
    model_input: ModelInput
    model_input_future, _, _, concrete_block = process_block_of(
        concrete_block,
        "input",
        state.with_yield_result(False).with_yield_background(False),
        scope,
        loc,
    )
    try:
        model_input_result = model_input_future.result()
        if isinstance(model_input_result, str):
            model_input_result = [{"role": state.role, "content": model_input_result}]
        model_input_context = ensure_context(model_input_result)
        match block:
            case LitellmModelBlock():
                model_input = model_input_context.serialize(SerializeMode.LITELLM)
            case GraniteioModelBlock():
                model_input = model_input_context.serialize(SerializeMode.GRANITEIO)
            case OpenaiModelBlock():
                model_input = model_input_context.serialize(SerializeMode.OPENAI)
            case _:
                assert False
        concrete_block = concrete_block.model_copy(
            update={
                "pdl__model_input": model_input,
            }
        )
        model_input = [
            {k: v for k, v in m.items() if k != "pdl__defsite"} for m in model_input
        ]

        # Execute model call
        litellm_params = {}

        def get_transformed_inputs(kwargs):
            params_to_model = kwargs["additional_args"]["complete_input_dict"]
            nonlocal litellm_params
            litellm_params = params_to_model

        import litellm

        litellm.input_callback = [get_transformed_inputs]
        # If the environment has a configured OpenTelemetry exporter, tell LiteLLM
        # to do OpenTelemetry callbacks for that exporter.  Note that this may
        # require optional OpenTelemetry Python libraries that are not pyproject.toml,
        # typically opentelemetry-api, opentelemetry-sdk,
        # opentelemetry-exporter-otlp-proto-http, and opentelemetry-exporter-otlp-proto-grpc
        if getenv("OTEL_EXPORTER") and getenv("OTEL_ENDPOINT"):
            litellm.callbacks = ["otel"]
        msg, raw_result = generate_client_response(
            state, scope, concrete_block, str(model_id), model_input
        )

        # PdlList([lazy_apply(lambda msg: msg | {"pdl__defsite": block.pdl__id}, msg)])
        background: LazyMessages = SingletonContext(lazy_apply(lambda msg: msg | {"pdl__defsite": block.pdl__id}, msg))  # type: ignore
        result = lazy_apply(
            lambda msg: "" if msg["content"] is None else msg["content"], msg
        )
        if block.modelResponse is not None:
            scope = scope | {block.modelResponse: raw_result}
            assert block.pdl__id is not None
            state.replay[block.pdl__id + ".modelResponse"] = raw_result
        trace: BlockTypeTVarProcessCallModel = concrete_block.model_copy(
            update={"pdl__result": result}
        )  # pyright: ignore
        return result, background, scope, trace
    except KeyboardInterrupt as exc:
        raise exc from exc
    except httpx.RequestError as exc:
        message = f"model '{model_id}' encountered {repr(exc)} trying to {exc.request.method} against {exc.request.url}"
        raise PDLRuntimeError(
            message,
            loc=loc,
            trace=ErrorBlock(msg=message, pdl__location=loc, program=concrete_block),
            source_exception=exc,
        ) from exc
    except Exception as exc:
        message = f"Error during '{model_id}' model call: {repr(exc)}"
        raise PDLRuntimeError(
            message,
            loc=loc,
            trace=ErrorBlock(msg=message, pdl__location=loc, program=concrete_block),
            source_exception=exc,
        ) from exc


def generate_client_response(
    state: InterpreterState,
    scope: ScopeType,
    block: LitellmModelBlock | GraniteioModelBlock | OpenaiModelBlock,
    model_id: str,
    model_input: ModelInput,
) -> tuple[LazyMessage, PdlLazy[Any]]:
    match state.batch:
        case 0:
            model_output, raw_result = generate_client_response_streaming(
                state, scope, block, model_id, model_input
            )
        case 1:
            model_output, raw_result = generate_client_response_single(
                state, scope, block, model_id, model_input
            )
        case _:
            assert False
    return model_output, raw_result


def generate_client_response_streaming(
    state: InterpreterState,
    scope: ScopeType,
    block: LitellmModelBlock | GraniteioModelBlock | OpenaiModelBlock,
    model_id: str,
    model_input: ModelInput,
) -> tuple[LazyMessage, PdlLazy[Any]]:
    msg_stream: Generator[dict[str, Any], Any, Any]
    match block:
        case LitellmModelBlock():
            if block.parameters is None:
                parameters = None
            else:
                parameters = value_of_expr(block.parameters)  # pyright: ignore
            assert parameters is None or isinstance(
                parameters, dict
            )  # block is a "concrete block"
            # Apply PDL defaults to model invocation

            parameters = apply_defaults(
                model_id,
                parameters or {},
                scope.get("pdl_model_default_parameters", []),
            )
            msg_stream = LitellmModel.generate_text_stream(
                block,
                model_id=value_of_expr(block.model),
                messages=model_input,
                parameters=litellm_parameters_to_dict(parameters),
            )
        case GraniteioModelBlock():
            # TODO: currently fallback to the non-streaming interface
            return generate_client_response_single(
                state, scope, block, model_id, model_input
            )
        case OpenaiModelBlock():
            if block.parameters is None:
                parameters = None
            else:
                parameters = value_of_expr(block.parameters)  # pyright: ignore
            assert parameters is None or isinstance(
                parameters, dict
            )  # block is a "concrete block"
            # Apply PDL defaults to model invocation

            parameters = apply_defaults(
                model_id,
                parameters or {},
                scope.get("pdl_model_default_parameters", []),
            )
            from .pdl_openai import OpenaiModel

            msg_stream = OpenaiModel.generate_text_stream(
                block,
                model_id=value_of_expr(block.model),
                messages=model_input,
                parameters=openai_parameters_to_dict(parameters),
            )
        case _:
            assert False
    complete_msg: dict[str, Any] | None = None
    role = None
    wrapped_gen = GeneratorWrapper(msg_stream)
    for chunk in wrapped_gen:
        if state.yield_result:
            yield_result(
                "" if chunk["content"] is None else chunk["content"], block.kind
            )
        if state.yield_background:
            yield_background([chunk])
        if complete_msg is None:
            complete_msg = chunk
            role = complete_msg["role"]
        else:
            chunk_role = chunk["role"]
            if (
                chunk_role is None
                or chunk_role == role
                and chunk["content"] is not None
            ):
                complete_msg["content"] += chunk["content"]
    raw_result = None
    if block.modelResponse is not None:
        raw_result = wrapped_gen.value
    if complete_msg is None:
        complete_msg = {"role": state.role, "content": ""}
    if len(wrapped_gen.value) > 0:
        last = wrapped_gen.value[-1]
        if last["usage"] is not None:
            usage = last["usage"]
            if (
                usage["completion_tokens"] is not None
                and usage["prompt_tokens"] is not None
            ):
                block.pdl__usage = PdlUsage(
                    model_calls=1,
                    completion_tokens=usage["completion_tokens"],
                    prompt_tokens=usage["prompt_tokens"],
                )
                state.add_usage(block.pdl__usage)
    return PdlConst(complete_msg), PdlConst(raw_result)


def litellm_parameters_to_dict(
    parameters: LitellmParameters | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(parameters, dict):
        return {k: v for k, v in parameters.items() if k != "stream"}
    if parameters is None:
        parameters = LitellmParameters()
    parameters_dict = parameters.model_dump(exclude={"stream"})
    return parameters_dict


def openai_parameters_to_dict(
    parameters: OpenaiParameters | dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(parameters, dict):
        return parameters
    if parameters is None:
        parameters = OpenaiParameters()
    parameters_dict = parameters.model_dump()
    return parameters_dict


def generate_client_response_single(
    state: InterpreterState,
    scope: ScopeType,
    block: LitellmModelBlock | GraniteioModelBlock | OpenaiModelBlock,
    model_id: str,
    model_input: ModelInput,
) -> tuple[LazyMessage, PdlLazy[Any]]:
    if block.parameters is None:
        parameters = None
    else:
        parameters = value_of_expr(block.parameters)  # pyright:ignore
    assert parameters is None or isinstance(
        parameters, dict
    )  # block is a "concrete block"
    parameters = apply_defaults(
        model_id,
        parameters or {},
        scope.get("pdl_model_default_parameters", []),
    )
    block.pdl__usage = PdlUsage()
    match block:
        case LitellmModelBlock():
            message, response = LitellmModel.generate_text(
                state=state,
                block=block,
                model_id=value_of_expr(block.model),
                messages=model_input,
                parameters=litellm_parameters_to_dict(parameters),
            )
        case GraniteioModelBlock():
            from .pdl_granite_io import GraniteioModel

            message, response = GraniteioModel.generate_text(
                block=block,
                messages=model_input,
                event_loop=state.event_loop,
            )
        case OpenaiModelBlock():
            from .pdl_openai import OpenaiModel

            message, response = OpenaiModel.generate_text(
                state=state,
                block=block,
                model_id=value_of_expr(block.model),
                messages=model_input,
                parameters=openai_parameters_to_dict(parameters),
            )
        case _:
            assert False
    if state.yield_result:
        msg = message.result()
        yield_result("" if msg["content"] is None else msg["content"], block.kind)
    if state.yield_background:
        msg = message.result()
        yield_background([msg])
    return (message, response)


def process_call_code(
    state: InterpreterState,
    scope: ScopeType,
    block: (
        ArgsBlock
        | PythonCodeBlock
        | IPythonCodeBlock
        | JinjaCodeBlock
        | PdlCodeBlock
        | CommandCodeBlock
    ),
    loc: PdlLocationType,
) -> tuple[
    PdlLazy[Any],
    LazyMessages,
    ScopeType,
    ArgsBlock
    | PythonCodeBlock
    | IPythonCodeBlock
    | JinjaCodeBlock
    | PdlCodeBlock
    | CommandCodeBlock,
]:
    background: LazyMessages
    code_a: None | list[str] = None
    code_s = ""
    execution_scope: ScopeType = scope
    match block:
        case ArgsBlock():
            code_a = []
            args_trace: list[LocalizedExpression[str]] = []
            for expr_i in block.args:
                arg_i: str
                trace_i: LocalizedExpression[str]
                arg_i, trace_i = process_expr(scope, expr_i, loc)
                code_a.append(arg_i)
                args_trace.append(trace_i)
            block = block.model_copy(update={"args": args_trace})
        case CodeBlock():
            code_, _, _, block = process_block_of(
                block,
                "code",
                state.with_yield_result(False).with_yield_background(False),
                scope,
                loc,
            )
            code_s = code_.result()
            if block.scope is not None:
                execution_scope, block = process_expr_of(block, "scope", scope, loc)

    match block:
        case ArgsBlock():
            try:
                result = call_command(code_s, code_a)
                background = SingletonContext(
                    PdlDict(
                        {
                            "role": state.role,
                            "content": result,
                            "pdl__defsite": block.pdl__id,
                        }
                    )
                )
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeError(
                    f"Shell Code error: {repr(exc)}",
                    loc=loc,
                    trace=block.model_copy(update={"args": code_a}),
                    source_exception=exc,
                ) from exc
        case PythonCodeBlock():
            try:
                result = call_python(code_s, execution_scope, state)
                background = SingletonContext(
                    PdlDict(
                        {
                            "role": state.role,
                            "content": lazy_apply(str, result),
                            "pdl__defsite": block.pdl__id,
                        }
                    )
                )
            except _MissingResultError as exc:
                # The code ran fine; it just never set the block's value. Point
                # at the `code` key rather than at the block as a whole, and
                # keep the `Python Code error:` prefix off a message that is
                # about a PDL rule.
                raise PDLRuntimeError(
                    exc.message,
                    loc=append(loc, "code"),
                    trace=block.model_copy(
                        update={"code": code_s, "pdl__defsite": block.pdl__id}
                    ),
                    source_exception=exc,
                ) from exc
            except _CodeBlockRaised as exc:
                # The diagnostic is already rendered -- `call_python` is the only
                # place the frames and the block's own source both exist. Point
                # at the `code` key rather than at the block as a whole, and keep
                # the `Python Code error:` category label off a message that
                # already says what happened.
                raise PDLRuntimeError(
                    exc.message,
                    loc=append(loc, "code"),
                    trace=block.model_copy(
                        update={"code": code_s, "pdl__defsite": block.pdl__id}
                    ),
                    source_exception=exc,
                ) from exc
            except PDLRuntimeExpressionError as exc:
                raise PDLRuntimeError(
                    f"Python Code error: {exc.message}",
                    loc=loc,
                    trace=block.model_copy(
                        update={"code": code_s, "pdl__defsite": block.pdl__id}
                    ),
                    source_exception=exc,
                ) from exc
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                # Near-unreachable now that `call_python` renders its own
                # diagnostic: only a failure building the background context
                # above lands here. `repr`, not `traceback.format_exc()`, so this
                # is not a second latent traceback leak in the same `match` arm.
                raise PDLRuntimeError(
                    f"Python Code error: {exc!r}",
                    loc=loc,
                    trace=block.model_copy(
                        update={"code": code_s, "pdl__defsite": block.pdl__id}
                    ),
                    source_exception=exc,
                ) from exc
        case IPythonCodeBlock():
            try:
                result = call_ipython(code_s, execution_scope)
                background = SingletonContext(
                    PdlList(
                        [
                            PdlDict(  # type: ignore
                                {
                                    "role": state.role,
                                    "content": lazy_apply(str, result),
                                    "pdl__defsite": block.pdl__id,
                                },
                            ),
                        ],  # type: ignore
                    )
                )
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeError(
                    f"Code error: {exc!r}",
                    loc=loc,
                    trace=block.model_copy(update={"code": code_s}),
                    source_exception=exc,
                ) from exc
        case CommandCodeBlock():
            try:
                result = call_command(code_s, code_a)
                background = SingletonContext(
                    PdlDict(
                        {
                            "role": state.role,
                            "content": result,
                            "pdl__defsite": block.pdl__id,
                        }
                    )
                )
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeError(
                    f"Shell Code error: {repr(exc)}",
                    loc=loc,
                    trace=block.model_copy(update={"code": code_s}),
                    source_exception=exc,
                ) from exc
        case JinjaCodeBlock():
            try:
                if block.parameters is not None:
                    parameters, block = process_expr_of(block, "parameters", scope, loc)
                else:
                    parameters = {}
                result = call_jinja(code_s, execution_scope, parameters)
                background = SingletonContext(
                    PdlDict(
                        {
                            "role": state.role,
                            "content": result,
                            "pdl__defsite": block.pdl__id,
                        }
                    )
                )
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeError(
                    f"Jinja Code error: {repr(exc)}",
                    loc=loc,
                    trace=block.model_copy(update={"code": code_s}),
                    source_exception=exc,
                ) from exc
        case PdlCodeBlock():
            try:
                result = call_pdl(code_s, execution_scope)
                background = DependentContext(
                    PdlList(
                        [
                            SingletonContext(
                                {"role": state.role, "content": result, "pdl__defsite": block.pdl__id}  # type: ignore
                            )
                        ]
                    )
                )
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeError(
                    f"PDL Code error: {repr(exc)}",
                    loc=loc,
                    trace=block.model_copy(update={"code": code_s}),
                    source_exception=exc,
                ) from exc
        case _:
            message = f"Unsupported language: {block.lang}"
            raise PDLRuntimeError(
                message,
                loc=loc,
                trace=block.model_copy(),
            )
    trace = block.model_copy(update={"pdl__result": result})
    return result, background, scope, trace


__PDL_SESSION = types.SimpleNamespace()


class _MissingResultError(PDLRuntimeExpressionError):
    """A Python `code:` block ran to completion without assigning `result`.

    Module private on purpose: `call_python` has neither the block nor its
    location, so it cannot raise a located error itself. `process_call_code`
    catches this before the general `PDLRuntimeExpressionError` clause and
    re-raises it as a `PDLRuntimeError` on the block's `code` key, without the
    `Python Code error:` prefix -- that prefix says the code errored, and this
    code did not.
    """


_MISSING_RESULT_MESSAGE = "code block finished without assigning `result`"

_MISSING_RESULT_RULE = (
    "A `code:` block's value is whatever its code assigns to the variable `result`."
)

_MISSING_RESULT_GENERIC_HELP = (
    "a code block must end by assigning its value, e.g. `result = ...`"
)

_PRINT_NOTE = "`print(...)` writes to stdout; it does not set the block's value."

_MISSING_RESULT_WIDTH = 76

_MISSING_RESULT_MAX_NAMES = 5


def _assigned_names(namespace: dict[str, Any], bound_before: set[str]) -> list[str]:
    """The names a code block bound, in binding order.

    Ordered `dict` iteration, never a `set` difference: the message must not
    depend on `PYTHONHASHSEED`. Imported modules and private/dunder names are
    dropped, as is anything the PDL scope already provided -- which means a name
    that was in scope and got reassigned does not show up here.
    """
    return [
        name
        for name, value in namespace.items()
        if name not in bound_before
        and name != "__builtins__"
        and not name.startswith("_")
        and not isinstance(value, types.ModuleType)
    ]


def _print_expression(code: str) -> tuple[bool, str | None]:
    """Whether the code mentions `print`, and what it printed.

    The second element is the source of the argument of the block's only
    top-level `print(<expr>)` call, when there is exactly one such call and it
    takes a single positional argument. That is the case where the fix can be
    spelled out; anything else only earns the generic advice.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:  # pragma: no cover - the code compiled a moment ago
        return False, None
    uses_print = any(
        isinstance(node, ast.Name) and node.id == "print" for node in ast.walk(tree)
    )
    if not uses_print:
        return False, None
    calls = [
        stmt.value
        for stmt in tree.body
        if isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "print"
    ]
    if len(calls) != 1:
        return True, None
    call = calls[0]
    if len(call.args) != 1 or call.keywords or isinstance(call.args[0], ast.Starred):
        return True, None
    return True, ast.unparse(call.args[0])


def _name_list(names: list[str]) -> str:
    shown = ", ".join(f"`{name}`" for name in names[:_MISSING_RESULT_MAX_NAMES])
    if len(names) > _MISSING_RESULT_MAX_NAMES:
        shown += f", and {len(names) - _MISSING_RESULT_MAX_NAMES} more"
    return shown


def _missing_result_diagnostic(code: str, assigned: list[str]) -> str:
    """The message body for a `code:` block that never assigned `result`.

    One diagnostic, several renderings: the evidence sentence says what was
    found, the `help:` line says what to do about it.
    """
    note: str | None = None
    replacement: str | None = None
    if len(assigned) == 0:
        evidence = "This block assigned nothing."
        uses_print, printed = _print_expression(code)
        if uses_print:
            note = _PRINT_NOTE
        if printed is not None:
            suggestion = "assign the value instead of printing it"
            replacement = f"result = {printed}"
        else:
            suggestion = _MISSING_RESULT_GENERIC_HELP
    elif len(assigned) == 1:
        evidence = f"This block assigned `{assigned[0]}`, but not `result`."
        suggestion = "assign it to `result`"
        replacement = f"result = {assigned[0]}"
    else:
        near = difflib.get_close_matches("result", assigned, n=1, cutoff=0.6)
        if near:
            evidence = f"This block assigned `{near[0]}`, but not `result`."
            suggestion = "did you mean to name it `result`?"
        else:
            evidence = f"This block assigned {_name_list(assigned)}, but not `result`."
            suggestion = "assign one of them to `result`"
            replacement = f"result = {assigned[-1]}"

    lines = [_MISSING_RESULT_MESSAGE, ""]
    lines += textwrap.wrap(
        f"{_MISSING_RESULT_RULE} {evidence}",
        width=_MISSING_RESULT_WIDTH,
        initial_indent="  ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    lines.append("")
    if note is not None:
        lines.append(f"  note: {note}")
    if replacement is None:
        lines.append(f"  help: {suggestion}")
    else:
        lines.append(f"  help: {suggestion}:  {replacement}")
    return "\n".join(lines)


class _CodeBlockRaised(PDLRuntimeExpressionError):
    """A Python `code:` block let an exception escape.

    Module private for the same reason as `_MissingResultError`: `call_python`
    holds the evidence (the code and the traceback) but neither the block nor
    its location, so it cannot raise a located error itself. `process_call_code`
    catches this before the general `PDLRuntimeExpressionError` clause and
    re-raises it on the block's `code` key, without the `Python Code error:`
    prefix -- the message already says what happened, and the prefix is a
    category label rather than a rule.
    """


_CODE_BLOCK_FILENAME = "<code-block>"

_RAISED_RULE = (
    "Python code in a `code:` block must run to completion; an exception that "
    "escapes it stops the program."
)

# Only true when something was printed with a `code:N` gutter above it. The
# sentence goes away entirely once file lines are available (phase-3 item 0).
_RAISED_GUTTER_CAVEAT = (
    "Line numbers above are within the block's code, not the PDL file."
)

_MODULE_ENV_NOTE = (
    "a `code:` block runs in the same Python environment as `pdl` itself, with "
    "the program's directory on `sys.path`."
)

_SCOPE_NAMES_NOTE = "PDL variables in scope are usable by name"

_RAISED_WIDTH = 76

# The exception's own text is wrapped a little wider than the prose: it is
# often a single unbreakable sentence from a library.
_RAISED_DETAIL_WIDTH = 78

# How much of `str(exc)` fits on the header line before it moves to a paragraph
# of its own. A fixed budget rather than a measured fit: the header is completed
# by `get_loc_string` at print time, so its final length is not known here.
_RAISED_DETAIL_CLIP = 60

_RAISED_MAX_DETAIL_LINES = 5

# A `RecursionError` must print five lines, not a thousand.
_RAISED_MAX_FRAMES = 3

_NEAR_MISS_CUTOFF = 0.7

# Seeded by the interpreter rather than by the user: `process_prog` adds
# `stdlib` and `call_python` adds `PDL_SESSION`. Everything else the interpreter
# injects -- `empty_scope`'s entries, `pdl_usage`, and the CLI's
# `pdl_model_default_parameters` -- lives under the reserved `pdl_` prefix.
_PDL_INTERNAL_NAMES = ("stdlib", "PDL_SESSION")

_PDL_RESERVED_PREFIX = "pdl_"


def _user_scope_names(scope: ScopeType) -> list[str]:
    """The PDL variables a `code:` block can refer to by name.

    The namespace is seeded from the block's scope, so every name here really is
    visible in the code. The interpreter's own entries are excluded rather than
    listed back at the user as if they had written them. Ordered iteration,
    never a `set`, so the list does not move with `PYTHONHASHSEED`.
    """
    hidden = set(empty_scope) | set(_PDL_INTERNAL_NAMES)
    return [
        name
        for name in scope
        if name not in hidden
        and not name.startswith("_")
        and not name.startswith(_PDL_RESERVED_PREFIX)
    ]


def _char_column(line: str, offset: int | None) -> int | None:
    """A `FrameSummary` column as a character offset into `line`.

    `colno`/`end_colno` are UTF-8 *byte* offsets, so a caret placed at one of
    them lands to the right of its token on any line containing non-ASCII text.
    CPython's own `traceback` module converts the same way.
    """
    if offset is None:
        return None
    return len(line.encode("utf-8")[:offset].decode("utf-8", "replace"))


def _code_line(code: str, lineno: int | None) -> str:
    """One line of the block's own source.

    `linecache` cannot resolve `<code-block>`, which is why a traceback prints
    those frames bare. The interpreter is holding the string, so it can.
    """
    lines = code.splitlines()
    if lineno is None or not 1 <= lineno <= len(lines):
        return ""
    return lines[lineno - 1]


def _block_frames(exc: BaseException) -> list[traceback.FrameSummary]:
    """The traceback frames running the block's own code, outermost first.

    `compile(code, "<code-block>", "exec")` stamps that filename on every frame
    executing the block's source, including frames inside functions the block
    itself defined, so the filter is mechanical and needs no heuristic: keep
    `<code-block>`, drop everything else. That drops PDL's own `exec` frame and
    any library the code called into -- neither is text the user can edit.
    """
    return [
        frame
        for frame in traceback.extract_tb(exc.__traceback__)
        if frame.filename == _CODE_BLOCK_FILENAME
    ]


def _caret_line(source: str, start: int | None, end: int | None, label: str) -> str:
    """The `^^^` under `source`, or the empty string when there is no column.

    Columns are absent under `-X no_debug_ranges` / `PYTHONNODEBUGRANGES=1`; the
    diagnostic stays valid without them, so the caret is optional and the frame's
    function name falls back to a line of its own.
    """
    if start is None or start > len(source):
        return f"|{label}" if label else ""
    stop = len(source) if end is None or end <= start else min(end, len(source))
    return "| " + " " * start + "^" * max(stop - start, 1) + label


def _gutter(rows: list[tuple[str, str, int | None, int | None, str]]) -> list[str]:
    """Render `code:N | <source>` rows with their caret lines.

    The `code:` prefix is load-bearing: a bare `1 |` would read as line 1 of the
    PDL file, which is a confidently-stated wrong location.
    """
    width = max((len(label) for label, _, _, _, _ in rows), default=0)
    lines: list[str] = []
    for label, source, start, end, note in rows:
        if not label:
            lines.append(f"{'':<{width}} {source}")
            continue
        lines.append(f"{label:<{width}} | {source}".rstrip())
        caret = _caret_line(source, start, end, note)
        if caret:
            lines.append(f"{'':<{width}} {caret}".rstrip())
    return lines


def _frame_rows(
    frames: list[traceback.FrameSummary], code: str
) -> list[tuple[str, str, int | None, int | None, str]]:
    """Gutter rows for the block's frames, Python's order: outermost first.

    Beyond `_RAISED_MAX_FRAMES` it is the outermost frame, a count, and the
    innermost one.
    """
    shown = frames
    elided = 0
    if len(frames) > _RAISED_MAX_FRAMES:
        shown = [frames[0], frames[-1]]
        elided = len(frames) - 2
    rows: list[tuple[str, str, int | None, int | None, str]] = []
    for index, frame in enumerate(shown):
        if elided and index == 1:
            rows.append(("", f"... {elided} more frames", None, None, ""))
        source = _code_line(code, frame.lineno)
        end_lineno = getattr(frame, "end_lineno", frame.lineno)
        start = _char_column(source, getattr(frame, "colno", None))
        end = (
            _char_column(source, getattr(frame, "end_colno", None))
            if end_lineno == frame.lineno
            else None
        )
        label = "" if frame.name == "<module>" else f" in {frame.name}"
        rows.append((f"code:{frame.lineno}", source, start, end, label))
    return rows


def _syntax_error_rows(
    exc: SyntaxError, code: str
) -> list[tuple[str, str, int | None, int | None, str]]:
    """Gutter rows for a `compile` failure, which has no `<code-block>` frame.

    Unlike a `FrameSummary` column, `SyntaxError.offset` is a 1-based *character*
    offset into `exc.text`.
    """
    if exc.lineno is None:
        return []
    source = (exc.text or _code_line(code, exc.lineno)).rstrip("\n")
    start = None if exc.offset is None else max(exc.offset - 1, 0)
    end_offset = getattr(exc, "end_offset", None)
    end = None if end_offset is None else max(end_offset - 1, 0)
    return [(f"code:{exc.lineno}", source, start, end, "")]


def _exception_summary(exc: BaseException) -> tuple[str, str | None]:
    """The `<Type>: <detail>` for the header, and the full text when it did not fit.

    The header carries the string a user greps for. A multi-line or overlong
    message moves to a paragraph of its own instead, so that a 2000-character
    exception from inside user code cannot become a wall in either position.
    """
    name = type(exc).__name__
    text = str(exc)
    first = text.split("\n", 1)[0]
    if not text:
        return name, None
    if first != text or len(first) > _RAISED_DETAIL_CLIP:
        return name, f"{name}: {text}"
    return f"{name}: {first}", None


def _wrap(text: str, subsequent: str = "  ", width: int = _RAISED_WIDTH) -> list[str]:
    """Wrap one paragraph into the two-space-indented body block."""
    return textwrap.wrap(
        text,
        width=width,
        initial_indent="  ",
        subsequent_indent=subsequent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _detail_paragraph(text: str) -> list[str]:
    """The exception's own text, when it did not fit on the header line.

    Bounded in both directions. `textwrap` will not split a word, so a single
    unbroken 400-character token comes back as one 400-character line and has to
    be clipped by hand; the paragraph as a whole is capped at five lines. A
    message from inside user code must not become a wall in either direction.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines += _wrap(paragraph, width=_RAISED_DETAIL_WIDTH) or [""]
    lines = [
        (
            line
            if len(line) <= _RAISED_DETAIL_WIDTH
            else line[:_RAISED_DETAIL_WIDTH] + "..."
        )
        for line in lines
    ]
    if len(lines) > _RAISED_MAX_DETAIL_LINES:
        hidden = len(lines) - _RAISED_MAX_DETAIL_LINES
        lines = lines[:_RAISED_MAX_DETAIL_LINES] + [f"  ... ({hidden} more lines)"]
    return lines


def _clip(text: str) -> str:
    first = text.split("\n", 1)[0]
    if len(first) > _RAISED_DETAIL_CLIP:
        return first[:_RAISED_DETAIL_CLIP].rstrip() + "..."
    return first


def _raised_inside_note(exc: BaseException) -> str | None:
    """Where the exception came from, when it was not the block's own code.

    Frames in libraries the user imported are dropped from the gutter -- they
    are not text the user can edit and the chain is unbounded -- but dropping
    them silently would leave `result = json.loads("{")` looking like the
    raising line when it is not. Basename only: no absolute paths, and the
    message stays machine-independent.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames or frames[-1].filename == _CODE_BLOCK_FILENAME:
        return None
    if not any(frame.filename == _CODE_BLOCK_FILENAME for frame in frames):
        return None
    innermost = frames[-1]
    name = innermost.filename
    if name.startswith("<") and name.endswith(">"):
        # `<frozen importlib._bootstrap>` and friends: plumbing, not a file.
        return None
    if Path(name).parent == Path(__file__).parent:
        # PDL's own package, reached by calling a PDL function from the code.
        return None
    return (
        f"raised inside `{Path(name).name}`, line {innermost.lineno}, "
        f"in `{innermost.name}`, which this block called."
    )


def _caused_by_note(exc: BaseException) -> str | None:
    cause = exc.__cause__
    if cause is None and not exc.__suppress_context__:
        cause = exc.__context__
    if cause is None:
        return None
    text = str(cause).split("\n", 1)[0]
    if text and text in str(exc):
        # A wrapper that re-raised its cause's own message verbatim. Saying it a
        # second time adds nothing.
        return None
    label = f"{type(cause).__name__}: {_clip(text)}" if text else type(cause).__name__
    return f"caused by `{label}`"


def _raised_advice(
    exc: BaseException, scope_names: list[str], assigned: list[str]
) -> tuple[list[str], str | None]:
    """The `note:`/`help:` lines for the branches where a suggestion is checkable.

    Everything else stays silent. There is nothing true and useful to say to
    someone who divided by zero, and a vacuous suggestion scores below none.
    """
    name = getattr(exc, "name", None)
    if isinstance(exc, ModuleNotFoundError) and name:
        return [_MODULE_ENV_NOTE], f"install `{name}` in that environment."
    if isinstance(exc, NameError) and not isinstance(exc, UnboundLocalError) and name:
        # Computed here rather than taken from CPython's own `Did you mean:`
        # suffix, whose heuristics move between versions; the golden must not.
        candidates = list(assigned)
        candidates += [n for n in scope_names if n not in candidates]
        candidates += [
            n for n in dir(builtins) if not n.startswith("_") and n not in candidates
        ]
        near = difflib.get_close_matches(
            name, candidates, n=1, cutoff=_NEAR_MISS_CUTOFF
        )
        if near:
            return [], f"did you mean `{near[0]}`?"
        notes = []
        if scope_names:
            notes.append(f"{_SCOPE_NAMES_NOTE}: {_name_list(scope_names)}.")
        # `def:`/`defs:` adds to the scope. Never `scope:`, which *replaces* the
        # block's execution scope and would silently drop everything else the
        # code relies on.
        return notes, f"define `{name}` in the code, or define it earlier with `def:`."
    return [], None


def _raised_diagnostic(
    exc: Exception,
    code: str,
    namespace: dict[str, Any],
    bound_before: set[str],
    scope: ScopeType,
) -> str:
    """The message body for a `code:` block that raised.

    One diagnostic, several renderings: the header verb, the evidence and the
    `help:` are computed; the rule paragraph is constant.
    """
    frames = _block_frames(exc)
    detail: str | None = None
    if isinstance(exc, SyntaxError) and not frames:
        headline = f"code block has a syntax error: {exc.msg}"
        rows = _syntax_error_rows(exc, code)
        notes: list[str] = []
        suggestion: str | None = None
    else:
        summary, detail = _exception_summary(exc)
        headline = f"code block raised {summary}"
        rows = _frame_rows(frames, code)
        notes, suggestion = _raised_advice(
            exc,
            _user_scope_names(scope),
            _assigned_names(namespace, bound_before),
        )

    gutter = _gutter(rows)
    lines = [headline, ""]
    if gutter:
        lines += gutter + [""]
    if detail is not None:
        lines += _detail_paragraph(detail) + [""]
    rule = _RAISED_RULE
    if gutter:
        rule += f" {_RAISED_GUTTER_CAVEAT}"
    lines += _wrap(rule)

    inside = _raised_inside_note(exc)
    if inside is not None:
        notes.append(inside)
    caused = _caused_by_note(exc)
    if caused is not None:
        notes.append(caused)
    if notes or suggestion is not None:
        lines.append("")
    for note in notes:
        lines += _wrap(f"note: {note}", subsequent=" " * 8)
    if suggestion is not None:
        lines += _wrap(f"help: {suggestion}", subsequent=" " * 8)
    return "\n".join(lines)


def call_python(code: str, scope: ScopeType, state: InterpreterState) -> PdlLazy[Any]:
    my_namespace = types.SimpleNamespace(PDL_SESSION=__PDL_SESSION, **scope)
    bound_before = set(my_namespace.__dict__)
    sys.path.append(str(state.cwd))
    try:
        c = compile(code, "<code-block>", "exec")
        exec(c, my_namespace.__dict__)  # nosec B102
        # [B102:exec_used] Use of exec detected.
        # This is the code that the user asked to execute. It can be executed in a docker container with the option `--sandbox`
    except KeyboardInterrupt as exc:
        raise exc from exc
    except Exception as exc:
        raise _CodeBlockRaised(
            _raised_diagnostic(exc, code, my_namespace.__dict__, bound_before, scope),
            source_exception=exc,
        ) from exc
    else:
        # `hasattr`, not attribute access: a PDL variable named `result` that is
        # already in scope was copied into the namespace above, and a block that
        # inherits it that way keeps working.
        if not hasattr(my_namespace, "result"):
            raise _MissingResultError(
                _missing_result_diagnostic(
                    code, _assigned_names(my_namespace.__dict__, bound_before)
                )
            )
        result = getattr(my_namespace, "result")
    finally:
        sys.path.pop()
    return PdlConst(result)


def call_ipython(code: str, scope: ScopeType) -> Any:
    my_namespace = types.SimpleNamespace(**scope)
    shell = PythonREPL(
        name_to_func_mapping=my_namespace.__dict__,
        timeout=5,
    )
    return PdlConst(shell(code))


def call_command(code: str, code_a: list[str] | None) -> PdlLazy[str]:
    if code_a is not None:
        args = code_a
    else:
        args = shlex.split(code)
    p = subprocess.run(
        args, capture_output=True, text=True, check=False, shell=False
    )  # nosec B603
    # [B603:subprocess_without_shell_equals_true] subprocess call - check for execution of untrusted input.
    # This is the code that the user asked to execute. It can be executed in a docker container with the option `--sandbox`
    if p.stderr != "":
        print(p.stderr, file=sys.stderr)
    if p.returncode != 0:
        raise ValueError(f"command exited with non zero code: {p.returncode}")
    output = p.stdout
    return PdlConst(output)


def call_jinja(code: str, scope: ScopeType, parameters: dict) -> PdlLazy[Any]:
    template = Template(
        code,
        **parameters,
    )
    result = template.render(scope)
    return PdlConst(result)


def call_pdl(code: str, scope: ScopeType) -> PdlLazy[Any]:
    program, loc = parse_str(code)
    state = InterpreterState()
    result, _, _, _ = process_prog(state, scope, program, loc)
    return result


def process_call(
    state: InterpreterState, scope: ScopeType, block: CallBlock, loc: PdlLocationType
) -> tuple[Any, LazyMessages, ScopeType, CallBlock]:
    result = None
    background: LazyMessages = DependentContext([])
    args, block = process_expr_of(block, "args", scope, loc)
    closure, _ = process_expr_of(block, "call", scope, loc)

    if not isinstance(closure, ClosureBlock):
        msg = f"Type error: {block.call} is of type {type(closure)} but should be a function."
        if isinstance(closure, str) and isinstance(scope.get(closure), FunctionBlock):
            msg += " You might want to call `${ " + str(block.call) + " }`."
        raise PDLRuntimeError(msg, loc=append(loc, "call"), trace=block.model_copy())
    args_loc = append(loc, "args")
    type_errors = type_check_args(args, closure.function, args_loc)
    if len(type_errors) > 0:
        raise PDLRuntimeError(
            f"Type errors during function call to {block.call}:\n"
            + "\n".join(type_errors),
            loc=args_loc,
            trace=block.model_copy(),
        )
    current_context = scope.data["pdl_context"]
    try:
        result, background, call_trace = execute_call(
            state, current_context, closure, args, loc
        )
    except PDLRuntimeError as exc:
        raise PDLRuntimeError(
            exc.message,
            loc=exc.loc or closure.pdl__location,
            trace=block.model_copy(update={"pdl__trace": exc.pdl__trace}),
            source_exception=exc,
        ) from exc
    trace = block.model_copy(update={"pdl__trace": call_trace})
    return result, background, scope, trace


def execute_call(state, current_context, closure, args, loc):
    if "pdl_context" in args:
        args = args | {"pdl_context": deserialize(args["pdl_context"])}
    f_body = closure.return_
    f_scope = (
        (closure.pdl__scope or ScopeType({}))
        | {"pdl_context": current_context}
        | (args or {})
    )
    if closure.pdl__location is not None:
        fun_loc = PdlLocationType(
            file=closure.pdl__location.file,
            path=closure.pdl__location.path + ["return"],
            table=loc.table,
        )
    else:
        fun_loc = empty_block_location
    result, background, _, f_trace = process_block(state, f_scope, f_body, fun_loc)
    if closure.spec is not None:
        result = lazy_apply(
            lambda r: result_with_type_checking(
                r,
                closure.spec,
                f"Type errors in result of the function{' ' + closure.signature['function'].get('name', '') if closure.signature is not None else ''}:",
                fun_loc,
                f_trace,
            ),
            result,
        )
    return result, background, f_trace


def process_input(
    state: InterpreterState, scope: ScopeType, block: ReadBlock, loc: PdlLocationType
) -> tuple[PdlLazy[str], LazyMessages, ScopeType, ReadBlock]:
    read, block = process_expr_of(block, "read", scope, loc)
    if read is not None:
        file = state.cwd / read
        try:
            with open(file, encoding="utf-8") as f:
                s = f.read()
        except KeyboardInterrupt as exc:
            raise exc from exc
        except Exception as exc:
            if isinstance(exc, FileNotFoundError):
                msg = f"file {str(file)} not found"
            else:
                msg = f"Fail to open file {str(file)}"
            raise PDLRuntimeError(
                message=msg,
                loc=loc,
                trace=ErrorBlock(msg=msg, pdl__location=loc, program=block),
                fallback="",
                source_exception=exc,
            ) from exc
    else:
        message = ""
        if block.message is not None:
            message = block.message
        elif block.multiline is False:
            message = "How can I help you?: "
        else:
            message = "Enter/Paste your content. Ctrl-D to save it."
        if block.multiline is False:
            s = input(message)
        else:  # multiline
            print(message)
            contents = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                contents.append(line + "\n")
            s = "".join(contents)
    trace = block.model_copy(update={"pdl__result": s})
    background: LazyMessages = SingletonContext(
        PdlDict({"role": state.role, "content": s, "pdl__defsite": block.pdl__id})
    )
    return PdlConst(s), background, scope, trace


def process_include(
    state: InterpreterState,
    scope: ScopeType,
    block: IncludeBlock,
    loc: PdlLocationType,
) -> tuple[Any, LazyMessages, ScopeType, IncludeBlock]:
    file = state.cwd / block.include
    try:
        prog, new_loc = parse_file(file)
        result, background, scope, trace = process_block(
            state, scope, prog.root, new_loc
        )
        include_trace = block.model_copy(update={"pdl__trace": trace})
        return result, background, scope, include_trace
    except PDLParseError as exc:
        message = f"Attempting to include invalid yaml: {str(file)}\n{exc.text}"
        raise PDLRuntimeError(
            message,
            loc=loc,
            trace=ErrorBlock(msg=message, program=block.model_copy()),
            source_exception=exc,
        ) from exc
    except PDLRuntimeProcessBlocksError as exc:
        trace = block.model_copy(update={"pdl__trace": exc.blocks})
        raise PDLRuntimeError(
            exc.message, loc=exc.loc or loc, trace=trace, source_exception=exc
        ) from exc


def import_read_error(
    block: ImportBlock, loc: PdlLocationType, diagnostic: Diagnostic
) -> PDLRuntimeError:
    """Wrap a rendered diagnostic about an unreadable imported file.

    The text is carried twice on purpose. `message` is what every re-wrap site
    propagates and what lands in the trace; the `PDLImportError` is what tells
    `generate` that the message is already a rendered diagnostic and must not be
    given a second location header.

    The caller chains the read failure with `raise ... from exc`, which is what
    keeps `retry`'s `exception_matches` matching a configured `FileNotFoundError`
    -- it walks `__cause__`. By the time the error reaches an SDK caller the
    retry path's own `raise exc from exc` has overwritten `__cause__`, and the
    `OSError` is on `__context__`.
    """
    message = diagnostic.text
    return PDLRuntimeError(
        message,
        loc=loc,
        trace=ErrorBlock(msg=message, program=block.model_copy()),
        source_exception=PDLImportError(diagnostic),
    )


def process_import(
    state: InterpreterState,
    scope: ScopeType,
    block: ImportBlock,
    loc: PdlLocationType,
) -> tuple[Any, LazyMessages, ScopeType, ImportBlock]:
    path = block.import_
    if not path.endswith(".pdl"):
        path += ".pdl"
    file = state.cwd / path
    # Only the read is guarded, so `prog_str` -- and therefore the cache key
    # below -- stays exactly where it is. `parse_file` would have brought the
    # parser's diagnostics for free, but it discards the source text that
    # `state.imported` is keyed on, and re-keying that cache on the path would
    # execute an imported program twice where today it runs once.
    try:
        with open(file, "r", encoding="utf-8") as pdl_fp:
            prog_str = pdl_fp.read()
    except OSError as exc:
        import_loc = append(loc, "import")
        raise import_read_error(
            block,
            import_loc,
            import_read_diagnostic(
                written=block.import_,
                resolved=file,
                cwd=state.cwd,
                exc=exc,
                file=loc.file,
                line=get_line(loc.table, import_loc.path),
                block_path=import_loc.path,
            ),
        ) from exc
    except UnicodeDecodeError as exc:
        # Not an `OSError`, so the clause above does not cover it and a non-UTF-8
        # imported file leaked a traceback. The parser already knows how to say
        # this; only the carrier differs, because here the failure surfaces on
        # the runtime path rather than the parse one.
        raise import_read_error(
            block, append(loc, "import"), undecodable_source_error(file, exc).diagnostic
        ) from exc
    try:
        prog, new_loc = parse_str(prog_str, file_name=str(file))
        cache = state.imported.get(prog_str)
        if cache is None:
            import_scope = empty_scope | {
                "stdlib": scope["stdlib"],
                "pdl_particle_id": scope["pdl_particle_id"],
            }
            _, _, new_scope, trace = process_block(
                state.with_yield_background(False).with_yield_result(False),
                import_scope,
                prog.root,
                new_loc,
            )
            state.imported[prog_str] = (new_scope, trace)
            if state.yield_result:
                yield_result(new_scope, block.kind)
        else:
            new_scope, trace = cache
        import_trace = block.model_copy(update={"pdl__trace": trace})
        return new_scope, DependentContext([]), scope, import_trace
    except PDLParseError as exc:
        message = f"Attempting to import invalid yaml: {str(file)}\n{exc.text}"
        raise PDLRuntimeError(
            message,
            loc=loc,
            trace=ErrorBlock(msg=message, program=block.model_copy()),
            source_exception=exc,
        ) from exc
    except PDLRuntimeProcessBlocksError as exc:
        trace = block.model_copy(update={"pdl__trace": exc.blocks})
        raise PDLRuntimeError(
            exc.message, loc=exc.loc or loc, trace=trace, source_exception=exc
        ) from exc


class Aggregator(ABC):
    @abstractmethod
    def contribute(
        self,
        result: PdlLazy[Any],
        role: RoleType | None = None,
        loc: PdlLocationType | None = None,
        block: BlockType | None = None,
    ) -> "Aggregator":
        """Function executed at the end of each block that contain the aggregator.

        Args:
            result: value computed by the block
            role: role associated to the block. Defaults to None.
            loc: source code location of the block. Defaults to None.
            block: block contributing the value. Defaults to None.

        Returns:
            Aggregator: new aggregator with the contributed value.
        """


class ContextAggregator(Aggregator):
    def __init__(self, messages: LazyMessages | None = None):
        if messages is None:
            self.messages: LazyMessages = DependentContext([])
        else:
            self.messages = messages

    def contribute(
        self,
        result: PdlLazy[Any],
        role: RoleType | None = None,
        loc: PdlLocationType | None = None,
        block: BlockType | None = None,
    ) -> "ContextAggregator":
        match block:
            case None | StructuredBlock():
                return self
            case LeafBlock():
                block_id = block.pdl__id
                msg = {"role": role, "content": result, "pdl__defsite": block_id}
            case _:
                msg = {"role": role, "content": result}
        new_messages: LazyMessages = SingletonContext(PdlDict(msg))
        messages = DependentContext([self.messages, new_messages])
        return ContextAggregator(messages)


class FileAggregator(Aggregator):
    def __init__(
        self, fp: IO, prefix: str = "", suffix: str = "\n", flush: bool = False
    ):
        self.fp = fp
        self.prefix = prefix
        self.suffix = suffix
        self.flush = flush

    def contribute(
        self,
        result: PdlLazy[Any],
        role: RoleType | None = None,
        loc: PdlLocationType | None = None,
        block: BlockType | None = None,
    ) -> "FileAggregator":
        print(
            f"{self.prefix}{stringify(result)}",
            file=self.fp,
            end=self.suffix,
            flush=self.flush,
        )
        return self


def process_aggregator(
    state: InterpreterState,
    scope: ScopeType,
    block: AggregatorBlock,
    loc: PdlLocationType,
) -> tuple[PdlLazy[Aggregator], LazyMessages, ScopeType, AggregatorBlock]:
    aggregator: Aggregator
    match block.aggregator:
        case "context":
            aggregator = ContextAggregator()
        case FileAggregatorConfig():
            try:
                cfg = block.aggregator
                file: str
                file_trace: ExpressionType[str]
                file, file_trace = process_expr(scope, cfg.file, loc)
                mode: str
                mode_trace: ExpressionType[str]
                mode, mode_trace = process_expr(scope, cfg.mode, loc)
                encoding: str | None
                encoding_trace: ExpressionType[str | None]
                encoding, encoding_trace = process_expr(scope, cfg.encoding, loc)
                prefix: str
                prefix_trace: ExpressionType[str]
                prefix, prefix_trace = process_expr(scope, cfg.prefix, loc)
                suffix: str
                suffix_trace: ExpressionType[str]
                suffix, suffix_trace = process_expr(scope, cfg.suffix, loc)
                flush: bool
                flush_trace: ExpressionType[bool]
                flush, flush_trace = process_expr(scope, cfg.flush, loc)
                cfg = block.aggregator.model_copy(
                    update={
                        "file": file_trace,
                        "mode": mode_trace,
                        "encoding": encoding_trace,
                        "prefix": prefix_trace,
                        "suffix": suffix_trace,
                        "flush": flush_trace,
                    }
                )
                trace = block.model_copy(update={"aggregator": cfg})
            except PDLRuntimeExpressionError as exc:
                raise PDLRuntimeError(
                    exc.message,
                    loc=exc.loc or loc,
                    trace=ErrorBlock(msg=exc.message, pdl__location=loc, program=block),
                    source_exception=exc,
                ) from exc
            fp = open(  # pylint: disable=consider-using-with
                file, mode=mode, encoding=encoding
            )
            state.opened_files.append(fp)  # Track for cleanup
            aggregator = FileAggregator(fp, prefix=prefix, suffix=suffix, flush=flush)
        case _:
            assert False, "Unexpected aggregator"
    background: LazyMessages = DependentContext([])
    trace = block.model_copy()
    return PdlConst(aggregator), background, scope, trace


def get_contribute_aggregator(
    block: AdvancedBlockType,
    target: ContributeTarget | str,
    scope: ScopeType,
    loc: PdlLocationType,
) -> Aggregator:
    match target:
        case ContributeTarget.STDOUT | "stdout":
            aggregator = FileAggregator(sys.stdout, flush=True)
        case ContributeTarget.STDERR | "stderr":
            aggregator = FileAggregator(sys.stderr, flush=True)
        case str():
            aggregator = get_var(target, scope, loc)
            if isinstance(aggregator, PdlLazy):
                aggregator = aggregator.result()
            if not isinstance(aggregator, Aggregator):
                msg = f"An aggregator was expected but got a value of type {type(aggregator)}."
                raise PDLRuntimeError(
                    msg,
                    loc=loc,
                    trace=ErrorBlock(msg=msg, pdl__location=loc, program=block),
                    fallback=[],
                )

        case _:
            assert False, f"Unexpected target type: {type(target)}"
    return aggregator


JSONReturnType = dict[str, Any] | list[Any] | str | float | int | bool | None


def parse_result(parser: ParserType, text: str) -> JSONReturnType:
    result: JSONReturnType
    match parser:
        case "json":
            try:
                if text == "False":
                    return json.loads("false")
                if text == "True":
                    return json.loads("true")
                result = json_repair.loads(text)  # type: ignore[reportAssignmentType]
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeParserError(
                    f"Attempted to parse ill-formed JSON: {repr(exc)}",
                    source_exception=exc,
                ) from exc
        case "jsonl":
            result = []
            try:
                for line in text.split("\n"):
                    if line == "":
                        continue
                    result.append(json.loads(line))
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeParserError(
                    f"Attempted to parse ill-formed JSON: {repr(exc)}",
                    source_exception=exc,
                ) from exc
        case "yaml":
            try:
                result = yaml.safe_load(text)
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeParserError(
                    f"Attempted to parse ill-formed YAML: {repr(exc)}",
                    source_exception=exc,
                ) from exc
        case "csv":
            try:
                result = []
                reader = csv.reader(StringIO(text))
                for row in reader:
                    result.append(row)
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                raise PDLRuntimeParserError(
                    f"Attempted to parse ill-formed CSV: {repr(exc)}",
                    source_exception=exc,
                ) from exc
        case PdlParser():
            assert False, "TODO"
        case RegexParser(mode="search" | "match" | "fullmatch"):
            regex = parser.regex
            match parser.mode:
                case "search":
                    matcher = re.search
                case "match":
                    matcher = re.match
                case "fullmatch":
                    matcher = re.fullmatch
                case _:
                    assert False
            try:
                m = matcher(regex, text, flags=re.M)
            except KeyboardInterrupt as exc:
                raise exc from exc
            except Exception as exc:
                msg = f"Fail to parse with regex {regex}: {repr(exc)}"
                raise PDLRuntimeParserError(msg, source_exception=exc) from exc
            if m is None:
                return None
            match parser.spec:
                case ObjectPdlType(object=dict() as spec) | (dict() as spec):
                    current_group_name = ""
                    try:
                        result = {}
                        for x in spec.keys():
                            current_group_name = x
                            result[x] = m.group(x)
                        return result
                    except IndexError as exc:
                        msg = f"No group named {current_group_name} found by {regex} in {text}"
                        raise PDLRuntimeParserError(msg, source_exception=exc) from exc
                case _:
                    result = list(m.groups())
        case RegexParser(mode="split" | "findall"):
            regex = parser.regex
            match parser.mode:
                case "split":
                    result = re.split(regex, text, flags=re.M)
                case "findall":
                    result = re.findall(regex, text, flags=re.M)
                case _:
                    assert False
        case _:
            assert False
    return result


def get_var(var: str, scope: ScopeType, loc: PdlLocationType) -> Any:
    v, _ = process_expr(scope, f"{EXPR_START_STRING} {var} {EXPR_END_STRING}", loc)
    return v
