"""BinderConfig: the run-level assertion-binder configuration.

Resolved once per run from four layers (CLI > scratch --benchspec-config >
pyproject.toml [tool.benchspec.binder] > built-in defaults — see resolve_binder_config),
mirroring the judge's `resolve_judge_config`. Structural validation (known provider, a
vendor-qualified model under `openrouter`) happens at resolve time, safe to run under
`--collect-only`; the credential check is a separate, later step in `binder.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchspec.agents import OPENROUTER_PROVIDER, unqualified_openrouter_model_error
from benchspec.specs.schema import SchemaError

GEMINI_PROVIDER = "gemini"
BINDER_PROVIDERS = frozenset({GEMINI_PROVIDER, OPENROUTER_PROVIDER})
DEFAULT_BINDER_PROVIDER = GEMINI_PROVIDER

GEMINI_BINDER_MODEL = "gemini-3.5-flash-lite"
# The same upstream model as `GEMINI_BINDER_MODEL`, under OpenRouter's vendor-prefixed slug.
OPENROUTER_BINDER_MODEL = "google/gemini-3.5-flash-lite"
DEFAULT_BINDER_MODELS = {
    GEMINI_PROVIDER: GEMINI_BINDER_MODEL,
    OPENROUTER_PROVIDER: OPENROUTER_BINDER_MODEL,
}


@dataclass(frozen=True)
class BinderConfig:
    """The resolved, run-level binder: which transport classifies assertions, with what model.

    Independent from the judge and from every task arm — the binder is a fixed classifier,
    not a grader, so its provider is chosen on its own.
    """

    provider: str = DEFAULT_BINDER_PROVIDER
    model: str = GEMINI_BINDER_MODEL


def _validated_provider(where: str, value: object) -> str:
    """Return value when it names a known binder provider, else raise SchemaError naming it."""
    if not isinstance(value, str) or value not in BINDER_PROVIDERS:
        raise SchemaError(
            f"{where}: unknown `provider` `{value}` (known: {sorted(BINDER_PROVIDERS)})"
        )
    return value


def _validated_model(where: str, value: object) -> str:
    """Return value when it is a non-empty string, else raise SchemaError."""
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{where}: `model` must be a non-empty string")
    return value


# Each binder field maps to the validator that both checks its type and returns the
# cleaned value; the keys double as the set of recognized binder keys.
_FIELD_VALIDATORS = {
    "provider": _validated_provider,
    "model": _validated_model,
}


def _validate_binder_table(where: str, table: dict) -> dict:
    """Validate one layer's [tool.benchspec.binder]-shaped dict.

    Return only the keys it declared (partial — callers merge over prior layers).
    Raises SchemaError naming the defect, never a silent no-op.
    """
    unknown = sorted(set(table) - set(_FIELD_VALIDATORS))
    if unknown:
        raise SchemaError(
            f"{where}: unknown binder key(s) {unknown} (known: {list(_FIELD_VALIDATORS)})"
        )
    return {
        key: validate(where, table[key])
        for key, validate in _FIELD_VALIDATORS.items()
        if key in table
    }


def resolve_binder_config(
    *, pyproject_table: dict | None = None, scratch_table: dict | None = None,
    cli_table: dict | None = None,
) -> BinderConfig:
    """Resolve the run's one BinderConfig from four layers, per field.

    CLI override > scratch --benchspec-config > pyproject [tool.benchspec.binder] >
    built-in defaults. A `model` no layer sets falls back to the resolved provider's own
    default, so switching the provider alone keeps the same upstream model. Raises
    SchemaError on any structural defect — the pytest plugin turns that into a
    pytest.UsageError at collection time, before any paid task arm runs.
    """
    resolved: dict = {}
    for label, table in (
        ("[tool.benchspec.binder]", pyproject_table),
        ("--benchspec-config [tool.benchspec.binder]", scratch_table),
        ("--benchspec-binder-* CLI overrides", cli_table),
    ):
        if not table:
            continue
        resolved.update(_validate_binder_table(label, table))

    provider = resolved.get("provider", DEFAULT_BINDER_PROVIDER)
    model = resolved.get("model", DEFAULT_BINDER_MODELS[provider])
    if provider == OPENROUTER_PROVIDER:
        model_error = unqualified_openrouter_model_error(model)
        if model_error:
            raise SchemaError(f"[tool.benchspec.binder] {model_error}")

    return BinderConfig(provider=provider, model=model)
