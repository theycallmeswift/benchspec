"""The run's one-time warning that its cells are waiting for a sandbox slot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchspec.events.schema import FORMAT_VERSION
from benchspec.runners.notices import CapacityNotice, capacity_warning


def _write_events(iteration_dir: Path, *events: dict[str, object]) -> None:
    """Write `events` as worker gw0's event file."""
    iteration_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"format_version": FORMAT_VERSION, "ts_ms": 0, "worker": "gw0", **event})
        for event in events
    ]
    (iteration_dir / "events-gw0.jsonl").write_text("\n".join(lines) + "\n")


def test_the_warning_names_the_limit_and_the_workers_to_match() -> None:
    """The warning names the account limit and suggests it as `--workers`."""
    wait = {"type": "cell.wait", "sandbox": "e2b", "limit": 10}

    assert capacity_warning(wait, workers=None) == (
        "warning: this e2b account runs at most 10 sandboxes at once; cells past that wait "
        "in boot for a free one. Pass --workers 10 to match."
    )


def test_the_warning_without_a_limit_asks_for_fewer_workers() -> None:
    """A refusal that names no limit still warns, asking for a lower `--workers`."""
    wait = {"type": "cell.wait", "sandbox": "e2b", "limit": None}

    assert capacity_warning(wait, workers=20) == (
        "warning: this e2b account is at its concurrent-sandbox limit; cells past it wait "
        "in boot for a free one. Pass a lower --workers to match."
    )


def test_a_run_whose_cells_never_wait_stays_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Other events, or none at all, produce no warning."""
    notice = CapacityNotice(tmp_path / "iteration_01", workers=4, poll_seconds=0.01)
    notice.start()
    _write_events(tmp_path / "iteration_01", {"type": "cell.phase", "cell": "c", "phase": "boot"})

    notice.stop()

    assert capsys.readouterr().err == ""


def test_a_wait_the_run_ended_on_is_still_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wait written just before the run ends is caught by the last read on stop."""
    notice = CapacityNotice(tmp_path / "iteration_01", workers=4, poll_seconds=60)
    notice.start()
    _write_events(
        tmp_path / "iteration_01",
        {"type": "cell.wait", "cell": "c", "sandbox": "e2b", "limit": 3},
    )

    notice.stop()

    assert "Pass --workers 3 to match (this run has 4)." in capsys.readouterr().err
