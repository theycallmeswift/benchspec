"""The coding-agent interface.

A `CodingAgent` hides everything agent-specific behind one boundary: where its home lives
in the guest, how to provision the CLI into a microVM (the cached step), which secrets it
needs, how to stage local skills, how to build its headless command, and how to parse its
output. `sandbox.py` drives a live sandbox through this interface and never names a concrete
agent; adding a second agent is additive, not a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from evalspec.runner import RunResult

# The agent-neutral home every per-cell `setup.sh` copies skills into. Each agent
# symlinks its own load dir here once at provision, so the install path is identical
# across agents and the per-agent load dir is the only agent-specific fact.
FIXED_SKILLS_HOME = "/home/evalspec/skills"


class BaseAgent:
    """Concrete CodingAgent behavior shared by every agent — keyed off skill_load_dir/guest_env/id."""

    def bridge_skills_home_script(self) -> str:
        """Symlink skill_load_dir → the fixed skills home so staged skills land where the agent loads them."""
        d = self.skill_load_dir
        parent = d.rsplit("/", 1)[0]
        return (
            f"mkdir -p {FIXED_SKILLS_HOME} && "
            f"mkdir -p {parent} && rm -rf {d} && "
            f"ln -s {FIXED_SKILLS_HOME} {d}"
        )

    def cell_env(self, *, arm: str, model: str, eval_set: str = "") -> dict:
        """EVALSPEC_* are informational for setup.sh — they do NOT route the task model
        (that goes through arm.model). EVALSPEC_SET names the explicitly-selected set
        (--evalspec-set / make evals SET=) so setup.sh can branch on it; empty when the
        run falls back to the pyproject default-set."""
        return {
            **self.guest_env(),
            "EVALSPEC_ARM": arm,
            "EVALSPEC_MODEL": model,
            "EVALSPEC_HARNESS": self.id,
            "EVALSPEC_SET": eval_set,
        }


@dataclass(frozen=True)
class AgentCapabilities:
    """What the harness can honestly do with this agent. Typed, not a dict:
    every field here has a consumer in the harness, and an unknown field is a
    type error rather than silently ignored documentation."""
    efforts: tuple[str, ...]  # values the agent's CLI accepts for the effort flag
    multi_turn: bool          # honors resume_session_id (session chaining across turns)
    token_split: bool         # reports input/output token split (enables cost estimates)


@runtime_checkable
class CodingAgent(Protocol):
    id: str            # snapshot-cache key + report label
    guest_home: str    # the agent's HOME inside the guest (where skills are staged, runs cwd)
    skill_load_dir: str  # absolute guest path the agent loads skills from
    capabilities: AgentCapabilities

    def version(self) -> str: ...
    def bridge_skills_home_script(self) -> str: ...   # symlink skill_load_dir → FIXED_SKILLS_HOME
    def cell_env(self, *, arm: str, model: str, eval_set: str = "") -> dict: ...  # guest_env + EVALSPEC_* (HARNESS=self.id, SET=eval_set)
    def artifact_dirs(self) -> list[str]: ...   # guest dirs where the agent writes skill
    #   artifacts (e.g. ~/.claude/skills). These live in the VM, NOT the workdir mount, so
    #   the session snapshots them and merges the agent-authored files (diffed against the
    #   staged baseline) into the judge's facts. Each agent scaffolds skills differently, so
    #   each owns its answer; return [] for an agent that writes only to the workdir.
    def secrets(self) -> list: ...
    def guest_env(self) -> dict: ...
    def build_command(
        self, prompt: str, *, plugin_dir: str | None, model: str, effort: str,
        resume_session_id: str | None, detect_skill: str | None,
        harness_args: list[str] | None = None,
    ) -> list[str]: ...
    async def provision(self, sb) -> None: ...
    async def stage_project_assets(self, sb, project_mount: str) -> None: ...
    async def invoke(
        self, sb, prompt: str, *, eval_id: str, config: str, workdir: str,
        plugin_dir: str | None, model: str, effort: str,
        resume_session_id: str | None, detect_skill: str | None,
        harness_args: list[str] | None = None,
        extra_env: dict | None = None,
    ) -> RunResult: ...
    def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str: ...
    def detect_dispatch(self, line: str, skill_name: str | None) -> bool: ...
    def detect_fired(self, lines, skill_name: str) -> bool: ...
    def streamed_activity(self, lines) -> bool: ...
