"""Binder tests — all mocked, NO network.

`call_model` is injected with fake BinderReply objects so bind()'s parse, validate,
and punt logic is exercised offline. The `_call_gemini` and `_call_openrouter` taxonomy
tests (below) are the one place that monkeypatches urllib — the network transport is
the painful edge.
"""

from __future__ import annotations

import email.message
import inspect
import io
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from benchspec.grading import binder
from benchspec.grading.binder import _BINDING_PROMPT, BinderAuthError, BinderReply, bind
from benchspec.grading.binder_config import BinderConfig


def _reply(text: str) -> Callable[..., BinderReply]:
    """Build a call_model fixture that returns a fixed BinderReply."""
    return lambda prompt, *, timeout=60: BinderReply(
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

    assert spec is not None
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

    assert spec is not None
    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./.meta/templates/entity-person.md"


def test_bare_exists_accepts_trailing_period_without_losing_hidden_dot() -> None:
    """Verify bare exists accepts trailing period without losing hidden dot."""
    spec = bind("./.meta/templates/entity-person.md exists.", call_model=_fail_call_model)

    assert spec is not None
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

    assert spec is not None
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

    assert spec is not None
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

    assert spec is not None
    assert spec["checker"] == "file_exists"



def test_returned_spec_is_dispatchable(tmp_path: Path) -> None:
    """Verify returned spec is dispatchable."""
    # The bound spec must flow straight into the existing checker dispatch.
    from benchspec.grading.checkers import run_assertion

    (tmp_path / "out.md").write_text("hi")
    spec = bind(
        "the file out.md exists",
        call_model=_reply('{"checker":"file_exists","path":"out.md"}'),
    )

    assert spec is not None
    assert run_assertion(spec, tmp_path, {})["passed"] is True


def test_verbatim_regex_pattern_binds() -> None:
    """Verify a regex spec whose pattern is copied verbatim from the assertion binds."""
    assertion = "./answer.sql matches the regex \"(?i)title\\s+ILIKE\\s+'%gemini%'\""
    reply = _reply(
        '{"checker":"regex","path":"./answer.sql","pattern":"(?i)title\\\\s+ILIKE\\\\s+\'%gemini%\'"}'
    )

    spec = bind(assertion, call_model=reply)

    assert spec is not None
    assert spec["checker"] == "regex"
    assert spec["pattern"] == r"(?i)title\s+ILIKE\s+'%gemini%'"


def test_regex_pattern_with_appended_quote_punts() -> None:
    """Verify a regex pattern that grew a trailing quote is not a verbatim copy and punts."""
    assertion = "./answer.sql matches the regex \"(?i)title\\s+ILIKE\\s+'%gemini%'\""
    reply = _reply(
        '{"checker":"regex","path":"./answer.sql","pattern":"(?i)title\\\\s+ILIKE\\\\s+\'%gemini%\'\'"}'
    )

    spec = bind(assertion, call_model=reply)

    assert spec is None


def test_regex_pattern_with_dropped_backslash_punts() -> None:
    """Verify a regex pattern that lost a backslash is not a verbatim copy and punts."""
    assertion = "./answer.sql matches the regex \"(?i)title\\s+ILIKE\\s+'%gemini%'\""
    reply = _reply(
        '{"checker":"regex","path":"./answer.sql","pattern":"(?i)titles+ILIKE\\\\s+\'%gemini%\'"}'
    )

    spec = bind(assertion, call_model=reply)

    assert spec is None


def test_prose_line_without_a_named_regex_binds_a_translated_pattern() -> None:
    """Verify prose that names no regex still binds the model's anchored translation."""
    assertion = "./.meta/templates/entity-person.md has a line beginning 'aliases:'"
    reply = _reply(
        '{"checker":"regex","path":"./.meta/templates/entity-person.md","pattern":"^aliases:"}'
    )

    spec = bind(assertion, call_model=reply)

    assert spec == {
        "type": "deterministic",
        "checker": "regex",
        "path": "./.meta/templates/entity-person.md",
        "pattern": "^aliases:",
    }


def test_verbatim_guard_leaves_other_checkers_alone() -> None:
    """Verify a file_exists path absent from the assertion text still binds as before."""
    reply = _reply('{"checker":"file_exists","path":"report.md"}')

    spec = bind("the report file was written", call_model=reply)

    assert spec == {"type": "deterministic", "checker": "file_exists", "path": "report.md"}


def test_drifted_regex_punt_logs_the_pattern_at_info(caplog: pytest.LogCaptureFixture) -> None:
    """Verify the drifted-regex punt logs one info line naming the rejected pattern."""
    caplog.set_level(logging.INFO, logger="benchspec.grading.binder")
    assertion = "./answer.sql matches the regex \"(?i)title\\s+ILIKE\\s+'%gemini%'\""
    reply = _reply(
        '{"checker":"regex","path":"./answer.sql","pattern":"(?i)title\\\\s+ILIKE\\\\s+\'%gemini%\'\'"}'
    )

    bind(assertion, call_model=reply)

    assert r"(?i)title\s+ILIKE\s+'%gemini%''" in caplog.text
    assert "not verbatim" in caplog.text


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


def test_prompt_carries_the_verbatim_regex_rule_and_example() -> None:
    """Verify the rendered prompt spells out the verbatim-pattern rule with a quoted example."""
    prompt = _BINDING_PROMPT.format(assertion="MY ASSERTION")

    assert "character for character" in prompt
    assert "Assertion: ./x matches the regex \"(?i)foo\\s+'bar'\"" in prompt
    assert '{"checker":"regex","path":"./x","pattern":"(?i)foo\\\\s+\'bar\'"}' in prompt


def test_infra_error_propagates() -> None:
    """Verify infra error propagates."""

    def boom(prompt: str, *, timeout: float = 60) -> NoReturn:
        """Fail the way a flaky transport does."""
        raise RuntimeError("gemini transient failure")

    with pytest.raises(RuntimeError):
        bind("unknown assertion", call_model=boom)


def test_bind_propagates_binder_auth_error() -> None:
    """Verify a BinderAuthError from call_model is never swallowed as a punt."""

    def boom(prompt: str, *, timeout: float = 60) -> NoReturn:
        """Fail the way a rejected API key does."""
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        bind("unknown assertion", call_model=boom)


def test_bind_default_config_calls_gemini_with_its_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify bind with no config reaches the real Gemini transport on the default model."""
    captured: dict[str, str] = {}

    def fake_call_gemini(prompt: str, *, timeout: float, model: str) -> BinderReply:
        """Stand in for the Gemini transport, recording the model it was asked for."""
        captured["model"] = model
        return BinderReply(text='{"punt":true}', prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_gemini", fake_call_gemini)

    assert bind("the note reads well") is None
    assert captured["model"] == "gemini-3.5-flash-lite"


def test_bind_openrouter_config_calls_openrouter_with_its_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an `openrouter` config routes bind through the OpenRouter transport."""
    captured: dict[str, str] = {}

    def fake_call_openrouter(prompt: str, *, timeout: float, model: str) -> BinderReply:
        """Stand in for the OpenRouter transport, recording the model it was asked for."""
        captured["model"] = model
        return BinderReply(text='{"punt":true}', prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_openrouter", fake_call_openrouter)
    config = BinderConfig(provider="openrouter", model="google/gemini-3.5-flash")

    assert bind("the note reads well", config=config) is None
    assert captured["model"] == "google/gemini-3.5-flash"


def test_binder_for_fixes_the_config_in_the_one_argument_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `binder_for(config)` is a plain `bind(text)` that carries the config."""
    seen: list[str] = []

    def fake_call_openrouter(prompt: str, *, timeout: float, model: str) -> BinderReply:
        """Record that the OpenRouter transport was the one called."""
        seen.append(model)
        return BinderReply(text='{"punt":true}', prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_openrouter", fake_call_openrouter)
    bound = binder.binder_for(BinderConfig(provider="openrouter", model="google/gemini-3.5-flash"))

    assert bound("the note reads well") is None
    assert seen == ["google/gemini-3.5-flash"]


class _CannedResponse:
    """A urlopen context manager whose body is a fixed byte string."""

    def __init__(self, payload: bytes) -> None:
        """Store the bytes `read()` hands back."""
        self._payload = payload

    def __enter__(self) -> _CannedResponse:
        """Enter the context, yielding the response itself."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave the context; nothing to release."""
        return None

    def read(self) -> bytes:
        """Return the canned body."""
        return self._payload


Urlopen = Callable[[urllib.request.Request, float], _CannedResponse]


def _http_response(body: dict) -> Urlopen:
    """Build a urlopen stub returning `body` as JSON."""

    def respond(request: urllib.request.Request, timeout: float) -> _CannedResponse:
        """Answer any request with the canned JSON body."""
        return _CannedResponse(json.dumps(body).encode("utf-8"))

    return respond


def _http_error(code: int, body: str) -> Urlopen:
    """Build a urlopen stub raising HTTPError with the given status and body."""

    def raise_it(request: urllib.request.Request, timeout: float) -> NoReturn:
        """Fail every request with the configured HTTP status."""
        raise urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com/x",
            code,
            "err",
            hdrs=email.message.Message(),
            fp=io.BytesIO(body.encode("utf-8")),
        )

    return raise_it


def test_call_gemini_returns_reply_with_text_usage_and_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the request carries x-goog-api-key, temperature 0, and JSON mime."""
    captured_headers: dict[str, str] = {}
    captured_body: dict = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _CannedResponse:
        """Record the outgoing headers and JSON body, then answer with an empty verdict."""
        captured_headers.update(request.header_items())
        assert isinstance(request.data, bytes)
        captured_body.update(json.loads(request.data))
        return _http_response({
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
        })(request, timeout)

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_gemini("prompt")

    assert captured_headers["X-goog-api-key"] == "test-key"
    assert captured_body["generationConfig"]["temperature"] == 0
    assert captured_body["generationConfig"]["responseMimeType"] == "application/json"


def test_call_gemini_uses_the_passed_model_in_the_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _call_gemini's `model` keyword controls the request URL — no env var involved.

    BENCHSPEC_BINDER_MODEL is a corpus-suite knob, read only by the corpus's recording
    wrapper; the production transport must stay env-independent.
    """
    captured: dict[str, str] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _CannedResponse:
        """Record the request URL, then answer with an empty verdict."""
        captured["url"] = request.full_url
        respond = _http_response({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        return respond(request, timeout)

    monkeypatch.delenv("BENCHSPEC_BINDER_MODEL", raising=False)
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_gemini("prompt", model="gemini-3.1-flash")

    assert "gemini-3.1-flash" in captured["url"]
    assert "gemini-3.5-flash-lite" not in captured["url"]


def test_call_gemini_defaults_to_gemini_binder_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the `model` keyword's default is the fixed production constant, not an env read."""
    monkeypatch.delenv("BENCHSPEC_BINDER_MODEL", raising=False)
    assert (
        inspect.signature(binder._call_gemini).parameters["model"].default
        == binder.GEMINI_BINDER_MODEL
    )


@pytest.mark.parametrize(("code", "body"), [(401, "unauthorized"), (403, "forbidden")])
def test_call_gemini_raises_binder_auth_error_on_401_403(
    monkeypatch: pytest.MonkeyPatch, code: int, body: str
) -> None:
    """Verify 401/403 raise BinderAuthError."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(code, body))
    with pytest.raises(BinderAuthError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_binder_auth_error_on_400_api_key_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a 400 body naming API_KEY_INVALID raises BinderAuthError, not RuntimeError."""
    monkeypatch.setattr(
        binder.urllib.request, "urlopen",
        _http_error(400, '{"error":{"status":"API_KEY_INVALID"}}'),
    )
    with pytest.raises(BinderAuthError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_other_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a 400 NOT naming API_KEY_INVALID stays a RuntimeError, not auth."""
    error_body = '{"error":{"status":"INVALID_ARGUMENT"}}'
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(400, error_body))
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a transport-level URLError normalizes to RuntimeError."""

    def raise_it(request: urllib.request.Request, timeout: float) -> NoReturn:
        """Fail at the transport layer."""
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(binder.urllib.request, "urlopen", raise_it)
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a socket timeout normalizes to RuntimeError."""

    def raise_it(request: urllib.request.Request, timeout: float) -> NoReturn:
        """Fail the way a socket timeout does."""
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
    monkeypatch: pytest.MonkeyPatch, payload: dict
) -> None:
    """Verify every degenerate-200 shape normalizes to RuntimeError, never a silent punt."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_response(payload))
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_call_gemini_raises_runtimeerror_on_malformed_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a non-JSON 200 body normalizes to RuntimeError, not a raw ValueError."""
    monkeypatch.setattr(
        binder.urllib.request,
        "urlopen",
        lambda request, timeout: _CannedResponse(b"not json"),
    )
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")


def test_preflight_verify_binder_key_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify preflight raises RuntimeError when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        binder.preflight_verify_binder_key(BinderConfig())


def test_preflight_verify_binder_key_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a set-but-empty GEMINI_API_KEY counts as missing."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        binder.preflight_verify_binder_key(BinderConfig())


def test_preflight_verify_binder_key_passes_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify preflight passes with a non-empty key."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    binder.preflight_verify_binder_key(BinderConfig())  # no raise


_OPENROUTER_CONFIG = BinderConfig(provider="openrouter", model="google/gemini-3.5-flash-lite")


def test_preflight_verify_binder_key_under_openrouter_names_openrouter_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `openrouter` the Gemini key is irrelevant; the OpenRouter key is what is named."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is required") as exc_info:
        binder.preflight_verify_binder_key(_OPENROUTER_CONFIG)

    assert "GEMINI_API_KEY" not in str(exc_info.value)


def test_preflight_verify_binder_key_under_openrouter_passes_with_only_that_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an OpenRouter binder needs no Gemini credential at all."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    binder.preflight_verify_binder_key(_OPENROUTER_CONFIG)  # no raise


def test_binder_credential_env_by_provider() -> None:
    """Verify each provider maps to the host variable it authenticates with."""
    assert binder.binder_credential_env(BinderConfig()) == "GEMINI_API_KEY"
    assert binder.binder_credential_env(_OPENROUTER_CONFIG) == "OPENROUTER_API_KEY"


def _openrouter_reply(content: object, **choice_extra: object) -> dict:
    """Build a chat-completions payload whose first choice carries `content`."""
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, **choice_extra}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def test_call_openrouter_returns_reply_with_text_usage_and_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the OpenRouter reply lands in the same BinderReply shape Gemini's does."""
    monkeypatch.setattr(
        binder.urllib.request, "urlopen", _http_response(_openrouter_reply('{"punt":true}'))
    )

    reply = binder._call_openrouter("prompt")

    assert reply.text == '{"punt":true}'
    assert reply.prompt_tokens == 12
    assert reply.output_tokens == 3
    assert reply.latency_ms >= 0


def test_call_openrouter_sends_bearer_key_json_mode_temperature_zero_and_required_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the request body pins the model, temperature, JSON mode, and require_parameters."""
    captured_headers: dict[str, str] = {}
    captured_body: dict = {}
    captured: dict[str, str] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _CannedResponse:
        """Record the outgoing request, then answer with an empty verdict."""
        captured["url"] = request.full_url
        captured_headers.update(request.header_items())
        assert isinstance(request.data, bytes)
        captured_body.update(json.loads(request.data))
        return _http_response(_openrouter_reply("{}"))(request, timeout)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_openrouter("prompt", model="google/gemini-3.5-flash")

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured_headers["Authorization"] == "Bearer sk-or-test"
    assert captured_body["model"] == "google/gemini-3.5-flash"
    assert captured_body["messages"] == [{"role": "user", "content": "prompt"}]
    assert captured_body["temperature"] == 0
    assert captured_body["response_format"] == {"type": "json_object"}
    assert captured_body["provider"] == {"require_parameters": True}


def test_call_openrouter_defaults_to_the_openrouter_binder_model() -> None:
    """Verify the default model is the vendor-prefixed slug of the same Gemini model."""
    assert (
        inspect.signature(binder._call_openrouter).parameters["model"].default
        == "google/gemini-3.5-flash-lite"
    )


@pytest.mark.parametrize(("code", "body"), [(401, "bad key"), (402, "insufficient credits")])
def test_call_openrouter_raises_binder_auth_error_on_401_402(
    monkeypatch: pytest.MonkeyPatch, code: int, body: str
) -> None:
    """Verify a rejected key or an unfunded account stops the run as BinderAuthError."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(code, body))

    with pytest.raises(BinderAuthError, match=str(code)):
        binder._call_openrouter("prompt")


@pytest.mark.parametrize("code", [403, 429, 500, 502, 503])
def test_call_openrouter_raises_runtimeerror_on_non_auth_http_errors(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Verify moderation (403), rate limits, and 5xx are RuntimeError, never auth."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_error(code, "nope"))

    with pytest.raises(RuntimeError) as exc_info:
        binder._call_openrouter("prompt")

    assert not isinstance(exc_info.value, BinderAuthError)


def test_call_openrouter_raises_runtimeerror_on_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a transport-level URLError normalizes to RuntimeError."""

    def raise_it(request: urllib.request.Request, timeout: float) -> NoReturn:
        """Fail at the transport layer."""
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(binder.urllib.request, "urlopen", raise_it)
    with pytest.raises(RuntimeError):
        binder._call_openrouter("prompt")


def test_call_openrouter_raises_runtimeerror_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a socket timeout normalizes to RuntimeError."""

    def raise_it(request: urllib.request.Request, timeout: float) -> NoReturn:
        """Fail the way a socket timeout does."""
        raise TimeoutError("timed out")

    monkeypatch.setattr(binder.urllib.request, "urlopen", raise_it)
    with pytest.raises(RuntimeError):
        binder._call_openrouter("prompt")


@pytest.mark.parametrize("payload", [
    {"choices": []},
    {"error": {"message": "no choices at all"}},
    _openrouter_reply("{}", error={"code": 502, "message": "upstream died"}),
    _openrouter_reply("{}", finish_reason="error"),
    _openrouter_reply(""),
    _openrouter_reply(None),
    _openrouter_reply(1),
    {"choices": ["not an object"]},
])
def test_call_openrouter_raises_runtimeerror_on_degenerate_200(
    monkeypatch: pytest.MonkeyPatch, payload: dict
) -> None:
    """Verify every degenerate-200 shape, including a relayed provider error, is RuntimeError."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_response(payload))

    with pytest.raises(RuntimeError):
        binder._call_openrouter("prompt")


def test_call_openrouter_raises_runtimeerror_on_malformed_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a non-JSON 200 body normalizes to RuntimeError, not a raw ValueError."""
    monkeypatch.setattr(
        binder.urllib.request,
        "urlopen",
        lambda request, timeout: _CannedResponse(b"not json"),
    )
    with pytest.raises(RuntimeError):
        binder._call_openrouter("prompt")


def test_openrouter_url_is_built_from_the_api_path_constant() -> None:
    """Verify `_OPENROUTER_URL` stays a single source of truth with `OPENROUTER_API_PATH`."""
    assert binder._OPENROUTER_URL.startswith(f"https://{binder.OPENROUTER_API_PATH}")


def test_binder_identity_matches_spec_shape() -> None:
    """Verify binder_identity() returns the exact spec 82-86 dict."""
    assert binder.binder_identity() == {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "api_path": "generativelanguage.googleapis.com/v1beta",
    }


def test_binder_identity_reports_the_configured_openrouter_transport() -> None:
    """Verify binder_identity() reflects the resolved provider, model, and API path."""
    config = BinderConfig(provider="openrouter", model="google/gemini-3.5-flash-lite")

    identity = binder.binder_identity(config)

    assert identity == {
        "provider": "openrouter",
        "model": "google/gemini-3.5-flash-lite",
        "api_path": "openrouter.ai/api/v1",
    }


def test_binder_identity_contains_no_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify binder_identity() never reads or leaks GEMINI_API_KEY."""
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    identity = binder.binder_identity()
    assert "GEMINI_API_KEY" not in repr(identity)
    assert "super-secret-value" not in repr(identity)
    assert set(identity) == {"provider", "model", "api_path"}


def test_gemini_url_is_built_from_the_api_path_constant() -> None:
    """Verify `_GEMINI_URL` stays a single source of truth with `GEMINI_API_PATH`."""
    assert binder._GEMINI_URL.startswith(f"https://{binder.GEMINI_API_PATH}")
