# Binder Corpus Infrastructure Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exhausted Gemini retries in the live binder corpus fail their pytest item (never skip), cap corpus-only Gemini calls at 15 seconds, stop the run after three failed draws, and report infra-failure diagnostics separately from success latency — closing the "hung, then green" gap where `make evals` reports Gemini infrastructure failures as skips while the overall run stays green.

**Architecture:** `_bind_resilient` moves into `evals/binder/conftest.py` (next to the `_recording_call_model` it already depends on, and where `evals/binder/test_corpus_integrity.py` can import and unit-test it without a circular import back through `test_corpus.py`'s existing `from test_corpus_integrity import CORPUS`). It gains wall-clock timing across every attempt and returns a `BindAttempt` dataclass carrying the final `RuntimeError` on exhaustion, replacing the old `_ERROR` sentinel. Both live tests in `test_corpus.py` record that result, then `pytest.fail(...)` — never `pytest.skip(...)` — when the retry budget is exhausted. `_recording_call_model` substitutes a suite-constant 15-second timeout for whatever `bind()` passes it (`bind()` always passes 60). `conftest.py`'s session-end aggregation drops the 5%-error-rate gate (redundant now that every exhaustion is already a failed pytest item) and adds a separate infra-failure-count/elapsed-time summary line. The Makefile's `evals` target adds `--maxfail=3` as the run-level circuit breaker.

**Tech Stack:** Python 3.11+, pytest + pytest-xdist, uv, Ruff + houserules (`make lint`), the `urllib`-based Gemini transport in `src/evalspec/grading/binder.py`.

## Global Constraints

- Exhausted Gemini retries in the live binder corpus FAIL the pytest item (final exception type/message), never skip; `BinderAuthError` continues to bypass the retry loop and fail immediately — it is deliberately not a `RuntimeError` subclass (`src/evalspec/grading/binder.py:178-186`).
- Assertion-quality failures (the leak/field-preservation `assert`s) stay distinct from transport failures (`pytest.fail`) in pytest output and corpus records.
- The 15-second timeout is corpus-only: `_recording_call_model` substitutes it when delegating to `binder._call_gemini`; production `bind()`/`_call_gemini` keep the 60-second default at `src/evalspec/grading/binder.py:370`. It is a suite constant, not a new public configuration surface. It's `urllib`'s per-blocking-socket-operation timeout, not a hard wall-clock cap, so one retry typically — not guaranteed — bounds an unhealthy draw to ~30s plus overhead; it is not a cancellation guarantee against a pathologically slow/trickling response.
- `make evals` adds `--maxfail=3` (via `BINDER_MAX_FAILURES ?= 3`) as the circuit breaker — it counts exhausted draws (failed pytest items), not individual attempts. `EVAL_ARGS` passthrough is unchanged.
- Per-draw JSONL records include elapsed time for all attempts, plus the final exception type and message when a draw exhausts its retries.
- The terminal summary reports infrastructure-failure count and elapsed time separately from the success-only latency/token aggregates.
- Remove the now-redundant 5%-infra-error `pytest_sessionfinish` gate in `evals/binder/conftest.py` — no code path may mutate `session.exitstatus` for infra errors anymore.
- Docs: Makefile comments/help text state the 15s corpus timeout and 3-failure stop; README documents that `make evals` is a paid live Gemini run and that exhausted retries fail it.
- Out of scope: production binder timeout/retry/degrade-to-judge behavior; a general retry library or backoff; making the timeout or failure threshold part of evalspec's public CLI or `[tool.evalspec]` config; guaranteeing immediate cancellation of in-flight xdist work.
- Live corpus runs (`make evals`) cost money and need `GEMINI_API_KEY` — never part of a task's offline verification. Each task verifies with `make test` and/or `make lint`; `make evals EVAL_ARGS="--collect-only -q"` (collection only, no live calls) is used where noted and re-collected in the final Verification section.

---

## Task 1: `BindAttempt` + `_bind_resilient` move into `conftest.py`, with elapsed timing

**Files:**
- Modify: `evals/binder/conftest.py` (imports at lines 9-17; new code inserted after `_recording_call_model`, currently ending at line 102)
- Modify: `evals/binder/test_corpus_integrity.py` (imports at lines 7-16)

**Interfaces:**
- Produces: `evals/binder/conftest.py`'s `BindAttempt` dataclass — `binding: dict | None`, `attempts: int`, `elapsed_ms: float`, `error: RuntimeError | None`.
- Produces: `evals/binder/conftest.py`'s `_bind_resilient(text: str, *, sink: list) -> BindAttempt`. On exhaustion, `binding` is `None` and `error` is the final caught `RuntimeError`; `_bind_resilient` never catches `BinderAuthError` (it isn't a `RuntimeError` subclass), so it propagates uncaught.
- Consumes: `evals/binder/conftest.py`'s existing `_recording_call_model(sink: list) -> object`.

- [ ] **Step 1: Write the failing tests**

Add to `evals/binder/test_corpus_integrity.py`, at the end of the file:

```python
def test_bind_resilient_exhausts_retries_and_reports_final_error(monkeypatch: object) -> None:
    """Two transient RuntimeErrors exhaust the retry budget; the final error survives."""
    attempt_count = {"n": 0}

    def failing_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        attempt_count["n"] += 1
        raise RuntimeError(f"Gemini API transport failure: attempt {attempt_count['n']}")

    monkeypatch.setattr(binder, "_call_gemini", failing_call_gemini)

    attempt = conftest._bind_resilient(
        "the summary faithfully reflects the three key facts from the source", sink=[]
    )

    assert attempt.binding is None
    assert attempt.attempts == 2
    assert isinstance(attempt.error, RuntimeError)
    assert "attempt 2" in str(attempt.error)
    assert attempt.elapsed_ms >= 0


def test_bind_resilient_succeeds_after_one_transient_error(monkeypatch: object) -> None:
    """One transient RuntimeError followed by a valid reply succeeds; both attempts count."""
    attempt_count = {"n": 0}

    def flaky_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        attempt_count["n"] += 1
        if attempt_count["n"] == 1:
            raise RuntimeError("Gemini API transport failure: cold start")
        return binder.GeminiReply(
            text='{"punt": true, "reason": "semantic"}',
            prompt_tokens=10,
            output_tokens=2,
            latency_ms=5.0,
        )

    monkeypatch.setattr(binder, "_call_gemini", flaky_call_gemini)

    sink: list = []
    attempt = conftest._bind_resilient(
        "the summary faithfully reflects the three key facts from the source", sink=sink
    )

    assert attempt.error is None
    assert attempt.attempts == 2
    assert attempt.binding is None  # the model punted
    assert len(sink) == 1
    assert attempt.elapsed_ms >= 0


def test_bind_resilient_lets_binder_auth_error_propagate_uncaught(monkeypatch: object) -> None:
    """BinderAuthError bypasses the retry loop entirely — a bad credential must fail loud.

    Deliberately not a RuntimeError subclass (see BinderAuthError's own docstring in
    binder.py), so `_bind_resilient`'s `except RuntimeError` must never catch it.
    """

    def rejecting_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        raise binder.BinderAuthError("Gemini API rejected the credential (HTTP 401): bad key")

    monkeypatch.setattr(binder, "_call_gemini", rejecting_call_gemini)

    with pytest.raises(binder.BinderAuthError):
        conftest._bind_resilient(
            "the summary faithfully reflects the three key facts from the source", sink=[]
        )
```

Add `import conftest` to the import block (alphabetically among the plain third-party-group imports, before `import pytest`):

```python
from __future__ import annotations

from pathlib import Path

import conftest
import pytest
import yaml
from conftest import _latency_cost_summary, _recording_call_model

from evalspec.grading import binder
from evalspec.grading.checkers import derive_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k bind_resilient -v`
Expected: FAIL/ERROR — `conftest` has no attribute `_bind_resilient` (it still only lives in `test_corpus.py`, and `conftest.py` doesn't yet define it).

- [ ] **Step 3: Update `conftest.py`'s module docstring and imports**

Replace the module docstring (lines 1-7):

```python
"""Controller-side aggregation for the parametrized binder corpus eval, plus the resilient
Gemini call the corpus's live tests share.

Each draw is its own pytest item in its own xdist worker, so there's no shared
accumulator: every draw appends a JSON record to a per-worker file, and the controller
reads them all at session end for the corpus-wide infra-failure/latency diagnostics and
the reporting rates. The per-draw false-positive gate and the fail-on-exhausted-retry
behavior both live in the test itself; a draw that exhausts its retries fails its own
pytest item, so `make evals`'s `--maxfail` is the only run-level stop condition.
"""
```

Replace the import block (lines 9-17):

```python
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from evalspec.grading import binder
```

- [ ] **Step 4: Implement `BindAttempt` and `_bind_resilient` in `conftest.py`**

Insert directly after `_recording_call_model`'s definition (after its closing `return call`, currently line 102) and before the pricing constants:

```python
@dataclass(frozen=True)
class BindAttempt:
    """Result of one resilient binder call, including retry diagnostics.

    Attributes:
        binding: Checker spec dict, or None (punt) on success. Always None when `error`
            is set — callers must check `error` first.
        attempts: Retry-loop iterations entered — 1 for a first-try success, 2 when a
            transient RuntimeError triggered the one retry (win or lose).
        elapsed_ms: Wall-clock time spent across every attempt, successful or not — the
            only place a failed attempt's cost is measured, since a raised RuntimeError
            never reaches `_recording_call_model`'s `sink.append`.
        error: The final RuntimeError after both attempts failed, otherwise None.
    """

    binding: dict | None
    attempts: int
    elapsed_ms: float
    error: RuntimeError | None


def _bind_resilient(text: str, *, sink: list) -> BindAttempt:
    """Bind one assertion with one retry for a transient Gemini infra failure.

    Args:
        text: The assertion text to bind.
        sink: List that the recording call_model appends each successful GeminiReply to.

    Returns:
        A BindAttempt. When `error` is set, both attempts failed — the caller must record
        the failure and fail the pytest item, never skip it. BinderAuthError is never
        caught here (it isn't a RuntimeError subclass) and propagates uncaught.
    """
    call_model = _recording_call_model(sink)
    attempts = 0
    elapsed_ms = 0.0
    error: RuntimeError | None = None
    for _ in range(2):
        attempts += 1
        started = time.perf_counter()
        try:
            binding = binder.bind(text, call_model=call_model)
        except RuntimeError as caught:
            elapsed_ms += (time.perf_counter() - started) * 1000
            error = caught
            continue
        elapsed_ms += (time.perf_counter() - started) * 1000
        return BindAttempt(binding=binding, attempts=attempts, elapsed_ms=elapsed_ms, error=None)
    return BindAttempt(binding=None, attempts=attempts, elapsed_ms=elapsed_ms, error=error)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k bind_resilient -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full offline suite and lint**

Run: `make test`
Expected: all pass (the new tests run under `make test`; `test_corpus.py`'s local `_bind_resilient`/`_ERROR` still exist unchanged at this point and are untouched by this task).

Run: `make lint`
Expected: clean. If Ruff flags import ordering in `test_corpus_integrity.py`, run `uv run ruff check --fix evals/binder/test_corpus_integrity.py` and re-check.

- [ ] **Step 7: Commit**

```bash
git add evals/binder/conftest.py evals/binder/test_corpus_integrity.py
git commit -m "feat(evals): move binder corpus retry loop into conftest with elapsed timing"
```

---

## Task 2: Corpus-only 15-second Gemini timeout

**Files:**
- Modify: `evals/binder/conftest.py` (the `_recording_call_model` function and its docstring)
- Modify: `evals/binder/test_corpus_integrity.py`

**Interfaces:**
- Produces: `evals/binder/conftest.py`'s `_CORPUS_TIMEOUT_SECONDS = 15` module constant.
- Changes: `_recording_call_model(sink: list) -> object`'s inner `call(prompt, *, timeout=60)` now always calls `binder._call_gemini(prompt, timeout=_CORPUS_TIMEOUT_SECONDS, model=model)`, ignoring whatever `timeout` the caller passed (`bind()` always passes 60 — `src/evalspec/grading/binder.py:370`, unchanged by this task).

- [ ] **Step 1: Write the failing test**

Add to `evals/binder/test_corpus_integrity.py`:

```python
def test_recording_call_model_uses_corpus_timeout_not_callers_timeout(monkeypatch: object) -> None:
    """The corpus's recording call_model overrides bind()'s 60s with the 15s corpus cap.

    `bind()` always calls `call_model(prompt, timeout=60)` (`src/evalspec/grading/binder.py:370`);
    production stays at 60s there. Only this corpus-side wrapper substitutes the
    suite-constant 15s cap when it delegates to `_call_gemini`.
    """
    captured = {}

    def fake_call_gemini(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        captured["timeout"] = timeout
        return binder.GeminiReply(text="{}", prompt_tokens=0, output_tokens=0, latency_ms=0.0)

    monkeypatch.setattr(binder, "_call_gemini", fake_call_gemini)

    call_model = _recording_call_model([])
    call_model("prompt", timeout=60)  # bind() always passes 60 here

    assert captured["timeout"] == 15
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k corpus_timeout -v`
Expected: FAIL — `captured["timeout"] == 60`, not 15 (`_recording_call_model` still forwards the caller's timeout unchanged).

- [ ] **Step 3: Implement the corpus timeout override**

Replace `_recording_call_model`'s definition (docstring and inner `call`) with:

```python
_CORPUS_TIMEOUT_SECONDS = 15  # Corpus-only cap. bind() always calls call_model(prompt, timeout=60)
# (src/evalspec/grading/binder.py:370); this wrapper substitutes a suite constant instead. It's
# urllib's per-blocking-socket-operation timeout, not a hard wall-clock cap, so an unhealthy draw
# is typically — not guaranteed — bounded well under production's 60s. A suite constant, not a
# public config surface: production bind()/_call_gemini keep their own 60s default untouched.


def _recording_call_model(sink: list) -> object:
    """Build a call_model that delegates to _call_gemini and records every reply.

    Reads EVALSPEC_BINDER_MODEL here, not in _call_gemini — the override is a
    corpus-suite knob and the production transport takes no env input. Exceptions
    propagate unchanged and only successful replies land in `sink`, so per-draw
    `attempts` is tracked by _bind_resilient's retry loop instead: `len(sink)`
    would undercount a retry that raised before producing a reply. Also substitutes
    _CORPUS_TIMEOUT_SECONDS for whatever timeout the caller passes — bind() always
    passes 60, but the corpus suite needs its own shorter cap.

    Args:
        sink: List to append each successful GeminiReply to.

    Returns:
        A call_model callable suitable for `bind(..., call_model=...)`.
    """
    model = os.environ.get("EVALSPEC_BINDER_MODEL", binder.GEMINI_BINDER_MODEL)

    def call(prompt: str, *, timeout: int = 60) -> object:
        """Call the Gemini binder model at the corpus's 15s timeout, recording the reply."""
        reply = binder._call_gemini(prompt, timeout=_CORPUS_TIMEOUT_SECONDS, model=model)
        sink.append(reply)
        return reply

    return call
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k corpus_timeout -v`
Expected: PASS.

- [ ] **Step 5: Run the full offline suite and lint**

Run: `make test`
Expected: all pass, including `test_recording_call_model_honors_evalspec_binder_model_env_override` (unaffected — it only asserts on `captured["model"]`).

Run: `make lint`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add evals/binder/conftest.py evals/binder/test_corpus_integrity.py
git commit -m "feat(evals): cap the binder corpus's Gemini calls at a 15s corpus-only timeout"
```

---

## Task 3: Wire `test_corpus.py` to `_bind_resilient` — fail, never skip, on exhaustion

**Files:**
- Modify: `evals/binder/test_corpus.py` (entire file — module docstring, imports, `_ERROR`/`_bind_resilient` removed, both live test functions updated)
- Modify: `evals/binder/test_corpus_integrity.py` (imports; two new pytester tests appended at the end of the file)

**Interfaces:**
- Consumes: `evals/binder/conftest.py`'s `_bind_resilient(text: str, *, sink: list) -> BindAttempt` (Task 1) and `_CORPUS_TIMEOUT_SECONDS` (Task 2, applied transparently inside `_bind_resilient`).
- Produces: per-draw JSONL records (both `test: "leak"` and `test: "fields"` rows) now always carry `result` (`"error" | "punt" | "bound"`), `elapsed_ms` (float, all attempts), `error_type` (`str | None`), and `error_message` (`str | None`) — consumed by Task 4's `_error_summary`.
- Produces: two offline `pytester`-based tests in `evals/binder/test_corpus_integrity.py` that run the real `test_corpus.py` items in-process (no live Gemini calls, no `make evals` cost) to lock in the fail-not-skip wiring and the `--maxfail=3` circuit breaker end-to-end.

- [ ] **Step 1: Write the failing pytester tests**

Add to `evals/binder/test_corpus_integrity.py`, at the end of the file. Written first, against
the *current* `test_corpus.py` (still `pytest.skip`-on-exhaustion at this point in the task) —
this is expected to fail red until Steps 3-5 below rewire the live tests:

```python
def test_binder_corpus_fails_not_skips_when_gemini_exhausts_retries(
    pytester: object, monkeypatch: object
) -> None:
    """A draw that exhausts its Gemini retries must fail its pytest item, never skip it.

    Runs the real evals/binder/test_corpus.py items in-process against the real
    corpus.yaml — not a synthetic pytester project — with binder._call_gemini forced to
    always raise. `binder` is a proper package module (evalspec.grading.binder), so this
    monkeypatch carries through runpytest_inprocess via the shared sys.modules entry
    (conftest.py itself does NOT carry through: pytest deletes and reimports any
    unpackaged "conftest" module by name on every load, see _importconftest in
    _pytest/config/__init__.py, so a patch on THIS module's `conftest` reference would be
    invisible to the nested run — hence targeting `binder` here, not `conftest`).
    `--rootdir` pins the nested run's rootpath to pytester.path, so its JSONL output
    lands inside this test's own isolated temp dir rather than the real repo's
    tmp/binder_results/ (which pytest_configure wipes at the start of every
    binder_corpus-marked run — piggybacking on the real directory would risk deleting a
    developer's own live `make evals` artifacts).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # any non-empty value satisfies preflight

    def always_fails(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        raise RuntimeError("Gemini API transport failure: forced for this test")

    monkeypatch.setattr(binder, "_call_gemini", always_fails)
    test_corpus_path = Path(__file__).resolve().parent / "test_corpus.py"

    result = pytester.runpytest_inprocess(
        "--rootdir",
        str(pytester.path),
        "-m",
        "binder_corpus",
        "-k",
        "persistence and test_binder_corpus_blocks_punt_leaks",
        "--maxfail=1",
        str(test_corpus_path),
    )

    result.assert_outcomes(failed=1, passed=0, skipped=0)

    rows = [
        json.loads(line)
        for result_path in (pytester.path / "tmp" / "binder_results").glob("results-*.jsonl")
        for line in result_path.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "error"
    assert row["error_type"] == "RuntimeError"
    assert "forced for this test" in row["error_message"]
    assert row["elapsed_ms"] >= 0


def test_binder_corpus_stops_after_maxfail_three(pytester: object, monkeypatch: object) -> None:
    """`--maxfail=3` (the Makefile's circuit breaker) stops the run after 3 failed draws.

    Same in-process real-corpus setup as
    test_binder_corpus_fails_not_skips_when_gemini_exhausts_retries, with --maxfail=3
    instead of 1 so a fourth persistence draw never runs once three have failed.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def always_fails(prompt: object, *, timeout: object = 60, model: object = None) -> object:
        raise RuntimeError("Gemini API transport failure: forced for this test")

    monkeypatch.setattr(binder, "_call_gemini", always_fails)
    test_corpus_path = Path(__file__).resolve().parent / "test_corpus.py"

    result = pytester.runpytest_inprocess(
        "--rootdir",
        str(pytester.path),
        "-m",
        "binder_corpus",
        "-k",
        "persistence and test_binder_corpus_blocks_punt_leaks",
        "--maxfail=3",
        str(test_corpus_path),
    )

    result.assert_outcomes(failed=3, passed=0, skipped=0)
    result.stdout.fnmatch_lines(["*stopping after 3 failures*"])
```

Both tests select `-k persistence`: the `persistence` cohort is always a punt resolved via the
Gemini path, never `_bind_bare_exists`'s regex fast path (`test_no_persistence_entry_is_bind`,
`test_has_persistence_and_semantic_punts` already pin this), so every selected draw is guaranteed
to call `_call_gemini` and hit the forced failure — no draw silently passes through the fast path
and starves `--maxfail=3` of failures to count. Its 20-draw heavy floor (`_samples_for` in
`test_corpus.py`) is comfortably above 3.

Add `import json` to the import block (stdlib group, before `from pathlib import Path`; `import
conftest` here is Task 1's addition, already in place by this point in the plan):

```python
from __future__ import annotations

import json
from pathlib import Path

import conftest
import pytest
import yaml
from conftest import _latency_cost_summary, _recording_call_model

from evalspec.grading import binder
from evalspec.grading.checkers import derive_text
```

- [ ] **Step 2: Run the pytester tests to verify they fail**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k "fails_not_skips or stops_after_maxfail" -v`
Expected: FAIL — `test_corpus.py` still calls `pytest.skip(...)` on exhaustion (Task 1/2 already
moved `_bind_resilient` into `conftest.py`, but `test_corpus.py` itself isn't rewired until Steps
3-5 below), so both `assert_outcomes(...)` calls see a nonzero `skipped` count and `failed=0`
instead of the expected fail counts.

- [ ] **Step 3: Update the module docstring and imports**

Replace the module docstring (lines 1-10):

```python
"""Live labeled-corpus eval for the Gemini binder: the false-positive gate.

Samples the real Gemini binder over the gold-labeled corpus and enforces the one hard
gate — a punt-labeled assertion must never bind to a checker. One pytest item per draw,
so the run shows live per-item progress and a leak names the exact draw; `make
evals` shards the draws across xdist workers and the `binder_corpus` marker keeps
it out of `make test` (it costs money and needs a `GEMINI_API_KEY`). A draw that
exhausts its Gemini retries fails its own pytest item — never a skip — and `make
evals`'s `--maxfail` stops the run after too many. Corpus-wide stats (infra-failure
count/elapsed time, retention/over-punt/mismatch) are aggregated in conftest.py from
the per-draw records.
"""

from __future__ import annotations

import os

import pytest
from conftest import _bind_resilient
from test_corpus_integrity import CORPUS

from evalspec.grading.binder import _bind_bare_exists

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("EVALSPEC_BINDER_SAMPLES", "5"))
```

This drops the `_ERROR = object()` sentinel and its comment, and the local `_bind_resilient` function (previously lines 26-61) entirely — both now live in `conftest.py` (Task 1). `_samples_for`, `_draws`, and `_field_expectation_draws` are unchanged.

- [ ] **Step 4: Update `test_binder_corpus_blocks_punt_leaks`**

```python
@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(entry: object, record: object) -> None:
    """Reject corpus examples where a punt expectation binds to a checker."""
    replies: list = []
    attempt = _bind_resilient(entry["text"], sink=replies)
    # Derive source from the same predicate bind() itself uses to skip call_model,
    # not from whether `replies` is non-empty — an all-retries-errored Gemini draw
    # never appends to `replies` either, and mislabeling it "regex" would inflate
    # regex_fast_path_count and deflate gemini_count.
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"
    result = (
        "error" if attempt.error is not None else "punt" if attempt.binding is None else "bound"
    )

    record(
        {
            "test": "leak",
            "gold": entry["gold"],
            "cohort": entry["cohort"],
            "expect_checker": entry.get("expect_checker"),
            "expect": entry.get("expect"),
            "actual": {key: attempt.binding.get(key) for key in entry.get("expect", {})}
            if isinstance(attempt.binding, dict)
            else None,
            "result": result,
            "checker": attempt.binding.get("checker") if isinstance(attempt.binding, dict) else None,
            "source": source,
            "attempts": attempt.attempts,
            "elapsed_ms": attempt.elapsed_ms,
            "error_type": type(attempt.error).__name__ if attempt.error is not None else None,
            "error_message": str(attempt.error) if attempt.error is not None else None,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if attempt.error is not None:
        pytest.fail(f"infra failure after retry: {type(attempt.error).__name__}: {attempt.error}")

    if entry["gold"] == "punt":
        assert not isinstance(attempt.binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {attempt.binding.get('checker')!r}"
        )
```

- [ ] **Step 5: Update `test_binder_corpus_preserves_expected_checker_fields`**

```python
@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(entry: object, record: object) -> None:
    """Ensure bound checker specs preserve expected fields from the corpus."""
    replies: list = []
    attempt = _bind_resilient(entry["text"], sink=replies)
    source = "regex" if _bind_bare_exists(entry["text"]) else "gemini"
    result = (
        "error" if attempt.error is not None else "punt" if attempt.binding is None else "bound"
    )

    record(
        {
            "test": "fields",
            "result": result,
            "source": source,
            "attempts": attempt.attempts,
            "elapsed_ms": attempt.elapsed_ms,
            "error_type": type(attempt.error).__name__ if attempt.error is not None else None,
            "error_message": str(attempt.error) if attempt.error is not None else None,
            "latency_ms": sum(r.latency_ms for r in replies) if replies else None,
            "prompt_tokens": sum(r.prompt_tokens for r in replies) if replies else None,
            "output_tokens": sum(r.output_tokens for r in replies) if replies else None,
        }
    )

    if attempt.error is not None:
        pytest.fail(f"infra failure after retry: {type(attempt.error).__name__}: {attempt.error}")

    assert isinstance(attempt.binding, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(attempt.binding.get(key) == value for key, value in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, got {attempt.binding!r}"
    )
```

Note on distinctness (spec requirement: "assertion-quality failures remain distinct from transport failures"): the two `assert` statements above raise `AssertionError` with pytest's assertion-rewrite diff; `pytest.fail(...)` raises `Failed` with the plain `"infra failure after retry: <Type>: <message>"` string and no diff — the two failure modes read differently in `pytest`'s terminal output and in `-rf`/`-rF` summaries.

- [ ] **Step 6: Run the pytester tests to verify they pass**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k "fails_not_skips or stops_after_maxfail" -v`
Expected: 2 passed.

The two `binder_corpus`-marked live tests themselves still only run against the live Gemini API and stay deselected under `make test` — consistent with the repo's convention (see `docs/plans/2026-07-09-gemini-binder-execution.md`) that live-run behavior is verified by collection, not a paid run, per task. But the pytester tests added in Steps 1-2 and 6 now exercise their fail-vs-skip wiring offline, in-process, with `binder._call_gemini` forced to raise — this task is no longer collection-only for that behavior. `make test` also still statically re-collects `test_corpus.py` every run (`testpaths = ["tests", "evals"]`, then the marker deselects the two live items) — an import error or `NameError` there fails `make test` outright.

- [ ] **Step 7: Verify collection and the offline suite**

Run: `make test`
Expected: all pass — this confirms `evals/binder/test_corpus.py` still imports and collects cleanly (it's deselected by `-m 'not binder_corpus'`, not skipped from collection), and includes the two new pytester tests from Step 1.

Run: `GEMINI_API_KEY=test-key make evals EVAL_ARGS="--collect-only -q"`
Expected: collection succeeds and lists both `test_binder_corpus_blocks_punt_leaks` and `test_binder_corpus_preserves_expected_checker_fields` items (a fake key satisfies `pytest_configure`'s preflight; `--collect-only` never makes a network call).

Run: `make lint`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add evals/binder/test_corpus.py evals/binder/test_corpus_integrity.py
git commit -m "fix(evals): fail (not skip) binder corpus draws that exhaust Gemini retries"
```

---

## Task 4: Infra-failure summary + remove the 5% session-finish gate

**Files:**
- Modify: `evals/binder/conftest.py` (`pytest_sessionfinish`, plus a new `_error_summary` function)
- Modify: `evals/binder/test_corpus_integrity.py`

**Interfaces:**
- Produces: `evals/binder/conftest.py`'s `_error_summary(rows: list) -> dict` — `{"error_count": int, "error_elapsed_ms_total": float}`, scoped to rows with `result == "error"` from either live test.
- Consumes: the `result`/`elapsed_ms` record fields Task 3 added to every JSONL row.
- Changes: `pytest_sessionfinish` no longer mutates `session.exitstatus`; it always reports an `infra_failures: ...` line (when `error_count` is nonzero) in addition to the existing `latency_ms: ...` line.

- [ ] **Step 1: Write the failing tests**

Add to `evals/binder/test_corpus_integrity.py`:

```python
def test_error_summary_counts_only_error_rows_across_both_tests() -> None:
    """Verify infra-failure count and elapsed time are scoped to result == 'error' rows."""
    rows = [
        {"test": "leak", "result": "bound", "elapsed_ms": 40.0},
        {"test": "leak", "result": "error", "elapsed_ms": 500.0},
        {"test": "fields", "result": "error", "elapsed_ms": 300.0},
        {"test": "fields", "result": "bound", "elapsed_ms": 20.0},
    ]

    summary = conftest._error_summary(rows)

    assert summary["error_count"] == 2
    assert summary["error_elapsed_ms_total"] == pytest.approx(800.0)


class _FakeConfig:
    """Minimal stand-in for pytest.Config, enough to call conftest hooks directly."""

    def __init__(self, rootpath: Path) -> None:
        self.rootpath = rootpath
        self.stash: dict = {}


class _FakeSession:
    """Minimal stand-in for pytest.Session, enough to call pytest_sessionfinish directly."""

    def __init__(self, config: _FakeConfig, exitstatus: int) -> None:
        self.config = config
        self.exitstatus = exitstatus


def test_pytest_sessionfinish_does_not_flip_exitstatus_on_high_error_rate(
    tmp_path: Path,
) -> None:
    """The removed 5% infra-error gate no longer turns a green run red.

    Every exhausted draw now fails its own pytest item via `pytest.fail` in
    `test_corpus.py`, so a broadly-broken run is already red long before
    sessionfinish runs — a second gate flipping `session.exitstatus` here would
    just be a second, redundant failure policy.
    """
    results_dir = tmp_path / "tmp" / "binder_results"
    results_dir.mkdir(parents=True)
    error_row = {
        "test": "leak",
        "gold": "punt",
        "cohort": "semantic",
        "expect_checker": None,
        "expect": None,
        "actual": None,
        "result": "error",
        "checker": None,
        "source": "gemini",
        "attempts": 2,
        "elapsed_ms": 500.0,
        "error_type": "RuntimeError",
        "error_message": "Gemini API transport failure",
        "latency_ms": None,
        "prompt_tokens": None,
        "output_tokens": None,
    }
    rows = [error_row] * 20  # 100% error rate — comfortably over the removed 5% threshold
    (results_dir / "results-gw0.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    config = _FakeConfig(rootpath=tmp_path)
    config.stash[conftest._RAN] = True
    session = _FakeSession(config, exitstatus=0)

    conftest.pytest_sessionfinish(session, exitstatus=0)

    assert session.exitstatus == 0
    summary = "\n".join(config.stash[conftest._SUMMARY])
    assert "infra_failures: 20 draws exhausted retries and failed" in summary
    assert "FAIL" not in summary
```

Add `import json` to the top of the import block (stdlib group, before `from pathlib import Path`):

```python
from __future__ import annotations

import json
from pathlib import Path

import conftest
import pytest
import yaml
from conftest import _latency_cost_summary, _recording_call_model

from evalspec.grading import binder
from evalspec.grading.checkers import derive_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k "error_summary or sessionfinish" -v`
Expected: FAIL/ERROR — `conftest` has no attribute `_error_summary`.

- [ ] **Step 3: Implement `_error_summary` and rewrite `pytest_sessionfinish`**

Insert `_error_summary` after `_latency_cost_summary`'s definition (and before `pytest_sessionfinish`):

```python
def _error_summary(rows: list) -> dict:
    """Aggregate infrastructure-failure count and elapsed time across all draws.

    Scoped to `result == "error"` rows from both live tests — kept separate from the
    leak-only retention/over-punt/mismatch math in `pytest_sessionfinish` and from
    `_latency_cost_summary`'s success-only latency/token aggregates, since an exhausted
    draw never produces a GeminiReply to meter.

    Args:
        rows: Per-draw records from both live tests (leak and field-preservation).

    Returns:
        A dict with the count of exhausted draws and their total elapsed time in ms.
    """
    error_rows = [row for row in rows if row.get("result") == "error"]
    return {
        "error_count": len(error_rows),
        "error_elapsed_ms_total": sum(row.get("elapsed_ms") or 0 for row in error_rows),
    }
```

Replace `pytest_sessionfinish` in full:

```python
def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    """Aggregate binder corpus records after the pytest session."""
    config = session.config
    if not (_is_controller(config) and config.stash.get(_RAN, False)):
        return
    rows = []
    for result_path in _results_dir(config).glob("results-*.jsonl"):
        rows += [json.loads(line) for line in result_path.read_text().splitlines() if line.strip()]
    if not rows:
        return

    leak_rows = [row for row in rows if row["test"] == "leak"]
    lines: list = []
    if leak_rows:
        errored = sum(1 for row in leak_rows if row["result"] == "error")
        binds = [row for row in leak_rows if row["gold"] == "bind" and row["result"] != "error"]
        retention = _rate(
            binds,
            lambda row: row["result"] == "bound" and row["checker"] == row["expect_checker"],
        )
        over_punt = _rate(binds, lambda row: row["result"] == "punt")
        mismatch = _rate(
            binds,
            lambda row: row["result"] == "bound" and row["checker"] != row["expect_checker"],
        )
        lines += [
            f"binder corpus: {len(leak_rows)} draws, {errored} infra errors",
            f"determinism_retention={retention:.3f} (no floor)  "
            f"over_punt_rate={over_punt:.3f}  bind_mismatch={mismatch:.3f}",
        ]

    # Every exhausted draw from either live test now fails its own pytest item (never a
    # skip), so this is a diagnostic total, not a pass/fail gate — --maxfail bounds the
    # run instead. Kept separate from the success-only latency/cost aggregate below,
    # since an exhausted draw never produces a GeminiReply to meter.
    errors = _error_summary(rows)
    if errors["error_count"]:
        lines.append(
            f"infra_failures: {errors['error_count']} draws exhausted retries and failed "
            f"(elapsed={errors['error_elapsed_ms_total']:.0f}ms)"
        )

    # Both live tests (leak + field-preservation) meter latency/cost; the leak-only
    # diagnostics above are unaffected — they only ever read leak_rows.
    cost = _latency_cost_summary(rows)
    lines.append(
        f"latency_ms: mean={cost['latency_ms_mean']} p95={cost['latency_ms_p95']} "
        f"(gemini={cost['gemini_count']} regex_fast_path={cost['regex_fast_path_count']})  "
        f"tokens: prompt={cost['total_prompt_tokens']} output={cost['total_output_tokens']}  "
        f"est_cost_usd~{cost['estimated_cost_usd']:.4f}"
    )

    config.stash[_SUMMARY] = lines
```

This removes the trailing `if leak_rows and errored / len(leak_rows) >= 0.05: ... session.exitstatus = 1` block entirely.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -k "error_summary or sessionfinish" -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full offline suite and lint**

Run: `make test`
Expected: all pass.

Run: `make lint`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add evals/binder/conftest.py evals/binder/test_corpus_integrity.py
git commit -m "fix(evals): report infra-failure elapsed time; drop the redundant 5% error-rate gate"
```

---

## Task 5: `make evals` circuit breaker (`--maxfail=3`) and Makefile docs

**Files:**
- Modify: `Makefile` (lines 16-19)

**Interfaces:**
- Produces: `BINDER_MAX_FAILURES ?= 3` make variable, threaded into `evals`'s pytest invocation as `--maxfail=$(BINDER_MAX_FAILURES)`.

- [ ] **Step 1: Update the Makefile**

Replace lines 16-19:

```make
# Keep modest: high fan-out trips the Gemini call's 15s corpus timeout (12-way -> throttling);
# one retry typically bounds a single unhealthy draw to ~30s plus overhead — urllib's timeout is
# per blocking socket op, not a hard wall-clock cap, so a trickling response can still run longer.
BINDER_WORKERS ?= 6
BINDER_MAX_FAILURES ?= 3
evals:  ## Run the binder corpus (paid live Gemini; 15s corpus timeout, stops after 3 failed draws). Pass EVAL_ARGS="--collect-only -q" to dry-run collection.
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) --maxfail=$(BINDER_MAX_FAILURES) evals/binder $(EVAL_ARGS)
```

- [ ] **Step 2: Verify the target still collects and the breaker flag is wired**

Run: `GEMINI_API_KEY=test-key make evals EVAL_ARGS="--collect-only -q"`
Expected: collection succeeds, exit 0 (same corpus items as before; `--maxfail` has no effect on `--collect-only`).

Run: `make help`
Expected: the `evals` line's description mentions "paid live Gemini" and "stops after 3 failed draws".

Run: `make lint`
Expected: clean (Makefile changes aren't linted by Ruff/houserules, but this confirms nothing else broke).

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat(evals): stop make evals after 3 failed binder corpus draws"
```

---

## Task 6: README — document `make evals` as a paid, fail-on-exhaustion run

**Files:**
- Modify: `README.md` (insert a new section between "## Getting started" and "## Why evalspec")

**Interfaces:** none (documentation only).

- [ ] **Step 1: Insert the new section**

`README.md` currently has no section documenting `make evals` at all — only a passing mention of `GEMINI_API_KEY` inside "## Getting started". Insert this new section immediately after the "## Getting started" code block (`evalspec lint` / `evalspec analyze` / `evalspec run`) and before `## Why evalspec`:

```markdown
## Checking the binder itself

The binder's own accuracy is a separate concern from `evalspec run`: `make evals`
samples the real Gemini binder over a hand-labeled corpus and enforces the one hard
gate — a punt-labeled assertion must never bind to a checker. It's a **paid, live
Gemini run**, not part of `make test`. Each draw retries once on a transient Gemini
failure; a draw that exhausts both attempts **fails its pytest item outright** — never
a silent skip — and the run stops after three failed draws so an unhealthy Gemini
endpoint can't burn through the rest of the corpus.

```bash
make evals                                  # zero punt-leaks; summary prints
make evals EVAL_ARGS="--collect-only -q"    # dry-run collection, no API calls
```
```

- [ ] **Step 2: Verify the doc renders and stays accurate**

Run: `git diff README.md`
Expected: only the new section is added; no other content changed.

Run: `make lint`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document make evals as a paid, fail-on-exhaustion binder corpus run"
```

---

## Verification

Run the full sequence after all six tasks land:

- `make test` — the unit and corpus-integrity suites pass with the new `BindAttempt`/`_bind_resilient`/`_error_summary` contracts.
- `make lint` — Ruff and houserules accept the implementation and documentation changes.
- `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -q` — deterministic corpus harness coverage passes without live Gemini calls.
- `GEMINI_API_KEY=test-key make evals EVAL_ARGS="--collect-only -q"` — the paid suite still collects through the default entrypoint and circuit-breaker invocation.

The spec's Behavior-tier testing items (infra errors visibly red under a live run, the unhealthy run bounded to 3 failures, the summary separating success latency from failure time) require an actual live `make evals` run against `GEMINI_API_KEY` and are intentionally not part of any task's offline verification — consistent with this repo's existing convention that paid corpus runs are exercised manually, not from CI.
