"""End-to-end artifact tests for a sample whose harness exits non-zero after producing output.

Each test drives the real adapter's `invoke` (Claude Code, Codex, OpenCode) through the real
`run_eval_arm` orchestration and artifact writer. Only the guest is a double: a `FakeSandbox`
that plays back a real harness stream on stdout and then exits non-zero, the way a harness
that crashes after its last tool call does. The sample must still be recorded as errored,
and it must keep its evidence: `session.jsonl`, a transcript with the tool calls it made,
and the tokens it spent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchspec.agents import CodingAgent
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.agents.codex import CodexAgent
from benchspec.agents.opencode import OpenCodeAgent
from benchspec.config.arms import Arm
from benchspec.grading.judges import JudgeConfig
from benchspec.orchestration import workspace
from benchspec.orchestration.execution import run_eval_arm
from benchspec.orchestration.results import RunResult
from benchspec.sandbox.sandbox import TurnRunner
from benchspec.specs.discovery import EvalCase
from benchspec.testing import FakeExecOutput, FakeSandbox

FIXTURES = Path(__file__).resolve().parents[1] / "agents" / "fixtures"

CLAUDE_STREAM = "\n".join(
    [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "result": "done",
                "is_error": False,
                "session_id": "s1",
                "duration_ms": 1500,
                "usage": {"input_tokens": 20, "output_tokens": 9},
            }
        ),
    ]
)


@pytest.fixture(autouse=True)
def _no_real_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `run_eval_arm` off a real sandbox runtime; the session double below holds the agent."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/benchspec-docker")
    monkeypatch.setattr(
        "benchspec.orchestration.execution.make_agent",
        lambda harness=None, provider="default": None,
    )
    monkeypatch.setattr(
        "benchspec.orchestration.execution.ensure_snapshot", lambda agent, **kwargs: "snap"
    )


class _CrashingHarnessSession:
    """A session whose one turn runs the real adapter against a guest that exits non-zero.

    Mirrors `SandboxSession._run`: the adapter's `invoke` is called with the same keyword
    shape the live session uses, so the whole adapter path from exec output to `RunResult`
    runs for real. The guest plays back `stream` on stdout and `stderr` on stderr.
    """

    def __init__(self, agent: CodingAgent, *, stream: str, stderr: str) -> None:
        """Hold the adapter under test and the canned guest output."""
        self._agent = agent
        self._sandbox = FakeSandbox(
            exec_outputs=[FakeExecOutput(exit_code=1, stdout_text=stream, stderr_text=stderr)]
        )

    def __call__(self, **kwargs: object) -> _CrashingHarnessSession:
        """Act as the session factory: `run_eval_arm` enters the returned session."""
        return self

    async def __aenter__(self) -> TurnRunner:
        """Enter the session, exposing the per-turn runner."""
        return self._run

    async def __aexit__(self, *exc: object) -> None:
        """Exit the session; the fake guest needs no teardown."""
        return None

    async def _run(
        self, prompt: str, *, resume_session_id: str | None, detect_skill: str | None
    ) -> RunResult:
        """Run the one turn through the real adapter."""
        return await self._agent.invoke(
            self._sandbox,
            prompt,
            eval_id="alpha",
            config="trial",
            workdir="/workspace",
            plugin_dir=None,
            model="provider/model",
            effort="medium",
            resume_session_id=resume_session_id,
            detect_skill=detect_skill,
            extra_env=None,
            harness_args=None,
        )


def _grade_all_pass(
    assertions: list[str],
    tree: str,
    contents: dict[str, str],
    shas: dict[str, str],
    final: str,
    eval_id: str,
    config: str,
    *,
    judge_config: JudgeConfig | None = None,
    original_shas: dict[str, str] | None = None,
    process_facts: str = "",
) -> dict:
    """Grade every assertion as passed; grading is not what these tests are about."""
    return {
        "eval_id": eval_id,
        "arm": config,
        "assertions": [
            {"text": assertion, "passed": True, "evidence": "ok"} for assertion in assertions
        ],
    }


def _punt_all(text: str) -> None:
    """Never bind an assertion, so no checker runs."""
    return None


def _eval_case(tmp_path: Path) -> EvalCase:
    """Build a one-prompt, one-assertion eval under `tmp_path`."""
    eval_dir = tmp_path / "skills" / "myskill" / "evals" / "alpha"
    eval_dir.mkdir(parents=True)
    return EvalCase(
        group="myskill",
        eval_dir=eval_dir,
        eval_file=eval_dir / "eval.md",
        eval={"id": "alpha", "prompt": "perform the task", "assertions": ["a"]},
    )


def _run_sample(
    tmp_path: Path, agent: CodingAgent, harness: str, *, stream: str, stderr: str
) -> Path:
    """Run one sample through `run_eval_arm` on a crashing guest and return its run dir."""
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()

    run_eval_arm(
        _eval_case(tmp_path),
        Arm("trial", harness, "provider/model"),
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=_CrashingHarnessSession(agent, stream=stream, stderr=stderr),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    return workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)


@pytest.mark.parametrize(
    ("harness", "agent", "stream"),
    [
        pytest.param(
            "claude-code",
            ClaudeCodeAgent(auth_value="sk-test", version="latest"),
            CLAUDE_STREAM,
            id="claude-code",
        ),
        pytest.param(
            "codex",
            CodexAgent(auth_value="sk-test", auth_env="CODEX_API_KEY", version="latest"),
            (FIXTURES / "codex_parse_success.jsonl").read_text(),
            id="codex",
        ),
        pytest.param(
            "opencode",
            OpenCodeAgent(auth_value="sk-test", auth_env="ANTHROPIC_API_KEY", version="latest"),
            (FIXTURES / "opencode_route_fired.jsonl").read_text(),
            id="opencode",
        ),
    ],
)
def test_errored_sample_keeps_its_stream_and_tokens(
    tmp_path: Path, harness: str, agent: CodingAgent, stream: str
) -> None:
    """A harness that exits non-zero after producing a stream still lands full artifacts."""
    run_dir = _run_sample(tmp_path, agent, harness, stream=stream, stderr="")

    grading = json.loads((run_dir / "grading.json").read_text())
    transcript = json.loads((run_dir / "transcript.json").read_text())
    timing = json.loads((run_dir / "timing.json").read_text())
    assert grading["errored"] is True
    assert transcript[0]["is_error"] is True
    assert transcript[0]["tool_call_count"] >= 1
    assert transcript[0]["result"] != ""
    assert timing["total_tokens"] > 0
    assert (run_dir / "session.jsonl").is_file()
    assert stream.strip() in (run_dir / "session.jsonl").read_text()


@pytest.mark.parametrize(
    ("harness", "agent", "stream"),
    [
        pytest.param(
            "claude-code",
            ClaudeCodeAgent(auth_value="sk-test", version="latest"),
            CLAUDE_STREAM,
            id="claude-code",
        ),
        pytest.param(
            "codex",
            CodexAgent(auth_value="sk-test", auth_env="CODEX_API_KEY", version="latest"),
            (FIXTURES / "codex_parse_success.jsonl").read_text(),
            id="codex",
        ),
        pytest.param(
            "opencode",
            OpenCodeAgent(auth_value="sk-test", auth_env="ANTHROPIC_API_KEY", version="latest"),
            (FIXTURES / "opencode_route_fired.jsonl").read_text(),
            id="opencode",
        ),
    ],
)
def test_errored_sample_headlines_stderr_but_keeps_the_stream(
    tmp_path: Path, harness: str, agent: CodingAgent, stream: str
) -> None:
    """When the crashed harness wrote to stderr, that is the transcript's result text."""
    run_dir = _run_sample(tmp_path, agent, harness, stream=stream, stderr="segfault at exit\n")

    transcript = json.loads((run_dir / "transcript.json").read_text())
    timing = json.loads((run_dir / "timing.json").read_text())
    assert transcript[0]["is_error"] is True
    assert "segfault at exit" in transcript[0]["result"]
    assert transcript[0]["tool_call_count"] >= 1
    assert timing["total_tokens"] > 0
    assert (run_dir / "session.jsonl").is_file()
