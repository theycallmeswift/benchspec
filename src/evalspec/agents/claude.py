"""Claude Code implementation of the `CodingAgent` interface.

Provisions the Claude Code CLI into a microVM (the cached snapshot step), injects the
Anthropic credential as a host-substituted secret, builds the headless `claude -p`
command, and parses its output through the shared helpers in `runner.py`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace

from evalspec.agents.base import AgentCapabilities, BaseAgent
from evalspec.agents.judge_cli import run_host_judge
from evalspec.runner import RunResult, parse_stream_run
from evalspec.trigger import detect_skill_fired, dispatches_skill, streamed_activity

# Credentials Claude Code reads, in preference order. The runner injects whichever is set as
# a microsandbox secret (substituted only for the Anthropic API host).
AUTH_ENV_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
_RESERVED_HARNESS_ARGS = {
    "-p",
    "--print",
    "--prompt",
    "--model",
    "--effort",
    "--output-format",
    "--input-format",
    "--permission-mode",
    "-r",
    "--resume",
    "-c",
    "--continue",
    "--session-id",
    "--fork-session",
    "--no-session-persistence",
}
_RESERVED_HARNESS_LONG_FLAGS = {arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("--")}
_RESERVED_HARNESS_SHORT_FLAGS = {
    arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("-") and not arg.startswith("--")
}


def _validate_harness_args(harness_args: list[str] | None) -> list[str]:
    """Handle _validate_harness_args."""
    if harness_args is None:
        return []
    for arg in harness_args:
        if (
            arg in _RESERVED_HARNESS_ARGS
            or any(arg.startswith(f"{flag}=") for flag in _RESERVED_HARNESS_LONG_FLAGS)
            or any(
                arg.startswith(flag) and len(arg) > len(flag)
                for flag in _RESERVED_HARNESS_SHORT_FLAGS
            )
        ):
            raise ValueError(f"reserved harness arg for Claude Code: {arg}")
    return harness_args


def _validate_plugin_dir_sources(plugin_dir: object, harness_args: list[str] | None) -> None:
    """Handle _validate_plugin_dir_sources."""
    if plugin_dir is None or harness_args is None:
        return
    for arg in harness_args:
        if arg == "--plugin-dir" or arg.startswith("--plugin-dir="):
            raise ValueError("plugin_dir cannot be combined with harness_args --plugin-dir")


class ClaudeCodeAgent(BaseAgent):
    """Represent ClaudeCodeAgent."""

    id = "claude-code"
    CLAUDE_BIN = "/root/.local/bin/claude"
    guest_home = "/root"
    skill_load_dir = "/root/.claude/skills"
    capabilities = AgentCapabilities(
        efforts=("low", "medium", "high", "xhigh", "max"),
        multi_turn=True,
        token_split=True,
    )
    PROVISION_SCRIPT = (
        "apt-get update && apt-get install -y curl ca-certificates && "
        "curl -fsSL https://claude.ai/install.sh | bash"
    )

    def __init__(
        self: object,
        auth_value: str = "",
        *,
        auth_env: str = "ANTHROPIC_API_KEY",
        version: str = "latest",
    ) -> None:
        """Initialize the instance."""
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._version = version

    @classmethod
    def from_env(cls: object) -> ClaudeCodeAgent:
        """Document the behavior."""
        version = os.environ.get("EVALSPEC_CLAUDE_VERSION", "latest")
        for env_name in AUTH_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                return cls(auth_value=value, auth_env=env_name, version=version)
        return cls(version=version)  # no credential set; preflight gates this

    @staticmethod
    def credential_error() -> str | None:
        """Document the behavior."""
        if any(os.environ.get(v) for v in AUTH_ENV_VARS):
            return None
        return (
            "no Claude credential — set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) "
            "or ANTHROPIC_API_KEY"
        )

    def version(self: object) -> str:
        """Handle version."""
        return self._version

    def artifact_dirs(self: object) -> list[str]:
        """Handle artifact_dirs."""
        # Claude Code auto-loads (and scaffolds) skills under $HOME/.claude/skills — outside
        # the workdir mount, so a skill the agent writes here is invisible to the judge
        # unless the session snapshots it. The staged skills sit here too; the session's
        # baseline diff drops them, leaving only what the agent authored.
        return [f"{self.guest_home}/.claude/skills"]

    def guest_env(self: object) -> dict:
        """Handle guest_env."""
        # IS_SANDBOX=1 lets claude run bypassPermissions as root (the guest is root); the
        # microVM is the real containment boundary. The credential rides as a substituted
        # secret (see secrets()), never entering the guest as a plain value. TZ=UTC pins the
        # guest clock to the zone the host computes {TODAY} in, so a dated path the agent
        # writes matches the date the assertions were substituted with.
        return {"HOME": self.guest_home, "IS_SANDBOX": "1", "TZ": "UTC"}

    def secrets(self: object) -> list:
        """Handle secrets."""
        from microsandbox import Secret

        return [
            Secret.env(
                self._auth_env,
                value=self._auth_value,
                allow_hosts=["api.anthropic.com"],
            )
        ]

    def build_command(
        self: object,
        prompt: object,
        *,
        plugin_dir: object,
        model: object,
        effort: object,
        resume_session_id: object,
        detect_skill: object,
        harness_args: list[str] | None = None,
    ) -> list[str]:
        """Handle build_command."""
        # Always stream-json so both arms capture a trajectory (the baseline too); detect_skill
        # gates fired-detection downstream, not the format.
        # bypassPermissions (not acceptEdits): the microVM is the containment boundary,
        # so the agent runs with full autonomy — no host-side --allowedTools workaround needed.
        _validate_plugin_dir_sources(plugin_dir, harness_args)
        cmd = [
            self.CLAUDE_BIN,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            model,
            "--effort",
            effort,
        ]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        if plugin_dir is not None:
            cmd += ["--plugin-dir", plugin_dir]
        cmd += _validate_harness_args(harness_args)
        return cmd

    async def provision(self: object, sb: object) -> None:
        """Handle provision."""
        res = await sb.shell(self.PROVISION_SCRIPT, env={"HOME": self.guest_home})
        if res.exit_code != 0:
            raise RuntimeError(
                f"claude-code provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def stage_project_assets(self: object, sb: object, project_mount: str) -> None:
        """Handle stage_project_assets."""
        # Claude auto-loads skills from the guest HOME's .claude/skills; copy (not mount)
        # the project's local skills there for a clean per-run tree. The agent-neutral
        # name covers other agents that stage more than just .claude/skills.
        await sb.shell(
            f"mkdir -p {self.guest_home}/.claude && "
            f"if [ -d {project_mount}/.claude/skills ]; then "
            f"cp -r {project_mount}/.claude/skills {self.guest_home}/.claude/skills; fi",
            env={"HOME": self.guest_home},
        )

    def judge(self: object, prompt: str, *, model: str, timeout: int = 300) -> str:
        """Grade via the host's `claude -p` (NOT in the sandbox); see run_host_judge.

        for the host-vs-VM asymmetry and the RuntimeError-on-infra-failure contract.
        """
        return run_host_judge(prompt, model=model, timeout=timeout)

    def detect_dispatch(self: object, line: str, skill_name: str | None) -> bool:
        """True if the stream-json line shows a skill dispatch in Claude Code's event.

        shape: a `Skill` tool_use, or a tool_use whose name is `skill_name` (the
        namespaced-tool fallback). Delegates to the shared `dispatches_skill` helper
        so the event-shape match lives in one place and `trigger.py` / `sandbox.py`
        stay agent-agnostic.
        """
        return dispatches_skill(line, skill_name)

    def detect_fired(self: object, lines: object, skill_name: str) -> bool:
        """Tally whether OUR skill fired across the routing stream.

        Delegates to the.         shared Claude-shape helper so the event-shape match
        lives in one place.
        """
        return detect_skill_fired(lines, skill_name)

    def streamed_activity(self: object, lines: object) -> bool:
        """True if the model began a turn (an `assistant` event), distinguishing a.

        clean non-fire from a retryable launch stall. Delegates to the shared helper.
        """
        return streamed_activity(lines)

    async def invoke(
        self: object,
        sb: object,
        prompt: object,
        *,
        eval_id: object,
        config: object,
        workdir: object,
        plugin_dir: object,
        model: object,
        effort: object,
        resume_session_id: object,
        detect_skill: object,
        harness_args: list[str] | None = None,
        extra_env: dict | None = None,
        timeout: int = 600,
    ) -> RunResult:
        """Handle invoke."""
        from microsandbox.errors import MicrosandboxError

        cmd = self.build_command(
            prompt,
            plugin_dir=plugin_dir,
            model=model,
            effort=effort,
            resume_session_id=resume_session_id,
            detect_skill=detect_skill,
            harness_args=harness_args,
        )
        try:
            res = await sb.exec(
                # Per-arm extra_env (e.g. a leaky OpenRouter base URL) merges over
                # guest_env(), arm env winning.
                cmd[0],
                cmd[1:],
                cwd=workdir,
                env={**self.guest_env(), **(extra_env or {})},
                timeout=timeout,
                stdin=b"",
            )
        except (MicrosandboxError, asyncio.TimeoutError, OSError) as e:
            # A sandbox-boundary failure (VM/exec/timeout) is an infra error for this arm,
            # not a graded miss — record it so the benchmark excludes it. A programming
            # error is not caught here: let it surface.
            return RunResult(eval_id, config, f"<sandbox-error> {e}"[-2000:], 0, 0, is_error=True)
        result = parse_stream_run(res.stdout_text, eval_id, config, detect_skill)
        # Non-zero exit with no result event = a crash; its diagnostic is on stderr, not in
        # the empty stream. Surface stderr, keeping the raw/trajectory already captured.
        if res.exit_code != 0 and result.is_error and res.stderr_text.strip():
            return replace(result, result_text=res.stderr_text[-2000:])
        return result
