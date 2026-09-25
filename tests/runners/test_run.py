"""The pure `run` flag translation into plugin `--benchspec-*` option tokens.

Each test builds a namespace inline and asserts the exact tokens `translate_run_flags`
emits for it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import pytest

from benchspec.config.options import RunOptions
from benchspec.runners import run
from benchspec.runners.run import PluginOptions, translate_run_flags

# The `test_run_subprocess_*` tests are live-in-process but NON-paid: they spawn a real
# child pytest with `--collect-only`, so the entry-point plugin loads and resolves the set,
# but no task arm executes and no sandbox or credentials are touched.

_ARMS_TOML = textwrap.dedent(
    """\
    [tool.benchspec]
    default-set = "default"

    [tool.benchspec.sets.default]
    harness = "claude-code"
    model = "sonnet"
    baseline = "baseline"
    arms = [{name="baseline"}, {name="trial", model="opus"}]
    """
)

_EVAL_MD = textwrap.dedent(
    """\
    ---
    {}
    ---

    ## Prompt

    Archive the source note.

    ## Assertions

    - [ ] source note archived
    """
)


def _populated_eval_project(root: Path) -> None:
    """Write a minimal but valid arms + one-eval project under `root` (no test_cases.py)."""
    (root / "pyproject.toml").write_text(_ARMS_TOML)
    evals = root / "skills" / "myskill" / "evals" / "myskill"
    evals.mkdir(parents=True)
    (evals / "alpha.eval.md").write_text(_EVAL_MD)


def _run_namespace(root: Path, **flags: object) -> argparse.Namespace:
    """Build a `run` namespace with every curated flag absent except the overrides."""
    values: dict[str, object] = {
        dest: None
        for dest in (
            "set",
            "config",
            "model",
            "models",
            "harness",
            "effort",
            "timeout",
            "trigger_budget",
            "eval_paths",
            "fail_under",
            "judge_harness",
            "judge_provider",
            "judge_model",
            "judge_effort",
            "binder_provider",
            "binder_model",
        )
    }
    values["env"] = []
    values["passthrough"] = []
    values.update(flags)
    return argparse.Namespace(root=root, **values)


@dataclass
class _SpawnRecord:
    """What a fake runner saw when `run` spawned it: the argv and the child environment."""

    argv: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


def _no_preflight(args: argparse.Namespace) -> None:
    """Skip the environment preflight so a fake-runner test never touches the host."""


def _runner_that_must_not_run(argv: list[str], env: dict[str, str] | None = None) -> NoReturn:
    """Fail loudly if the subprocess is spawned when the run should have stopped first."""
    raise AssertionError("runner must not be called")


def _runner_returning(
    returncode: int, recorder: _SpawnRecord
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a fake runner that records its argv/env and reports a fixed return code."""

    def fake_runner(
        argv: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Record the spawned argv and env, then report the chosen return code."""
        recorder.argv = argv
        recorder.env = env
        return subprocess.CompletedProcess(argv, returncode)

    return fake_runner


def test_translates_set_to_benchspec_set() -> None:
    """Verify --set becomes an --benchspec-set token."""
    args = _run_namespace(Path("repo"), set="default")

    tokens = translate_run_flags(args)

    assert "--benchspec-set=default" in tokens


def test_translates_config_model_harness_effort() -> None:
    """Verify config/model/harness/effort each become their --benchspec-* token."""
    args = _run_namespace(
        Path("repo"),
        config="benchspec.toml",
        model="opus",
        harness="claude-code",
        effort="high",
    )

    tokens = translate_run_flags(args)

    assert "--benchspec-config=benchspec.toml" in tokens
    assert "--benchspec-model=opus" in tokens
    assert "--benchspec-harness=claude-code" in tokens
    assert "--benchspec-effort=high" in tokens


def test_translates_timeout_to_benchspec_timeout() -> None:
    """Verify --timeout becomes a --benchspec-timeout token."""
    args = _run_namespace(Path("repo"), timeout=5)

    tokens = translate_run_flags(args)

    assert "--benchspec-timeout=5" in tokens


def test_translates_trigger_budget_to_benchspec_trigger_budget() -> None:
    """Verify --trigger-budget becomes a --benchspec-trigger-budget token."""
    args = _run_namespace(Path("repo"), trigger_budget=3)

    tokens = translate_run_flags(args)

    assert "--benchspec-trigger-budget=3" in tokens


def test_translates_models_and_eval_paths_and_fail_under() -> None:
    """Verify the models sweep, eval paths, and fail-under gate each translate."""
    args = _run_namespace(
        Path("repo"),
        models="opus,sonnet",
        eval_paths="skills,evals",
        fail_under="0.8",
    )

    tokens = translate_run_flags(args)

    assert "--benchspec-models=opus,sonnet" in tokens
    assert "--benchspec-eval-paths=skills,evals" in tokens
    assert "--benchspec-fail-under=0.8" in tokens


def test_translates_common_judge_flags() -> None:
    """Verify judge harness/model/effort each become their --benchspec-judge-* token."""
    args = _run_namespace(
        Path("repo"),
        judge_harness="gemini",
        judge_model="gemini-2.5-pro",
        judge_effort="low",
    )

    tokens = translate_run_flags(args)

    assert "--benchspec-judge-harness=gemini" in tokens
    assert "--benchspec-judge-model=gemini-2.5-pro" in tokens
    assert "--benchspec-judge-effort=low" in tokens


def test_repeatable_env_emits_one_token_per_value() -> None:
    """Verify a repeatable --env emits one --benchspec-env token per value."""
    args = _run_namespace(Path("repo"), env=["A=1", "B=2"])

    tokens = translate_run_flags(args)

    assert "--benchspec-env=A=1" in tokens
    assert "--benchspec-env=B=2" in tokens


def test_root_becomes_repo_root_token() -> None:
    """Verify the root positional becomes a resolved --benchspec-repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens[0] == f"--benchspec-repo-root={root.resolve()}"


def test_absent_flags_add_nothing() -> None:
    """Verify a namespace with no curated flags emits only the repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens == [f"--benchspec-repo-root={root.resolve()}"]


def test_run_assembles_pytest_argv_and_maps_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify run spawns `python -m pytest` with translated flags and no `-p` plugin token."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"), set="default")
    recorder = _SpawnRecord()

    exit_code = run.run(args, runner=_runner_returning(0, recorder), preflight=_no_preflight)

    assert exit_code == 0
    assert recorder.argv == [
        sys.executable,
        "-m",
        "pytest",
        f"--benchspec-repo-root={Path('repo').resolve()}",
        "--benchspec-set=default",
    ]


def test_run_forwards_passthrough_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify passthrough args are appended verbatim as the argv tail."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"), passthrough=["-k", "hello", "-x"])
    recorder = _SpawnRecord()

    run.run(args, runner=_runner_returning(0, recorder), preflight=_no_preflight)

    assert recorder.argv[-3:] == ["-k", "hello", "-x"]


def test_run_child_env_enables_plugin_autoload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the child env drops PYTEST_DISABLE_PLUGIN_AUTOLOAD so the plugin autoloads."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    args = _run_namespace(Path("repo"))
    recorder = _SpawnRecord()

    run.run(args, runner=_runner_returning(0, recorder), preflight=_no_preflight)

    assert recorder.env is not None
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in recorder.env


def test_run_maps_gate_failure_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a pytest status of 1 (tests failed) maps to exit 1."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"))

    exit_code = run.run(args, runner=_runner_returning(1, _SpawnRecord()), preflight=_no_preflight)

    assert exit_code == 1


def test_run_maps_pytest_collection_usage_status_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a pytest status of 2 (collection usage error) maps to exit 2."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"))

    exit_code = run.run(args, runner=_runner_returning(2, _SpawnRecord()), preflight=_no_preflight)

    assert exit_code == 2


def test_run_maps_pytest_usage_error_status_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a pytest status of 4 (usage error) maps to exit 2."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"))

    exit_code = run.run(args, runner=_runner_returning(4, _SpawnRecord()), preflight=_no_preflight)

    assert exit_code == 2


def test_run_empty_root_reports_no_evals_without_spawning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify an empty root returns 5 with a readable message and never spawns the runner."""

    def runner_that_must_not_run(
        argv: list[str], env: dict[str, str] | None = None
    ) -> NoReturn:
        """Fail loudly if the subprocess is spawned for an empty root."""
        raise AssertionError("runner must not be called when no evals are discovered")

    args = _run_namespace(tmp_path)

    exit_code = run.run(args, runner=runner_that_must_not_run)

    assert exit_code == 5
    assert f"no evals discovered under {tmp_path.resolve()}" in capsys.readouterr().out


def test_run_subprocess_collects_populated_fixture_cleanly(tmp_path: Path) -> None:
    """Verify a real `--collect-only` subprocess loads the plugin once and maps success to 0."""
    _populated_eval_project(tmp_path)
    args = _run_namespace(tmp_path, set="default", passthrough=["--collect-only"])

    exit_code = run.run(args)

    assert exit_code == 0


def test_run_subprocess_unknown_set_exits_two(tmp_path: Path) -> None:
    """Verify an unknown --set surfaces the collection UsageError as exit 2 end to end."""
    _populated_eval_project(tmp_path)
    args = _run_namespace(tmp_path, set="does-not-exist", passthrough=["--collect-only"])

    exit_code = run.run(args)

    assert exit_code == 2


def test_plugin_options_present_curated_flags_under_plugin_option_names(tmp_path: Path) -> None:
    """Verify the adapter answers `getoption` with the plugin names the resolvers read."""
    args = _run_namespace(
        tmp_path, set="micro", judge_harness="codex", env=["A=1", "B=2"], eval_paths="x,y"
    )

    options = PluginOptions.from_args(args)

    assert options.rootpath == tmp_path.resolve()
    assert options.getoption("benchspec_repo_root") == str(tmp_path.resolve())
    assert options.getoption("benchspec_set") == "micro"
    assert options.getoption("benchspec_judge_harness") == "codex"
    assert options.getoption("benchspec_env") == ["A=1", "B=2"]
    assert options.getoption("benchspec_eval_paths") == "x,y"
    assert options.getoption("benchspec_model") is None


def test_plugin_options_answer_none_for_any_uncurated_option(tmp_path: Path) -> None:
    """A plugin option `run` does not curate reads as unset, so new options need no mapping."""
    options = PluginOptions.from_args(_run_namespace(tmp_path))

    assert options.getoption("benchspec_judge_timeout") is None
    assert options.getoption("benchspec_option_added_next_year") is None


def test_preflight_run_drives_grading_then_sandbox_through_the_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify preflight_run runs the plugin's grading and sandbox preflights, in that order."""
    calls: list[tuple[str, RunOptions]] = []

    def record_grading(options: RunOptions) -> list[str]:
        """Stand in for the grading preflight, recording the options it was handed."""
        calls.append(("grading", options))
        return []

    def record_sandbox(options: RunOptions) -> list[str]:
        """Stand in for the sandbox preflight, recording the options it was handed."""
        calls.append(("sandbox", options))
        return []

    monkeypatch.setattr(run.cases, "grading_preflight_errors", record_grading)
    monkeypatch.setattr(run.cases, "sandbox_preflight_errors", record_sandbox)
    args = _run_namespace(tmp_path, set="micro")

    run.preflight_run(args)

    assert [name for name, _ in calls] == ["grading", "sandbox"]
    assert all(options.getoption("benchspec_set") == "micro" for _, options in calls)


def test_preflight_run_reports_grading_and_sandbox_failures_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A host missing everything learns every missing piece from one run, not one per run."""
    monkeypatch.setattr(
        run.cases,
        "grading_preflight_errors",
        lambda options: ["GEMINI_API_KEY is required", "judge harness `codex`: not logged in"],
    )
    monkeypatch.setattr(
        run.cases,
        "sandbox_preflight_errors",
        lambda options: ["harness `claude-code`: no Claude credential"],
    )
    args = _run_namespace(tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        run.preflight_run(args)

    assert str(exc_info.value) == textwrap.dedent(
        """\
        benchspec preflight failed:
          - GEMINI_API_KEY is required
          - judge harness `codex`: not logged in
          - harness `claude-code`: no Claude credential"""
    )


def test_run_preflight_runtime_error_exits_two_without_spawning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing credential is one `error:` line and exit 2; pytest never spawns."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])

    def failing_preflight(args: argparse.Namespace) -> None:
        """Reject the environment the way the binder preflight does."""
        raise RuntimeError("GEMINI_API_KEY is required")

    args = _run_namespace(Path("repo"))

    exit_code = run.run(args, runner=_runner_that_must_not_run, preflight=failing_preflight)

    assert exit_code == 2
    assert capsys.readouterr().err == "error: GEMINI_API_KEY is required\n"


def test_run_preflight_usage_error_exits_two_without_spawning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown set caught by the CLI preflight is one `error:` line and exit 2."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])

    def failing_preflight(args: argparse.Namespace) -> None:
        """Reject the set the way the plugin's resolver does at collection."""
        raise pytest.UsageError("unknown eval set `nope`")

    args = _run_namespace(Path("repo"), set="nope")

    exit_code = run.run(args, runner=_runner_that_must_not_run, preflight=failing_preflight)

    assert exit_code == 2
    assert capsys.readouterr().err == "error: unknown eval set `nope`\n"


@pytest.mark.parametrize("collect_flag", ["--collect-only", "--co"])
def test_run_collect_only_passthrough_skips_preflight(
    monkeypatch: pytest.MonkeyPatch, collect_flag: str
) -> None:
    """A collect-only passthrough spends nothing, so the environment preflight is skipped."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])

    def preflight_that_must_not_run(args: argparse.Namespace) -> None:
        """Fail loudly if a collect-only run preflights the environment."""
        raise AssertionError("preflight must not run under collect-only")

    args = _run_namespace(Path("repo"), passthrough=[collect_flag, "-q"])

    exit_code = run.run(
        args, runner=_runner_returning(0, _SpawnRecord()), preflight=preflight_that_must_not_run
    )

    assert exit_code == 0


def test_translates_provider_flags_for_judge_and_binder() -> None:
    """Verify judge/binder provider and binder model each become their --benchspec-* token."""
    args = _run_namespace(
        Path("repo"),
        judge_provider="openrouter",
        binder_provider="openrouter",
        binder_model="google/gemini-3.5-flash-lite",
    )

    tokens = translate_run_flags(args)

    assert "--benchspec-judge-provider=openrouter" in tokens
    assert "--benchspec-binder-provider=openrouter" in tokens
    assert "--benchspec-binder-model=google/gemini-3.5-flash-lite" in tokens
