"""Codex CLI implementation of the `CodingAgent` interface."""

from __future__ import annotations

import datetime
import json
import os
from textwrap import dedent
from typing import TYPE_CHECKING

from benchspec.agents.base import AgentCapabilities, BaseAgent, Credential
from benchspec.grading.trajectory import iter_events
from benchspec.orchestration.environments import ExecutionEnv, GuestSandbox, Host
from benchspec.orchestration.results import RunResult
from benchspec.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig

_PROVIDER_HOSTS = {
    "CODEX_API_KEY": ["api.openai.com"],
    "CODEX_ACCESS_TOKEN": ["chatgpt.com", "auth.openai.com"],
}
AUTH_ENV_VARS = tuple(_PROVIDER_HOSTS)
_RESERVED_HARNESS_ARGS = {
    "-m",
    "--model",
    "-C",
    "--cd",
    "--json",
    "-o",
    "--output-last-message",
    "--output-schema",
    "-i",
    "--image",
    "--enable",
    "--disable",
    "-c",
    "--config",
    "--strict-config",
    "-p",
    "--profile",
    "-s",
    "--sandbox",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--add-dir",
    "--oss",
    "--local-provider",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    "--ephemeral",
    "resume",
    "review",
}
_RESERVED_HARNESS_LONG_FLAGS = {arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("--")}
_RESERVED_HARNESS_SHORT_FLAGS = {
    arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("-") and not arg.startswith("--")
}


def _validate_harness_args(harness_args: list[str] | None) -> list[str]:
    """Validate harness argument strings from configuration."""
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
            raise ValueError(f"reserved harness arg for Codex: {arg}")
    return harness_args


def _auth_json_from_env() -> str | None:
    """Read Codex auth JSON from environment variables."""
    path = os.environ.get("CODEX_AUTH_JSON_PATH")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as auth_file:
            auth_json = auth_file.read()
    except OSError:
        return None
    try:
        data = json.loads(auth_json)
    except json.JSONDecodeError:
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        return None
    return path


class CodexAgent(BaseAgent):
    """Store codex agent data."""

    id = "codex"
    AUTH_JSON_GUEST_SOURCE = "/benchspec-codex-auth/auth.json"
    guest_home = "/root"
    skill_load_dir = "/root/.codex/skills"
    capabilities = AgentCapabilities(multi_turn=False, token_split=True)
    def provision_script(self: object) -> str:
        """Install the instance's pinned Codex CLI version (baked in so the cache key tracks it)."""
        return dedent(f"""\
            apt-get update && apt-get install -y curl ca-certificates nodejs npm &&
            export CODEX_NON_INTERACTIVE=1 &&
            npm i -g "@openai/codex@{self._version}" &&
            test -x /usr/local/bin/codex
        """)

    def __init__(
        self: object,
        auth_value: str = "",
        *,
        auth_env: str = "CODEX_API_KEY",
        version: str = "latest",
        auth_json_path: str = "",
        agent_bin: str = "/usr/local/bin/codex",  # default binding: the guest install path
    ) -> None:
        """Initialize the instance."""
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._auth_json_path = auth_json_path
        self._version = version
        self.agent_bin = agent_bin

    @classmethod
    def for_host(cls: object) -> CodexAgent:
        """An instance bound to the host environment (judge mode): PATH resolves `codex`."""
        return cls(agent_bin="codex")

    @classmethod
    def from_env(cls: object) -> CodexAgent:
        """Build an agent instance from host environment settings."""
        version = os.environ.get("BENCHSPEC_CODEX_VERSION", "latest")
        for env_name in AUTH_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                return cls(auth_value=value, auth_env=env_name, version=version)
        auth_json_path = _auth_json_from_env()
        if auth_json_path:
            return cls(auth_json_path=auth_json_path, version=version)
        return cls(version=version)

    @staticmethod
    def credential_error() -> str | None:
        """Return a credential preflight error message when credentials are missing."""
        if any(os.environ.get(env_name) for env_name in AUTH_ENV_VARS):
            return None
        if _auth_json_from_env():
            return None
        return (
            "no Codex credential - set CODEX_AUTH_JSON_PATH, CODEX_API_KEY, or CODEX_ACCESS_TOKEN"
        )

    def version(self: object) -> str:
        """Return the agent CLI version string."""
        return self._version

    def artifact_dirs(self: object) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        # Codex populates CODEX_HOME/skills with runtime-managed `.system` skills during
        # execution. Those are not agent-authored artifacts, and some are not UTF-8-safe
        # for benchspec's artifact reader, so Codex reports workdir outputs only.
        return []

    def auth_json_path(self: object) -> str:
        """Auth json path."""
        return self._auth_json_path

    def guest_env(self: object) -> dict:
        """Return environment variables passed to guest agent commands."""
        return {
            "HOME": self.guest_home,
            "CODEX_HOME": f"{self.guest_home}/.codex",
            "TZ": "UTC",
            "BENCHSPEC_CODEX_VERSION": self._version,
        }

    def secrets(self: object) -> list[Credential]:
        """Return the provider credentials to inject into the guest."""
        if self._auth_json_path:
            return []
        return [
            Credential(
                self._auth_env, self._auth_value, tuple(_PROVIDER_HOSTS[self._auth_env])
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
        workdir: str | None = None,
    ) -> list[str]:
        """Build the guest command used to invoke the agent."""
        # plugin_dir/resume_session_id/detect_skill are accepted for protocol parity.
        # Codex exec has no stable benchspec-owned equivalents for them yet.
        cd = workdir or self.guest_home
        return [
            self.agent_bin,
            "exec",
            "--json",
            "-m",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "-C",
            cd,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            *_validate_harness_args(harness_args),
            prompt,
        ]

    def detect_dispatch(self: object, line: str, skill_name: str | None) -> bool:
        """Return whether one stream line shows a skill dispatch."""
        text = line.strip()
        if not text:
            return False
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return False
        return _item_dispatches_any_skill(_event_item(event), skill_name)

    def detect_fired(self: object, lines: object, skill_name: str) -> bool:
        """Return whether stream lines show the expected skill firing."""
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if _item_dispatches_skill(_event_item(event), skill_name):
                return True
        return False

    async def _write_auth_json(self: object, sandbox: object) -> None:
        """Stage Codex auth JSON into the guest home when needed."""
        if not self._auth_json_path:
            return
        res = await sandbox.shell(
            "mkdir -p /root/.codex && "
            "umask 077 && "
            f"cp {self.AUTH_JSON_GUEST_SOURCE} /root/.codex/auth.json",
            env=self.guest_env(),
        )
        if res.exit_code != 0:
            raise RuntimeError(
                f"codex auth copy failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    def streamed_activity(self: object, lines: object) -> bool:
        """Return whether streamed output shows meaningful agent activity."""
        activity_events = {
            "turn.started",
            "item.started",
            "item.completed",
            "turn.completed",
        }
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in activity_events:
                return True
        return False

    async def provision(self: object, sandbox: object) -> None:
        """Install the agent CLI and credentials inside the guest."""
        res = await sandbox.shell(self.provision_script(), env=self.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"codex provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def stage_project_assets(self: object, sandbox: object, project_mount: str) -> None:
        """Copy project-local assets needed by the guest agent."""
        await self._write_auth_json(sandbox)
        dest = self.skill_load_dir
        await sandbox.shell(
            f"mkdir -p {dest} && "
            f"for src in {project_mount}/skills {project_mount}/.agents/skills "
            f"{project_mount}/.claude/skills; do "
            f"  if [ -d $src ]; then cp -r $src/. {dest}/ 2>/dev/null || true; fi; "
            f"done",
            env=self.guest_env(),
        )

    async def invoke(
        self: object,
        sandbox: object,
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
        """Run one prompt through the agent inside the guest."""
        cmd = self.build_command(
            prompt,
            plugin_dir=plugin_dir,
            model=model,
            effort=effort,
            resume_session_id=resume_session_id,
            detect_skill=detect_skill,
            harness_args=harness_args,
            workdir=workdir,
        )
        try:
            await self._write_auth_json(sandbox)
            res = await GuestSandbox(sandbox).exec(
                cmd,
                cwd=workdir,
                env={**self.guest_env(), **(extra_env or {})},
                timeout=timeout,
                stdin=b"",
            )
        except (TimeoutError, SandboxError, OSError, RuntimeError) as error:
            return RunResult(
                eval_id,
                config,
                f"<sandbox-error> {error}"[-2000:],
                0,
                0,
                is_error=True,
            )
        if res.exit_code != 0:
            return RunResult(eval_id, config, res.stderr[-2000:], 0, 0, is_error=True)
        return parse_codex_jsonl(res.stdout, eval_id, config, detect_skill)

    async def judge(
        self: object,
        prompt: str,
        config: JudgeConfig,
        *,
        env: ExecutionEnv | None = None,
    ) -> str:
        """Grade via `codex exec --json` (default env: fresh Host process).

        Reads the output through the same `parse_codex_jsonl` the sandbox path uses,
        wraps the final agent text in the {"result": ...} envelope judge.py parses,
        and raises RuntimeError on infra failure — a missing binary, a nonzero exit,
        or a harness error event surfaced by the parser as `is_error`. `config.effort`
        is pinned via `-c model_reasoning_effort=...` so the verdict never depends on
        whatever `~/.codex/config.toml` the host happens to carry.
        """
        command = [
            self.agent_bin, "exec", "--json", "-m", config.model,
            "-c", f"model_reasoning_effort={config.effort}",
            *config.harness_args, prompt,
        ]
        proc = await (env or Host()).exec(command, env=config.env, timeout=config.timeout)

        # Parse before the exit check: codex reports a rejected request as a `turn.failed`
        # event on stdout and exits 1 with only progress chatter on stderr.
        result = parse_codex_jsonl(proc.stdout, "judge", "judge", None)
        if result.is_error:
            raise RuntimeError(f"codex judge reported an error: {result.result_text[:1000]}")

        proc.require_success()

        return json.dumps({"result": result.result_text})


def _error_message(event: dict) -> str:
    """Return the human-readable message from a codex error event, or "" when absent.

    `error` events carry `message` at the top level; `turn.failed` nests it as
    `error.message`.
    """
    for candidate in (event.get("message"), event.get("error")):
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, dict) and isinstance(candidate.get("message"), str):
            return candidate["message"]
    return ""


def _event_item(event: dict) -> dict:
    """Return the Codex event item payload when present."""
    item = event.get("item") if isinstance(event, dict) else None
    return item if isinstance(item, dict) else {}


def _skill_name_matches(actual: str, expected: str) -> bool:
    """Return whether a skill value names the expected skill."""
    return actual == expected or actual.endswith(f":{expected}")


def _skill_dispatch_name(item: dict) -> str | None:
    """Extract the dispatched skill name from a Codex event item."""
    item_type = item.get("type")
    if item_type == "skill_invocation":
        name = item.get("name") or item.get("skill")
        return name if isinstance(name, str) and name else None
    if item_type == "tool_call":
        name = item.get("name")
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        if name == "Skill":
            skill = args.get("skill") or args.get("name")
            return skill if isinstance(skill, str) and skill else None
        if isinstance(name, str) and name:
            return name
    return None


def _item_dispatches_skill(item: dict, skill_name: str) -> bool:
    """Return whether a Codex item dispatches the expected skill."""
    name = _skill_dispatch_name(item)
    return isinstance(name, str) and _skill_name_matches(name, skill_name)


def _item_dispatches_any_skill(item: dict, skill_name: str | None) -> bool:
    """Return whether a Codex item dispatches any skill."""
    name = _skill_dispatch_name(item)
    if name is None:
        return False
    if item.get("type") == "tool_call" and item.get("name") != "Skill":
        return bool(skill_name) and _skill_name_matches(name, skill_name)
    return True


def _timestamp_ms(value: object) -> int | None:
    """Convert a timestamp value to milliseconds when possible."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _debug_tail(events: list[dict]) -> str:
    """Format the trailing Codex events for diagnostics."""
    return json.dumps(events[-3:]) if events else ""


def _usage_int(usage: dict, *names: str) -> int:
    """Return an integer usage field from a Codex result object."""
    total = 0
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            total += value
    return total


def _codex_trajectory(events: list[dict]) -> list[dict]:
    """Convert Codex event items into benchspec trajectory facts."""
    traj: list[dict] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = _event_item(event)
        item_type = item.get("type")
        item_id = item.get("id") or ""
        if item_type == "skill_invocation":
            skill = _skill_dispatch_name(item)
            if skill:
                traj.append(
                    {
                        "kind": "tool_call",
                        "id": item_id,
                        "name": "Skill",
                        "arguments": {"skill": skill},
                    }
                )
        elif item_type == "tool_call":
            skill = _skill_dispatch_name(item)
            if item.get("name") == "Skill" and skill:
                traj.append(
                    {
                        "kind": "tool_call",
                        "id": item_id,
                        "name": "Skill",
                        "arguments": {"skill": skill},
                    }
                )
            else:
                args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                traj.append(
                    {
                        "kind": "tool_call",
                        "id": item_id,
                        "name": str(item.get("name") or ""),
                        "arguments": args,
                    }
                )
        elif item_type == "command_execution":
            command = item.get("command")
            args = {"command": command} if isinstance(command, str) else {}
            traj.append(
                {
                    "kind": "tool_call",
                    "id": item_id,
                    "name": "command_execution",
                    "arguments": args,
                }
            )
    return traj


def parse_codex_jsonl(
    stdout: str,
    eval_id: str,
    config: str,
    detect_skill: str | None,
) -> RunResult:
    """Parse codex jsonl."""
    events = list(iter_events(stdout))
    session_id = ""
    text_parts: list[str] = []
    input_tokens = 0
    cache_read_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    is_error = False
    error_text = ""
    fired = False
    first_ts: int | None = None
    last_ts: int | None = None

    for event in events:
        ts = _timestamp_ms(event.get("timestamp"))
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

        etype = event.get("type")
        if etype == "thread.started":
            tid = event.get("thread_id") or event.get("threadId") or event.get("id")
            if isinstance(tid, str):
                session_id = tid
        elif etype == "item.completed":
            item = _event_item(event)
            if item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
            if detect_skill and _item_dispatches_skill(item, detect_skill):
                fired = True
        elif etype == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            input_tokens += _usage_int(usage, "input_tokens")
            cache_read_tokens += _usage_int(usage, "cached_input_tokens", "cache_read_input_tokens")
            output_tokens += _usage_int(usage, "output_tokens")
            reasoning_tokens += _usage_int(usage, "reasoning_tokens", "reasoning_output_tokens")
        elif etype in {"turn.failed", "error"}:
            is_error = True
            error_text = _error_message(event) or error_text

    total_tokens = input_tokens + cache_read_tokens + output_tokens + reasoning_tokens
    duration_ms = (last_ts - first_ts) if first_ts is not None and last_ts is not None else 0
    result_text = "\n\n".join(text_parts) or error_text or _debug_tail(events)
    return RunResult(
        eval_id=eval_id,
        config=config,
        result_text=result_text,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        is_error=is_error,
        session_id=session_id,
        fired=fired,
        raw=stdout,
        trajectory=_codex_trajectory(events),
        cache_read_tokens=cache_read_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
