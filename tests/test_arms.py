from __future__ import annotations

import pytest

from evalspec.arms import (
    Arm,
    expand_env,
    parse_sets,
    resolve_set,
)
from evalspec.schema import SchemaError


def _sets_table(sets, default="default", **extra):
    return {"sets": sets, "default-set": default, **extra}


def test_parse_sets_basic_default_and_baseline():
    rawsets, default = parse_sets(_sets_table({
        "default": {
            "model": "sonnet", "effort": "medium", "baseline": "baseline",
            "arms": [
                {"name": "baseline", "harness": "claude-code"},
                {"name": "trial", "harness": "claude-code"},
            ],
        },
    }))

    assert default == "default"
    assert set(rawsets) == {"default"}
    rs = rawsets["default"]
    assert rs.baseline == "baseline"
    assert [a["name"] for a in rs.raw_arms] == ["baseline", "trial"]
    assert rs.defaults["model"] == "sonnet"


def test_parse_sets_rejects_legacy_flat_config():
    # No [tool.evalspec.sets.*] but the old flat shape present → fail-fast pointer.
    with pytest.raises(SchemaError, match="eval set"):
        parse_sets({"arms": [{"name": "x", "harness": "claude-code", "model": "opus"}],
                    "reference": "x"})


def test_parse_sets_missing_sets_fails():
    with pytest.raises(SchemaError, match="eval set"):
        parse_sets({})


def test_parse_sets_duplicate_arm_name_fails():
    with pytest.raises(SchemaError, match="duplicate arm name"):
        parse_sets(_sets_table({"default": {"model": "sonnet", "arms": [
            {"name": "a", "harness": "claude-code"},
            {"name": "a", "harness": "claude-code"},
        ]}}))


def test_parse_sets_baseline_names_undeclared_arm_fails():
    with pytest.raises(SchemaError, match="baseline.*undeclared"):
        parse_sets(_sets_table({"default": {"model": "sonnet", "baseline": "nope",
            "arms": [{"name": "a", "harness": "claude-code"}]}}))


def test_parse_sets_unknown_harness_fails():
    with pytest.raises(SchemaError, match="unknown harness"):
        parse_sets(_sets_table({"default": {"model": "opus",
            "arms": [{"name": "a", "harness": "cursor"}]}}))


def test_parse_sets_default_set_undeclared_fails():
    with pytest.raises(SchemaError, match="default-set.*undeclared"):
        parse_sets(_sets_table({"default": {"model": "opus",
            "arms": [{"name": "a", "harness": "claude-code"}]}}, default="other"))


def test_parse_sets_empty_arms_fails():
    with pytest.raises(SchemaError, match="at least one"):
        parse_sets(_sets_table({"default": {"model": "opus", "arms": []}}))


def test_parse_sets_non_dict_env_fails():
    with pytest.raises(SchemaError, match="env.*table"):
        parse_sets(_sets_table({"default": {"model": "opus", "env": "nope",
            "arms": [{"name": "a", "harness": "claude-code"}]}}))


def test_parse_sets_set_env_values_must_be_strings():
    with pytest.raises(SchemaError, match=r"env\.COUNT.*string"):
        parse_sets(_sets_table({"default": {
            "model": "opus", "env": {"COUNT": 5},
            "arms": [{"name": "a", "harness": "claude-code"}],
        }}))


def test_parse_sets_arm_env_values_must_be_strings():
    with pytest.raises(SchemaError, match=r"env\.COUNT.*string"):
        parse_sets(_sets_table({"default": {"model": "opus",
            "arms": [{"name": "a", "harness": "claude-code", "env": {"COUNT": 5}}]}}))


def test_resolve_set_appends_harness_args():
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet",
        "harness": "claude-code",
        "harness_args": ["--set-flag", "set-value"],
        "arms": [
            {"name": "baseline"},
            {"name": "plugin", "harness_args": ["--plugin-dir", "/project"]},
        ],
    }}))

    s = resolve_set(rawsets, default, environ={})

    baseline, plugin = s.arms
    assert baseline.harness_args == ["--set-flag", "set-value"]
    assert plugin.harness_args == [
        "--set-flag", "set-value",
        "--plugin-dir", "/project",
    ]


def test_parse_sets_rejects_non_list_harness_args():
    with pytest.raises(SchemaError, match="harness_args.*list"):
        parse_sets(_sets_table({"default": {
            "model": "sonnet",
            "harness": "claude-code",
            "harness_args": "--bad",
            "arms": [{"name": "baseline"}],
        }}))


def test_parse_sets_rejects_non_string_harness_arg_entry():
    with pytest.raises(SchemaError, match="harness_args.*string"):
        parse_sets(_sets_table({"default": {
            "model": "sonnet",
            "harness": "claude-code",
            "arms": [{"name": "baseline", "harness_args": ["--ok", 7]}],
        }}))


def test_expand_env_expands_and_passes_literals():
    out = expand_env({"BASE": "https://x", "TOK": "$MY_TOKEN"}, {"MY_TOKEN": "secret"})
    assert out == {"BASE": "https://x", "TOK": "secret"}


def test_expand_env_unset_var_raises():
    with pytest.raises(SchemaError, match="MY_TOKEN"):
        expand_env({"TOK": "$MY_TOKEN"}, {})


def test_resolve_set_inherits_defaults_and_merges_env():
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "effort": "medium", "baseline": "base",
        "env": {"A": "1", "B": "2"},
        "arms": [
            {"name": "base", "harness": "claude-code"},
            {"name": "trial", "harness": "opencode", "model": "anthropic/claude-sonnet-4-6",
             "effort": "high", "env": {"B": "override", "C": "3"}},
        ],
    }}))

    s = resolve_set(rawsets, default, environ={})

    base, trial = s.arms
    assert base == Arm("base", "claude-code", "sonnet", "medium", {"A": "1", "B": "2"})
    assert trial.harness == "opencode"
    assert trial.model == "anthropic/claude-sonnet-4-6"
    assert trial.effort == "high"
    assert trial.env == {"A": "1", "B": "override", "C": "3"}  # arm keys win
    assert s.baseline == "base"


def test_resolve_set_picks_named_set():
    rawsets, default = parse_sets(_sets_table({
        "default": {"model": "sonnet", "arms": [{"name": "a", "harness": "claude-code"}]},
        "other": {"model": "opus", "arms": [{"name": "b", "harness": "claude-code"}]},
    }))

    s = resolve_set(rawsets, default, set_name="other", environ={})

    assert s.name == "other"
    assert s.arms[0].model == "opus"


def test_resolve_set_unknown_set_name_raises():
    rawsets, default = parse_sets(_sets_table({"default": {"model": "opus",
        "arms": [{"name": "a", "harness": "claude-code"}]}}))
    with pytest.raises(SchemaError, match="no eval set named"):
        resolve_set(rawsets, default, set_name="ghost", environ={})


def test_resolve_set_scalar_overrides_replace_defaults():
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "effort": "medium", "baseline": "base",
        "arms": [{"name": "base", "harness": "claude-code"}],
    }}))

    s = resolve_set(rawsets, default, model="opus", effort="high", environ={})

    assert s.arms[0].model == "opus"
    assert s.arms[0].effort == "high"


def test_resolve_set_models_sweep_expands_one_arm_per_model():
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "harness": "claude-code", "effort": "medium", "baseline": "base",
        "arms": [{"name": "base", "harness": "claude-code"}],
    }}))

    s = resolve_set(rawsets, default, models=["sonnet", "opus", "haiku"], environ={})

    assert [a.name for a in s.arms] == ["sonnet", "opus", "haiku"]
    assert [a.model for a in s.arms] == ["sonnet", "opus", "haiku"]
    assert [a.harness for a in s.arms] == ["claude-code"] * 3  # inherited from set default
    assert s.baseline == "sonnet"  # baseline = first swept value


def test_resolve_set_missing_model_raises():
    rawsets, default = parse_sets(_sets_table({"default": {
        "effort": "medium", "arms": [{"name": "a", "harness": "claude-code"}]}}))
    with pytest.raises(SchemaError, match="model"):
        resolve_set(rawsets, default, environ={})


def test_resolve_set_does_not_expand_env_at_resolve_time():
    # $VAR survives resolution untouched even when UNSET (lazy expansion): collection
    # must never require a secret only a deselected arm references.
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "baseline": "base",
        "arms": [{"name": "base", "harness": "claude-code",
                  "env": {"TOK": "$UNSET_SECRET"}}],
    }}))

    s = resolve_set(rawsets, default, environ={})  # no UNSET_SECRET present → must not raise

    assert s.arms[0].env == {"TOK": "$UNSET_SECRET"}


def test_resolve_set_unknown_cli_harness_raises():
    # The arm declares no harness, so it inherits the set default — a bogus
    # --evalspec-harness override then reaches _materialize_arm and fails fast.
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "opus", "harness": "claude-code",
        "arms": [{"name": "a"}]}}))

    with pytest.raises(SchemaError, match="unknown harness"):
        resolve_set(rawsets, default, harness="cursor", environ={})


def test_resolve_set_models_sweep_sanitizes_provider_qualified_names():
    # Provider-qualified model strings like "google/gemini-3.5-flash" must produce
    # filesystem-safe arm names (single path segment, no `/`) while preserving the
    # raw model string in arm.model for agent invocation.
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "harness": "claude-code", "effort": "medium", "baseline": "base",
        "arms": [{"name": "base", "harness": "claude-code"}],
    }}))

    models = ["google/gemini-3.5-flash", "anthropic/claude"]
    s = resolve_set(rawsets, default, models=models, environ={})

    assert len(s.arms) == 2
    # arm.name must be a single, filesystem-safe path segment
    for arm in s.arms:
        assert "/" not in arm.name
        assert arm.name  # non-empty
    # safe names produced by sanitization
    assert s.arms[0].name == "google-gemini-3.5-flash"
    assert s.arms[1].name == "anthropic-claude"
    # raw model string preserved for agent invocation
    assert [a.model for a in s.arms] == models
    # names are unique
    assert len({a.name for a in s.arms}) == len(s.arms)
    # baseline references the safe name of the first model
    assert s.baseline == "google-gemini-3.5-flash"


def test_resolve_set_models_sweep_dedupes_sanitized_name_collisions():
    rawsets, default = parse_sets(_sets_table({"default": {
        "model": "sonnet", "harness": "claude-code",
        "arms": [{"name": "base"}],
    }}))

    s = resolve_set(rawsets, default, models=["a", "a-2", "a"], environ={})

    assert [a.name for a in s.arms] == ["a", "a-2", "a-3"]
    assert len({a.name for a in s.arms}) == len(s.arms)
