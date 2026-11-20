"""Tests for benchspec.config.sets — set and judge-config resolution by value."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchspec.config import sets
from benchspec.grading.judges import JudgeConfig


class _SetConfig:
    """Config stub for resolved_run_set: repo root + the eval-set CLI options.

    Backs the unit-level resolved_run_set tests (no pytester collect) that drive the
    pyproject read + resolve_set wiring by value.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        set_name: str | None = None,
        config: str | None = None,
        model: str | None = None,
        harness: str | None = None,
        effort_level: str | None = None,
        timeout: int | None = None,
        env: list[str] | None = None,
        models: str | None = None,
    ) -> None:
        """Store the eval-set options exactly as the CLI would hand them over."""
        self.rootpath = repo_root
        self._opts: dict[str, object] = {
            "benchspec_repo_root": str(repo_root),
            "benchspec_set": set_name,
            "benchspec_config": config,
            "benchspec_model": model,
            "benchspec_harness": harness,
            "benchspec_effort": effort_level,
            "benchspec_timeout": timeout,
            "benchspec_env": env or [],
            "benchspec_models": models,
        }

    def getoption(self, name: str) -> object:
        """Return the configured value for `name` (None when unset)."""
        return self._opts.get(name)


def _write_sets_pyproject(tmp_path: Path) -> None:
    """Write sets pyproject."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "default"\n'
        "[tool.benchspec.sets.default]\n"
        'harness = "claude-code"\n'  # set-level default → sweep arms inherit a concrete harness
        'model = "sonnet"\n'
        'baseline = "baseline"\n'
        'arms = [{name="baseline"}, {name="trial"}]\n'
    )


def test_resolved_run_set_reads_pyproject(tmp_path: Path) -> None:
    """Verify resolved run set reads pyproject."""
    _write_sets_pyproject(tmp_path)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path))
    assert [arm.name for arm in run_set.arms] == ["baseline", "trial"]
    assert run_set.baseline == "baseline"


def test_resolved_run_set_preserves_harness_args(tmp_path: Path) -> None:
    """Verify resolved run set preserves harness args."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "default"\n'
        "[tool.benchspec.sets.default]\n"
        'harness = "claude-code"\n'
        'model = "sonnet"\n'
        'harness_args = ["--set-flag"]\n'
        'baseline = "baseline"\n'
        "arms = [\n"
        '  {name="baseline"},\n'
        '  {name="trial", harness_args=["--plugin-dir", "/project"]},\n'
        "]\n"
    )

    run_set = sets.resolved_run_set(_SetConfig(tmp_path))

    assert run_set.arms[0].harness_args == ["--set-flag"]
    assert run_set.arms[1].harness_args == ["--set-flag", "--plugin-dir", "/project"]


def test_resolved_run_set_timeout_overrides_set_default_not_arm(tmp_path: Path) -> None:
    """Verify --benchspec-timeout replaces the set default while an arm's own value stays."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "default"\n'
        "[tool.benchspec.sets.default]\n"
        'harness = "claude-code"\n'
        'model = "sonnet"\n'
        "timeout = 900\n"
        'baseline = "baseline"\n'
        "arms = [\n"
        '  {name="baseline"},\n'
        '  {name="slow", timeout=1800},\n'
        "]\n"
    )

    run_set = sets.resolved_run_set(_SetConfig(tmp_path, timeout=300))

    assert run_set.arms[0].timeout == 300
    assert run_set.arms[1].timeout == 1800


def test_resolved_run_set_set_model_default_not_clobbered(tmp_path: Path) -> None:
    """Verify resolved run set set model default not clobbered."""
    # A non-sonnet set default must survive a plain run (no --benchspec-model passed).
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec]\ndefault-set = "default"\n'
        '[tool.benchspec.sets.default]\nharness = "claude-code"\n'
        'model = "opus"\nbaseline = "b"\narms = [{name="b"}]\n'
    )
    run_set = sets.resolved_run_set(_SetConfig(tmp_path))  # benchspec_model defaults to None
    assert run_set.arms[0].model == "opus"


def test_resolved_run_set_models_sweep(tmp_path: Path) -> None:
    """Verify resolved run set models sweep."""
    _write_sets_pyproject(tmp_path)

    run_set = sets.resolved_run_set(_SetConfig(tmp_path, models="sonnet,opus"))

    assert [arm.name for arm in run_set.arms] == ["sonnet", "opus"]
    assert run_set.baseline == "sonnet"
    assert all(arm.harness == "claude-code" for arm in run_set.arms)  # inherited, never None


def test_resolved_run_set_unknown_set_raises_usageerror(tmp_path: Path) -> None:
    """Verify resolved run set unknown set raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    with pytest.raises(pytest.UsageError, match="no eval set named"):
        sets.resolved_run_set(_SetConfig(tmp_path, set_name="ghost"))


def test_resolved_run_set_legacy_config_raises_usageerror(tmp_path: Path) -> None:
    """Verify resolved run set legacy config raises for usageerror."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec]\nreference = "x"\n'
        '[[tool.benchspec.arms]]\nname="x"\nharness="claude-code"\nmodel="opus"\n'
    )
    with pytest.raises(pytest.UsageError, match="eval set"):
        sets.resolved_run_set(_SetConfig(tmp_path))


def test_resolved_run_set_missing_scratch_config_raises_usageerror(
    tmp_path: Path,
) -> None:
    """Verify resolved run set missing scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)

    with pytest.raises(pytest.UsageError, match="--benchspec-config"):
        sets.resolved_run_set(_SetConfig(tmp_path, config=str(tmp_path / "missing.toml")))


def test_resolved_run_set_malformed_scratch_config_raises_usageerror(
    tmp_path: Path,
) -> None:
    """Verify resolved run set malformed scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    scratch = tmp_path / "scratch.toml"
    scratch.write_text("[tool.benchspec\n")

    with pytest.raises(pytest.UsageError, match="--benchspec-config"):
        sets.resolved_run_set(_SetConfig(tmp_path, config=str(scratch)))


def test_resolved_run_set_invalid_utf8_scratch_config_raises_usageerror(
    tmp_path: Path,
) -> None:
    """Verify resolved run set invalid utf8 scratch config raises for usageerror."""
    _write_sets_pyproject(tmp_path)
    scratch = tmp_path / "scratch.toml"
    scratch.write_bytes(b"\xff")

    with pytest.raises(pytest.UsageError, match="--benchspec-config"):
        sets.resolved_run_set(_SetConfig(tmp_path, config=str(scratch)))


def test_session_run_set_resolves_when_cases_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify session_run_set resolves the set (with its sandbox) when a run has cases."""
    _write_sets_pyproject(tmp_path)  # sandbox default = docker
    monkeypatch.setattr(sets, "discover_eval_cases", lambda root, paths: [object()])

    run_set = sets.session_run_set(_FakeConfig(tmp_path))

    assert run_set is not None
    assert run_set.sandbox == "docker"  # feeds the session sandbox preflight


def test_session_run_set_none_for_trigger_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a trigger-only run (no cases, no sets table) degrades to None, never raises."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")
    monkeypatch.setattr(sets, "discover_eval_cases", lambda root, paths: [])

    # resolved_run_set here WOULD raise UsageError (no sets table); the guard must not.
    assert sets.session_run_set(_FakeConfig(tmp_path)) is None


def test_run_set_when_needed_degrades_without_resolving(tmp_path: Path) -> None:
    """Verify needs_set=False returns None even where resolving would raise (no sets table)."""
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")

    assert sets.run_set_when_needed(_FakeConfig(tmp_path), needs_set=False) is None


class _FakeConfig:
    """Fake config for the pytest_sessionfinish / pytest_terminal_summary tests.

    Those hooks are the manifest + benchmark glue; this stub supplies the option
    surface they read.
    """

    def __init__(self, repo_root: Path, fail_under: float | None = None) -> None:
        """Store the repo root and the optional pass-rate floor."""
        self.rootpath = repo_root
        self._repo_root = str(repo_root)
        self._fail_under = fail_under
        self.stash = pytest.Stash()

    def getoption(self, name: str) -> object:
        """Return the configured value for `name` (None when unset)."""
        if name == "benchspec_repo_root":
            return self._repo_root
        return {
            "benchspec_model": None,
            "benchspec_set": None,
            "benchspec_config": None,
            "benchspec_harness": None,
            "benchspec_effort": None,
            "benchspec_timeout": None,
            "benchspec_env": [],
            "benchspec_models": None,
            "benchspec_judge_harness": None,
            "benchspec_judge_model": None,
            "benchspec_judge_effort": None,
            "benchspec_judge_timeout": None,
            "benchspec_judge_harness_arg": [],
            "benchspec_judge_env": [],
            "benchspec_fail_under": self._fail_under,
        }.get(name)


def _write_timeout_pyproject(tmp_path: Path, *, set_timeout: int, arm_timeout: int) -> None:
    """Write a set whose set-level and `slow` arm-level cell timeouts are given."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.benchspec]\n"
        'default-set = "default"\n'
        "[tool.benchspec.sets.default]\n"
        'harness = "claude-code"\n'
        'model = "sonnet"\n'
        f"timeout = {set_timeout}\n"
        'baseline = "baseline"\n'
        "arms = [\n"
        '  {name="baseline"},\n'
        f'  {{name="slow", timeout={arm_timeout}}},\n'
        "]\n"
    )


@pytest.mark.parametrize("set_timeout", [299, 300])
def test_set_level_timeout_at_or_below_the_judge_timeout_is_rejected(
    tmp_path: Path, set_timeout: int
) -> None:
    """A set-level cap at or below the judge timeout fails naming both values and the fix."""
    _write_timeout_pyproject(tmp_path, set_timeout=set_timeout, arm_timeout=900)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path))

    with pytest.raises(pytest.UsageError) as raised:
        sets.validate_cell_caps(run_set, JudgeConfig(timeout=300))

    message = str(raised.value)
    assert f"baseline ({set_timeout}s)" in message
    assert "slow" not in message
    assert "judge timeout (300s)" in message
    assert "Raise `timeout`" in message
    assert "lower the judge `timeout`" in message


@pytest.mark.parametrize("arm_timeout", [299, 300])
def test_arm_level_timeout_at_or_below_the_judge_timeout_is_rejected(
    tmp_path: Path, arm_timeout: int
) -> None:
    """An arm's own cap is checked too, and only the offending arm is named."""
    _write_timeout_pyproject(tmp_path, set_timeout=900, arm_timeout=arm_timeout)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path))

    with pytest.raises(pytest.UsageError) as raised:
        sets.validate_cell_caps(run_set, JudgeConfig(timeout=300))

    assert f"slow ({arm_timeout}s)" in str(raised.value)
    assert "baseline" not in str(raised.value)


@pytest.mark.parametrize("cli_timeout", [200, 300])
def test_cli_timeout_at_or_below_the_judge_timeout_is_rejected(
    tmp_path: Path, cli_timeout: int
) -> None:
    """`--timeout` replaces the set default, so it is checked against the judge timeout."""
    _write_timeout_pyproject(tmp_path, set_timeout=900, arm_timeout=900)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path, timeout=cli_timeout))

    with pytest.raises(pytest.UsageError, match=rf"baseline \({cli_timeout}s\)"):
        sets.validate_cell_caps(run_set, JudgeConfig(timeout=300))


def test_timeouts_just_above_the_judge_timeout_are_accepted(tmp_path: Path) -> None:
    """A cap one second over the judge timeout leaves the agent a second, and is valid."""
    _write_timeout_pyproject(tmp_path, set_timeout=301, arm_timeout=301)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path, timeout=301))

    sets.validate_cell_caps(run_set, JudgeConfig(timeout=300))


def test_lowering_the_judge_timeout_makes_a_short_cap_valid(tmp_path: Path) -> None:
    """A 20 s cap is valid once the judge timeout is below it."""
    _write_timeout_pyproject(tmp_path, set_timeout=900, arm_timeout=900)
    run_set = sets.resolved_run_set(_SetConfig(tmp_path, timeout=20))

    sets.validate_cell_caps(run_set, JudgeConfig(timeout=10))
