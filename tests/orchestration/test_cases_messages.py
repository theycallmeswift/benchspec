"""Tests for the eval-case assertion messaging."""

from __future__ import annotations

from harnessbench.orchestration.cases import _errored_message
from harnessbench.orchestration.execution import ArmOutcome


def _outcome(assertions: list[dict]) -> ArmOutcome:
    """Build an errored ArmOutcome carrying the given graded assertions."""
    grading = {"eval_id": "greets", "arm": "baseline", "assertions": assertions}
    return ArmOutcome(grading, errored=True, duration_ms=0, total_tokens=0)


def test_errored_message_inlines_the_infra_evidence() -> None:
    """The judge's infra evidence is surfaced in the pytest summary line."""
    outcome = _outcome(
        [
            {"text": "a", "passed": True, "evidence": "ok"},
            {"text": "b", "passed": False, "evidence": "JUDGE INFRA ERROR: `codex` exited 1"},
        ]
    )

    message = _errored_message("baseline", outcome)

    assert "grading.json" in message
    assert "JUDGE INFRA ERROR: `codex` exited 1" in message


def test_errored_message_points_at_grading_json_without_evidence() -> None:
    """With no infra evidence, the message still names the right file, not the transcript."""
    outcome = _outcome([{"text": "a", "passed": False, "evidence": "CHECK regex: no match"}])

    message = _errored_message("trial", outcome)

    assert "grading.json" in message
    assert "transcript.json" not in message
