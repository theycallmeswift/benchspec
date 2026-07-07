"""Tests for room seed."""

import pytest

from evalspec.room import render_seed


def test_render_seed_none_is_empty() -> None:
    """Verify render seed none is empty."""
    assert render_seed(None) == ""
    assert render_seed([]) == ""


def test_render_seed_renders_block() -> None:
    """Verify render seed renders block."""
    out = render_seed(
        [
            {"role": "user", "text": "set up my vault"},
            {"role": "assistant", "text": "done"},
        ],
    )

    assert out == ("<transcript>\nuser: set up my vault\nassistant: done\n</transcript>\n\n")


def test_render_seed_substitutes_today() -> None:
    """Verify render seed substitutes today."""
    out = render_seed([{"role": "user", "text": "today is {TODAY}"}], today="2026-06-23")
    assert "today is 2026-06-23" in out
    assert "{TODAY}" not in out


def test_render_seed_missing_role_rejected() -> None:
    """Verify render seed missing role rejected."""
    with pytest.raises(ValueError, match="role"):
        render_seed([{"text": "no role here"}])


def test_render_seed_missing_text_rejected() -> None:
    """Verify render seed missing text rejected."""
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user"}])


def test_render_seed_non_string_rejected() -> None:
    """Verify render seed non string rejected."""
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user", "text": 7}])


def test_render_seed_non_dict_turn_rejected() -> None:
    """Verify render seed non dict turn rejected."""
    with pytest.raises(ValueError, match="mapping"):
        render_seed(["set up my vault"])


def test_render_seed_stray_placeholder_rejected() -> None:
    """Verify render seed stray placeholder rejected."""
    with pytest.raises(ValueError, match="placeholder"):
        render_seed([{"role": "user", "text": "write to {WORKDIR}/x"}], today="2026-06-23")
