"""Codex CLI implementation of the `CodingAgent` interface.

Under `provider = "openrouter"` the adapter appends benchspec-owned `-c` overrides that
declare an OpenRouter model provider on the Responses wire API, in both the guest
command and the host judge, so no `config.toml` is written anywhere and the user's
`~/.codex/config.toml` stays untouched; `OPENROUTER_API_KEY` rides as the credential,
scoped to `openrouter.ai`. Nothing about the snapshot changes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from textwrap import dedent
from typing import TYPE_CHECKING

from benchspec.agents.base import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_PROVIDER,
    FIXED_SKILLS_HOME,
    OPENROUTER_PROVIDER,
    AgentCapabilities,
    BaseAgent,
    Credential,
)
from benchspec.grading.trajectory import dict_or_empty, iter_events
from benchspec.orchestration.environments import ExecutionEnv, GuestSandbox, Host
from benchspec.orchestration.results import RunResult, mark_errored_by_nonzero_exit
from benchspec.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig
    from benchspec.sandbox.backend import LiveSandbox

# In preference order.
_PROVIDER_HOSTS = {
    "CODEX_API_KEY": ["api.openai.com"],
    "CODEX_ACCESS_TOKEN": ["chatgpt.com", "auth.openai.com"],
    "OPENAI_API_KEY": ["api.openai.com"],
}
AUTH_ENV_VARS = tuple(_PROVIDER_HOSTS)
# Host env var → the name Codex reads; unlisted entries keep theirs. Codex CLI ignores
# `OPENAI_API_KEY`, so accept it but inject it as `CODEX_API_KEY`, in guest and judge.
_CODEX_ENV_NAMES = {
    "OPENAI_API_KEY": "CODEX_API_KEY",
}
_CREDENTIAL_REMEDY = (
    "set CODEX_AUTH_JSON_PATH, CODEX_API_KEY, OPENAI_API_KEY, or CODEX_ACCESS_TOKEN"
)
# Under `openrouter`, the one credential Codex reads (named by `env_key` below).
OPENROUTER_AUTH_ENV = "OPENROUTER_API_KEY"
_OPENROUTER_HOSTS = ["openrouter.ai"]
_OPENROUTER_CREDENTIAL_REMEDY = "no OpenRouter credential — set OPENROUTER_API_KEY"
# Codex reaches a custom provider only through `model_provider` plus a
# `[model_providers.<id>]` table; these `-c` overrides declare it per invocation so no
# config file is written. Codex ignores `OPENAI_BASE_URL`, and it speaks only the
# Responses wire API, which OpenRouter serves at `/api/v1/responses`. Values are quoted
# because `-c` parses them as TOML.
_OPENROUTER_CONFIG_OVERRIDES = (
    'model_provider="openrouter"',
    'model_providers.openrouter.name="OpenRouter"',
    'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
    f'model_providers.openrouter.env_key="{OPENROUTER_AUTH_ENV}"',
    'model_providers.openrouter.wire_api="responses"',
)
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
_CODEX_SKILL_LOAD_DIR = "/root/.codex/skills"
# Codex has no native skill-dispatch event: it loads a skill by shelling out to read its
# `SKILL.md`. The match keys on the skill's own path under either skills home plus the
# literal `SKILL.md` leaf, so any read verb (`cat`, `sed`, `head`, a `<` redirect) counts
# while a sibling reference file or a `SKILL.md` outside the skills home does not.
_SKILL_MD_READ_RE = re.compile(
    rf"(?<![\w.\-/])(?:{re.escape(FIXED_SKILLS_HOME)}|{re.escape(_CODEX_SKILL_LOAD_DIR)})"
    r"/((?!\.\.?/)[^/\s'\"]+)/SKILL\.md(?![\w.\-/])"
)


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


def _auth_json_from_env(environ: Mapping[str, str] | None = None) -> str | None:
    """Read Codex auth JSON from environment variables."""
    environ = os.environ if environ is None else environ
    path = environ.get("CODEX_AUTH_JSON_PATH")
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


def _provider_overrides(provider: str) -> list[str]:
    """The benchspec-owned `-c` tokens that route Codex through `provider`; none under `default`."""
    if provider != OPENROUTER_PROVIDER:
        return []
    return [token for override in _OPENROUTER_CONFIG_OVERRIDES for token in ("-c", override)]


def _renamed_host_credentials() -> dict[str, str]:
    """Host credentials renamed to what Codex reads; an exported Codex name is kept."""
    return {
        codex_name: os.environ[host_name]
        for host_name, codex_name in _CODEX_ENV_NAMES.items()
        if os.environ.get(host_name) and not os.environ.get(codex_name)
    }


class CodexAgent(BaseAgent):
    """Store codex agent data."""

    id = "codex"
    AUTH_JSON_GUEST_SOURCE = "/benchspec-codex-auth/auth.json"
    guest_home = "/root"
    skill_load_dir = _CODEX_SKILL_LOAD_DIR
    capabilities = AgentCapabilities(multi_turn=False, token_split=True)

    def provision_script(self) -> str:
        """Install the instance's pinned Codex CLI version (baked in so the cache key tracks it)."""
        return dedent(f"""\
            apt-get update && apt-get install -y curl ca-certificates nodejs npm &&
            export CODEX_NON_INTERACTIVE=1 &&
            npm i -g "@openai/codex@{self._version}" &&
            test -x /usr/local/bin/codex
        """)

    def __init__(
        self,
        auth_value: str = "",
        *,
        auth_env: str = "CODEX_API_KEY",
        version: str = "latest",
        auth_json_path: str = "",
        agent_bin: str = "/usr/local/bin/codex",  # default binding: the guest install path
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        """Initialize the instance."""
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._auth_json_path = auth_json_path
        self._version = version
        self.agent_bin = agent_bin
        self.provider = provider

    @classmethod
    def for_host(cls, provider: str = DEFAULT_PROVIDER) -> CodexAgent:
        """An instance bound to the host environment (judge mode): PATH resolves `codex`."""
        return cls(agent_bin="codex", provider=provider)

    @classmethod
    def from_env(cls, provider: str = DEFAULT_PROVIDER) -> CodexAgent:
        """Build an agent instance from host environment settings.

        Under `openrouter` the credential is the host's `OPENROUTER_API_KEY`; under
        `default` it is the first Codex credential set, else a mounted auth.json. Either
        way a missing credential is left for preflight to report.
        """
        version = os.environ.get("BENCHSPEC_CODEX_VERSION", "latest")
        if provider == OPENROUTER_PROVIDER:
            return cls(
                auth_value=os.environ.get(OPENROUTER_AUTH_ENV, ""),
                auth_env=OPENROUTER_AUTH_ENV,
                version=version,
                provider=provider,
            )
        for env_name in AUTH_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                return cls(
                    auth_value=value, auth_env=env_name, version=version, provider=provider
                )
        auth_json_path = _auth_json_from_env()
        if auth_json_path:
            return cls(auth_json_path=auth_json_path, version=version, provider=provider)
        return cls(version=version, provider=provider)

    @staticmethod
    def credential_error(
        provider: str = DEFAULT_PROVIDER, environ: Mapping[str, str] | None = None
    ) -> str | None:
        """Return a credential preflight error message when credentials are missing.

        Under `openrouter` only `OPENROUTER_API_KEY` counts — an OpenAI credential or a
        ChatGPT login cannot authenticate to the gateway, so neither is accepted or named.
        """
        environ = os.environ if environ is None else environ
        if provider == OPENROUTER_PROVIDER:
            return None if environ.get(OPENROUTER_AUTH_ENV) else _OPENROUTER_CREDENTIAL_REMEDY
        if any(environ.get(env_name) for env_name in AUTH_ENV_VARS):
            return None
        if _auth_json_from_env(environ):
            return None
        return f"no Codex credential - {_CREDENTIAL_REMEDY}"

    def host_credential_error(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Accept an env credential, else ask `codex login status` whether the host is logged in.

        Under `openrouter` the probe is skipped: `codex login status` reports "Not logged
        in" by design with a custom provider, so the env check alone decides.
        """
        if self.credential_error(self.provider, environ) is None:
            return None
        if self.provider == OPENROUTER_PROVIDER:
            return _OPENROUTER_CREDENTIAL_REMEDY
        proc = self.host_probe("login", "status", env=environ)
        if proc is not None and proc.returncode == 0:
            return None
        return f"Codex is not logged in on the host - run `codex login`, or {_CREDENTIAL_REMEDY}"

    def version(self) -> str:
        """Return the agent CLI version string."""
        return self._version

    def artifact_dirs(self) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        # Codex populates CODEX_HOME/skills with runtime-managed `.system` skills during
        # execution. Those are not agent-authored artifacts, and some are not UTF-8-safe
        # for benchspec's artifact reader, so Codex reports workdir outputs only.
        return []

    def auth_json_path(self) -> str:
        """Auth json path."""
        return self._auth_json_path

    def guest_env(self) -> dict[str, str]:
        """Return environment variables passed to guest agent commands."""
        return {
            "HOME": self.guest_home,
            "CODEX_HOME": f"{self.guest_home}/.codex",
            "TZ": "UTC",
            "BENCHSPEC_CODEX_VERSION": self._version,
        }

    def secrets(self) -> list[Credential]:
        """Return the provider credentials to inject into the guest.

        Under `openrouter` the key may only reach `openrouter.ai`, so under microsandbox
        the guest never sees it and cannot send it anywhere else.
        """
        if self.provider == OPENROUTER_PROVIDER:
            return [Credential(OPENROUTER_AUTH_ENV, self._auth_value, tuple(_OPENROUTER_HOSTS))]
        if self._auth_json_path:
            return []
        guest_env_name = _CODEX_ENV_NAMES.get(self._auth_env, self._auth_env)
        return [
            Credential(guest_env_name, self._auth_value, tuple(_PROVIDER_HOSTS[self._auth_env]))
        ]

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
        workdir: str | None = None,
    ) -> list[str]:
        """Build the guest command used to invoke the agent.

        The provider overrides are benchspec-owned `-c` tokens appended by the adapter;
        `-c` stays reserved for user `harness_args`.
        """
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
            *_provider_overrides(self.provider),
            "-C",
            cd,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            *_validate_harness_args(harness_args),
            prompt,
        ]

    async def _write_auth_json(self, sandbox: LiveSandbox) -> None:
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

    def streamed_activity(self, lines: Iterable[str]) -> bool:
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

    async def provision(self, sandbox: LiveSandbox) -> None:
        """Install the agent CLI and credentials inside the guest."""
        res = await sandbox.shell(self.provision_script(), env=self.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"codex provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

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
        # `codex exec --json` events carry no timing, so the run's time is the process's.
        result = replace(
            parse_codex_jsonl(res.stdout, eval_id, config, detect_skill),
            duration_ms=res.duration_ms,
        )
        if res.exit_code != 0:
            return mark_errored_by_nonzero_exit(result, res.stderr)
        return result

    async def judge(
        self,
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
        whatever `~/.codex/config.toml` the host happens to carry, and under `openrouter`
        the same `-c` overrides the guest gets declare the provider per invocation, so
        the host's config.toml is neither read for it nor written. A host
        `OPENAI_API_KEY` reaches Codex as `CODEX_API_KEY`; `config.env` wins.
        """
        command = [
            self.agent_bin, "exec", "--json", "-m", config.model,
            "-c", f"model_reasoning_effort={config.effort}",
            *_provider_overrides(self.provider),
            *config.harness_args, prompt,
        ]
        judge_env = {**_renamed_host_credentials(), **config.env}
        proc = await (env or Host()).exec(command, env=judge_env, timeout=config.timeout)

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
    """Extract the dispatched skill name from a Codex event item.

    A `command_execution` resolves to the first `SKILL.md` read it names: the contract
    is one name per item, and every consumer treats an item as one dispatch.
    """
    item_type = item.get("type")
    if item_type == "skill_invocation":
        name = item.get("name") or item.get("skill")
        return name if isinstance(name, str) and name else None
    if item_type == "tool_call":
        name = item.get("name")
        args = dict_or_empty(item.get("arguments"))
        if name == "Skill":
            skill = args.get("skill") or args.get("name")
            return skill if isinstance(skill, str) and skill else None
        if isinstance(name, str) and name:
            return name
    if item_type == "command_execution":
        match = _SKILL_MD_READ_RE.search(str(item.get("command") or ""))
        return match.group(1) if match else None
    return None


def _item_dispatches_skill(item: dict, skill_name: str) -> bool:
    """Return whether a Codex item dispatches the expected skill."""
    name = _skill_dispatch_name(item)
    return isinstance(name, str) and _skill_name_matches(name, skill_name)


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
                args = dict_or_empty(item.get("arguments"))
                traj.append(
                    {
                        "kind": "tool_call",
                        "id": item_id,
                        "name": str(item.get("name") or ""),
                        "arguments": args,
                    }
                )
        elif item_type == "command_execution":
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
            else:
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
    """Parse a `codex exec --json` stream into a RunResult.

    `is_error` means the turn did not complete: a `turn.failed` event, or an `error`
    event with no `turn.completed` after it. Codex also emits `error` events for
    transports it recovers from (a websocket that fails and falls back to HTTP logs
    "Reconnecting..." and then completes the turn normally), so an `error` followed by
    `turn.completed` is not a failure; the recovered messages stay in `raw`.

    The stream carries no timing, so `duration_ms` is 0; `invoke` stamps the measured
    process wall time over it.
    """
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

    for event in events:
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
            usage = dict_or_empty(event.get("usage"))
            input_tokens += _usage_int(usage, "input_tokens")
            cache_read_tokens += _usage_int(usage, "cached_input_tokens", "cache_read_input_tokens")
            output_tokens += _usage_int(usage, "output_tokens")
            reasoning_tokens += _usage_int(usage, "reasoning_tokens", "reasoning_output_tokens")
            # The turn finished, so every `error` before it was recovered from.
            is_error = False
            error_text = ""
        elif etype in {"turn.failed", "error"}:
            is_error = True
            error_text = _error_message(event) or error_text

    total_tokens = input_tokens + cache_read_tokens + output_tokens + reasoning_tokens
    result_text = "\n\n".join(text_parts) or error_text or _debug_tail(events)
    return RunResult(
        eval_id=eval_id,
        config=config,
        result_text=result_text,
        duration_ms=0,
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
