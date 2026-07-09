"""The coding-agent interface.

A `CodingAgent` hides everything agent-specific behind one boundary: where its home
lives in the guest, how to provision the CLI into a microVM (the cached step), which
secrets it needs, how to stage local skills, how to build its headless command, and how
to parse its output. `sandbox.py` drives a live sandbox through this interface and never
names a concrete agent; adding a second agent is additive, not a refactor.

One adapter per harness, transport-blind: the adapter builds commands and parses
output, and an `evalspec.environments.ExecutionEnv` decides where the process runs —
`GuestSandbox` for task arms (`invoke`), `Host` for grading (`judge`). Sandbox-vs-host
is a parameter of the call, not a code path baked into each harness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from evalspec.runner import RunResult

if TYPE_CHECKING:
    from evalspec.environments import ExecutionEnv
    from evalspec.judges.config import JudgeConfig

# The agent-neutral home every per-cell `setup.sh` copies skills into. Each agent
# symlinks its own load dir here once at provision, so the install path is identical
# across agents and the per-agent load dir is the only agent-specific fact.
FIXED_SKILLS_HOME = "/home/evalspec/skills"


class BaseAgent:
    """Store base agent data."""

    host_bin: str  # the harness's host-side binary name (judge mode + version probe)

    @classmethod
    def probe_host_version(cls: object) -> str | None:
        """Best-effort `host_bin --version` probe — never raises, never fails the run."""
        try:
            proc = subprocess.run(
                [cls.host_bin, "--version"], capture_output=True, text=True, timeout=10
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def bridge_skills_home_script(self: object) -> str:
        """Bridge skills home script."""
        skill_dir = self.skill_load_dir
        parent = skill_dir.rsplit("/", 1)[0]
        return (
            f"mkdir -p {FIXED_SKILLS_HOME} && "
            f"mkdir -p {parent} && rm -rf {skill_dir} && "
            f"ln -s {FIXED_SKILLS_HOME} {skill_dir}"
        )

    def cell_env(self: object, *, arm: str, model: str, eval_set: str = "") -> dict:
        """EVALSPEC_* are informational for setup.sh — they do NOT route the task model.

        (that goes through arm.model). EVALSPEC_SET names the explicitly-selected set
        (--evalspec-set / make evals SET=) so setup.sh can branch on it; empty when the
        run falls back to the pyproject default-set.
        """
        return {
            **self.guest_env(),
            "EVALSPEC_ARM": arm,
            "EVALSPEC_MODEL": model,
            "EVALSPEC_HARNESS": self.id,
            "EVALSPEC_SET": eval_set,
        }


@dataclass(frozen=True)
class AgentCapabilities:
    """What the harness can honestly do with this agent.

    Typed, not a dict: every field here has a consumer in the harness, and an
    unknown field is a type error rather than silently ignored documentation.
    """

    efforts: tuple[str, ...]  # values the agent's CLI accepts for the effort flag
    multi_turn: bool  # honors resume_session_id (session chaining across turns)
    token_split: bool  # reports input/output token split (enables cost estimates)


@runtime_checkable
class CodingAgent(Protocol):
    """Define the coding agent interface."""

    id: str  # snapshot-cache key + report label
    guest_home: str  # the agent's HOME inside the guest (where skills are staged, runs cwd)
    skill_load_dir: str  # absolute guest path the agent loads skills from
    host_bin: str  # host-side binary name (judge mode + version probe)
    capabilities: AgentCapabilities

    def version(self: object) -> str:
        """Return the agent CLI version string."""
        ...

    def bridge_skills_home_script(
        self: object,
    ) -> str:
        """Bridge skills home script."""
        ...

    def cell_env(self: object, *, arm: str, model: str, eval_set: str = "") -> dict:
        """Return per-cell environment variables for an arm run."""
        ...

    def artifact_dirs(
        self: object,
    ) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        ...

    #   artifacts (e.g. ~/.claude/skills). These live in the VM, NOT the workdir mount, so
    #   the session snapshots them and merges the agent-authored files (diffed against the
    #   staged baseline) into the judge's facts. Each agent scaffolds skills differently, so
    #   each owns its answer; return [] for an agent that writes only to the workdir.
    def secrets(self: object) -> list:
        """Return secret values that must be redacted from logs."""
        ...

    def guest_env(self: object) -> dict:
        """Return environment variables passed to guest agent commands."""
        ...

    def build_command(
        self: object,
        prompt: str,
        *,
        plugin_dir: str | None,
        model: str,
        effort: str,
        resume_session_id: str | None,
        detect_skill: str | None,
        harness_args: list[str] | None = None,
    ) -> list[str]:
        """Build the guest command used to invoke the agent."""
        ...

    async def provision(self: object, sandbox: object) -> None:
        """Install the agent CLI and credentials inside the guest."""
        ...

    async def stage_project_assets(self: object, sandbox: object, project_mount: str) -> None:
        """Copy project-local assets needed by the guest agent."""
        ...

    async def invoke(
        self: object,
        sandbox: object,
        prompt: str,
        *,
        eval_id: str,
        config: str,
        workdir: str,
        plugin_dir: str | None,
        model: str,
        effort: str,
        resume_session_id: str | None,
        detect_skill: str | None,
        harness_args: list[str] | None = None,
        extra_env: dict | None = None,
    ) -> RunResult:
        """Run one prompt through the agent inside the guest."""
        ...

    async def judge(
        self: object,
        prompt: str,
        config: JudgeConfig,
        *,
        env: ExecutionEnv | None = None,
    ) -> str:
        """Grade a judge prompt in `env` (default: a fresh Host process).

        Returns the {"result": "<judge-json-string>"} envelope judge.py parses.
        Raises RuntimeError for the harness's infra-failure shapes.
        """
        ...

    @classmethod
    def probe_host_version(cls: object) -> str | None:
        """Return the host CLI version, or None on any failure (best effort)."""
        ...

    def detect_dispatch(self: object, line: str, skill_name: str | None) -> bool:
        """Return whether one stream line shows a skill dispatch."""
        ...

    def detect_fired(self: object, lines: object, skill_name: str) -> bool:
        """Return whether stream lines show the expected skill firing."""
        ...

    def streamed_activity(self: object, lines: object) -> bool:
        """Return whether streamed output shows meaningful agent activity."""
        ...
