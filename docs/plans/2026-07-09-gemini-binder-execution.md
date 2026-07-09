# Gemini Binder + Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the binder's host-Claude transport with a fixed, stdlib-only Gemini API call (`gemini-3.1-flash-lite`), give the binder's failure taxonomy a total, non-degradable `BinderAuthError` carve-out, make binder degradation to judge grading visible instead of silent, delete `agents/judge_cli.py` now that the binder no longer needs it, and relocate + extend the binder corpus suite with latency/cost reporting under `evals/binder/`.

**Architecture:** `binder.py` grows a `_call_gemini` transport (mirroring `lib/style_lint/gemini.py`'s request shape, with no shared import — the wheel ships only `src/evalspec`) that returns a `GeminiReply` dataclass and raises exactly two exception types: `BinderAuthError` (credential rejected, never degradable) or `RuntimeError` (everything else). `bind()`'s injection point renames `call_host` → `call_model`, defaulting to `_call_gemini`. `execution.py`'s `_grade_mixed` narrows its except tuple to `RuntimeError` only and counts each degradation once per `bind_cache` miss; the count rides in `grading.json` as `binder_degraded`, gets summed by `report._arm_stats`, and `plugin.py`'s `pytest_sessionfinish` appends a WARN summary line when nonzero — mirroring the existing `fail_under` gate pattern. `agents/judge_cli.py` is deleted in one atomic commit once the binder no longer imports it: its one surviving helper (`raise_for_is_error_envelope`) moves into `agents/claude.py`, the module's only remaining consumer. The corpus suite moves to `evals/binder/{corpus.yaml,conftest.py,test_corpus.py,test_corpus_integrity.py}`, and each per-draw record gains `source`/`attempts`/`latency_ms`/token counts by having the corpus inject its own recording `call_model` wrapper around `_call_gemini`.

**Tech Stack:** Python 3.10+, stdlib `urllib.request`/`urllib.error` (no new dependency), `pytest` 8, `pyyaml` (corpus fixtures).

## Global Constraints

- `GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"` is a fixed module constant in `binder.py` — no `[tool.evalspec.binder]` table, no CLI flag. `EVALSPEC_BINDER_MODEL` is read *inside* `_call_gemini` via `os.environ.get("EVALSPEC_BINDER_MODEL", GEMINI_BINDER_MODEL)` so it overrides the model for every caller (production and corpus) without threading a model parameter through `bind()`.
- The failure taxonomy is total: no raw `urllib`/socket/JSON exception may escape `_call_gemini`. Every failure normalizes to `BinderAuthError` (HTTP 401, 403, or 400 with `API_KEY_INVALID` in the body) or `RuntimeError` (every other HTTP status, transport failure, or degenerate 200 — no candidates, empty parts, `finishReason` `SAFETY`/`MAX_TOKENS`, `promptFeedback` block).
- `BinderAuthError` is deliberately **not** a `RuntimeError` subclass. Every `except RuntimeError` / `except (RuntimeError, ...)` site this plan touches (execution.py's degrade path, the corpus retry loop) must let it propagate unchanged.
- `bind()` keeps its `dict | None` outward contract; `_parse_binding` never raises.
- House style applies to every touched file: `from __future__ import annotations` first import, Google-style docstrings on everything (including private helpers), no `# noqa` / `# type: ignore`, `textwrap.dedent` for multiline strings, keyword-first signatures, DAMP four-phase tests, mock only the painful edge (here: the network transport), conventional-commit messages.
- Live corpus runs (`make evals:binder`) cost money and need `GEMINI_API_KEY` — never part of a task's offline verification command. Each task's verification is `make test` and/or `make lint` only; live-run commands are called out explicitly where relevant and re-collected in the final Verification section.

---

## File Structure

| File | Change |
|---|---|
| `src/evalspec/binder.py` | Gemini transport (`GEMINI_BINDER_MODEL`, `BinderAuthError`, `GeminiReply`, `_call_gemini`), `bind(call_model=...)`, `_parse_binding` envelope-unwrap removal, `preflight_gemini_key()`. |
| `src/evalspec/execution.py` | `_grade_mixed` except-tuple narrows to `RuntimeError`, counts `binder_degraded`; `run_eval_arm` stamps it into `grading.json`; dead `import subprocess` removed. |
| `src/evalspec/report.py` | `_arm_stats` sums `binder_degraded` across a run's `grading.json` files into the per-arm stats dict. |
| `src/evalspec/plugin.py` | `pytest_sessionfinish` appends a WARN summary line when the run-wide `binder_degraded` total is nonzero. |
| `src/evalspec/agents/judge_cli.py` | **Deleted.** |
| `src/evalspec/agents/claude.py` | Gains a private `_raise_for_is_error_envelope`; `judge()` docstring no longer references `judge_cli`. |
| `src/evalspec/judges/__init__.py` | Module docstring's `judge_cli` reference rewritten. |
| `src/evalspec/cases.py` | `judge_config` fixture calls `binder.preflight_gemini_key()` before the binary preflight. |
| `evals/binder/corpus.yaml` | Renamed from `evals/binder_corpus.yaml`. |
| `evals/binder/conftest.py` | Renamed from `evals/conftest.py`; gains a `GEMINI_API_KEY` preflight in `pytest_configure`. |
| `evals/binder/test_corpus.py` | Renamed from `evals/test_binder_corpus.py`; retry tuple narrows to `RuntimeError`; draws record `source`/`attempts`/`latency_ms`/tokens via a recording `call_model`. |
| `evals/binder/test_corpus_integrity.py` | Renamed from `evals/test_binder_corpus_integrity.py`; `CORPUS_PATH` points at the renamed `corpus.yaml`. |
| `evals/test_binder_corpus.py`, `evals/test_binder_corpus_integrity.py`, `evals/binder_corpus.yaml`, `evals/conftest.py` | **Deleted** (relocated above). |
| `Makefile` | `evals` target's positional arg → `evals/binder`; `BINDER_WORKERS` comment updated. |
| `pyproject.toml` | `binder_corpus` marker wording drops "Haiku". |
| `tests/test_binder.py` | Near-total rewrite: `call_model`/`GeminiReply` fixtures, `_call_gemini` taxonomy tests, `preflight_gemini_key` tests. |
| `tests/test_execution.py` | Timeout-punt test reworked to `RuntimeError`; new `binder_degraded` counting + `BinderAuthError` propagation tests; `judge_cli` monkeypatch retargeted. |
| `tests/conftest.py` | `seed_arm` gains a `binder_degraded: int = 0` keyword. |
| `tests/test_report.py` | New `_arm_stats` binder-degraded aggregation test. |
| `tests/test_plugin.py` | New binder-degraded WARN-line tests; new `GEMINI_API_KEY` preflight fixture tests. |
| `tests/agents/test_judge_cli.py` | **Deleted.** |
| `docs/configuration.md`, `docs/agents.md`, `docs/quickstart.md`, `docs/concepts.md` | Prose sync per the spec's Documentation Plan. |

---

### Task 1: Binder Gemini transport, failure taxonomy, and parse contract

**Files:**
- Modify: `src/evalspec/binder.py`
- Modify: `tests/test_binder.py` (near-total rewrite)

**Interfaces:**
- Produces: `GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"`, `class BinderAuthError(Exception)`, `@dataclass(frozen=True) class GeminiReply: text: str; prompt_tokens: int; output_tokens: int; latency_ms: float`, `_call_gemini(prompt: str, *, timeout: float = 60.0) -> GeminiReply`, `bind(assertion_text: str, *, call_model: object = _call_gemini) -> dict | None` (renamed `call_host` → `call_model`, `model` param dropped), `_parse_binding(text: str) -> dict | None` (envelope-unwrap removed — takes reply text directly).
- Consumes: `os`, `time`, `urllib.request`, `urllib.error` (new imports); existing `_balanced_objects`, `_validate_checker_obj`, `SchemaError`, `_bind_bare_exists` unchanged.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/test_binder.py` in two parts.

Part A — `bind()`'s existing parse/validate/punt tests, ported from `call_host` (string-returning, envelope-wrapped) to `call_model` (returns `GeminiReply`, no envelope):

```python
"""Binder tests — all mocked, NO network.

`call_model` is injected with fake GeminiReply objects so bind()'s parse, validate,
and punt logic is exercised offline. `_call_gemini` taxonomy tests (below) are the
one place that monkeypatches urllib — the network transport is the painful edge.
"""

from __future__ import annotations

import inspect
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

# ... every existing test_bare_exists_*, test_compound_*, test_descriptive_*,
# test_non_path_exists_prose_punts, test_directory_created_*, test_bind_skill_invoked,
# test_punt_explicit, test_punt_on_garbage, test_punt_on_unknown_checker,
# test_punt_on_schema_invalid, test_parses_fenced_json, test_returned_spec_is_dispatchable,
# test_prompt_carries_load_bearing_pieces ports the same way: call_host=_host(...) becomes
# call_model=_reply(...), call_host=_fail_host becomes call_model=_fail_call_model.
# DELETED: test_punt_on_none_host_output (GeminiReply.text is always a string; there is
#   no "abnormal None host call" shape at this layer anymore).
# DELETED: test_binder_still_imports_the_original_run_host_judge (import-identity pin
#   for a symbol that no longer exists).


def test_infra_error_propagates() -> None:
    """Verify infra error propagates."""
    def boom(prompt: object, *, timeout: object = 60) -> NoReturn:
        raise RuntimeError("gemini transient failure")

    with pytest.raises(RuntimeError):
        bind("x", call_model=boom)


def test_bind_propagates_binder_auth_error() -> None:
    """Verify a BinderAuthError from call_model is never swallowed as a punt."""
    def boom(prompt: object, *, timeout: object = 60) -> NoReturn:
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        bind("x", call_model=boom)


def test_bind_default_call_model_is_call_gemini() -> None:
    """Verify bind's default transport is the real Gemini call — the wiring proof."""
    assert inspect.signature(bind).parameters["call_model"].default is binder._call_gemini
```

Part B — the `_call_gemini` taxonomy, monkeypatching `urllib.request.urlopen` (the one network edge):

```python
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
            "https://generativelanguage.googleapis.com/x", code, "err",
            hdrs=None, fp=__import__("io").BytesIO(body.encode("utf-8")),
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


def test_call_gemini_honors_evalspec_binder_model_env_override(monkeypatch: object) -> None:
    """Verify EVALSPEC_BINDER_MODEL overrides the fixed production model."""
    captured = {}

    def fake_urlopen(request: object, timeout: object) -> object:
        captured["url"] = request.full_url
        return _http_response({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})(
            request, timeout
        )

    monkeypatch.setenv("EVALSPEC_BINDER_MODEL", "gemini-3.1-flash")
    monkeypatch.setattr(binder.urllib.request, "urlopen", fake_urlopen)

    binder._call_gemini("prompt")

    assert "gemini-3.1-flash" in captured["url"]
    assert "gemini-3.1-flash-lite" not in captured["url"]


@pytest.mark.parametrize("code,body", [(401, "unauthorized"), (403, "forbidden")])
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
    monkeypatch.setattr(
        binder.urllib.request, "urlopen", _http_error(400, '{"error":{"status":"INVALID_ARGUMENT"}}'),
    )
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
])
def test_call_gemini_raises_runtimeerror_on_degenerate_200(
    monkeypatch: object, payload: dict
) -> None:
    """Verify every degenerate-200 shape normalizes to RuntimeError, never a silent punt."""
    monkeypatch.setattr(binder.urllib.request, "urlopen", _http_response(payload))
    with pytest.raises(RuntimeError):
        binder._call_gemini("prompt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_binder.py -v`
Expected: FAIL — `binder.GEMINI_BINDER_MODEL`/`BinderAuthError`/`GeminiReply`/`_call_gemini` don't exist yet, and `bind()` still takes `call_host`/`model`.

- [ ] **Step 3: Implement the transport in `binder.py`**

Add imports (`os`, `time`, `urllib.error`, `urllib.request`, `dataclasses.dataclass`), drop `from evalspec.agents.judge_cli import run_host_judge`, add:

```python
GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_TIMEOUT_SECONDS = 60.0
_AUTH_HTTP_CODES = {401, 403}


class BinderAuthError(Exception):
    """Raised when the Gemini API rejects the configured credential.

    Deliberately NOT a RuntimeError subclass — every RuntimeError catch site in the
    binder's failure taxonomy (execution.py's degrade-to-judge path, the corpus
    retry loop) must let this propagate instead of silently rerouting every
    assertion to paid judge grading behind a green run.
    """


@dataclass(frozen=True)
class GeminiReply:
    """One Gemini generateContent response, trimmed to what the binder and the
    binder corpus suite need."""

    text: str
    prompt_tokens: int
    output_tokens: int
    latency_ms: float


def _call_gemini(prompt: str, *, timeout: float = _GEMINI_TIMEOUT_SECONDS) -> GeminiReply:
    """Call the fixed Gemini binder model and return its reply.

    Args:
        prompt: The rendered binding prompt.
        timeout: Request timeout in seconds.

    Returns:
        The model's text plus token usage and measured latency.

    Raises:
        BinderAuthError: the API key was rejected (HTTP 401/403, or a 400 body
            naming API_KEY_INVALID).
        RuntimeError: every other transport, HTTP, or degenerate-response failure.
    """
    model = os.environ.get("EVALSPEC_BINDER_MODEL", GEMINI_BINDER_MODEL)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode("utf-8")
    request = urllib.request.Request(
        _GEMINI_URL.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        if error.code in _AUTH_HTTP_CODES or (error.code == 400 and "API_KEY_INVALID" in error_body):
            raise BinderAuthError(
                f"Gemini API rejected the credential (HTTP {error.code}): {error_body[:500]}"
            ) from error
        raise RuntimeError(f"Gemini API HTTP {error.code}: {error_body[:500]}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"Gemini API transport failure: {error}") from error
    latency_ms = (time.perf_counter() - started) * 1000

    return _parse_gemini_payload(payload, latency_ms=latency_ms)


def _parse_gemini_payload(payload: object, *, latency_ms: float) -> GeminiReply:
    """Validate a 200-OK Gemini payload and extract text + usage.

    Raises RuntimeError for every degenerate shape (no candidates, empty parts,
    finishReason SAFETY/MAX_TOKENS, promptFeedback block) — a 200 status code alone
    does not mean the model produced usable text.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("Gemini API returned a non-object payload")
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise RuntimeError(f"Gemini API blocked the prompt: {feedback['blockReason']}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Gemini API response had no candidates")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise RuntimeError("Gemini API candidate was not an object")
    finish_reason = candidate.get("finishReason")
    if finish_reason in ("SAFETY", "MAX_TOKENS"):
        raise RuntimeError(f"Gemini API candidate finished with {finish_reason}")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("Gemini API response had no content parts")
    text = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")
    )
    if not text:
        raise RuntimeError("Gemini API response had empty text")

    usage = payload.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}
    return GeminiReply(
        text=text,
        prompt_tokens=_usage_int(usage.get("promptTokenCount")),
        output_tokens=_usage_int(usage.get("candidatesTokenCount")),
        latency_ms=latency_ms,
    )


def _usage_int(value: object) -> int:
    """Return non-boolean integer usage values, 0 otherwise."""
    if type(value) is int:
        return value
    return 0
```

Change `bind()`:

```python
def bind(
    assertion_text: str,
    *,
    call_model: object = _call_gemini,
) -> dict | None:
    """Return a deterministic checker spec dict for `assertion_text`, or None to punt.

    The returned dict is the exact checker-spec shape `checkers.run_assertion` consumes.
    `call_model` is injected so tests drive the binder with fake GeminiReply objects
    (no network); production and the corpus suite both default to `_call_gemini`.
    """
    if spec := _bind_bare_exists(assertion_text):
        return spec
    prompt = _BINDING_PROMPT.format(assertion=assertion_text)
    reply = call_model(prompt, timeout=60)
    return _parse_binding(reply.text)
```

Change `_parse_binding` to drop the `{"result": ...}` envelope unwrap — it now receives the reply text directly:

```python
def _parse_binding(text: str) -> dict | None:
    """Parse a Gemini reply's text into a checker spec or punt."""
    for candidate in _balanced_objects(text):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("punt"):
            return None
        if "checker" in obj:
            spec = {**obj, "type": "deterministic"}
            try:
                _validate_checker_obj(spec, "binder")
            except SchemaError:
                return None  # malformed/hallucinated args → punt, never a false-positive
            return spec
    return None  # no object / unparseable → punt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_binder.py -v`
Expected: PASS — every ported test plus the new taxonomy tests.

- [ ] **Step 5: Run the wider offline suite and lint**

Run: `make test && make lint`
Expected: PASS, except pre-existing failures caused by `binder.py` no longer importing `judge_cli` — `evals/test_binder_corpus.py` (still imports `bind` and works unchanged) should still pass; `tests/agents/test_judge_cli.py` and `tests/test_execution.py::test_missing_judge_binary_marks_arm_errored` are untouched by this task and still green (judge_cli.py is orphaned-but-still-present, deleted in Task 4).

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/binder.py tests/test_binder.py
git commit -m "feat(binder): replace host-Claude transport with a direct Gemini API call"
```

---

### Task 2: Execution — narrow the degrade taxonomy and count binder degradations

**Files:**
- Modify: `src/evalspec/execution.py`
- Modify: `tests/test_execution.py`

**Interfaces:**
- Changes: `_grade_mixed(...) -> tuple[list, int, bool, int]` (adds `binder_degraded: int` as the fourth return value); `run_eval_arm` unpacks the new tuple and adds `"binder_degraded": binder_degraded` to the `grading` dict written to `grading.json`.
- Removes: `import subprocess` (dead after the except-tuple narrows — `subprocess.TimeoutExpired` was its only reference in this file).

- [ ] **Step 1: Write the failing tests**

In `tests/test_execution.py`:

- Rename `test_binder_timeout_punts_to_judge_not_errors` → `test_binder_runtime_error_punts_to_judge_not_errors`; replace the local `import subprocess` / `raise subprocess.TimeoutExpired(...)` with `raise RuntimeError("gemini transient failure")`; add `assert outcome.grading["binder_degraded"] == 1`.
- Extend `test_bind_failure_punts_to_judge_not_error` with `assert outcome.grading["binder_degraded"] == 1`.
- Add `test_binder_degraded_counts_once_per_bind_cache_miss_not_per_assertion`: two eval assertions with identical text, `bind` raising `RuntimeError` once via a call counter; assert the `RuntimeError`-raising `bind` was invoked once (cache hit on the second identical text) and `outcome.grading["binder_degraded"] == 1`.
- Add `test_bind_punt_does_not_count_as_binder_degraded`: `bind` returns `None` (an ordinary punt, not a `RuntimeError`); assert `outcome.grading["binder_degraded"] == 0`.
- Add `test_run_eval_arm_propagates_binder_auth_error`: `bind` raises `evalspec.binder.BinderAuthError`; assert `run_eval_arm(...)` raises `BinderAuthError` (never caught, never counted, run never writes a degraded artifact).

```python
def test_binder_degraded_counts_once_per_bind_cache_miss_not_per_assertion(
    tmp_path: object,
) -> None:
    """Verify binder_degraded counts distinct bind_cache misses, not assertions."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(
        tmp_path, {"slug": "alpha", "prompt": "work", "assertions": ["dup", "dup"]}
    )
    calls = []

    def bind(text: object) -> NoReturn:
        calls.append(text)
        raise RuntimeError("gemini transient failure")

    outcome = run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
        ),
        grade=_grade_all_pass,
        bind=bind,
    )

    assert len(calls) == 1  # the second "dup" hit the bind_cache, never called bind again
    assert outcome.grading["binder_degraded"] == 1


def test_bind_punt_does_not_count_as_binder_degraded(tmp_path: object) -> None:
    """Verify an ordinary punt (bind returns None) is not a degradation."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(tmp_path, {"slug": "alpha", "prompt": "work", "assertions": ["a1"]})

    outcome = run_eval_arm(
        eval_case, TRIAL, workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=fake_session_factory(
            RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
        ),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    assert outcome.grading["binder_degraded"] == 0


def test_run_eval_arm_propagates_binder_auth_error(tmp_path: object) -> None:
    """Verify a BinderAuthError is never caught or counted — it fails the run."""
    from evalspec.binder import BinderAuthError

    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    eval_case = _case(tmp_path, {"slug": "alpha", "prompt": "work", "assertions": ["a1"]})

    def bind(text: object) -> NoReturn:
        raise BinderAuthError("gemini api key rejected")

    with pytest.raises(BinderAuthError):
        run_eval_arm(
            eval_case, TRIAL, workdir, {}, tmp_path,
            today="2099-01-01", repo_root=tmp_path, sample=0,
            session_factory=fake_session_factory(
                RunResult("alpha", "trial", "done", 1, 1, False, session_id="s1", fired=True)
            ),
            grade=_grade_all_pass,
            bind=bind,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_execution.py -k "binder_degraded or binder_runtime_error or binder_auth_error or bind_punt_does_not_count" -v`
Expected: FAIL — `grading["binder_degraded"]` `KeyError`, and the renamed test doesn't exist yet.

- [ ] **Step 3: Implement in `execution.py`**

Remove `import subprocess` (line 16). Change `_grade_mixed`:

```python
def _grade_mixed(
    *,
    assertions: object,
    tree: object,
    contents: object,
    shas: object,
    result_text: object,
    bind: object,
    grade: object,
    workdir: object,
    grade_context: object,
    judge_config: object,
    eval_id: object,
    arm_name: object,
    pre_run_shas: object,
    process_facts: object = "",
) -> object:
    """Grade bound assertions locally and punt the rest to the judge.

    Returns (results, judge_ms, judge_errored, binder_degraded) — binder_degraded
    counts each distinct bind_cache MISS that raised RuntimeError (a transient
    binder infra failure degrading to judge grading), never a punt (bind() returning
    None is the binder's designed, non-degraded outcome). A BinderAuthError is never
    caught here — it propagates and fails the run.
    """
    results: list = [None] * len(assertions)
    judge_idx: list[int] = []
    bind_cache: dict[str, dict | None] = {}
    binder_degraded = 0
    for assertion_index, text in enumerate(assertions):
        if text not in bind_cache:
            try:
                bind_cache[text] = bind(text)
            except RuntimeError:
                # A transient binder infra failure degrades to judge grading (not a
                # cell error) but must be counted, not silently absorbed — see
                # execution.py's degradation-visibility contract. BinderAuthError
                # deliberately isn't caught here: it propagates and fails the run.
                bind_cache[text] = None
                binder_degraded += 1
        spec = bind_cache[text]
        if spec is not None:
            entry = checkers.run_assertion(spec, workdir, pre_run_shas, context=grade_context)
            entry["text"] = text
            results[assertion_index] = entry
        else:
            judge_idx.append(assertion_index)
    if not judge_idx:
        return results, 0, False, binder_degraded

    texts = [assertions[assertion_index] for assertion_index in judge_idx]
    graded, judge_ms, judge_errored = _grade_via_judge(
        assertions=texts, tree=tree, contents=contents, shas=shas, result_text=result_text,
        grade=grade, judge_config=judge_config, eval_id=eval_id, arm_name=arm_name,
        pre_run_shas=pre_run_shas, process_facts=process_facts,
    )
    for assertion_index, entry in zip(judge_idx, graded["assertions"], strict=False):
        entry["type"] = "semantic"
        results[assertion_index] = entry
    return results, judge_ms, judge_errored, binder_degraded
```

In `run_eval_arm`, unpack the fourth value and stamp it into `grading`:

```python
    merged, judge_ms, judge_errored, binder_degraded = _grade_mixed(
        assertions=graded_assertions,
        ...
    )
    ...
    grading = {
        "eval_id": eval_id,
        "skill": eval_case.skill,
        "arm": arm_name,
        "sample": sample,
        "errored": errored,
        "binder_degraded": binder_degraded,
        "assertions": merged,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_execution.py -v`
Expected: PASS — full file, including the renamed/extended tests.

- [ ] **Step 5: Verify**

Run: `make test && make lint`
Expected: PASS. Confirm `grep -n "^import subprocess" src/evalspec/execution.py` returns nothing.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/execution.py tests/test_execution.py
git commit -m "feat(execution): narrow binder degrade taxonomy to RuntimeError and count degradations"
```

---

### Task 3: Report + plugin — surface binder degradation as a run-level warning

**Files:**
- Modify: `src/evalspec/report.py`
- Modify: `src/evalspec/plugin.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_report.py`
- Modify: `tests/test_plugin.py`

**Interfaces:**
- Changes: `report._arm_stats(...) -> dict` gains a `"binder_degraded": int` key (sum across every sample's `grading.json`).
- Changes: `plugin.pytest_sessionfinish` sums `binder_degraded` across every arm of every skill's benchmark and appends one WARN line to the `_SUMMARY_LINES` stash when the total is nonzero — never touches `session.exitstatus` (unlike the `fail_under` gate, this is visibility, not a CI failure).
- Changes: `tests/conftest.seed_arm(..., binder_degraded: int = 0)` writes the field into the seeded `grading.json`.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
def test_arm_stats_sums_binder_degraded_across_samples(tmp_path: object) -> None:
    """Verify _arm_stats sums binder_degraded across every sample in the arm."""
    eval_root = tmp_path / "archive"
    seed_arm(eval_root, "alpha", "trial", passes=1, total=1, sample=0, binder_degraded=2)
    seed_arm(eval_root, "alpha", "trial", passes=1, total=1, sample=1, binder_degraded=1)

    stats = report._arm_stats([eval_root / "eval-alpha"], "trial")

    assert stats["binder_degraded"] == 3
```

`tests/test_plugin.py` (mirroring `test_fail_under_sets_exit_status`'s `_FakeConfig`/`_FakeSession`/`_FakeTR` harness):

```python
def test_binder_degraded_warns_in_terminal_summary(tmp_path: object, monkeypatch: object) -> None:
    """Verify a nonzero binder_degraded total prints a WARN line, doesn't fail the run."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2, binder_degraded=3)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 0, config)
    assert any("WARN" in line and "binder" in line for line in terminal_reporter.lines)


def test_binder_degraded_quiet_when_zero(tmp_path: object, monkeypatch: object) -> None:
    """Verify no binder WARN line when nothing degraded."""
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skill_results_dir = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(skill_results_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(skill_results_dir, "alpha", "baseline", passes=2, total=2)

    config = _FakeConfig(tmp_path)
    session = _FakeSession(config)
    plugin.pytest_sessionfinish(session, 0)

    terminal_reporter = _FakeTR()
    plugin.pytest_terminal_summary(terminal_reporter, 0, config)
    assert not any("binder" in line for line in terminal_reporter.lines)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py tests/test_plugin.py -k "binder_degraded" -v`
Expected: FAIL — `seed_arm()` has no `binder_degraded` keyword; `_arm_stats` has no such key; no WARN line exists.

- [ ] **Step 3: Implement**

`tests/conftest.py` — add the keyword and write it into the seeded grading dict:

```python
def seed_arm(
    eval_root: Path, eval_id: str, arm: str, passes: int, total: int, *,
    sample: int = 0, duration_ms: int = 1000, total_tokens: int = 500,
    input_tokens: int = 300, output_tokens: int = 100, cache_read_tokens: int = 50,
    cache_creation_tokens: int = 25, judge_ms: int = 200, errored: bool = False,
    binder_degraded: int = 0,
) -> Path:
    ...
    (d / "grading.json").write_text(
        json.dumps({
            "eval_id": eval_id, "skill": eval_root.name, "arm": arm, "sample": sample,
            "errored": errored, "binder_degraded": binder_degraded, "assertions": assertions,
        })
    )
```

`report.py`'s `_arm_stats` — track and return the sum (declare `binder_degraded_total = 0` alongside the other accumulators, `binder_degraded_total += grading.get("binder_degraded", 0)` in the per-sample loop, add `"binder_degraded": binder_degraded_total` to the returned dict).

`plugin.py`'s `pytest_sessionfinish` — after the `for skill_dir in ...` loop that builds each `benchmark`, accumulate a run-wide total and append the WARN line before the `config.stash[_SUMMARY_LINES] = lines` assignment:

```python
    binder_degraded_total = 0
    for skill_dir in sorted(...):
        ...
        benchmark = report.write_benchmark(...)
        binder_degraded_total += sum(
            stats.get("binder_degraded", 0) for stats in benchmark["arms"].values()
        )
        ...
    if binder_degraded_total:
        lines.append(
            f"WARN binder: {binder_degraded_total} assertion(s) degraded to judge "
            "grading after a binder infra RuntimeError — see grading.json's "
            "binder_degraded field per arm"
        )
    ...
    config.stash[_SUMMARY_LINES] = lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py tests/test_plugin.py -v`
Expected: PASS — full files.

- [ ] **Step 5: Verify**

Run: `make test && make lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/report.py src/evalspec/plugin.py tests/conftest.py tests/test_report.py tests/test_plugin.py
git commit -m "feat(plugin): warn when binder degradation to judge grading is nonzero"
```

---

### Task 4: Delete `agents/judge_cli.py` (atomic relocation)

This task must land as ONE commit: `raise_for_judge_cli_failure` calls `raise_for_is_error_envelope` internally, so splitting "move the helper" from "delete the module" leaves an intermediate state where `judge_cli.py` references an undefined name. Its precondition (binder off `run_host_judge`) is satisfied by Task 1.

**Files:**
- Delete: `src/evalspec/agents/judge_cli.py`
- Delete: `tests/agents/test_judge_cli.py`
- Modify: `src/evalspec/agents/claude.py`
- Modify: `src/evalspec/judges/__init__.py`
- Modify: `tests/test_execution.py`

**Interfaces:**
- Removes: `evalspec.agents.judge_cli` (module), `run_host_judge`, `raise_for_judge_cli_failure`, `raise_for_is_error_envelope` (public names).
- Produces: `evalspec.agents.claude._raise_for_is_error_envelope(stdout: str) -> None` (private — single remaining consumer is `ClaudeCodeAgent.judge` in the same module).

- [ ] **Step 1: Confirm the is_error envelope behavior is already covered at its new home**

`tests/judges/test_claude_code.py::test_judge_raises_runtimeerror_on_is_error_envelope` already drives `_raise_for_is_error_envelope`'s exact contract through `ClaudeCodeAgent.judge` (not a direct import of the helper). No new test is needed for the relocated function — verify this by running it before touching anything:

Run: `uv run pytest tests/judges/test_claude_code.py::test_judge_raises_runtimeerror_on_is_error_envelope -v`
Expected: PASS (already green, unaffected by this task).

- [ ] **Step 2: Retarget the `tests/test_execution.py:1063` monkeypatch**

`test_missing_judge_binary_marks_arm_errored` currently does:

```python
monkeypatch.setattr("evalspec.agents.judge_cli.subprocess.run", boom)
```

This works today only because `judge_cli.subprocess` and every other module's `import subprocess` are the same module object in `sys.modules` — monkeypatching `.run` through any alias patches it globally. `src/evalspec/environments.py:79` (`Host.exec`) is the real call site this test exercises (via `ClaudeCodeAgent.judge` → `Host().exec` → `subprocess.run`). Retarget:

```python
monkeypatch.setattr("evalspec.environments.subprocess.run", boom)
```

- [ ] **Step 3: Run the test to verify it still fails identically without the module present**

Run: `uv run pytest tests/test_execution.py::test_missing_judge_binary_marks_arm_errored -v`
Expected: PASS with the retargeted monkeypatch (still passes now, before deletion — proves the retarget alone is correct, isolating this change from the deletion below).

- [ ] **Step 4: Move `raise_for_is_error_envelope` into `claude.py`, delete `judge_cli.py`**

In `src/evalspec/agents/claude.py`, replace the import and add the private helper near `judge()`:

```python
# remove: from evalspec.agents.judge_cli import raise_for_is_error_envelope

def _raise_for_is_error_envelope(stdout: str) -> None:
    """Raise RuntimeError when a 0-exit `claude -p` envelope carries is_error=true.

    This is how `claude -p` reports auth, rate-limit, quota, and overload failures.
    Unparseable stdout is deliberately NOT an error here (a transient streaming
    glitch isn't a CLI failure); the caller's own JSON parsing surfaces that case.
    """
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        return
    if isinstance(outer, dict) and outer.get("is_error"):
        msg = str(outer.get("result") or "").strip() or "(no error message)"
        raise RuntimeError(f"host claude CLI returned is_error=true: {msg[:1000]}")
```

Add `import json` to `claude.py`'s imports if not already present. Update the call site (`raise_for_is_error_envelope(proc.stdout)` → `_raise_for_is_error_envelope(proc.stdout)`) and the `judge()` docstring, which currently ends "…via judge_cli":

```python
    async def judge(
        self: object, prompt: str, config: JudgeConfig, *, env: ExecutionEnv | None = None,
    ) -> str:
        """Grade via `claude -p --output-format json` (default env: fresh Host process).

        The output is already the {"result": ..., "is_error": ...} envelope judge.py
        parses, so it is returned as-is after infra checks. RuntimeError on a missing
        binary, a nonzero exit, or an is_error envelope (auth/rate-limit/quota).
        """
```

Delete `src/evalspec/agents/judge_cli.py` and `tests/agents/test_judge_cli.py` entirely.

Update `src/evalspec/judges/__init__.py`'s module docstring — it currently reads:

```
`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
WHICH harness grades and resolves its config. `binder.py`'s host-Claude call
(`agents.judge_cli.run_host_judge`) is a separate, untouched transport.
```

Replace the last sentence (the `judge_cli` reference no longer exists):

```
`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
WHICH harness grades and resolves its config. `binder.py`'s prose→checker classifier
is a separate, unrelated direct Gemini API call, independent from this package.
```

- [ ] **Step 5: Run the affected suites**

Run: `uv run pytest tests/agents/ tests/judges/ tests/test_execution.py tests/test_binder.py -v`
Expected: PASS — no `judge_cli` import errors, `test_missing_judge_binary_marks_arm_errored` still green via the `environments.subprocess.run` retarget.

- [ ] **Step 6: Verify the code tree is scrubbed**

Run: `grep -rEn "agents[./]judge_cli" src tests evals`
Expected: no output (`docs/agents.md`/`concepts.md` are the Documentation Plan's — Task 8 — and `plugin.py`'s unrelated `_parse_judge_cli_table` function name doesn't match this pattern).

Run: `make test && make lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A src/evalspec/agents/judge_cli.py src/evalspec/agents/claude.py src/evalspec/judges/__init__.py tests/agents/test_judge_cli.py tests/test_execution.py
git commit -m "refactor(agents): delete judge_cli.py, relocate is_error envelope check to claude.py"
```

---

### Task 5: `GEMINI_API_KEY` preflight wired into the graded-run entry point

**Files:**
- Modify: `src/evalspec/binder.py`
- Modify: `src/evalspec/cases.py`
- Modify: `tests/test_binder.py`
- Modify: `tests/test_plugin.py`

**Interfaces:**
- Produces: `binder.preflight_gemini_key() -> None` (raises `RuntimeError`; missing OR empty `GEMINI_API_KEY` counts as absent).
- Changes: `cases.judge_config` fixture calls `binder.preflight_gemini_key()` before `preflight_judge_binary(config)` — fires only when a run will grade (the fixture is requested only by `test_eval`), never for a trigger-only session.

- [ ] **Step 1: Write the failing tests**

`tests/test_binder.py`:

```python
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
```

`tests/test_plugin.py` (mirroring `test_judge_preflight_fixture_raises_when_binary_missing`'s pytester harness):

```python
def test_gemini_key_preflight_fixture_raises_when_missing(
    pytester: object, monkeypatch: object
) -> None:
    """Verify the judge_config fixture fails fast on a missing GEMINI_API_KEY."""
    import shutil

    from evalspec import sandbox

    _make_project(pytester)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")  # binary IS present
    monkeypatch.setattr(sandbox, "preflight", lambda: None)

    result = pytester.runpytest(
        "-p", "evalspec.plugin",
        "--evalspec-repo-root", str(pytester.path),
        "-k", "test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "GEMINI_API_KEY" in out


def test_gemini_key_preflight_does_not_fire_on_trigger_only_session(
    pytester: object, monkeypatch: object
) -> None:
    """Verify a trigger-only session (no judge_config fixture request) needs no key."""
    from evalspec import sandbox

    evals = pytester.path / "skills" / "myskill" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: myskill\n---\n## Trigger\n\n- q1: do it\n\n## No Trigger\n\n- q2: nope\n"
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(sandbox, "preflight", lambda: None)

    result = pytester.runpytest(
        "-p", "evalspec.plugin", "--collect-only",
        "--evalspec-repo-root", str(pytester.path),
    )
    assert result.ret == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_binder.py -k preflight_gemini_key tests/test_plugin.py -k gemini_key_preflight -v`
Expected: FAIL — `binder.preflight_gemini_key` doesn't exist.

- [ ] **Step 3: Implement**

`binder.py`:

```python
def preflight_gemini_key() -> None:
    """Raise RuntimeError if GEMINI_API_KEY is missing or empty.

    A GEMINI_API_KEY set to the empty string counts as missing — python-dotenv
    never overrides an already-set environment variable, so `GEMINI_API_KEY=
    make evals:binder` can't be silently repopulated from `.env`; this makes that
    invocation fail fast, before any paid call, exactly as intended.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY is required — the binder calls the Gemini API for every "
            "graded run. Set it in the environment or a repo-root .env."
        )
```

`cases.py` — add `from evalspec import binder` to imports, update the fixture:

```python
@pytest.fixture
def judge_config(request: object) -> JudgeConfig:
    """Resolve the run's judge and preflight its binary and the binder's Gemini credential."""
    # Only test_eval requests this fixture, so both preflights (environment-dependent,
    # unlike resolved_judge_config's structural checks already run at collection) fire
    # exactly when a run will grade — never for a trigger-only session.
    binder.preflight_gemini_key()
    config = resolved_judge_config(request.config)
    preflight_judge_binary(config)
    return config
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_binder.py tests/test_plugin.py -v`
Expected: PASS — full files.

- [ ] **Step 5: Verify `make test` needs no `GEMINI_API_KEY`**

Run: `env -u GEMINI_API_KEY make test`
Expected: PASS. `cases.py` is not collected by `make test` (it runs `pytest -p pytester` against `tests/`, not `-p evalspec.plugin`), so the preflight never fires here — pin this explicitly since it's the whole point of the fixture placement.

Run: `make lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/binder.py src/evalspec/cases.py tests/test_binder.py tests/test_plugin.py
git commit -m "feat(binder): preflight GEMINI_API_KEY before any graded run"
```

---

### Task 6: Relocate the binder corpus suite to `evals/binder/`

**Files:**
- Rename: `evals/binder_corpus.yaml` → `evals/binder/corpus.yaml`
- Rename: `evals/conftest.py` → `evals/binder/conftest.py`
- Rename: `evals/test_binder_corpus.py` → `evals/binder/test_corpus.py`
- Rename: `evals/test_binder_corpus_integrity.py` → `evals/binder/test_corpus_integrity.py`
- Modify: `Makefile`
- Modify: `pyproject.toml`

This task is a pure relocation plus the retry-tuple narrow and the `GEMINI_API_KEY` corpus-side preflight — metrics extension is Task 7, kept separate so this task's diff is reviewable as "moved + retargeted," not "moved + changed shape."

**Interfaces:**
- Changes: `evals/binder/test_corpus_integrity.py`'s `CORPUS_PATH = Path(__file__).resolve().parent / "corpus.yaml"` (was `binder_corpus.yaml`).
- Changes: `evals/binder/test_corpus.py`'s `from test_corpus_integrity import CORPUS` (was `from test_binder_corpus_integrity import CORPUS`); `_bind_resilient`'s except tuple narrows from `(subprocess.TimeoutExpired, RuntimeError, OSError)` to `(RuntimeError,)` — safe now because `_call_gemini`'s taxonomy is total (`urllib.error.URLError` is an `OSError` subclass, so it already normalizes to `RuntimeError` before reaching this retry); drop the file's `import subprocess`.
- Produces: `evals/binder/conftest.py`'s `pytest_configure` calls `binder.preflight_gemini_key()`, gated on `_is_controller(config) and _binder_selected(config)`, converting the `RuntimeError` to `pytest.UsageError` (matching `plugin.py`'s established idiom for collection-time config failures) so the corpus session fails once, before any draw, with a clean message rather than a traceback.

- [ ] **Step 1: Move the files**

```bash
mkdir -p evals/binder
git mv evals/binder_corpus.yaml evals/binder/corpus.yaml
git mv evals/conftest.py evals/binder/conftest.py
git mv evals/test_binder_corpus.py evals/binder/test_corpus.py
git mv evals/test_binder_corpus_integrity.py evals/binder/test_corpus_integrity.py
```

- [ ] **Step 2: Fix the intra-suite references**

In `evals/binder/test_corpus_integrity.py`:

```python
CORPUS_PATH = Path(__file__).resolve().parent / "corpus.yaml"
```

In `evals/binder/test_corpus.py`, update the import and narrow the retry tuple:

```python
from test_corpus_integrity import CORPUS
...

def _bind_resilient(text: object) -> object:
    """Bind one assertion with one retry for a transient Gemini infra failure."""
    # RuntimeError is the total normalization every _call_gemini failure funnels
    # through except BinderAuthError (never caught — a bad key must fail the corpus
    # session, not masquerade as one more "infra error" data point).
    for _ in range(2):
        try:
            return bind(text)
        except RuntimeError:
            continue
    return _ERROR
```

Remove the file's `import subprocess` (now unused).

- [ ] **Step 3: Wire the corpus-side `GEMINI_API_KEY` preflight**

In `evals/binder/conftest.py`, add the import and extend `pytest_configure`:

```python
from evalspec import binder

...

def pytest_configure(config: object) -> None:
    """Configure pytest state for evalspec collection."""
    if not (_is_controller(config) and _binder_selected(config)):
        return
    try:
        binder.preflight_gemini_key()
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from None
    config.stash[_RAN] = True
    d = _results_dir(config)
    if d.exists():
        for f in d.glob("results-*.jsonl"):
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Update the Makefile and pyproject.toml**

`Makefile`:

```makefile
# Keep modest: high fan-out trips the Gemini call's ~60s timeout (12-way -> throttling).
BINDER_WORKERS ?= 6
evals:  ## Run the live eval suite. Pass EVAL_ARGS="--collect-only -q" to dry-run collection.
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) evals/binder $(EVAL_ARGS)
```

`pyproject.toml`:

```toml
markers = [
    "binder_corpus: live Gemini binder corpus eval (costs money; run via `make evals:binder`)",
]
```

- [ ] **Step 5: Verify offline**

Run: `uv run pytest evals/binder/test_corpus_integrity.py -v`
Expected: PASS — deterministic, no network, no `GEMINI_API_KEY` needed (this file carries no `binder_corpus` marker).

Run: `make evals EVAL_ARGS="--collect-only -q"`
Expected: Collection succeeds and lists `evals/binder/test_corpus.py` items — no `GEMINI_API_KEY` required for `--collect-only` (the corpus preflight only fires when `_binder_selected`, which it is here, but collection-time `pytest_configure` firing on a *present* key in the developer's shell is expected; run `env -u GEMINI_API_KEY make evals EVAL_ARGS="--collect-only -q"` separately and expect a clean `UsageError` naming `GEMINI_API_KEY`, proving the fail-fast-before-any-draw contract).

Run: `make test && make lint`
Expected: PASS — `testpaths = ["tests", "evals"]` still recurses into `evals/binder/`.

- [ ] **Step 6: Commit**

```bash
git add evals/binder Makefile pyproject.toml
git status  # confirm evals/binder_corpus.yaml, evals/conftest.py, evals/test_binder_corpus*.py are gone
git commit -m "chore(evals): relocate the binder corpus suite to evals/binder/"
```

---

### Task 7: Corpus per-draw source/latency/token records + session aggregates

**Files:**
- Modify: `evals/binder/test_corpus.py`

**Interfaces:**
- Produces: a recording `call_model` wrapper (module-private, e.g. `_recording_call_model(sink: list) -> object`) that calls `binder._call_gemini`, appends each `GeminiReply` (plus its wall-clock invocation) to `sink`, and lets exceptions propagate unchanged — this is how `EVALSPEC_BINDER_MODEL` still reaches the model (the wrapper delegates to `_call_gemini`, which reads the env var itself) and how the corpus observes `latency_ms`/token counts without `bind()` exposing them.
- Changes: each `record(...)` call in `test_binder_corpus_blocks_punt_leaks` gains `source` (`"regex"` if the wrapper was never invoked for this draw, else `"gemini"`), `attempts` (len of the sink, 0 for a regex-fast-path bind), and the Gemini-sourced draw's `latency_ms`/`prompt_tokens`/`output_tokens` (omitted/`None` for a regex draw).
- Changes: `evals/binder/conftest.py`'s `pytest_sessionfinish` aggregates latency (mean, p95) and token totals over Gemini-sourced draws only, an estimated cost from an in-suite pricing constant, and reports the regex fast-path count as its own line — the existing leak/retention/over-punt/mismatch/infra-error-guard math is unchanged.

- [ ] **Step 1: Write/extend the corpus's own tests**

The corpus suite has no unit tests of its own today (it IS the live-run test); this task's "failing test" is the aggregation logic living in `conftest.py`, which — being pure math over already-JSON-serializable records — gets a small offline unit test alongside the existing deterministic integrity test:

```python
# evals/binder/test_corpus_integrity.py (append)
from evals.binder.conftest import _latency_cost_summary  # adjust import to actual module path


def test_latency_cost_summary_excludes_regex_fast_path_rows() -> None:
    """Verify regex-sourced rows are excluded from latency/token/cost aggregates."""
    rows = [
        {"source": "regex", "attempts": 0, "latency_ms": None,
         "prompt_tokens": None, "output_tokens": None},
        {"source": "gemini", "attempts": 1, "latency_ms": 120.0,
         "prompt_tokens": 500, "output_tokens": 20},
        {"source": "gemini", "attempts": 1, "latency_ms": 140.0,
         "prompt_tokens": 500, "output_tokens": 20},
    ]

    summary = _latency_cost_summary(rows)

    assert summary["regex_fast_path_count"] == 1
    assert summary["gemini_count"] == 2
    assert summary["latency_ms_mean"] == pytest.approx(130.0)
    assert summary["total_prompt_tokens"] == 1000
    assert summary["total_output_tokens"] == 40
```

(Import path note: `evals/` has no `__init__.py`, so cross-file imports inside the suite use bare module names via pytest's `prepend` import mode, matching `test_corpus.py`'s existing `from test_corpus_integrity import CORPUS` — adjust the test import above to `from conftest import _latency_cost_summary`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest evals/binder/test_corpus_integrity.py -k latency_cost_summary -v`
Expected: FAIL — `_latency_cost_summary` doesn't exist yet.

- [ ] **Step 3: Implement the recording wrapper in `test_corpus.py`**

```python
from evalspec.binder import _bind_bare_exists, _call_gemini


def _recording_call_model(sink: list) -> object:
    """Build a call_model that delegates to _call_gemini and records every reply.

    Exceptions propagate unchanged (a retry in _bind_resilient still sees the real
    failure) — this wrapper only observes successful replies, so `attempts` on a
    record that ultimately errored reflects how many replies were actually captured
    before the retry loop gave up, not how many attempts were made.
    """
    def call(prompt: str, *, timeout: int = 60) -> object:
        reply = _call_gemini(prompt, timeout=timeout)
        sink.append(reply)
        return reply
    return call


def _bind_resilient(text: object, *, sink: list) -> object:
    """Bind one assertion with one retry for a transient Gemini infra failure."""
    call_model = _recording_call_model(sink)
    for _ in range(2):
        try:
            return bind(text, call_model=call_model)
        except RuntimeError:
            continue
    return _ERROR
```

Update `test_binder_corpus_blocks_punt_leaks` to thread a per-draw sink and extend the record:

```python
@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(entry: object, record: object) -> None:
    """Reject corpus examples where a punt expectation binds to a checker."""
    replies: list = []
    binding = _bind_resilient(entry["text"], sink=replies)
    # Derive source from the same predicate bind() itself uses to skip call_model,
    # not from whether `replies` is non-empty — an all-retries-errored Gemini draw
    # never appends to `replies` either, and mislabeling it "regex" would inflate
    # regex_fast_path_count and deflate gemini_count.
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"

    record({
        "gold": entry["gold"],
        "cohort": entry["cohort"],
        "expect_checker": entry.get("expect_checker"),
        "expect": entry.get("expect"),
        "actual": {key: binding.get(key) for key in entry.get("expect", {})}
        if isinstance(binding, dict) else None,
        "result": "error" if binding is _ERROR else "punt" if binding is None else "bound",
        "checker": binding.get("checker") if isinstance(binding, dict) else None,
        "source": source,
        "attempts": len(replies),
        "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
        "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
        "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
    })

    if binding is _ERROR:
        pytest.skip("infra failure after retry")
    if entry["gold"] == "punt":
        assert not isinstance(binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {binding.get('checker')!r}"
        )
```

Update `test_binder_corpus_preserves_expected_checker_fields` to pass a throwaway `sink=[]` to `_bind_resilient` (it doesn't call `record`, so no metrics change needed there beyond the new required keyword).

- [ ] **Step 4: Implement `_latency_cost_summary` and wire it into the session summary**

In `evals/binder/conftest.py`:

```python
# Approximate — a corpus-suite pricing constant, not a billing source of truth.
_GEMINI_FLASH_LITE_USD_PER_1K_PROMPT_TOKENS = 0.0001
_GEMINI_FLASH_LITE_USD_PER_1K_OUTPUT_TOKENS = 0.0004


def _latency_cost_summary(rows: list) -> dict:
    """Aggregate latency/token/cost stats over Gemini-sourced rows only.

    Regex fast-path rows (bare file_exists assertions bound without any API call)
    are reported as their own count, never folded into the latency mean — their
    near-zero latency would corrupt it.
    """
    gemini_rows = [r for r in rows if r.get("source") == "gemini"]
    latencies = sorted(r["latency_ms"] for r in gemini_rows if r.get("latency_ms") is not None)
    prompt_tokens = sum(r.get("prompt_tokens") or 0 for r in gemini_rows)
    output_tokens = sum(r.get("output_tokens") or 0 for r in gemini_rows)
    p95_index = max(0, int(len(latencies) * 0.95) - 1) if latencies else None
    return {
        "regex_fast_path_count": sum(1 for r in rows if r.get("source") == "regex"),
        "gemini_count": len(gemini_rows),
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p95": latencies[p95_index] if latencies else None,
        "total_prompt_tokens": prompt_tokens,
        "total_output_tokens": output_tokens,
        "estimated_cost_usd": (
            prompt_tokens / 1000 * _GEMINI_FLASH_LITE_USD_PER_1K_PROMPT_TOKENS
            + output_tokens / 1000 * _GEMINI_FLASH_LITE_USD_PER_1K_OUTPUT_TOKENS
        ),
    }
```

Append its output to `pytest_sessionfinish`'s `lines` list (after the existing `determinism_retention=...` line, before the infra-error-guard `FAIL` check):

```python
    cost_summary = _latency_cost_summary(rows)
    lines.append(
        f"latency_ms: mean={cost_summary['latency_ms_mean']} p95={cost_summary['latency_ms_p95']} "
        f"(gemini={cost_summary['gemini_count']} regex_fast_path={cost_summary['regex_fast_path_count']})  "
        f"tokens: prompt={cost_summary['total_prompt_tokens']} output={cost_summary['total_output_tokens']}  "
        f"est_cost_usd~{cost_summary['estimated_cost_usd']:.4f}"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest evals/binder/test_corpus_integrity.py -v`
Expected: PASS.

Run: `make evals EVAL_ARGS="--collect-only -q"`
Expected: Collection still succeeds (no signature drift breaks parametrization).

- [ ] **Step 6: Verify**

Run: `make test && make lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add evals/binder/test_corpus.py evals/binder/test_corpus_integrity.py evals/binder/conftest.py
git commit -m "feat(evals): record binder corpus draw source/latency/tokens and report cost"
```

---

### Task 8: Documentation sync

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/agents.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/concepts.md`

No code changes — this task is prose only, per the spec's Documentation Plan. The code-tree grep-clean gate (`grep -rEn "agents[./]judge_cli" src tests evals`) was already satisfied by Task 4; this task's `docs/agents.md`/`docs/concepts.md` edits are cosmetic prose, not part of that gate (the spec's Verification section explicitly carves them out as "the Documentation Plan's").

- [ ] **Step 1: `docs/agents.md:71`**

Replace:

```
`binder.py`'s prose→checker classifier is a separate host-Claude call (`agents.judge_cli.run_host_judge`): it always uses Claude regardless of the configured judge harness.
```

With:

```
`binder.py`'s prose→checker classifier is a fixed, direct Gemini API call (`gemini-3.1-flash-lite`, stdlib `urllib` — no harness, no host CLI), independent from the configured judge harness and from every task arm's own harness. `ClaudeCodeAgent.judge` is the only host-Claude call site left in the package.
```

- [ ] **Step 2: `docs/concepts.md:15` and `:117`**

Line 15, replace `"an author-invisible, cheap (Haiku) classifier"` with `"an author-invisible, cheap (\`gemini-3.1-flash-lite\`, a direct Gemini API call) classifier"` — leave the rest of the bullet (checker list, false-negative tuning, A9 rule, `make evals:binder` pointer) unchanged.

Line 117, replace:

```
`binder.py`'s prose→checker classifier is a separate, unrelated host-Claude call, unaffected by the configured judge harness.
```

With:

```
`binder.py`'s prose→checker classifier is a separate, unrelated direct Gemini API call — not a harness call at all — unaffected by the configured judge harness.
```

- [ ] **Step 3: `docs/quickstart.md` prerequisites**

Add a bullet after the existing credential bullet (around line 10):

```markdown
- **`GEMINI_API_KEY`** — the binder's classifier credential, required for every graded run (exported or in a repo-root `.env`). Unrelated to the Claude Code credential above.
```

- [ ] **Step 4: `docs/configuration.md`**

Disambiguate the existing `GEMINI_API_KEY` row in the Environment variables table (currently describes only OpenCode's provider fallback) — replace it:

```markdown
| `GEMINI_API_KEY` | Two independent consumers. (1) **The binder** (`binder.py`) — required unconditionally for any graded run; classification always calls `gemini-3.1-flash-lite` directly, regardless of the task or judge harness. Empty counts as missing. (2) **OpenCode's** Google/Gemini provider credential — conditional, used only when neither `OPENROUTER_API_KEY` nor `ANTHROPIC_API_KEY` is set, and only for OpenCode arms. |
```

Add a new `## The binder` section after `## The judge` (before `## CLI flags`), covering the fixed model, the corpus-only override, and degradation visibility:

```markdown
## The binder

The binder (`binder.py`) maps each prose assertion to a deterministic checker or punts to the judge, via a fixed, direct call to `gemini-3.1-flash-lite` — no `[tool.evalspec.binder]` table, no CLI flag; the model is a module constant, not a config surface. Every graded run needs `GEMINI_API_KEY` (see Environment variables above); a missing or empty key fails fast, before any paid arm runs.

A transient binder infra failure degrades that one assertion to judge grading rather than erroring the cell — this is by design (see [`concepts.md`](concepts.md#the-binder)) — but is never silent: each degraded assertion increments `binder_degraded` in the arm's `grading.json`, and the run prints a `WARN binder: N assertion(s) degraded…` summary line when the total is nonzero. A credential rejection (`BinderAuthError`) is never degraded — it fails the run outright.

`EVALSPEC_BINDER_MODEL` overrides the binder model for the **corpus suite only** (`make evals:binder`, `evals/binder/`) — a candidate-model comparison knob with no production surface. Corpus runs call the real Gemini API directly and cost money; they are a separate concern from evalspec's own offline integration tests (`tests/test_execution.py -k bind`), which inject a fake `call_model` and never touch the network.
```

- [ ] **Step 5: Verify**

Run: `make test && make lint`
Expected: PASS. Note `make lint` is `ruff check .` (Python only) and `bin/linters/style_lint.py`'s `DEFAULT_PATHS` doesn't include `docs/` either — neither gate parses Markdown, so this step is confirming the doc edits didn't break anything importable, not validating prose. Proofread the four diffs by eye instead.

Run: `grep -rn "Haiku) classifier\|separate, unrelated host-Claude\|agents.judge_cli.run_host_judge" docs/`
Expected: no output — every stale reference is gone.

- [ ] **Step 6: Commit**

```bash
git add docs/configuration.md docs/agents.md docs/quickstart.md docs/concepts.md
git commit -m "docs: sync binder docs with the direct Gemini transport"
```

---

## Verification (final acceptance gate)

Run every command from the spec's own Verification section, in order:

```bash
make lint
make test
grep -rEn "agents[./]judge_cli" src tests evals   # expect nothing
```

Then, live and paid — needs `GEMINI_API_KEY`:

```bash
make evals:binder                                          # zero punt-leaks; summary prints
                                                             # leaks/retention/over_punt/mismatch/
                                                             # latency/cost/regex-fast-path-count
GEMINI_API_KEY= make evals:binder                           # fails fast before any API call
EVALSPEC_BINDER_MODEL=<candidate> make evals:binder         # candidate-model override, no source edit
uv run pytest tests/test_execution.py -k "bind or binder"   # degradation counting, judge fallback,
                                                             # artifact fields, loud BinderAuthError
```
