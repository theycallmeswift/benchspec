"""Aggregate one skill-eval iteration's results into a benchmark.

Walks a skill's iteration dir, reads each sample's grading.json/timing.json, and writes
benchmark.json + benchmark.md at the root of that dir. Errored samples (infra failures)
are excluded from pass rates but counted and surfaced — a half-crashed run must not read
like a clean one.

Layout: `eval-<id>/<arm>/sample-<k>/{grading,timing}.json` where arm names are arbitrary
strings discovered from disk (the per-eval subdirs are the arm names). The `baseline`
arm — when one ran — is the arm every other arm's Δ is measured against; with no
baseline in the sweep, each arm reports its absolute pass rate. The dir is
`tmp/evals/iteration_NN/skills/<skill>/`.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

from evalspec.trigger import xfail_applies

# Credential-shaped env key names; their values are masked in the report. URLs and
# other config pass through.
_SECRET_KEY = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)


def redact_env(env: dict | None) -> dict:
    """Mask secret-ish values for recording; URLs and other config pass through."""
    return {k: ("***" if _SECRET_KEY.search(k) else v) for k, v in (env or {}).items()}


def _load_json(path: Path) -> dict | None:
    """Load one JSON artifact from disk."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON in {path}: {e.msg} at line {e.lineno}") from e


def _sample_dirs(parent: Path) -> list[Path]:
    """Return sample directories below an arm result directory."""
    # Numeric sort: sample-10 must come after sample-2, not before. isdigit filter
    # rejects sample-backup AND sample-1abc, so a stray sibling can't crash the write.
    return sorted(
        (
            p
            for p in parent.glob("sample-*")
            if p.is_dir() and p.name.removeprefix("sample-").isdigit()
        ),
        key=lambda p: int(p.name.removeprefix("sample-")),
    )


def _trigger_qdirs(root: Path) -> list[Path]:
    """Return trigger-query result directories for one skill."""
    # `trigger-<slug>` query dirs, sorted by slug. Slugs are kebab strings, so a
    # plain name sort is stable and deterministic.
    return sorted((d for d in root.glob("trigger-*") if d.is_dir()), key=lambda d: d.name)


def _arm_stats(eval_dirs: list[Path], arm: str) -> dict:
    """Compute aggregate pass-rate and token statistics for an arm."""
    per_eval: list[dict] = []
    pair_rates: list[float] = []  # one rate per (eval × sample) — the macro-mean unit
    durations: list[int] = []
    judge_ms: list[int] = []
    tokens: list[int] = []
    errored_total = 0

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
            if grading.get("errored"):
                # Infra failure — excluded from rates, counted so the report can't
                # present a half-crashed run as a clean one.
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

        per_eval.append(
            {
                "eval_id": eval_dir.name.removeprefix("eval-"),
                "samples": len(sample_rates),
                "errored_samples": errored_count,
                "passed_total": passed_total,
                "total_total": total_total,
                "pass_rate_mean": statistics.mean(sample_rates),
                "pass_rate_stdev": statistics.stdev(sample_rates)
                if len(sample_rates) > 1
                else None,
            }
        )
        pair_rates.extend(sample_rates)

    return {
        # Macro-mean across (eval × sample) pairs. If errors drop samples unevenly,
        # evals with more surviving samples carry more weight — see per_eval[].samples.
        "pass_rate": statistics.mean(pair_rates) if pair_rates else None,
        "pass_rate_stdev": statistics.stdev(pair_rates) if len(pair_rates) > 1 else None,
        "duration_ms_mean": statistics.mean(durations) if durations else None,
        "duration_ms_stdev": statistics.stdev(durations) if len(durations) > 1 else None,
        "judge_ms_mean": statistics.mean(judge_ms) if judge_ms else None,
        "tokens_mean": statistics.mean(tokens) if tokens else None,
        "tokens_stdev": statistics.stdev(tokens) if len(tokens) > 1 else None,
        "errored_samples": errored_total,
        "n": len(pair_rates),
        "per_eval": per_eval,
    }


def _as_expected(timing: dict) -> bool:
    """Whether one trigger sample matches its documented expectation.

    A tier-scoped `xfail` is a documented routing miss only on the tiers it lists:
    on one of those tiers a miss is a green xfail and an unexpected fire is an XPASS,
    both non-failing on the scoreboard. On any other tier the query is as-expected only
    when it passed (`fired == should_trigger`), matching the strict gate. The sample
    carries its own run `model`, so the scoreboard and the gate agree.
    """
    xfail = timing.get("xfail")
    if xfail and xfail_applies(xfail, timing.get("model", "")):
        return True
    return timing["passed"]


def _trigger_rows(eval_root: Path) -> list[dict]:
    """Read trigger result records for report rendering."""
    rows: list[dict] = []
    for query_dir in _trigger_qdirs(eval_root):
        samples = [
            timing
            for sample_dir in _sample_dirs(query_dir)
            if (timing := _load_json(sample_dir / "timing.json"))
        ]
        if not samples:
            continue
        expected = samples[0]["should_trigger"]
        rows.append(
            {
                "slug": samples[0]["slug"],
                "query": samples[0].get("query", ""),
                "should_trigger": expected,
                "xfail": samples[0].get("xfail"),
                "samples": len(samples),
                "as_expected": sum(1 for timing in samples if _as_expected(timing)),
            }
        )
    return rows


def index_rows(skill_dir: Path, skill: str) -> list[dict]:
    """Flat per-sample rows for the iteration-level index.jsonl.

    Emits one line per eval sample and trigger-query sample. An aggregator reads these
    without tree-walking; everything here is also in the per-sample artifacts.
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
    for query_dir in _trigger_qdirs(skill_dir):
        for sample_dir in _sample_dirs(query_dir):
            timing = _load_json(sample_dir / "timing.json")
            if timing is None:
                continue
            rows.append(
                {
                    "skill": skill,
                    "kind": "trigger",
                    "slug": timing["slug"],
                    "sample": int(sample_dir.name.removeprefix("sample-")),
                    "passed": timing["passed"],
                    "should_trigger": timing["should_trigger"],
                    "fires": timing["fires"],
                    "threshold": timing["threshold"],
                    "duration_ms": sum(
                        pass_record.get("ms", 0) for pass_record in timing.get("per_pass", [])
                    ),
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


def _md_cell(text: str, limit: int = 48) -> str:
    """Format a Markdown table cell with stable scalar rendering."""
    cell = text.replace("|", "\\|").replace("\n", " ")
    return cell if len(cell) <= limit else cell[: limit - 1] + "…"


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


def _matrix_table(benchmark: dict) -> list[str]:
    """Return the primary eval-by-arm matrix."""
    arms = benchmark["arms"]
    baseline = benchmark.get("baseline")
    # baseline column first, then the rest in declared order (dict preserves it).
    names = ([baseline] if baseline in arms else []) + [n for n in arms if n != baseline]
    if not names:
        return []
    headers = [f"{name} ({arms[name].get('harness') or '?'})" for name in names]
    # Union of eval_ids across arms, stable.
    eval_ids: list[str] = []
    for name in names:
        for row in arms[name]["per_eval"]:
            if row["eval_id"] not in eval_ids:
                eval_ids.append(row["eval_id"])
    lines = [
        "## Matrix",
        "",
        "| Eval | " + " | ".join(headers) + " |",
        "|------|" + "|".join(["------"] * len(names)) + "|",
    ]
    for eid in eval_ids:
        ref_rate = (
            next(
                (r["pass_rate_mean"] for r in arms[baseline]["per_eval"] if r["eval_id"] == eid),
                None,
            )
            if baseline in arms
            else None
        )
        cells = []
        for n in names:
            row = next((r for r in arms[n]["per_eval"] if r["eval_id"] == eid), None)
            if row is None:
                cells.append("—")
            elif n == baseline or ref_rate is None:
                cells.append(f"{row['pass_rate_mean']:.0%}")
            else:
                cells.append(f"{(row['pass_rate_mean'] - ref_rate) * 100:+.0f}pp")
        lines.append(f"| {eid} | " + " | ".join(cells) + " |")
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
            rendered = ", ".join(f"{k}={v}" for k, v in stats["env"].items())
            lines.append(f"- Env: {rendered}")
        dm = stats["duration_ms_mean"]
        if dm is not None:
            sd = stats["duration_ms_stdev"]
            entry = f"- Time per sample: {dm / 1000:.1f}s task" + (
                f" ± {sd / 1000:.1f}s" if sd else ""
            )
            if stats["judge_ms_mean"] is not None:
                entry += f" + {stats['judge_ms_mean'] / 1000:.1f}s judge"
            lines.append(entry)
        tm = stats["tokens_mean"]
        if tm is not None:
            sd = stats["tokens_stdev"]
            lines.append(f"- Tokens per sample: {tm:,.0f}" + (f" ± {sd:,.0f}" if sd else ""))
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
    trigger = benchmark.get("trigger") or []
    if trigger:
        ok = sum(1 for row in trigger if row["as_expected"] == row["samples"])
        lines += [f"## Trigger routing — {ok}/{len(trigger)} queries as expected", ""]
        lines.append("| Query | Text | Expected | As expected |")
        lines.append("|-------|------|----------|-------------|")
        for row in trigger:
            expected = "fire" if row["should_trigger"] else "no fire"
            mark = " (xfail)" if row.get("xfail") else ""
            lines.append(
                f"| {row['slug']}{mark} | {_md_cell(row['query'])} "
                f"| {expected} | {row['as_expected']}/{row['samples']} |"
            )
        lines.append("")
    return "\n".join(lines)


def _inline_code(value: str) -> str:
    """Wrap text in Markdown code ticks without breaking embedded ticks."""
    longest_run = max((len(m.group(0)) for m in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest_run + 1)
    padding = " " if "`" in value else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def build_benchmark(
    eval_root: Path,
    label: str,
    *,
    baseline: str | None = None,
    arm_meta: dict | None = None,
) -> dict:
    """Build the machine-readable benchmark report object."""
    eval_dirs = sorted(d for d in eval_root.iterdir() if d.is_dir() and d.name.startswith("eval-"))
    # Arm names are arbitrary strings on disk: the per-eval subdirs ARE the arm names.
    # Require a graded sample before counting a dir as an arm, so a stray subdir
    # (__pycache__, an editor temp) never becomes an empty zero-sample arm.
    arm_names = sorted(
        {
            d.name
            for ed in eval_dirs
            for d in ed.iterdir()
            if d.is_dir() and any(d.glob("sample-*/grading.json"))
        }
    )
    arm_stats = {a: _arm_stats(eval_dirs, a) for a in arm_names}

    # A declared baseline that never landed on disk (e.g. its arm errored out) coerces to
    # None, so the report scores arms absolutely exactly as the run did, instead of
    # asserting an absent baseline.
    if baseline is not None and baseline not in arm_stats:
        baseline = None

    # Per-arm metadata (harness/model/effort/env/harness_args) is joined by arm name onto the
    # on-disk stats — it's the run config, not anything derivable from the artifacts.
    meta = arm_meta or {}
    for name, stats in arm_stats.items():
        metadata = meta.get(name, {})
        stats["harness"] = metadata.get("harness")
        stats["model"] = metadata.get("model")
        stats["effort"] = metadata.get("effort")
        stats["env"] = metadata.get("env", {})
        stats["harness_args"] = metadata.get("harness_args", [])
    # arm_stats is built from sorted(arm_names); reorder to the SET-DECLARED order
    # (arm_meta preserves it) so matrix columns follow the set, not the alphabet.
    if meta:
        arm_stats = {name: arm_stats[name] for name in meta if name in arm_stats} | {
            name: stats for name, stats in arm_stats.items() if name not in meta
        }

    # Δ is measured against the baseline arm — when one ran. Each non-baseline arm
    # carries its delta_pp + noise band; with no baseline, arms report absolute rates.
    ref_stats = arm_stats.get(baseline) if baseline is not None else None
    ref_rate = ref_stats["pass_rate"] if ref_stats is not None else None
    for name, stats in arm_stats.items():
        if name == baseline or ref_stats is None:
            continue
        if stats["pass_rate"] is not None and ref_rate is not None:
            stats["delta_pp"] = (stats["pass_rate"] - ref_rate) * 100
            stats["delta_noise_pp"] = delta_noise_pp(stats, ref_stats)

    # Observed --count N; per-eval `samples` may be smaller where samples errored.
    max_samples = max(
        (row["samples"] for stats in arm_stats.values() for row in stats["per_eval"]),
        default=0,
    )
    return {
        "format_version": 1,
        "label": label,
        "baseline": baseline,
        "max_samples": max_samples,
        "arms": arm_stats,
        "trigger": _trigger_rows(eval_root),
    }


def write_benchmark(
    eval_root: Path,
    label: str,
    *,
    baseline: str | None = None,
    arm_meta: dict | None = None,
) -> dict:
    """Write benchmark JSON and Markdown report artifacts."""
    benchmark = build_benchmark(eval_root, label, baseline=baseline, arm_meta=arm_meta)
    (eval_root / "benchmark.json").write_text(json.dumps(benchmark, indent=2) + "\n")
    (eval_root / "benchmark.md").write_text(_format_markdown(benchmark))
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
    trigger = benchmark.get("trigger") or []
    if trigger:
        ok = sum(1 for row in trigger if row["as_expected"] == row["samples"])
        parts.append(f"trigger {ok}/{len(trigger)}")
    return f"{skill}: " + "  |  ".join(parts) + f"  -> {benchmark_md}"
