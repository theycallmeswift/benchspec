"""Fail a live run whose samples are missing their time or token accounting.

Run with ``uv run scripts/check_sample_accounting.py`` right after ``benchspec run`` (``make
e2e`` does, after each of its runs). It reads the newest ``tmp/evals/iteration_NN/`` and
requires every sample that was not errored to carry a nonzero ``duration_ms``,
``total_tokens``, ``input_tokens``, and ``output_tokens`` in its ``timing.json``: the fields
the report's time-per-sample and tokens-per-sample are built from. Errored samples are
skipped, since the benchmark already counts them and a sandbox failure has nothing to time.

The unit suite proves the adapters against recorded CLI output; this proves them against
whatever the pinned CLIs print today, which is where a harness's output format drifts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchspec.orchestration.workspace import evals_root

REQUIRED_NONZERO_FIELDS = ("duration_ms", "total_tokens", "input_tokens", "output_tokens")


def newest_iteration(root: Path) -> Path:
    """Return the highest-numbered ``iteration_NN`` directory under ``root``.

    Args:
        root: The ``tmp/evals`` directory a run writes into.

    Returns:
        The newest iteration directory.

    Raises:
        SystemExit: if ``root`` holds no iteration.
    """
    iterations = [
        path
        for path in root.glob("iteration_*")
        if path.is_dir() and path.name.removeprefix("iteration_").isdecimal()
    ]
    if not iterations:
        raise SystemExit(f"no iteration_NN directory under {root}")

    return max(iterations, key=lambda path: int(path.name.removeprefix("iteration_")))


def sample_problems(sample_dir: Path) -> list[str]:
    """Return what is missing from one sample's accounting; empty when it is complete.

    Args:
        sample_dir: A ``sample-K`` directory holding ``grading.json`` and ``timing.json``.

    Returns:
        One message per missing or zero field.
    """
    timing_path = sample_dir / "timing.json"
    if not timing_path.is_file():
        return ["no timing.json"]

    timing = json.loads(timing_path.read_text())
    return [
        f"{field} is {timing.get(field)!r}"
        for field in REQUIRED_NONZERO_FIELDS
        if not timing.get(field)
    ]


def is_errored(sample_dir: Path) -> bool:
    """Return whether the sample's ``grading.json`` records it as errored."""
    grading_path = sample_dir / "grading.json"
    return grading_path.is_file() and bool(json.loads(grading_path.read_text()).get("errored"))


def main() -> int:
    """Check the newest iteration's samples and print a verdict.

    Returns:
        0 when every clean sample's accounting is complete, 1 otherwise.
    """
    iteration = newest_iteration(evals_root(Path.cwd()))
    sample_dirs = sorted(iteration.glob("skills/*/eval-*/*/sample-*"))
    clean_samples = [sample_dir for sample_dir in sample_dirs if not is_errored(sample_dir)]

    failures = [
        f"{sample_dir.relative_to(iteration)}: {problem}"
        for sample_dir in clean_samples
        for problem in sample_problems(sample_dir)
    ]

    if not clean_samples:
        print(f"sample accounting: {iteration.name} has no clean sample to check")
        return 1

    if failures:
        print(f"sample accounting incomplete in {iteration.name}:")
        print("\n".join(f"  {failure}" for failure in failures))
        return 1

    print(f"sample accounting ok: {iteration.name}, {len(clean_samples)} clean samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
