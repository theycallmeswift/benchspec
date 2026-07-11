"""The pure `run` flag translation into plugin `--evalspec-*` option tokens.

Each test builds a namespace inline and asserts the exact tokens `translate_run_flags`
emits for it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evalspec import run
from evalspec.run import translate_run_flags


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
            "eval_paths",
            "fail_under",
            "judge_harness",
            "judge_model",
            "judge_effort",
        )
    }
    values["env"] = []
    values["passthrough"] = []
    values.update(flags)
    return argparse.Namespace(root=root, **values)


class _FakeCompleted:
    """A stand-in for `subprocess.CompletedProcess` exposing only `.returncode`."""

    def __init__(self: object, returncode: int) -> None:
        """Record the return code the fake runner should report."""
        self.returncode = returncode


def _runner_returning(returncode: int, recorder: dict[str, object]) -> object:
    """Build a fake runner that records its argv/env and reports a fixed return code."""

    def fake_runner(argv: list[str], env: dict[str, str] | None = None) -> _FakeCompleted:
        """Record the spawned argv and env, then report the chosen return code."""
        recorder["argv"] = argv
        recorder["env"] = env
        return _FakeCompleted(returncode)

    return fake_runner


def test_translates_set_to_evalspec_set() -> None:
    """Verify --set becomes an --evalspec-set token."""
    args = _run_namespace(Path("repo"), set="default")

    tokens = translate_run_flags(args)

    assert "--evalspec-set=default" in tokens


def test_translates_config_model_harness_effort() -> None:
    """Verify config/model/harness/effort each become their --evalspec-* token."""
    args = _run_namespace(
        Path("repo"),
        config="evalspec.toml",
        model="opus",
        harness="claude-code",
        effort="high",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-config=evalspec.toml" in tokens
    assert "--evalspec-model=opus" in tokens
    assert "--evalspec-harness=claude-code" in tokens
    assert "--evalspec-effort=high" in tokens


def test_translates_models_and_eval_paths_and_fail_under() -> None:
    """Verify the models sweep, eval paths, and fail-under gate each translate."""
    args = _run_namespace(
        Path("repo"),
        models="opus,sonnet",
        eval_paths="skills,evals",
        fail_under="0.8",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-models=opus,sonnet" in tokens
    assert "--evalspec-eval-paths=skills,evals" in tokens
    assert "--evalspec-fail-under=0.8" in tokens


def test_translates_common_judge_flags() -> None:
    """Verify judge harness/model/effort each become their --evalspec-judge-* token."""
    args = _run_namespace(
        Path("repo"),
        judge_harness="gemini",
        judge_model="gemini-2.5-pro",
        judge_effort="low",
    )

    tokens = translate_run_flags(args)

    assert "--evalspec-judge-harness=gemini" in tokens
    assert "--evalspec-judge-model=gemini-2.5-pro" in tokens
    assert "--evalspec-judge-effort=low" in tokens


def test_repeatable_env_emits_one_token_per_value() -> None:
    """Verify a repeatable --env emits one --evalspec-env token per value."""
    args = _run_namespace(Path("repo"), env=["A=1", "B=2"])

    tokens = translate_run_flags(args)

    assert "--evalspec-env=A=1" in tokens
    assert "--evalspec-env=B=2" in tokens


def test_root_becomes_repo_root_token() -> None:
    """Verify the root positional becomes a resolved --evalspec-repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens[0] == f"--evalspec-repo-root={root.resolve()}"


def test_absent_flags_add_nothing() -> None:
    """Verify a namespace with no curated flags emits only the repo-root token."""
    root = Path("some/dir")

    tokens = translate_run_flags(_run_namespace(root))

    assert tokens == [f"--evalspec-repo-root={root.resolve()}"]


def test_run_assembles_pytest_argv_and_maps_success(monkeypatch: object) -> None:
    """Verify run spawns `python -m pytest` with translated flags and no `-p` plugin token."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"), set="default")
    recorder: dict[str, object] = {}

    exit_code = run.run(args, runner=_runner_returning(0, recorder))

    assert exit_code == 0
    assert recorder["argv"] == [
        sys.executable,
        "-m",
        "pytest",
        f"--evalspec-repo-root={Path('repo').resolve()}",
        "--evalspec-set=default",
    ]


def test_run_forwards_passthrough_verbatim(monkeypatch: object) -> None:
    """Verify passthrough args are appended verbatim as the argv tail."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"), passthrough=["-k", "hello", "-x"])
    recorder: dict[str, object] = {}

    run.run(args, runner=_runner_returning(0, recorder))

    assert recorder["argv"][-3:] == ["-k", "hello", "-x"]


def test_run_child_env_enables_plugin_autoload(monkeypatch: object) -> None:
    """Verify the child env drops PYTEST_DISABLE_PLUGIN_AUTOLOAD so the plugin autoloads."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    args = _run_namespace(Path("repo"))
    recorder: dict[str, object] = {}

    run.run(args, runner=_runner_returning(0, recorder))

    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in recorder["env"]


def test_run_maps_gate_failure_to_one(monkeypatch: object) -> None:
    """Verify a pytest status of 1 (tests failed) maps to exit 1."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"))

    exit_code = run.run(args, runner=_runner_returning(1, {}))

    assert exit_code == 1


def test_run_maps_usage_status_to_two(monkeypatch: object) -> None:
    """Verify pytest statuses 2 and 4 both map to the usage exit code 2."""
    monkeypatch.setattr(run.discovery, "discover_eval_cases", lambda root, eval_paths: [object()])
    args = _run_namespace(Path("repo"))

    assert run.run(args, runner=_runner_returning(2, {})) == 2
    assert run.run(args, runner=_runner_returning(4, {})) == 2


def test_run_empty_root_reports_no_evals_without_spawning(
    tmp_path: Path, capsys: object
) -> None:
    """Verify an empty root returns 5 with a readable message and never spawns the runner."""

    def runner_that_must_not_run(argv: list[str], env: dict[str, str] | None = None) -> object:
        """Fail loudly if the subprocess is spawned for an empty root."""
        raise AssertionError("runner must not be called when no evals are discovered")

    args = _run_namespace(tmp_path)

    exit_code = run.run(args, runner=runner_that_must_not_run)

    assert exit_code == 5
    assert f"no evals discovered under {tmp_path.resolve()}" in capsys.readouterr().out
