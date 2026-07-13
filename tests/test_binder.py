"""Binder tests — all mocked, NO network.

`call_model` is injected with fake GeminiReply objects so bind()'s parse, validate,
and punt logic is exercised offline. `_call_gemini` taxonomy tests (below) are the
one place that monkeypatches urllib — the network transport is the painful edge.
"""

from __future__ import annotations

import inspect
import io
import json
import urllib.error
from typing import NoReturn

import pytest

from evalspec import binder
from evalspec.binder import _BINDING_PROMPT, BinderAuthError, GeminiReply, bind


def _reply(text: str) -> object:
    """Build a call_model fixture that returns a fixed GeminiReply."""
    return lambda prompt, *, timeout=60: GeminiReply(
        text=text, prompt_tokens=0, output_tokens=0, latency_ms=0.0
    )


def _fail_call_model(*args: object, **kwargs: object) -> NoReturn:
    """Build a call_model fixture that fails the test if invoked."""
    raise AssertionError("model binder should not be called")


def test_bind_file_exists() -> None:
    """Verify bind file exists."""
    spec = bind(
        "the file out.md exists",
        call_model=_reply('{"checker":"file_exists","path":"out.md"}'),
    )
    assert spec["checker"] == "file_exists"
    assert spec["path"] == "out.md"


def test_bare_exists_hidden_template_path_binds_without_host_call() -> None:
    """Verify bare exists hidden template path binds without host call."""
    spec = bind("./.meta/templates/entity-person.md exists", call_model=_fail_call_model)

    assert spec == {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "./.meta/templates/entity-person.md",
    }


def test_bare_exists_strips_quotes_without_losing_hidden_dot() -> None:
    """Verify bare exists strips quotes without losing hidden dot."""
    spec = bind("'./.meta/templates/entity-person.md' exists", call_model=_fail_call_model)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./.meta/templates/entity-person.md"


def test_bare_exists_accepts_trailing_period_without_losing_hidden_dot() -> None:
    """Verify bare exists accepts trailing period without losing hidden dot."""
    spec = bind("./.meta/templates/entity-person.md exists.", call_model=_fail_call_model)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./.meta/templates/entity-person.md"


def test_compound_hidden_path_assertion_punts() -> None:
    """Verify compound hidden path assertion punts."""
    spec = bind(
        "./.meta/templates/entity-person.md exists and contains frontmatter",
        call_model=_reply('{"punt":true,"reason":"compound"}'),
    )

    assert spec is None


def test_descriptive_exists_prose_preserves_model_bound_path() -> None:
    """Verify descriptive exists prose preserves model bound path."""
    spec = bind(
        "The previously-missing ./.obsidian/ configuration now exists",
        call_model=_reply('{"checker":"file_exists","path":"./.obsidian/"}'),
    )

    assert spec["path"] == "./.obsidian/"


def test_non_path_exists_prose_punts() -> None:
    """Verify non path exists prose punts."""
    assert (
        bind(
            "the success message now exists",
            call_model=_reply('{"punt":true,"reason":"not a path assertion"}'),
        )
        is None
    )


def test_directory_created_path_shape_binds_without_host_call() -> None:
    """Verify directory created path shape binds without host call."""
    spec = bind("the ./output/ directory was created", call_model=_fail_call_model)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./output/"


def test_activation_line_binds_via_model() -> None:
    """Verify an activation line reaches the model and binds to its checker.

    There is no offline skill recognizer — the binder handles activation like any other
    assertion; the corpus gate (`make evals`) covers the model's reliability.
    """
    spec = bind(
        "Skill `ingest` invoked",
        call_model=_reply('{"checker":"skill_invoked","skill":"ingest"}'),
    )

    assert spec == {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}


def test_punt_explicit() -> None:
    """Verify punt explicit."""
    assert (
        bind("the note reads well", call_model=_reply('{"punt":true,"reason":"semantic"}'))
        is None
    )


def test_punt_on_garbage() -> None:
    """Verify punt on garbage."""
    assert bind("unknown assertion", call_model=_reply("here you go: not json at all")) is None


def test_punt_on_unknown_checker() -> None:
    """Verify punt on unknown checker."""
    assert bind("unknown assertion", call_model=_reply('{"checker":"vibes","path":"a"}')) is None


def test_punt_on_schema_invalid() -> None:  # glob_count needs exactly one of count/min
    """Verify punt on schema invalid."""
    assert bind(
        "unknown assertion",
        call_model=_reply('{"checker":"glob_count","glob":"*.md"}'),
    ) is None


def test_parses_fenced_json() -> None:
    """Verify parses fenced json."""
    spec = bind(
        "unknown assertion",
        call_model=_reply('```json\n{"checker":"file_exists","path":"a.md"}\n```'),
    )
    assert spec["checker"] == "file_exists"



def test_returned_spec_is_dispatchable(tmp_path: object) -> None:
    """Verify returned spec is dispatchable."""
    # The bound spec must flow straight into the existing checker dispatch.
    from evalspec.checkers import run_assertion

    (tmp_path / "out.md").write_text("hi")
    spec = bind(
        "the file out.md exists",
        call_model=_reply('{"checker":"file_exists","path":"out.md"}'),
    )
    assert run_assertion(spec, tmp_path, {})["passed"] is True


def test_prompt_carries_load_bearing_pieces() -> None:
    """Verify prompt carries load bearing pieces."""
    prompt = _BINDING_PROMPT.format(assertion="MY ASSERTION")
    assert "MY ASSERTION" in prompt
    for name in (
        "file_exists",
        "glob_count",
        "frontmatter_has",
        "regex",
        "sha256_match",
        "skill_invoked",
    ):
        assert name in prompt
    assert "not duplicated" in prompt  # A9 rule encoded


def test_infra_error_propagates() -> None:
    """Verify infra error propagates."""

    def boom(prompt: object, *, timeout: object = 60) -> NoReturn:
        raise RuntimeError("gemini transient failure")

    with pytest.raises(RuntimeError):
        bind("unknown assertion", call_model=boom)


def test_bind_propagates_binder_auth_error() -> None:
    """Verify a BinderAuthError from call_model is never swallowed as a punt."""

    def boom(prompt: object, *, timeout: object = 60) -> NoReturn:
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        bind("unknown assertion", call_model=boom)


def test_bind_default_call_model_is_call_gemini() -> None:
    """Verify bind's default transport is the real Gemini call — the wiring proof."""
    assert inspect.signature(bind).parameters["call_model"].default is binder._call_gemini


def _http_response(body: dict) -> object:
    """Build a urlopen-context-manager stub returning body as JSON."""

    class _Resp:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(body).encode("utf-8")

    return lambda request, timeout: _Resp()


def _http_error(code: int, body: str) -> object:
    """Build a urlopen stub raising HTTPError with the given status and body."""

    def raise_it(request: object, timeout: object) -> NoReturn:
        raise urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com/x",
            code,
            "err",
            hdrs=None,
            fp=io.BytesIO(body.encode("utf-8")),
        )

    return raise_it


def test_call_gemini_returns_reply_with_text_usage_and_latency(monkeypatch: object) -> None:
    """Verify call_gemini returns reply with text usage and latency."""
    monkeypatch.setattr(
        binder.urllib.request, "urlopen",
        _http_response({
            "candidates": [{"content": {"parts": [{"text": '{"punt":true}'}]}}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3},
        }),
    )
    reply = binder._call_gemini("prompt")
    assert reply.text == '{"punt":true}'
    assert reply.prompt_tokens == 12
    assert reply.output_tokens == 3
    assert reply.latency_ms >= 0


def test_call_gemini_sends_api_key_header_temperature_zero_and_json_mime(
    monkeypatch: object, tmp_path: object,
) -> None:
    """Verify the request carries x-goog-api-key, temperature 0, and JSON mime."""
    captured = {}

    def fake_urlopen(request: object, timeout: object) -> object:
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        return _http_response({
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
        })(request, timeout)

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_gemini("prompt")

    assert captured["headers"]["X-goog-api-key"] == "test-key"
    assert captured["body"]["generationConfig"]["temperature"] == 0
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_call_gemini_uses_the_passed_model_in_the_request_url(monkeypatch: object) -> None:
    """Verify _call_gemini's `model` keyword controls the request URL — no env var involved.

    EVALSPEC_BINDER_MODEL is a corpus-suite knob, read only by the corpus's recording
    wrapper; the production transport must stay env-independent.
    """
    captured = {}

    def fake_urlopen(request: object, timeout: object) -> object:
        captured["url"] = request.full_url
        respond = _http_response({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        return respond(request, timeout)

    monkeypatch.delenv("EVALSPEC_BINDER_MODEL", raising=False)
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_gemini("prompt", model="gemini-3.1-flash")

    assert "gemini-3.1-flash" in captured["url"]
    assert "gemini-3.1-flash-lite" not in captured["url"]


def test_call_gemini_defaults_to_gemini_binder_model(monkeypatch: object) -> None:
    """Verify the `model` keyword's default is the fixed production constant, not an env read."""
    monkeypatch.delenv("EVALSPEC_BINDER_MODEL", raising=False)
    assert (
        inspect.signature(binder._call_gemini).parameters["model"].default
        == binder.GEMINI_BINDER_MODEL
    )


@pytest.mark.parametrize(("code", "body"), [(401, "unauthorized"), (403, "forbidden")])
def test_call_gemini_raises_binder_auth_error_on_401_403(
    monkeypatch: object, code: int, body: str
) -> None:
    """Verify 401/403 raise BinderAuthError."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(code, body))
    with pytest.raises(BinderAuthError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_binder_auth_error_on_400_api_key_invalid(monkeypatch: object) -> None:
    """Verify a 400 body naming API_KEY_INVALID raises BinderAuthError, not RuntimeError."""
    monkeypatch.setattr(
        binder.urllib.request, "urlopen",
        _http_error(400, '{"error":{"status":"API_KEY_INVALID"}}'),
    )
    with pytest.raises(BinderAuthError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_other_400(monkeypatch: object) -> None:
    """Verify a 400 NOT naming API_KEY_INVALID stays a RuntimeError, not auth."""
    error_body = '{"error":{"status":"INVALID_ARGUMENT"}}'
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(400, error_body))
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_url_error(monkeypatch: object) -> None:
    """Verify a transport-level URLError normalizes to RuntimeError."""

    def raise_it(request: object, timeout: object) -> NoReturn:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(binder.urllib.request, "urlopen", raise_it)
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_timeout(monkeypatch: object) -> None:
    """Verify a socket timeout normalizes to RuntimeError."""

    def raise_it(request: object, timeout: object) -> NoReturn:
        raise TimeoutError("timed out")

    monkeypatch.setattr(binder.urllib.request, "urlopen", raise_it)
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


@pytest.mark.parametrize("payload", [
    {"candidates": []},
    {"candidates": [{"content": {"parts": []}}]},
    {"candidates": [{"content": {}, "finishReason": "SAFETY"}]},
    {"candidates": [{"content": {}, "finishReason": "MAX_TOKENS"}]},
    {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
    {"candidates": [{"content": {"parts": [{"text": 1}]}}]},
])
def test_call_gemini_raises_runtimeerror_on_degenerate_200(
    monkeypatch: object, payload: dict
) -> None:
    """Verify every degenerate-200 shape normalizes to RuntimeError, never a silent punt."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_response(payload))
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_malformed_json_body(monkeypatch: object) -> None:
    """Verify a non-JSON 200 body normalizes to RuntimeError, not a raw ValueError."""

    class _Resp:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not json"

    monkeypatch.setattr(binder.urllib.request, "urlopen", lambda request, timeout: _Resp())
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_preflight_gemini_key_raises_when_unset(monkeypatch: object) -> None:
    """Verify preflight raises RuntimeError when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        binder.preflight_gemini_key()


def test_preflight_gemini_key_raises_when_empty(monkeypatch: object) -> None:
    """Verify a set-but-empty GEMINI_API_KEY counts as missing."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        binder.preflight_gemini_key()


def test_preflight_gemini_key_passes_when_set(monkeypatch: object) -> None:
    """Verify preflight passes with a non-empty key."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    binder.preflight_gemini_key()  # no raise


def test_binder_identity_matches_spec_shape() -> None:
    """Verify binder_identity() returns the exact spec 82-86 dict."""
    assert binder.binder_identity() == {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "api_path": "generativelanguage.googleapis.com/v1beta",
    }


def test_binder_identity_contains_no_key_material(monkeypatch: object) -> None:
    """Verify binder_identity() never reads or leaks GEMINI_API_KEY."""
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    identity = binder.binder_identity()
    assert "GEMINI_API_KEY" not in repr(identity)
    assert "super-secret-value" not in repr(identity)
    assert set(identity) == {"provider", "model", "api_path"}


def test_gemini_url_is_built_from_the_api_path_constant() -> None:
    """Verify `_GEMINI_URL` stays a single source of truth with `GEMINI_API_PATH`."""
    assert binder._GEMINI_URL.startswith(f"https://{binder.GEMINI_API_PATH}")
