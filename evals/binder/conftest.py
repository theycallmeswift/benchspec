"""Controller-side aggregation for the parametrized binder corpus eval.

Each draw is its own pytest item in its own xdist worker, so there's no shared
accumulator: every draw appends a JSON record to a per-worker file, and the controller
reads them all at session end for the corpus-wide infra-error guard and the reporting
rates. The per-draw false-positive gate asserts in the test itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evalspec import binder

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

    errored = sum(1 for row in rows if row["result"] == "error")
    binds = [row for row in rows if row["gold"] == "bind" and row["result"] != "error"]
    retention = _rate(
        binds,
        lambda row: row["result"] == "bound" and row["checker"] == row["expect_checker"],
    )
    over_punt = _rate(binds, lambda row: row["result"] == "punt")
    mismatch = _rate(
        binds,
        lambda row: row["result"] == "bound" and row["checker"] != row["expect_checker"],
    )

    lines = [
        f"binder corpus: {len(rows)} draws, {errored} infra errors",
        f"determinism_retention={retention:.3f} (no floor)  "
        f"over_punt_rate={over_punt:.3f}  bind_mismatch={mismatch:.3f}",
    ]
    # 0 leaks is meaningless if most draws errored, so a broadly-broken infra run fails loud.
    if errored / len(rows) >= 0.05:
        lines.append(f"FAIL: too many infra errors ({errored}/{len(rows)}) — gate unmeasurable")
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
