"""Validate the frozen artifact contract on the newest e2e iteration.

Usage: ``python scripts/verify_e2e_artifacts.py <artifacts-root>`` (e.g. ``tmp/evals``).

Structural checks only — pass rates are measurements, not gates. Picks the
highest-numbered ``iteration_NN`` directory under the root and verifies every
artifact the v2/v3 contract freezes: ``meta.json`` (planned arms, observed arms,
judge, binder), ``benchmark.json``, ``benchmark.md`` (matrix, roll-up, provenance
section), ``index.jsonl`` rows, and one ``provenance.json`` per graded sample.
Exits 1 with one line per violation, 0 when the contract holds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

META_FORMAT_VERSION = 2
BENCHMARK_FORMAT_VERSION = 3
ARM_KEYS = ("name", "harness", "model", "effort", "env")
INDEX_ROW_KEYS = ("eval_id", "arm", "harness", "model", "effort", "sample", "errored")
BENCHMARK_MD_MARKERS = ("## Matrix", "All evals", "## Provenance")


def newest_iteration(root: Path) -> Path | None:
    """Return the highest-numbered iteration directory under the artifacts root.

    Args:
        root: The artifacts root, e.g. ``tmp/evals``.

    Returns:
        The newest ``iteration_NN`` directory, or None when none exists.
    """
    iterations = [
        path
        for path in root.glob("iteration_*")
        if path.is_dir() and path.name.removeprefix("iteration_").isdigit()
    ]
    if not iterations:
        return None
    return max(iterations, key=lambda path: int(path.name.removeprefix("iteration_")))


def _load_json(path: Path, problems: list[str]) -> dict | None:
    """Load a JSON object from a path, recording a problem when impossible.

    Args:
        path: The JSON file to read.
        problems: The running list of contract violations to append to.

    Returns:
        The parsed object, or None when the file is missing or malformed.
    """
    if not path.is_file():
        problems.append(f"{path.name}: missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        problems.append(f"{path.name}: not valid JSON ({error})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: expected a JSON object")
        return None
    return data


def _check_meta(iteration: Path, problems: list[str]) -> None:
    """Verify meta.json carries the frozen v2 run-level identity blocks.

    Args:
        iteration: The iteration directory under inspection.
        problems: The running list of contract violations to append to.
    """
    meta = _load_json(iteration / "meta.json", problems)
    if meta is None:
        return
    if meta.get("format_version") != META_FORMAT_VERSION:
        problems.append(
            f"meta.json: format_version {meta.get('format_version')!r}, "
            f"expected {META_FORMAT_VERSION}"
        )
    arms = meta.get("arms")
    if not isinstance(arms, list) or not arms:
        problems.append("meta.json: planned `arms` missing or empty")
    else:
        for index, arm in enumerate(arms):
            missing = [key for key in ARM_KEYS if key not in arm]
            if missing:
                problems.append(f"meta.json: arms[{index}] missing keys {missing}")
    if not isinstance(meta.get("observed_arms"), dict) or not meta["observed_arms"]:
        problems.append("meta.json: `observed_arms` missing or empty")
    judge = meta.get("judge")
    if not isinstance(judge, dict) or not all(key in judge for key in ("harness", "model")):
        problems.append("meta.json: `judge` missing harness/model identity")
    if not isinstance(meta.get("binder"), dict):
        problems.append("meta.json: `binder` identity block missing")


def _check_benchmark(iteration: Path, problems: list[str]) -> None:
    """Verify benchmark.json is v3 and benchmark.md renders the frozen sections.

    Args:
        iteration: The iteration directory under inspection.
        problems: The running list of contract violations to append to.
    """
    benchmark = _load_json(iteration / "benchmark.json", problems)
    if benchmark is not None:
        if benchmark.get("format_version") != BENCHMARK_FORMAT_VERSION:
            problems.append(
                f"benchmark.json: format_version {benchmark.get('format_version')!r}, "
                f"expected {BENCHMARK_FORMAT_VERSION}"
            )
        for key in ("arms", "planned_arms", "observed_arms"):
            if key not in benchmark:
                problems.append(f"benchmark.json: `{key}` missing")

    markdown = iteration / "benchmark.md"
    if not markdown.is_file():
        problems.append("benchmark.md: missing")
    else:
        text = markdown.read_text(encoding="utf-8")
        for marker in BENCHMARK_MD_MARKERS:
            if marker not in text:
                problems.append(f"benchmark.md: `{marker}` section missing")


def _check_samples(iteration: Path, problems: list[str]) -> None:
    """Verify index.jsonl rows and per-sample provenance.json artifacts.

    Args:
        iteration: The iteration directory under inspection.
        problems: The running list of contract violations to append to.
    """
    index = iteration / "index.jsonl"
    if not index.is_file():
        problems.append("index.jsonl: missing")
        return
    rows = []
    for line_number, line in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"index.jsonl:{line_number}: not valid JSON")
            continue
        missing = [key for key in INDEX_ROW_KEYS if key not in row]
        if missing:
            problems.append(f"index.jsonl:{line_number}: missing keys {missing}")
        rows.append(row)
    if not rows:
        problems.append("index.jsonl: no rows")
        return

    graded = sum(1 for row in rows if not row.get("errored"))
    provenance_count = len(list(iteration.rglob("provenance.json")))
    if provenance_count < graded:
        problems.append(
            f"provenance.json: {provenance_count} found for {graded} graded samples"
        )


def main(argv: list[str]) -> int:
    """Run the contract checks and report violations.

    Args:
        argv: Command-line arguments, expecting exactly the artifacts root.

    Returns:
        0 when the newest iteration satisfies the contract, 1 otherwise.
    """
    if len(argv) != 2:
        print("usage: verify_e2e_artifacts.py <artifacts-root>", file=sys.stderr)
        return 1
    root = Path(argv[1])
    if not root.is_dir():
        print(f"verify_e2e_artifacts: artifacts root {root} does not exist", file=sys.stderr)
        return 1
    iteration = newest_iteration(root)
    if iteration is None:
        print(f"verify_e2e_artifacts: no iteration_NN directory under {root}", file=sys.stderr)
        return 1

    problems: list[str] = []
    _check_meta(iteration, problems)
    _check_benchmark(iteration, problems)
    _check_samples(iteration, problems)

    if problems:
        print(f"verify_e2e_artifacts: {iteration} violates the artifact contract:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"verify_e2e_artifacts: {iteration} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
