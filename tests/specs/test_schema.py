"""Schema tests for benchspec.specs.schema — the self-contained `evals/<slug>/` shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchspec.specs import schema as v
from benchspec.specs.schema import SchemaError, _validate, _validate_checker_obj


def _doc(evals: list[dict]) -> dict:
    """Build the doc test fixture."""
    return {"$schema": "benchspec/v1", "evals": evals}


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
                "$schema": "benchspec/v1",
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


def test_json_document_history_still_rejects_path_form() -> None:
    """Verify benchspec/v1 documents retain their inline-list-only contract."""
    with pytest.raises(SchemaError, match="expected list"):
        _validate(
            _doc(
                [
                    {
                        "id": "a",
                        "prompt": "p",
                        "assertions": ["x"],
                        "history": "session.jsonl",
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


def test_not_skill_invoked_validates() -> None:
    """Verify the negative activation checker validates with just a skill."""
    _validate_checker_obj({"checker": "not_skill_invoked", "skill": "x"}, "root")


def test_not_file_exists_validates() -> None:
    """Verify the negative existence checker validates with just a path."""
    _validate_checker_obj({"checker": "not_file_exists", "path": "x"}, "root")


def test_skill_invoked_rejects_legacy_expected_flag() -> None:
    """Verify the retired `expected` polarity flag is now an unknown field."""
    with pytest.raises(SchemaError, match="unknown field"):
        _validate_checker_obj(
            {"checker": "skill_invoked", "skill": "x", "expected": True}, "root"
        )


def test_skill_invoked_rejects_unknown_key() -> None:
    """Verify an undeclared key on skill_invoked still raises SchemaError."""
    with pytest.raises(SchemaError, match="unknown field"):
        _validate_checker_obj(
            {"checker": "skill_invoked", "skill": "x", "bogus": True}, "root"
        )


def test_former_trigger_doc_now_rejected() -> None:
    """Verify a former benchspec-trigger/v1 doc raises, naming only benchspec/v1."""
    with pytest.raises(SchemaError) as ei:
        _validate(
            {
                "$schema": "benchspec-trigger/v1",
                "skill_name": "demo-skill",
                "queries": [{"slug": "q-one", "query": "q", "should_trigger": True}],
            }
        )

    msg = str(ei.value)
    # The message names the sole supported schema and echoes the rejected value, but no
    # longer advertises the removed trigger schema as a supported option.
    assert "benchspec/v1" in msg
    assert "trigger-evals.md" not in msg


def test_validate_path_returns_parsed_data(tmp_path: Path) -> None:
    """Verify validate path returns parsed data."""
    f = tmp_path / "evals.json"
    f.write_text(json.dumps(_doc([{"id": "ok", "prompt": "p", "assertions": ["a"]}])))

    data = v.validate_path(f)

    assert data["evals"][0]["id"] == "ok"


def test_validate_path_raises_on_bad_schema(tmp_path: Path) -> None:
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
    assert "benchspec/v1" in msg
    assert "first key" in msg


def test_stop_early_accepts_a_boolean() -> None:
    """Verify `stop_early: false` validates."""
    _validate(_doc([{"id": "a", "prompt": "p", "assertions": ["x"], "stop_early": False}]))


def test_stop_early_rejects_a_non_boolean() -> None:
    """Verify `stop_early` must be a boolean, not a truthy string."""
    with pytest.raises(SchemaError, match="stop_early: expected bool"):
        _validate(_doc([{"id": "a", "prompt": "p", "assertions": ["x"], "stop_early": "no"}]))
