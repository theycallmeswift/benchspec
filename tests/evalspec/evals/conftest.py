"""Controller-side aggregation for the parametrized binder corpus eval.

Each draw is its own pytest item in its own xdist worker, so there's no shared accumulator:
every draw appends a JSON record to a per-worker file, and the controller reads them all at
session end for the corpus-wide infra-error guard and the reporting rates. The per-draw
false-positive gate asserts in the test itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_SUMMARY = pytest.StashKey[list]()
_RAN = pytest.StashKey[bool]()


def _results_dir(config):
    return Path(config.rootpath) / "tmp" / "binder_results"


def _is_controller(config):
    return not hasattr(config, "workerinput")


def _binder_selected(config):
    # The eval runs via exactly `-m binder_corpus`; `make test` and bare runs use
    # `-m 'not binder_corpus'`, so an exact match keeps the destructive clear off them.
    return (config.getoption("markexpr") or "").strip() == "binder_corpus"


@pytest.fixture
def record(request):
    # Per-worker file: concurrent xdist workers must not share one append target.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    path = _results_dir(request.config) / f"results-{worker}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(rec):
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")

    return write


def pytest_configure(config):
    # Wipe a prior run's records before workers append. Controller-only and binder-only, so an
    # unrelated `make test` never deletes a live run's data. Runs before workers spawn.
    if not (_is_controller(config) and _binder_selected(config)):
        return
    config.stash[_RAN] = True
    d = _results_dir(config)
    if d.exists():
        for f in d.glob("results-*.jsonl"):
            f.unlink()
    d.mkdir(parents=True, exist_ok=True)


def _rate(rows, hit):
    return sum(1 for r in rows if hit(r)) / len(rows) if rows else 0.0


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not (_is_controller(config) and config.stash.get(_RAN, False)):
        return
    rows = []
    for f in _results_dir(config).glob("results-*.jsonl"):
        rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    if not rows:
        return

    errored = sum(1 for r in rows if r["result"] == "error")
    binds = [r for r in rows if r["gold"] == "bind" and r["result"] != "error"]
    retention = _rate(binds, lambda r: r["result"] == "bound" and r["checker"] == r["expect_checker"])
    over_punt = _rate(binds, lambda r: r["result"] == "punt")
    mismatch = _rate(binds, lambda r: r["result"] == "bound" and r["checker"] != r["expect_checker"])

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


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    lines = config.stash.get(_SUMMARY, [])
    if not lines:
        return
    terminalreporter.write_sep("=", "binder corpus")
    for line in lines:
        terminalreporter.line(line)
