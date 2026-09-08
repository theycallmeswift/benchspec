"""Resolve the run's eval set and judge config from pyproject, scratch config, and CLI overrides."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from benchspec.config.arms import Set as EvalSet
from benchspec.config.arms import parse_sets, resolve_set
from benchspec.config.options import (
    RunOptions,
    option_int,
    option_str,
    option_str_list,
)
from benchspec.grading.judges import JudgeConfig, resolve_judge_config
from benchspec.specs.discovery import (
    discover_eval_cases,
    pyproject_table,
    resolve_eval_paths,
    resolve_repo_root,
)
from benchspec.specs.schema import SchemaError


def _parse_env_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse KEY=VALUE environment overrides from CLI options."""
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise pytest.UsageError(f"--benchspec-env expects KEY=VAL, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key] = value
    return out


def _read_scratch_benchspec_table(config_path: str | None) -> dict:
    """The raw `[tool.benchspec]` table from an untracked --benchspec-config scratch file.

    Or {} if none was passed. Shared by `_layer_config_sets` (sets/default-set)
    and `resolved_judge_config` ([tool.benchspec.judge]) so there's one TOML read and
    one error-reporting path for a malformed scratch file.
    """
    if not config_path:
        return {}
    try:
        with Path(config_path).open("rb") as config_file:
            raw = tomllib.load(config_file)
    except OSError as err:
        raise pytest.UsageError(f"--benchspec-config {config_path}: {err}") from None
    except UnicodeDecodeError as err:
        raise pytest.UsageError(f"--benchspec-config {config_path}: {err}") from None
    except tomllib.TOMLDecodeError as err:
        raise pytest.UsageError(f"--benchspec-config {config_path}: {err}") from None
    scratch = raw.get("tool", {}).get("benchspec")
    if not isinstance(scratch, dict):
        raise pytest.UsageError(
            f"--benchspec-config {config_path}: expected a [tool.benchspec] table "
            "with [tool.benchspec.sets.<name>] and/or [tool.benchspec.judge]"
        )
    return scratch


def _layer_config_sets(table: dict, config_path: str | None) -> dict:
    """Merge a scratch --benchspec-config file's [tool.benchspec.sets.*] over pyproject's.

    The scratch file uses one shape — a `[tool.benchspec]` table, like pyproject: a file
    missing it errors rather than mixing its top-level keys into the sets table.
    """
    scratch = _read_scratch_benchspec_table(config_path)
    if not scratch:
        return table
    merged = dict(table)
    merged["sets"] = {**table.get("sets", {}), **scratch.get("sets", {})}
    if scratch.get("default-set"):
        merged["default-set"] = scratch["default-set"]
    return merged


def resolved_run_set(config: RunOptions) -> EvalSet:
    """The single eval set this run uses — uniform columns across every skill.

    Layers `--benchspec-config` over pyproject, selects `--benchspec-set` (else default-
    set), applies scalar/sweep CLI overrides. A malformed or unknown set fails fast as a
    UsageError at collection.
    """
    repo_root = resolve_repo_root(config)
    table = _layer_config_sets(pyproject_table(repo_root), option_str(config, "benchspec_config"))
    raw_models = option_str(config, "benchspec_models")
    models = (
        [model.strip() for model in raw_models.split(",") if model.strip()]
        if raw_models
        else None
    )
    try:
        rawsets, default_set = parse_sets(table)
        return resolve_set(
            rawsets,
            default_set,
            set_name=option_str(config, "benchspec_set"),
            model=option_str(config, "benchspec_model"),
            harness=option_str(config, "benchspec_harness"),
            effort=option_str(config, "benchspec_effort"),
            env=_parse_env_pairs(option_str_list(config, "benchspec_env")),
            models=models,
        )
    except SchemaError as error:
        raise pytest.UsageError(str(error)) from None


def has_configured_set(config: RunOptions) -> bool:
    """True when the layered config declares a `[tool.benchspec.sets.*]` table.

    This is the collection-time fact — an eval set is configured — not whether output
    artifacts landed. A run whose every arm errored before writing its `eval-*` dir still
    has a planned roster to record, so the run manifest must not degrade to trigger-only
    metadata just because no output directory exists. A genuinely trigger-only project
    declares no sets table and degrades to None.
    """
    repo_root = resolve_repo_root(config)
    table = _layer_config_sets(pyproject_table(repo_root), option_str(config, "benchspec_config"))
    sets_table = table.get("sets")
    return isinstance(sets_table, dict) and bool(sets_table)


def run_set_when_needed(config: RunOptions, *, needs_set: bool) -> EvalSet | None:
    """Resolve this run's eval set, but only when the run actually needs one.

    A trigger-only project declares no `[tool.benchspec.sets.*]` table, so calling
    `resolved_run_set` there raises a UsageError. Both the session sandbox preflight
    (via `session_run_set`) and `pytest_sessionfinish` compute their own "a set is
    required" signal — the collection's eval cases, and the produced `eval-*` artifact
    dirs respectively — then share this seam so the trigger-only path degrades to None
    identically instead of parsing/raising.
    """
    return resolved_run_set(config) if needs_set else None


def session_run_set(config: RunOptions) -> EvalSet | None:
    """The eval set for the session sandbox preflight, or None for a trigger-only run.

    Guards `resolved_run_set` on the same eval-case-existence signal
    `pytest_generate_tests` uses to decide whether arms are required, so a trigger-only
    project with no sets table degrades to the default backend instead of raising. Runs
    at session start (before any `eval-*` artifact exists), so it discovers cases rather
    than walking produced artifacts like `pytest_sessionfinish` does.
    """
    repo_root = resolve_repo_root(config)
    cases = discover_eval_cases(repo_root, resolve_eval_paths(config))
    return run_set_when_needed(config, needs_set=bool(cases))


def _parse_judge_cli_table(config: RunOptions) -> dict:
    """The CLI-override layer for resolve_judge_config.

    Only the `--benchspec-judge-*` flags the user actually passed. Scalar flags signal
    "unset" with None (an explicit empty value is kept, to fail validation loudly); the
    repeatable flags signal "unset" with an empty list.
    """
    cli_table: dict = {}
    for option, key in (
        ("benchspec_judge_harness", "harness"),
        ("benchspec_judge_model", "model"),
        ("benchspec_judge_effort", "effort"),
    ):
        if (value := option_str(config, option)) is not None:
            cli_table[key] = value
    if (timeout := option_int(config, "benchspec_judge_timeout")) is not None:
        cli_table["timeout"] = timeout

    if harness_args := option_str_list(config, "benchspec_judge_harness_arg"):
        cli_table["harness_args"] = harness_args
    if env_pairs := option_str_list(config, "benchspec_judge_env"):
        cli_table["env"] = _parse_env_pairs(env_pairs)
    return cli_table


def resolved_judge_config(config: RunOptions) -> JudgeConfig:
    """The single JudgeConfig this run uses.

    Layers `--benchspec-config`'s [tool.benchspec.judge] over pyproject's, applies CLI
    overrides, and preflights structurally (known harness, reserved harness_args,
    opencode provider-qualified model). Binary-on-PATH is NOT checked here — see
    cases.py's judge preflight — so this stays safe to call under `--collect-only` and
    from every existing pytester-based collection test.
    """
    repo_root = resolve_repo_root(config)
    pyproject_judge = pyproject_table(repo_root).get("judge")
    scratch_path = option_str(config, "benchspec_config")
    scratch = _read_scratch_benchspec_table(scratch_path)
    scratch_judge = scratch.get("judge")
    # A present-but-non-dict `judge` key is a malformed config, not "no judge table" —
    # coercing it to None here would silently skip _validate_judge_table entirely and
    # fall through to defaults. Raise loudly instead, same as any other structural
    # judge-config defect (caught below and turned into pytest.UsageError).
    if pyproject_judge is not None and not isinstance(pyproject_judge, dict):
        raise pytest.UsageError(
            "[tool.benchspec.judge] must be a table, got "
            f"{type(pyproject_judge).__name__}"
        )
    if scratch_judge is not None and not isinstance(scratch_judge, dict):
        raise pytest.UsageError(
            f"--benchspec-config {scratch_path}: "
            f"[tool.benchspec.judge] must be a table, got {type(scratch_judge).__name__}"
        )
    try:
        return resolve_judge_config(
            pyproject_table=pyproject_judge,
            scratch_table=scratch_judge,
            cli_table=_parse_judge_cli_table(config),
        )
    except SchemaError as error:
        raise pytest.UsageError(str(error)) from None
