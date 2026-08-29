"""The coding-agent interface.

A `CodingAgent` hides everything agent-specific behind one boundary: where its home
lives in the guest, how to provision the CLI into a microVM (the cached step), which
secrets it needs, how to stage local skills, how to build its headless command, and how
to parse its output. `sandbox.py` drives a live sandbox through this interface and never
names a concrete agent; adding a second agent is additive, not a refactor.

One adapter per harness, transport-blind: the adapter builds commands and parses
output, and a `harnessbench.orchestration.environments.ExecutionEnv` decides where the
process runs —
`GuestSandbox` for task arms (`invoke`), `Host` for grading (`judge`). Sandbox-vs-host
is a parameter of the call, not a code path baked into each harness.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harnessbench.orchestration.results import RunResult

if TYPE_CHECKING:
    from harnessbench.grading.judges.config import JudgeConfig
    from harnessbench.orchestration.environments import ExecutionEnv
    from harnessbench.sandbox.backend import SandboxBackend

# The agent-neutral home every per-cell `setup.sh` copies skills into. Each agent
# symlinks its own load dir here once at provision, so the install path is identical
# across agents and the per-agent load dir is the only agent-specific fact.
FIXED_SKILLS_HOME = "/home/harnessbench/skills"

# Matches the first dotted-numeric token in `--version` output, e.g. the "1.2.3" in
# both "claude-code 1.2.3" and "codex-cli 0.144.1". Shared by every guest-version parse
# so adapters never hand-roll their own extraction.
_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)+")

# The guest `--version` probe runs before the task on every sample. A wedged guest
# command must not block the run forever, so the await is bounded and a timeout becomes
# an explained-unavailable result like any other probe failure.
GUEST_VERSION_PROBE_TIMEOUT_SECONDS = 30.0


def _parse_version_token(output: str) -> str | None:
    """Extract a dotted version token (e.g. "1.2.3") from raw `--version` output."""
    match = _VERSION_TOKEN_RE.search(output)
    return match.group(0) if match else None


async def probe_guest_version(
    backend: SandboxBackend, sandbox: object, agent: object
) -> tuple[str | None, str | None]:
    """Measure the task-harness binary's version inside the live guest sandbox.

    Runs `<agent.agent_bin> --version` through the backend's guest command seam
    (`backend.guest_shell`) against the already-booted `sandbox` instance. This is the
    guest task-harness probe: it reads the binary actually installed in the selected
    snapshot, never the host binding (`for_host()`) or the pinned install selector
    (`agent.version()`, e.g. `"latest"`).

    Never raises: any failure to reach the guest, or to parse a version out of what it
    returns, is reported as an explained `(None, error)` pair rather than an
    unexplained null (ground rule: explained unavailable).

    Args:
        backend: The resolved `SandboxBackend` driving this arm's sandbox.
        sandbox: The live sandbox instance for the running arm session.
        agent: The `CodingAgent` whose `agent_bin` is probed.

    Returns:
        `(version, None)` on success, or `(None, error)` describing why the probe
        could not produce a version.
    """
    script = f"{agent.agent_bin} --version"
    try:
        # Bounded so a wedged guest command surfaces as unavailable instead of hanging
        # every sample before its task runs.
        output = await asyncio.wait_for(
            backend.guest_shell(sandbox, agent, script),
            timeout=GUEST_VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return None, f"guest version probe timed out after {GUEST_VERSION_PROBE_TIMEOUT_SECONDS}s"
    except Exception as error:
        # Broad on purpose: this probe's contract is "never raise" (see docstring), and
        # guest_shell's own concrete failure modes vary by backend.
        return None, f"guest_shell raised {type(error).__name__}: {error}"
    if output is None:
        return None, f"guest_shell returned no output for `{script}`"
    version = _parse_version_token(output)
    if version is None:
        return None, f"could not parse a version from guest output: {output.strip()!r}"
    return version, None


class BaseAgent:
    """Store base agent data."""

    # The binary THIS instance runs. An instance is bound to one execution
    # environment: the default binding is the guest install path (task arms);
    # for_host() rebinds to the name PATH resolves on the host (judge mode).
    agent_bin: str

    def provision_script(self: object) -> str:
        """The fully-resolved commands this instance runs to install the CLI in the guest.

        Concrete task adapters override it, baking in any instance state (e.g. a pinned
        version). `provision()` runs exactly this, and `install_fingerprint()` hashes it — so
        every install-affecting input is captured in the cache key structurally, with nothing
        to fold by hand. Empty on the base (host/judge agents install nothing in a guest).
        """
        return ""

    def binary_version(self: object) -> str | None:
        """Best-effort `agent_bin --version` probe — never raises, never fails the run."""
        try:
            proc = subprocess.run(
                [self.agent_bin, "--version"], capture_output=True, text=True, timeout=10
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def install_fingerprint(self: object) -> str:
        """Cache-key fingerprint of the CLI install: a hash of the resolved provision script.

        `provision_script()` is the single source of truth for what installs the CLI, so
        changing the installer (a new revision, package list, pinned version, or bootstrap
        commands) rebuilds the snapshot. The agent version also appears directly in the
        snapshot name, so a version bump rebuilds even for an adapter whose installer does not
        embed the version.
        """
        return hashlib.sha256(self.provision_script().encode()).hexdigest()[:12]

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
        """HARNESSBENCH_* are informational for setup.sh — they do NOT route the task model.

        (that goes through arm.model). HARNESSBENCH_SET names the explicitly-selected set
        (--harnessbench-set / make evals SET=) so setup.sh can branch on it; empty when the
        run falls back to the pyproject default-set.
        """
        return {
            **self.guest_env(),
            "HARNESSBENCH_ARM": arm,
            "HARNESSBENCH_MODEL": model,
            "HARNESSBENCH_HARNESS": self.id,
            "HARNESSBENCH_SET": eval_set,
        }


@dataclass(frozen=True)
class AgentCapabilities:
    """What the harness can honestly do with this agent.

    Typed, not a dict: every field here has a consumer in the harness, and an
    unknown field is a type error rather than silently ignored documentation.
    Effort is deliberately not a capability: every driver forwards it verbatim and
    the agent's CLI is the validator, the same way model names are handled.
    """

    multi_turn: bool  # honors resume_session_id (session chaining across turns)
    token_split: bool  # reports input/output token split (enables cost estimates)


@runtime_checkable
class CodingAgent(Protocol):
    """Define the coding agent interface."""

    id: str  # snapshot-cache key + report label
    guest_home: str  # the agent's HOME inside the guest (where skills are staged, runs cwd)
    skill_load_dir: str  # absolute guest path the agent loads skills from
    agent_bin: str  # the binary this instance runs (guest install path; for_host() rebinds)
    capabilities: AgentCapabilities

    def version(self: object) -> str:
        """Return the agent CLI version string."""
        ...

    def provision_script(self: object) -> str:
        """Return the fully-resolved commands that install the CLI in the guest."""
        ...

    def install_fingerprint(self: object) -> str:
        """Return the CLI install fingerprint (a hash of `provision_script()`)."""
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
    def for_host(cls: object) -> CodingAgent:
        """An instance bound to the host environment: agent_bin resolves from PATH."""
        ...

    def binary_version(self: object) -> str | None:
        """Return this instance's CLI version, or None on any failure (best effort)."""
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
