"""End-to-end tests for per-sample time and token accounting, per harness.

Each test drives the real adapter's `invoke` (Claude Code, Codex, OpenCode) through the real
`run_eval_arm` orchestration, artifact writer, and benchmark builder. Only the guest is a
double: a sandbox that plays back a stream captured from the live CLI and takes a measurable
moment to exit cleanly. Every harness must land a nonzero duration and a full token split in
`timing.json`, so the report's time-per-sample and tokens-per-sample mean something for it.
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
from benchspec.reporting import report
from benchspec.sandbox.sandbox import TurnRunner
from benchspec.specs.discovery import EvalCase
from benchspec.testing import FakeExecOutput
from tests.agents.doubles import SLOW_EXEC_SECONDS, SlowSandbox

FIXTURES = Path(__file__).resolve().parents[1] / "agents" / "fixtures"

HARNESS_CAPTURES = [
    pytest.param(
        "claude-code",
        ClaudeCodeAgent(auth_value="sk-test", version="latest"),
        "claude_stream_openrouter.jsonl",
        id="claude-code",
    ),
    pytest.param(
        "codex",
        CodexAgent(auth_value="sk-test", auth_env="CODEX_API_KEY", version="latest"),
        "codex_exec_openrouter.jsonl",
        id="codex",
    ),
    pytest.param(
        "opencode",
        OpenCodeAgent(auth_value="sk-test", auth_env="ANTHROPIC_API_KEY", version="latest"),
        "opencode_run_openrouter.jsonl",
        id="opencode",
    ),
]


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


class _ReplayingHarnessSession:
    """A session whose one turn runs the real adapter against a guest replaying a live capture.

    Mirrors `SandboxSession._run`: the adapter's `invoke` is called with the same keyword
    shape the live session uses, so the whole adapter path from exec output to `RunResult`
    runs for real. The guest takes `SLOW_EXEC_SECONDS`, then exits 0 with `stream` on stdout.
    """

    def __init__(self, agent: CodingAgent, *, stream: str) -> None:
        """Hold the adapter under test and the captured guest output."""
        self._agent = agent
        self._sandbox = SlowSandbox(exec_outputs=[FakeExecOutput(exit_code=0, stdout_text=stream)])

    def __call__(self, **kwargs: object) -> _ReplayingHarnessSession:
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


def _run_sample(tmp_path: Path, agent: CodingAgent, harness: str, *, stream: str) -> Path:
    """Run one clean sample through `run_eval_arm` and return its run dir."""
    workspace.set_current_iteration("iteration_01")
    eval_dir = tmp_path / "skills" / "myskill" / "evals" / "alpha"
    eval_dir.mkdir(parents=True)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    run_eval_arm(
        EvalCase(
            group="myskill",
            eval_dir=eval_dir,
            eval_file=eval_dir / "eval.md",
            eval={"id": "alpha", "prompt": "perform the task", "assertions": ["a"]},
        ),
        Arm("trial", harness, "provider/model"),
        workdir,
        {},
        tmp_path,
        today="2099-01-01",
        repo_root=tmp_path,
        sample=0,
        session_factory=_ReplayingHarnessSession(agent, stream=stream),
        grade=_grade_all_pass,
        bind=_punt_all,
    )

    return workspace.arm_dir(tmp_path, "myskill", "alpha", "trial", sample=0)


@pytest.mark.parametrize(("harness", "agent", "capture"), HARNESS_CAPTURES)
def test_clean_sample_records_its_time_and_token_split(
    tmp_path: Path, harness: str, agent: CodingAgent, capture: str
) -> None:
    """Every harness lands a real duration and a nonzero input/output split in timing.json."""
    stream = (FIXTURES / capture).read_text()

    run_dir = _run_sample(tmp_path, agent, harness, stream=stream)

    grading = json.loads((run_dir / "grading.json").read_text())
    timing = json.loads((run_dir / "timing.json").read_text())
    assert grading["errored"] is False
    assert timing["duration_ms"] >= SLOW_EXEC_SECONDS * 1000
    assert timing["total_tokens"] > 0
    assert timing["input_tokens"] > 0
    assert timing["output_tokens"] > 0


@pytest.mark.parametrize(("harness", "agent", "capture"), HARNESS_CAPTURES)
def test_benchmark_reports_time_and_tokens_per_sample(
    tmp_path: Path, harness: str, agent: CodingAgent, capture: str
) -> None:
    """The benchmark built from the run carries a nonzero time and token mean for the arm."""
    stream = (FIXTURES / capture).read_text()
    _run_sample(tmp_path, agent, harness, stream=stream)

    benchmark = report.build_benchmark(
        report.discover_eval_dirs(workspace.skills_root(tmp_path)), label="iteration_01"
    )

    arm_stats = benchmark["arms"]["trial"]
    assert arm_stats["duration_ms_mean"] >= SLOW_EXEC_SECONDS * 1000
    assert arm_stats["tokens_mean"] > 0
