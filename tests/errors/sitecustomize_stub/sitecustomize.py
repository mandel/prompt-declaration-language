"""Offline enforcement and model stubbing for the error corpus.

Imported automatically by CPython at startup because the harness puts this
directory on ``PYTHONPATH``. Two jobs:

1. Make any outbound network connection fail loudly, so "the corpus runs
   offline" is enforced rather than asserted.
2. Stub the LiteLLM entry points that PDL calls, so ``E-MODEL-*`` entries can
   reproduce provider failures deterministically and without credentials.

Both are inert unless ``PDL_ERROR_CORPUS`` is set, so this file cannot affect a
normal run even if it ends up on someone's path.
"""

import os
import socket
import sys

if os.environ.get("PDL_ERROR_CORPUS") == "1":

    class OfflineViolation(RuntimeError):
        """Raised when corpus code attempts a real network connection."""

    _ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}

    def _guard(original):
        def connect(self, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if host not in _ALLOWED_HOSTS:
                raise OfflineViolation(
                    f"error corpus attempted a network connection to {host!r}; "
                    "corpus entries must run fully offline"
                )
            return original(self, address, *args, **kwargs)

        return connect

    # Monkeypatching the socket methods is the point: it is what makes "offline"
    # enforced rather than merely intended.
    socket.socket.connect = _guard(socket.socket.connect)  # type: ignore[method-assign]
    socket.socket.connect_ex = _guard(  # type: ignore[method-assign]
        socket.socket.connect_ex
    )

    # ------------------------------------------------------------------
    # LiteLLM stubbing
    # ------------------------------------------------------------------
    # PDL_TEST_MODEL selects the failure a model call should exhibit. When it is
    # unset, litellm is left completely alone -- several E-MODEL entries (an
    # unrecognised provider, for one) are decided by litellm's own local
    # validation and never touch the network, so the real message is both
    # reproducible and more faithful than a stub.
    _behaviour = os.environ.get("PDL_TEST_MODEL")

    if _behaviour:
        import types

        class BadRequestError(Exception):
            """Mimics ``litellm.BadRequestError`` closely enough for PDL."""

            def __init__(self, message):
                super().__init__(message)
                self.message = message
                self.status_code = 400

            def __repr__(self):
                return f"litellm.BadRequestError: {self.args[0]}"

            __str__ = __repr__

        class APIConnectionError(Exception):
            def __init__(self, message):
                super().__init__(message)
                self.message = message

            def __repr__(self):
                return f"litellm.APIConnectionError: {self.args[0]}"

            __str__ = __repr__

        # Deferred: this module runs at interpreter startup for every corpus
        # subprocess, so it must not import httpx unless a case actually needs
        # it. PDL's own model backends import httpx at module scope anyway, so
        # nothing is hidden by doing it here.
        import httpx  # pylint: disable=wrong-import-position

        async def _acompletion(model=None, messages=None, **kwargs):
            del messages, kwargs
            match _behaviour:
                case "connect_error":
                    raise httpx.ConnectError(
                        "[Errno 111] Connection refused",
                        request=httpx.Request(
                            "POST", "http://localhost:11434/api/chat"
                        ),
                    )
                case "bad_request":
                    raise BadRequestError(
                        f"LLM Provider NOT provided. Pass in the LLM provider you "
                        f"are trying to call. You passed model={model}"
                    )
                case "timeout":
                    raise APIConnectionError(f"Request to {model} timed out.")
                case _:
                    raise RuntimeError(
                        f"unknown PDL_TEST_MODEL behaviour: {_behaviour!r}"
                    )

        def _completion(model=None, messages=None, **kwargs):
            del model, messages, kwargs
            raise RuntimeError("synchronous litellm.completion is not stubbed")

        # Carry litellm's module name so tracebacks and reprs read the way a
        # real provider failure would, rather than naming this file.
        for _cls in (BadRequestError, APIConnectionError):
            _cls.__module__ = "litellm"

        def _module(name, **attributes):
            """Build a module object. `setattr` keeps mypy happy about a
            dynamically-populated ModuleType."""
            module = types.ModuleType(name)
            for attribute, value in attributes.items():
                setattr(module, attribute, value)
            return module

        _exceptions = _module(
            "litellm.exceptions",
            BadRequestError=BadRequestError,
            APIConnectionError=APIConnectionError,
        )
        # Only the handful of names PDL actually touches: `acompletion` and
        # `completion` in pdl_llms.py, and the three module-level flags set in
        # pdl_llms.py:42 and pdl_interpreter.py:2185.
        _stub = _module(
            "litellm",
            acompletion=_acompletion,
            completion=_completion,
            suppress_debug_info=True,
            input_callback=[],
            callbacks=[],
            BadRequestError=BadRequestError,
            APIConnectionError=APIConnectionError,
            exceptions=_exceptions,
            __version__="0.0.0-stub",
        )

        sys.modules["litellm"] = _stub
        sys.modules["litellm.exceptions"] = _exceptions
