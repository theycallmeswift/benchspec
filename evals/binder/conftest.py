"""Controller-side aggregation for the binder corpus eval, plus its shared resilient Gemini call.

Each draw is its own pytest item in its own xdist worker, so there's no shared
accumulator: every draw appends a JSON record to a per-worker file, and the controller
reads them all at session end for the corpus-wide infra-failure/latency diagnostics and
the reporting rates. The per-draw false-positive gate and the fail-on-exhausted-retry
behavior both live in the test itself; a draw that exhausts its retries fails its own
pytest item, so `make evals`'s `--maxfail` is the only run-level stop condition.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from evalspec.grading import binder

_SUMMARY = pytest.StashKey[list]()
_RAN = pytest.StashKey[bool]()


def _results_dir(config: object) -> object:
    """Return the directory where binder corpus workers write result records."""
    return Path(config.rootpath) / "tmp" / "binder_results"


def _is_controller(config: object) -> bool:
    """Return whether pytest is running in the controller process."""
    return not hasattr(config, "workerinput")


def _binder_selected(config: object) -> object:
    """Return whether this pytest run selected the binder corpus marker."""
    # The eval runs via exactly `-m binder_corpus`; `make test` and bare runs use
    # `-m 'not binder_corpus'`, so an exact match keeps the destructive clear off them.
    return (config.getoption("markexpr") or "").strip() == "binder_corpus"


@pytest.fixture
def record(request: object) -> object:
    """Return a worker-local callback for recording binder corpus draw results."""
    # Per-worker file: concurrent xdist workers must not share one append target.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    path = _results_dir(request.config) / f"results-{worker}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(record_data: object) -> None:
        """Append one JSON record to the worker result file."""
        with path.open("a") as result_file:
            result_file.write(json.dumps(record_data) + "\n")

    return write


def pytest_configure(config: object) -> None:
    """Configure pytest state for evalspec collection."""
    # Wipe a prior run's records before workers append. Controller-only and binder-only, so an
    # unrelated `make test` never deletes a live run's data. Runs before workers spawn.
    if not (_is_controller(config) and _binder_selected(config)):
        return
    try:
        binder.preflight_gemini_key()
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from None
    config.stash[_RAN] = True
    results_dir = _results_dir(config)
    if results_dir.exists():
        for result_path in results_dir.glob("results-*.jsonl"):
            result_path.unlink()
    results_dir.mkdir(parents=True, exist_ok=True)


def _rate(rows: object, hit: object) -> object:
    """Compute the fraction of rows matching a predicate."""
    return sum(1 for row in rows if hit(row)) / len(rows) if rows else 0.0


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


# Approximate — a corpus-suite pricing constant, not a billing source of truth.
_GEMINI_FLASH_LITE_USD_PER_1K_PROMPT_TOKENS = 0.0001
_GEMINI_FLASH_LITE_USD_PER_1K_OUTPUT_TOKENS = 0.0004


def _latency_cost_summary(rows: list) -> dict:
    """Aggregate latency/token/cost stats over Gemini-sourced rows only.

    Regex fast-path rows (bare file_exists assertions bound without any API call)
    are reported as their own count, never folded into the latency mean — their
    near-zero latency would corrupt it.

    Args:
        rows: Per-draw records from both live tests (leak and field-preservation).

    Returns:
        A dict of regex/gemini counts, latency mean/p95, token totals, and an
        approximate USD cost estimate.
    """
    gemini_rows = [row for row in rows if row.get("source") == "gemini"]
    latencies = sorted(
        row["latency_ms"] for row in gemini_rows if row.get("latency_ms") is not None
    )
    prompt_tokens = sum(row.get("prompt_tokens") or 0 for row in gemini_rows)
    output_tokens = sum(row.get("output_tokens") or 0 for row in gemini_rows)
    p95_index = max(0, int(len(latencies) * 0.95) - 1) if latencies else None
    return {
        "regex_fast_path_count": sum(1 for row in rows if row.get("source") == "regex"),
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

    # Both live tests (leak + field-preservation) meter latency/cost; the leak-only
    # gate above is unaffected — it only ever read leak_rows.
    cost = _latency_cost_summary(rows)
    lines.append(
        f"latency_ms: mean={cost['latency_ms_mean']} p95={cost['latency_ms_p95']} "
        f"(gemini={cost['gemini_count']} regex_fast_path={cost['regex_fast_path_count']})  "
        f"tokens: prompt={cost['total_prompt_tokens']} output={cost['total_output_tokens']}  "
        f"est_cost_usd~{cost['estimated_cost_usd']:.4f}"
    )

    # 0 leaks is meaningless if most draws errored, so a broadly-broken infra run fails loud.
    # Guarded on leak_rows: a `-k`-filtered run that selects only the field-preservation
    # test has no leak rows to divide by, and must not crash sessionfinish over it.
    if leak_rows and errored / len(leak_rows) >= 0.05:
        lines.append(
            f"FAIL: too many infra errors ({errored}/{len(leak_rows)}) — gate unmeasurable"
        )
        if session.exitstatus == 0:
            session.exitstatus = 1
    config.stash[_SUMMARY] = lines


def pytest_terminal_summary(terminalreporter: object, exitstatus: object, config: object) -> None:
    """Print binder corpus summary lines in pytest output."""
    lines = config.stash.get(_SUMMARY, [])
    if not lines:
        return
    terminalreporter.write_sep("=", "binder corpus")
    for line in lines:
        terminalreporter.line(line)
