"""Tests for the retained skill-activation detection primitives in harnessbench.grading.trigger."""

import json

from harnessbench.grading.trigger import (
    RoutingError,
    detect_skill_fired,
    dispatches_skill,
    streamed_activity,
)


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
    """Build the named tool line test fixture."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": name, "input": {}},
                ]
            },
        }
    )


def test_retained_primitives_are_importable() -> None:
    """Verify the kept routing primitives remain importable and callable."""
    assert issubclass(RoutingError, RuntimeError)
    assert callable(dispatches_skill)
    assert callable(streamed_activity)


def test_retained_detect_skill_fired_detects_a_minimal_fire() -> None:
    """Verify the retained detect_skill_fired reports True for a minimal skill fire."""
    assert detect_skill_fired([_skill_line("bootstrap")], "bootstrap") is True


def test_detect_skill_fired_namespaced_matches_bare_name() -> None:
    """Verify detect skill fired namespaced matches bare name."""
    # Real shape: input.skill == "knowledge-base:bootstrap", query "bootstrap"
    assert detect_skill_fired([_skill_line("knowledge-base:bootstrap")], "bootstrap") is True


def test_detect_skill_fired_bare_skill_name() -> None:
    """Verify detect skill fired bare skill name."""
    # input.skill == "bootstrap", query "bootstrap"
    assert detect_skill_fired([_skill_line("bootstrap")], "bootstrap") is True


def test_detect_skill_no_fire_text_block_only() -> None:
    """Verify detect skill no fire text block only."""
    # Assistant message with only a text block — no tool_use
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Here is your answer."}]},
    }
    assert detect_skill_fired([json.dumps(event)], "bootstrap") is False


def test_detect_skill_no_fire_different_skill() -> None:
    """Verify detect skill no fire different skill."""
    # Guards against substring bugs: "archive" must not match query "bootstrap"
    assert detect_skill_fired([_skill_line("knowledge-base:archive")], "bootstrap") is False


def test_detect_skill_fires_through_malformed_and_blank_lines() -> None:
    """Verify detect skill fires through malformed and blank lines."""
    # Malformed JSON and blank lines are skipped, not fatal — a real fire still registers.
    lines = ["not json", "", "  ", _skill_line("knowledge-base:bootstrap")]
    assert detect_skill_fired(lines, "bootstrap") is True


def test_detect_skill_malformed_and_blank_lines_alone_do_not_fire() -> None:
    """Verify detect skill malformed and blank lines alone do not fire."""
    assert detect_skill_fired(["not json", "", "  "], "bootstrap") is False


def test_detect_skill_no_match_returns_false_on_empty() -> None:
    """Verify detect skill no match returns false on empty."""
    assert detect_skill_fired([], "bootstrap") is False


def _malformed_shape_lines() -> object:
    """Valid-JSON-but-unexpected shapes that must never crash detection (the caller's.

    retry loop only catches subprocess failures, so an AttributeError here would be
    fatal).
    """
    return [
        "5",  # bare scalar
        "[1, 2, 3]",  # bare array
        json.dumps({"type": "assistant", "message": None}),  # null message
        json.dumps({"type": "assistant", "message": {"content": None}}),  # null content
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": None,
                            "input": None,
                        },  # null name/input
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": None},
                        },  # null skill
                    ]
                },
            }
        ),
    ]


def test_detect_skill_fires_after_malformed_shapes() -> None:
    """Verify detect skill fires after malformed shapes."""
    lines = _malformed_shape_lines() + [_skill_line("knowledge-base:bootstrap")]
    assert detect_skill_fired(lines, "bootstrap") is True


def test_detect_skill_malformed_shapes_alone_do_not_crash_or_fire() -> None:
    """Verify detect skill malformed shapes alone do not crash or fire."""
    assert detect_skill_fired(_malformed_shape_lines(), "bootstrap") is False


def test_dispatches_skill_true_for_any_skill_tool_use() -> None:
    """Verify dispatches skill true for any skill tool use."""
    assert dispatches_skill(_skill_line("writing-prompts")) is True
    assert dispatches_skill(_skill_line("knowledge-base:archive")) is True


def test_dispatches_skill_false_for_non_skill_and_junk() -> None:
    """Verify dispatches skill false for non skill and junk."""
    read = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}},
                ]
            },
        }
    )
    text = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hi"},
                ]
            },
        }
    )
    assert dispatches_skill(read) is False
    assert dispatches_skill(text) is False
    assert dispatches_skill("not json") is False
    assert dispatches_skill("") is False


def test_dispatches_skill_matches_our_skill_namespaced_fallback() -> None:
    """Verify dispatches skill matches our skill namespaced fallback."""
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


def test_streamed_activity_true_when_turn_began() -> None:
    """Verify streamed_activity is true once an assistant event is seen."""
    lines = ['{"type":"system"}', _skill_line("bootstrap")]
    assert streamed_activity(lines) is True


def test_streamed_activity_false_on_startup_only() -> None:
    """Verify streamed_activity is false when only a startup/init line streamed."""
    assert streamed_activity(['{"type":"system"}', "", "not json"]) is False
