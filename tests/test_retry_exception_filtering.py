"""Tests for exception filtering in retry configuration."""

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from pdl.pdl import exec_dict
from pdl.pdl_ast import PDLRuntimeError

# Whether a retry was taken, observed in the program rather than in PDL's output.
#
# These tests used to read the `[Retry 1/2]` banner -- later the one-line retry
# notice -- off stderr to tell that a retry had happened. Nothing is printed when
# a retry is taken any more (E-RUNTIME-011), so each reproducer appends to a list
# passed in through `scope` and the test counts the entries. `retry.tries: 2`
# means three attempts, so a filter that matches gives three and one that does
# not gives one.
_COUNT_ATTEMPT = "attempts.append(1)\n"


def test_retry_with_specific_exception_match():
    """Test that retry occurs when exception matches the specified type."""
    data = {
        "description": "Test retry with matching exception",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "ValueError",  # Only retry on ValueError
        },
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # Should retry, since ValueError matches: three attempts for `tries: 2`
    assert len(attempts) == 3
    # And say nothing while doing it
    assert err_msg == ""


def test_retry_with_specific_exception_no_match():
    """Test that retry does NOT occur when exception doesn't match."""
    data = {
        "description": "Test retry with non-matching exception",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise RuntimeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "ValueError",  # Only retry on ValueError
        },
    }

    attempts: list[int] = []
    err_msg = ""
    exception_raised = False
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except PDLRuntimeError:
            exception_raised = True
        err_msg = buf.getvalue()

    # Should NOT retry, since RuntimeError doesn't match ValueError
    assert len(attempts) == 1
    assert err_msg == ""
    # Exception should be raised immediately
    assert exception_raised


def test_retry_with_exception_list_match():
    """Test retry with a list of exception types - matching case."""
    data = {
        "description": "Test retry with exception list",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise KeyError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": ["ValueError", "KeyError"],  # Retry on either
        },
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # Should retry, since KeyError is in the list
    assert len(attempts) == 3
    assert err_msg == ""


def test_retry_with_exception_list_no_match():
    """Test retry with a list of exception types - non-matching case."""
    data = {
        "description": "Test retry with exception list no match",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise TypeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": ["ValueError", "KeyError"],  # Retry on either
        },
    }

    attempts: list[int] = []
    err_msg = ""
    exception_raised = False
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except PDLRuntimeError:
            exception_raised = True
        err_msg = buf.getvalue()

    # Should NOT retry, since TypeError is not in the list
    assert len(attempts) == 1
    assert err_msg == ""
    # Exception should be raised immediately
    assert exception_raised


def test_retry_with_default_exception():
    """Test that default Exception catches all exception types."""
    data = {
        "description": "Test retry with default Exception",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise RuntimeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            # exceptions field not specified, defaults to Exception
        },
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # Should retry, since default Exception catches all
    assert len(attempts) == 3
    assert err_msg == ""


def test_retry_backward_compatibility_integer():
    """Test backward compatibility with integer retry (catches all exceptions)."""
    data = {
        "description": "Test backward compatibility",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise RuntimeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": 2,  # Old-style integer retry
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # Should retry - integer retry catches all exceptions
    assert len(attempts) == 3
    assert err_msg == ""


def test_retry_with_exception_hierarchy():
    """Test that exception hierarchy is respected (subclass matching)."""
    data = {
        "description": "Test exception hierarchy",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise KeyError('test exception')\n",  # KeyError is subclass of LookupError
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "LookupError",  # Parent class
        },
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"attempts": attempts})
        except Exception:
            pass
        err_msg = buf.getvalue()

    # Should retry, since KeyError is a subclass of LookupError
    assert len(attempts) == 3
    assert err_msg == ""


def test_retry_with_fallback_and_exception_filter():
    """Test that exception filtering works with fallback."""
    data = {
        "description": "Test exception filter with fallback",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise TypeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "ValueError",  # Only retry on ValueError
        },
        "fallback": {
            "text": "fallback result",
        },
    }

    # When exception doesn't match, it should go to fallback
    attempts: list[int] = []
    result = exec_dict(data, scope={"attempts": attempts})
    assert result == "fallback result"
    # Straight to the fallback: the filter does not match, so no retry
    assert len(attempts) == 1


def test_fallback_is_executed_only_once():
    """Test that the fallback is not re-executed for the remaining attempts."""
    data = {
        "description": "Test that the fallback runs once",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise TypeError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 3,
            "exceptions": "ValueError",  # Does not match, so the fallback is used
        },
        "fallback": {
            "text": "fallback result",
        },
    }

    # `yield_result` streams the result of each executed block, so the fallback
    # would show up once per attempt if it were executed more than once.
    attempts: list[int] = []
    out_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        result = exec_dict(
            data, config={"yield_result": True}, scope={"attempts": attempts}
        )
        out_msg = buf.getvalue()

    assert result == "fallback result"
    assert out_msg.count("fallback result") == 1
    assert len(attempts) == 1


def test_retry_with_unknown_exception_name():
    """Test that an exception name that cannot be resolved is reported."""
    data = {
        "description": "Test unknown exception name",
        "text": [
            {
                "lang": "python",
                "code": {"text": ["result = 'success'"]},
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "NoSuchExceptionName",
        },
    }

    # The error is reported even though the block itself does not fail
    with pytest.raises(PDLRuntimeError, match="Invalid exception"):
        _ = exec_dict(data)


def test_retry_with_exception_matching_python_class():
    """Test that exceptions can be given as Python exception classes."""
    data = {
        "description": "Test exception given as a class",
        "text": [
            {
                "lang": "python",
                "code": {
                    "text": [
                        _COUNT_ATTEMPT,
                        "raise ValueError('test exception')\n",
                        "result = 'success'",
                    ]
                },
            },
        ],
        "retry": {
            "tries": 2,
            "exceptions": "${ exceptions }",
        },
    }

    attempts: list[int] = []
    err_msg = ""
    with io.StringIO() as buf, redirect_stdout(buf), redirect_stderr(buf):
        try:
            _ = exec_dict(data, scope={"exceptions": ValueError, "attempts": attempts})
        except Exception:  # pylint: disable=broad-except
            pass
        err_msg = buf.getvalue()

    assert len(attempts) == 3
    assert err_msg == ""


# Made with Bob
