import pytest

from evalspec.room import render_seed


def test_render_seed_none_is_empty():
    assert render_seed(None) == ""
    assert render_seed([]) == ""


def test_render_seed_renders_block():
    out = render_seed(
        [{"role": "user", "text": "set up my vault"},
         {"role": "assistant", "text": "done"}],
    )

    assert out == (
        "<transcript>\n"
        "user: set up my vault\n"
        "assistant: done\n"
        "</transcript>\n\n"
    )


def test_render_seed_substitutes_today():
    out = render_seed([{"role": "user", "text": "today is {TODAY}"}], today="2026-06-23")
    assert "today is 2026-06-23" in out
    assert "{TODAY}" not in out


def test_render_seed_missing_role_rejected():
    with pytest.raises(ValueError, match="role"):
        render_seed([{"text": "no role here"}])


def test_render_seed_missing_text_rejected():
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user"}])


def test_render_seed_non_string_rejected():
    with pytest.raises(ValueError, match="text"):
        render_seed([{"role": "user", "text": 7}])


def test_render_seed_non_dict_turn_rejected():
    with pytest.raises(ValueError, match="mapping"):
        render_seed(["set up my vault"])


def test_render_seed_stray_placeholder_rejected():
    with pytest.raises(ValueError, match="placeholder"):
        render_seed([{"role": "user", "text": "write to {WORKDIR}/x"}], today="2026-06-23")
