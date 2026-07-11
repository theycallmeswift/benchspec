"""Classify discovered assertions deterministic/activation/judge-backed before a run.

The injected `bind` stub stands in for the real binder so no test touches the network;
each test pins one assertion shape to the label it must carry.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from textwrap import dedent

import pytest

from evalspec import analyze, discovery, workspace
from evalspec.arms import Arm
from evalspec.binder import _bind_bare_exists, _bind_skill_invoked
from evalspec.execution import run_eval_arm
from evalspec.runner import RunResult
from evalspec.schema import SchemaError

_ACTIVATION_FIXTURE = Path(__file__).parent / "fixtures" / "activation"


def _bind_like_binder(text: object) -> object:
    """Bind stub mirroring the real binder for the three canonical assertion shapes."""
    if text == "Skill `ingest` invoked":
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}
    if text == "a file exists at ./notes.md":
        return {"type": "deterministic", "checker": "file_exists", "path": "./notes.md"}
    return None


def _write_eval(
    tmp_path: object, assertions: object, *, skill: object = "demo", slug: object = "a"
) -> object:
    """Write a minimal discoverable eval under a `skills/<skill>/evals/<slug>/` tree."""
    group_dir = tmp_path / "skills" / skill / "evals" / slug
    group_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {assertion}\n" for assertion in assertions)
    header = dedent("""\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

    """)
    (group_dir / "eval.md").write_text(header + body)
    return group_dir


def test_classify_assertion_labels_activation() -> None:
    """Verify a bound skill_invoked spec classifies as activation."""
    label = analyze.classify_assertion("Skill `ingest` invoked", bind=_bind_like_binder)

    assert label == "activation"


def test_classify_assertion_labels_deterministic() -> None:
    """Verify a bound non-activation spec classifies as deterministic."""
    label = analyze.classify_assertion("a file exists at ./notes.md", bind=_bind_like_binder)

    assert label == "deterministic"


def test_classify_assertion_labels_judge_backed_on_punt() -> None:
    """Verify a punt (bind returns None) classifies as judge-backed."""
    label = analyze.classify_assertion("the summary is accurate", bind=_bind_like_binder)

    assert label == "judge-backed"


def test_analyze_repo_labels_each_assertion(tmp_path: object) -> None:
    """Verify analyze_repo yields one right-labeled Classification per assertion."""
    _write_eval(
        tmp_path,
        ["Skill `ingest` invoked", "a file exists at ./notes.md", "the summary is accurate"],
    )

    classifications = analyze.analyze_repo(tmp_path, bind=_bind_like_binder)

    assert [classification.label for classification in classifications] == [
        "activation",
        "deterministic",
        "judge-backed",
    ]


def test_analyze_repo_records_file_and_eval_id(tmp_path: object) -> None:
    """Verify each Classification carries its source file and eval id."""
    group_dir = _write_eval(tmp_path, ["the summary is accurate"], slug="alpha")

    classifications = analyze.analyze_repo(tmp_path, bind=_bind_like_binder)

    assert classifications[0].file == group_dir / "eval.md"
    assert classifications[0].eval_id == "alpha"


def test_run_returns_zero_on_clean_suite(tmp_path: object, monkeypatch: object) -> None:
    """Verify run classifies a non-empty suite and returns 0, unlike lint's warning exit.

    The assertions all bind through the binder's offline fast paths, so the real
    `binder.bind` classifies them without a network call.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked", "The file ./notes.md exists"])

    exit_code = analyze.run(tmp_path)

    assert exit_code == 0


def test_run_surfaces_schema_error_on_malformed_eval(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a malformed eval surfaces a SchemaError rather than a nonzero-clean run."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    with pytest.raises(SchemaError):
        analyze.run(tmp_path)


def _bind_offline(text: object) -> object:
    """Bind through the binder's offline fast paths only; punt everything else.

    Mirrors what the real `binder.bind` does for the fixture without a network call,
    so tests classify and grade the committed activation eval deterministically.
    """
    return _bind_skill_invoked(text) or _bind_bare_exists(text)


def _fake_session_factory(result: object) -> object:
    """Single-turn session factory yielding one fixed RunResult for the arm's run."""

    @contextlib.asynccontextmanager
    async def factory(**_kwargs: object) -> object:
        """Yield a run callable returning the pre-baked result."""

        async def run(
            _prompt: object, *, resume_session_id: object, detect_skill: object
        ) -> object:
            """Return the fixed run result for the single graded turn."""
            return result

        yield run

    return factory


def _grade_all_pass(assertions: object, *_args: object, **_kwargs: object) -> object:
    """Judge stub passing every punted assertion so no network judge is called."""
    return {
        "assertions": [
            {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
        ]
    }


def test_activation_fixture_discovers_four_assertions() -> None:
    """Verify the committed fixture is discoverable and yields its four assertions."""
    cases = discovery.discover_eval_cases(_ACTIVATION_FIXTURE)

    assert len(cases) == 1
    assert cases[0].assertions == [
        "Skill `ingest` invoked",
        "Skill `codex` not invoked",
        "The file ./notes.md exists",
        "The summary is accurate",
    ]


def test_activation_fixture_classifies_each_assertion() -> None:
    """Verify analyze_repo labels the fixture activation/activation/deterministic/judge-backed."""
    classifications = analyze.analyze_repo(_ACTIVATION_FIXTURE, bind=_bind_offline)

    assert [classification.label for classification in classifications] == [
        "activation",
        "activation",
        "deterministic",
        "judge-backed",
    ]


def test_activation_fixture_grades_both_polarities_end_to_end(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a discover to grade run records both activation polarities as pass.

    The arm's trajectory dispatches `ingest` but not `codex`, so the positive
    `` Skill `ingest` invoked `` and the negative `` Skill `codex` not invoked ``
    both grade True off the arm's dispatched-skill set.
    """
    workspace.set_current_iteration("iteration_01")
    monkeypatch.setattr("evalspec.execution.make_agent", lambda harness=None: None)
    monkeypatch.setattr("evalspec.execution.ensure_snapshot", lambda agent, **kwargs: "snap")
    eval_case = discovery.discover_eval_cases(_ACTIVATION_FIXTURE)[0]
    trajectory = [
        {"kind": "tool_call", "id": "1", "name": "Skill", "arguments": {"skill": "ingest"}}
    ]
    workdir = tmp_path / "wd"
    workdir.mkdir()

    run_eval_arm(
        eval_case,
        Arm("trial", "claude-code", "opus"),
        workdir,
        {},
        None,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=_fake_session_factory(
            RunResult(
                "activation-demo", "trial", "out", 1, 1, False,
                session_id="s1", fired=True, trajectory=trajectory,
            )
        ),
        grade=_grade_all_pass,
        bind=_bind_offline,
    )

    run_dir = workspace.arm_dir(
        tmp_path, eval_case.skill, eval_case.eval_id, "trial", sample=0
    )
    grading = json.loads((run_dir / "grading.json").read_text())
    passed_by_text = {
        assertion["text"]: assertion["passed"] for assertion in grading["assertions"]
    }
    assert passed_by_text["Skill `ingest` invoked"] is True
    assert passed_by_text["Skill `codex` not invoked"] is True
