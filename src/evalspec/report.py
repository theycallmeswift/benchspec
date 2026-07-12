"""Aggregate one run's eval results into a single run-level benchmark.

Consumes an explicit list of `eval-*` directories spanning every group (skill) in the
run, reads each sample's grading.json/timing.json, and writes one benchmark.json +
benchmark.md to a caller-supplied out_dir (the iteration root, beside meta.json and
index.jsonl). Rows are keyed `group/eval_id`; columns are the run's arms. Errored samples
(infra failures) are excluded from pass rates but counted and surfaced — a half-crashed
run must not read like a clean one.

Layout: `<group>/eval-<id>/<arm>/sample-<k>/{grading,timing}.json` where arm names are
arbitrary strings discovered from disk (the per-eval subdirs are the arm names). Evals
sort lexically by dir name (`eval-10` before `eval-2`). The `baseline` arm — when one
ran — is the arm every other arm's Δ is measured against; with no baseline in the sweep,
each arm reports its absolute pass rate.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import TYPE_CHECKING

from evalspec.agents import make_agent

if TYPE_CHECKING:
    from evalspec.arms import Set as EvalSet

# Credential-shaped env key names are masked in reports. URLs and other config pass through.
_SECRET_KEY = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)


def redact_env(env: dict | None) -> dict:
    """Mask secret-ish values for recording; URLs and other config pass through."""
    return {
        key: ("***" if _SECRET_KEY.search(key) else value)
        for key, value in (env or {}).items()
    }


def planned_arms(run_set: EvalSet | None) -> list[dict]:
    """The complete configured arm roster, in the per-arm meta.json shape.

    One source of the planned-arm shape, reused by meta.json (`_write_manifest`),
    the benchmark join, and index rows. `requested_version` is the install selector
    the arm's harness resolves to (`make_agent(harness).version()`, e.g. `"latest"`) —
    configured intent, not the concrete binary that ran (that lives in `observed_arms`).
    `capabilities.token_split` replaces the removed run-level `token_split` field.

    Side-effect free and independent of any run artifacts: it reflects config only, so it
    lists every configured arm including those that never ran. `env` is redacted. Returns
    `[]` for a trigger-only run with no eval set. A genuinely unknown harness raises via
    `make_agent`; arms are schema-validated upstream, so that only fires on a real bug.
    """
    if run_set is None:
        return []
    arms = []
    for arm in run_set.arms:
        agent = make_agent(arm.harness)
        arms.append(
            {
                "name": arm.name,
                "harness": arm.harness,
                "model": arm.model,
                "effort": arm.effort,
                "env": redact_env(arm.env),
                "harness_args": arm.harness_args,
                "requested_version": agent.version(),
                "capabilities": {"token_split": agent.capabilities.token_split},
            }
        )
    return arms


def _load_json(path: Path) -> dict | None:
    """Load one JSON artifact from disk."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Malformed JSON in {path}: {error.msg} at line {error.lineno}"
        ) from error


def _sample_dirs(parent: Path) -> list[Path]:
    """Return sample directories below an arm result directory."""
    # Numeric sort: sample-10 must come after sample-2, not before. isdigit filter
    # rejects sample-backup AND sample-1abc, so a stray sibling can't crash the write.
    return sorted(
        (
            sample_dir
            for sample_dir in parent.glob("sample-*")
            if sample_dir.is_dir() and sample_dir.name.removeprefix("sample-").isdigit()
        ),
        key=lambda sample_dir: int(sample_dir.name.removeprefix("sample-")),
    )


def _arm_stats(eval_dirs: list[Path], arm: str) -> dict:
    """Compute aggregate pass-rate and token statistics for an arm."""
    per_eval: list[dict] = []
    # Pooled with equal weight per (eval × sample) pair — sample-weighted, not a
    # per-eval macro-mean.
    pair_rates: list[float] = []
    durations: list[int] = []
    judge_ms: list[int] = []
    tokens: list[int] = []
    errored_total = 0
    binder_degraded_total = 0

    for eval_dir in eval_dirs:
        arm_dir = eval_dir / arm
        if not arm_dir.is_dir():
            continue
        sample_rates: list[float] = []
        passed_total = 0
        total_total = 0
        errored_count = 0

        for sample_dir in _sample_dirs(arm_dir):
            grading = _load_json(sample_dir / "grading.json")
            timing = _load_json(sample_dir / "timing.json")
            if grading is None:
                continue
            binder_degraded_total += grading.get("binder_degraded", 0)
            if grading.get("errored"):
                # Counted but excluded from rates, so a half-crashed run can't read as clean.
                errored_count += 1
                continue
            assertions = grading.get("assertions", [])
            if not assertions:
                continue
            passed = sum(1 for assertion in assertions if assertion.get("passed"))
            sample_rates.append(passed / len(assertions))
            passed_total += passed
            total_total += len(assertions)
            if timing:
                if "duration_ms" in timing:
                    durations.append(timing["duration_ms"])
                if "judge_ms" in timing:
                    judge_ms.append(timing["judge_ms"])
                if "total_tokens" in timing:
                    tokens.append(timing["total_tokens"])

        errored_total += errored_count
        if not sample_rates:
            continue

        pass_rate_stdev = statistics.stdev(sample_rates) if len(sample_rates) > 1 else None

        per_eval.append(
            {
                "group": eval_dir.parent.name,
                "eval_id": eval_dir.name.removeprefix("eval-"),
                "samples": len(sample_rates),
                "errored_samples": errored_count,
                "passed_total": passed_total,
                "total_total": total_total,
                "pass_rate_mean": statistics.mean(sample_rates),
                "pass_rate_stdev": pass_rate_stdev,
            }
        )
        pair_rates.extend(sample_rates)

    return {
        "pass_rate": statistics.mean(pair_rates) if pair_rates else None,
        "pass_rate_stdev": statistics.stdev(pair_rates) if len(pair_rates) > 1 else None,
        "duration_ms_mean": statistics.mean(durations) if durations else None,
        "duration_ms_stdev": statistics.stdev(durations) if len(durations) > 1 else None,
        "judge_ms_mean": statistics.mean(judge_ms) if judge_ms else None,
        "tokens_mean": statistics.mean(tokens) if tokens else None,
        "tokens_stdev": statistics.stdev(tokens) if len(tokens) > 1 else None,
        "errored_samples": errored_total,
        "binder_degraded": binder_degraded_total,
        "n": len(pair_rates),
        "per_eval": per_eval,
    }


def discover_eval_dirs(skills_root: Path) -> list[Path]:
    """Return every `<group>/eval-*` directory under a skills root.

    A group is any child directory of skills_root; an eval directory is an `eval-*`
    child directory of a group. This is the run-level roster passed to build_benchmark.

    Args:
        skills_root: The `skills/` directory holding one child dir per group.

    Returns:
        Every `skills_root/<group>/eval-*` directory, sorted by (group_name, eval_id)
        as strings (lexical, so `eval-10` precedes `eval-2`).
    """
    return sorted(
        (
            eval_dir
            for group_dir in skills_root.iterdir()
            if group_dir.is_dir()
            for eval_dir in group_dir.iterdir()
            if eval_dir.is_dir() and eval_dir.name.startswith("eval-")
        ),
        key=lambda eval_dir: (eval_dir.parent.name, eval_dir.name),
    )


def index_rows(skill_dir: Path, skill: str) -> list[dict]:
    """Flat per-sample rows for the iteration-level index.jsonl.

    Emits one line per eval sample. An aggregator reads these without tree-walking;
    everything here is also in the per-sample artifacts.
    """
    rows: list[dict] = []
    eval_dirs = sorted(
        entry for entry in skill_dir.iterdir() if entry.is_dir() and entry.name.startswith("eval-")
    )
    for eval_dir in eval_dirs:
        for arm_dir in sorted(
            (entry for entry in eval_dir.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name,
        ):
            for sample_dir in _sample_dirs(arm_dir):
                grading = _load_json(sample_dir / "grading.json")
                if grading is None:
                    continue
                timing = _load_json(sample_dir / "timing.json") or {}
                assertions = grading.get("assertions", [])
                rows.append(
                    {
                        "skill": skill,
                        "kind": "eval",
                        "eval_id": eval_dir.name.removeprefix("eval-"),
                        "arm": arm_dir.name,
                        "sample": int(sample_dir.name.removeprefix("sample-")),
                        "errored": bool(grading.get("errored")),
                        "passed": sum(1 for assertion in assertions if assertion.get("passed")),
                        "total": len(assertions),
                        "duration_ms": timing.get("duration_ms"),
                        "judge_ms": timing.get("judge_ms"),
                        "total_tokens": timing.get("total_tokens"),
                        "input_tokens": timing.get("input_tokens"),
                        "output_tokens": timing.get("output_tokens"),
                    }
                )
    return rows


def _pct(value: object) -> str:
    """Format a numeric rate as a percentage string."""
    return "n/a" if value is None else f"{value:.0%}"


def delta_noise_pp(arm_a: dict, arm_b: dict) -> float | None:
    """Return the sampling-noise band for the delta between two arms."""
    # `is None`, not falsy: a zero-variance arm (stdev 0.0) is a real measurement
    # and must not suppress the band the other arm contributes.
    if arm_a.get("pass_rate_stdev") is None or arm_b.get("pass_rate_stdev") is None:
        return None
    return 100 * math.sqrt(
        arm_a["pass_rate_stdev"] ** 2 / arm_a["n"] + arm_b["pass_rate_stdev"] ** 2 / arm_b["n"]
    )


def _headline_lines(benchmark: dict) -> list[str]:
    """Return benchmark headline lines."""
    arms = benchmark["arms"]
    baseline = benchmark.get("baseline")
    if baseline is None:
        scored = [f"{name} {_pct(stats['pass_rate'])}" for name, stats in arms.items()]
        return [f"**Pass rates:** {' · '.join(scored)}"]
    ref_rate = arms.get(baseline, {}).get("pass_rate")
    lines = []
    for name, stats in arms.items():
        if name == baseline:
            continue
        seg = f"**{name}:** baseline {_pct(ref_rate)} → {name} {_pct(stats['pass_rate'])}"
        delta_pp = stats.get("delta_pp")
        if delta_pp is not None:
            seg += f" (**{delta_pp:+.0f}pp**)"
            band = stats.get("delta_noise_pp")
            if band is not None:
                seg += f" — noise band ±{band:.0f}pp"
                if abs(delta_pp) <= band:
                    seg += " (within noise)"
        lines.append(seg)
    # Baseline-only sweep: no contrast arm to take a Δ against, so show the baseline's
    # own rate rather than an empty headline.
    if not lines and baseline in arms:
        return [f"**{baseline}:** {_pct(ref_rate)}"]
    return lines


def _per_eval_rate(arm_stats: dict, group: str, eval_id: str) -> float | None:
    """Return an arm's per-eval pass_rate_mean for one (group, eval_id), or None."""
    return next(
        (
            row["pass_rate_mean"]
            for row in arm_stats["per_eval"]
            if row["group"] == group and row["eval_id"] == eval_id
        ),
        None,
    )


def _rate_cell(rate: float | None, delta_pp: float | None) -> str:
    """Render one matrix cell: — for no rate, bare % for no delta, else `rate (+Npp)`."""
    if rate is None:
        return "—"
    if delta_pp is None:
        return f"{rate:.0%}"
    return f"{rate:.0%} ({delta_pp:+.0f}pp)"


def _matrix_table(benchmark: dict) -> list[str]:
    """Return the run-level group/eval-by-arm matrix with an All evals footer."""
    arms = benchmark["arms"]
    baseline = benchmark.get("baseline")
    # baseline column first, then the rest in declared order (dict preserves it).
    names = ([baseline] if baseline in arms else []) + [
        arm_name for arm_name in arms if arm_name != baseline
    ]
    if not names:
        return []
    headers = [f"{name} ({arms[name].get('harness') or '?'})" for name in names]

    lines = [
        "## Matrix",
        "",
        "| Eval | " + " | ".join(headers) + " |",
        "|------|" + "|".join(["------"] * len(names)) + "|",
    ]
    # Rows come from the roster, not the per-eval union, so an all-errored eval (which
    # has no per-eval row in any arm) still renders a row of — cells.
    for entry in benchmark["roster"]:
        group = entry["group"]
        eval_id = entry["eval_id"]
        ref_rate = _per_eval_rate(arms[baseline], group, eval_id) if baseline in arms else None
        cells = []
        for arm_name in names:
            rate = _per_eval_rate(arms[arm_name], group, eval_id)
            measured = arm_name != baseline and ref_rate is not None and rate is not None
            delta_pp = (rate - ref_rate) * 100 if measured else None
            cells.append(_rate_cell(rate, delta_pp))
        lines.append(f"| {group}/{eval_id} | " + " | ".join(cells) + " |")

    footer_cells = []
    for arm_name in names:
        delta_pp = None if arm_name == baseline else arms[arm_name].get("delta_pp")
        footer_cells.append(_rate_cell(arms[arm_name]["pass_rate"], delta_pp))
    lines.append("| All evals | " + " | ".join(footer_cells) + " |")

    lines.append("")
    return lines


def _format_markdown(benchmark: dict) -> str:
    """Render benchmark results as a Markdown report."""
    arms = benchmark["arms"]
    lines = [f"# Benchmark — {benchmark['label']}", ""]

    lines += [*_headline_lines(benchmark), ""]
    lines += _matrix_table(benchmark)

    for arm, stats in arms.items():
        if stats["n"] == 0 and not stats["errored_samples"]:
            continue
        lines += [f"## {arm}", ""]
        lines.append(f"- Pass rate: **{_pct(stats['pass_rate'])}**")
        if stats.get("harness") or stats.get("model"):
            lines.append(f"- Harness: {stats.get('harness')} · Model: `{stats.get('model')}`")
        if stats.get("harness_args"):
            rendered_args = " ".join(_inline_code(arg) for arg in stats["harness_args"])
            lines.append(f"- Harness args: {rendered_args}")
        if stats.get("env"):
            rendered = ", ".join(f"{key}={value}" for key, value in stats["env"].items())
            lines.append(f"- Env: {rendered}")
        duration_mean = stats["duration_ms_mean"]
        if duration_mean is not None:
            duration_stdev = stats["duration_ms_stdev"]
            entry = f"- Time per sample: {duration_mean / 1000:.1f}s task" + (
                f" ± {duration_stdev / 1000:.1f}s" if duration_stdev else ""
            )
            if stats["judge_ms_mean"] is not None:
                entry += f" + {stats['judge_ms_mean'] / 1000:.1f}s judge"
            lines.append(entry)
        tokens_mean = stats["tokens_mean"]
        if tokens_mean is not None:
            tokens_stdev = stats["tokens_stdev"]
            lines.append(
                f"- Tokens per sample: {tokens_mean:,.0f}"
                + (f" ± {tokens_stdev:,.0f}" if tokens_stdev else "")
            )
        if stats["errored_samples"]:
            lines.append(
                f"- Errored: {stats['errored_samples']} sample(s) excluded "
                "from rates (infra, not skill)"
            )

        lines += [""]
        lines.append("| Eval | Samples | Passed | Total | Rate | Flakiness | Note |")
        lines.append("|------|---------|--------|-------|------|-----------|------|")

        for row in stats["per_eval"]:
            flakiness = ""
            if row["samples"] > 1 and row["pass_rate_stdev"] is not None:
                flakiness = f"±{row['pass_rate_stdev']:.0%}"
            notes = []
            if row["errored_samples"]:
                notes.append(f"{row['errored_samples']} errored")
            lines.append(
                f"| {row['eval_id']} | {row['samples']} | {row['passed_total']} "
                f"| {row['total_total']} | {row['pass_rate_mean']:.0%} | {flakiness} "
                f"| {', '.join(notes)} |"
            )

        lines.append("")

    return "\n".join(lines)


def _inline_code(value: str) -> str:
    """Wrap text in Markdown code ticks without breaking embedded ticks."""
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest_run + 1)
    padding = " " if "`" in value else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def build_benchmark(
    eval_dirs: list[Path],
    label: str,
    *,
    baseline: str | None = None,
    arm_meta: dict | None = None,
) -> dict:
    """Build the machine-readable run-level benchmark report object.

    Args:
        eval_dirs: The run's roster — every `<group>/eval-*` directory to aggregate.
        label: Human-readable report label (the run/iteration name).
        baseline: Arm name every other arm's Δ is measured against, or None for
            absolute scoring. Coerced to None when the baseline has no rate on disk.
        arm_meta: Per-arm run config (harness/model/effort/env/harness_args), keyed by
            arm name. Its keys also declare the arm column order and force a
            configured-but-absent arm to appear as an empty column.

    Returns:
        The benchmark dict: format_version, label, baseline, max_samples, roster, arms.
    """
    configured = list(arm_meta) if arm_meta else []
    # Require a graded sample so a stray subdir (__pycache__, editor temp) never becomes
    # an empty zero-sample arm.
    discovered = sorted(
        {
            arm_dir.name
            for eval_dir in eval_dirs
            for arm_dir in eval_dir.iterdir()
            if arm_dir.is_dir() and any(arm_dir.glob("sample-*/grading.json"))
        }
    )
    arm_names = configured + [name for name in discovered if name not in configured]
    arm_stats = {arm_name: _arm_stats(eval_dirs, arm_name) for arm_name in arm_names}

    # A declared baseline with no rate on disk (never ran, or all samples errored) coerces
    # to None, so arms score absolutely instead of against an absent baseline.
    if baseline is not None and (
        baseline not in arm_stats or arm_stats[baseline]["pass_rate"] is None
    ):
        baseline = None

    # Run config joined onto the on-disk stats by arm name — not derivable from artifacts.
    meta = arm_meta or {}
    for name, stats in arm_stats.items():
        metadata = meta.get(name, {})
        stats["harness"] = metadata.get("harness")
        stats["model"] = metadata.get("model")
        stats["effort"] = metadata.get("effort")
        stats["env"] = metadata.get("env", {})
        stats["harness_args"] = metadata.get("harness_args", [])

    # Each non-baseline arm's Δ and noise band against the baseline rate.
    ref_stats = arm_stats.get(baseline) if baseline is not None else None
    ref_rate = ref_stats["pass_rate"] if ref_stats is not None else None
    for name, stats in arm_stats.items():
        if name == baseline or ref_stats is None:
            continue
        if stats["pass_rate"] is not None and ref_rate is not None:
            stats["delta_pp"] = (stats["pass_rate"] - ref_rate) * 100
            stats["delta_noise_pp"] = delta_noise_pp(stats, ref_stats)

    # The roster comes from the eval dirs on disk, not the per-eval union — so an
    # all-errored eval (which _arm_stats skips) still yields a matrix row.
    roster = [
        {"group": group, "eval_id": eval_id}
        for group, eval_id in sorted(
            {(eval_dir.parent.name, eval_dir.name.removeprefix("eval-")) for eval_dir in eval_dirs}
        )
    ]

    # Observed --count N; per-eval `samples` may be smaller where samples errored.
    max_samples = max(
        (row["samples"] for stats in arm_stats.values() for row in stats["per_eval"]),
        default=0,
    )

    return {
        "format_version": 2,
        "label": label,
        "baseline": baseline,
        "max_samples": max_samples,
        "roster": roster,
        "arms": arm_stats,
    }


def write_benchmark(
    out_dir: Path,
    eval_dirs: list[Path],
    label: str,
    *,
    baseline: str | None = None,
    arm_meta: dict | None = None,
) -> dict:
    """Build over eval_dirs then write out_dir/benchmark.{json,md}.

    Args:
        out_dir: Directory the benchmark artifacts land in (the iteration root).
        eval_dirs: The run's roster — every `<group>/eval-*` directory to aggregate.
        label: Human-readable report label (the run/iteration name).
        baseline: Arm name every other arm's Δ is measured against, or None.
        arm_meta: Per-arm run config keyed by arm name.

    Returns:
        The benchmark dict (also written to disk).
    """
    benchmark = build_benchmark(eval_dirs, label, baseline=baseline, arm_meta=arm_meta)
    (out_dir / "benchmark.json").write_text(json.dumps(benchmark, indent=2) + "\n")
    (out_dir / "benchmark.md").write_text(_format_markdown(benchmark))
    return benchmark


def delta_line(skill: str, benchmark: dict, benchmark_md: Path) -> str:
    """Return one terminal-summary line for a benchmark."""
    arms = benchmark["arms"]
    baseline = benchmark.get("baseline")
    parts = []
    if baseline is None:
        scored = [f"{name} {_pct(stats.get('pass_rate'))}" for name, stats in arms.items()]
        if scored:
            parts.append(" · ".join(scored))
    else:
        ref_rate = arms.get(baseline, {}).get("pass_rate")
        for name, stats in arms.items():
            if name == baseline:
                continue
            rate = stats.get("pass_rate")
            if rate is None and ref_rate is None:
                continue
            seg = f"baseline {_pct(ref_rate)} -> {name} {_pct(rate)}"
            if rate is not None and ref_rate is not None:
                delta_pp = (rate - ref_rate) * 100
                seg += f"  (delta {delta_pp:+.0f}pp"
                band = stats.get("delta_noise_pp")
                if band is not None and abs(delta_pp) <= band:
                    seg += f", within noise ±{band:.0f}pp"
                seg += ")"
            parts.append(seg)

        # Baseline-only sweep: show the baseline's own rate so the terminal line still
        # carries a score, not just a bare path.
        if not parts and baseline in arms:
            parts.append(f"{baseline} {_pct(ref_rate)}")
    return f"{skill}: " + "  |  ".join(parts) + f"  -> {benchmark_md}"
