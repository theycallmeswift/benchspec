"""BinderConfig: the run-level, harness-independent assertion-binder configuration.

Resolved once per run from three layers (CLI > scratch --benchspec-config >
pyproject.toml [tool.benchspec.binder] > built-in defaults — see
resolve_binder_config), the same shape as the judge. Structural validation (field
types, known provider) happens at resolve time, safe to run under `--collect-only`.
The credential check is a separate, later step (`binder.preflight_verify_gemini_key`)
that only runs when tests actually execute.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchspec.grading.binder import GEMINI_API_PATH, GEMINI_BINDER_MODEL
from benchspec.specs.schema import SchemaError

GEMINI_PROVIDER = "gemini"
# Provider → the API path its transport posts to; the keys are the supported providers.
_API_PATHS = {GEMINI_PROVIDER: GEMINI_API_PATH}
BINDER_PROVIDERS = frozenset(_API_PATHS)


@dataclass(frozen=True)
class BinderConfig:
    """The resolved, run-level binder: which provider classifies assertions."""

    provider: str = GEMINI_PROVIDER


def _validate_binder_table(where: str, table: dict) -> dict:
    """Validate one layer's [tool.benchspec.binder]-shaped dict.

    Return only the keys it declared (partial — callers merge over prior layers).
    Raises SchemaError naming the defect, never a silent no-op.
    """
    unknown = sorted(set(table) - {"provider"})
    if unknown:
        raise SchemaError(f"{where}: unknown binder key(s) {unknown} (known: ['provider'])")
    validated: dict = {}
    if "provider" in table:
        provider = table["provider"]
        if not isinstance(provider, str) or not provider:
            raise SchemaError(f"{where}: `provider` must be a non-empty string")
        validated["provider"] = provider
    return validated


def resolve_binder_config(
    *, pyproject_table: dict | None = None, scratch_table: dict | None = None,
    cli_table: dict | None = None,
) -> BinderConfig:
    """Resolve the run's one BinderConfig from three layers, per field.

    CLI override > scratch --benchspec-config > pyproject [tool.benchspec.binder] >
    built-in defaults. Raises SchemaError on any structural defect — the pytest plugin
    turns that into a pytest.UsageError at collection time, before any paid task arm
    runs.
    """
    resolved: dict = {"provider": GEMINI_PROVIDER}
    for label, table in (
        ("[tool.benchspec.binder]", pyproject_table),
        ("--benchspec-config [tool.benchspec.binder]", scratch_table),
        ("--benchspec-binder-* CLI overrides", cli_table),
    ):
        if not table:
            continue
        resolved.update(_validate_binder_table(label, table))

    config = BinderConfig(**resolved)
    if config.provider not in BINDER_PROVIDERS:
        raise SchemaError(
            f"[tool.benchspec.binder] provider `{config.provider}` is not a supported "
            f"binder provider (supported: {sorted(BINDER_PROVIDERS)})"
        )
    return config


def binder_identity(config: BinderConfig) -> dict:
    """Return the configured binder's transport identity for artifact metadata.

    Run-level — describes the assertion binder, not the grader. Never reads or includes
    key material.
    """
    return {
        "provider": config.provider,
        "model": GEMINI_BINDER_MODEL,
        "api_path": _API_PATHS[config.provider],
    }
