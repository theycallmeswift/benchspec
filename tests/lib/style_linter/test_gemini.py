"""Tests for Gemini transport payload validation."""

from __future__ import annotations

import json

import pytest

from tests.lib.style_linter._helpers import framework


def test_call_gemini_rejects_malformed_api_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject partial Gemini payloads that omit the expected content shape."""
    style_lint = framework()

    class _Response:
        """Minimal context manager response for urllib stubs."""

        def __init__(self, payload: object) -> None:
            self._payload = payload

        def read(self) -> bytes:
            """Return encoded payload bytes."""
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self) -> _Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the response context."""
            return None

    malformed_payloads = [
        {"candidates": []},
        {"candidates": [{}]},
        {"candidates": [{"content": {}}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": ["text"]}}]},
        {"candidates": [{"content": {"parts": [{"text": 1}]}}]},
    ]

    for payload in malformed_payloads:
        monkeypatch.setattr(
            style_lint.urllib.request,
            "urlopen",
            lambda request, *, timeout, payload=payload: _Response(payload),
        )

        with pytest.raises(ValueError, match="Gemini response"):
            style_lint.call_gemini(
                prompt="{}",
                api_key="test-key",
                model="gemini-test",
                timeout=1.0,
            )
