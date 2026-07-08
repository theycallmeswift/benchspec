"""Tests for judge."""

import json
import subprocess
from dataclasses import dataclass

import pytest

from evalspec.judge import build_judge_prompt, grade_run, parse_judge_json


@dataclass
class FakeAgent:
    """Test double matching the `judge` contract of CodingAgent: returns a canned.

    stdout string, or raises a stashed exception. Lets grade_run be tested without
    spawning a subprocess.
    """

    stdout: str = ""
    raises: BaseException | None = None
    calls: list[dict] = None

    def __post_init__(self: object) -> object:
        """Build the post init test fixture."""
        if self.calls is None:
            self.calls = []

    def judge(self: object, prompt: str, *, model: str, timeout: int = 300) -> str:
        """Judge."""
        self.calls.append({"prompt": prompt, "model": model, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.stdout


# ---------------------------------------------------------------------------
# build_judge_prompt
# ---------------------------------------------------------------------------


def test_judge_prompt_contains_assertions_facts_and_output_contract() -> None:
    """Verify judge prompt contains assertions facts and output contract."""
    p = build_judge_prompt(
        assertions=["the file X exists", "Y is byte-identical"],
        tree="vault/\n  a.md",
        file_contents={"a.md": "hello"},
        shas={"a.md": "abc123"},
        final_message="I archived it.",
        original_shas={"a.md": "deadbeef01"},
    )
    assert "the file X exists" in p
    assert "Y is byte-identical" in p
    assert "vault/" in p
    assert "abc123" in p
    assert "I archived it." in p
    # must instruct strict JSON output with the grading shape
    assert "passed" in p
    assert "evidence" in p
    assert "JSON" in p or "json" in p
    # original SHA block must be present
    assert "ORIGINAL FILES" in p
    assert "deadbeef01" in p


def test_judge_prompt_no_original_shas_omits_block() -> None:
    """Verify judge prompt no original shas omits block."""
    p = build_judge_prompt(
        assertions=["the file X exists"],
        tree="vault/",
        file_contents={},
        shas={},
        final_message="done",
    )
    assert "ORIGINAL FILES" not in p


# ---------------------------------------------------------------------------
# parse_judge_json
# ---------------------------------------------------------------------------


def test_parse_judge_json_builds_grading() -> None:
    """Verify parse judge json builds grading."""
    raw = '{"assertions":[{"text":"a","passed":true,"evidence":"x"}]}'
    g = parse_judge_json(raw, eval_id="e1", config="without_skill")
    assert g["eval_id"] == "e1"
    assert g["arm"] == "without_skill"
    assert g["assertions"][0]["passed"] is True
    assert g["assertions"][0]["evidence"] == "x"


def test_parse_judge_json_strips_markdown_fence() -> None:
    """Verify parse judge json strips markdown fence."""
    # The judge wraps its JSON in a ```json fence in practice.
    raw = '```json\n{"assertions":[{"text":"a","passed":true,"evidence":"x"}]}\n```'
    g = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert g["assertions"][0]["passed"] is True


def test_parse_judge_json_strips_prose_preamble() -> None:
    """Verify parse judge json strips prose preamble."""
    raw = 'Here is my grading:\n{"assertions":[{"text":"a","passed":false,"evidence":"y"}]}\nDone.'
    g = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert g["assertions"][0]["passed"] is False
    assert g["assertions"][0]["evidence"] == "y"


def test_parse_judge_json_no_object_raises() -> None:
    """Verify parse judge json no object raises."""
    with pytest.raises(ValueError, match="no JSON object"):
        parse_judge_json("no json here", eval_id="e1", config="with_skill")


def test_parse_judge_json_ignores_prose_braces_before_object() -> None:
    """Verify parse judge json ignores prose braces before object."""
    # Prose containing stray braces (e.g. echoing a {PLACEHOLDER}) must not derail the
    # extraction — the real assertions object is found regardless.
    raw = (
        "Consider {PLACEHOLDER} and {TODAY}. Grading: "
        '{"assertions":[{"text":"a","passed":true,"evidence":"e"}]}'
    )
    g = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert g["assertions"][0]["passed"] is True


def test_parse_judge_json_coerces_quoted_false_to_false() -> None:
    """Verify parse judge json coerces quoted false to false."""
    # bool("false") is True in Python — a judge that quotes the value must not flip
    # a failing assertion to passed.
    raw = '{"assertions":[{"text":"a","passed":"false","evidence":"e"}]}'
    g = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert g["assertions"][0]["passed"] is False


def test_parse_judge_json_coerces_quoted_true_to_true() -> None:
    """Verify parse judge json coerces quoted true to true."""
    raw = '{"assertions":[{"text":"a","passed":"true","evidence":"e"}]}'
    g = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert g["assertions"][0]["passed"] is True


# ---------------------------------------------------------------------------
# grade_run (agent.judge boundary stubbed via FakeAgent)
# ---------------------------------------------------------------------------


def test_grade_run_timeout_records_error_not_raises() -> None:
    """Verify grade run timeout records error not raises."""
    # A hung judge must be recorded as a graded error, not crash the whole arm.
    agent = FakeAgent(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1))

    g = grade_run(["a1", "a2"], "tree", {}, {}, "msg", "e1", "with_skill", agent=agent)

    assert [x["passed"] for x in g["assertions"]] == [False, False]
    assert all("JUDGE ERROR" in x["evidence"] for x in g["assertions"])


def test_grade_run_assertion_count_mismatch_is_error() -> None:
    """Verify grade run assertion count mismatch is error."""
    # The judge drops an assertion → misaligned response treated as unparseable.
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "e"}]})
    agent = FakeAgent(stdout=json.dumps({"result": inner}))

    g = grade_run(["a1", "a2"], "tree", {}, {}, "msg", "e1", "with_skill", agent=agent)

    assert all("JUDGE ERROR" in x["evidence"] for x in g["assertions"])


def test_grade_run_happy_path() -> None:
    """Verify grade run happy path."""
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "ok"}]})
    agent = FakeAgent(stdout=json.dumps({"result": inner}))
    g = grade_run(["a1"], "tree", {}, {}, "msg", "e1", "with_skill", agent=agent)
    assert g["assertions"] == [{"text": "a1", "passed": True, "evidence": "ok"}]


def test_grade_run_does_not_mask_judge_infra_error() -> None:
    """Verify grade run does not mask judge infra error."""
    # An infra-level judge failure (missing host CLI, auth, rate limit) reaches
    # grade_run as RuntimeError from agent.judge (run_host_judge normalizes a missing
    # binary's FileNotFoundError to RuntimeError). grade_run must NOT catch it — no fake
    # "JUDGE ERROR" mask — so it propagates and run_eval_arm can mark the arm errored.
    agent = FakeAgent(raises=RuntimeError("host claude CLI not found on PATH"))

    with pytest.raises(RuntimeError, match="not found on PATH"):
        grade_run(["a1"], "tree", {}, {}, "msg", "e1", "with_skill", agent=agent)


def test_grade_run_passes_model_and_timeout_to_agent() -> None:
    """Verify grade run passes model and timeout to agent."""
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "ok"}]})
    agent = FakeAgent(stdout=json.dumps({"result": inner}))

    grade_run(
        ["a1"],
        "tree",
        {},
        {},
        "msg",
        "e1",
        "with_skill",
        agent=agent,
        model="haiku",
        timeout=42,
    )

    assert agent.calls[0]["model"] == "haiku"
    assert agent.calls[0]["timeout"] == 42


def test_build_judge_prompt_injects_process_facts() -> None:
    """Verify build judge prompt injects process facts."""
    prompt = build_judge_prompt(
        ["uses writing-prompts"],
        tree="x",
        file_contents={},
        shas={},
        final_message="Applied the editorial pass inline.",
        process_facts="Turn 1: Skill(writing-prompts), Write",
    )
    assert "PROCESS / TOOL ACTIVITY" in prompt
    assert "Skill(writing-prompts)" in prompt


def test_build_judge_prompt_omits_process_section_when_empty() -> None:
    """Verify build judge prompt omits process section when empty."""
    prompt = build_judge_prompt(
        ["a"],
        tree="x",
        file_contents={},
        shas={},
        final_message="done",
    )
    assert "PROCESS / TOOL ACTIVITY" not in prompt


def test_grade_run_passes_process_facts_into_prompt() -> None:
    """Verify grade run passes process facts into prompt."""
    captured = {}

    class _Agent:
        """Store agent data."""

        def judge(self: object, prompt: object, *, model: object, timeout: object = 300) -> object:
            """Judge."""
            captured["prompt"] = prompt
            return json.dumps(
                {
                    "result": json.dumps(
                        {
                            "assertions": [
                                {
                                    "text": "a",
                                    "passed": True,
                                    "evidence": "Skill(writing-prompts)",
                                }
                            ]
                        }
                    )
                }
            )

    grade_run(
        ["a"],
        "tree",
        {},
        {},
        "final",
        "e1",
        "with_skill",
        agent=_Agent(),
        process_facts="Turn 1: Skill(writing-prompts)",
    )
    assert "Skill(writing-prompts)" in captured["prompt"]
