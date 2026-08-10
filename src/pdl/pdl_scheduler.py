from asyncio import AbstractEventLoop, new_event_loop, set_event_loop
from concurrent.futures import Future
from os import environ
from sys import stderr
from threading import Thread
from time import time_ns
from traceback import print_exc
from typing import TYPE_CHECKING, Any, Callable

from termcolor import colored

from .pdl_ast import BlockKind, ModelBlock
from .pdl_diagnostics import Diagnostic, Note, Span, Suggestion
from .pdl_location_utils import get_line
from .pdl_utils import stringify

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checking only
    # `pdl_interpreter_state` imports this module, so the runtime import would be
    # a cycle. Nothing here needs `InterpreterState` at runtime: the callback
    # only calls `state.add_usage`.
    from .pdl_interpreter_state import InterpreterState


def _start_background_loop(loop):
    set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def create_event_loop_thread() -> AbstractEventLoop:
    loop = new_event_loop()
    loop_thread = Thread(target=_start_background_loop, args=(loop,), daemon=True)
    loop_thread.start()
    return loop


def color_of(kind: BlockKind):
    color: str | None
    match kind:
        case BlockKind.FUNCTION:
            color = None
        case BlockKind.CALL:
            color = None
        case BlockKind.MODEL:
            color = "green"
        case BlockKind.CODE:
            color = "magenta"
        case BlockKind.GET:
            color = None
        case BlockKind.DATA:
            color = None
        case BlockKind.SEQUENCE:
            color = None
        case BlockKind.TEXT:
            color = None
        case BlockKind.LASTOF:
            color = None
        case BlockKind.ARRAY:
            color = None
        case BlockKind.OBJECT:
            color = None
        case BlockKind.MESSAGE:
            color = None
        case BlockKind.IF:
            color = None
        case BlockKind.MATCH:
            color = None
        case BlockKind.REPEAT:
            color = None
        case BlockKind.MAP:
            color = None
        case BlockKind.READ:
            color = None
        case BlockKind.INCLUDE:
            color = None
        case BlockKind.IMPORT:
            color = None
        case BlockKind.FACTOR:
            color = None
        case BlockKind.AGGREGATOR:
            color = None
        case BlockKind.EMPTY:
            color = None
        case BlockKind.ERROR:
            color = "red"
    return color


def color_of_role(role: str):
    color: str | None = None
    match role:
        case "assistant":
            color = "green"
        case "user":
            color = None
        case "system":
            color = "cyan"
        case "available_tools":
            color = "magenta"
    return color


def yield_result(result: Any, kind: BlockKind) -> None:
    if color_of(kind) is None:
        text = stringify(result)
    else:
        text = colored(stringify(result), color_of(kind))
    print(text, end="", flush=True)


_LAST_ROLE = None
ROLE_COLOR = "blue"


def yield_background(background) -> None:
    global _LAST_ROLE  # pylint: disable= global-statement
    if len(background) > 0 and background[0]["role"] == _LAST_ROLE:
        s = background[0]["content"]
        _LAST_ROLE = background[-1]["role"]
        background = background[1:]
    else:
        s = "\n"
    s += "\n".join(
        [
            f"{colored(msg['role'], ROLE_COLOR)}: {colored(msg['content'], color_of_role(msg['role']))}"
            for msg in background
        ]
    )
    print(s, end="", flush=True)


# --------------------------------------------------------------------------
# Model-call bookkeeping
# --------------------------------------------------------------------------
#
# `LitellmModel.generate_text` and `OpenaiModel.generate_text` schedule the
# provider call on the event-loop thread and attach a done-callback that records
# usage counters and an end timestamp. That callback lived twice, verbatim, in
# `pdl_llms.py` and `pdl_openai.py`, and both copies called `future.result()`
# unguarded: when the call failed, `.result()` re-raised inside
# `concurrent.futures._base._invoke_callbacks`, which logs
# `exception calling callback for <Future ...>` plus the whole stack to stderr.
#
# The failure was already reported, with a location, by the main thread forcing
# the lazy result. So the traceback was a second report of a handled failure --
# and because `InterpreterState.batch` defaults to 1, it hit *every* SDK
# embedder (`exec_file`/`exec_str`/`exec_dict` with no config) and `pdl --stream
# none`, with no way to suppress it. The CLI default (`--stream result`) takes
# the streaming path, registers no callback, and never showed it, which is why
# this survived: it is primarily an SDK defect.
#
# It also fired on runs that *succeeded*: a model block with `fallback:` or
# `retry:` recovers on the main thread, prints its result and exits 0, and still
# dumped ~20 frames. Corpus entry `E-MODEL-003` pins that.
#
# One copy lives here so the guard cannot drift back apart. This module imports
# only `pdl_ast`, `pdl_diagnostics`, `pdl_location_utils` and `pdl_utils`, none
# of which import it back.
#
# `pdl_granite_io.py` attaches *no* done-callback at all, so granite-io model
# calls record neither usage nor timing. That is a pre-existing gap rather than
# a regression, and it is deliberately left alone here: it needs its own change
# with its own test, and adding a third attach site to a hygiene fix is how the
# duplication started.

_RECORDING_BUG_RULE = (
    "This is a bug in PDL, not in your program. The model call itself succeeded "
    "and its result is unaffected; only the usage and timing bookkeeping failed."
)
_RECORDING_BUG_ISSUES = "https://github.com/IBM/prompt-declaration-language/issues"


def _report_recording_bug(block: ModelBlock, model_id: str, exc: Exception) -> None:
    """Report a failure of the bookkeeping itself, as one line rather than a wall.

    Reachable only after a *successful* model call, because a failed future
    returns early, so this can never compete with a real diagnostic for the same
    block. It renames the bug rather than swallowing it: a `KeyError` on a
    provider's usage dict stays visible, but as a diagnostic instead of an
    off-thread traceback.

    It prints from the event-loop thread, so its position relative to other
    stderr output is not deterministic. That is accepted here and only here: no
    corpus entry can reach this path, so no golden can flake on it.
    """
    loc = block.pdl__location
    diagnostic = Diagnostic(
        code="E-INTERNAL-USAGE-RECORDING",
        message=(
            f"internal error while recording usage for the model call to "
            f"'{model_id}': {exc!r}"
        ),
        file="" if loc is None else loc.file,
        spans=(
            []
            if loc is None
            else [Span(line=get_line(loc.table, loc.path), primary=True)]
        ),
        notes=[Note("rule", _RECORDING_BUG_RULE)],
        suggestions=[
            Suggestion(
                "please report it, with your program, at", _RECORDING_BUG_ISSUES
            ),
            Suggestion(
                "set PDL_TRACEBACK=1 to print the stack trace of this internal error."
            ),
        ],
    )
    print(diagnostic.text, file=stderr)
    if environ.get("PDL_TRACEBACK"):
        # Called from the `except` clause below, so the exception being handled
        # is still current. `E-BOUNDARY.md` reserved this variable name; this is
        # the first site to honour it.
        print_exc(file=stderr)


def _record_model_call(
    state: "InterpreterState",
    block: ModelBlock,
    model_id: str,
    future: "Future[tuple[dict[str, Any], Any]]",
) -> None:
    """Record usage counters and the end timestamp of a completed model call.

    `.get` throughout: the `is not None` tests already say that a response
    without usage records nothing, and the litellm copy of this code spelled the
    same intent in a way that raised `KeyError` instead. A provider response is
    not under PDL's control, so absence is a case, not a bug.
    """
    result = future.result()[1]
    usage = result.get("usage")
    if (
        block.pdl__usage is not None
        and usage is not None
        and usage.get("completion_tokens") is not None
        and usage.get("prompt_tokens") is not None
    ):
        block.pdl__usage.model_calls = 1
        block.pdl__usage.completion_tokens = usage["completion_tokens"]
        block.pdl__usage.prompt_tokens = usage["prompt_tokens"]
        state.add_usage(block.pdl__usage)

    if block.pdl__timing is not None:
        block.pdl__timing.end_nanos = time_ns()

        # report call completion and its duration
        start = (
            block.pdl__timing.start_nanos
            if block.pdl__timing.start_nanos is not None
            else 0
        )
        exec_nanos = block.pdl__timing.end_nanos - start
        if "PDL_VERBOSE_ASYNC" in environ:
            print(
                f"Asynchronous model call to {model_id} completed in {(exec_nanos)/1000000}ms",
                file=stderr,
            )
            msg = future.result()[0]
            if msg.get("content") is not None:
                print(
                    colored(msg["content"], color=color_of(BlockKind.MODEL)),
                    file=stderr,
                )
                print("\n", file=stderr)


def make_model_call_done_callback(
    state: "InterpreterState", block: ModelBlock, model_id: str
) -> Callable[["Future[tuple[dict[str, Any], Any]]"], None]:
    """The done-callback both model backends attach to their provider call.

    Three cases, in the order they must be tested:

    * **cancelled** -- nothing ran, so there is nothing to record. Tested first
      because `Future.exception()` *raises* `CancelledError` on a cancelled
      future rather than returning it.
    * **failed** -- the provider call raised. There is no usage to record, and
      the failure is reported, once and with a location, by whichever main-thread
      consumer forces the result. Return silently: this is not an error *in* the
      callback, it is a case the callback has nothing to do for.
    * **succeeded** -- do the bookkeeping. A failure *of the bookkeeping* is a
      bug in PDL and is reported as one line, not as a traceback.

    `Future.exception()` is the discriminator rather than a `try/except` around
    `.result()` because it reports the stored exception instead of raising it,
    which is exactly the distinction the two branches need. Inside a
    done-callback the future is already done, so it returns immediately and
    cannot block the event-loop thread.

    The invariant to preserve: **when `future.exception()` is not None, no
    `print` is reachable from this callback.** That is what makes the failed
    path single-writer, and it is what removes the main-thread/event-loop-thread
    interleaving race rather than merely narrowing it.
    """

    def _on_model_call_done(future: "Future[tuple[dict[str, Any], Any]]") -> None:
        if future.cancelled():
            # Nothing to record, and `exception()` below would raise here.
            return
        if future.exception() is not None:
            # The model call failed. There is no usage to record, and the
            # failure is reported by whoever forces the result on the main
            # thread. Nothing is printed from here, deliberately.
            return
        try:
            _record_model_call(state, block, model_id, future)
        except Exception as exc:  # pylint: disable=broad-except
            _report_recording_bug(block, model_id, exc)

    return _on_model_call_done
