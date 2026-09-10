"""Tests for BinderConfig validation and resolve_binder_config layer precedence."""

from __future__ import annotations

import pytest

from benchspec.grading.binder_config import (
    BinderConfig,
    _validate_binder_table,
    resolve_binder_config,
)
from benchspec.specs.schema import SchemaError


def test_binder_config_defaults_to_gemini_flash_lite() -> None:
    """Verify the built-in binder is the Gemini transport on its Flash-Lite model."""
    config = BinderConfig()

    assert config.provider == "gemini"
    assert config.model == "gemini-3.5-flash-lite"


def test_resolve_binder_config_defaults_with_no_layers() -> None:
    """Verify no layers resolve to the built-in default."""
    assert resolve_binder_config() == BinderConfig()


def test_resolve_binder_config_openrouter_picks_its_own_default_model() -> None:
    """Switching the provider alone keeps the same upstream model under OpenRouter's slug."""
    config = resolve_binder_config(pyproject_table={"provider": "openrouter"})

    assert config.provider == "openrouter"
    assert config.model == "google/gemini-3.5-flash-lite"


def test_resolve_binder_config_scratch_beats_pyproject_and_cli_beats_scratch() -> None:
    """Verify per-field precedence: CLI > scratch > pyproject."""
    config = resolve_binder_config(
        pyproject_table={"provider": "gemini", "model": "gemini-3.1-flash"},
        scratch_table={"provider": "openrouter"},
        cli_table={"model": "google/gemini-3.5-flash"},
    )

    assert config.provider == "openrouter"
    assert config.model == "google/gemini-3.5-flash"


def test_resolve_binder_config_rejects_unknown_provider_naming_it() -> None:
    """Verify an unknown provider fails naming the value and the known ones."""
    with pytest.raises(SchemaError, match=r"unknown `provider` `vertex`.*gemini.*openrouter"):
        resolve_binder_config(pyproject_table={"provider": "vertex"})


def test_resolve_binder_config_rejects_unqualified_model_under_openrouter() -> None:
    """Verify a bare Gemini model name cannot ride through OpenRouter."""
    with pytest.raises(SchemaError, match=r"\[tool.benchspec.binder\].*vendor-qualified"):
        resolve_binder_config(
            pyproject_table={"provider": "openrouter", "model": "gemini-3.5-flash-lite"}
        )


def test_validate_binder_table_rejects_unknown_key() -> None:
    """Verify an unknown binder key fails at the layer that declared it."""
    with pytest.raises(SchemaError, match="unknown binder key"):
        _validate_binder_table("[tool.benchspec.binder]", {"timeout": 30})


@pytest.mark.parametrize("bad_model", ["", 5, None])
def test_validate_binder_table_rejects_bad_model(bad_model: object) -> None:
    """Verify `model` must be a non-empty string."""
    with pytest.raises(SchemaError, match="model"):
        _validate_binder_table("[tool.benchspec.binder]", {"model": bad_model})
