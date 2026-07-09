"""JudgeConfig: the run-level, harness-independent judge configuration.

Resolved once per run from four layers (CLI > scratch --evalspec-config >
pyproject.toml [tool.evalspec.judge] > built-in defaults — see resolve_judge_config).
Structural validation (field types, known harness, reserved harness_args) happens at
resolve time, safe to run under `--collect-only`. Binary-on-PATH is a separate, later
check (judges.registry.preflight_judge_binary) that only runs when tests actually
execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evalspec.agents.claude import _validate_harness_args as _validate_claude_code_harness_args
from evalspec.agents.codex import _validate_harness_args as _validate_codex_harness_args
from evalspec.agents.opencode import _validate_harness_args as _validate_opencode_harness_args
from evalspec.judges.registry import known_judge_harnesses
from evalspec.schema import SchemaError

DEFAULT_HARNESS = "claude-code"
DEFAULT_MODEL = "sonnet"
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 300

_JUDGE_KEYS = ("harness", "model", "effort", "timeout", "harness_args", "env")


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
    env: dict = field(default_factory=dict, hash=False)


def _validate_judge_table(where: str, table: dict) -> dict:
    """Validate one layer's [tool.evalspec.judge]-shaped dict.

    Return only the keys it declared (partial — callers merge over prior layers).
    Raises SchemaError naming the defect, never a silent no-op.
    """
    out: dict = {}
    if "harness" in table:
        value = table["harness"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `harness` must be a non-empty string")
        out["harness"] = value
    if "model" in table:
        value = table["model"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `model` must be a non-empty string")
        out["model"] = value
    if "effort" in table:
        value = table["effort"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `effort` must be a non-empty string")
        out["effort"] = value
    if "timeout" in table:
        value = table["timeout"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SchemaError(f"{where}: `timeout` must be a positive integer")
        out["timeout"] = value
    if "harness_args" in table:
        value = table["harness_args"]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SchemaError(f"{where}: `harness_args` must be a list of strings")
        out["harness_args"] = value
    if "env" in table:
        value = table["env"]
        if not isinstance(value, dict) or not all(
            isinstance(item, str) for item in value.values()
        ):
            raise SchemaError(f"{where}: `env` must be a table of strings")
        out["env"] = value
    unknown = sorted(set(table) - set(_JUDGE_KEYS))
    if unknown:
        raise SchemaError(f"{where}: unknown judge key(s) {unknown} (known: {list(_JUDGE_KEYS)})")
    return out


_HARNESS_ARG_VALIDATORS = {
    "claude-code": _validate_claude_code_harness_args,
    "codex": _validate_codex_harness_args,
    "opencode": _validate_opencode_harness_args,
}


def _preflight_judge_config(config: JudgeConfig) -> None:
    """Structural, environment-free checks — safe to run under `--collect-only`.

    Also safe on a host with no judge CLI installed. Binary-on-PATH is a separate,
    later check (judges.registry.preflight_judge_binary) run only when tests actually
    execute.
    """
    if config.harness not in known_judge_harnesses():
        raise SchemaError(
            f"[tool.evalspec.judge] harness `{config.harness}` is not a supported "
            f"judge harness (known: {sorted(known_judge_harnesses())})"
        )
    try:
        _HARNESS_ARG_VALIDATORS[config.harness](config.harness_args)
    except ValueError as e:
        raise SchemaError(
            f"[tool.evalspec.judge] harness_args invalid for `{config.harness}`: {e}"
        ) from e
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

    CLI override > scratch --evalspec-config > pyproject [tool.evalspec.judge] >
    built-in defaults. `env` shallow-merges across layers (later layer's keys win);
    every other field fully replaces. Raises SchemaError on any structural defect —
    callers (plugin.py) turn that into a pytest.UsageError at collection time, before
    any paid task arm runs.
    """
    resolved: dict = {
        "harness": DEFAULT_HARNESS, "model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT,
        "timeout": DEFAULT_TIMEOUT, "harness_args": [], "env": {},
    }
    for label, table in (
        ("[tool.evalspec.judge]", pyproject_table),
        ("--evalspec-config [tool.evalspec.judge]", scratch_table),
        ("--evalspec-judge-* CLI overrides", cli_table),
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
