"""Tests for JudgeConfig validation and resolve_judge_config layer precedence."""

import pytest

from evalspec.judges.config import JudgeConfig, _validate_judge_table, resolve_judge_config
from evalspec.specs.schema import SchemaError


def test_judge_config_defaults() -> None:
    """Verify judge config defaults."""
    config = JudgeConfig()
    assert config.harness == "claude-code"
    assert config.model == "sonnet"
    assert config.effort == "medium"
    assert config.timeout == 300
    assert config.harness_args == []
    assert config.env == {}


def test_validate_judge_table_accepts_known_keys() -> None:
    """Verify validate judge table accepts known keys."""
    validated = _validate_judge_table("[tool.evalspec.judge]", {
        "harness": "codex", "model": "gpt-5.5", "effort": "medium",
        "timeout": 300, "harness_args": ["--sandbox", "read-only"],
        "env": {"CODEX_HOME": "$CODEX_HOME"},
    })
    assert validated == {
        "harness": "codex", "model": "gpt-5.5", "effort": "medium",
        "timeout": 300, "harness_args": ["--sandbox", "read-only"],
        "env": {"CODEX_HOME": "$CODEX_HOME"},
    }


def test_validate_judge_table_rejects_unknown_key() -> None:
    """Verify validate judge table rejects unknown key."""
    with pytest.raises(SchemaError, match="unknown judge key"):
        _validate_judge_table("[tool.evalspec.judge]", {"harness": "codex", "bogus": 1})


@pytest.mark.parametrize(("bad_table", "match"), [
    ({"harness": 5}, "harness"),
    ({"model": ""}, "model"),
    ({"effort": None}, "effort"),
    ({"timeout": "300"}, "timeout"),
    ({"timeout": 0}, "timeout"),
    ({"timeout": True}, "timeout"),
    ({"harness_args": "not-a-list"}, "harness_args"),
    ({"harness_args": [1, 2]}, "harness_args"),
    ({"env": ["not", "a", "table"]}, "env"),
    ({"env": {"KEY": 1}}, "env"),
])
def test_validate_judge_table_rejects_bad_types(bad_table: object, match: object) -> None:
    """Verify validate judge table rejects bad types."""
    with pytest.raises(SchemaError, match=match):
        _validate_judge_table("[tool.evalspec.judge]", bad_table)


def test_resolve_judge_config_defaults_with_no_layers() -> None:
    """Verify resolve judge config defaults with no layers."""
    config = resolve_judge_config()
    assert config == JudgeConfig()


def test_resolve_judge_config_pyproject_layer() -> None:
    """Verify resolve judge config pyproject layer."""
    config = resolve_judge_config(pyproject_table={"harness": "codex", "model": "gpt-5.5"})
    assert config.harness == "codex"
    assert config.model == "gpt-5.5"
    assert config.effort == "medium"  # untouched fields keep the default


def test_resolve_judge_config_scratch_beats_pyproject_per_field() -> None:
    """Verify resolve judge config scratch beats pyproject per field."""
    config = resolve_judge_config(
        pyproject_table={"harness": "codex", "model": "gpt-5.5", "effort": "high"},
        scratch_table={"model": "gpt-5-mini"},
    )
    assert config.harness == "codex"      # from pyproject, scratch didn't touch it
    assert config.model == "gpt-5-mini"   # scratch wins
    assert config.effort == "high"        # from pyproject


def test_resolve_judge_config_cli_beats_everything() -> None:
    """Verify resolve judge config CLI beats everything."""
    config = resolve_judge_config(
        pyproject_table={"harness": "codex", "model": "gpt-5.5"},
        scratch_table={"model": "gpt-5-mini"},
        cli_table={"model": "gpt-5-nano"},
    )
    assert config.model == "gpt-5-nano"


def test_resolve_judge_config_env_shallow_merges_cli_over_config() -> None:
    """Verify resolve judge config env shallow merges CLI over config."""
    config = resolve_judge_config(
        pyproject_table={"env": {"A": "1", "B": "2"}},
        cli_table={"env": {"B": "override", "C": "3"}},
    )
    assert config.env == {"A": "1", "B": "override", "C": "3"}


def test_resolve_judge_config_harness_args_cli_fully_replaces_not_merges() -> None:
    """Verify resolve judge config harness_args CLI fully replaces, not merges."""
    config = resolve_judge_config(
        pyproject_table={"harness_args": ["--from-pyproject"]},
        cli_table={"harness_args": ["--from-cli"]},
    )
    assert config.harness_args == ["--from-cli"]


def test_resolve_judge_config_rejects_unsupported_harness() -> None:
    """Verify resolve judge config rejects unsupported harness."""
    with pytest.raises(SchemaError, match="not a supported judge harness"):
        resolve_judge_config(pyproject_table={"harness": "cursor"})


def test_resolve_judge_config_rejects_reserved_harness_arg() -> None:
    """Verify resolve judge config rejects reserved harness arg."""
    with pytest.raises(SchemaError, match="reserved"):
        resolve_judge_config(
            pyproject_table={"harness": "claude-code", "harness_args": ["--model", "opus"]},
        )


def test_resolve_judge_config_opencode_requires_provider_qualified_model() -> None:
    """Verify resolve judge config opencode requires provider-qualified model."""
    with pytest.raises(SchemaError, match="provider-qualified"):
        resolve_judge_config(pyproject_table={"harness": "opencode", "model": "sonnet"})


def test_resolve_judge_config_opencode_accepts_provider_qualified_model() -> None:
    """Verify resolve judge config opencode accepts provider-qualified model."""
    config = resolve_judge_config(
        pyproject_table={"harness": "opencode", "model": "anthropic/claude-sonnet-4-6"},
    )
    assert config.harness == "opencode"
