"""Parse harnessbench config into typed eval sets/arms and resolve a run.

An arm is a `harness×model` cell — plus its own `effort` and `env` — carried verbatim
into one `(eval × arm)` test. Arms live in a named set (`[tool.harnessbench.sets.<name>]`):
the set adds harness/model/effort/env defaults (inherited by arms that omit a key) and a
`baseline` (the arm every other arm's Δ is measured against). `env` `$VAR`s expand
lazily at exec time, not resolve time. Validation is fail-fast at config-read time — a
bad table raises SchemaError naming the defect, not a silent no-op mid-run.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from harnessbench.agents import known_harnesses
from harnessbench.sandbox.backend import DEFAULT_SANDBOX, resolve_sandbox
from harnessbench.specs.schema import SchemaError


@dataclass(frozen=True)
class Arm:
    """Describe one runnable harness/model arm."""

    name: str
    harness: str
    model: str
    effort: str = "medium"
    env: dict = field(default_factory=dict, hash=False)  # dict is unhashable; keep Arm hashable
    harness_args: list[str] = field(default_factory=list, hash=False)


_SET_DEFAULT_KEYS = ("harness", "model", "effort", "env", "harness_args")
_VAR = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")
_DEFAULT_EFFORT = "medium"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

SUPPORTED_RUNNERS = {"pytest"}
_DEFAULT_RUNNER = "pytest"


@dataclass(frozen=True)
class Set:
    """Describe one named eval set and its arms."""

    name: str
    arms: list[Arm]
    baseline: str | None
    runner: str = _DEFAULT_RUNNER
    sandbox: str = DEFAULT_SANDBOX


@dataclass(frozen=True)
class RawSet:
    """Store raw set data."""

    name: str
    defaults: dict
    raw_arms: list[dict]
    baseline: str | None
    runner: str = _DEFAULT_RUNNER
    sandbox: str = DEFAULT_SANDBOX


def _arm_name_from_model(model: str) -> str:
    """Derive a stable arm name from a model identifier."""
    return _UNSAFE_NAME_CHARS.sub("-", model)


def _validate_harness_args(where: str, value: object) -> list[str]:
    """Validate harness argument strings from configuration."""
    if not isinstance(value, list):
        raise SchemaError(f"{where}: `harness_args` must be a list")
    for arg_index, item in enumerate(value):
        if not isinstance(item, str):
            raise SchemaError(f"{where}: `harness_args[{arg_index}]` must be a string")
    return value


def _validate_env_table(where: str, value: object) -> None:
    """Validate environment variable overrides from configuration."""
    if not isinstance(value, dict):
        raise SchemaError(f"{where}: `env` must be a table")
    for key, item in value.items():
        if not isinstance(item, str):
            raise SchemaError(f"{where}: `env.{key}` must be a string")


def parse_sets(table: dict) -> tuple[dict[str, RawSet], str]:
    """Parse eval set definitions from pyproject configuration."""
    sets_table = table.get("sets")
    if not sets_table or not isinstance(sets_table, dict):
        raise SchemaError(
            "[tool.harnessbench] needs at least one eval set "
            "([tool.harnessbench.sets.<name>] with `arms`, optional set-level "
            "harness/model/effort/env defaults, and a `baseline`). The flat "
            "[[tool.harnessbench.arms]] + `reference` shape is no longer supported — "
            "wrap your arms in a named set and add `default-set`."
        )
    known = known_harnesses()
    rawsets: dict[str, RawSet] = {}
    for set_name, body in sets_table.items():
        where = f"[tool.harnessbench.sets.{set_name}]"
        if not isinstance(body, dict):
            raise SchemaError(f"{where}: expected a table")
        defaults = {key: body[key] for key in _SET_DEFAULT_KEYS if key in body}
        if "env" in defaults:
            _validate_env_table(f"{where}: set-level `env`", defaults["env"])
        if "harness_args" in defaults:
            defaults["harness_args"] = _validate_harness_args(
                f"{where}: set-level `harness_args`",
                defaults["harness_args"],
            )
        raw_arms = body.get("arms")
        if not raw_arms or not isinstance(raw_arms, list):
            raise SchemaError(f"{where}: needs at least one arm in `arms`")

        seen: set[str] = set()

        for arm_index, entry in enumerate(raw_arms):
            at = f"{where} arms[{arm_index}]"

            if not isinstance(entry, dict):
                raise SchemaError(f"{at}: expected a table")

            name = entry.get("name")
            if not (isinstance(name, str) and name):
                raise SchemaError(f"{at}: missing or non-string `name`")
            if name in seen:
                raise SchemaError(f"{at}: duplicate arm name `{name}`")
            seen.add(name)

            harness = entry.get("harness", defaults.get("harness"))
            if not (isinstance(harness, str) and harness):
                raise SchemaError(f"{at}: missing `harness` (no arm value, no set default)")
            if harness not in known:
                raise SchemaError(f"{at}: unknown harness `{harness}` (known: {sorted(known)})")

            if "env" in entry:
                _validate_env_table(f"{at}: arm-level `env`", entry["env"])
            if "harness_args" in entry:
                _validate_harness_args(f"{at}: arm-level `harness_args`", entry["harness_args"])

        baseline = body.get("baseline")
        if baseline is not None:
            if not isinstance(baseline, str):
                raise SchemaError(f"{where}: baseline must be a string")
            if baseline not in seen:
                raise SchemaError(
                    f"{where}: baseline `{baseline}` names an undeclared arm "
                    f"(declared: {sorted(seen)})"
                )

        runner = body.get("runner", _DEFAULT_RUNNER)
        # TOML permits arrays/tables here; a non-string would raise an unhashable-key
        # TypeError on the membership check below instead of the documented exit-2 diagnostic.
        if not isinstance(runner, str) or not runner:
            raise SchemaError(f"{where}: runner must be a non-empty string")
        if runner not in SUPPORTED_RUNNERS:
            raise SchemaError(
                f"{where}: unsupported runner `{runner}` (supported: {sorted(SUPPORTED_RUNNERS)})"
            )

        sandbox_name = body.get("sandbox", DEFAULT_SANDBOX)
        if not isinstance(sandbox_name, str) or not sandbox_name:
            raise SchemaError(f"{where}: sandbox must be a non-empty string")
        # resolve_sandbox raises SchemaError on docker/unknown with the specific
        # "not implemented" wording; re-raise with the set's `where` prefix so the
        # message names the offending [tool.harnessbench.sets.<name>] (spec example).
        try:
            resolve_sandbox(sandbox_name)
        except SchemaError as err:
            raise SchemaError(f"{where}: {err}") from None

        rawsets[set_name] = RawSet(
            set_name, defaults, raw_arms, baseline, runner=runner, sandbox=sandbox_name
        )

    default_set = table.get("default-set")
    if not isinstance(default_set, str) or not default_set:
        raise SchemaError("[tool.harnessbench] needs a `default-set` naming a declared set")
    if default_set not in rawsets:
        raise SchemaError(
            f"[tool.harnessbench] default-set `{default_set}` names an undeclared set "
            f"(declared: {sorted(rawsets)})"
        )
    return rawsets, default_set


def expand_env(env: dict, environ: Mapping) -> dict:
    """Expand a `$VAR`/`${VAR}` env value from `environ`; literals pass through.

    An unset referenced var raises SchemaError (never a silent empty string).
    """
    out: dict[str, str] = {}
    for key, val in env.items():
        if not isinstance(val, str):
            raise SchemaError(f"env `{key}` must be a string, got {type(val).__name__}")
        match = _VAR.match(val)
        if match:
            name = match.group(1) or match.group(2)
            if name not in environ:
                raise SchemaError(
                    f"env `{key}` references ${name}, which is unset in the environment"
                )
            out[key] = environ[name]
        else:
            out[key] = val
    return out


def _materialize_arm(name: str, raw: dict, defaults: dict, where: str) -> Arm:
    """Build a concrete arm config from raw arm settings."""
    harness = raw.get("harness", defaults.get("harness"))
    if not harness:
        raise SchemaError(f"{where} arm `{name}`: no `harness` (no arm value, no set default)")
    if harness not in known_harnesses():
        raise SchemaError(f"{where} arm `{name}`: unknown harness `{harness}`")
    model = raw.get("model", defaults.get("model"))
    if not (isinstance(model, str) and model):
        raise SchemaError(f"{where} arm `{name}`: no `model` (no arm value, no set default)")
    effort = raw.get("effort", defaults.get("effort", _DEFAULT_EFFORT))
    merged = {**defaults.get("env", {}), **raw.get("env", {})}
    harness_args = [*defaults.get("harness_args", []), *raw.get("harness_args", [])]

    # Env stays unexpanded until the arm executes.
    return Arm(name, harness, model, effort, merged, harness_args)


def resolve_set(
    rawsets: dict,
    default_set: str,
    *,
    set_name: str | None = None,
    model: str | None = None,
    harness: str | None = None,
    effort: str | None = None,
    env: dict | None = None,
    models: list | None = None,
    environ: Mapping | None = None,
) -> Set:
    """Resolve one eval set into concrete arms."""
    environ = environ if environ is not None else os.environ
    chosen = set_name if set_name is not None else default_set
    rs = rawsets.get(chosen)
    if rs is None:
        raise SchemaError(f"no eval set named `{chosen}` (declared: {sorted(rawsets)})")
    where = f"[tool.harnessbench.sets.{rs.name}]"

    defaults = dict(rs.defaults)
    if model is not None:
        defaults["model"] = model
    if harness is not None:
        defaults["harness"] = harness
    if effort is not None:
        defaults["effort"] = effort
    if env:
        defaults["env"] = {**defaults.get("env", {}), **env}

    if models:
        safe = [_arm_name_from_model(model) for model in models]
        used: set[str] = set()
        unique: list[str] = []

        for safe_name in safe:
            candidate = safe_name
            suffix = 2
            while candidate in used:
                candidate = f"{safe_name}-{suffix}"
                suffix += 1
            used.add(candidate)
            unique.append(candidate)
        arms = [
            _materialize_arm(safe_name, {"name": safe_name, "model": model}, defaults, where)
            for safe_name, model in zip(unique, models, strict=False)
        ]
        return Set(rs.name, arms, baseline=unique[0], runner=rs.runner, sandbox=rs.sandbox)

    arms = [_materialize_arm(raw_arm["name"], raw_arm, defaults, where) for raw_arm in rs.raw_arms]
    return Set(rs.name, arms, baseline=rs.baseline, runner=rs.runner, sandbox=rs.sandbox)
