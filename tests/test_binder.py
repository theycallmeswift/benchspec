"""Binder tests — all mocked, NO network.

`call_host` is injected with recorded host-Claude envelopes so the binder's parse, validate,
and punt logic is exercised offline.
"""

from __future__ import annotations

import json
from typing import NoReturn

import pytest

from evalspec import binder
from evalspec.agents.judge_cli import run_host_judge
from evalspec.binder import _BINDING_PROMPT, bind


def _host(reply: object) -> object:
    """Build the host test fixture."""
    # mimic the host-claude --output-format json envelope: {"result": "<model text>"}
    return lambda prompt, *, model, timeout=60: json.dumps({"result": reply})


def _fail_host(*args: object, **kwargs: object) -> NoReturn:
    """Build the fail host test fixture."""
    raise AssertionError("host binder should not be called")


def test_bind_file_exists() -> None:
    """Verify bind file exists."""
    spec = bind(
        "the file out.md exists",
        call_host=_host('{"checker":"file_exists","path":"out.md"}'),
    )
    assert spec["checker"] == "file_exists"
    assert spec["path"] == "out.md"


def test_bare_exists_hidden_template_path_binds_without_host_call() -> None:
    """Verify bare exists hidden template path binds without host call."""
    spec = bind("./.meta/templates/entity-person.md exists", call_host=_fail_host)

    assert spec == {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "./.meta/templates/entity-person.md",
    }


def test_bare_exists_strips_quotes_without_losing_hidden_dot() -> None:
    """Verify bare exists strips quotes without losing hidden dot."""
    spec = bind("'./.meta/templates/entity-person.md' exists", call_host=_fail_host)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./.meta/templates/entity-person.md"


def test_bare_exists_accepts_trailing_period_without_losing_hidden_dot() -> None:
    """Verify bare exists accepts trailing period without losing hidden dot."""
    spec = bind("./.meta/templates/entity-person.md exists.", call_host=_fail_host)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./.meta/templates/entity-person.md"


def test_compound_hidden_path_assertion_punts() -> None:
    """Verify compound hidden path assertion punts."""
    spec = bind(
        "./.meta/templates/entity-person.md exists and contains frontmatter",
        call_host=_host('{"punt":true,"reason":"compound"}'),
    )

    assert spec is None


def test_descriptive_exists_prose_preserves_model_bound_path() -> None:
    """Verify descriptive exists prose preserves model bound path."""
    spec = bind(
        "The previously-missing ./.obsidian/ configuration now exists",
        call_host=_host('{"checker":"file_exists","path":"./.obsidian/"}'),
    )

    assert spec["path"] == "./.obsidian/"


def test_non_path_exists_prose_punts() -> None:
    """Verify non path exists prose punts."""
    assert (
        bind(
            "the success message now exists",
            call_host=_host('{"punt":true,"reason":"not a path assertion"}'),
        )
        is None
    )


def test_directory_created_path_shape_binds_without_host_call() -> None:
    """Verify directory created path shape binds without host call."""
    spec = bind("the ./output/ directory was created", call_host=_fail_host)

    assert spec["checker"] == "file_exists"
    assert spec["path"] == "./output/"


def test_bind_skill_invoked() -> None:
    """Verify bind skill invoked."""
    spec = bind(
        "Skill `ingest` invoked",
        call_host=_host('{"checker":"skill_invoked","skill":"ingest"}'),
    )
    assert spec["checker"] == "skill_invoked"
    assert spec["skill"] == "ingest"


def test_punt_explicit() -> None:
    """Verify punt explicit."""
    assert bind("the note reads well", call_host=_host('{"punt":true,"reason":"semantic"}')) is None


def test_punt_on_garbage() -> None:
    """Verify punt on garbage."""
    assert bind("unknown assertion", call_host=_host("here you go: not json at all")) is None


def test_punt_on_unknown_checker() -> None:
    """Verify punt on unknown checker."""
    assert bind("unknown assertion", call_host=_host('{"checker":"vibes","path":"a"}')) is None


def test_punt_on_none_host_output() -> None:
    """Verify punt on none host output."""
    # An abnormal host call yielding None must punt, not raise (never-raises contract).
    assert bind("unknown assertion", call_host=lambda *args, **kwargs: None) is None


def test_punt_on_schema_invalid() -> None:  # glob_count needs exactly one of count/min
    """Verify punt on schema invalid."""
    assert bind(
        "unknown assertion",
        call_host=_host('{"checker":"glob_count","glob":"*.md"}'),
    ) is None


def test_parses_fenced_json() -> None:
    """Verify parses fenced json."""
    spec = bind(
        "unknown assertion",
        call_host=_host('```json\n{"checker":"file_exists","path":"a.md"}\n```'),
    )
    assert spec["checker"] == "file_exists"


def test_infra_error_propagates() -> None:
    """Verify infra error propagates."""

    def boom(prompt: object, *, model: object, timeout: object = 60) -> NoReturn:
        """Boom."""
        raise RuntimeError("not logged in")

    with pytest.raises(RuntimeError):
        bind("unknown assertion", call_host=boom)


def test_returned_spec_is_dispatchable(tmp_path: object) -> None:
    """Verify returned spec is dispatchable."""
    # The bound spec must flow straight into the existing checker dispatch.
    from evalspec.checkers import run_assertion

    (tmp_path / "out.md").write_text("hi")
    spec = bind(
        "the file out.md exists",
        call_host=_host('{"checker":"file_exists","path":"out.md"}'),
    )
    assert run_assertion(spec, tmp_path, {})["passed"] is True


def test_prompt_carries_load_bearing_pieces() -> None:
    """Verify prompt carries load bearing pieces."""
    prompt = _BINDING_PROMPT.format(assertion="MY ASSERTION")
    assert "MY ASSERTION" in prompt
    for name in (
        "file_exists",
        "glob_count",
        "frontmatter_has",
        "regex",
        "sha256_match",
        "skill_invoked",
    ):
        assert name in prompt
    assert "not duplicated" in prompt  # A9 rule encoded


def test_binder_still_imports_the_original_run_host_judge() -> None:
    """Verify binder still imports the original run_host_judge."""
    assert binder.run_host_judge is run_host_judge
