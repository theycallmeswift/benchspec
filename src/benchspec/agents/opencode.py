"""OpenCode (sst/opencode) implementation of the CodingAgent interface.

Provisions OpenCode into a sandbox via `npm i -g opencode-ai@<pinned>`, declares the
provider credential for the backend to inject, builds the `opencode run --format json`
command, and parses its JSONL output. Pinning, model surface, and effort taxonomy are
documented in `docs/harnesses.md`.

Event shape: JSONL, one event per line, nested
under `part`. Turn events are `step_start` / `text` / `tool_use` / `step_finish`;
there is NO terminal `result` event. A tool call the guest could not approve is a
`tool_use` whose `part.state` is `{"status":"error","error":"The user rejected permission
to use this specific tool call."}`; a run that ends on one with no final text is
classified as errored, not as a clean failure. A skill dispatch is
`{"type":"tool_use","part":{"tool":"skill","state":{"input":{"name":"<skill>"}}}}`;
the final message concatenates every non-empty `part.text` (blank-line joined); totals
are `step_finish.part.tokens.total`.

OpenCode reaches OpenRouter natively (`OPENROUTER_API_KEY` plus an `openrouter/<slug>`
model), so `provider = "openrouter"` only makes that path deterministic: the credential
is forced to `OPENROUTER_API_KEY` instead of "first of four keys set", and the model must
carry the `openrouter/` prefix. `default` keeps the fallback chain untouched.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from benchspec.agents.base import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_PROVIDER,
    OPENROUTER_PROVIDER,
    AgentCapabilities,
    BaseAgent,
    Credential,
)
from benchspec.grading.trajectory import dict_or_empty, iter_events
from benchspec.grading.trigger import TriggerWatch
from benchspec.orchestration.environments import ExecutionEnv, GuestSandbox, Host
from benchspec.orchestration.results import RunResult, mark_errored_by_nonzero_exit
from benchspec.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from benchspec.grading.judges.config import JudgeConfig
    from benchspec.sandbox.backend import LiveSandbox

# Credentials OpenCode reads (host-side env var names), in preference order. Whichever
# is set is handed to the backend with its provider host attached; microsandbox honors
# that scope at the network boundary, Docker passes the value into the container.
AUTH_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
)
OPENROUTER_AUTH_ENV = "OPENROUTER_API_KEY"
OPENROUTER_MODEL_PREFIX = "openrouter/"
_OPENROUTER_CREDENTIAL_REMEDY = "no OpenRouter credential — set OPENROUTER_API_KEY"
_PROVIDER_HOSTS = {
    "OPENROUTER_API_KEY": "openrouter.ai",
    "ANTHROPIC_API_KEY": "api.anthropic.com",
    "GEMINI_API_KEY": "generativelanguage.googleapis.com",
    "GOOGLE_GENERATIVE_AI_API_KEY": "generativelanguage.googleapis.com",
}
# Host env var → guest env var. Unlisted entries: guest sees the same name as host.
# OpenCode is built on the Vercel AI SDK, whose Google provider reads
# `GOOGLE_GENERATIVE_AI_API_KEY` and ignores `GEMINI_API_KEY` — so we accept the
# friendlier `GEMINI_API_KEY` from the host but always inject under the SDK's name.
_GUEST_ENV_NAMES = {
    "GEMINI_API_KEY": "GOOGLE_GENERATIVE_AI_API_KEY",
}
_RESERVED_HARNESS_ARGS = {
    "-m",
    "--model",
    "--variant",
    "--format",
    "-c",
    "--continue",
    "-s",
    "--session",
    "--fork",
    "--command",
    "--prompt",
}
_RESERVED_HARNESS_LONG_FLAGS = {arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("--")}
_RESERVED_HARNESS_SHORT_FLAGS = {
    arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("-") and not arg.startswith("--")
}
# Judge-mode effort mapping: opencode expresses reasoning effort as a --variant.
_EFFORT_TO_VARIANT = {"low": "fast", "medium": "default", "high": "thorough"}
_PERMISSION_REJECTED_MARKER = "rejected permission"


def openrouter_model_error(model: str) -> str | None:
    """Why `model` cannot be routed through OpenRouter by OpenCode, or None when it can.

    OpenCode selects the provider from the model's own prefix, so under `openrouter` the
    slug must be `openrouter/<vendor>/<model>`; any other prefix would route directly to
    that vendor with the wrong key.
    """
    if model.startswith(OPENROUTER_MODEL_PREFIX):
        return None
    return (
        f"provider `{OPENROUTER_PROVIDER}` needs an `{OPENROUTER_MODEL_PREFIX}`-prefixed model "
        f"(e.g. 'openrouter/anthropic/claude-sonnet-4.6'), got {model!r}"
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
            raise ValueError(f"reserved harness arg for OpenCode: {arg}")
    return harness_args


# Minimal OpenCode plugin baked into the snapshot. It uses OpenCode's
# `experimental.chat.messages.transform` hook: injects a generic "you have these
# skills available via the `skill` tool" bootstrap into the first user message of
# each session. The list is regenerated per session from whatever's staged at
# ~/.config/opencode/skills/, so the same plugin works for any project's skills
# without rebuilding the snapshot. Neutral cuing — names + descriptions only;
# the model still decides whether a given task matches.
_BOOTSTRAP_PLUGIN_JS = r"""import path from 'path';
import fs from 'fs';
import os from 'os';

const SKILLS_DIR = path.join(os.homedir(), '.config/opencode/skills');

const extractFrontmatter = (content) => {
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return {};
  const fm = {};
  for (const line of match[1].split('\n')) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex > 0) {
      const key = line.slice(0, separatorIndex).trim();
      const value = line.slice(separatorIndex + 1).trim().replace(/^["']|["']$/g, '');
      fm[key] = value;
    }
  }
  return fm;
};

const listSkills = () => {
  if (!fs.existsSync(SKILLS_DIR)) return [];
  return fs.readdirSync(SKILLS_DIR).flatMap((dir) => {
    const skillPath = path.join(SKILLS_DIR, dir, 'SKILL.md');
    if (!fs.existsSync(skillPath)) return [];
    const fm = extractFrontmatter(fs.readFileSync(skillPath, 'utf8'));
    return [{ name: fm.name || dir, description: fm.description || '' }];
  });
};

let bootstrapCache = undefined; // undefined = not loaded; null = no skills found

const getBootstrap = () => {
  if (bootstrapCache !== undefined) return bootstrapCache;
  const skills = listSkills();
  if (!skills.length) {
    bootstrapCache = null;
    return null;
  }
  const list = skills.map((skill) => `- \`${skill.name}\` — ${skill.description}`).join('\n');
  bootstrapCache = `<BENCHSPEC_SKILLS_AVAILABLE>
You have access to the following user-installed skills via the \`skill\` tool:

${list}

Use the \`skill\` tool to load any of these when its description matches the task.
</BENCHSPEC_SKILLS_AVAILABLE>`;
  return bootstrapCache;
};

export const BenchspecBootstrap = async () => ({
  // OpenCode 1.15.x calls plugins.trigger('experimental.chat.system.transform',
  // {sessionID, model}, {system: r}) where r is the system-prompt string array.
  // Hooks mutate `r` in place (push, splice). Newer versions also expose
  // 'experimental.chat.messages.transform' — register both so this plugin
  // works across versions without coupling to one shape.
  'experimental.chat.system.transform': async (_input, output) => {
    const bootstrap = getBootstrap();
    if (!bootstrap || !Array.isArray(output.system)) return;
    if (output.system.some(
      (systemMessage) => typeof systemMessage === 'string' &&
        systemMessage.includes('BENCHSPEC_SKILLS_AVAILABLE')
    )) return;
    output.system.push(bootstrap);
  },
  'experimental.chat.messages.transform': async (_input, output) => {
    const bootstrap = getBootstrap();
    if (!bootstrap || !output.messages || !output.messages.length) return;
    const firstUser = output.messages.find((message) => (
      message.info && message.info.role === 'user'
    ));
    if (!firstUser || !firstUser.parts || !firstUser.parts.length) return;
    if (firstUser.parts.some(
      (part) => part.type === 'text' &&
        part.text &&
        part.text.includes('BENCHSPEC_SKILLS_AVAILABLE')
    )) return;
    const ref = firstUser.parts[0];
    firstUser.parts.unshift({ ...ref, type: 'text', text: bootstrap });
  },
});
"""

_COMPACT_SEPARATORS = (",", ":")
_BOOTSTRAP_PACKAGE_JSON = json.dumps(
    {"name": "benchspec-bootstrap", "version": "0.0.0", "type": "module", "main": "index.js"},
    separators=_COMPACT_SEPARATORS,
)
# Headless `opencode run` cannot answer permission prompts; the sandbox is the boundary.
_OPENCODE_CONFIG_JSON = json.dumps(
    {
        "$schema": "https://opencode.ai/config.json",
        "plugin": ["/root/.config/opencode/plugins/benchspec-bootstrap"],
        "permission": "allow",
    },
    separators=_COMPACT_SEPARATORS,
)


class OpenCodeAgent(BaseAgent):
    """Store open code agent data."""

    id = "opencode"
    guest_home = "/root"
    skill_load_dir = "/root/.config/opencode/skills"
    # multi_turn: invoke() accepts resume_session_id but does not
    # honor it — each call is a fresh session. token_split: OpenCode usage events
    # carry no cache split, so cost would be a guess.
    capabilities = AgentCapabilities(multi_turn=False, token_split=False)

    def provision_script(self) -> str:
        """Install the instance's pinned OpenCode version and bake the bootstrap plugin in."""
        return (
            dedent("""\
            apt-get update && apt-get install -y curl ca-certificates nodejs npm &&
            """)
            + f'npm i -g "opencode-ai@{self._version}" && '
            # Bake the benchspec bootstrap plugin into the snapshot, plus a global
            # opencode.json that registers it.
            "mkdir -p /root/.config/opencode/plugins/benchspec-bootstrap && "
            + "cat > /root/.config/opencode/plugins/benchspec-bootstrap/package.json"
            + " <<'EOF_PKG'\n"
            + _BOOTSTRAP_PACKAGE_JSON
            + "\nEOF_PKG\n"
            "cat > /root/.config/opencode/plugins/benchspec-bootstrap/index.js <<'EOF_JS'\n"
            + _BOOTSTRAP_PLUGIN_JS
            + "EOF_JS\n"
            "cat > /root/.config/opencode/opencode.json <<'EOF_CFG'\n"
            + _OPENCODE_CONFIG_JSON
            + "\nEOF_CFG\n"
            # Warm OpenCode's one-time SQLite migration at snapshot-build time.
            + "/usr/local/bin/opencode auth list > /dev/null 2>&1 && "
            # Fail provisioning loudly if the migration did not bake the DB into the snapshot.
            + "test -f /root/.local/share/opencode/opencode.db"
        )

    def __init__(
        self,
        auth_value: str = "",
        *,
        auth_env: str = "ANTHROPIC_API_KEY",
        version: str = "latest",
        # Default binding: the guest install path (npm i -g installs the symlink here).
        agent_bin: str = "/usr/local/bin/opencode",
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        """Initialize the instance."""
        self.agent_bin = agent_bin
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._version = version
        self.provider = provider

    @classmethod
    def for_host(cls, provider: str = DEFAULT_PROVIDER) -> OpenCodeAgent:
        """An instance bound to the host environment (judge mode): PATH resolves `opencode`."""
        return cls(agent_bin="opencode", provider=provider)

    @classmethod
    def from_env(cls, provider: str = DEFAULT_PROVIDER) -> OpenCodeAgent:
        """Build the agent from the host env: pinned version (env var > pyproject >.

        'latest') + the credential. Under `openrouter` that is `OPENROUTER_API_KEY` and
        nothing else; under `default` it is the first of `AUTH_ENV_VARS` set. Either way
        a missing credential is left for preflight to report.
        """
        version = (
            os.environ.get("BENCHSPEC_OPENCODE_VERSION")
            or cls._pinned_version_from_pyproject()
            or "latest"
        )
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
        return cls(version=version, provider=provider)  # no credential set; preflight gates this

    @staticmethod
    def _pinned_version_from_pyproject() -> str | None:
        """Read `[tool.benchspec] opencode_version` from pyproject.toml.

        Tries `$PROJECT_ROOT` first (same env var resolve_repo_root honors), then
        `Path.cwd()` so direct `cli_build` invocations from the repo root still find the
        pin. Returns None if the file is missing or the value isn't a string.
        """
        candidates: list[Path] = []
        env_root = os.environ.get("PROJECT_ROOT")
        if env_root:
            candidates.append(Path(env_root) / "pyproject.toml")
        candidates.append(Path.cwd() / "pyproject.toml")
        for pyproject in candidates:
            if not pyproject.is_file():
                continue
            with pyproject.open("rb") as pyproject_file:
                data = tomllib.load(pyproject_file)
            version = data.get("tool", {}).get("benchspec", {}).get("opencode_version")
            if isinstance(version, str):
                return version
        return None

    @staticmethod
    def credential_error(
        provider: str = DEFAULT_PROVIDER, environ: Mapping[str, str] | None = None
    ) -> str | None:
        """Return a credential preflight error message when credentials are missing.

        Under `openrouter` only `OPENROUTER_API_KEY` counts, even when another key in
        `AUTH_ENV_VARS` is set — that is the determinism the provider buys.
        """
        environ = os.environ if environ is None else environ
        if provider == OPENROUTER_PROVIDER:
            return None if environ.get(OPENROUTER_AUTH_ENV) else _OPENROUTER_CREDENTIAL_REMEDY
        if any(environ.get(env_name) for env_name in AUTH_ENV_VARS):
            return None
        return "no OpenCode provider credential — set one of " + ", ".join(AUTH_ENV_VARS)

    def version(self) -> str:
        """Return the agent CLI version string."""
        return self._version

    def artifact_dirs(self) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        return [f"{self.guest_home}/.config/opencode/skills"]

    def guest_env(self) -> dict[str, str]:
        """Return environment variables passed to guest agent commands."""
        return {
            "HOME": self.guest_home,
            "TZ": "UTC",
            "BENCHSPEC_OPENCODE_VERSION": self._version,
        }

    def secrets(self) -> list[Credential]:
        """Return the provider credentials to inject into the guest."""
        allow_host = _PROVIDER_HOSTS[self._auth_env]
        guest_env_name = _GUEST_ENV_NAMES.get(self._auth_env, self._auth_env)
        return [Credential(guest_env_name, self._auth_value, (allow_host,))]

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
        if "/" not in model:
            raise ValueError(
                f"OpenCode needs a provider-qualified model (e.g. 'google/gemini-3.5-flash'), "
                f"got {model!r}. The --benchspec-model default 'sonnet' is a Claude-only alias; "
                f"pass --benchspec-model explicitly under BENCHSPEC_AGENT=opencode."
            )
        if self.provider == OPENROUTER_PROVIDER:
            model_error = openrouter_model_error(model)
            if model_error:
                raise ValueError(f"OpenCode: {model_error}")
        # Map the agent-neutral effort tier to OpenCode's --variant.
        variant = {"low": "fast", "medium": "default", "high": "thorough"}.get(effort, "default")
        return [
            self.agent_bin,
            "run",
            "--format",
            "json",
            "--variant",
            variant,
            "-m",
            model,
            *_validate_harness_args(harness_args),
            prompt,
        ]

    async def provision(self, sandbox: LiveSandbox) -> None:
        """Install the agent CLI and credentials inside the guest."""
        res = await sandbox.shell(self.provision_script(), env=self.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"opencode provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
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
        watch: TriggerWatch | None = None,
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
        # Per-arm extra_env merges over guest_env(), arm env winning.
        env = {**self.guest_env(), **(extra_env or {})}
        if watch is not None:
            return await self.invoke_watched(
                sandbox,
                cmd,
                watch=watch,
                parse=lambda stdout: parse_opencode_jsonl(stdout, eval_id, config, detect_skill),
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
                # Force EOF on stdin so `opencode run` cannot block on an open pipe.
                stdin=b"",
            )
        except (TimeoutError, SandboxError, OSError) as error:
            return RunResult(
                eval_id,
                config,
                f"<sandbox-error> {error}"[-2000:],
                0,
                0,
                is_error=True,
            )
        result = parse_opencode_jsonl(res.stdout, eval_id, config, detect_skill)
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
        """Grade via `opencode run --format json` (default env: fresh Host process).

        Reads the output through the same `parse_opencode_jsonl` the sandbox path
        uses — that parser treats a run that spent zero tokens as an error (the
        harness never reached the model: auth, quota, or launch failure), so infra
        detection is the same structured signal as the arm, never a stderr scan.
        Wraps the final text in the {"result": ...} envelope judge.py parses. Under
        `openrouter` a model without the `openrouter/` prefix is refused before any
        process spawns, as a RuntimeError like every other judge infra failure.
        """
        if self.provider == OPENROUTER_PROVIDER:
            model_error = openrouter_model_error(config.model)
            if model_error:
                raise RuntimeError(f"opencode judge: {model_error}")
        variant = _EFFORT_TO_VARIANT.get(config.effort, "default")
        command = [
            self.agent_bin, "run", "--format", "json", "--variant", variant,
            "-m", config.model, *config.harness_args, prompt,
        ]
        # Host closes stdin by default; opencode blocks reading an open pipe forever.
        proc = await (env or Host()).exec(command, env=config.env, timeout=config.timeout)

        proc.require_success()
        result = parse_opencode_jsonl(proc.stdout, "judge", "judge", None)
        if result.is_error:
            detail = (proc.stderr or "").strip()[-1000:] or result.result_text[:1000]
            raise RuntimeError(f"opencode judge made no successful model call: {detail}")
        return json.dumps({"result": result.result_text})

    def stream_tool_calls(self, line: str) -> list[dict]:
        """Return the tool calls one OpenCode JSONL line carries, as the trajectory has them.

        Running and pending frames are not calls yet, so a dispatch counts once it completes.
        """
        return _opencode_trajectory(list(iter_events(line)))

    def streamed_activity(self, lines: Iterable[str]) -> bool:
        """Return true when the OpenCode stream proves the model began a turn.

        OpenCode emits step_start/text/tool_use/step_finish (never Claude's `assistant`),
        so any of those proves the agent worked; a budget timeout after one is a clean
        non-fire, not a launch stall.
        """
        turn_events = {"step_start", "text", "tool_use", "step_finish"}
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in turn_events:
                return True
        return False


def _skill_dispatch_name(part: dict) -> str | None:
    """Return the skill name from a `skill` dispatcher tool_use."""
    if part.get("tool") != "skill":
        return None
    state = dict_or_empty(part.get("state"))
    inp = dict_or_empty(state.get("input"))
    name = inp.get("name")
    return name if isinstance(name, str) and name else None


def _tool_dispatches_skill(part: dict, skill_name: str) -> bool:
    """True if this tool_use event's `part` block dispatches the named skill."""
    name = _skill_dispatch_name(part)
    if name is not None and (name == skill_name or name.endswith(f":{skill_name}")):
        return True
    tool = part.get("tool")
    return isinstance(tool, str) and (tool == skill_name or tool.endswith(f":{skill_name}"))


def _tool_call_was_rejected(part: dict) -> bool:
    """True when a tool_use `part` errored because the guest could not grant permission."""
    state = dict_or_empty(part.get("state"))

    if state.get("status") != "error":
        return False

    error = state.get("error")
    return isinstance(error, str) and _PERMISSION_REJECTED_MARKER in error


def _opencode_trajectory(events: list[dict]) -> list[dict]:
    """Canonical trajectory from OpenCode events.

    Completed tool calls are recorded as-is; a call the guest could not approve is kept
    with `"status": "rejected"` so the judge sees what the agent tried. Running and
    pending frames are skipped.
    """
    traj: list[dict] = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = dict_or_empty(event.get("part"))
        tool = part.get("tool")
        if not isinstance(tool, str):
            continue
        state = dict_or_empty(part.get("state"))
        inp = dict_or_empty(state.get("input"))
        if _tool_call_was_rejected(part):
            traj.append(
                {
                    "kind": "tool_call",
                    "id": "",
                    "name": tool,
                    "arguments": inp,
                    "status": "rejected",
                }
            )
            continue
        if state.get("status") != "completed":
            continue
        if tool == "skill":
            skill = _skill_dispatch_name(part)
            traj.append(
                {
                    "kind": "tool_call",
                    "id": "",
                    "name": "Skill",
                    "arguments": {"skill": skill} if skill else {},
                }
            )
        else:
            traj.append({"kind": "tool_call", "id": "", "name": tool, "arguments": inp})
    return traj


def _debug_tail(events: list[dict]) -> str:
    """Last few parsed events re-serialized as JSON, for when the model emits no.

    final text. Always valid UTF-8 (unlike a raw stdout slice, which can land on binary
    bytes); empty when there are no events to show.
    """
    return json.dumps(events[-3:]) if events else ""


def parse_opencode_jsonl(
    stdout: str,
    eval_id: str,
    config: str,
    detect_skill: str | None,
) -> RunResult:
    """Parse OpenCode's JSONL event stream into a RunResult.

    A run is errored when it spent no tokens (the harness never reached the model) or
    when its last tool call was rejected for permission and no final text followed: the
    stream was cut short by a prompt the guest cannot answer, not by the agent finishing.
    """
    events = list(iter_events(stdout))

    text_parts: list[str] = []
    total_tokens = 0
    session_id = ""
    fired = False
    last_tool_rejected = False
    first_ts: int | None = None
    last_ts: int | None = None

    for event in events:
        timestamp = event.get("timestamp")
        if isinstance(timestamp, int):
            first_ts = timestamp if first_ts is None else min(first_ts, timestamp)
            last_ts = timestamp if last_ts is None else max(last_ts, timestamp)
        parsed_session_id = event.get("sessionID")
        if isinstance(parsed_session_id, str) and parsed_session_id:
            session_id = parsed_session_id

        event_type = event.get("type")
        part = dict_or_empty(event.get("part"))

        if event_type == "step_finish":
            tokens = dict_or_empty(part.get("tokens"))
            token_total = tokens.get("total")
            if isinstance(token_total, int):
                total_tokens += token_total
        elif event_type == "text":
            text_value = part.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value)
        elif event_type == "tool_use":
            state = dict_or_empty(part.get("state"))

            if _tool_call_was_rejected(part):
                last_tool_rejected = True
            elif state.get("status") == "completed":
                last_tool_rejected = False
                # Gate on completed frames so `fired` agrees with the process-facts trajectory.
                if detect_skill and _tool_dispatches_skill(part, detect_skill):
                    fired = True

    duration_ms = (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else 0
    result_text = "\n\n".join(text_parts) or _debug_tail(events)
    is_error = total_tokens == 0 or (last_tool_rejected and not text_parts)

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
        trajectory=_opencode_trajectory(events),
        # OpenCode currently reports only total tokens and no terminal result subtype.
    )
