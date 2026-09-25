"""Claude Code implementation of the `CodingAgent` interface.

Provisions the Claude Code CLI into a sandbox (the cached snapshot step), declares the
provider credential for the backend to inject, builds the headless `claude -p`
command, and parses its output through the shared helpers in `results.py`.

Under `provider = "openrouter"` the same CLI is pointed at OpenRouter's Anthropic
Messages skin at exec time: `ANTHROPIC_BASE_URL` names the gateway, `ANTHROPIC_API_KEY`
is explicitly emptied so no direct-Anthropic credential can be picked up, and the
OpenRouter key rides as `ANTHROPIC_AUTH_TOKEN` scoped to `openrouter.ai`. Nothing about
the snapshot changes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from benchspec.agents.base import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_PROVIDER,
    OPENROUTER_PROVIDER,
    AgentCapabilities,
    BaseAgent,
    Credential,
)
from benchspec.grading.trajectory import extract_trajectory
from benchspec.grading.trigger import StopRule
from benchspec.orchestration.environments import ExecutionEnv, GuestSandbox, Host
from benchspec.orchestration.results import (
    RunResult,
    mark_errored_by_nonzero_exit,
    parse_stream_run,
)
from benchspec.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig
    from benchspec.sandbox.backend import LiveSandbox

# Credentials Claude Code reads, in preference order. Whichever is set becomes a scoped
# credential the backend injects (substituted only for the Anthropic API host).
AUTH_ENV_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
# Under `openrouter`, the host key Claude Code is handed as a bearer token.
OPENROUTER_AUTH_ENV = "OPENROUTER_API_KEY"
_OPENROUTER_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
_OPENROUTER_HOST = "openrouter.ai"
# No `/v1`: Claude Code appends `/v1/messages` itself, per OpenRouter's cookbook.
_OPENROUTER_BASE_URL = "https://openrouter.ai/api"
_OPENROUTER_CREDENTIAL_REMEDY = "no OpenRouter credential — set OPENROUTER_API_KEY"
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
    """Validate harness argument strings from configuration."""
    if harness_args is None:
        return []
    for harness_arg in harness_args:
        if (
            harness_arg in _RESERVED_HARNESS_ARGS
            or any(
                harness_arg.startswith(f"{flag}=") for flag in _RESERVED_HARNESS_LONG_FLAGS
            )
            or any(
                harness_arg.startswith(flag) and len(harness_arg) > len(flag)
                for flag in _RESERVED_HARNESS_SHORT_FLAGS
            )
        ):
            raise ValueError(f"reserved harness arg for Claude Code: {harness_arg}")
    return harness_args


def _validate_plugin_dir_sources(plugin_dir: str | None, harness_args: list[str] | None) -> None:
    """Validate plugin dir sources."""
    if plugin_dir is None or harness_args is None:
        return
    for harness_arg in harness_args:
        if harness_arg == "--plugin-dir" or harness_arg.startswith("--plugin-dir="):
            raise ValueError("plugin_dir cannot be combined with harness_args --plugin-dir")


def _raise_for_is_error_envelope(stdout: str) -> None:
    """Raise RuntimeError when a 0-exit `claude -p` envelope carries is_error=true.

    This is how `claude -p` reports auth, rate-limit, quota, and overload failures.
    Unparseable stdout is deliberately NOT an error here (a transient streaming
    glitch isn't a CLI failure); the caller's own JSON parsing surfaces that case.
    """
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        return
    if isinstance(outer, dict) and outer.get("is_error"):
        msg = str(outer.get("result") or "").strip() or "(no error message)"
        raise RuntimeError(f"host claude CLI returned is_error=true: {msg[:1000]}")


def _openrouter_routing_env() -> dict[str, str]:
    """The env that points Claude Code at OpenRouter's Anthropic Messages skin.

    `ANTHROPIC_API_KEY` is set to the empty string on purpose: OpenRouter's cookbook
    requires the explicit override so Claude Code cannot fall back to a direct-Anthropic
    credential that happens to be in the environment.
    """
    return {"ANTHROPIC_BASE_URL": _OPENROUTER_BASE_URL, "ANTHROPIC_API_KEY": ""}


def _reports_logged_in(auth_status_json: str) -> bool:
    """Whether a `claude auth status` JSON envelope reports an active login."""
    try:
        status = json.loads(auth_status_json)
    except json.JSONDecodeError:
        return False
    return isinstance(status, dict) and status.get("loggedIn") is True


class ClaudeCodeAgent(BaseAgent):
    """Store claude code agent data."""

    id = "claude-code"
    guest_home = "/root"
    skill_load_dir = "/root/.claude/skills"
    capabilities = AgentCapabilities(multi_turn=True, token_split=True)

    def provision_script(self) -> str:
        """Install script — the Claude installer always fetches latest; no version is baked in."""
        return (
            "apt-get update && apt-get install -y curl ca-certificates && "
            "curl -fsSL https://claude.ai/install.sh | bash"
        )

    def __init__(
        self,
        auth_value: str = "",
        *,
        auth_env: str = "ANTHROPIC_API_KEY",
        version: str = "latest",
        agent_bin: str = "/root/.local/bin/claude",  # default binding: the guest install path
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        """Initialize the instance."""
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._version = version
        self.agent_bin = agent_bin
        self.provider = provider

    @classmethod
    def for_host(cls, provider: str = DEFAULT_PROVIDER) -> ClaudeCodeAgent:
        """An instance bound to the host environment (judge mode): PATH resolves `claude`."""
        return cls(agent_bin="claude", provider=provider)

    @classmethod
    def from_env(cls, provider: str = DEFAULT_PROVIDER) -> ClaudeCodeAgent:
        """Build an agent instance from host environment settings.

        Under `openrouter` the credential is the host's `OPENROUTER_API_KEY`, carried under
        the bearer-token name Claude Code reads; under `default` it is the first Anthropic
        credential set. Either way a missing credential is left for preflight to report.
        """
        version = os.environ.get("BENCHSPEC_CLAUDE_VERSION", "latest")
        if provider == OPENROUTER_PROVIDER:
            return cls(
                auth_value=os.environ.get(OPENROUTER_AUTH_ENV, ""),
                auth_env=_OPENROUTER_TOKEN_ENV,
                version=version,
                provider=provider,
            )
        for env_name in AUTH_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                return cls(
                    auth_value=value, auth_env=env_name, version=version, provider=provider
                )
        return cls(version=version, provider=provider)  # no credential set; preflight gates this

    @staticmethod
    def credential_error(
        provider: str = DEFAULT_PROVIDER, environ: Mapping[str, str] | None = None
    ) -> str | None:
        """Return a credential preflight error message when credentials are missing.

        Under `openrouter` only `OPENROUTER_API_KEY` counts — an Anthropic credential
        cannot authenticate to the gateway, so it is neither accepted nor mentioned.
        """
        environ = os.environ if environ is None else environ
        if provider == OPENROUTER_PROVIDER:
            return None if environ.get(OPENROUTER_AUTH_ENV) else _OPENROUTER_CREDENTIAL_REMEDY
        if any(environ.get(env_var) for env_var in AUTH_ENV_VARS):
            return None
        return (
            "no Claude credential — set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) "
            "or ANTHROPIC_API_KEY"
        )

    def host_credential_error(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Accept an env credential, else ask `claude auth status` whether the host is logged in.

        Under `openrouter` the host login is irrelevant (the gateway takes only the bearer
        token), so the probe is skipped and the env check alone decides.
        """
        if self.credential_error(self.provider, environ) is None:
            return None
        if self.provider == OPENROUTER_PROVIDER:
            return _OPENROUTER_CREDENTIAL_REMEDY
        proc = self.host_probe("auth", "status", env=environ)
        if proc is not None and proc.returncode == 0 and _reports_logged_in(proc.stdout):
            return None
        return (
            "Claude Code is not logged in on the host — run `claude login`, or set "
            "CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY"
        )

    def version(self) -> str:
        """Return the agent CLI version string."""
        return self._version

    def artifact_dirs(self) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        # Claude Code auto-loads (and scaffolds) skills under $HOME/.claude/skills — outside
        # the workdir mount, so a skill the agent writes here is invisible to the judge
        # unless the session snapshots it. The staged skills sit here too; the session's
        # baseline diff drops them, leaving only what the agent authored.
        return [f"{self.guest_home}/.claude/skills"]

    def guest_env(self) -> dict[str, str]:
        """Return environment variables passed to guest agent commands."""
        # IS_SANDBOX=1 lets claude run bypassPermissions as root (the guest is root); the
        # sandbox is the real containment boundary. The credential rides as the backend's
        # injection of secrets() — microsandbox scopes it to the provider host at the
        # network boundary, Docker passes it as a container environment variable, so how
        # exposed it is inside the guest is the backend's answer, not this adapter's.
        # TZ=UTC pins the guest clock to the zone the host computes {TODAY}
        # in, so a dated path the agent writes matches the date the assertions were
        # substituted with.
        env = {"HOME": self.guest_home, "IS_SANDBOX": "1", "TZ": "UTC"}
        if self.provider == OPENROUTER_PROVIDER:
            env.update(_openrouter_routing_env())
        return env

    def secrets(self) -> list[Credential]:
        """Return the provider credentials to inject into the guest.

        The allow-host follows the provider: under `openrouter` the bearer token may only
        reach `openrouter.ai`, so under microsandbox the guest never sees the key and
        cannot send it anywhere else.
        """
        allow_host = (
            _OPENROUTER_HOST if self.provider == OPENROUTER_PROVIDER else "api.anthropic.com"
        )
        return [Credential(self._auth_env, self._auth_value, (allow_host,))]

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
        # Always stream-json so both arms capture a trajectory (the baseline too); detect_skill
        # gates fired-detection downstream, not the format.
        # bypassPermissions (not acceptEdits): the sandbox is the containment boundary,
        # so the agent runs with full autonomy — no host-side --allowedTools workaround needed.
        _validate_plugin_dir_sources(plugin_dir, harness_args)
        cmd = [
            self.agent_bin,
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

    async def provision(self, sandbox: LiveSandbox) -> None:
        """Install the agent CLI and credentials inside the guest."""
        res = await sandbox.shell(self.provision_script(), env={"HOME": self.guest_home})
        if res.exit_code != 0:
            raise RuntimeError(
                f"claude-code provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def judge(
        self,
        prompt: str,
        config: JudgeConfig,
        *,
        env: ExecutionEnv | None = None,
    ) -> str:
        """Grade via `claude -p --output-format json` (default env: fresh Host process).

        The output is already the {"result": ..., "is_error": ...} envelope judge.py
        parses, so it is returned as-is after infra checks. RuntimeError on a missing
        binary, a nonzero exit, or an is_error envelope (auth/rate-limit/quota). Under
        `openrouter` the routing env and the bearer token are merged under `config.env`,
        so a user's judge `env` still wins.
        """
        command = [self.agent_bin, "-p", prompt, "--output-format", "json",
                   "--model", config.model, "--effort", config.effort,
                   *config.harness_args]
        judge_env = {**self._judge_provider_env(config.env), **config.env}
        proc = await (env or Host()).exec(command, env=judge_env, timeout=config.timeout)

        proc.require_success()
        _raise_for_is_error_envelope(proc.stdout)
        return proc.stdout

    def _judge_provider_env(self, config_env: Mapping[str, str]) -> dict[str, str]:
        """The provider routing the host judge needs beneath `config_env`; empty under `default`.

        The OpenRouter key is read from the host environment with the judge's own `env`
        laid over it — the same view the credential preflight evaluated.
        """
        if self.provider != OPENROUTER_PROVIDER:
            return {}
        token = {**os.environ, **config_env}.get(OPENROUTER_AUTH_ENV, "")
        return {**_openrouter_routing_env(), _OPENROUTER_TOKEN_ENV: token}

    def stream_tool_calls(self, line: str) -> list[dict]:
        """Return the tool calls one stream-json line carries, as the trajectory records them."""
        return [event for event in extract_trajectory(line) if event["kind"] == "tool_call"]

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
        stop: StopRule | None = None,
    ) -> RunResult:
        """Run one prompt through the agent inside the guest."""
        cmd = self.build_command(
            prompt,
            plugin_dir=plugin_dir,
            model=model,
            effort=effort,
            resume_session_id=resume_session_id,
            detect_skill=detect_skill,
            harness_args=harness_args,
        )
        # Per-arm extra_env (e.g. a leaky OpenRouter base URL) merges over guest_env(),
        # arm env winning.
        env = {**self.guest_env(), **(extra_env or {})}
        if stop is not None:
            return await self.invoke_watched(
                sandbox,
                cmd,
                stop=stop,
                parse=lambda stdout: parse_stream_run(stdout, eval_id, config, detect_skill),
                cwd=workdir,
                env=env,
                timeout=timeout,
                eval_id=eval_id,
                config=config,
            )

        try:
            res = await GuestSandbox(sandbox).exec(
                cmd,
                cwd=workdir,
                env=env,
                timeout=timeout,
                stdin=b"",
            )
        except (TimeoutError, SandboxError, OSError) as error:
            # A sandbox-boundary failure (VM/exec/timeout) is an infra error for this arm,
            # not a graded miss — record it so the benchmark excludes it. A programming
            # error is not caught here: let it surface.
            message = f"<sandbox-error> {error}"[-2000:]
            return RunResult(eval_id, config, message, 0, 0, is_error=True)
        result = parse_stream_run(res.stdout, eval_id, config, detect_skill)
        if res.exit_code != 0:
            return mark_errored_by_nonzero_exit(result, res.stderr)
        return result
