"""Live labeled-corpus eval for the Haiku binder: the false-positive gate.

Samples the real Haiku binder over the gold-labeled corpus and enforces the one hard
gate — a punt-labeled assertion must never bind to a checker. One pytest item per draw,
so the run shows live per-item progress and a leak names the exact draw; `make
evals:binder` shards the draws across xdist workers and the `binder_corpus` marker keeps
it out of `make test` (it costs money and needs a Claude credential). Corpus-wide stats
(infra-error guard, retention/over-punt/mismatch) are aggregated in conftest.py from the
per-draw records.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from test_binder_corpus_integrity import CORPUS

from evalspec.binder import bind

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("EVALSPEC_BINDER_SAMPLES", "5"))

# A bind that failed on infra (timeout/CLI crash) even after a retry — distinct from a punt
# (None) and a bind (dict). Excluded from every rate; counted by the infra-error guard.
_ERROR = object()


def _samples_for(entry: object) -> object:
    """Choose the sample count for a binder corpus entry."""
    # Heavy floor only where a false-positive can occur: persistence (the documented leak) and
    # skill_invoked (a punt on an activation assertion is unrecoverable). A zero-tolerance gate
    # needs enough draws on these that "0 observed" is meaningful.
    heavy = entry["cohort"] in ("persistence", "skill_invoked")
    return max(SAMPLES, 20) if heavy else SAMPLES


def _bind_resilient(text: object) -> object:
    """Bind one assertion with one retry for transient infra failures."""
    # Retry one transient infra failure, then give up with _ERROR — a timed-out/crashed call
    # must not masquerade as a punt. Only infra exceptions are caught, so no logic bug hides.
    for _ in range(2):
        try:
            return bind(text)
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            continue
    return _ERROR


def _draws() -> object:
    """Build parametrized binder corpus leak-check draws."""
    return [
        pytest.param(e, id=f"{e['cohort']}-{idx}#{s}")
        for idx, e in enumerate(CORPUS)
        for s in range(_samples_for(e))
    ]


def _field_expectation_draws() -> object:
    """Build binder corpus draws with expected checker fields."""
    return [
        pytest.param(e, id=f"{e['cohort']}-{idx}#{s}")
        for idx, e in enumerate(CORPUS)
        if e["gold"] == "bind" and e.get("expect")
        for s in range(SAMPLES)
    ]


@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(entry: object, record: object) -> None:
    """Reject corpus examples where a punt expectation binds to a checker."""
    binding = _bind_resilient(entry["text"])

    record(
        {
            "gold": entry["gold"],
            "cohort": entry["cohort"],
            "expect_checker": entry.get("expect_checker"),
            "expect": entry.get("expect"),
            "actual": {key: binding.get(key) for key in entry.get("expect", {})}
            if isinstance(binding, dict)
            else None,
            "result": "error" if binding is _ERROR else "punt" if binding is None else "bound",
            "checker": binding.get("checker") if isinstance(binding, dict) else None,
        }
    )

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    if entry["gold"] == "punt":
        assert not isinstance(binding, dict), (
            f"false-positive leak: {entry['text']!r} bound to {binding.get('checker')!r}"
        )


@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(entry: object) -> None:
    """Ensure bound checker specs preserve expected fields from the corpus."""
    binding = _bind_resilient(entry["text"])

    if binding is _ERROR:
        pytest.skip("infra failure after retry")

    assert isinstance(binding, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(binding.get(key) == value for key, value in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, got {binding!r}"
    )
