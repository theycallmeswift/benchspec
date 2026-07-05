import json

import pytest

from evalspec.trigger import (
    RoutingError,
    count_fires,
    detect_skill_fired,
    dispatches_skill,
    fire_threshold,
    first_dispatched_skill,
    xfail_applies,
)


def _no_sleep(*_args):
    return None


def _skill_line(skill_value: str) -> str:
    """Return a stream-json line matching the real Skill tool_use shape."""
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Skill",
                    "input": {"skill": skill_value},
                }
            ]
        },
    }
    return json.dumps(event)


def _named_tool_line(name: str) -> str:
    """A tool_use whose name is the skill itself — the namespaced-tool fallback fire shape."""
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": {}},
    ]}})


def test_detect_skill_fired_namespaced_matches_bare_name():
    # Real shape: input.skill == "knowledge-base:bootstrap", query "bootstrap"
    assert detect_skill_fired([_skill_line("knowledge-base:bootstrap")], "bootstrap") is True


def test_detect_skill_fired_bare_skill_name():
    # input.skill == "bootstrap", query "bootstrap"
    assert detect_skill_fired([_skill_line("bootstrap")], "bootstrap") is True


def test_detect_skill_no_fire_text_block_only():
    # Assistant message with only a text block — no tool_use
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Here is your answer."}]
        },
    }
    assert detect_skill_fired([json.dumps(event)], "bootstrap") is False


def test_detect_skill_no_fire_different_skill():
    # Guards against substring bugs: "archive" must not match query "bootstrap"
    assert detect_skill_fired([_skill_line("knowledge-base:archive")], "bootstrap") is False


def test_detect_skill_fires_through_malformed_and_blank_lines():
    # Malformed JSON and blank lines are skipped, not fatal — a real fire still registers.
    lines = ["not json", "", "  ", _skill_line("knowledge-base:bootstrap")]
    assert detect_skill_fired(lines, "bootstrap") is True


def test_detect_skill_malformed_and_blank_lines_alone_do_not_fire():
    assert detect_skill_fired(["not json", "", "  "], "bootstrap") is False


def test_detect_skill_no_match_returns_false_on_empty():
    assert detect_skill_fired([], "bootstrap") is False


def _malformed_shape_lines():
    """Valid-JSON-but-unexpected shapes that must never crash detection (the caller's
    retry loop only catches subprocess failures, so an AttributeError here would be
    fatal)."""
    return [
        "5",                                                    # bare scalar
        "[1, 2, 3]",                                            # bare array
        json.dumps({"type": "assistant", "message": None}),    # null message
        json.dumps({"type": "assistant", "message": {"content": None}}),  # null content
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": None, "input": None},  # null name/input
            {"type": "tool_use", "name": "Skill", "input": {"skill": None}},  # null skill
        ]}}),
    ]


def test_detect_skill_fires_after_malformed_shapes():
    lines = _malformed_shape_lines() + [_skill_line("knowledge-base:bootstrap")]
    assert detect_skill_fired(lines, "bootstrap") is True


def test_detect_skill_malformed_shapes_alone_do_not_crash_or_fire():
    assert detect_skill_fired(_malformed_shape_lines(), "bootstrap") is False


# ---------------------------------------------------------------------------
# count_fires (3×/majority routing loop, with an injected router)
# ---------------------------------------------------------------------------


def _route_seq(*per_pass_lines):
    it = iter(per_pass_lines)

    def route(query, repo_root, model, timeout, **kwargs):
        return next(it)

    return route


def test_count_fires_counts_each_firing_pass():
    fire = [_skill_line("knowledge-base:bootstrap")]
    miss = ["{}"]
    route = _route_seq(fire, miss, fire)  # fires 2 of 3
    assert count_fires("q", "bootstrap", "/plugin", "sonnet", route=route) == 2


def test_count_fires_zero_when_never_fires():
    miss = ["{}"]
    route = _route_seq(miss, miss, miss)
    assert count_fires("q", "bootstrap", "/plugin", "sonnet", route=route) == 0


def test_count_fires_respects_passes_argument():
    fire = [_skill_line("bootstrap")]
    route = _route_seq(fire, fire)
    assert count_fires("q", "bootstrap", "/plugin", "sonnet", passes=2, route=route) == 2


def test_count_fires_default_timeout_is_20():
    # Routing is a snap decision; the budget is tight. A query that hasn't routed
    # within 20s is treated as a non-fire by _route_once, not waited out for minutes.
    seen = {}

    def route(query, repo_root, model, timeout, **kwargs):
        seen["timeout"] = timeout
        return ["{}"]

    count_fires("q", "bootstrap", "/p", "sonnet", passes=1, threshold=1, route=route)
    assert seen["timeout"] == 20


def test_count_fires_passes_effort_and_skill_name_to_route():
    # count_fires threads the effort level and the skill name through to the router,
    # so _route_once can set --effort and mirror the early-stop on our skill.
    seen = {}

    def route(query, repo_root, model, timeout, **kwargs):
        seen.update(kwargs)
        return ["{}"]

    count_fires(
        "q", "bootstrap", "/p", "sonnet",
        passes=1, threshold=1, route=route, effort="high",
    )
    assert seen["effort"] == "high"
    assert seen["skill_name"] == "bootstrap"


def test_count_fires_retries_transient_routing_error():
    # A failed routing call (rate limit, timeout) must not be miscounted as a
    # non-fire: it retries, and a subsequent success is counted normally.
    fire = [_skill_line("bootstrap")]
    calls = []

    def route(query, repo_root, model, timeout, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RoutingError("transient rate limit")
        return fire

    fires = count_fires(
        "q", "bootstrap", "/plugin", "sonnet",
        passes=1, attempts=3, route=route, sleep=_no_sleep,
    )
    assert fires == 1
    assert len(calls) == 2  # first failed, retry succeeded


def test_count_fires_raises_when_routing_keeps_failing():
    # A persistent routing failure propagates as an error rather than silently
    # counting as the skill not firing (a false negative).
    def route(query, repo_root, model, timeout, **kwargs):
        raise RoutingError("persistent failure")

    with pytest.raises(RoutingError):
        count_fires(
            "q", "bootstrap", "/plugin", "sonnet",
            passes=1, attempts=2, route=route, sleep=_no_sleep,
        )


# ---------------------------------------------------------------------------
# fire_threshold + threshold-based short-circuit
# ---------------------------------------------------------------------------


def test_fire_threshold_majority_ignores_should_trigger():
    assert fire_threshold("majority", True, 3) == 2
    assert fire_threshold("majority", False, 3) == 2


def test_fire_threshold_best_of_is_one():
    assert fire_threshold("best-of", True, 3) == 1
    assert fire_threshold("best-of", False, 3) == 1


def test_fire_threshold_asymmetric_lenient_positive_robust_negative():
    assert fire_threshold("asymmetric", True, 3) == 1   # should-trigger: any fire
    assert fire_threshold("asymmetric", False, 3) == 2  # should-not: majority


def test_fire_threshold_unknown_mode_raises():
    with pytest.raises(ValueError):
        fire_threshold("bogus", True, 3)


def test_count_fires_threshold_1_stops_on_first_fire():
    # best-of / asymmetric-positive: one fire locks the outcome — no extra passes.
    calls = []

    def route(query, repo_root, model, timeout, **kwargs):
        calls.append(1)
        return [_skill_line("bootstrap")]

    fires = count_fires("q", "bootstrap", "/plugin", "sonnet", passes=3, threshold=1, route=route)
    assert fires == 1
    assert len(calls) == 1


def test_count_fires_threshold_2_stops_when_majority_unreachable():
    # majority / asymmetric-negative: two misses lock not-fired before the 3rd pass.
    calls = []

    def route(query, repo_root, model, timeout, **kwargs):
        calls.append(1)
        return ["{}"]  # never fires

    fires = count_fires("q", "bootstrap", "/plugin", "sonnet", passes=3, threshold=2, route=route)
    assert fires == 0
    assert len(calls) == 2


def test_count_fires_on_pass_records_each_pass():
    # on_pass fires once per routing pass, with (duration_ms, fired, fired_skill).
    seq = [[_skill_line("bootstrap")], ["{}"], [_skill_line("bootstrap")]]
    route = _route_seq(*seq)
    records = []
    count_fires(
        "q", "bootstrap", "/plugin", "sonnet",
        passes=3, threshold=2, route=route,
        on_pass=lambda ms, fired, routed: records.append((ms, fired, routed)),
    )
    assert [(fired, routed) for _, fired, routed in records] == [
        (True, "bootstrap"), (False, None), (True, "bootstrap"),
    ]
    assert all(isinstance(ms, int) and ms >= 0 for ms, _, _ in records)


# ---------------------------------------------------------------------------
# first_dispatched_skill (routed-skill observability)
# ---------------------------------------------------------------------------


def test_first_dispatched_skill_returns_skill_tool_value():
    # `_skill_line(name)` emits a `Skill` tool_use with input={"skill": name}.
    assert first_dispatched_skill([_skill_line("knowledge-base:archive")]) == "knowledge-base:archive"


def test_first_dispatched_skill_returns_first_of_several():
    lines = [_skill_line("ingest"), _skill_line("archive")]
    assert first_dispatched_skill(lines) == "ingest"


def test_first_dispatched_skill_none_when_no_skill():
    text = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"},
    ]}})
    assert first_dispatched_skill([text, "not json", ""]) is None


def test_first_dispatched_skill_skips_malformed_lines():
    lines = ["not json", "", _skill_line("bootstrap")]
    assert first_dispatched_skill(lines) == "bootstrap"


# ---------------------------------------------------------------------------
# dispatches_skill (routing-decided signal)
# ---------------------------------------------------------------------------


def test_dispatches_skill_true_for_any_skill_tool_use():
    assert dispatches_skill(_skill_line("writing-prompts")) is True
    assert dispatches_skill(_skill_line("knowledge-base:archive")) is True


def test_dispatches_skill_false_for_non_skill_and_junk():
    read = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}},
    ]}})
    text = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"},
    ]}})
    assert dispatches_skill(read) is False
    assert dispatches_skill(text) is False
    assert dispatches_skill("not json") is False
    assert dispatches_skill("") is False


def test_dispatches_skill_matches_our_skill_namespaced_fallback():
    # Mirror detect_skill_fired's fallback: a tool_use whose name IS our skill counts
    # as a dispatch when skill_name is supplied — so the early-stop covers it too.
    line = _named_tool_line("writing-prompts")
    ns_line = _named_tool_line("knowledge-base:writing-prompts")
    assert dispatches_skill(line, "writing-prompts") is True
    assert dispatches_skill(ns_line, "writing-prompts") is True
    # Without a skill_name, a non-Skill tool_use is not a dispatch (can't tell it's a skill).
    assert dispatches_skill(line) is False
    # A different skill's tool name must not match (no substring false-positives).
    assert dispatches_skill(_named_tool_line("archive"), "writing-prompts") is False


def test_count_fires_uses_injected_detect_fired():
    # A detector that fires only on a sentinel line proves count_fires honors the
    # injected callable rather than the Claude-hardcoded default.
    def route(*a, **k):
        return ["FIRE"]

    def detect_fired(lines, skill_name):
        return "FIRE" in lines

    n = count_fires("q", "archive", "/repo", "sonnet",
                    route=route, detect_fired=detect_fired, passes=3, threshold=3)
    assert n == 3


# ---------------------------------------------------------------------------
# trigger_record — persisted per-sample trigger artifact
# ---------------------------------------------------------------------------


def test_trigger_record_persists_verdict_and_query():
    from evalspec.trigger import trigger_record

    rec = trigger_record(
        {"slug": "archive-meeting", "query": "archive `meeting.md` for me", "should_trigger": True},
        mode="asymmetric", threshold=1, fires=0,
        per_pass=[{"ms": 900, "fired": False}] * 3,
        model="opus",
    )

    assert rec["slug"] == "archive-meeting"
    assert rec["query"] == "archive `meeting.md` for me"
    assert rec["fired"] is False
    assert rec["passed"] is False  # should_trigger=True but never fired
    assert rec["passes_run"] == 3
    assert "xfail" not in rec


def test_trigger_record_passes_on_expected_nonfire():
    from evalspec.trigger import trigger_record

    rec = trigger_record(
        {"slug": "weather-query", "query": "what's the weather", "should_trigger": False},
        mode="asymmetric", threshold=2, fires=1,
        per_pass=[{"ms": 1, "fired": True}, {"ms": 1, "fired": False},
                  {"ms": 1, "fired": False}],
        model="opus",
    )

    assert rec["fired"] is False     # 1 < threshold 2
    assert rec["passed"] is True


def test_trigger_record_round_trips_fired_skill_in_per_pass():
    from evalspec.trigger import trigger_record

    query = {"slug": "ingest-article", "query": "q", "should_trigger": True}
    per_pass = [
        {"ms": 10, "fired": True, "fired_skill": "archive"},
        {"ms": 12, "fired": False, "fired_skill": "ingest"},
    ]
    rec = trigger_record(query, mode="best-of", threshold=1, fires=1, per_pass=per_pass, model="opus")
    assert rec["per_pass"] == per_pass
    assert rec["per_pass"][1]["fired_skill"] == "ingest"


def test_trigger_record_carries_xfail_reason():
    from evalspec.trigger import trigger_record

    rec = trigger_record(
        {"slug": "should-trigger-xfail", "query": "q", "should_trigger": True,
         "xfail": {"models": ["sonnet"], "reason": "documented sonnet routing boundary"}},
        mode="asymmetric", threshold=1, fires=0, per_pass=[],
        model="opus",
    )

    assert rec["xfail"] == {"models": ["sonnet"], "reason": "documented sonnet routing boundary"}


def test_trigger_record_carries_slug_and_model():
    from evalspec.trigger import trigger_record

    q = {"slug": "ingest-article", "query": "q", "should_trigger": True}
    rec = trigger_record(q, mode="majority", threshold=2, fires=2, per_pass=[], model="opus")
    assert rec["slug"] == "ingest-article"
    assert rec["model"] == "opus"
    assert "query_id" not in rec
    assert rec["fired"] is True and rec["passed"] is True


# ---------------------------------------------------------------------------
# xfail_applies (tier-scoped gate)
# ---------------------------------------------------------------------------


def test_xfail_applies_when_model_is_a_listed_tier():
    xf = {"models": ["sonnet", "haiku"], "reason": "r"}
    assert xfail_applies(xf, "sonnet") is True
    assert xfail_applies(xf, "haiku") is True


def test_xfail_does_not_apply_on_unlisted_tier():
    # opus is not listed, so the gate stays strict on an opus run.
    assert xfail_applies({"models": ["sonnet"], "reason": "r"}, "opus") is False


def test_xfail_applies_matches_provider_qualified_model_id():
    # OpenCode-style ids embed the tier; a substring match still relaxes the gate.
    assert xfail_applies({"models": ["sonnet"], "reason": "r"}, "anthropic/claude-sonnet-4-6") is True
    assert xfail_applies({"models": ["haiku"], "reason": "r"}, "anthropic/claude-sonnet-4-6") is False
