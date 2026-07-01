"""Live labeled-corpus eval for the Haiku binder: the false-positive gate.

Samples the real Haiku binder over the gold-labeled corpus and enforces the one hard gate —
a punt-labeled assertion must never bind to a checker. One pytest item per draw, so the run
shows live per-item progress and a leak names the exact draw; `make evals:binder` shards the
draws across xdist workers and the `binder_corpus` marker keeps it out of `make test` (it
costs money and needs a Claude credential). Corpus-wide stats (infra-error guard,
retention/over-punt/mismatch) are aggregated in conftest.py from the per-draw records.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from evalspec.binder import bind
from tests.evalspec.evals.test_binder_corpus_integrity import CORPUS

pytestmark = pytest.mark.binder_corpus

SAMPLES = int(os.environ.get("EVALSPEC_BINDER_SAMPLES", "5"))

# A bind that failed on infra (timeout/CLI crash) even after a retry — distinct from a punt
# (None) and a bind (dict). Excluded from every rate; counted by the infra-error guard.
_ERROR = object()


def _samples_for(entry):
    # Heavy floor only where a false-positive can occur: persistence (the documented leak) and
    # skill_invoked (a punt on an activation assertion is unrecoverable). A zero-tolerance gate
    # needs enough draws on these that "0 observed" is meaningful.
    heavy = entry["cohort"] in ("persistence", "skill_invoked")
    return max(SAMPLES, 20) if heavy else SAMPLES


def _bind_resilient(text):
    # Retry one transient infra failure, then give up with _ERROR — a timed-out/crashed call
    # must not masquerade as a punt. Only infra exceptions are caught, so no logic bug hides.
    for _ in range(2):
        try:
            return bind(text)
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            continue
    return _ERROR


def _draws():
    return [
        pytest.param(e, id=f"{e['cohort']}-{idx}#{s}")
        for idx, e in enumerate(CORPUS)
        for s in range(_samples_for(e))
    ]


def _field_expectation_draws():
    return [
        pytest.param(e, id=f"{e['cohort']}-{idx}#{s}")
        for idx, e in enumerate(CORPUS)
        if e["gold"] == "bind" and e.get("expect")
        for s in range(SAMPLES)
    ]


@pytest.mark.parametrize("entry", _draws())
def test_binder_corpus_blocks_punt_leaks(entry, record):
    b = _bind_resilient(entry["text"])

    record(
        {
            "gold": entry["gold"],
            "cohort": entry["cohort"],
            "expect_checker": entry.get("expect_checker"),
            "expect": entry.get("expect"),
            "actual": {k: b.get(k) for k in entry.get("expect", {})} if isinstance(b, dict) else None,
            "result": "error" if b is _ERROR else "punt" if b is None else "bound",
            "checker": b.get("checker") if isinstance(b, dict) else None,
        }
    )

    if b is _ERROR:
        pytest.skip("infra failure after retry")

    if entry["gold"] == "punt":
        assert not isinstance(b, dict), (
            f"false-positive leak: {entry['text']!r} bound to {b.get('checker')!r}"
        )


@pytest.mark.parametrize("entry", _field_expectation_draws())
def test_binder_corpus_preserves_expected_checker_fields(entry):
    b = _bind_resilient(entry["text"])

    if b is _ERROR:
        pytest.skip("infra failure after retry")

    assert isinstance(b, dict), f"expected bind for {entry['text']!r}, got punt"
    assert all(b.get(k) == v for k, v in entry["expect"].items()), (
        f"field mismatch for {entry['text']!r}: expected {entry['expect']!r}, got {b!r}"
    )
