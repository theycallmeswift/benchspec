"""Live checks on the artifacts of a real `benchspec run` of the in-repo suite.

Every test here spends money and needs a sandbox backend and OpenRouter credentials, so
the `e2e` marker keeps them out of `make test`; `make e2e` runs them after the suite, with
plugin autoload off so this session's own benchspec plugin never claims the artifacts the
child run writes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.cli import run_benchspec

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTING_GROUP = "hello-routing"
TRIAL_ARMS = ("trial", "trial-codex", "trial-opencode")


def _run_routing_pair() -> list[dict]:
    """Run the routing pair on every harness, one sample per cell; return its index rows."""
    result = run_benchspec(
        "run",
        "--set", "e2e-openrouter",
        "--judge-provider", "openrouter",
        "--judge-model", "google/gemini-3.5-flash",
        "--binder-provider", "openrouter",
        "--",
        "--count", "1",
        "-n", "4",
        "-k", ROUTING_GROUP,
        cwd=REPO_ROOT,
        # The child is a real run: it needs the benchspec and xdist plugins autoloaded.
        drop=("PYTEST_DISABLE_PLUGIN_AUTOLOAD",),
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    report = re.search(r"^Report: (.+)$", result.stdout, re.MULTILINE)
    assert report, f"no `Report:` line in:\n{result.stdout}"
    index_path = REPO_ROOT / Path(report.group(1)).parent / "index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text().splitlines() if line.strip()]
    return [row for row in rows if row["skill"] == ROUTING_GROUP]


def _row(rows: list[dict], eval_id: str, arm: str) -> dict:
    """The one sample of `eval_id` on `arm`."""
    (row,) = [row for row in rows if row["eval_id"] == eval_id and row["arm"] == arm]
    return row


def test_every_harness_stops_a_run_once_its_skill_fires() -> None:
    """Verify Claude Code, Codex and OpenCode each end the should-trigger prompt early and pass."""
    rows = _run_routing_pair()

    for arm in TRIAL_ARMS:
        fires = _row(rows, "fires-on-a-greeting", arm)
        assert fires["errored"] is False, arm
        assert fires["stopped"] is True, arm
        assert (fires["passed"], fires["total"]) == (1, 1), arm

        quiet = _row(rows, "stays-quiet-on-a-listing", arm)
        assert quiet["errored"] is False, arm
        assert quiet["stopped"] is False, arm
        assert (quiet["passed"], quiet["total"]) == (1, 1), arm

    for eval_id in ("fires-on-a-greeting", "stays-quiet-on-a-listing"):
        assert _row(rows, eval_id, "baseline")["stopped"] is False
