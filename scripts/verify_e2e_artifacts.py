"""Validate evalspec's frozen E2E artifact contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

META_FORMAT_VERSION = 2
BENCHMARK_FORMAT_VERSION = 3
EXPECTED_EVALS = {("hello", "greets-by-name"), ("hello", "writes-greeting-file")}
EXPECTED_ARMS = {
    "baseline": ("claude-code", "sonnet", "medium", "en-US"),
    "trial": ("claude-code", "sonnet", "medium", "en-US"),
    "trial-overrides": ("claude-code", "opus", "high", "en-GB"),
}
EXPECTED_BINDER = {
    "provider": "gemini",
    "model": "gemini-3.1-flash-lite",
    "api_path": "generativelanguage.googleapis.com/v1beta",
}
META_KEYS = {
    "run_id",
    "commit",
    "config_hash",
    "iteration",
    "started_at",
    "evalspec_version",
    "set",
    "runner",
    "arms",
    "observed_arms",
    "judge",
    "binder",
}
INDEX_ROW_KEYS = {"eval_id", "arm", "harness", "model", "effort", "sample", "errored"}
PLANNED_ARM_KEYS = {
    "name", "harness", "model", "effort", "env", "harness_args",
    "requested_version", "capabilities",
}
INDEX_RESULT_KEYS = {
    "skill", "kind", "passed", "total", "duration_ms", "judge_ms",
    "total_tokens", "input_tokens", "output_tokens",
}
PROVENANCE_KEYS = {
    "arm", "actual_version", "actual_version_status", "actual_version_error", "sandbox",
}
SANDBOX_KEYS = {
    "backend", "snapshot", "fingerprint", "base_image_ref", "install_fingerprint",
    "env_script_sha256", "image_digest", "image_digest_status", "image_digest_error",
}
OBSERVED_KEYS = {"actual_version", "actual_version_status", "sandbox"}
JUDGE_KEYS = {"harness", "model", "effort", "timeout", "env", "harness_args", "actual_version"}
BINDER_KEYS = {"provider", "model", "api_path"}
ARM_STATS_KEYS = {
    "pass_rate", "pass_rate_stdev", "duration_ms_mean", "duration_ms_stdev",
    "judge_ms_mean", "tokens_mean", "tokens_stdev", "errored_samples",
    "binder_degraded", "n", "per_eval", "harness", "model", "effort", "env",
    "harness_args",
}
PER_EVAL_KEYS = {
    "group", "eval_id", "samples", "errored_samples", "passed_total", "total_total",
    "pass_rate_mean", "pass_rate_stdev",
}


def _iterations(root: Path) -> list[Path]:
    """Return iteration directories in numeric order."""
    return sorted(
        (
            path
            for path in root.glob("iteration_*")
            if path.is_dir() and path.name.removeprefix("iteration_").isdigit()
        ),
        key=lambda path: int(path.name.removeprefix("iteration_")),
    )


def newest_iteration(root: Path) -> Path | None:
    """Return the highest-numbered iteration directory, if one exists."""
    iterations = _iterations(root)
    return iterations[-1] if iterations else None


def _load_json(path: Path, problems: list[str]) -> dict | None:
    """Load a JSON object and record readable boundary errors."""
    if not path.is_file():
        problems.append(f"{path.name}: missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        problems.append(f"{path.name}: not valid JSON ({error})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: expected a JSON object")
        return None
    return data


def _planned_axes(arms: object, source: str, problems: list[str]) -> dict[str, tuple]:
    """Validate and normalize the exact configured E2E arm roster."""
    if not isinstance(arms, list):
        problems.append(f"{source}: planned arm roster expected a JSON array")
        return {}
    axes = {}
    names = []
    for index, arm in enumerate(arms):
        if not isinstance(arm, dict):
            problems.append(f"{source}: arms[{index}] expected a JSON object")
            continue
        name = arm.get("name")
        missing = sorted(PLANNED_ARM_KEYS - arm.keys())
        if missing:
            problems.append(f"{source}: arms[{index}] missing keys {missing}")
        env = arm.get("env")
        if not isinstance(name, str) or not isinstance(env, dict):
            problems.append(f"{source}: arms[{index}] missing string name or object env")
            continue
        names.append(name)
        axes[name] = (
            arm.get("harness"),
            arm.get("model"),
            arm.get("effort"),
            env.get("GREETING_LOCALE"),
        )
        if env.get("GREETING_STYLE") != "formal":
            problems.append(f"{source}: arm {name!r} has wrong GREETING_STYLE")
        capabilities = arm.get("capabilities")
        expected_env = {
            "GREETING_STYLE": "formal",
            "GREETING_LOCALE": EXPECTED_ARMS.get(name, (None, None, None, None))[3],
        }
        if (
            arm.get("harness_args") != []
            or arm.get("requested_version") != "latest"
            or not isinstance(capabilities, dict)
            or capabilities.get("token_split") is not True
            or env != expected_env
        ):
            problems.append(f"{source}: arm {name!r} has invalid configured fields")
    if len(names) != len(set(names)):
        problems.append(f"{source}: duplicate arm names")
    if axes != EXPECTED_ARMS:
        problems.append(f"{source}: planned arm roster {axes!r}, expected {EXPECTED_ARMS!r}")
    return axes


def _valid_status(*, status: object, value: object, error: object) -> bool:
    """Return whether one available/unavailable value triple is valid."""
    if status == "available":
        return isinstance(value, str) and bool(value) and error is None
    if status == "unavailable":
        return value is None and isinstance(error, str) and bool(error)
    return False


def _valid_observed_provenance(record: dict) -> bool:
    """Validate the aggregate provenance shape emitted by RuntimeProvenance."""
    sandbox = record["sandbox"]
    version_error = record.get("actual_version_error")
    digest_error = sandbox.get("image_digest_error")
    return _valid_status(
        status=record["actual_version_status"],
        value=record["actual_version"],
        error=version_error,
    ) and _valid_status(
        status=sandbox["image_digest_status"],
        value=sandbox["image_digest"],
        error=digest_error,
    )


def _valid_disk_provenance(record: dict) -> bool:
    """Validate the complete on-disk RuntimeProvenance serialization."""
    sandbox = record.get("sandbox")
    if not isinstance(sandbox, dict) or SANDBOX_KEYS - sandbox.keys():
        return False
    return _valid_status(
        status=record.get("actual_version_status"),
        value=record.get("actual_version"),
        error=record.get("actual_version_error"),
    ) and _valid_status(
        status=sandbox.get("image_digest_status"),
        value=sandbox.get("image_digest"),
        error=sandbox.get("image_digest_error"),
    )


def _check_meta(iteration: Path, problems: list[str]) -> dict | None:
    """Validate meta.json and return it for cross-artifact checks."""
    meta = _load_json(iteration / "meta.json", problems)
    if meta is None:
        return None
    missing = sorted(META_KEYS - meta.keys())
    if missing:
        problems.append(f"meta.json: missing keys {missing}")
    if meta.get("format_version") != META_FORMAT_VERSION:
        problems.append(f"meta.json: format_version must be {META_FORMAT_VERSION}")
    if meta.get("iteration") != iteration.name or meta.get("set") != "e2e":
        problems.append("meta.json: iteration/set identity does not match this E2E run")
    _planned_axes(meta.get("arms"), "meta.json", problems)
    observed = meta.get("observed_arms")
    if not isinstance(observed, dict) or set(observed) != set(EXPECTED_ARMS):
        problems.append("meta.json: observed_arms must contain exactly all three E2E arms")
    elif any(
        not isinstance(value, dict)
        or OBSERVED_KEYS - value.keys()
        or not isinstance(value.get("sandbox"), dict)
        or (SANDBOX_KEYS - {"image_digest_error"}) - value["sandbox"].keys()
        for value in observed.values()
    ):
        problems.append("meta.json: observed_arms has invalid provenance shape")
    elif any(not _valid_observed_provenance(value) for value in observed.values()):
        problems.append("meta.json: observed_arms violates status/error invariants")
    judge = meta.get("judge")
    expected_judge = {
        "harness": "codex",
        "model": "gpt-5.5",
        "effort": "medium",
        "timeout": 300,
        "env": {},
        "harness_args": [],
    }
    if (
        not isinstance(judge, dict)
        or JUDGE_KEYS - judge.keys()
        or any(judge.get(key) != value for key, value in expected_judge.items())
    ):
        problems.append("meta.json: judge defaults do not match the resolved E2E judge")
    binder = meta.get("binder")
    if binder != EXPECTED_BINDER:
        problems.append("meta.json: binder identity does not match the production binder")
    return meta


def _check_benchmark(iteration: Path, meta: dict | None, problems: list[str]) -> None:
    """Validate benchmark JSON, Markdown, and run-level consistency."""
    benchmark = _load_json(iteration / "benchmark.json", problems)
    if benchmark is not None:
        if benchmark.get("format_version") != BENCHMARK_FORMAT_VERSION:
            problems.append(f"benchmark.json: format_version must be {BENCHMARK_FORMAT_VERSION}")
        _planned_axes(benchmark.get("planned_arms"), "benchmark.json", problems)
        if (
            benchmark.get("label") != iteration.name
            or benchmark.get("baseline") != "baseline"
            or benchmark.get("max_samples") != 1
        ):
            problems.append("benchmark.json: label/baseline/max_samples do not match the run")
        roster = benchmark.get("roster")
        if not isinstance(roster, list) or any(not isinstance(item, dict) for item in roster):
            problems.append("benchmark.json: roster expected an array of objects")
        else:
            valid_roster = all(
                isinstance(item.get("group"), str) and isinstance(item.get("eval_id"), str)
                for item in roster
            )
            actual = (
                {(item["group"], item["eval_id"]) for item in roster} if valid_roster else set()
            )
            if not valid_roster or actual != EXPECTED_EVALS:
                problems.append(
                    f"benchmark.json: eval roster {actual!r}, expected {EXPECTED_EVALS!r}"
                )
        arms = benchmark.get("arms")
        if not isinstance(arms, dict) or set(arms) != set(EXPECTED_ARMS):
            problems.append("benchmark.json: arms must contain exactly all three E2E arms")
        else:
            for name, stats in arms.items():
                if not isinstance(stats, dict) or ARM_STATS_KEYS - stats.keys():
                    problems.append(f"benchmark.json: arm {name!r} has invalid stats shape")
                    continue
                expected_axes = EXPECTED_ARMS[name][:3]
                if (stats.get("harness"), stats.get("model"), stats.get("effort")) != expected_axes:
                    problems.append(f"benchmark.json: arm {name!r} axes do not match config")
                per_eval = stats.get("per_eval")
                if (
                    not isinstance(per_eval, list)
                    or len(per_eval) != len(EXPECTED_EVALS)
                    or any(
                        not isinstance(item, dict) or PER_EVAL_KEYS - item.keys()
                        for item in per_eval
                    )
                ):
                    problems.append(f"benchmark.json: arm {name!r} has invalid per_eval shape")
                elif {(item["group"], item["eval_id"]) for item in per_eval} != EXPECTED_EVALS:
                    problems.append(f"benchmark.json: arm {name!r} per_eval roster is wrong")
        if meta is not None:
            for key in ("planned_arms", "observed_arms", "runner", "binder"):
                meta_key = "arms" if key == "planned_arms" else key
                if benchmark.get(key) != meta.get(meta_key):
                    problems.append(f"benchmark.json: {key} does not match meta.json")

    markdown_path = iteration / "benchmark.md"
    if not markdown_path.is_file():
        problems.append("benchmark.md: missing")
        return
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        problems.append(f"benchmark.md: unreadable ({error})")
        return
    required = {
        "## Matrix",
        "All evals",
        "## Provenance",
        *EXPECTED_ARMS,
        *(eval_id for _, eval_id in EXPECTED_EVALS),
    }
    for marker in sorted(required):
        if marker not in text:
            problems.append(f"benchmark.md: required content {marker!r} missing")


def _check_samples(iteration: Path, problems: list[str]) -> None:
    """Validate the exact index matrix and provenance beside each graded sample."""
    index = iteration / "index.jsonl"
    if not index.is_file():
        problems.append("index.jsonl: missing")
        return
    rows = []
    try:
        lines = index.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        problems.append(f"index.jsonl: unreadable ({error})")
        return
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"index.jsonl:{line_number}: not valid JSON")
            continue
        if not isinstance(row, dict):
            problems.append(f"index.jsonl:{line_number}: expected a JSON object")
            continue
        missing = sorted((INDEX_ROW_KEYS | INDEX_RESULT_KEYS) - row.keys())
        if missing:
            problems.append(f"index.jsonl:{line_number}: missing keys {missing}")
            continue
        if not (
            isinstance(row["eval_id"], str)
            and isinstance(row["arm"], str)
            and isinstance(row["harness"], str)
            and isinstance(row["model"], str)
            and isinstance(row["effort"], str)
            and isinstance(row["sample"], int)
            and not isinstance(row["sample"], bool)
            and isinstance(row["errored"], bool)
        ):
            problems.append(f"index.jsonl:{line_number}: invalid field types")
            continue
        rows.append((line_number, row))
        expected_axes = EXPECTED_ARMS.get(row["arm"])
        actual_axes = (row["harness"], row["model"], row["effort"])
        if expected_axes is None or actual_axes != expected_axes[:3]:
            problems.append(f"index.jsonl:{line_number}: axes {actual_axes!r} do not match arm")
        numeric_or_none = (int, float, type(None))
        if (
            row["skill"] != "hello"
            or row["kind"] != "eval"
            or not isinstance(row["passed"], int)
            or isinstance(row["passed"], bool)
            or not isinstance(row["total"], int)
            or row["passed"] < 0
            or row["total"] < row["passed"]
            or any(
                not isinstance(row[key], numeric_or_none)
                for key in (
                    "duration_ms",
                    "judge_ms",
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                )
            )
        ):
            problems.append(f"index.jsonl:{line_number}: invalid result fields")
        provenance = (
            iteration / "skills" / "hello" / f"eval-{row['eval_id']}" / row["arm"]
            / f"sample-{row['sample']}" / "provenance.json"
        )
        if not provenance.is_file():
            problems.append(f"provenance.json: missing for index.jsonl:{line_number}")
            continue
        record = _load_json(provenance, problems)
        if record is not None and (
            PROVENANCE_KEYS - record.keys()
            or record.get("arm") != row["arm"]
            or not _valid_disk_provenance(record)
        ):
            problems.append(f"provenance.json: invalid for index.jsonl:{line_number}")
    cells = {(row["arm"], row["eval_id"], row["sample"]) for _, row in rows}
    expected_cells = {(arm, eval_id, 0) for arm in EXPECTED_ARMS for _, eval_id in EXPECTED_EVALS}
    if cells != expected_cells or len(rows) != len(expected_cells):
        problems.append(f"index.jsonl: cell roster {cells!r}, expected {expected_cells!r}")


def main(argv: list[str]) -> int:
    """Validate exactly one iteration created after the supplied pre-run snapshot."""
    if len(argv) < 2:
        usage = "usage: verify_e2e_artifacts.py <artifacts-root> [--existing iteration ...]"
        print(usage, file=sys.stderr)
        return 1
    root = Path(argv[1])
    if not root.is_dir():
        print(f"verify_e2e_artifacts: artifacts root {root} does not exist", file=sys.stderr)
        return 1
    snapshot_mode = len(argv) >= 3 and argv[2] == "--existing"
    if len(argv) >= 3 and not snapshot_mode:
        print("verify_e2e_artifacts: expected --existing before snapshot entries", file=sys.stderr)
        return 1
    if snapshot_mode:
        existing = {Path(value).name for value in argv[3:]}
        new_iterations = [path for path in _iterations(root) if path.name not in existing]
        if len(new_iterations) != 1:
            message = (
                "verify_e2e_artifacts: expected exactly one new iteration, "
                f"found {len(new_iterations)}"
            )
            print(
                message,
                file=sys.stderr,
            )
            return 1
        iteration = new_iterations[0]
    else:
        iteration = newest_iteration(root)
        if iteration is None:
            print(f"verify_e2e_artifacts: no iteration_NN directory under {root}", file=sys.stderr)
            return 1

    problems: list[str] = []
    meta = _check_meta(iteration, problems)
    _check_benchmark(iteration, meta, problems)
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
