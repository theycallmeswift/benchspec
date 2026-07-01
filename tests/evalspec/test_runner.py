import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from evalspec.runner import (
    parse_run_json,
    parse_stream_run,
    substitute_assertions,
    substitute_prompt,
    sum_tokens,
    utc_today,
)


# ---------------------------------------------------------------------------
# utc_today — host {TODAY} must match the guest's UTC clock
# ---------------------------------------------------------------------------


def test_utc_today_uses_utc_calendar_date_not_local():
    # Evening in a timezone behind UTC is already the next UTC day; the guest VM
    # (TZ=UTC) would write that next day, so the host date must be UTC too.
    evening_eastern = datetime.datetime(2026, 5, 28, 23, 30, tzinfo=ZoneInfo("America/New_York"))
    assert utc_today(evening_eastern) == "2026-05-29"


def test_utc_today_passthrough_utc_instant():
    assert utc_today(datetime.datetime(2026, 5, 29, 0, 1, tzinfo=datetime.timezone.utc)) == "2026-05-29"


# The no-arg default just reads the real UTC clock; asserting it against a second
# real-clock read is a tautology with a midnight-rollover flake window, so it's
# dropped. The injected-`now` cases above cover all the date-math behavior.


# ---------------------------------------------------------------------------
# sum_tokens
# ---------------------------------------------------------------------------


def test_sum_tokens_adds_all_token_fields():
    usage = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 5,
        "output_tokens": 50,
        "service_tier": "standard",   # non-token field ignored
    }
    assert sum_tokens(usage) == 175


def test_sum_tokens_missing_fields_default_zero():
    assert sum_tokens({"input_tokens": 10, "output_tokens": 5}) == 15
    assert sum_tokens({}) == 0


def test_sum_tokens_none_guard():
    assert sum_tokens({"input_tokens": None}) == 0


# ---------------------------------------------------------------------------
# substitute_prompt
# ---------------------------------------------------------------------------


def test_substitute_prompt_relative_paths_untouched():
    # Prompts now use cwd-relative `./` paths; there is no path placeholder to
    # substitute. A relative path passes through verbatim.
    out = substitute_prompt("Work in ./Greetings now.")
    assert out == "Work in ./Greetings now."


def test_residual_placeholder_raises():
    with pytest.raises(ValueError) as e:
        substitute_prompt("read {WIKI_SPEC} first; work in ./vault")
    assert "WIKI_SPEC" in str(e.value)


def test_stray_legacy_path_placeholder_raises():
    # The old absolute-path token no longer exists; a leftover trips the
    # residual-placeholder guard rather than reaching the agent verbatim.
    legacy = "{" + "OUTPUT" + "}"
    with pytest.raises(ValueError, match="OUTPUT"):
        substitute_prompt(f"work in {legacy}")


def test_substitute_prompt_replaces_today():
    out = substitute_prompt("today is {TODAY} in ./vault", today="2099-01-01")
    assert out == "today is 2099-01-01 in ./vault"


def test_substitute_prompt_today_none_leaves_today_token():
    # When today is None, {TODAY} is not replaced — but if it stays it will
    # trigger the residual placeholder guard.
    with pytest.raises(ValueError, match="TODAY"):
        substitute_prompt("today is {TODAY}")


def test_substitute_prompt_today_none_no_token_still_works():
    out = substitute_prompt("work in ./vault")
    assert out == "work in ./vault"


# ---------------------------------------------------------------------------
# substitute_assertions
# ---------------------------------------------------------------------------


def test_substitute_assertions_replaces_today():
    result = substitute_assertions(["{TODAY}/foo", "other"], "2099-06-15")
    assert result == ["2099-06-15/foo", "other"]


def test_substitute_assertions_leaves_relative_paths_untouched():
    result = substitute_assertions(["./file", "{TODAY}/log"], "2099-06-15")
    assert result[0] == "./file"
    assert result[1] == "2099-06-15/log"


def test_substitute_assertions_empty_list():
    assert substitute_assertions([], "2099-06-15") == []


def test_substitute_assertions_handles_typed_dicts():
    out = substitute_assertions(
        ["log at {TODAY}.md",
         {"type": "deterministic", "checker": "file_exists",
          "path": "logs/{TODAY}.md", "should_exist": True}],
        "2099-01-01",
    )
    assert out[0] == "log at 2099-01-01.md"
    assert out[1]["path"] == "logs/2099-01-01.md"
    assert out[1]["should_exist"] is True  # non-strings untouched


def test_substitute_assertions_rejects_residual_placeholder():
    with pytest.raises(ValueError, match="TODAAY"):
        substitute_assertions(["the file at {TODAAY}.md exists"], "2099-01-01")


def test_substitute_assertions_rejects_residual_placeholder_in_typed_value():
    with pytest.raises(ValueError, match="WIKI_SPEC"):
        substitute_assertions(
            [{"type": "deterministic", "checker": "file_exists", "path": "{WIKI_SPEC}"}],
            "2099-01-01",
        )


# ---------------------------------------------------------------------------
# parse_run_json
# ---------------------------------------------------------------------------


def test_parse_run_json_extracts_fields():
    raw = (
        '{"type":"result","is_error":false,"duration_ms":8500,'
        '"result":"done","usage":{"input_tokens":100,"output_tokens":50}}'
    )
    r = parse_run_json(raw, eval_id="e1", config="with_skill")
    assert r.eval_id == "e1"
    assert r.config == "with_skill"
    assert r.result_text == "done"
    assert r.duration_ms == 8500
    assert r.total_tokens == 150
    assert r.is_error is False


def test_parse_run_json_extracts_session_id():
    raw = '{"is_error":false,"result":"ok","session_id":"sess-abc123","usage":{}}'
    r = parse_run_json(raw, eval_id="e1", config="with_skill")
    assert r.session_id == "sess-abc123"


def test_parse_run_json_session_id_defaults_empty_when_absent():
    raw = '{"is_error":false,"result":"ok","usage":{}}'
    r = parse_run_json(raw, eval_id="e1", config="with_skill")
    assert r.session_id == ""


# ---------------------------------------------------------------------------
# parse_stream_run (with-skill arm: stream-json + skill-fired detection)
# ---------------------------------------------------------------------------


def _skill_event(skill_value: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Skill",
                                 "input": {"skill": skill_value}}]},
    })


def _result_event(**fields) -> str:
    return json.dumps({"type": "result", "is_error": False, **fields})


def test_parse_stream_run_detects_fire_and_parses_result():
    stdout = "\n".join([
        _skill_event("eval-myskill:myskill"),
        _result_event(result="done", duration_ms=4200,
                      usage={"input_tokens": 10, "output_tokens": 5}, session_id="sX"),
    ])
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.fired is True
    assert r.result_text == "done"
    assert r.duration_ms == 4200
    assert r.total_tokens == 15
    assert r.session_id == "sX"
    assert r.is_error is False


def test_parse_stream_run_no_fire_when_skill_absent():
    stdout = "\n".join([
        _skill_event("knowledge-base:archive"),  # a different skill
        _result_event(result="hand-rolled", usage={}),
    ])
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.fired is False
    assert r.result_text == "hand-rolled"
    assert r.is_error is False


def test_parse_stream_run_baseline_skips_firing_but_keeps_raw_and_trajectory():
    # skill_name=None: a Skill dispatch in the stream must not set fired, but raw +
    # trajectory are still captured (both arms stream).
    stdout = "\n".join([
        _skill_event("knowledge-base:archive"),
        _result_event(result="hand-rolled", usage={}),
    ])

    r = parse_stream_run(stdout, "e1", "without_skill", None)

    assert r.fired is False
    assert r.result_text == "hand-rolled"
    assert r.raw == stdout
    assert r.trajectory[0]["name"] == "Skill"


def test_parse_stream_run_missing_result_event_is_error():
    # Skill fired but the stream has no result event (truncated/crashed run):
    # fired is still reported, but the run is flagged errored.
    stdout = _skill_event("myskill")
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.fired is True
    assert r.is_error is True


def test_parse_stream_run_skips_blank_and_malformed_lines():
    stdout = "\n".join([
        "", "  ", "not json",
        _skill_event("myskill"),
        _result_event(result="ok", usage={}),
    ])
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.fired is True
    assert r.result_text == "ok"


def test_parse_stream_run_carries_raw_stdout():
    # The full stream is kept so callers can persist it for debugging.
    stdout = "\n".join([
        _skill_event("myskill"),
        _result_event(result="ok", usage={}),
    ])

    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")

    assert r.raw == stdout


def test_parse_stream_run_carries_raw_on_missing_result_event():
    # A truncated/crashed run (no terminal result event) is exactly when the raw
    # stream is most useful — keep it even on the errored return path.
    stdout = _skill_event("myskill")
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.is_error is True
    assert r.raw == stdout


def test_parse_run_json_leaves_raw_empty():
    # The plain-json baseline path has no stream; raw stays empty.
    raw = '{"type":"result","is_error":false,"result":"done","usage":{}}'
    r = parse_run_json(raw, "e1", "without_skill")
    assert r.raw == ""


def test_parse_run_json_breaks_out_cache_tokens_and_result_subtype():
    raw = (
        '{"type":"result","subtype":"success","is_error":false,"result":"done",'
        '"usage":{"input_tokens":100,"output_tokens":50,'
        '"cache_read_input_tokens":40,"cache_creation_input_tokens":7}}'
    )
    r = parse_run_json(raw, "e1", "without_skill")
    assert r.cache_read_tokens == 40
    assert r.cache_creation_tokens == 7
    assert r.result_subtype == "success"


def test_parse_run_json_metadata_defaults_when_absent():
    r = parse_run_json('{"is_error":false,"result":"ok","usage":{}}', "e1", "without_skill")
    assert r.cache_read_tokens == 0
    assert r.cache_creation_tokens == 0
    assert r.result_subtype == ""
    assert r.trajectory == []


def test_parse_stream_run_carries_structured_trajectory():
    tool_use = json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "myskill"}}
        ]},
    })
    stdout = "\n".join([tool_use, _result_event(result="done", usage={})])
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.trajectory == [
        {"kind": "tool_call", "id": "t1", "name": "Skill", "arguments": {"skill": "myskill"}},
    ]


def test_parse_stream_run_carries_trajectory_on_missing_result_event():
    stdout = json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "x"}}
        ]},
    })
    r = parse_stream_run(stdout, "e1", "with_skill", "myskill")
    assert r.is_error is True
    assert r.trajectory[0]["name"] == "Write"


def test_parse_run_json_captures_token_split():
    raw = json.dumps({
        "result": "done", "duration_ms": 10, "is_error": False,
        "usage": {"input_tokens": 300, "output_tokens": 100,
                  "cache_read_input_tokens": 50, "cache_creation_input_tokens": 25},
    })
    r = parse_run_json(raw, "e1", "with_skill")
    assert r.input_tokens == 300
    assert r.output_tokens == 100
    assert r.total_tokens == 475

