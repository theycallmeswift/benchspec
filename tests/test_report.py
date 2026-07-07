"""Tests for report."""

import json

import pytest

from evalspec import report
from tests.support import seed_arm, seed_trigger


def test_redact_env_masks_secrets_keeps_urls() -> None:
    """Verify redact env masks secrets keeps urls."""
    out = report.redact_env(
        {
            "ANTHROPIC_BASE_URL": "https://o",
            "OPENROUTER_API_KEY": "sk-123",
            "AUTH_TOKEN": "t",
        }
    )

    assert out["ANTHROPIC_BASE_URL"] == "https://o"
    assert out["OPENROUTER_API_KEY"] == "***"
    assert out["AUTH_TOKEN"] == "***"


def test_build_benchmark_baseline_and_arm_meta(tmp_path: object) -> None:
    """Verify build benchmark baseline and arm meta."""
    seed_arm(tmp_path, "x", "baseline", passes=1, total=2)
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline="baseline",
        arm_meta={
            "baseline": {
                "harness": "claude-code",
                "model": "sonnet",
                "effort": "medium",
                "env": {},
            },
            "trial": {
                "harness": "opencode",
                "model": "x",
                "effort": "high",
                "env": {"K": "***"},
                "harness_args": ["--print-logs"],
            },
        },
    )

    assert bench["baseline"] == "baseline"
    assert bench["arms"]["trial"]["harness"] == "opencode"
    assert bench["arms"]["trial"]["model"] == "x"
    assert bench["arms"]["trial"]["effort"] == "high"
    assert bench["arms"]["trial"]["env"] == {"K": "***"}
    assert bench["arms"]["trial"]["harness_args"] == ["--print-logs"]
    assert bench["arms"]["baseline"]["harness_args"] == []
    assert "reference" not in bench  # renamed


def test_build_benchmark_arm_meta_drives_column_order(tmp_path: object) -> None:
    """Verify build benchmark arm meta drives column order."""
    # arm_stats is discovered alphabetically; arm_meta's declared order wins so the
    # matrix columns follow the set, not the alphabet.
    seed_arm(tmp_path, "x", "zeta", passes=1, total=2)
    seed_arm(tmp_path, "x", "alpha", passes=2, total=2)

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline="zeta",
        arm_meta={"zeta": {"harness": "claude-code"}, "alpha": {"harness": "opencode"}},
    )

    assert list(bench["arms"]) == ["zeta", "alpha"]


def test_format_markdown_renders_matrix_table(tmp_path: object) -> None:
    """Verify format markdown renders matrix table."""
    seed_arm(tmp_path, "x", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)  # 100% → +50pp

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline="baseline",
        arm_meta={
            "baseline": {"harness": "claude-code"},
            "trial": {"harness": "opencode"},
        },
    )

    md = report._format_markdown(bench)

    assert "## Matrix" in md
    assert "| Eval |" in md
    assert "baseline (claude-code)" in md
    assert "trial (opencode)" in md
    # baseline column shows the absolute rate; trial shows the ±pp delta.
    assert "50%" in md
    assert "+50pp" in md


def test_format_markdown_renders_harness_args(tmp_path: object) -> None:
    """Verify format markdown renders harness args."""
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline=None,
        arm_meta={
            "trial": {
                "harness": "claude-code",
                "model": "sonnet",
                "harness_args": ["--plugin-dir", "/project"],
            }
        },
    )

    md = report._format_markdown(bench)

    assert "- Harness args: `--plugin-dir` `/project`" in md


def test_format_markdown_quotes_harness_args_with_spaces_and_backticks(
    tmp_path: object,
) -> None:
    """Verify format markdown quotes harness args with spaces and backticks."""
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline=None,
        arm_meta={
            "trial": {
                "harness": "claude-code",
                "model": "sonnet",
                "harness_args": ["--label", "value with space", "value`withtick"],
            }
        },
    )

    md = report._format_markdown(bench)

    assert "- Harness args: `--label` `value with space` `` value`withtick ``" in md


def test_format_markdown_omits_empty_harness_args(tmp_path: object) -> None:
    """Verify format markdown omits empty harness args."""
    seed_arm(tmp_path, "x", "baseline", passes=2, total=2)

    bench = report.build_benchmark(
        tmp_path,
        "label",
        baseline=None,
        arm_meta={"baseline": {"harness": "claude-code", "model": "sonnet"}},
    )

    md = report._format_markdown(bench)

    assert "Harness args:" not in md


def test_build_benchmark_computes_pass_rates(tmp_path: object) -> None:
    """Verify build benchmark computes pass rates."""
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(it, "alpha", "baseline", passes=0, total=2)  # 0%

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline="baseline")

    assert bench["arms"]["trial"]["pass_rate"] == 1.0
    assert bench["arms"]["baseline"]["pass_rate"] == 0.0
    # Single sample → top-level max_samples == 1, per-eval samples == 1, stdev is None.
    assert bench["max_samples"] == 1
    trial_eval = bench["arms"]["trial"]["per_eval"][0]
    assert trial_eval["samples"] == 1
    assert trial_eval["pass_rate_mean"] == 1.0
    assert trial_eval["pass_rate_stdev"] is None
    assert trial_eval["passed_total"] == 2
    assert trial_eval["total_total"] == 2


def test_build_benchmark_computes_delta_vs_reference(tmp_path: object) -> None:
    """Verify build benchmark computes delta vs reference."""
    ed = tmp_path / "eval-x"
    seed_arm(tmp_path, "x", "baseline", passes=0, total=2)
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)

    bench = report.build_benchmark(tmp_path, "label", baseline="baseline")

    assert bench["baseline"] == "baseline"
    assert bench["arms"]["trial"]["delta_pp"] == pytest.approx(100.0)
    assert "delta_pp" not in bench["arms"]["baseline"]
    assert ed.is_dir()  # discovered the eval dir on disk


def test_build_benchmark_no_reference_absolute_only(tmp_path: object) -> None:
    """Verify build benchmark no reference absolute only."""
    seed_arm(tmp_path, "x", "trial-opus", passes=1, total=2)
    seed_arm(tmp_path, "x", "trial-sonnet", passes=2, total=2)

    bench = report.build_benchmark(tmp_path, "label", baseline=None)

    assert bench["baseline"] is None
    assert bench["arms"]["trial-opus"]["pass_rate"] == pytest.approx(0.5)
    assert "delta_pp" not in bench["arms"]["trial-opus"]
    assert "delta_pp" not in bench["arms"]["trial-sonnet"]


def test_build_benchmark_drops_reference_absent_from_disk(tmp_path: object) -> None:
    """Verify build benchmark drops reference absent from disk."""
    # A declared reference the run dropped (--skip-baseline / `baseline = false`) never
    # lands on disk: coerce reference to None and score the surviving arm absolutely,
    # instead of framing it against a baseline that never ran.
    seed_arm(tmp_path, "x", "trial", passes=2, total=2)

    bench = report.build_benchmark(tmp_path, "label", baseline="baseline")

    assert bench["baseline"] is None
    assert "delta_pp" not in bench["arms"]["trial"]
    assert bench["arms"]["trial"]["pass_rate"] == 1.0


def test_build_benchmark_discovers_arbitrary_arm_names(tmp_path: object) -> None:
    """Verify build benchmark discovers arbitrary arm names."""
    # Arm names are arbitrary strings on disk, discovered by walking the eval dir's
    # subdirs — not pinned to a with_skill/without_skill literal.
    seed_arm(tmp_path, "x", "claude-opus", passes=2, total=2)
    seed_arm(tmp_path, "x", "opencode-sonnet", passes=1, total=2)

    bench = report.build_benchmark(tmp_path, "label", baseline="claude-opus")

    assert set(bench["arms"]) == {"claude-opus", "opencode-sonnet"}


def test_delta_noise_pp_generalized(tmp_path: object) -> None:
    """Verify delta noise pp generalized."""
    # The generalized delta_noise_pp(arm_a, arm_b) takes two arm-stat dicts and returns
    # a noise band in pp — assert it's computed (and non-negative), so the Δ-noise
    # rewrite away from the with_skill/without_skill literals is verified.
    for s in range(4):
        seed_arm(tmp_path, "x", "baseline", passes=1, total=2, sample=s)
        seed_arm(tmp_path, "x", "trial", passes=2, total=2, sample=s)

    bench = report.build_benchmark(tmp_path, "label", baseline="baseline")

    assert bench["arms"]["trial"]["delta_noise_pp"] >= 0
    band = report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"])
    assert band is not None
    assert band >= 0


def test_errored_arm_excluded_from_stats(tmp_path: object) -> None:
    """Verify errored arm excluded from stats."""
    # An errored sample (timeout/crash) is an infra failure, not a measurement — it
    # must not drag the mean pass rate / duration down.
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2)  # 100%, real
    seed_arm(it, "beta", "trial", passes=0, total=2, errored=True)  # crashed

    stats = report.build_benchmark(it, label="run", baseline=None)["arms"]["trial"]

    assert stats["n"] == 1  # one (eval × sample) pair counts
    assert stats["pass_rate"] == 1.0  # the errored 0% is excluded


def test_write_benchmark_writes_files(tmp_path: object) -> None:
    """Verify write benchmark writes files."""
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=1, total=2)

    report.write_benchmark(it, label="iteration_01 · alpha", baseline=None)

    assert (it / "benchmark.json").is_file()
    assert (it / "benchmark.md").is_file()
    assert "Benchmark" in (it / "benchmark.md").read_text()
    assert "iteration_01 · alpha" in (it / "benchmark.md").read_text()


def test_markdown_headline_shows_delta_vs_reference(tmp_path: object) -> None:
    """Verify markdown headline shows delta vs reference."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "baseline", passes=0, total=2)

    report.write_benchmark(tmp_path, label="iteration_01 · demo", baseline="baseline")

    md = (tmp_path / "benchmark.md").read_text()
    assert md.splitlines()[0] == "# Benchmark — iteration_01 · demo"
    # Headline lists each non-reference arm's `baseline <ref%> → <arm> <pct%> (Δpp)`.
    assert "baseline 0%" in md
    assert "trial 100%" in md
    assert "+100pp" in md


def test_markdown_headline_absolute_when_no_reference(tmp_path: object) -> None:
    """Verify markdown headline absolute when no reference."""
    seed_arm(tmp_path, "alpha", "trial-opus", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "trial-sonnet", passes=1, total=2)

    report.write_benchmark(tmp_path, label="demo", baseline=None)

    md = (tmp_path / "benchmark.md").read_text()
    # No Δ when there's no reference — just per-arm absolute rates.
    assert "pp" not in md.splitlines()[2]
    assert "trial-opus" in md
    assert "trial-sonnet" in md


def test_markdown_headline_reference_only_shows_its_rate(tmp_path: object) -> None:
    """Verify markdown headline reference only shows its rate."""
    # With the reference as the sole arm on disk, the headline shows its own rate.
    seed_arm(tmp_path, "alpha", "baseline", passes=1, total=2)

    report.write_benchmark(tmp_path, label="demo", baseline="baseline")

    headline = (tmp_path / "benchmark.md").read_text().splitlines()[2]
    assert "baseline" in headline
    assert "50%" in headline


def test_delta_line_reference_only_shows_its_rate(tmp_path: object) -> None:
    """Verify delta line reference only shows its rate."""
    bench = {
        "baseline": "baseline",
        "arms": {"baseline": {"pass_rate": 0.5}},
    }

    line = report.delta_line("archive", bench, tmp_path / "benchmark.md")

    assert "baseline 50%" in line


def test_errored_samples_surface_instead_of_vanishing(tmp_path: object) -> None:
    """Verify errored samples surface instead of vanishing."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2, sample=0)
    seed_arm(tmp_path, "alpha", "trial", passes=0, total=2, sample=1, errored=True)

    bench = report.write_benchmark(tmp_path, label="iteration_01 · demo", baseline=None)

    assert bench["arms"]["trial"]["errored_samples"] == 1
    assert bench["arms"]["trial"]["pass_rate"] == 1.0  # still excluded from rates
    md = (tmp_path / "benchmark.md").read_text()
    assert "1 sample(s) excluded" in md


def test_delta_line_shows_delta(tmp_path: object) -> None:
    """Verify delta line shows delta."""
    bench = {
        "baseline": "baseline",
        "arms": {
            "trial": {"pass_rate": 0.9},
            "baseline": {"pass_rate": 0.4},
        },
    }

    line = report.delta_line("archive", bench, tmp_path / "benchmark.md")

    assert "archive" in line
    assert "90%" in line
    assert "40%" in line
    assert "+50pp" in line


def test_delta_line_handles_missing_arm(tmp_path: object) -> None:
    """Verify delta line handles missing arm."""
    bench = {
        "baseline": "baseline",
        "arms": {
            "trial": {"pass_rate": 0.8},
            "baseline": {"pass_rate": None},
        },
    }

    line = report.delta_line("archive", bench, tmp_path / "benchmark.md")

    assert "n/a" in line
    assert "pp" not in line  # no delta when the reference arm is missing


def test_delta_line_absolute_when_no_reference(tmp_path: object) -> None:
    """Verify delta line absolute when no reference."""
    bench = {
        "baseline": None,
        "arms": {
            "trial-opus": {"pass_rate": 0.8},
            "trial-sonnet": {"pass_rate": 0.6},
        },
    }

    line = report.delta_line("archive", bench, tmp_path / "benchmark.md")

    assert "80%" in line
    assert "60%" in line
    assert "pp" not in line  # no delta without a reference


def test_multi_sample_stable_zero_stdev(tmp_path: object) -> None:
    """Verify multi sample stable zero stdev."""
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(it, "alpha", "trial", passes=2, total=2, sample=1)  # 100%

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline=None)

    assert bench["max_samples"] == 2
    stats = bench["arms"]["trial"]
    assert stats["n"] == 2
    assert stats["pass_rate"] == 1.0
    assert stats["pass_rate_stdev"] == 0.0
    row = stats["per_eval"][0]
    assert row["samples"] == 2
    assert row["pass_rate_mean"] == 1.0
    assert row["pass_rate_stdev"] == 0.0
    assert row["passed_total"] == 4  # 2 + 2 summed across samples
    assert row["total_total"] == 4


def test_multi_sample_flaky_nonzero_stdev(tmp_path: object) -> None:
    """Verify multi sample flaky nonzero stdev."""
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(it, "alpha", "trial", passes=0, total=2, sample=1)  # 0%

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline=None)

    stats = bench["arms"]["trial"]
    row = stats["per_eval"][0]
    assert row["samples"] == 2
    assert row["pass_rate_mean"] == 0.5
    assert row["pass_rate_stdev"] is not None
    assert row["pass_rate_stdev"] > 0
    assert row["passed_total"] == 2
    assert row["total_total"] == 4

    report.write_benchmark(it, label="iteration_01 · alpha", baseline=None)

    md = (it / "benchmark.md").read_text()
    # Flakiness column shows ±X% when samples disagree.
    assert "±" in md


def test_multi_sample_with_errored_sample_excluded(tmp_path: object) -> None:
    """Verify multi sample with errored sample excluded."""
    # One errored sample shouldn't drag the mean/stdev for the other.
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(it, "alpha", "trial", passes=0, total=2, sample=1, errored=True)  # excluded

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline=None)

    stats = bench["arms"]["trial"]
    row = stats["per_eval"][0]
    assert row["samples"] == 1  # only the non-errored sample counts
    assert row["pass_rate_mean"] == 1.0
    assert row["pass_rate_stdev"] is None  # n=1 → no stdev
    assert stats["n"] == 1


def test_sample_dirs_sorted_numerically(tmp_path: object) -> None:
    """Verify sample dirs sorted numerically."""
    # sample-10 sorts before sample-2 lexicographically — the report must use a
    # numeric key for any user-visible ordering.
    it = tmp_path / "iteration-1"
    it.mkdir()
    for k in (0, 2, 10):
        seed_arm(it, "alpha", "trial", passes=k, total=10, sample=k)

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline=None)

    row = bench["arms"]["trial"]["per_eval"][0]
    assert row["samples"] == 3
    assert row["passed_total"] == 12  # 0 + 2 + 10 regardless of glob order


def test_sample_dirs_skips_non_numeric_siblings(tmp_path: object) -> None:
    """Verify sample dirs skips non numeric siblings."""
    # A stray sibling whose suffix isn't a clean integer (e.g., a manual
    # `cp -r sample-0 sample-0bak`) must be skipped, not crash the int() parse.
    it = tmp_path / "iteration-1"
    it.mkdir()
    seed_arm(it, "alpha", "trial", passes=2, total=2, sample=0)
    # Forge a malformed sibling alongside the real sample-0/.
    bad = it / "eval-alpha" / "trial" / "sample-0bak"
    bad.mkdir()
    (bad / "grading.json").write_text("{}")  # would crash if parsed

    bench = report.build_benchmark(it, label="iteration_01 · alpha", baseline=None)

    row = bench["arms"]["trial"]["per_eval"][0]
    assert row["samples"] == 1  # only the well-formed sample counted


def test_trigger_rows_aggregate_per_query(tmp_path: object) -> None:
    """Verify trigger rows aggregate per query."""
    seed_trigger(tmp_path, 1, should_trigger=True, fires=2)  # fired as expected
    seed_trigger(tmp_path, 2, should_trigger=False, fires=1)  # fired but shouldn't

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)

    rows = bench["trigger"]
    assert rows[0]["slug"] == "q1"
    assert rows[0]["should_trigger"] is True
    assert rows[0]["samples"] == 1
    assert rows[0]["as_expected"] == 1
    assert rows[1]["slug"] == "q2"
    assert rows[1]["should_trigger"] is False
    assert rows[1]["samples"] == 1
    assert rows[1]["as_expected"] == 0
    md = report._format_markdown(bench)
    assert "## Trigger routing — 1/2 queries as expected" in md


def test_benchmark_carries_format_version(tmp_path: object) -> None:
    """Verify benchmark carries format version."""
    seed_arm(tmp_path, "alpha", "trial", passes=1, total=1)

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)

    assert bench["format_version"] == 1


def test_trigger_rows_read_persisted_verdict_and_query(tmp_path: object) -> None:
    """Verify trigger rows read persisted verdict and query."""
    seed_trigger(tmp_path, 1, should_trigger=True, fires=2, query="archive this note")
    seed_trigger(tmp_path, 2, should_trigger=False, fires=1, query="what is PARA?")

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)

    assert bench["trigger"][0]["query"] == "archive this note"
    assert bench["trigger"][0]["as_expected"] == 1
    assert bench["trigger"][1]["as_expected"] == 0
    md = report._format_markdown(bench)
    assert "archive this note" in md


def test_trigger_md_table_escapes_pipes_and_truncates(tmp_path: object) -> None:
    """Verify trigger md table escapes pipes and truncates."""
    seed_trigger(tmp_path, 1, should_trigger=True, fires=1, query="a | b " + "x" * 80)

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)
    md = report._format_markdown(bench)

    assert "a \\| b" in md  # cell-safe
    assert "x" * 80 not in md  # truncated


def test_trigger_xfail_marked_in_report(tmp_path: object) -> None:
    """Verify trigger xfail marked in report."""
    seed_trigger(
        tmp_path,
        3,
        should_trigger=True,
        fires=0,
        query="boundary query",
        xfail={"models": ["sonnet"], "reason": "documented routing boundary"},
    )

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)

    assert bench["trigger"][0]["xfail"] == {
        "models": ["sonnet"],
        "reason": "documented routing boundary",
    }
    assert "(xfail)" in report._format_markdown(bench)


def test_index_rows_flatten_evals_and_triggers(tmp_path: object) -> None:
    """Verify index rows flatten evals and triggers."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "baseline", passes=1, total=2)
    seed_arm(tmp_path, "alpha", "trial", passes=0, total=2, sample=1, errored=True)
    seed_trigger(tmp_path, 1, should_trigger=True, fires=2, query="route me")

    rows = report.index_rows(tmp_path, "demo")

    evals = [r for r in rows if r["kind"] == "eval"]
    triggers = [r for r in rows if r["kind"] == "trigger"]
    assert len(evals) == 3
    assert len(triggers) == 1
    first = next(r for r in evals if r["arm"] == "trial" and r["sample"] == 0)
    assert first == {
        "skill": "demo",
        "kind": "eval",
        "eval_id": "alpha",
        "arm": "trial",
        "sample": 0,
        "errored": False,
        "passed": 2,
        "total": 2,
        "duration_ms": 1000,
        "judge_ms": 200,
        "total_tokens": 500,
        "input_tokens": 300,
        "output_tokens": 100,
    }
    errored = next(r for r in evals if r["sample"] == 1)
    assert errored["errored"] is True
    assert triggers[0]["slug"] == "q1"
    assert triggers[0]["passed"] is True
    assert triggers[0]["skill"] == "demo"


def test_index_rows_discover_arbitrary_arm_names(tmp_path: object) -> None:
    """Verify index rows discover arbitrary arm names."""
    # index_rows iterates discovered arm names, not the retired _ARMS literal.
    seed_arm(tmp_path, "alpha", "claude-opus", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "opencode-sonnet", passes=1, total=2)

    rows = report.index_rows(tmp_path, "demo")

    assert {r["arm"] for r in rows if r["kind"] == "eval"} == {
        "claude-opus",
        "opencode-sonnet",
    }


def test_noise_band_computed_from_arm_stdevs(tmp_path: object) -> None:
    """Verify noise band computed from arm stdevs."""
    for s, passes in enumerate((2, 0, 2)):  # trial: 100%, 0%, 100% → noisy
        seed_arm(tmp_path, "alpha", "trial", passes=passes, total=2, sample=s)
    for s in range(3):
        seed_arm(tmp_path, "alpha", "baseline", passes=1, total=2, sample=s)

    bench = report.build_benchmark(tmp_path, label="x", baseline="baseline")
    band = report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"])

    assert band is not None
    assert band > 0


def test_noise_band_none_for_single_sample(tmp_path: object) -> None:
    """Verify noise band none for single sample."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "baseline", passes=0, total=2)

    bench = report.build_benchmark(tmp_path, label="x", baseline="baseline")

    assert report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"]) is None


def test_within_noise_label_in_markdown_and_delta_line(tmp_path: object) -> None:
    """Verify within noise label in markdown and delta line."""
    # delta +17pp, but arms this scattered have SE > 17pp → labeled.
    for s, passes in enumerate((2, 0, 1)):
        seed_arm(tmp_path, "alpha", "trial", passes=passes, total=2, sample=s)
    for s, passes in enumerate((0, 1, 1)):
        seed_arm(tmp_path, "alpha", "baseline", passes=passes, total=2, sample=s)

    bench = report.write_benchmark(tmp_path, label="x", baseline="baseline")
    md = (tmp_path / "benchmark.md").read_text()

    assert "within noise" in md
    assert "within noise" in report.delta_line("demo", bench, tmp_path / "benchmark.md")


def test_as_expected_counts_xfail_miss_as_expected() -> None:
    """Verify as expected counts xfail miss as expected."""
    # An xfail query that missed on its listed tier (the documented behavior) is "as expected".
    assert (
        report._as_expected(
            {
                "passed": False,
                "model": "sonnet",
                "xfail": {"models": ["sonnet"], "reason": "known sonnet miss"},
            }
        )
        is True
    )
    # An xfail query that fired anyway (XPASS) on its listed tier is also non-failing.
    assert (
        report._as_expected(
            {
                "passed": True,
                "model": "sonnet",
                "xfail": {"models": ["sonnet"], "reason": "known sonnet miss"},
            }
        )
        is True
    )
    # A non-xfail query is as-expected only when it passed.
    assert report._as_expected({"passed": False, "model": "opus"}) is False
    assert report._as_expected({"passed": True, "model": "opus"}) is True


def _write_trigger_sample(skill_dir: object, slug: object, rec: object) -> None:
    """Write trigger sample."""
    sd = skill_dir / f"trigger-{slug}" / "sample-0"
    sd.mkdir(parents=True)
    (sd / "timing.json").write_text(json.dumps(rec), encoding="utf-8")


def test_as_expected_xfail_credited_only_on_listed_tier(tmp_path: object) -> None:
    """Verify as expected xfail credited only on listed tier."""
    # A documented sonnet miss, run on sonnet: missed but as-expected (green).
    on_tier = {
        "slug": "x",
        "query": "q",
        "should_trigger": True,
        "passed": False,
        "model": "sonnet",
        "xfail": {"models": ["sonnet"], "reason": "r"},
    }
    assert report._as_expected(on_tier) is True
    # Same query run on opus (not in models): a miss is NOT credited — it's a fail.
    off_tier = {**on_tier, "model": "opus", "passed": False}
    assert report._as_expected(off_tier) is False
    # Off-tier but actually passed: as-expected via passed.
    off_tier_pass = {**on_tier, "model": "opus", "passed": True}
    assert report._as_expected(off_tier_pass) is True


def test_trigger_rows_use_slug(tmp_path: object) -> None:
    """Verify trigger rows use slug."""
    skill_dir = tmp_path / "ingest"
    _write_trigger_sample(
        skill_dir,
        "ingest-article",
        {
            "slug": "ingest-article",
            "query": "ingest this",
            "should_trigger": True,
            "passed": True,
            "model": "opus",
            "fires": 2,
            "threshold": 2,
        },
    )
    rows = report._trigger_rows(skill_dir)
    assert rows[0]["slug"] == "ingest-article"
    assert rows[0]["as_expected"] == 1


def test_trigger_rows_counts_xfail_miss_as_expected(tmp_path: object) -> None:
    """Verify trigger rows counts xfail miss as expected."""
    # An xfail query whose sample missed (passed=False) must count as as_expected
    # in _trigger_rows. If the call site used t["passed"] instead of _as_expected(t)
    # this assertion would fail: as_expected would be 0, not 1.
    seed_trigger(
        tmp_path,
        1,
        should_trigger=True,
        fires=0,
        xfail={"models": ["sonnet"], "reason": "documented sonnet routing miss"},
    )

    bench = report.build_benchmark(tmp_path, label="x", baseline=None)

    row = bench["trigger"][0]
    assert row["xfail"] == {
        "models": ["sonnet"],
        "reason": "documented sonnet routing miss",
    }
    assert row["samples"] == 1
    # The miss is documented — it must be counted as expected, not a failure.
    assert row["as_expected"] == 1
