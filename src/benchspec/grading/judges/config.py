"""JudgeConfig: the run-level, harness-independent judge configuration.

Resolved once per run from four layers (CLI > scratch --benchspec-config >
pyproject.toml [tool.benchspec.judge] > built-in defaults — see resolve_judge_config).
Structural validation (field types, known harness, reserved harness_args) happens at
resolve time, safe to run under `--collect-only`. Binary-on-PATH is a separate, later
check (judges.registry.preflight_verify_judge_binary) that only runs when tests actually
execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchspec.agents import provider_error
from benchspec.agents.base import DEFAULT_PROVIDER
from benchspec.agents.claude import _validate_harness_args as _validate_claude_code_harness_args
from benchspec.agents.codex import _validate_harness_args as _validate_codex_harness_args
from benchspec.agents.opencode import _validate_harness_args as _validate_opencode_harness_args
from benchspec.grading.judges.registry import known_judge_harnesses
from benchspec.specs.schema import SchemaError

DEFAULT_HARNESS = "claude-code"
DEFAULT_MODEL = "sonnet"
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 300


def _validated_non_empty_str(where: str, key: str, value: object) -> str:
    """Return value when it is a non-empty string, else raise SchemaError."""
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{where}: `{key}` must be a non-empty string")
    return value


def _validated_positive_int(where: str, key: str, value: object) -> int:
    """Return value when it is a positive integer (bool excluded), else raise SchemaError."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchemaError(f"{where}: `{key}` must be a positive integer")
    return value


def _validated_str_list(where: str, key: str, value: object) -> list[str]:
    """Return value when it is a list of strings, else raise SchemaError."""
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise SchemaError(f"{where}: `{key}` must be a list of strings")
    return value


def _validated_str_dict(where: str, key: str, value: object) -> dict[str, str]:
    """Return value when it is a table with string values, else raise SchemaError."""
    if not isinstance(value, dict) or not all(
        isinstance(entry, str) for entry in value.values()
    ):
        raise SchemaError(f"{where}: `{key}` must be a table of strings")
    return value


# Each judge field maps to the validator that both checks its type and returns the
# cleaned value; the keys double as the set of recognized judge keys.
_FIELD_VALIDATORS = {
    "harness": _validated_non_empty_str,
    "provider": _validated_non_empty_str,
    "model": _validated_non_empty_str,
    "effort": _validated_non_empty_str,
    "timeout": _validated_positive_int,
    "harness_args": _validated_str_list,
    "env": _validated_str_dict,
}


@dataclass(frozen=True)
class JudgeConfig:
    """The resolved, run-level judge: which harness grades.

    With what model/effort/timeout/pass-through args/env, independent from every task
    arm's own harness/model/effort/env — resolving this must never mutate or read arm
    config.
    """

    harness: str = DEFAULT_HARNESS
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    timeout: int = DEFAULT_TIMEOUT
    harness_args: list[str] = field(default_factory=list, hash=False)
    env: dict[str, str] = field(default_factory=dict, hash=False)
    provider: str = DEFAULT_PROVIDER


def _validate_judge_table(where: str, table: dict) -> dict:
    """Validate one layer's [tool.benchspec.judge]-shaped dict.

    Return only the keys it declared (partial — callers merge over prior layers).
    Raises SchemaError naming the defect, never a silent no-op.
    """
    unknown = sorted(set(table) - set(_FIELD_VALIDATORS))
    if unknown:
        raise SchemaError(
            f"{where}: unknown judge key(s) {unknown} (known: {list(_FIELD_VALIDATORS)})"
        )
    return {
        key: validate(where, key, table[key])
        for key, validate in _FIELD_VALIDATORS.items()
        if key in table
    }


_HARNESS_ARG_VALIDATORS = {
    "claude-code": _validate_claude_code_harness_args,
    "codex": _validate_codex_harness_args,
    "opencode": _validate_opencode_harness_args,
}


def _preflight_judge_config(config: JudgeConfig) -> None:
    """Structural, environment-free checks — safe to run under `--collect-only`.

    Also safe on a host with no judge CLI installed. Binary-on-PATH is a separate,
    later check (judges.registry.preflight_verify_judge_binary) run only when tests actually
    execute.
    """
    if config.harness not in known_judge_harnesses():
        raise SchemaError(
            f"[tool.benchspec.judge] harness `{config.harness}` is not a supported "
            f"judge harness (known: {sorted(known_judge_harnesses())})"
        )
    if (error := provider_error(config.harness, config.provider)) is not None:
        raise SchemaError(f"[tool.benchspec.judge] {error}")
    try:
        _HARNESS_ARG_VALIDATORS[config.harness](config.harness_args)
    except ValueError as error:
        raise SchemaError(
            f"[tool.benchspec.judge] harness_args invalid for `{config.harness}`: {error}"
        ) from error
    if config.harness == "opencode" and "/" not in config.model:
        raise SchemaError(
            "judge harness `opencode` needs a provider-qualified model "
            f"(e.g. 'anthropic/claude-sonnet-4-6'), got `{config.model}`"
        )


def resolve_judge_config(
    *, pyproject_table: dict | None = None, scratch_table: dict | None = None,
    cli_table: dict | None = None,
) -> JudgeConfig:
    """Resolve the run's one JudgeConfig from four layers, per field.

    CLI override > scratch --benchspec-config > pyproject [tool.benchspec.judge] >
    built-in defaults. `env` shallow-merges across layers (later layer's keys win);
    every other field fully replaces. Raises SchemaError on any structural defect —
    the pytest plugin turns that into a pytest.UsageError at collection time, before
    any paid task arm runs.
    """
    resolved: dict = {
        "harness": DEFAULT_HARNESS, "model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT,
        "timeout": DEFAULT_TIMEOUT, "harness_args": [], "env": {},
        "provider": DEFAULT_PROVIDER,
    }
    for label, table in (
        ("[tool.benchspec.judge]", pyproject_table),
        ("--benchspec-config [tool.benchspec.judge]", scratch_table),
        ("--benchspec-judge-* CLI overrides", cli_table),
    ):
        if not table:
            continue
        validated = _validate_judge_table(label, table)
        if "env" in validated:
            validated["env"] = {**resolved["env"], **validated["env"]}
        resolved.update(validated)

    config = JudgeConfig(**resolved)
    _preflight_judge_config(config)
    return config
