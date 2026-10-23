"""The guard on the model-call done-callback (E-MODEL-001, E-MODEL-002, E-MODEL-003).

`LitellmModel.generate_text` and `OpenaiModel.generate_text` attach a
`concurrent.futures` done-callback that records usage and an end timestamp. Both
copies used to call `future.result()` unguarded, so a failed model call was
reported a second time, off-thread, as `exception calling callback for <Future
...>` plus ~20 frames -- after the main thread had already printed a located
diagnostic for it, and even on runs that recovered via `fallback:` and exited 0.

The corpus goldens pin what a user sees. These tests pin the *property* behind
them, which one golden run cannot: **when `future.exception()` is not None, no
writer to stdout or stderr is reachable from the callback.** That is what makes
the failed path single-writer, and it is why the old main-thread /
event-loop-thread interleaving is removed rather than merely made less likely.
"""

import builtins
import inspect
from concurrent.futures import Future
from io import StringIO

import pytest

from pdl import pdl_llms, pdl_openai, pdl_scheduler
from pdl.pdl_ast import LitellmModelBlock, PdlLocationType, PdlTiming, PdlUsage
from pdl.pdl_interpreter_state import InterpreterState
from pdl.pdl_scheduler import make_model_call_done_callback

MODEL_ID = "ollama/granite"

TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    "exception calling callback",
)


def _block() -> LitellmModelBlock:
    block = LitellmModelBlock(model=MODEL_ID)
    block.pdl__usage = PdlUsage()
    block.pdl__timing = PdlTiming()
    block.pdl__location = PdlLocationType(
        file="prog.pdl", path=["text", "[0]"], line=2, col=3
    )
    return block


def _failed() -> "Future":
    future: Future = Future()
    future.set_running_or_notify_cancel()
    future.set_exception(RuntimeError("the provider was not reachable"))
    return future


def _cancelled() -> "Future":
    future: Future = Future()
    future.cancel()
    return future


def _succeeded(response) -> "Future":
    future: Future = Future()
    future.set_running_or_notify_cancel()
    future.set_result(({"role": "assistant", "content": "hi"}, response))
    return future


@pytest.fixture(name="callback")
def _callback():
    block = _block()
    state = InterpreterState()
    return make_model_call_done_callback(state, block, MODEL_ID), state, block


@pytest.fixture(name="written")
def _written(monkeypatch):
    """Everything the callback writes to stderr.

    Not `capsys`/`capfd`: `pdl_scheduler` does `from sys import stderr` at import
    time, as both model backends do, so it holds the stream object pytest had
    installed at collection and neither capture fixture sees these writes.
    Rebinding the module's own name is what actually intercepts them.
    """
    sink = StringIO()
    monkeypatch.setattr(pdl_scheduler, "stderr", sink)
    return sink


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_future", [_failed, _cancelled], ids=["failed", "cancelled"]
)
def test_no_writer_is_reachable_when_the_call_did_not_succeed(
    make_future, callback, monkeypatch
):
    """No `print` is reachable at all -- not "prints nothing", *calls* nothing.

    `print` is replaced by a landmine rather than captured, so a writer added to
    either guard branch in the future fails this test even if what it would have
    written happens to be empty. Calling the callback directly matters:
    `Future._invoke_callbacks` swallows and *logs* whatever escapes a callback,
    which is the very behaviour under test.
    """

    def _landmine(*args, **kwargs):
        raise AssertionError(
            "the model-call done-callback reached a writer on a path where the "
            "future did not succeed; that reintroduces the second report and "
            "the interleaving race with the main thread"
        )

    monkeypatch.setattr(builtins, "print", _landmine)
    on_done, _, _ = callback
    on_done(make_future())


@pytest.mark.parametrize(
    "make_future", [_failed, _cancelled], ids=["failed", "cancelled"]
)
def test_unsuccessful_call_is_silent_through_the_real_future(
    make_future, callback, written
):
    """The same property through `add_done_callback`, as the interpreter uses it."""
    on_done, _, _ = callback
    future = make_future()
    future.add_done_callback(on_done)
    assert written.getvalue() == ""


@pytest.mark.parametrize(
    "make_future", [_failed, _cancelled], ids=["failed", "cancelled"]
)
def test_unsuccessful_call_records_no_usage(make_future, callback):
    """A call that never produced tokens must not be counted as if it had."""
    on_done, state, block = callback
    on_done(make_future())
    assert state.llm_usage.model_calls == 0
    assert block.pdl__usage is not None and block.pdl__usage.model_calls == 0


# ---------------------------------------------------------------------------
# The success path, unchanged
# ---------------------------------------------------------------------------


def test_success_records_usage_and_timing_and_says_nothing(callback, written):
    on_done, state, block = callback
    on_done(_succeeded({"usage": {"completion_tokens": 7, "prompt_tokens": 11}}))
    assert written.getvalue() == ""
    assert state.llm_usage.model_calls == 1
    assert state.llm_usage.completion_tokens == 7
    assert state.llm_usage.prompt_tokens == 11
    assert block.pdl__timing is not None and block.pdl__timing.end_nanos > 0


def test_success_without_usage_records_timing_only(callback, written):
    """A response with no usage is a case, not a bug: nothing is recorded, nothing said.

    The litellm copy of this callback spelled the same intent with `result["usage"]`
    and so raised `KeyError` instead, which the unguarded callback turned into a
    traceback. A provider's response shape is not under PDL's control.
    """
    on_done, state, block = callback
    on_done(_succeeded({}))
    assert written.getvalue() == ""
    assert state.llm_usage.model_calls == 0
    assert block.pdl__timing is not None and block.pdl__timing.end_nanos > 0


# ---------------------------------------------------------------------------
# A bug *in* the bookkeeping stays visible, as a diagnostic
# ---------------------------------------------------------------------------


class _BrokenUsage(dict):
    """A response whose usage lookup raises, i.e. a bug in PDL rather than a case."""

    def get(self, *args, **kwargs):
        raise KeyError("usage")


def test_a_recording_bug_is_one_diagnostic_and_not_a_traceback(callback, written):
    on_done, _, _ = callback
    on_done(_succeeded(_BrokenUsage()))
    reported = written.getvalue()
    assert reported.startswith(
        "prog.pdl:2 - internal error while recording usage for the model call "
        "to 'ollama/granite': KeyError('usage')\n"
    )
    assert "This is a bug in PDL, not in your program." in reported
    assert "PDL_TRACEBACK=1" in reported
    for marker in TRACEBACK_MARKERS:
        assert marker not in reported


def test_pdl_traceback_prints_the_stack_of_the_recording_bug(
    callback, written, monkeypatch
):
    """The `help:` line is true when followed. `E-BOUNDARY.md` reserved the name."""
    monkeypatch.setenv("PDL_TRACEBACK", "1")
    on_done, _, _ = callback
    on_done(_succeeded(_BrokenUsage()))
    reported = written.getvalue()
    assert "Traceback (most recent call last)" in reported
    assert "KeyError: 'usage'" in reported


# ---------------------------------------------------------------------------
# The duplication that caused all of the above
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [pdl_llms, pdl_openai], ids=["litellm", "openai"])
def test_both_backends_use_the_one_shared_callback(module):
    """The guard cannot be reintroduced in only one of the two backends.

    The defect existed twice because the callback did. Each backend must attach
    the shared factory and must not force the future itself.
    """
    source = inspect.getsource(module)
    assert "make_model_call_done_callback(state, block, model_id)" in source
    assert "future.result()" not in source
    assert "def update_end_nanos" not in source


def test_the_diagnostic_is_written_in_one_call(callback, monkeypatch):
    """One `write`, so main-thread output cannot land inside the diagnostic.

    `print` emits the text and the newline separately and stderr is line
    buffered, so the last `help:` line waits in the buffer between the two
    calls. This callback runs off the main thread, so another thread's output
    was observed splicing into the middle of the message in 2 of 5 runs. The
    diagnostic's *position* relative to other output is nondeterministic and
    accepted; a line cut in half is not.
    """
    calls: list[str] = []

    class _CountingSink:
        def write(self, text):
            calls.append(text)
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(pdl_scheduler, "stderr", _CountingSink())
    on_done, _, _ = callback
    on_done(_succeeded(_BrokenUsage()))
    assert len(calls) == 1, calls
    assert calls[0].endswith("\n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("yes", True), ("0", False), ("", False)],
)
def test_pdl_traceback_is_off_for_zero_and_empty(
    callback, written, monkeypatch, value, expected
):
    """`PDL_TRACEBACK=0` must not switch tracebacks *on*.

    A bare truthiness test on the environment variable makes every non-empty
    value true, including `"0"` -- which contradicts the `help:` line this same
    function prints and the semantics reserved in `E-BOUNDARY.md`. This is the
    first site to honour the name, so it is the precedent for the rest.
    """
    monkeypatch.setenv("PDL_TRACEBACK", value)
    on_done, _, _ = callback
    on_done(_succeeded(_BrokenUsage()))
    assert ("Traceback (most recent call last)" in written.getvalue()) is expected
