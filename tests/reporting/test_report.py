"""Tests for report."""

import pytest

from harnessbench.reporting import report
from tests.support import seed_arm


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
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
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
                "model": "test-model",
                "effort": "high",
                "env": {"K": "***"},
                "harness_args": ["--print-logs"],
            },
        },
    )

    assert bench["baseline"] == "baseline"
    assert bench["arms"]["trial"]["harness"] == "opencode"
    assert bench["arms"]["trial"]["model"] == "test-model"
    assert bench["arms"]["trial"]["effort"] == "high"
    assert bench["arms"]["trial"]["env"] == {"K": "***"}
    assert bench["arms"]["trial"]["harness_args"] == ["--print-logs"]
    assert bench["arms"]["baseline"]["harness_args"] == []
    assert "reference" not in bench  # renamed


def test_build_benchmark_arm_meta_drives_column_order(tmp_path: object) -> None:
    """Verify build benchmark arm meta drives column order."""
    # arm_stats is discovered alphabetically; arm_meta's declared order wins so the
    # matrix columns follow the set, not the alphabet.
    seed_arm(tmp_path / "archive", "alpha", "zeta", passes=1, total=2)
    seed_arm(tmp_path / "archive", "alpha", "alpha", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        "label",
        baseline="zeta",
        arm_meta={"zeta": {"harness": "claude-code"}, "alpha": {"harness": "opencode"}},
    )

    assert list(bench["arms"]) == ["zeta", "alpha"]


def test_format_markdown_renders_matrix_table(tmp_path: object) -> None:
    """Verify format markdown renders matrix table."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100% → +50pp

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
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
    # Rows are keyed group/eval_id; the baseline cell shows the absolute rate, the trial
    # cell shows the absolute rate AND the ±pp delta together.
    assert "archive/alpha" in md
    assert "50%" in md
    assert "100% (+50pp)" in md


def test_format_markdown_renders_harness_args(tmp_path: object) -> None:
    """Verify format markdown renders harness args."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
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
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
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
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        "label",
        baseline=None,
        arm_meta={"baseline": {"harness": "claude-code", "model": "sonnet"}},
    )

    md = report._format_markdown(bench)

    assert "Harness args:" not in md


def test_build_benchmark_computes_pass_rates(tmp_path: object) -> None:
    """Verify build benchmark computes pass rates."""
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(skills_root / "archive", "alpha", "baseline", passes=0, total=2)  # 0%

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline="baseline"
    )

    assert bench["arms"]["trial"]["pass_rate"] == 1.0
    assert bench["arms"]["baseline"]["pass_rate"] == 0.0
    # Single sample → top-level max_samples == 1, per-eval samples == 1, stdev is None.
    assert bench["max_samples"] == 1
    trial_eval = bench["arms"]["trial"]["per_eval"][0]
    assert trial_eval["group"] == "archive"
    assert trial_eval["samples"] == 1
    assert trial_eval["pass_rate_mean"] == 1.0
    assert trial_eval["pass_rate_stdev"] is None
    assert trial_eval["passed_total"] == 2
    assert trial_eval["total_total"] == 2


def test_build_benchmark_computes_delta_vs_reference(tmp_path: object) -> None:
    """Verify build benchmark computes delta vs reference."""
    eval_dir = tmp_path / "archive" / "eval-alpha"
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=0, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), "label", baseline="baseline"
    )

    assert bench["baseline"] == "baseline"
    assert bench["arms"]["trial"]["delta_pp"] == pytest.approx(100.0)
    assert "delta_pp" not in bench["arms"]["baseline"]
    assert eval_dir.is_dir()  # discovered the eval dir on disk


def test_build_benchmark_no_reference_absolute_only(tmp_path: object) -> None:
    """Verify build benchmark no reference absolute only."""
    seed_arm(tmp_path / "archive", "alpha", "trial-opus", passes=1, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial-sonnet", passes=2, total=2)

    bench = report.build_benchmark(report.discover_eval_dirs(tmp_path), "label", baseline=None)

    assert bench["baseline"] is None
    assert bench["arms"]["trial-opus"]["pass_rate"] == pytest.approx(0.5)
    assert "delta_pp" not in bench["arms"]["trial-opus"]
    assert "delta_pp" not in bench["arms"]["trial-sonnet"]


def test_build_benchmark_drops_reference_absent_from_disk(tmp_path: object) -> None:
    """Verify build benchmark drops reference absent from disk."""
    # A declared reference the run dropped (--skip-baseline / `baseline = false`) never
    # lands on disk: coerce reference to None and score the surviving arm absolutely,
    # instead of framing it against a baseline that never ran.
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), "label", baseline="baseline"
    )

    assert bench["baseline"] is None
    assert "delta_pp" not in bench["arms"]["trial"]
    assert bench["arms"]["trial"]["pass_rate"] == 1.0


def test_build_benchmark_discovers_arbitrary_arm_names(tmp_path: object) -> None:
    """Verify build benchmark discovers arbitrary arm names."""
    # Arm names are arbitrary strings on disk, discovered by walking the eval dir's
    # subdirs — not pinned to a with_skill/without_skill literal.
    seed_arm(tmp_path / "archive", "alpha", "claude-opus", passes=2, total=2)
    seed_arm(tmp_path / "archive", "alpha", "opencode-sonnet", passes=1, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), "label", baseline="claude-opus"
    )

    assert set(bench["arms"]) == {"claude-opus", "opencode-sonnet"}


def test_delta_noise_pp_generalized(tmp_path: object) -> None:
    """Verify delta noise pp generalized."""
    # The generalized delta_noise_pp(arm_a, arm_b) takes two arm-stat dicts and returns
    # a noise band in pp — assert it's computed (and non-negative), so the Δ-noise
    # rewrite away from the with_skill/without_skill literals is verified.
    for sample_index in range(4):
        seed_arm(tmp_path / "archive", "eval", "baseline", passes=1, total=2, sample=sample_index)
        seed_arm(tmp_path / "archive", "eval", "trial", passes=2, total=2, sample=sample_index)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), "label", baseline="baseline"
    )

    assert bench["arms"]["trial"]["delta_noise_pp"] >= 0
    band = report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"])
    assert band is not None
    assert band >= 0


def test_errored_arm_excluded_from_stats(tmp_path: object) -> None:
    """Verify errored arm excluded from stats."""
    # An errored sample (timeout/crash) is an infra failure, not a measurement — it
    # must not drag the mean pass rate / duration down.
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2)  # 100%, real
    seed_arm(skills_root / "archive", "beta", "trial", passes=0, total=2, errored=True)  # crashed

    stats = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="run", baseline=None
    )["arms"]["trial"]

    assert stats["n"] == 1  # one (eval × sample) pair counts
    assert stats["pass_rate"] == 1.0  # the errored 0% is excluded


def test_arm_stats_sums_binder_degraded_across_samples(tmp_path: object) -> None:
    """Verify _arm_stats sums binder_degraded across every sample in the arm."""
    eval_root = tmp_path / "archive"
    seed_arm(eval_root, "alpha", "trial", passes=1, total=1, sample=0, binder_degraded=2)
    seed_arm(eval_root, "alpha", "trial", passes=1, total=1, sample=1, binder_degraded=1)

    stats = report._arm_stats([eval_root / "eval-alpha"], "trial")

    assert stats["binder_degraded"] == 3
    assert stats["per_eval"][0]["group"] == "archive"


def test_write_benchmark_writes_files(tmp_path: object) -> None:
    """Verify write benchmark writes files to the out_dir."""
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=1, total=2)

    report.write_benchmark(
        skills_root, report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

    assert (skills_root / "benchmark.json").is_file()
    assert (skills_root / "benchmark.md").is_file()
    assert "Benchmark" in (skills_root / "benchmark.md").read_text()
    assert "iteration_01" in (skills_root / "benchmark.md").read_text()


def test_markdown_headline_shows_delta_vs_reference(tmp_path: object) -> None:
    """Verify markdown headline shows delta vs reference."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=0, total=2)

    report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    md = (tmp_path / "benchmark.md").read_text()
    assert md.splitlines()[0] == "# Benchmark — iteration_01"
    # Headline lists each non-baseline arm's `baseline <ref%> → <arm> <pct%> (Δpp)`.
    assert "baseline 0%" in md
    assert "trial 100%" in md
    assert "+100pp" in md


def test_markdown_headline_absolute_when_no_reference(tmp_path: object) -> None:
    """Verify markdown headline absolute when no reference."""
    seed_arm(tmp_path / "archive", "alpha", "trial-opus", passes=2, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial-sonnet", passes=1, total=2)

    report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="demo", baseline=None
    )

    md = (tmp_path / "benchmark.md").read_text()
    # No Δ when there's no reference — just per-arm absolute rates.
    assert "pp" not in md.splitlines()[2]
    assert "trial-opus" in md
    assert "trial-sonnet" in md


def test_markdown_headline_reference_only_shows_its_rate(tmp_path: object) -> None:
    """Verify markdown headline reference only shows its rate."""
    # With the reference as the sole arm on disk, the headline shows its own rate.
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)

    report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="demo", baseline="baseline"
    )

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
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2, sample=0)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=0, total=2, sample=1, errored=True)

    bench = report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="iteration_01", baseline=None
    )

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
    assert "pp" not in line  # no delta when the baseline arm is missing


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
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2, sample=1)  # 100%

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

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
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(skills_root / "archive", "alpha", "trial", passes=0, total=2, sample=1)  # 0%

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

    stats = bench["arms"]["trial"]
    row = stats["per_eval"][0]
    assert row["samples"] == 2
    assert row["pass_rate_mean"] == 0.5
    assert row["pass_rate_stdev"] is not None
    assert row["pass_rate_stdev"] > 0
    assert row["passed_total"] == 2
    assert row["total_total"] == 4

    report.write_benchmark(
        skills_root, report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

    md = (skills_root / "benchmark.md").read_text()
    # Flakiness column shows ±X% when samples disagree.
    assert "±" in md


def test_multi_sample_with_errored_sample_excluded(tmp_path: object) -> None:
    """Verify multi sample with errored sample excluded."""
    # One errored sample shouldn't drag the mean/stdev for the other.
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2, sample=0)  # 100%
    seed_arm(
        skills_root / "archive", "alpha", "trial", passes=0, total=2, sample=1, errored=True
    )  # excluded

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

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
    skills_root = tmp_path / "iteration-1"
    for sample_index in (0, 2, 10):
        seed_arm(
            skills_root / "archive",
            "alpha",
            "trial",
            passes=sample_index,
            total=10,
            sample=sample_index,
        )

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

    row = bench["arms"]["trial"]["per_eval"][0]
    assert row["samples"] == 3
    assert row["passed_total"] == 12  # 0 + 2 + 10 regardless of glob order


def test_sample_dirs_skips_non_numeric_siblings(tmp_path: object) -> None:
    """Verify sample dirs skips non numeric siblings."""
    # A stray sibling whose suffix isn't a clean integer (e.g., a manual
    # `cp -r sample-0 sample-0bak`) must be skipped, not crash the int() parse.
    skills_root = tmp_path / "iteration-1"
    seed_arm(skills_root / "archive", "alpha", "trial", passes=2, total=2, sample=0)
    # Forge a malformed sibling alongside the real sample-0/.
    bad = skills_root / "archive" / "eval-alpha" / "trial" / "sample-0bak"
    bad.mkdir()
    (bad / "grading.json").write_text("{}")  # would crash if parsed

    bench = report.build_benchmark(
        report.discover_eval_dirs(skills_root), label="iteration_01", baseline=None
    )

    row = bench["arms"]["trial"]["per_eval"][0]
    assert row["samples"] == 1  # only the well-formed sample counted


def test_benchmark_carries_format_version(tmp_path: object) -> None:
    """Verify benchmark carries format version."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=1, total=1)

    bench = report.build_benchmark(report.discover_eval_dirs(tmp_path), label="demo", baseline=None)

    assert bench["format_version"] == 3


def test_index_rows_flatten_evals(tmp_path: object) -> None:
    """Verify index rows flatten eval samples."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "baseline", passes=1, total=2)
    seed_arm(tmp_path, "alpha", "trial", passes=0, total=2, sample=1, errored=True)

    rows = report.index_rows(tmp_path, "demo")

    evals = [row for row in rows if row["kind"] == "eval"]
    assert len(evals) == 3
    first = next(row for row in evals if row["arm"] == "trial" and row["sample"] == 0)
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
    errored = next(row for row in evals if row["sample"] == 1)
    assert errored["errored"] is True


def test_index_rows_discover_arbitrary_arm_names(tmp_path: object) -> None:
    """Verify index rows discover arbitrary arm names."""
    # index_rows iterates discovered arm names, not the retired _ARMS literal.
    seed_arm(tmp_path, "alpha", "claude-opus", passes=2, total=2)
    seed_arm(tmp_path, "alpha", "opencode-sonnet", passes=1, total=2)

    rows = report.index_rows(tmp_path, "demo")

    assert {row["arm"] for row in rows if row["kind"] == "eval"} == {
        "claude-opus",
        "opencode-sonnet",
    }


def test_noise_band_computed_from_arm_stdevs(tmp_path: object) -> None:
    """Verify noise band computed from arm stdevs."""
    archive = tmp_path / "archive"
    for sample_index, passes in enumerate((2, 0, 2)):  # trial: 100%, 0%, 100% → noisy
        seed_arm(archive, "alpha", "trial", passes=passes, total=2, sample=sample_index)
    for sample_index in range(3):
        seed_arm(archive, "alpha", "baseline", passes=1, total=2, sample=sample_index)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="alpha", baseline="baseline"
    )
    band = report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"])

    assert band is not None
    assert band > 0


def test_noise_band_none_for_single_sample(tmp_path: object) -> None:
    """Verify noise band none for single sample."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=0, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="alpha", baseline="baseline"
    )

    assert report.delta_noise_pp(bench["arms"]["trial"], bench["arms"]["baseline"]) is None


def test_within_noise_label_in_markdown_and_delta_line(tmp_path: object) -> None:
    """Verify within noise label in markdown and delta line."""
    # delta +17pp, but arms this scattered have SE > 17pp → labeled.
    archive = tmp_path / "archive"
    for sample_index, passes in enumerate((2, 0, 1)):
        seed_arm(archive, "alpha", "trial", passes=passes, total=2, sample=sample_index)
    for sample_index, passes in enumerate((0, 1, 1)):
        seed_arm(archive, "alpha", "baseline", passes=passes, total=2, sample=sample_index)

    bench = report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="alpha", baseline="baseline"
    )
    md = (tmp_path / "benchmark.md").read_text()

    assert "within noise" in md
    assert "within noise" in report.delta_line("demo", bench, tmp_path / "benchmark.md")


def test_matrix_row_from_roster_for_all_errored_eval(tmp_path: object) -> None:
    """Verify an all-errored eval still gets a roster row with a — cell."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path / "archive", "ghost", "trial", passes=0, total=2, errored=True)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline=None
    )
    md = report._format_markdown(bench)

    # The all-errored eval has no per_eval row in any arm, so a per_eval-union matrix
    # would drop it — the roster keeps it with a — cell.
    assert "| archive/ghost | — |" in md


def test_matrix_column_present_for_wholly_missing_arm(tmp_path: object) -> None:
    """Verify an arm configured but absent from disk gets a — column and footer."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        label="iteration_01",
        baseline="baseline",
        arm_meta={
            "baseline": {"harness": "claude-code"},
            "trial": {"harness": "opencode"},
        },
    )
    md = report._format_markdown(bench)

    assert "trial (opencode)" in md
    assert bench["arms"]["trial"]["pass_rate"] is None
    assert "| archive/alpha | 50% | — |" in md
    assert "| All evals | 50% | — |" in md


def test_matrix_distinguishes_same_eval_id_across_groups(tmp_path: object) -> None:
    """Verify the same eval_id in two groups renders two distinct group/eval rows."""
    seed_arm(tmp_path / "archive", "summary", "trial", passes=2, total=2)
    seed_arm(tmp_path / "ingest", "summary", "trial", passes=1, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline=None
    )
    md = report._format_markdown(bench)

    assert "| archive/summary |" in md
    assert "| ingest/summary |" in md


def test_matrix_all_evals_footer_equals_arm_headline_single_group(tmp_path: object) -> None:
    """Verify the All evals footer equals each arm's headline pass_rate (single group)."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "archive", "beta", "baseline", passes=2, total=2)  # 100%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(tmp_path / "archive", "beta", "trial", passes=2, total=2)  # 100%

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )
    md = report._format_markdown(bench)

    baseline_rate = bench["arms"]["baseline"]["pass_rate"]  # pooled (0.5 + 1.0)/2 = 0.75
    trial_rate = bench["arms"]["trial"]["pass_rate"]  # 1.0
    trial_delta_pp = bench["arms"]["trial"]["delta_pp"]  # +25pp
    assert (
        f"| All evals | {baseline_rate:.0%} | {trial_rate:.0%} ({trial_delta_pp:+.0f}pp) |" in md
    )


def test_matrix_all_evals_footer_is_run_level_pooled_mean_multi_group(tmp_path: object) -> None:
    """Verify the All evals footer baseline is the run-level pooled mean across groups."""
    # archive contributes two baseline samples (100% and 0%); ingest contributes one (50%).
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=2, total=2, sample=0)
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=0, total=2, sample=1)
    seed_arm(tmp_path / "ingest", "beta", "baseline", passes=1, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )
    md = report._format_markdown(bench)

    # Pooled over every surviving (eval×sample) rate: (1.0 + 0.0 + 0.5) / 3 = 0.5.
    assert bench["arms"]["baseline"]["pass_rate"] == 0.5
    assert "| All evals | 50% |" in md


def test_matrix_non_baseline_cell_shows_rate_and_delta(tmp_path: object) -> None:
    """Verify a non-baseline cell shows its rate and the ±pp delta vs the baseline."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100%

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )
    md = report._format_markdown(bench)

    assert "| archive/alpha | 50% | 100% (+50pp) |" in md
    assert "| All evals | 50% | 100% (+50pp) |" in md


def test_matrix_non_baseline_cell_absolute_without_baseline(tmp_path: object) -> None:
    """Verify a no-baseline build shows each arm's absolute rate with no delta."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100%

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline=None
    )
    md = report._format_markdown(bench)

    assert "| archive/alpha | 50% | 100% |" in md
    assert "| All evals | 50% | 100% |" in md


def test_benchmark_v3_carries_planned_observed_runner_binder(tmp_path: object) -> None:
    """Verify v3 carries the planned/observed split plus runner and binder identity."""
    # `trial` ran and has observed runtime provenance; `absent` is a configured column
    # that never ran, so it stays a valid empty matrix column with NO observed entry —
    # provenance is never fabricated for it.
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        label="iteration_01",
        baseline=None,
        arm_meta={"trial": {"harness": "claude-code"}, "absent": {"harness": "claude-code"}},
        planned=[
            {"name": "trial", "harness": "claude-code", "model": "opus", "effort": "medium"},
            {"name": "absent", "harness": "claude-code", "model": "sonnet", "effort": "low"},
        ],
        observed_arms={"trial": {"actual_version": "1.2.3", "sandbox": {"snapshot": "snap-x"}}},
        runner="pytest",
        binder={"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    )

    assert bench["format_version"] == 3
    assert bench["runner"] == "pytest"
    assert bench["binder"]["provider"] == "gemini"
    assert {arm["name"] for arm in bench["planned_arms"]} == {"trial", "absent"}
    # Configured-but-unobserved arm: present as a matrix column, absent from observed.
    assert "absent" in bench["arms"]
    assert set(bench["observed_arms"]) == {"trial"}


def test_markdown_provenance_labels_unobserved_arm(tmp_path: object) -> None:
    """Verify the Provenance section shows observed identity and labels unobserved arms."""
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        label="iteration_01",
        baseline=None,
        arm_meta={"trial": {"harness": "claude-code"}, "absent": {"harness": "claude-code"}},
        observed_arms={
            "trial": {
                "actual_version": "1.2.3",
                "sandbox": {"snapshot": "snap-x", "image_digest": "sha256:dead"},
            }
        },
    )
    md = report._format_markdown(bench)

    assert "## Provenance" in md
    assert "- **trial**: version `1.2.3` · snapshot `snap-x` · digest `sha256:dead`" in md
    assert "- **absent**: not observed" in md


def test_index_rows_carry_core_axes_and_no_heavy_provenance(tmp_path: object) -> None:
    """Verify index rows gain harness/model/effort and nothing heavier."""
    seed_arm(tmp_path, "alpha", "trial", passes=2, total=2)

    rows = report.index_rows(
        tmp_path,
        "demo",
        {"trial": {"harness": "claude-code", "model": "opus", "effort": "medium"}},
    )

    row = next(r for r in rows if r["arm"] == "trial")
    assert row["harness"] == "claude-code"
    assert row["model"] == "opus"
    assert row["effort"] == "medium"
    # Nothing heavier than the three core axes leaks onto the row.
    heavy = {"sandbox", "snapshot", "image_digest", "actual_version", "fingerprint"}
    assert not (heavy & set(row))


def test_index_rows_omit_axes_for_unknown_arm(tmp_path: object) -> None:
    """Verify a row whose arm isn't in the axes lookup omits the axes rather than faking null."""
    seed_arm(tmp_path, "alpha", "mystery", passes=1, total=1)

    rows = report.index_rows(tmp_path, "demo", {"trial": {"harness": "claude-code"}})

    row = next(r for r in rows if r["arm"] == "mystery")
    assert "harness" not in row
    assert "model" not in row
    assert "effort" not in row
