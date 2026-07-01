"""Schema tests for evalspec.schema — the self-contained `evals/<slug>/` shape."""

from __future__ import annotations

import json

import pytest

from evalspec import schema as v
from evalspec.schema import SchemaError, _validate


def _doc(evals: list[dict]) -> dict:
    return {"$schema": "evalspec/v1", "evals": evals}


def test_minimal_eval_ok():
    _validate(_doc([{"slug": "happy", "prompt": "do X", "assertions": ["X happened"]}]))


def test_skill_name_rejected():
    with pytest.raises(SchemaError, match="unknown field"):
        _validate({"$schema": "evalspec/v1", "skill_name": "ingest",
                   "evals": [{"slug": "a", "prompt": "p", "assertions": ["x"]}]})


def test_checks_key_rejected():
    with pytest.raises(SchemaError, match="unknown field"):
        _validate(_doc([{"slug": "a", "prompt": "p", "assertions": ["x"],
                         "checks": [{"checker": "file_exists", "path": "y"}]}]))


def test_seed_ok():
    _validate(_doc([{
        "slug": "seeded", "prompt": "continue",
        "seed": [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}],
        "assertions": ["the reply continued"],
    }]))


def test_seed_missing_text_rejected():
    with pytest.raises(SchemaError, match="text"):
        _validate(_doc([{"slug": "a", "prompt": "p", "assertions": ["x"],
                         "seed": [{"role": "user"}]}]))


def test_seed_extra_key_rejected():
    with pytest.raises(SchemaError, match="unknown field"):
        _validate(_doc([{"slug": "a", "prompt": "p", "assertions": ["x"],
                         "seed": [{"role": "user", "text": "hi", "name": "alice"}]}]))


def test_assertions_must_be_strings():
    with pytest.raises(SchemaError):
        _validate(_doc([{"slug": "a", "prompt": "p",
                         "assertions": [{"checker": "file_exists", "path": "y"}]}]))


def test_assertions_empty_string_rejected():
    with pytest.raises(SchemaError, match="non-empty"):
        _validate(_doc([{"slug": "a", "prompt": "p", "assertions": ["  "]}]))


def test_duplicate_slug_rejected():
    with pytest.raises(SchemaError, match="duplicate"):
        _validate(_doc([
            {"slug": "a", "prompt": "p", "assertions": ["x"]},
            {"slug": "a", "prompt": "q", "assertions": ["y"]},
        ]))


def test_empty_evals_rejected():
    with pytest.raises(SchemaError, match="non-empty"):
        _validate(_doc([]))


def test_slug_must_be_kebab():
    with pytest.raises(SchemaError, match="not kebab-case"):
        _validate(_doc([{"slug": "Bad Slug", "prompt": "p", "assertions": ["x"]}]))


def test_missing_prompt_rejected():
    with pytest.raises(SchemaError, match="prompt"):
        _validate(_doc([{"slug": "a", "assertions": ["x"]}]))


def test_missing_assertions_shows_example():
    with pytest.raises(SchemaError) as ei:
        _validate(_doc([{"slug": "a", "prompt": "p"}]))
    assert 'e.g. "assertions": ["a Resource page was created"]' in str(ei.value)


def _trigger_doc(query_obj: dict) -> dict:
    return {
        "$schema": "evalspec-trigger/v1",
        "skill_name": "demo-skill",
        "queries": [query_obj],
    }


def test_trigger_xfail_object_valid():
    v._validate(_trigger_doc({
        "slug": "q-one", "query": "q", "should_trigger": True,
        "xfail": {"models": ["sonnet", "haiku"], "reason": "documented routing boundary"},
    }))


def test_trigger_xfail_must_be_object():
    with pytest.raises(v.SchemaError, match="xfail"):
        v._validate(_trigger_doc({
            "slug": "q-one", "query": "q", "should_trigger": True,
            "xfail": "a bare string is no longer allowed",
        }))


def test_trigger_xfail_empty_models_rejected():
    with pytest.raises(v.SchemaError, match="models"):
        v._validate(_trigger_doc({
            "slug": "q-one", "query": "q", "should_trigger": True,
            "xfail": {"models": [], "reason": "r"},
        }))


def test_trigger_xfail_unknown_tier_rejected():
    with pytest.raises(v.SchemaError, match="not in"):
        v._validate(_trigger_doc({
            "slug": "q-one", "query": "q", "should_trigger": True,
            "xfail": {"models": ["sonet"], "reason": "r"},
        }))


def test_trigger_xfail_empty_reason_rejected():
    with pytest.raises(v.SchemaError, match="reason"):
        v._validate(_trigger_doc({
            "slug": "q-one", "query": "q", "should_trigger": True,
            "xfail": {"models": ["sonnet"], "reason": "  "},
        }))


def test_trigger_xfail_extra_key_rejected():
    with pytest.raises(v.SchemaError, match="unknown field"):
        v._validate(_trigger_doc({
            "slug": "q-one", "query": "q", "should_trigger": True,
            "xfail": {"models": ["sonnet"], "reason": "r", "verified_on": "2026-05-30"},
        }))


def test_trigger_slug_must_be_kebab():
    with pytest.raises(v.SchemaError, match="slug"):
        v._validate(_trigger_doc({
            "slug": "Not Kebab", "query": "q", "should_trigger": True,
        }))


def test_trigger_slug_duplicate_rejected():
    doc = {
        "$schema": "evalspec-trigger/v1", "skill_name": "ingest",
        "queries": [
            {"slug": "dup", "query": "a", "should_trigger": True},
            {"slug": "dup", "query": "b", "should_trigger": False},
        ],
    }
    with pytest.raises(v.SchemaError, match="duplicate slug"):
        v._validate(doc)


def test_trigger_int_id_now_rejected():
    with pytest.raises(v.SchemaError, match="slug"):
        v._validate(_trigger_doc({
            "id": 1, "query": "q", "should_trigger": True,
        }))


def test_validate_path_returns_parsed_data(tmp_path):
    f = tmp_path / "evals.json"
    f.write_text(json.dumps(_doc([{"slug": "ok", "prompt": "p", "assertions": ["a"]}])))

    data = v.validate_path(f)

    assert data["evals"][0]["slug"] == "ok"


def test_validate_path_raises_on_bad_schema(tmp_path):
    f = tmp_path / "evals.json"
    f.write_text(json.dumps(_doc([{"slug": "Bad Slug", "prompt": "p", "assertions": ["a"]}])))
    with pytest.raises(v.SchemaError):
        v.validate_path(f)


def test_missing_schema_key_says_how_to_add_it():
    with pytest.raises(v.SchemaError) as ei:
        v._validate({"evals": []})
    msg = str(ei.value)
    assert "evalspec/v1" in msg and "evalspec-trigger/v1" in msg
    assert "first key" in msg
