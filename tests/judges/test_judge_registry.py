"""Tests for the judge runner registry, binary preflight, and run_judge dispatch."""

from typing import NoReturn

import pytest

from evalspec.judges.config import JudgeConfig
from evalspec.judges.registry import (
    judge_binary,
    known_judge_harnesses,
    preflight_judge_binary,
    probe_judge_version,
    run_judge,
)
from evalspec.schema import SchemaError


def test_known_judge_harnesses() -> None:
    """Verify known judge harnesses."""
    assert known_judge_harnesses() == frozenset({"claude-code", "codex", "opencode"})


def test_judge_binary_names() -> None:
    """Verify judge binary names."""
    assert judge_binary("claude-code") == "claude"
    assert judge_binary("codex") == "codex"
    assert judge_binary("opencode") == "opencode"


class _Config:
    """A minimal JudgeConfig stand-in exposing only the `harness` attribute."""

    def __init__(self, harness: object) -> None:
        """Store the harness name."""
        self.harness = harness


def test_preflight_judge_binary_raises_when_missing(monkeypatch: object) -> None:
    """Verify preflight judge binary raises when missing."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        preflight_judge_binary(_Config("claude-code"))


def test_preflight_judge_binary_passes_when_present(monkeypatch: object) -> None:
    """Verify preflight judge binary passes when present."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    preflight_judge_binary(_Config("codex"))  # no raise


def test_run_judge_dispatches_by_harness(monkeypatch: object) -> None:
    """Verify run judge dispatches by harness."""
    calls = []

    def fake_claude_run(prompt: object, **kwargs: object) -> str:
        """Record the dispatched call and return a canned envelope."""
        calls.append(("claude-code", prompt, kwargs))
        return '{"result": "ok"}'

    monkeypatch.setattr("evalspec.judges.claude_code.run", fake_claude_run)

    out = run_judge("grade", config=JudgeConfig(harness="claude-code", model="sonnet"))

    assert out == '{"result": "ok"}'
    assert calls[0][0] == "claude-code"
    assert calls[0][2]["model"] == "sonnet"


def test_run_judge_expands_env_using_arms_expand_env(monkeypatch: object) -> None:
    """Verify run judge expands env using arms expand_env."""
    monkeypatch.setenv("MY_JUDGE_VAR", "expanded-value")
    captured = {}

    def fake_codex_run(prompt: object, **kwargs: object) -> str:
        """Capture the expanded env passed to the runner and return a canned envelope."""
        captured["env"] = kwargs["env"]
        return '{"result": "ok"}'

    monkeypatch.setattr("evalspec.judges.codex.run", fake_codex_run)

    run_judge("grade", config=JudgeConfig(harness="codex", model="gpt-5.5",
                                          env={"CODEX_HOME": "$MY_JUDGE_VAR", "LITERAL": "x"}))

    assert captured["env"] == {"CODEX_HOME": "expanded-value", "LITERAL": "x"}


def test_run_judge_env_unset_var_raises_schemaerror(monkeypatch: object) -> None:
    """Verify run judge env unset var raises SchemaError."""
    monkeypatch.delenv("DEFINITELY_UNSET_JUDGE_VAR", raising=False)

    with pytest.raises(SchemaError, match="DEFINITELY_UNSET_JUDGE_VAR"):
        run_judge("grade", config=JudgeConfig(env={"KEY": "$DEFINITELY_UNSET_JUDGE_VAR"}))


def test_probe_judge_version_best_effort_none_on_exception(monkeypatch: object) -> None:
    """Verify probe judge version is best effort, returning None on exception."""
    def boom() -> NoReturn:
        raise RuntimeError("boom")
    monkeypatch.setattr("evalspec.judges.claude_code.probe_version", boom)
    assert probe_judge_version("claude-code") is None


def test_probe_judge_version_returns_probe_result(monkeypatch: object) -> None:
    """Verify probe judge version returns the probe result."""
    monkeypatch.setattr("evalspec.judges.codex.probe_version", lambda: "1.2.3")
    assert probe_judge_version("codex") == "1.2.3"
