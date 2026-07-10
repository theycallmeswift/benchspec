"""Schema tests for evalspec.schema — the self-contained `evals/<slug>/` shape."""

from __future__ import annotations

import json

import pytest

from evalspec import schema as v
from evalspec.schema import SchemaError, _validate


def _doc(evals: list[dict]) -> dict:
    """Build the doc test fixture."""
    return {"$schema": "evalspec/v1", "evals": evals}


def test_is_kebab() -> None:
    """Verify is kebab."""
    assert v.is_kebab("write-spec") is True
    assert v.is_kebab("Write Spec") is False
    assert v.is_kebab("") is False


def test_minimal_eval_ok() -> None:
    """Verify minimal eval ok."""
    _validate(_doc([{"id": "happy", "prompt": "do X", "assertions": ["X happened"]}]))


def test_skill_name_rejected() -> None:
    """Verify skill name rejected."""
    with pytest.raises(SchemaError, match="unknown field"):
        _validate(
            {
                "$schema": "evalspec/v1",
                "skill_name": "ingest",
                "evals": [{"id": "a", "prompt": "p", "assertions": ["x"]}],
            }
        )


def test_checks_key_rejected() -> None:
    """Verify checks key rejected."""
    with pytest.raises(SchemaError, match="unknown field"):
        _validate(
            _doc(
                [
                    {
                        "id": "a",
                        "prompt": "p",
                        "assertions": ["x"],
                        "checks": [{"checker": "file_exists", "path": "y"}],
                    }
                ]
            )
        )


def test_history_ok() -> None:
    """Verify history ok."""
    _validate(
        _doc(
            [
                {
                    "id": "seeded",
                    "prompt": "continue",
                    "history": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "ok"},
                    ],
                    "assertions": ["the reply continued"],
                }
            ]
        )
    )


def test_history_missing_content_rejected() -> None:
    """Verify history missing content rejected."""
    with pytest.raises(SchemaError, match="content"):
        _validate(
            _doc([{"id": "a", "prompt": "p", "assertions": ["x"], "history": [{"role": "user"}]}])
        )


def test_history_extra_key_rejected() -> None:
    """Verify history extra key rejected."""
    with pytest.raises(SchemaError, match="unknown field"):
        _validate(
            _doc(
                [
                    {
                        "id": "a",
                        "prompt": "p",
                        "assertions": ["x"],
                        "history": [{"role": "user", "content": "hi", "name": "alice"}],
                    }
                ]
            )
        )


def test_assertions_must_be_strings() -> None:
    """Verify assertions must be strings."""
    with pytest.raises(SchemaError):
        _validate(
            _doc(
                [
                    {
                        "id": "a",
                        "prompt": "p",
                        "assertions": [{"checker": "file_exists", "path": "y"}],
                    }
                ]
            )
        )


def test_assertions_empty_string_rejected() -> None:
    """Verify assertions empty string rejected."""
    with pytest.raises(SchemaError, match="non-empty"):
        _validate(_doc([{"id": "a", "prompt": "p", "assertions": ["  "]}]))


def test_duplicate_slug_rejected() -> None:
    """Verify duplicate slug rejected."""
    with pytest.raises(SchemaError, match="duplicate"):
        _validate(
            _doc(
                [
                    {"id": "a", "prompt": "p", "assertions": ["x"]},
                    {"id": "a", "prompt": "q", "assertions": ["y"]},
                ]
            )
        )


def test_empty_evals_rejected() -> None:
    """Verify empty evals rejected."""
    with pytest.raises(SchemaError, match="non-empty"):
        _validate(_doc([]))


def test_slug_must_be_kebab() -> None:
    """Verify slug must be kebab."""
    with pytest.raises(SchemaError, match="not kebab-case"):
        _validate(_doc([{"id": "Bad Slug", "prompt": "p", "assertions": ["x"]}]))


def test_missing_prompt_rejected() -> None:
    """Verify missing prompt rejected."""
    with pytest.raises(SchemaError, match="prompt"):
        _validate(_doc([{"id": "a", "assertions": ["x"]}]))


def test_missing_assertions_shows_example() -> None:
    """Verify missing assertions shows example."""
    with pytest.raises(SchemaError) as ei:
        _validate(_doc([{"id": "a", "prompt": "p"}]))
    assert 'e.g. "assertions": ["a Resource page was created"]' in str(ei.value)


def _trigger_doc(query_obj: dict) -> dict:
    """Build the trigger doc test fixture."""
    return {
        "$schema": "evalspec-trigger/v1",
        "skill_name": "demo-skill",
        "queries": [query_obj],
    }


def test_trigger_xfail_object_valid() -> None:
    """Verify trigger xfail object valid."""
    v._validate(
        _trigger_doc(
            {
                "slug": "q-one",
                "query": "q",
                "should_trigger": True,
                "xfail": {
                    "models": ["sonnet", "haiku"],
                    "reason": "documented routing boundary",
                },
            }
        )
    )


def test_trigger_xfail_must_be_object() -> None:
    """Verify trigger xfail must be object."""
    with pytest.raises(v.SchemaError, match="xfail"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "q-one",
                    "query": "q",
                    "should_trigger": True,
                    "xfail": "a bare string is no longer allowed",
                }
            )
        )


def test_trigger_xfail_empty_models_rejected() -> None:
    """Verify trigger xfail empty models rejected."""
    with pytest.raises(v.SchemaError, match="models"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "q-one",
                    "query": "q",
                    "should_trigger": True,
                    "xfail": {"models": [], "reason": "r"},
                }
            )
        )


def test_trigger_xfail_unknown_tier_rejected() -> None:
    """Verify trigger xfail unknown tier rejected."""
    with pytest.raises(v.SchemaError, match="not in"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "q-one",
                    "query": "q",
                    "should_trigger": True,
                    "xfail": {"models": ["sonet"], "reason": "r"},
                }
            )
        )


def test_trigger_xfail_empty_reason_rejected() -> None:
    """Verify trigger xfail empty reason rejected."""
    with pytest.raises(v.SchemaError, match="reason"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "q-one",
                    "query": "q",
                    "should_trigger": True,
                    "xfail": {"models": ["sonnet"], "reason": "  "},
                }
            )
        )


def test_trigger_xfail_extra_key_rejected() -> None:
    """Verify trigger xfail extra key rejected."""
    with pytest.raises(v.SchemaError, match="unknown field"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "q-one",
                    "query": "q",
                    "should_trigger": True,
                    "xfail": {
                        "models": ["sonnet"],
                        "reason": "r",
                        "verified_on": "2026-05-30",
                    },
                }
            )
        )


def test_trigger_slug_must_be_kebab() -> None:
    """Verify trigger slug must be kebab."""
    with pytest.raises(v.SchemaError, match="slug"):
        v._validate(
            _trigger_doc(
                {
                    "slug": "Not Kebab",
                    "query": "q",
                    "should_trigger": True,
                }
            )
        )


def test_trigger_slug_duplicate_rejected() -> None:
    """Verify trigger slug duplicate rejected."""
    doc = {
        "$schema": "evalspec-trigger/v1",
        "skill_name": "ingest",
        "queries": [
            {"slug": "dup", "query": "a", "should_trigger": True},
            {"slug": "dup", "query": "b", "should_trigger": False},
        ],
    }
    with pytest.raises(v.SchemaError, match="duplicate slug"):
        v._validate(doc)


def test_trigger_int_id_now_rejected() -> None:
    """Verify trigger int id now rejected."""
    with pytest.raises(v.SchemaError, match="slug"):
        v._validate(
            _trigger_doc(
                {
                    "id": 1,
                    "query": "q",
                    "should_trigger": True,
                }
            )
        )


def test_validate_path_returns_parsed_data(tmp_path: object) -> None:
    """Verify validate path returns parsed data."""
    f = tmp_path / "evals.json"
    f.write_text(json.dumps(_doc([{"id": "ok", "prompt": "p", "assertions": ["a"]}])))

    data = v.validate_path(f)

    assert data["evals"][0]["id"] == "ok"


def test_validate_path_raises_on_bad_schema(tmp_path: object) -> None:
    """Verify validate path raises for on bad schema."""
    f = tmp_path / "evals.json"
    f.write_text(json.dumps(_doc([{"id": "Bad Slug", "prompt": "p", "assertions": ["a"]}])))
    with pytest.raises(v.SchemaError):
        v.validate_path(f)


def test_missing_schema_key_says_how_to_add_it() -> None:
    """Verify missing schema key says how to add it."""
    with pytest.raises(v.SchemaError) as ei:
        v._validate({"evals": []})
    msg = str(ei.value)
    assert "evalspec/v1" in msg
    assert "evalspec-trigger/v1" in msg
    assert "first key" in msg
