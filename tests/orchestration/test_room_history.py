"""Tests for room history."""

import pytest

from benchspec.orchestration.room import render_history


def test_render_history_none_is_empty() -> None:
    """Verify render history none is empty."""
    assert render_history(None) == ""
    assert render_history([]) == ""


def test_render_history_renders_block() -> None:
    """Verify render history renders block."""
    out = render_history(
        [
            {"role": "user", "content": "set up my vault"},
            {"role": "assistant", "content": "done"},
        ],
    )

    assert out == ("<transcript>\nuser: set up my vault\nassistant: done\n</transcript>\n\n")


def test_render_history_substitutes_today() -> None:
    """Verify render history substitutes today."""
    out = render_history([{"role": "user", "content": "today is {TODAY}"}], today="2026-06-23")
    assert "today is 2026-06-23" in out
    assert "{TODAY}" not in out


def test_render_history_missing_role_rejected() -> None:
    """Verify render history missing role rejected."""
    with pytest.raises(ValueError, match="role"):
        render_history([{"content": "no role here"}])


def test_render_history_missing_content_rejected() -> None:
    """Verify render history missing content rejected."""
    with pytest.raises(ValueError, match="content"):
        render_history([{"role": "user"}])


def test_render_history_non_string_rejected() -> None:
    """Verify render history non string rejected."""
    with pytest.raises(ValueError, match="content"):
        render_history([{"role": "user", "content": 7}])


def test_render_history_non_dict_turn_rejected() -> None:
    """Verify render history non dict turn rejected."""
    with pytest.raises(ValueError, match="mapping"):
        render_history(["set up my vault"])


def test_render_history_stray_placeholder_rejected() -> None:
    """Verify render history stray placeholder rejected."""
    with pytest.raises(ValueError, match="placeholder"):
        render_history([{"role": "user", "content": "write to {WORKDIR}/x"}], today="2026-06-23")
