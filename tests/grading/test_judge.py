"""Tests for judge."""

import json
import subprocess
from typing import NoReturn

import pytest

from evalspec.grading.judge import build_judge_prompt, grade_run, parse_judge_json
from evalspec.grading.judges import JudgeConfig


def test_judge_prompt_contains_assertions_facts_and_output_contract() -> None:
    """Verify the prompt carries assertions, facts, and the output contract."""
    prompt = build_judge_prompt(
        assertions=["the file X exists", "Y is byte-identical"],
        tree="vault/\n  a.md",
        file_contents={"a.md": "hello"},
        shas={"a.md": "abc123"},
        final_message="I archived it.",
        original_shas={"a.md": "deadbeef01"},
    )
    assert "the file X exists" in prompt
    assert "Y is byte-identical" in prompt
    assert "vault/" in prompt
    assert "abc123" in prompt
    assert "I archived it." in prompt
    # must instruct strict JSON output with the grading shape
    assert "passed" in prompt
    assert "evidence" in prompt
    assert "JSON" in prompt or "json" in prompt
    # original SHA block must be present
    assert "ORIGINAL FILES" in prompt
    assert "deadbeef01" in prompt


def test_judge_prompt_no_original_shas_omits_block() -> None:
    """Verify the ORIGINAL FILES block is omitted when no original SHAs given."""
    prompt = build_judge_prompt(
        assertions=["the file X exists"],
        tree="vault/",
        file_contents={},
        shas={},
        final_message="done",
    )
    assert "ORIGINAL FILES" not in prompt


def test_parse_judge_json_builds_grading() -> None:
    """Verify parse_judge_json builds a grading dict."""
    raw = '{"assertions":[{"text":"a","passed":true,"evidence":"x"}]}'
    grade = parse_judge_json(raw, eval_id="e1", config="without_skill")
    assert grade["eval_id"] == "e1"
    assert grade["arm"] == "without_skill"
    assert grade["assertions"][0]["passed"] is True
    assert grade["assertions"][0]["evidence"] == "x"


def test_parse_judge_json_strips_markdown_fence() -> None:
    """Verify a ```json fence is stripped before parsing."""
    # The judge wraps its JSON in a ```json fence in practice.
    raw = '```json\n{"assertions":[{"text":"a","passed":true,"evidence":"x"}]}\n```'
    grade = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert grade["assertions"][0]["passed"] is True


def test_parse_judge_json_strips_prose_preamble() -> None:
    """Verify surrounding prose is stripped before parsing."""
    raw = (
        'Here is my grading:\n'
        '{"assertions":[{"text":"a","passed":false,"evidence":"y"}]}\nDone.'
    )
    grade = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert grade["assertions"][0]["passed"] is False
    assert grade["assertions"][0]["evidence"] == "y"


def test_parse_judge_json_no_object_raises() -> None:
    """Verify a missing JSON object raises ValueError."""
    with pytest.raises(ValueError, match="no JSON object"):
        parse_judge_json("no json here", eval_id="e1", config="with_skill")


def test_parse_judge_json_ignores_prose_braces_before_object() -> None:
    """Verify stray prose braces do not derail object extraction."""
    # Prose containing stray braces (e.g. echoing a {PLACEHOLDER}) must not derail the
    # extraction — the real assertions object is found regardless.
    raw = (
        'Consider {PLACEHOLDER} and {TODAY}. Grading: '
        '{"assertions":[{"text":"a","passed":true,"evidence":"e"}]}'
    )
    grade = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert grade["assertions"][0]["passed"] is True


def test_parse_judge_json_coerces_quoted_false_to_false() -> None:
    """Verify a quoted "false" value is coerced to False."""
    # bool("false") is True in Python — a judge that quotes the value must not flip
    # a failing assertion to passed.
    raw = '{"assertions":[{"text":"a","passed":"false","evidence":"e"}]}'
    grade = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert grade["assertions"][0]["passed"] is False


def test_parse_judge_json_coerces_quoted_true_to_true() -> None:
    """Verify a quoted "true" value is coerced to True."""
    raw = '{"assertions":[{"text":"a","passed":"true","evidence":"e"}]}'
    grade = parse_judge_json(raw, eval_id="e1", config="with_skill")
    assert grade["assertions"][0]["passed"] is True


def test_grade_run_calls_run_judge_with_the_resolved_config(monkeypatch: object) -> None:
    """Verify grade_run forwards the resolved judge config to run_judge."""
    captured = {}

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        captured["config"] = config
        return (
            '{"result": "{\\"assertions\\": [{\\"text\\": \\"a1\\", '
            '\\"passed\\": true, \\"evidence\\": \\"ok\\"}]}"}'
        )

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    judge_config = JudgeConfig(harness="codex", model="gpt-5.5")
    result = grade_run(
        ["a1"], "tree", {}, {}, "final message", "eval1", "trial",
        judge_config=judge_config,
    )

    assert captured["config"] == judge_config
    assert result["assertions"][0]["passed"] is True


def test_grade_run_defaults_to_a_default_judge_config_when_none_given(monkeypatch: object) -> None:
    """Verify grade_run falls back to a default JudgeConfig when none given."""
    captured = {}

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        captured["config"] = config
        return (
            '{"result": "{\\"assertions\\": [{\\"text\\": \\"a1\\", '
            '\\"passed\\": true, \\"evidence\\": \\"ok\\"}]}"}'
        )

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    grade_run(["a1"], "tree", {}, {}, "final message", "eval1", "trial")

    assert captured["config"] == JudgeConfig()  # harness=claude-code, model=sonnet, ...


def test_grade_run_timeout_records_error_not_raises(monkeypatch: object) -> None:
    """Verify a judge timeout is recorded as a graded error, not raised."""
    # A hung judge must be recorded as a graded error, not crash the whole arm.
    def fake_run_judge(prompt: object, *, config: object) -> NoReturn:
        """Grade."""
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    grade = grade_run(["a1", "a2"], "tree", {}, {}, "msg", "e1", "with_skill")

    assert [assertion["passed"] for assertion in grade["assertions"]] == [False, False]
    assert all("JUDGE ERROR" in assertion["evidence"] for assertion in grade["assertions"])


def test_grade_run_assertion_count_mismatch_is_error(monkeypatch: object) -> None:
    """Verify a misaligned assertion count is treated as unparseable."""
    # The judge drops an assertion → misaligned response treated as unparseable.
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "e"}]})

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        return json.dumps({"result": inner})

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    grade = grade_run(["a1", "a2"], "tree", {}, {}, "msg", "e1", "with_skill")

    assert all("JUDGE ERROR" in assertion["evidence"] for assertion in grade["assertions"])


def test_grade_run_happy_path(monkeypatch: object) -> None:
    """Verify grade_run happy path."""
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "ok"}]})

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        return json.dumps({"result": inner})

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    grade = grade_run(["a1"], "tree", {}, {}, "msg", "e1", "with_skill")
    assert grade["assertions"] == [{"text": "a1", "passed": True, "evidence": "ok"}]


def test_grade_run_does_not_mask_judge_infra_error(monkeypatch: object) -> None:
    """Verify grade_run does not mask an infra-level judge failure."""
    # An infra-level judge failure (missing host CLI, auth, rate limit) reaches
    # grade_run as RuntimeError from judges.run_judge. grade_run must NOT catch it — no
    # fake "JUDGE ERROR" mask — so it propagates and run_eval_arm can mark the arm
    # errored.
    def boom(prompt: object, *, config: object) -> NoReturn:
        """Grade."""
        raise RuntimeError("host claude CLI not found on PATH")

    monkeypatch.setattr("evalspec.grading.judge.run_judge", boom)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        grade_run(["a1"], "tree", {}, {}, "msg", "e1", "with_skill")


def test_grade_run_does_not_catch_runtimeerror(monkeypatch: object) -> None:
    """Verify grade_run does not catch a RuntimeError from run_judge."""
    def boom(prompt: object, *, config: object) -> NoReturn:
        """Grade."""
        raise RuntimeError("host claude CLI returned is_error=true: Not logged in")

    monkeypatch.setattr("evalspec.grading.judge.run_judge", boom)

    with pytest.raises(RuntimeError, match="Not logged in"):
        grade_run(["a1"], "tree", {}, {}, "final", "eval1", "trial")


def test_grade_run_passes_judge_config_through(monkeypatch: object) -> None:
    """Verify grade_run passes an explicit judge config through to run_judge."""
    inner = json.dumps({"assertions": [{"text": "a1", "passed": True, "evidence": "ok"}]})
    captured = {}

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        captured["config"] = config
        return json.dumps({"result": inner})

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    judge_config = JudgeConfig(harness="opencode", model="anthropic/claude-sonnet-4-6", timeout=42)
    grade_run(["a1"], "tree", {}, {}, "msg", "e1", "with_skill", judge_config=judge_config)

    assert captured["config"] == judge_config


def test_build_judge_prompt_injects_process_facts() -> None:
    """Verify process facts are injected into the prompt."""
    prompt = build_judge_prompt(
        ["uses writing-prompts"], tree="x", file_contents={}, shas={},
        final_message="Applied the editorial pass inline.",
        process_facts="Turn 1: Skill(writing-prompts), Write",
    )
    assert "PROCESS / TOOL ACTIVITY" in prompt
    assert "Skill(writing-prompts)" in prompt


def test_build_judge_prompt_omits_process_section_when_empty() -> None:
    """Verify the process section is omitted when no process facts given."""
    prompt = build_judge_prompt(
        ["a"], tree="x", file_contents={}, shas={}, final_message="done",
    )
    assert "PROCESS / TOOL ACTIVITY" not in prompt


def test_grade_run_passes_process_facts_into_prompt(monkeypatch: object) -> None:
    """Verify grade_run threads process facts into the judge prompt."""
    captured = {}

    def fake_run_judge(prompt: object, *, config: object) -> str:
        """Grade."""
        captured["prompt"] = prompt
        return json.dumps({"result": json.dumps(
            {"assertions": [{"text": "a", "passed": True, "evidence": "Skill(writing-prompts)"}]}
        )})

    monkeypatch.setattr("evalspec.grading.judge.run_judge", fake_run_judge)

    grade_run(["a"], "tree", {}, {}, "final", "e1", "with_skill",
              process_facts="Turn 1: Skill(writing-prompts)")
    assert "Skill(writing-prompts)" in captured["prompt"]
