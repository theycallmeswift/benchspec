"""Tests for the skill-activation primitives and the activation stop rule."""

import json

from benchspec.grading.trajectory import extract_trajectory
from benchspec.grading.trigger import detect_skill_fired, settled_once_dispatched


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


def _malformed_shape_lines() -> list[str]:
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


def _settled_after(skills: set[str], lines: list[str]) -> list[bool]:
    """Whether the rule over `skills` is settled after each of `lines`, read as Claude's stream."""
    stop = settled_once_dispatched(skills)
    trajectory: list[dict] = []
    verdicts = []
    for line in lines:
        trajectory.extend(extract_trajectory(line))
        verdicts.append(stop(trajectory))
    return verdicts


def _read_line() -> str:
    """A non-skill tool call."""
    return _named_tool_line("Read")


def test_invoked_line_is_settled_by_its_skills_first_dispatch() -> None:
    """Verify `X invoked` alone is settled by X's dispatch."""
    verdicts = _settled_after({"hello"}, ['{"type":"system"}', _skill_line("hello")])

    assert verdicts == [False, True]


def test_two_skills_stay_open_until_both_fire() -> None:
    """Verify `X invoked` + `Y not invoked` is not settled by X alone."""
    verdicts = _settled_after({"hello", "goodbye"}, [_skill_line("hello"), _skill_line("goodbye")])

    assert verdicts == [False, True]


def test_an_unrelated_skill_dispatch_does_not_settle() -> None:
    """Verify another skill firing leaves the named skill's verdict open."""
    verdicts = _settled_after({"hello"}, [_skill_line("hello-world"), _skill_line("other:hello2")])

    assert verdicts == [False, False]


def test_a_namespaced_or_fallback_dispatch_settles() -> None:
    """Verify both fire shapes grading recognizes also settle the run."""
    assert _settled_after({"hello"}, [_skill_line("greetings:hello")]) == [True]
    assert _settled_after({"hello"}, [_named_tool_line("greetings:hello")]) == [True]


def test_an_unsettled_run_is_never_stopped() -> None:
    """Verify tool calls and text alone never settle a run whose skill has not fired."""
    text = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    )

    verdicts = _settled_after({"hello"}, [_read_line()] * 20 + [text])

    assert verdicts == [False] * 21


def test_no_skills_is_settled_at_once() -> None:
    """Verify a rule over no skills has nothing to wait for."""
    assert settled_once_dispatched(set())([]) is True
