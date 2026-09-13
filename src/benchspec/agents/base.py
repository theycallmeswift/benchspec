"""The agent interface.

A `CodingAgent` hides everything agent-specific behind one boundary: where its home
lives in the guest, how to provision the CLI into a sandbox (the cached step), which
credentials it needs, how to stage local skills, how to build its headless command, and
how to parse its output. `sandbox.py` drives a live sandbox through this interface and
never names a concrete agent; adding a second agent is additive, not a refactor.

One adapter per harness, transport-blind: the adapter builds commands and parses
output, and a `benchspec.orchestration.environments.ExecutionEnv` decides where the
process runs —
`GuestSandbox` for task arms (`invoke`), `Host` for grading (`judge`). Sandbox-vs-host
is a parameter of the call, not a code path baked into each harness.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from benchspec.orchestration.results import RunResult

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig
    from benchspec.orchestration.environments import ExecutionEnv
    from benchspec.sandbox.backend import LiveSandbox, SandboxBackend

# The transport a harness reaches its model through. `default` is the vendor's own API
# or CLI login; `openrouter` routes every request through OpenRouter on one key. An enum,
# not a plugin seam: a third value is a deliberate follow-up, not a registry entry.
DEFAULT_PROVIDER = "default"
OPENROUTER_PROVIDER = "openrouter"
HARNESS_PROVIDERS = frozenset({DEFAULT_PROVIDER, OPENROUTER_PROVIDER})

# The agent-neutral home every per-cell `setup.sh` copies skills into. Each agent
# symlinks its own load dir here once at provision, so the install path is identical
# across agents and the per-agent load dir is the only agent-specific fact.
FIXED_SKILLS_HOME = "/home/benchspec/skills"

# Matches the first dotted-numeric token in `--version` output, e.g. the "1.2.3" in
# both "claude-code 1.2.3" and "codex-cli 0.144.1". Shared by every guest-version parse
# so adapters never hand-roll their own extraction.
_VERSION_TOKEN_RE = re.compile(r"\d+(?:\.\d+)+")

# The guest `--version` probe runs before the task on every sample. A wedged guest
# command must not block the run forever, so the await is bounded and a timeout becomes
# an explained-unavailable result like any other probe failure.
GUEST_VERSION_PROBE_TIMEOUT_SECONDS = 30.0

# The wall-clock cap on one graded agent turn, seconds. The built-in default every arm
# inherits when neither the set, the arm, nor `--benchspec-timeout` says otherwise; the
# adapters' `invoke` defaults to it so a direct call and a configured arm agree.
DEFAULT_AGENT_TIMEOUT = 600


def unqualified_openrouter_model_error(model: str) -> str | None:
    """Why `model` cannot be sent through OpenRouter, or None when it can.

    OpenRouter slugs carry a vendor prefix (`anthropic/...`, `openai/...`, `google/...`).
    A bare alias such as `sonnet` resolves client-side to a vendor ID that OpenRouter does
    not document mapping, so it is refused at config-read time instead of guessed at.

    Args:
        model: The configured model string.

    Returns:
        A message naming the model, or None when it carries a vendor prefix.
    """
    if "/" in model:
        return None
    return (
        f"provider `{OPENROUTER_PROVIDER}` needs a vendor-qualified model slug "
        f"(e.g. 'anthropic/claude-sonnet-4.6'), got `{model}`"
    )


def _parse_version_token(output: str) -> str | None:
    """Extract a dotted version token (e.g. "1.2.3") from raw `--version` output."""
    match = _VERSION_TOKEN_RE.search(output)
    return match.group(0) if match else None


async def probe_guest_version(
    backend: SandboxBackend, sandbox: LiveSandbox, agent: CodingAgent
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


@dataclass(frozen=True)
class Credential:
    """A provider credential an agent needs in the guest, declared backend-neutrally.

    Every agent's `secrets()` returns these instead of a concrete runtime's own secret
    type, so the sandbox seam (not the agent) decides how a credential reaches the guest.

    Attributes:
        env_var: The environment variable name the guest process reads.
        value: The credential's secret value.
        allow_hosts: The guest hostnames this credential may be sent to.
    """

    env_var: str
    value: str
    allow_hosts: tuple[str, ...]


class BaseAgent(ABC):
    """Shared behavior for the built-in adapters, plus the class-level factory contract.

    The registry in `benchspec.agents` builds and preflights agents through the class
    itself (`from_env`, `for_host`, `credential_error`), so those are abstract here: a
    registered adapter that forgets one fails at import, not mid-run.
    """

    id: str  # snapshot-cache key + report label
    guest_home: str  # the agent's HOME inside the guest (where skills are staged, runs cwd)
    skill_load_dir: str  # absolute guest path the agent loads skills from
    capabilities: AgentCapabilities
    # The binary THIS instance runs. An instance is bound to one execution
    # environment: the default binding is the guest install path (task arms);
    # for_host() rebinds to the name PATH resolves on the host (judge mode).
    agent_bin: str
    # The transport this instance reaches its model through (`HARNESS_PROVIDERS`).
    provider: str

    @classmethod
    @abstractmethod
    def from_env(cls, provider: str = DEFAULT_PROVIDER) -> CodingAgent:
        """Build an instance from host environment settings (credential, pinned version)."""

    @classmethod
    @abstractmethod
    def for_host(cls, provider: str = DEFAULT_PROVIDER) -> CodingAgent:
        """An instance bound to the host environment: agent_bin resolves from PATH."""

    @staticmethod
    @abstractmethod
    def credential_error(
        provider: str = DEFAULT_PROVIDER, environ: Mapping[str, str] | None = None
    ) -> str | None:
        """Return a credential preflight error message when credentials are missing.

        Args:
            provider: The transport the credential must serve.
            environ: The environment to read credentials from; the process environment
                when None.
        """

    @abstractmethod
    def guest_env(self) -> dict[str, str]:
        """Return environment variables passed to guest agent commands."""

    def provision_script(self) -> str:
        """The fully-resolved commands this instance runs to install the CLI in the guest.

        Concrete task adapters override it, baking in any instance state (e.g. a pinned
        version). `provision()` runs exactly this, and `install_fingerprint()` hashes it — so
        every install-affecting input is captured in the cache key structurally, with nothing
        to fold by hand. Empty on the base (host/judge agents install nothing in a guest).
        """
        return ""

    def host_credential_error(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Judge-mode credential check for an instance bound to the host (`for_host()`).

        The judge runs on the host with whatever its CLI can authenticate with, so an env
        credential is sufficient but not necessary. Adapters whose CLI can report its own
        login state override this to ask it; the base accepts the env credential alone.

        Args:
            environ: The environment the judge will run with (the host environment plus
                the judge's own `env`); the process environment when None.
        """
        return self.credential_error(self.provider, environ)

    def host_probe(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Run `agent_bin *args` on the host; None when it cannot run at all (absent, hung).

        Args:
            *args: The CLI arguments after the binary.
            env: The process environment for the probe; inherited when None.
        """
        try:
            return subprocess.run(
                [self.agent_bin, *args], capture_output=True, text=True, timeout=10,
                env=None if env is None else dict(env),
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None

    def binary_version(self) -> str | None:
        """Best-effort `agent_bin --version` probe — never raises, never fails the run."""
        proc = self.host_probe("--version")
        if proc is None or proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def install_fingerprint(self) -> str:
        """Cache-key fingerprint of the CLI install: a hash of the resolved provision script.

        `provision_script()` is the single source of truth for what installs the CLI, so
        changing the installer (a new revision, package list, pinned version, or bootstrap
        commands) rebuilds the snapshot. The agent version also appears directly in the
        snapshot name, so a version bump rebuilds even for an adapter whose installer does not
        embed the version.
        """
        return hashlib.sha256(self.provision_script().encode()).hexdigest()[:12]

    def bridge_skills_home_script(self) -> str:
        """Bridge skills home script."""
        skill_dir = self.skill_load_dir
        parent = skill_dir.rsplit("/", 1)[0]
        return (
            f"mkdir -p {FIXED_SKILLS_HOME} && "
            f"mkdir -p {parent} && rm -rf {skill_dir} && "
            f"ln -s {FIXED_SKILLS_HOME} {skill_dir}"
        )

    def cell_env(
        self, *, arm: str, model: str, eval_set: str = "", baseline: str = ""
    ) -> dict[str, str]:
        """BENCHSPEC_* are informational for setup.sh — they do NOT route the task model.

        (that goes through arm.model). BENCHSPEC_SET names the explicitly-selected set
        (--benchspec-set / make evals SET=) so setup.sh can branch on it; empty when the
        run falls back to the pyproject default-set. BENCHSPEC_BASELINE names the set's
        baseline arm (empty when none) so a script or clause can tell the control arm
        apart without hardcoding its name.
        """
        return {
            **self.guest_env(),
            "BENCHSPEC_ARM": arm,
            "BENCHSPEC_MODEL": model,
            "BENCHSPEC_HARNESS": self.id,
            "BENCHSPEC_SET": eval_set,
            "BENCHSPEC_BASELINE": baseline,
        }


@runtime_checkable
class CodingAgent(Protocol):
    """Define the agent interface."""

    id: str  # snapshot-cache key + report label
    guest_home: str  # the agent's HOME inside the guest (where skills are staged, runs cwd)
    skill_load_dir: str  # absolute guest path the agent loads skills from
    agent_bin: str  # the binary this instance runs (guest install path; for_host() rebinds)
    provider: str  # the transport this instance reaches its model through
    capabilities: AgentCapabilities

    def version(self) -> str:
        """Return the agent CLI version string."""
        ...

    def provision_script(self) -> str:
        """Return the fully-resolved commands that install the CLI in the guest."""
        ...

    def install_fingerprint(self) -> str:
        """Return the CLI install fingerprint (a hash of `provision_script()`)."""
        ...

    def bridge_skills_home_script(self) -> str:
        """Bridge skills home script."""
        ...

    def cell_env(
        self, *, arm: str, model: str, eval_set: str = "", baseline: str = ""
    ) -> dict[str, str]:
        """Return per-cell environment variables for an arm run."""
        ...

    def artifact_dirs(self) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts.

        These live in the VM, NOT the workdir mount, so the session snapshots them and
        merges the agent-authored files (diffed against the staged baseline) into the
        judge's facts. Each agent scaffolds skills differently, so each owns its answer;
        return [] for an agent that writes only to the workdir.
        """
        ...

    def secrets(self) -> list[Credential]:
        """Return the provider credentials to inject into the guest."""
        ...

    def guest_env(self) -> dict[str, str]:
        """Return environment variables passed to guest agent commands."""
        ...

    def build_command(
        self,
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

    async def provision(self, sandbox: LiveSandbox) -> None:
        """Install the agent CLI and credentials inside the guest."""
        ...

    async def stage_project_assets(self, sandbox: LiveSandbox, project_mount: str) -> None:
        """Copy project-local assets needed by the guest agent."""
        ...

    async def invoke(
        self,
        sandbox: LiveSandbox,
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
        extra_env: dict[str, str] | None = None,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
    ) -> RunResult:
        """Run one prompt through the agent inside the guest."""
        ...

    async def judge(
        self,
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
    def for_host(cls, provider: str = DEFAULT_PROVIDER) -> CodingAgent:
        """An instance bound to the host environment: agent_bin resolves from PATH."""
        ...

    def binary_version(self) -> str | None:
        """Return this instance's CLI version, or None on any failure (best effort)."""
        ...

    def host_credential_error(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Return a remediation message when this host-bound instance cannot authenticate."""
        ...

    def detect_dispatch(self, line: str, skill_name: str | None) -> bool:
        """Return whether one stream line shows a skill dispatch."""
        ...

    def detect_fired(self, lines: Iterable[str], skill_name: str) -> bool:
        """Return whether stream lines show the expected skill firing."""
        ...

    def streamed_activity(self, lines: Iterable[str]) -> bool:
        """Return whether streamed output shows meaningful agent activity."""
        ...
