"""Controller-side aggregation for the parametrized binder corpus eval.

Each draw is its own pytest item in its own xdist worker, so there's no shared
accumulator: every draw appends a JSON record to a per-worker file, and the controller
reads them all at session end for the corpus-wide infra-error guard and the reporting
rates. The per-draw false-positive gate asserts in the test itself.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest

from benchspec.grading import binder

_SUMMARY = pytest.StashKey[list[str]]()
_RAN = pytest.StashKey[bool]()


class DrawRecord(TypedDict):
    """One binder draw's per-worker record.

    Every draw meters its transport (`source`, `attempts`, latency, tokens); only the
    leak-gate draws carry the gold label and the bind outcome the retention rates read.
    """

    test: str
    source: str
    attempts: int
    latency_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    gold: NotRequired[str]
    cohort: NotRequired[str]
    expect_checker: NotRequired[str | None]
    expect: NotRequired[dict[str, object] | None]
    actual: NotRequired[dict[str, object] | None]
    result: NotRequired[str]
    checker: NotRequired[str | None]


RecordDraw = Callable[[DrawRecord], None]


def _results_dir(config: pytest.Config) -> Path:
    """Return the directory where binder corpus workers write result records."""
    return Path(config.rootpath) / "tmp" / "binder_results"


def _is_controller(config: pytest.Config) -> bool:
    """Return whether pytest is running in the controller process."""
    return not hasattr(config, "workerinput")


def _binder_selected(config: pytest.Config) -> bool:
    """Return whether this pytest run selected the binder corpus marker."""
    # The eval runs via exactly `-m binder_corpus`; `make test` and bare runs use
    # `-m 'not binder_corpus'`, so an exact match keeps the destructive clear off them.
    return (config.getoption("markexpr") or "").strip() == "binder_corpus"


@pytest.fixture
def record(request: pytest.FixtureRequest) -> RecordDraw:
    """Return a worker-local callback for recording binder corpus draw results."""
    # Per-worker file: concurrent xdist workers must not share one append target.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    path = _results_dir(request.config) / f"results-{worker}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(record_data: DrawRecord) -> None:
        """Append one JSON record to the worker result file."""
        with path.open("a") as result_file:
            result_file.write(json.dumps(record_data) + "\n")

    return write


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest state for benchspec collection."""
    # Wipe a prior run's records before workers append. Controller-only and binder-only, so an
    # unrelated `make test` never deletes a live run's data. Runs before workers spawn.
    if not (_is_controller(config) and _binder_selected(config)):
        return
    try:
        binder.preflight_verify_gemini_key()
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from None
    config.stash[_RAN] = True
    results_dir = _results_dir(config)
    if results_dir.exists():
        for result_path in results_dir.glob("results-*.jsonl"):
            result_path.unlink()
    results_dir.mkdir(parents=True, exist_ok=True)


def _rate(rows: list[DrawRecord], hit: Callable[[DrawRecord], bool]) -> float:
    """Compute the fraction of rows matching a predicate."""
    return sum(1 for row in rows if hit(row)) / len(rows) if rows else 0.0


def _recording_call_model(sink: list[binder.GeminiReply]) -> Callable[..., binder.GeminiReply]:
    """Build a call_model that delegates to _call_gemini and records every reply.

    Reads BENCHSPEC_BINDER_MODEL here, not in _call_gemini — the override is a
    corpus-suite knob and the production transport takes no env input. Exceptions
    propagate unchanged and only successful replies land in `sink`, so per-draw
    `attempts` is tracked by _bind_resilient's retry loop instead: `len(sink)`
    would undercount a retry that raised before producing a reply.

    Args:
        sink: List to append each successful GeminiReply to.

    Returns:
        A call_model callable suitable for `bind(..., call_model=...)`.
    """
    model = os.environ.get("BENCHSPEC_BINDER_MODEL", binder.GEMINI_BINDER_MODEL)

    def call(prompt: str, *, timeout: float = 60) -> binder.GeminiReply:
        """Call the Gemini binder model and record the reply in sink."""
        reply = binder._call_gemini(prompt, timeout=timeout, model=model)
        sink.append(reply)
        return reply

    return call


# Approximate — a corpus-suite pricing constant, not a billing source of truth.
_GEMINI_FLASH_LITE_USD_PER_1K_PROMPT_TOKENS = 0.0003
_GEMINI_FLASH_LITE_USD_PER_1K_OUTPUT_TOKENS = 0.0025


def _gemini_latencies(rows: list[DrawRecord]) -> list[float]:
    """Return the measured latencies of the Gemini-sourced rows, ascending."""
    latencies: list[float] = []
    for row in rows:
        latency = row["latency_ms"]
        if latency is not None:
            latencies.append(latency)
    return sorted(latencies)


def _latency_cost_summary(rows: list[DrawRecord]) -> dict[str, float | int | None]:
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
    gemini_rows = [row for row in rows if row["source"] == "gemini"]
    latencies = _gemini_latencies(gemini_rows)
    prompt_tokens = sum(row["prompt_tokens"] or 0 for row in gemini_rows)
    output_tokens = sum(row["output_tokens"] or 0 for row in gemini_rows)
    p95_index = max(0, int(len(latencies) * 0.95) - 1) if latencies else None
    return {
        "regex_fast_path_count": sum(1 for row in rows if row["source"] == "regex"),
        "gemini_count": len(gemini_rows),
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p95": latencies[p95_index] if p95_index is not None else None,
        "total_prompt_tokens": prompt_tokens,
        "total_output_tokens": output_tokens,
        "estimated_cost_usd": (
            prompt_tokens / 1000 * _GEMINI_FLASH_LITE_USD_PER_1K_PROMPT_TOKENS
            + output_tokens / 1000 * _GEMINI_FLASH_LITE_USD_PER_1K_OUTPUT_TOKENS
        ),
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Aggregate binder corpus records after the pytest session."""
    config = session.config
    if not (_is_controller(config) and config.stash.get(_RAN, False)):
        return
    rows: list[DrawRecord] = []
    for result_path in _results_dir(config).glob("results-*.jsonl"):
        rows += [json.loads(line) for line in result_path.read_text().splitlines() if line.strip()]
    if not rows:
        return

    leak_rows = [row for row in rows if row["test"] == "leak"]
    lines: list[str] = []
    errored = 0
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


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    """Print binder corpus summary lines in pytest output."""
    lines = config.stash.get(_SUMMARY, [])
    if not lines:
        return
    terminalreporter.write_sep("=", "binder corpus")
    for line in lines:
        terminalreporter.line(line)
