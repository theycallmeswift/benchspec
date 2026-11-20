"""The `cells` progress format, driven through real pytest subprocesses.

Each test writes a small eval project plus a conftest that stands in for the sandbox
run, then runs the plugin the way `benchspec run` does. A subprocess keeps the stand-ins
out of this process and reaches xdist workers too.
"""

from __future__ import annotations

import re
import shutil
import textwrap

import pytest

PYPROJECT = textwrap.dedent(
    """\
    [tool.benchspec]
    default-set = "default"
    {extra}
    [tool.benchspec.sets.default]
    harness = "claude-code"
    model = "sonnet"
    baseline = "baseline"
    arms = [{{name="baseline"}}, {{name="trial", model="opus"}}]
    """
)

EVALS = {
    "alpha": ["source note archived", "summary written"],
    "beta": ["report filed"],
}

SCOPED_OFF_BASELINE_EVAL = textwrap.dedent(
    """\
    ---
    {}
    ---

    ## Prompt

    Use the skill.

    ## Assertions

    - [ ] Skill `myskill` invoked
      - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
    """
)

# Stands in for the sandbox run: alpha passes on baseline and fails one assertion on
# trial; beta errors on baseline and raises on trial. Patched at import, not through a
# fixture, so the stand-ins also hold on the xdist controller at session end.
STAND_IN_CONFTEST = textwrap.dedent(
    """\
    import json
    import os

    from benchspec.agents.base import AgentCapabilities
    from benchspec.grading import binder
    from benchspec.orchestration import cases, workspace
    from benchspec.orchestration.execution import ArmOutcome
    from benchspec.reporting import manifest, report
    from benchspec.sandbox import sandbox
    from benchspec.specs import scope


    class StubAgent:
        id = "claude-code"
        capabilities = AgentCapabilities(multi_turn=True, token_split=True)

        def version(self):
            return "9.9.9"


    def stand_in_arm(eval_case, arm, *args, repo_root, sample, baseline, eval_set, **kwargs):
        cell = f"{eval_case.eval_id}-{arm.name}"
        if cell == os.environ.get("CRASH_WORKER_ON"):
            os._exit(1)
        if cell == "beta-trial":
            raise RuntimeError("setup.sh exited 2\\nsecond line")

        graded = scope.applicable(eval_case, arm, baseline=baseline, eval_set=eval_set)
        errored = cell == "beta-baseline"
        failing = len(graded) if errored else 1 if cell == "alpha-trial" else 0
        assertions = [
            {"text": text, "passed": index >= failing, "evidence": ""}
            if holds
            else {"text": text, "passed": None, "skipped": True, "scoped": True}
            for index, (text, holds) in enumerate(zip(eval_case.assertions, graded))
        ]
        grading = {
            "eval_id": eval_case.eval_id,
            "skill": eval_case.skill,
            "arm": arm.name,
            "sample": sample,
            "errored": errored,
            "binder_degraded": 0,
            "assertions": assertions,
        }
        timing = {"duration_ms": 1000, "judge_ms": 200, "total_tokens": 500}
        if cell == "alpha-trial":
            timing.update(first_output_ms=2100, cost_usd=0.031)

        run_dir = workspace.arm_dir(
            repo_root, eval_case.skill, eval_case.eval_id, arm.name, sample=sample
        )
        run_dir.mkdir(parents=True)
        (run_dir / "grading.json").write_text(json.dumps(grading))
        (run_dir / "timing.json").write_text(json.dumps(timing))

        return ArmOutcome(grading, errored, 1000, 500, timing)


    def stand_in_room(*args, **kwargs):
        if os.environ.get("BREAK_CELL_SETUP"):
            raise RuntimeError("workspace seed failed")

        return {}


    sandbox.preflight = lambda backend=None, **kwargs: None
    binder.preflight_verify_binder_key = lambda config: None
    binder.binder_for = lambda config: lambda text: None
    cases.grading_preflight_errors = lambda config: []
    cases.preflight_verify_judge_binary = lambda config: None
    cases.preflight_verify_judge_credential = lambda config: None
    cases.seed_room = stand_in_room
    cases.run_eval_arm = stand_in_arm
    report.make_agent = lambda harness=None: StubAgent()
    manifest.probe_judge_version = lambda harness: "judge-1.0.0"
    """
)

# A `-p` plugin, not the conftest: the cells live outside the project directory, where a
# conftest's hooks do not reach.
TEARDOWN_BREAKER_PLUGIN = textwrap.dedent(
    """\
    import pytest


    @pytest.hookimpl(trylast=True)
    def pytest_runtest_teardown(item):
        if "alpha-baseline" in item.name:
            raise RuntimeError("teardown failed")
    """
)

CELL_LINE = re.compile(r"^\[\s*(\d+)/(\d+|\?)\] ")
PROGRESS_LETTERS = re.compile(r"^[.FE]+(\s+\[\s*\d+%\])?$", re.MULTILINE)
ANSI_OR_REDRAW = re.compile(r"\x1b|\r")


def _make_project(pytester: pytest.Pytester, *, pyproject_extra: str = "") -> None:
    """Write the eval set, the alpha and beta evals, and the stand-in conftest."""
    (pytester.path / "pyproject.toml").write_text(
        PYPROJECT.format(extra=pyproject_extra), encoding="utf-8"
    )
    evals = pytester.path / "skills" / "myskill" / "evals" / "myskill"
    evals.mkdir(parents=True)
    for eval_id, assertions in EVALS.items():
        checklist = "\n".join(f"- [ ] {assertion}" for assertion in assertions)
        (evals / f"{eval_id}.eval.md").write_text(
            f"---\n{{}}\n---\n\n## Prompt\n\nDo the task.\n\n## Assertions\n\n{checklist}\n",
            encoding="utf-8",
        )
    pytester.makeconftest(STAND_IN_CONFTEST)


def _run(pytester: pytest.Pytester, *extra: str) -> pytest.RunResult:
    """Run the project's eval cells in a pytest subprocess with the plugin loaded."""
    return pytester.runpytest_subprocess(
        "-p",
        "benchspec.runners.pytest",
        "-p",
        "xdist",
        "-p",
        "pytest_repeat",
        "-p",
        "no:cacheprovider",
        f"--benchspec-repo-root={pytester.path}",
        *extra,
    )


def _cell_lines(result: pytest.RunResult) -> list[str]:
    """The `[n/N]` lines a run printed, with each wall time pinned so runs compare."""
    return [
        re.sub(r"\d+\.\ds  first", "0.0s  first", line)
        for line in result.outlines
        if CELL_LINE.match(line)
    ]


def _counters(lines: list[str]) -> list[str]:
    """The `[n/N]` counter each cell line opens with."""
    return [line.split(" ", 1)[0] for line in lines]


def _benchmark_section(result: pytest.RunResult) -> list[str]:
    """The lines from the `benchspec benchmark` banner through its `Report:` pointer."""
    lines = result.outlines
    start = next(index for index, line in enumerate(lines) if "benchspec benchmark" in line)
    end = next(index for index, line in enumerate(lines) if line.startswith("Report:"))

    return lines[start : end + 1]


def test_serial_run_prints_one_line_per_finished_sample_before_the_matrix(
    pytester: pytest.Pytester,
) -> None:
    """Verify serial run prints one line per finished sample before the matrix."""
    _make_project(pytester)

    result = _run(pytester)

    assert _cell_lines(result) == [
        "[1/4] PASS     myskill/eval-alpha  baseline  s0   2/2    0.0s  first —     —",
        "[2/4] FAIL     myskill/eval-alpha  trial     s0   1/2    0.0s  first 2.1s  $0.031",
        "[3/4] ERROR    myskill/eval-beta   baseline  s0   0/1    0.0s  first —     —"
        "  AssertionError: baseline run errored (see the arm's grading.json)",
        "[4/4] ERROR    myskill/eval-beta   trial     s0   —      0.0s  first —     —"
        "  RuntimeError: setup.sh exited 2",
    ]
    last_cell_line = max(
        index for index, line in enumerate(result.outlines) if CELL_LINE.match(line)
    )
    banner = next(
        index for index, line in enumerate(result.outlines) if "benchspec benchmark" in line
    )
    assert last_cell_line < banner


def test_cells_replace_the_progress_letters_and_the_eval_file_path(
    pytester: pytest.Pytester,
) -> None:
    """Verify cells replace the progress letters and the eval file path."""
    _make_project(pytester)

    result = _run(pytester)

    before_failures = result.stdout.str().split("= FAILURES =")[0]
    assert ".eval.md" not in before_failures
    assert not PROGRESS_LETTERS.search(before_failures)
    assert "%]" not in before_failures


@pytest.mark.parametrize(
    ("option", "pyproject_extra"),
    [("--benchspec-progress=dots", ""), ("-q", 'progress = "dots"\n')],
    ids=["flag", "config-key"],
)
def test_dots_keeps_pytests_progress_line(
    pytester: pytest.Pytester, option: str, pyproject_extra: str
) -> None:
    """Verify dots keeps pytests progress line."""
    _make_project(pytester, pyproject_extra=pyproject_extra)

    result = _run(pytester, option, "--benchspec-progress=dots" if option == "-q" else "-q")

    assert _cell_lines(result) == []
    assert re.search(r"^\.\.FF\s+\[100%\]$", result.stdout.str(), re.MULTILINE)


def test_dots_from_the_config_key_names_each_eval_file(pytester: pytest.Pytester) -> None:
    """Verify dots from the config key names each eval file."""
    _make_project(pytester, pyproject_extra='progress = "dots"\n')

    result = _run(pytester)

    assert _cell_lines(result) == []
    result.stdout.re_match_lines(
        [
            r"skills/myskill/evals/myskill/alpha\.eval\.md \.\.",
            r"skills/myskill/evals/myskill/beta\.eval\.md FF",
        ]
    )


def test_the_flag_beats_the_config_key(pytester: pytest.Pytester) -> None:
    """Verify the flag beats the config key."""
    _make_project(pytester, pyproject_extra='progress = "dots"\n')

    result = _run(pytester, "--benchspec-progress=cells")

    assert len(_cell_lines(result)) == 4


@pytest.mark.parametrize(
    ("option", "pyproject_extra", "source"),
    [
        ("--benchspec-progress=spinner", "", "--benchspec-progress='spinner'"),
        ("-q", 'progress = "spinner"\n', "[tool.benchspec] progress='spinner'"),
    ],
    ids=["flag", "config-key"],
)
def test_an_unknown_progress_format_is_a_usage_error_naming_its_source(
    pytester: pytest.Pytester, option: str, pyproject_extra: str, source: str
) -> None:
    """Verify an unknown progress format is a usage error naming its source."""
    _make_project(pytester, pyproject_extra=pyproject_extra)

    result = _run(pytester, option)

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert f"{source} is not a progress format; allowed: cells, dots" in result.stderr.str()


def test_xdist_controller_prints_every_cell_once_with_a_gapless_counter(
    pytester: pytest.Pytester,
) -> None:
    """Verify xdist controller prints every cell once with a gapless counter."""
    _make_project(pytester)

    result = _run(pytester, "-n", "2")

    lines = _cell_lines(result)
    assert _counters(lines) == ["[1/4]", "[2/4]", "[3/4]", "[4/4]"]
    cells = sorted(re.sub(r"\s+", " ", line.split("] ", 1)[1])[:44] for line in lines)
    assert [cell.split(" s0")[0] for cell in cells] == [
        "ERROR myskill/eval-beta baseline",
        "ERROR myskill/eval-beta trial",
        "FAIL myskill/eval-alpha trial",
        "PASS myskill/eval-alpha baseline",
    ]
    assert not [line for line in result.outlines if re.match(r"\[gw\d+\].*test_eval", line)]


def test_a_failing_setup_fixture_prints_one_error_line_per_cell(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a failing setup fixture prints one error line per cell."""
    _make_project(pytester)
    monkeypatch.setenv("BREAK_CELL_SETUP", "1")

    result = _run(pytester, "-k", "alpha")

    assert _cell_lines(result) == [
        "[1/2] ERROR    myskill/eval-alpha  baseline  s0   —      0.0s  first —     —"
        "  RuntimeError: workspace seed failed",
        "[2/2] ERROR    myskill/eval-alpha  trial     s0   —      0.0s  first —     —"
        "  RuntimeError: workspace seed failed",
    ]
    result.assert_outcomes(errors=2)


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_a_failing_teardown_adds_no_line_and_leaks_no_progress_letter(
    pytester: pytest.Pytester, workers: tuple[str, ...]
) -> None:
    """Verify a failing teardown adds no line and leaks no progress letter."""
    _make_project(pytester)
    pytester.makepyfile(teardown_breaker=TEARDOWN_BREAKER_PLUGIN)

    result = _run(pytester, "-p", "teardown_breaker", "-k", "alpha", *workers)

    lines = _cell_lines(result)
    assert _counters(lines) == ["[1/2]", "[2/2]"]
    assert len([line for line in lines if " PASS " in line]) == 1
    assert len([line for line in lines if " FAIL " in line]) == 1
    before_errors = result.stdout.str().split("= ERRORS =")[0]
    assert not PROGRESS_LETTERS.search(before_errors)
    assert "%]" not in before_errors
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.re_match_lines([r"ERROR .*test_eval\[myskill-alpha-baseline\]"])


def test_a_sample_whose_xdist_worker_dies_prints_an_error_line(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a sample whose xdist worker dies prints an error line."""
    _make_project(pytester)
    monkeypatch.setenv("CRASH_WORKER_ON", "alpha-trial")

    result = _run(pytester, "-n", "2", "--count", "2", "-k", "alpha")

    lines = _cell_lines(result)
    assert _counters(lines) == ["[1/4]", "[2/4]", "[3/4]", "[4/4]"]
    crashed = [line for line in lines if "ERROR    myskill/eval-alpha  trial " in line]
    assert sorted(re.findall(r" s\d ", "".join(crashed))) == [" s0 ", " s1 "]
    assert all(re.search(r"—  worker 'gw\d+' crashed while running", line) for line in crashed)
    before_failures = result.stdout.str().split("= FAILURES =")[0]
    assert not PROGRESS_LETTERS.search(before_failures)
    assert "%]" not in before_failures


def test_dots_reports_carry_no_cell_properties(pytester: pytest.Pytester) -> None:
    """Verify dots reports carry no cell properties."""
    _make_project(pytester)

    _run(pytester, "--benchspec-progress=dots", "--junitxml=dots.xml")
    shutil.rmtree(pytester.path / "tmp")
    _run(pytester, "--junitxml=cells.xml")

    assert "benchspec_cell" not in (pytester.path / "dots.xml").read_text(encoding="utf-8")
    assert "benchspec_cell_result" in (pytester.path / "cells.xml").read_text(encoding="utf-8")


def test_the_counter_total_follows_the_selection(pytester: pytest.Pytester) -> None:
    """Verify the counter total follows the selection."""
    _make_project(pytester)

    result = _run(pytester, "-k", "alpha-baseline", "--count", "3")

    assert _cell_lines(result) == [
        "[1/3] PASS     myskill/eval-alpha  baseline  s0   2/2    0.0s  first —     —",
        "[2/3] PASS     myskill/eval-alpha  baseline  s1   2/2    0.0s  first —     —",
        "[3/3] PASS     myskill/eval-alpha  baseline  s2   2/2    0.0s  first —     —",
    ]


@pytest.mark.parametrize("workers", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_a_sample_scoped_off_its_arm_prints_no_line_and_counts_in_neither_number(
    pytester: pytest.Pytester, workers: tuple[str, ...]
) -> None:
    """Verify a sample scoped off its arm prints no line and counts in neither number."""
    _make_project(pytester)
    evals = pytester.path / "skills" / "myskill" / "evals" / "myskill"
    (evals / "gamma.eval.md").write_text(SCOPED_OFF_BASELINE_EVAL, encoding="utf-8")

    result = _run(pytester, "-k", "alpha or gamma", "--count", "2", *workers)

    lines = _cell_lines(result)
    assert _counters(lines) == [f"[{finished}/6]" for finished in range(1, 7)]
    assert not [line for line in lines if "eval-gamma  baseline" in line]
    assert len([line for line in lines if "eval-gamma  trial" in line]) == 2
    assert not PROGRESS_LETTERS.search(result.stdout.str())


def test_piped_output_has_no_color_and_no_redraw(pytester: pytest.Pytester) -> None:
    """Verify piped output has no color and no redraw."""
    _make_project(pytester)

    result = _run(pytester)

    assert not ANSI_OR_REDRAW.search(result.stdout.str())


def test_no_color_wins_over_a_forced_color_terminal(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify no color wins over a forced color terminal."""
    _make_project(pytester)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    result = _run(pytester)

    assert len(_cell_lines(result)) == 4
    assert not ANSI_OR_REDRAW.search(result.stdout.str())


def test_a_color_terminal_colors_the_state_word(pytester: pytest.Pytester) -> None:
    """Verify a color terminal colors the state word."""
    _make_project(pytester)

    result = _run(pytester, "--color=yes", "-k", "alpha")

    output = result.stdout.str()
    assert "[1/2] \x1b[32mPASS\x1b[0m     myskill/eval-alpha  baseline" in output
    assert "[2/2] \x1b[31mFAIL\x1b[0m     myskill/eval-alpha  trial" in output


def test_exit_status_footer_and_matrix_match_between_cells_and_dots(
    pytester: pytest.Pytester,
) -> None:
    """Verify exit status footer and matrix match between cells and dots."""
    _make_project(pytester)

    cells = _run(pytester)
    shutil.rmtree(pytester.path / "tmp")
    dots = _run(pytester, "--benchspec-progress=dots")

    assert cells.ret == dots.ret == pytest.ExitCode.TESTS_FAILED
    assert cells.parseoutcomes() == dots.parseoutcomes() == {"failed": 2, "passed": 2}
    assert _benchmark_section(cells) == _benchmark_section(dots)


def test_verbose_run_prints_the_cell_line_under_pytests_own(pytester: pytest.Pytester) -> None:
    """Verify verbose run prints the cell line under pytests own."""
    _make_project(pytester)

    result = _run(pytester, "-v", "-k", "alpha-baseline")

    result.stdout.re_match_lines(
        [
            r".*alpha\.eval\.md::test_eval\[myskill-alpha-baseline\]\s*$",
            r"\[1/1\] PASS     myskill/eval-alpha  baseline  s0   2/2 .*",
        ],
        consecutive=True,
    )


def test_a_session_without_eval_cells_keeps_pytests_progress_line(
    pytester: pytest.Pytester,
) -> None:
    """Verify a session without eval cells keeps pytests progress line."""
    _make_project(pytester)
    pytester.makepyfile(test_plain="def test_plain():\n    pass\n")

    result = _run(pytester, "test_plain.py")

    result.stdout.re_match_lines([r"test_plain\.py \.\s+\[100%\]"])
    assert _cell_lines(result) == []
