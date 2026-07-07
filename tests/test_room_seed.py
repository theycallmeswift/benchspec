"""Tests and helpers for evalspec."""

import pytest

from evalspec.room import render_seed


def test_render_seed_none_is_empty() -> None:
    """Test the expected behavior."""
    assert render_seed(None) == ""
    assert render_seed([]) == ""


def test_render_seed_renders_block() -> None:
    """Test the expected behavior."""
    out = render_seed(
        [
            {"role": "user", "text": "set up my vault"},
            {"role": "assistant", "text": "done"},
        ],
    )

    assert out == ("<transcript>\nuser: set up my vault\nassistant: done\n</transcript>\n\n")


def test_render_seed_substitutes_today() -> None:
    """Test the expected behavior."""
    out = render_seed([{"role": "user", "text": "today is {TODAY}"}], today="2026-06-23")
    assert "today is 2026-06-23" in out
    assert "{TODAY}" not in out


def test_render_seed_missing_role_rejected() -> None:
    """Test the expected behavior."""
    with pytest.raises(ValueError, match="role"):
        render_seed([{"text": "no role here"}])


def test_render_seed_missing_text_rejected() -> None:
    """Test the expected behavior."""
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user"}])


def test_render_seed_non_string_rejected() -> None:
    """Test the expected behavior."""
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user", "text": 7}])


def test_render_seed_non_dict_turn_rejected() -> None:
    """Test the expected behavior."""
    with pytest.raises(ValueError, match="mapping"):
        render_seed(["set up my vault"])


def test_render_seed_stray_placeholder_rejected() -> None:
    """Test the expected behavior."""
    with pytest.raises(ValueError, match="placeholder"):
        render_seed([{"role": "user", "text": "write to {WORKDIR}/x"}], today="2026-06-23")
