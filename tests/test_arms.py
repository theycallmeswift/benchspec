"""Tests for arms."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from evalspec.arms import (
    Arm,
    expand_env,
    parse_sets,
    resolve_set,
)
from evalspec.specs.schema import SchemaError


def _sets_table(sets: object, default: object = "default", **extra: object) -> object:
    """Build the sets table test fixture."""
    return {"sets": sets, "default-set": default, **extra}


def test_parse_sets_basic_default_and_baseline() -> None:
    """Verify parse sets basic default and baseline."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "effort": "medium",
                    "baseline": "baseline",
                    "arms": [
                        {"name": "baseline", "harness": "claude-code"},
                        {"name": "trial", "harness": "claude-code"},
                    ],
                },
            }
        )
    )

    assert default == "default"
    assert set(rawsets) == {"default"}
    rs = rawsets["default"]
    assert rs.baseline == "baseline"
    assert [a["name"] for a in rs.raw_arms] == ["baseline", "trial"]
    assert rs.defaults["model"] == "sonnet"


def test_parse_sets_rejects_legacy_flat_config() -> None:
    """Verify parse sets rejects legacy flat config."""
    # No [tool.evalspec.sets.*] but the old flat shape present → fail-fast pointer.
    with pytest.raises(SchemaError, match="eval set"):
        parse_sets(
            {
                "arms": [{"name": "legacy", "harness": "claude-code", "model": "opus"}],
                "reference": "legacy",
            }
        )


def test_parse_sets_missing_sets_fails() -> None:
    """Verify parse sets missing sets fails."""
    with pytest.raises(SchemaError, match="eval set"):
        parse_sets({})


def test_parse_sets_duplicate_arm_name_fails() -> None:
    """Verify parse sets duplicate arm name fails."""
    with pytest.raises(SchemaError, match="duplicate arm name"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "arms": [
                            {"name": "duplicate", "harness": "claude-code"},
                            {"name": "duplicate", "harness": "claude-code"},
                        ],
                    }
                }
            )
        )


def test_parse_sets_baseline_names_undeclared_arm_fails() -> None:
    """Verify parse sets baseline names undeclared arm fails."""
    with pytest.raises(SchemaError, match="baseline.*undeclared"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "baseline": "nope",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )


def test_parse_sets_unknown_harness_fails() -> None:
    """Verify parse sets unknown harness fails."""
    with pytest.raises(SchemaError, match="unknown harness"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "opus",
                        "arms": [{"name": "alpha", "harness": "cursor"}],
                    }
                }
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("runner", ["pytest"]), ("sandbox", ["microsandbox"])],
)
def test_parse_sets_backend_fields_require_strings(field: str, value: object) -> None:
    """Malformed runner and sandbox values fail as schema errors, not type errors."""
    table = _sets_table(
        {
            "default": {
                "model": "opus",
                "arms": [{"name": "alpha", "harness": "claude-code"}],
                field: value,
            }
        }
    )

    with pytest.raises(SchemaError, match=field):
        parse_sets(table)


def test_parse_sets_default_set_undeclared_fails() -> None:
    """Verify parse sets default set undeclared fails."""
    with pytest.raises(SchemaError, match="default-set.*undeclared"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "opus",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                },
                default="other",
            )
        )


def test_parse_sets_empty_arms_fails() -> None:
    """Verify parse sets empty arms fails."""
    with pytest.raises(SchemaError, match="at least one"):
        parse_sets(_sets_table({"default": {"model": "opus", "arms": []}}))


def test_parse_sets_non_dict_env_fails() -> None:
    """Verify parse sets non dict env fails."""
    with pytest.raises(SchemaError, match="env.*table"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "opus",
                        "env": "nope",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )


def test_parse_sets_set_env_values_must_be_strings() -> None:
    """Verify parse sets set env values must be strings."""
    with pytest.raises(SchemaError, match=r"env\.COUNT.*string"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "opus",
                        "env": {"COUNT": 5},
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )


def test_parse_sets_arm_env_values_must_be_strings() -> None:
    """Verify parse sets arm env values must be strings."""
    with pytest.raises(SchemaError, match=r"env\.COUNT.*string"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "opus",
                        "arms": [
                            {"name": "alpha", "harness": "claude-code", "env": {"COUNT": 5}}
                        ],
                    }
                }
            )
        )


def test_resolve_set_appends_harness_args() -> None:
    """Verify resolve set appends harness args."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "harness": "claude-code",
                    "harness_args": ["--set-flag", "set-value"],
                    "arms": [
                        {"name": "baseline"},
                        {
                            "name": "plugin",
                            "harness_args": ["--plugin-dir", "/project"],
                        },
                    ],
                }
            }
        )
    )

    resolved_set = resolve_set(rawsets, default, environ={})

    baseline, plugin = resolved_set.arms
    assert baseline.harness_args == ["--set-flag", "set-value"]
    assert plugin.harness_args == [
        "--set-flag",
        "set-value",
        "--plugin-dir",
        "/project",
    ]


def test_parse_sets_rejects_non_list_harness_args() -> None:
    """Verify parse sets rejects non list harness args."""
    with pytest.raises(SchemaError, match="harness_args.*list"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "harness": "claude-code",
                        "harness_args": "--bad",
                        "arms": [{"name": "baseline"}],
                    }
                }
            )
        )


def test_parse_sets_rejects_non_string_harness_arg_entry() -> None:
    """Verify parse sets rejects non string harness arg entry."""
    with pytest.raises(SchemaError, match="harness_args.*string"):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "harness": "claude-code",
                        "arms": [{"name": "baseline", "harness_args": ["--ok", 7]}],
                    }
                }
            )
        )


def test_expand_env_expands_and_passes_literals() -> None:
    """Verify expand env expands and passes literals."""
    out = expand_env({"BASE": "https://x", "TOK": "$MY_TOKEN"}, {"MY_TOKEN": "secret"})
    assert out == {"BASE": "https://x", "TOK": "secret"}


def test_expand_env_unset_var_raises() -> None:
    """Verify expand env unset var raises."""
    with pytest.raises(SchemaError, match="MY_TOKEN"):
        expand_env({"TOK": "$MY_TOKEN"}, {})


def test_resolve_set_inherits_defaults_and_merges_env() -> None:
    """Verify resolve set inherits defaults and merges env."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "effort": "medium",
                    "baseline": "base",
                    "env": {"A": "1", "B": "2"},
                    "arms": [
                        {"name": "base", "harness": "claude-code"},
                        {
                            "name": "trial",
                            "harness": "opencode",
                            "model": "anthropic/claude-sonnet-4-6",
                            "effort": "high",
                            "env": {"B": "override", "C": "3"},
                        },
                    ],
                }
            }
        )
    )

    resolved_set = resolve_set(rawsets, default, environ={})

    base, trial = resolved_set.arms
    assert base == Arm("base", "claude-code", "sonnet", "medium", {"A": "1", "B": "2"})
    assert trial.harness == "opencode"
    assert trial.model == "anthropic/claude-sonnet-4-6"
    assert trial.effort == "high"
    assert trial.env == {"A": "1", "B": "override", "C": "3"}  # arm keys win
    assert resolved_set.baseline == "base"


def test_resolve_set_picks_named_set() -> None:
    """Verify resolve set picks named set."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "arms": [{"name": "alpha", "harness": "claude-code"}],
                },
                "other": {
                    "model": "opus",
                    "arms": [{"name": "beta", "harness": "claude-code"}],
                },
            }
        )
    )

    resolved_set = resolve_set(rawsets, default, set_name="other", environ={})

    assert resolved_set.name == "other"
    assert resolved_set.arms[0].model == "opus"


def test_resolve_set_unknown_set_name_raises() -> None:
    """Verify resolve set unknown set name raises."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "opus",
                    "arms": [{"name": "alpha", "harness": "claude-code"}],
                }
            }
        )
    )
    with pytest.raises(SchemaError, match="no eval set named"):
        resolve_set(rawsets, default, set_name="ghost", environ={})


def test_resolve_set_scalar_overrides_replace_defaults() -> None:
    """Verify resolve set scalar overrides replace defaults."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "effort": "medium",
                    "baseline": "base",
                    "arms": [{"name": "base", "harness": "claude-code"}],
                }
            }
        )
    )

    resolved_set = resolve_set(rawsets, default, model="opus", effort="high", environ={})

    assert resolved_set.arms[0].model == "opus"
    assert resolved_set.arms[0].effort == "high"


def test_resolve_set_models_sweep_expands_one_arm_per_model() -> None:
    """Verify resolve set models sweep expands one arm per model."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "harness": "claude-code",
                    "effort": "medium",
                    "baseline": "base",
                    "arms": [{"name": "base", "harness": "claude-code"}],
                }
            }
        )
    )

    resolved_set = resolve_set(
        rawsets, default, models=["sonnet", "opus", "haiku"], environ={}
    )

    assert [arm.name for arm in resolved_set.arms] == ["sonnet", "opus", "haiku"]
    assert [arm.model for arm in resolved_set.arms] == ["sonnet", "opus", "haiku"]
    assert [arm.harness for arm in resolved_set.arms] == [
        "claude-code"
    ] * 3  # inherited from set default
    assert resolved_set.baseline == "sonnet"  # baseline = first swept value


def test_resolve_set_missing_model_raises() -> None:
    """Verify resolve set missing model raises."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "effort": "medium",
                    "arms": [{"name": "alpha", "harness": "claude-code"}],
                }
            }
        )
    )
    with pytest.raises(SchemaError, match="model"):
        resolve_set(rawsets, default, environ={})


def test_resolve_set_does_not_expand_env_at_resolve_time() -> None:
    """Verify resolve set does not expand env at resolve time."""
    # $VAR survives resolution untouched even when UNSET (lazy expansion): collection
    # must never require a secret only a deselected arm references.
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "baseline": "base",
                    "arms": [
                        {
                            "name": "base",
                            "harness": "claude-code",
                            "env": {"TOK": "$UNSET_SECRET"},
                        }
                    ],
                }
            }
        )
    )

    # no UNSET_SECRET present → must not raise
    resolved_set = resolve_set(rawsets, default, environ={})

    assert resolved_set.arms[0].env == {"TOK": "$UNSET_SECRET"}


def test_resolve_set_unknown_cli_harness_raises() -> None:
    """Verify resolve set unknown cli harness raises."""
    # The arm declares no harness, so it inherits the set default — a bogus
    # --evalspec-harness override then reaches _materialize_arm and fails fast.
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "opus",
                    "harness": "claude-code",
                        "arms": [{"name": "alpha"}],
                }
            }
        )
    )

    with pytest.raises(SchemaError, match="unknown harness"):
        resolve_set(rawsets, default, harness="cursor", environ={})


def test_resolve_set_models_sweep_sanitizes_provider_qualified_names() -> None:
    """Verify resolve set models sweep sanitizes provider qualified names."""
    # Provider-qualified model strings like "google/gemini-3.5-flash" must produce
    # filesystem-safe arm names (single path segment, no `/`) while preserving the
    # raw model string in arm.model for agent invocation.
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "harness": "claude-code",
                    "effort": "medium",
                    "baseline": "base",
                    "arms": [{"name": "base", "harness": "claude-code"}],
                }
            }
        )
    )

    models = ["google/gemini-3.5-flash", "anthropic/claude"]
    resolved_set = resolve_set(rawsets, default, models=models, environ={})

    assert len(resolved_set.arms) == 2
    # arm.name must be a single, filesystem-safe path segment
    for arm in resolved_set.arms:
        assert "/" not in arm.name
        assert arm.name  # non-empty
    # safe names produced by sanitization
    assert resolved_set.arms[0].name == "google-gemini-3.5-flash"
    assert resolved_set.arms[1].name == "anthropic-claude"
    # raw model string preserved for agent invocation
    assert [arm.model for arm in resolved_set.arms] == models
    # names are unique
    assert len({arm.name for arm in resolved_set.arms}) == len(resolved_set.arms)
    # baseline references the safe name of the first model
    assert resolved_set.baseline == "google-gemini-3.5-flash"


def test_resolve_set_models_sweep_dedupes_sanitized_name_collisions() -> None:
    """Verify resolve set models sweep dedupes sanitized name collisions."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "harness": "claude-code",
                    "arms": [{"name": "base"}],
                }
            }
        )
    )

    resolved_set = resolve_set(rawsets, default, models=["a", "a-2", "a"], environ={})

    assert [arm.name for arm in resolved_set.arms] == ["a", "a-2", "a-3"]
    assert len({arm.name for arm in resolved_set.arms}) == len(resolved_set.arms)


def test_parse_sets_unsupported_runner_fails() -> None:
    """An unsupported runner fails fast naming the set, field, and supported values."""
    with pytest.raises(
        SchemaError, match=r"tool\.evalspec\.sets\.default.*runner.*jest.*pytest"
    ):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "runner": "jest",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )


def test_parse_sets_unsupported_sandbox_fails_naming_set() -> None:
    """An unsupported sandbox (docker) fails fast naming Docker and the offending set."""
    with pytest.raises(
        SchemaError, match=r"tool\.evalspec\.sets\.default.*docker.*not implemented"
    ):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "sandbox": "docker",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )


def test_resolve_set_defaults_runner_and_sandbox() -> None:
    """Omitted runner/sandbox default to pytest/microsandbox on the resolved Set."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "baseline": "baseline",
                    "arms": [{"name": "baseline", "harness": "claude-code"}],
                }
            }
        )
    )

    resolved = resolve_set(rawsets, default)

    assert resolved.runner == "pytest"
    assert resolved.sandbox == "microsandbox"


def test_resolve_set_carries_declared_runner_and_sandbox() -> None:
    """Declared runner/sandbox values reach the resolved Set."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "runner": "pytest",
                    "sandbox": "microsandbox",
                    "baseline": "baseline",
                    "arms": [{"name": "baseline", "harness": "claude-code"}],
                }
            }
        )
    )

    resolved = resolve_set(rawsets, default)

    assert resolved.runner == "pytest"
    assert resolved.sandbox == "microsandbox"


def test_two_backends_fixture_fails_whole_file() -> None:
    """The fail-fast fixture: the docker set makes the whole file fail to parse."""
    raw = tomllib.loads(
        Path("tests/fixtures/sandbox/two-backends.toml").read_text(encoding="utf-8")
    )

    with pytest.raises(SchemaError, match="docker.*not implemented"):
        parse_sets(raw["tool"]["evalspec"])


def test_microsandbox_fixture_parses_and_resolves() -> None:
    """The accept fixture: parses cleanly and resolves to a microsandbox set."""
    raw = tomllib.loads(
        Path("tests/fixtures/sandbox/microsandbox.toml").read_text(encoding="utf-8")
    )

    rawsets, default = parse_sets(raw["tool"]["evalspec"])
    resolved = resolve_set(rawsets, default)

    assert resolved.sandbox == "microsandbox"
