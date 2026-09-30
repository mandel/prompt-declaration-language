import io
import time
from contextlib import redirect_stderr, redirect_stdout

from pdl.pdl import exec_dict

# How many times a block was actually run, taken from the program rather than
# from anything PDL prints.
#
# A retry used to announce itself on stderr, and these tests read that line to
# tell that a retry had happened at all. It is no longer printed on any path
# (E-RUNTIME-011), so the count comes from a list passed in through `scope`,
# which the `code:` block appends to on every attempt. That is the same fact,
# observed where it happens instead of inferred from a message, and it pins the
# attempt *count* exactly rather than just the first one.
_COUNT_ATTEMPT = "attempts.append(1)\n"


def repeat_retry_data(n: int):
    return {
        "description": "Example of retry code within a repeat block",
        "repeat": {
            "text": [
                "Hello, ",
                {
                    "lang": "python",
                    "code": {
                        "text": [
                            _COUNT_ATTEMPT,
                            "raise ValueError('dummy exception')\n",
                            "result = 'World'",
                        ]
                    },
                },
                "!\n",
            ],
            "retry": n,
        },
        "maxIterations": 2,
    }


def repeat_retry(n: int):
    attempts: list[int] = []
    err_msg = ""
    # catch stdout string
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(repeat_retry_data(n), scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # `retry: n` allows n retries, so n + 1 attempts.
    assert len(attempts) == n + 1
    # And a retry is silent, whichever way it ends: this block never succeeds,
    # so every attempt but the last was a retry taken, and the run still writes
    # nothing of its own (E-RUNTIME-011). The final error is raised, not
    # printed, and is swallowed above.
    assert err_msg == ""


# def test_repeat_retry_negative():
#     repeat_retry(-1)


def test_repeat_retry0():
    repeat_retry(0)


def test_repeat_retry1():
    repeat_retry(1)


def test_repeat_retry2():
    repeat_retry(2)


def test_repeat_retry3():
    repeat_retry(3)


def code_retry_data(n: int):
    return {
        "description": "Example of retry code within a code block",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise ValueError('dummy exception')\n",
                        "result = 'hello, world!'",
                    ]
                },
            },
        ],
        "retry": n,
    }


def code_retry(n: int):
    attempts: list[int] = []
    err_msg = ""
    # catch stdout string
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(code_retry_data(n), scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()
    assert len(attempts) == n + 1
    assert err_msg == ""


# def test_code_retry_negative():
#     code_retry(-1)


def test_code_retry0():
    code_retry(0)


def test_code_retry1():
    code_retry(1)


def test_code_retry2():
    code_retry(2)


def test_code_retry3():
    code_retry(3)


# ============================================================================
# What a retry reports, and to whom (E-RUNTIME-011)
# ============================================================================


def test_a_retry_that_succeeds_reports_nothing():
    """The whole point of E-RUNTIME-011: a run that recovers is a quiet run.

    The block fails once and succeeds on its second attempt, so from outside
    nothing went wrong -- the program produces its result and exits 0. Anything
    on stderr is then noise a user has to read and dismiss, and this is the
    reproducer the corpus entry pins byte for byte.
    """
    data = {
        "description": "A retry that succeeds on the second attempt",
        "lang": "python",
        "retry": 1,
        "code": (
            "n = getattr(PDL_SESSION, 'quiet_attempt', 0) + 1\n"
            "PDL_SESSION.quiet_attempt = n\n"
            "if n == 1:\n"
            "    raise ValueError('transient failure')\n"
            "result = f'ok on attempt {n}'\n"
        ),
    }

    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        result = exec_dict(data)
        captured = buf.getvalue()

    assert result == "ok on attempt 2"
    assert captured == ""


def test_a_retry_that_exhausts_reports_only_the_final_error():
    """The failure path is silent about the retries too, deliberately.

    Nothing is printed when a retry is taken, on any path, so a block that runs
    out of attempts reports the exception from the *last* one and no history of
    the earlier ones. That is a real loss on the failure path -- three attempts
    failing for three different reasons now surface only the third -- and it is
    pinned here so that it stays a decision rather than becoming an accident in
    either direction.
    """
    attempts: list[int] = []
    data = {
        "description": "A retry that never succeeds",
        "lang": "python",
        "retry": 2,
        "code": (
            "attempts.append(1)\n"
            "raise ValueError(f'attempt {len(attempts)} failed')\n"
        ),
    }

    raised = None
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception as exc:  # pylint: disable=broad-except
            raised = exc
        captured = buf.getvalue()

    assert len(attempts) == 3
    assert captured == ""
    # Only the last attempt's cause survives, and it is raised rather than
    # printed: the CLI is what turns it into a diagnostic.
    assert raised is not None
    assert "attempt 3 failed" in str(raised)
    assert "attempt 1 failed" not in str(raised)


def test_trace_error_on_retry_keeps_the_full_error_in_the_context():
    """The scope string is a second audience, and it keeps every byte of detail.

    `trace_error_on_retry` puts the error into `pdl_context` -- into the *model's*
    conversation for the next attempt, which is the entire point of the flag --
    and `set_error_to_scope_for_retry` compares it against the previous one to
    collapse a repeat. Changing what is *printed* must not change that, and
    nothing else in the suite reads the injected message, so the regression would
    be invisible.

    That string is now the only consumer of the error text at all: nothing is
    printed for the human on this path any more, so this test is the only thing
    standing between the detailed message and a well-meant simplification.
    """
    data = {
        "description": "A retry that succeeds, reporting the context it was given",
        "lang": "python",
        "code": (
            "n = getattr(PDL_SESSION, 'e11_attempt', 0) + 1\n"
            "PDL_SESSION.e11_attempt = n\n"
            "if n == 1:\n"
            "    raise ValueError('boom')\n"
            "result = str(pdl_context)\n"
        ),
        "retry": 1,
        "trace_error_on_retry": True,
    }

    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        result = exec_dict(data)
        err_msg = buf.getvalue()

    # What the model is told on the second attempt: unchanged, traceback included.
    assert "An error occurred in a PDL block. Error details:" in result
    assert "Traceback (most recent call last):" in result
    assert "ValueError: boom" in result

    # What the person watching is told: nothing. The run recovered.
    assert err_msg == ""


# ============================================================================
# Tests for Retry Delay Functionality
# ============================================================================


def test_retry_delay_basic():
    """Test that basic delay is applied between retry attempts."""
    data = {
        "description": "Test basic retry delay",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.1,  # 100ms delay
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Should have at least 2 delays (between 3 attempts)
    # Allow some tolerance for execution time
    assert elapsed >= 0.2, f"Expected at least 0.2s delay, got {elapsed:.3f}s"


def test_retry_delay_exponential_backoff():
    """Test exponential backoff with delay * (backoff ** trial_idx)."""
    data = {
        "description": "Test exponential backoff",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.1,  # 100ms base delay
            "backoff": 2.0,  # Double each time
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Expected delays: 0.1 * (2^0) = 0.1, 0.1 * (2^1) = 0.2
    # Total: 0.3s minimum
    assert elapsed >= 0.3, f"Expected at least 0.3s with backoff, got {elapsed:.3f}s"


def test_retry_delay_max_delay_capping():
    """Test that delays don't exceed max_delay."""
    data = {
        "description": "Test max_delay capping",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 4,
            "delay": 0.1,
            "backoff": 10.0,  # Would grow very large
            "max_delay": 0.15,  # Cap at 150ms
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Expected delays (capped): 0.1, 0.15, 0.15
    # Total: ~0.4s (with cap) vs much higher without cap
    assert elapsed >= 0.4, f"Expected at least 0.4s, got {elapsed:.3f}s"
    assert elapsed < 1.0, f"Expected less than 1.0s with capping, got {elapsed:.3f}s"


def test_retry_delay_fixed_jitter():
    """Test fixed jitter added to delay."""
    data = {
        "description": "Test fixed jitter",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.1,
            "jitter": 0.05,  # Fixed 50ms jitter
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Expected delays: (0.1 + 0.05) * 2 = 0.3s
    assert elapsed >= 0.3, f"Expected at least 0.3s with jitter, got {elapsed:.3f}s"


def test_retry_delay_random_jitter():
    """Test random jitter in a range."""
    data = {
        "description": "Test random jitter",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.1,
            "jitter": [0.0, 0.1],  # Random jitter 0-100ms
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Minimum: 0.1 * 2 = 0.2s (with 0 jitter)
    # Maximum: (0.1 + 0.1) * 2 = 0.4s (with max jitter)
    # Add tolerance for execution overhead
    assert elapsed >= 0.2, f"Expected at least 0.2s, got {elapsed:.3f}s"
    assert (
        elapsed <= 1.0
    ), f"Expected at most 1.0s with jitter range, got {elapsed:.3f}s"


def test_retry_delay_backward_compatibility():
    """Test that integer retry values still work (no delay)."""
    data = {
        "description": "Test backward compatibility",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": 3,  # Old-style integer retry
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Should complete quickly with no delays
    assert elapsed < 0.5, f"Expected quick execution with no delay, got {elapsed:.3f}s"


def test_retry_delay_no_delay_on_last_attempt():
    """Test that no delay is applied after the final retry."""
    data = {
        "description": "Test no delay after final attempt",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "delay": 0.2,  # 200ms delay
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Should have only 1 delay (between attempt 0 and 1)
    # Not after the final attempt (attempt 1)
    assert elapsed >= 0.2, f"Expected at least 0.2s, got {elapsed:.3f}s"


def test_retry_delay_with_expectations():
    """Test retry delay with expectation-based retries."""
    data = {
        "description": "Test retry delay with expectations",
        "text": [
            {
                "lang": "python",
                "code": "result = 'wrong answer'",
            },
        ],
        "retry": {
            "tries": 2,
            "delay": 0.1,
        },
        "expectations": [
            {
                "expect": "correct answer",
            }
        ],
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Should have at least 1 delay between attempts
    assert elapsed >= 0.1, f"Expected at least 0.1s delay, got {elapsed:.3f}s"


def test_retry_delay_combined_features():
    """Test combination of backoff, max_delay, and jitter."""
    data = {
        "description": "Test combined retry features",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.05,
            "backoff": 2.0,
            "max_delay": 0.15,
            "jitter": 0.02,  # Fixed jitter
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Expected delays:
    # Attempt 0->1: 0.05 * (2^0) + 0.02 = 0.07
    # Attempt 1->2: min(0.05 * (2^1), 0.15) + 0.02 = 0.12
    # Total: ~0.19s
    assert elapsed >= 0.19, f"Expected at least 0.19s, got {elapsed:.3f}s"


def test_retry_delay_zero_delay():
    """Test that zero delay works correctly."""
    data = {
        "description": "Test zero delay",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "delay": 0.0,  # No delay
        },
    }

    start_time = time.time()
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data)
        except Exception:
            pass
    elapsed = time.time() - start_time

    # Should complete quickly with no delays
    assert (
        elapsed < 1.5
    ), f"Expected quick execution with zero delay, got {elapsed:.3f}s"
