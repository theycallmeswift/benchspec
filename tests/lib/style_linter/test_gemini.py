"""Tests for Gemini transport payload validation."""

from __future__ import annotations

import json
from types import ModuleType
from urllib.request import Request

import pytest


def test_call_gemini_rejects_malformed_api_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Reject partial Gemini payloads that omit the expected content shape."""

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
            framework.urllib.request,
            "urlopen",
            lambda request, *, timeout, payload=payload: _Response(payload),
        )

        with pytest.raises(ValueError, match="Gemini response"):
            framework.call_gemini(
                prompt="{}",
                api_key="test-key",
                model="gemini-test",
                timeout=1.0,
            )


def test_call_gemini_uses_deterministic_generation_config(
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Pin detector sampling so advisory runs are less variable."""

    class _Response:
        """Minimal context manager response for urllib stubs."""

        def read(self) -> bytes:
            """Return a valid Gemini payload."""
            return json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
            ).encode("utf-8")

        def __enter__(self) -> _Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the response context."""
            return None

    captured_body: dict[str, object] = {}

    def fake_urlopen(request: Request, *, timeout: float) -> _Response:
        captured_body.update(json.loads(request.data or b"{}"))
        assert timeout == 1.0
        return _Response()

    monkeypatch.setattr(framework.urllib.request, "urlopen", fake_urlopen)

    framework.call_gemini(
        prompt="{}",
        api_key="test-key",
        model="gemini-test",
        timeout=1.0,
    )

    assert captured_body["generationConfig"] == {
        "responseMimeType": "application/json",
        "temperature": 0,
    }
