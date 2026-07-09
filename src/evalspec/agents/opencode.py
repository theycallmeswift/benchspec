"""OpenCode (sst/opencode) implementation of the CodingAgent interface.

Provisions OpenCode into a microVM via `npm i -g opencode-ai@<pinned>`, passes the
provider credential as a host-substituted secret scoped to the provider host, builds
the `opencode run --format json` command, and parses its JSONL output. Pinning,
model surface, and effort taxonomy are documented in `agents.md`.

Event shape: JSONL, one event per line, nested
under `part`. Turn events are `step_start` / `text` / `tool_use` / `step_finish`;
there is NO terminal `result` event. A skill dispatch is
`{"type":"tool_use","part":{"tool":"skill","state":{"input":{"name":"<skill>"}}}}`;
the final message concatenates every non-empty `part.text` (blank-line joined); totals
are `step_finish.part.tokens.total`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from evalspec.agents.base import AgentCapabilities, BaseAgent
from evalspec.environments import ExecutionEnv, GuestSandbox, Host
from evalspec.runner import RunResult
from evalspec.trajectory import iter_events

if TYPE_CHECKING:
    from evalspec.judges.config import JudgeConfig

# Credentials OpenCode reads (host-side env var names), in preference order. The
# runner injects whichever is set as a microsandbox secret, substituted only for
# the matching provider host.
AUTH_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
)
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
  const m = content.match(/^---\n([\s\S]*?)\n---/);
  if (!m) return {};
  const fm = {};
  for (const line of m[1].split('\n')) {
    const c = line.indexOf(':');
    if (c > 0) {
      const k = line.slice(0, c).trim();
      const v = line.slice(c + 1).trim().replace(/^["']|["']$/g, '');
      fm[k] = v;
    }
  }
  return fm;
};

const listSkills = () => {
  if (!fs.existsSync(SKILLS_DIR)) return [];
  return fs.readdirSync(SKILLS_DIR).flatMap((dir) => {
    const f = path.join(SKILLS_DIR, dir, 'SKILL.md');
    if (!fs.existsSync(f)) return [];
    const fm = extractFrontmatter(fs.readFileSync(f, 'utf8'));
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
  const list = skills.map((s) => `- \`${s.name}\` — ${s.description}`).join('\n');
  bootstrapCache = `<EVALSPEC_SKILLS_AVAILABLE>
You have access to the following user-installed skills via the \`skill\` tool:

${list}

Use the \`skill\` tool to load any of these when its description matches the task.
</EVALSPEC_SKILLS_AVAILABLE>`;
  return bootstrapCache;
};

export const EvalspecBootstrap = async () => ({
  // OpenCode 1.15.x calls plugins.trigger('experimental.chat.system.transform',
  // {sessionID, model}, {system: r}) where r is the system-prompt string array.
  // Hooks mutate `r` in place (push, splice). Newer versions also expose
  // 'experimental.chat.messages.transform' — register both so this plugin
  // works across versions without coupling to one shape.
  'experimental.chat.system.transform': async (_input, output) => {
    const b = getBootstrap();
    if (!b || !Array.isArray(output.system)) return;
    if (output.system.some(
      (s) => typeof s === 'string' && s.includes('EVALSPEC_SKILLS_AVAILABLE')
    )) return;
    output.system.push(b);
  },
  'experimental.chat.messages.transform': async (_input, output) => {
    const b = getBootstrap();
    if (!b || !output.messages || !output.messages.length) return;
    const firstUser = output.messages.find((m) => m.info && m.info.role === 'user');
    if (!firstUser || !firstUser.parts || !firstUser.parts.length) return;
    if (firstUser.parts.some(
      (p) => p.type === 'text' && p.text && p.text.includes('EVALSPEC_SKILLS_AVAILABLE')
    )) return;
    const ref = firstUser.parts[0];
    firstUser.parts.unshift({ ...ref, type: 'text', text: b });
  },
});
"""

_COMPACT_JSON = {"separators": ((",", ":"))}
_BOOTSTRAP_PACKAGE_JSON = json.dumps(
    {"name": "evalspec-bootstrap", "version": "0.0.0", "type": "module", "main": "index.js"},
    **_COMPACT_JSON,
)
_OPENCODE_CONFIG_JSON = json.dumps(
    {
        "$schema": "https://opencode.ai/config.json",
        "plugin": ["/root/.config/opencode/plugins/evalspec-bootstrap"],
    },
    **_COMPACT_JSON,
)


class OpenCodeAgent(BaseAgent):
    """Store open code agent data."""

    id = "opencode"
    guest_home = "/root"
    skill_load_dir = "/root/.config/opencode/skills"
    # multi_turn: invoke() accepts resume_session_id but does not
    # honor it — each call is a fresh session. token_split: OpenCode usage events
    # carry no cache split, so cost would be a guess.
    capabilities = AgentCapabilities(
        efforts=("low", "medium", "high"),
        multi_turn=False,
        token_split=False,
    )
    PROVISION_SCRIPT = (
        dedent("""\
        apt-get update && apt-get install -y curl ca-certificates nodejs npm &&
        """)
        + 'npm i -g "opencode-ai@${EVALSPEC_OPENCODE_VERSION:-latest}" && '
        # Bake the evalspec bootstrap plugin into the snapshot, plus a global
        # opencode.json that registers it.
        "mkdir -p /root/.config/opencode/plugins/evalspec-bootstrap && "
        + "cat > /root/.config/opencode/plugins/evalspec-bootstrap/package.json <<'EOF_PKG'\n"
        + _BOOTSTRAP_PACKAGE_JSON
        + "\nEOF_PKG\n"
        "cat > /root/.config/opencode/plugins/evalspec-bootstrap/index.js <<'EOF_JS'\n"
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
        self: object,
        auth_value: str = "",
        *,
        auth_env: str = "ANTHROPIC_API_KEY",
        version: str = "latest",
        # Default binding: the guest install path (npm i -g installs the symlink here).
        agent_bin: str = "/usr/local/bin/opencode",
    ) -> None:
        """Initialize the instance."""
        self.agent_bin = agent_bin
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._version = version

    @classmethod
    def for_host(cls: object) -> OpenCodeAgent:
        """An instance bound to the host environment (judge mode): PATH resolves `opencode`."""
        return cls(agent_bin="opencode")

    @classmethod
    def from_env(cls: object) -> OpenCodeAgent:
        """Build the agent from the host env: pinned version (env var > pyproject >.

        'latest') + the preferred credential.
        """
        version = (
            os.environ.get("EVALSPEC_OPENCODE_VERSION")
            or cls._pinned_version_from_pyproject()
            or "latest"
        )
        for env_name in AUTH_ENV_VARS:
            value = os.environ.get(env_name)
            if value:
                return cls(auth_value=value, auth_env=env_name, version=version)
        return cls(version=version)  # no credential set; preflight gates this

    @staticmethod
    def _pinned_version_from_pyproject() -> str | None:
        """Read `[tool.evalspec] opencode_version` from pyproject.toml.

        Tries `$PROJECT_ROOT` first (same env var resolve_repo_root honors), then
        `Path.cwd()` so direct `cli_build` invocations from the repo root still find the
        pin. Returns None if the file is missing or the value isn't a string.
        """
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        candidates = []
        env_root = os.environ.get("PROJECT_ROOT")
        if env_root:
            candidates.append(Path(env_root) / "pyproject.toml")
        candidates.append(Path.cwd() / "pyproject.toml")
        for pyproject in candidates:
            if not pyproject.is_file():
                continue
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
            v = data.get("tool", {}).get("evalspec", {}).get("opencode_version")
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def credential_error() -> str | None:
        """Return a credential preflight error message when credentials are missing."""
        if any(os.environ.get(v) for v in AUTH_ENV_VARS):
            return None
        return "no OpenCode provider credential — set one of " + ", ".join(AUTH_ENV_VARS)

    def version(self: object) -> str:
        """Return the agent CLI version string."""
        return self._version

    def artifact_dirs(self: object) -> list[str]:
        """Return guest directories that may contain agent-authored artifacts."""
        return [f"{self.guest_home}/.config/opencode/skills"]

    def guest_env(self: object) -> dict:
        """Return environment variables passed to guest agent commands."""
        return {
            "HOME": self.guest_home,
            "TZ": "UTC",
            "EVALSPEC_OPENCODE_VERSION": self._version,
        }

    def secrets(self: object) -> list:
        """Return secret values that must be redacted from logs."""
        from microsandbox import Secret

        allow_host = _PROVIDER_HOSTS[self._auth_env]
        guest_env_name = _GUEST_ENV_NAMES.get(self._auth_env, self._auth_env)
        return [
            Secret.env(guest_env_name, value=self._auth_value, allow_hosts=[allow_host]),
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
        """Build the guest command used to invoke the agent."""
        if "/" not in model:
            raise ValueError(
                f"OpenCode needs a provider-qualified model (e.g. 'google/gemini-3.5-flash'), "
                f"got {model!r}. The --evalspec-model default 'sonnet' is a Claude-only alias; "
                f"pass --evalspec-model explicitly under EVALSPEC_AGENT=opencode."
            )
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

    async def provision(self: object, sandbox: object) -> None:
        """Install the agent CLI and credentials inside the guest."""
        res = await sandbox.shell(self.PROVISION_SCRIPT, env=self.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"opencode provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def stage_project_assets(self: object, sandbox: object, project_mount: str) -> None:
        """Copy project-local assets needed by the guest agent."""
        dest = f"{self.guest_home}/.config/opencode/skills"
        await sandbox.shell(
            f"mkdir -p {dest} && "
            f"for src in {project_mount}/skills {project_mount}/.opencode/skills "
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
            # Per-arm extra_env merges over guest_env(), arm env winning.
            env = {**self.guest_env(), **(extra_env or {})}

            res = await GuestSandbox(sandbox).exec(
                cmd,
                cwd=workdir,
                env=env,
                timeout=timeout,
                # Force EOF on stdin so `opencode run` cannot block on an open pipe.
                stdin=b"",
            )
        except (MicrosandboxError, asyncio.TimeoutError, OSError) as e:
            return RunResult(eval_id, config, f"<sandbox-error> {e}"[-2000:], 0, 0, is_error=True)
        if res.exit_code != 0:
            return RunResult(eval_id, config, res.stderr[-2000:], 0, 0, is_error=True)
        return parse_opencode_jsonl(res.stdout, eval_id, config, detect_skill)

    async def judge(
        self: object,
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
        Wraps the final text in the {"result": ...} envelope judge.py parses.
        """
        variant = _EFFORT_TO_VARIANT.get(config.effort, "default")
        command = [
            self.agent_bin, "run", "--format", "json", "--variant", variant,
            "-m", config.model, *config.harness_args, prompt,
        ]
        proc = await (env or Host()).exec(
            command, env=config.env, timeout=config.timeout,
            stdin=subprocess.DEVNULL,  # opencode blocks reading stdin forever without this
        )

        proc.require_success()
        result = parse_opencode_jsonl(proc.stdout, "judge", "judge", None)
        if result.is_error:
            detail = (proc.stderr or "").strip()[-1000:] or result.result_text[:1000]
            raise RuntimeError(f"opencode judge made no successful model call: {detail}")
        return json.dumps({"result": result.result_text})

    def detect_dispatch(self: object, line: str, skill_name: str | None) -> bool:
        """Return true when a line shows any skill route."""
        text = line.strip()
        if not text:
            return False
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not (isinstance(event, dict) and event.get("type") == "tool_use"):
            return False
        part = event.get("part")
        if not isinstance(part, dict):
            return False
        return _part_dispatches_any_skill(part, skill_name)

    def detect_fired(self: object, lines: object, skill_name: str) -> bool:
        """Return true when the target skill fired in the OpenCode stream.

        Uses the strict `_tool_dispatches_skill` matcher directly (not `detect_dispatch`,
        which now early-stops on any skill).
        """
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not (isinstance(event, dict) and event.get("type") == "tool_use"):
                continue
            part = event.get("part")
            if isinstance(part, dict) and _tool_dispatches_skill(part, skill_name):
                return True
        return False

    def streamed_activity(self: object, lines: object) -> bool:
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
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    name = inp.get("name")
    return name if isinstance(name, str) and name else None


def _tool_dispatches_skill(part: dict, skill_name: str) -> bool:
    """True if this tool_use event's `part` block dispatches the named skill."""
    name = _skill_dispatch_name(part)
    if name is not None and (name == skill_name or name.endswith(f":{skill_name}")):
        return True
    tool = part.get("tool")
    return isinstance(tool, str) and (tool == skill_name or tool.endswith(f":{skill_name}"))


def _part_dispatches_any_skill(part: dict, skill_name: str | None) -> bool:
    """Return true when a part routes to any skill."""
    tool = part.get("tool")
    if not isinstance(tool, str):
        return False
    if tool == "skill":
        return True
    return bool(skill_name) and (tool == skill_name or tool.endswith(f":{skill_name}"))


def _opencode_trajectory(events: list[dict]) -> list[dict]:
    """Canonical trajectory from OpenCode events."""
    traj: list[dict] = []
    for ev in events:
        if ev.get("type") != "tool_use":
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        tool = part.get("tool")
        if not isinstance(tool, str):
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        if state.get("status") != "completed":
            continue
        inp = state.get("input") if isinstance(state.get("input"), dict) else {}
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
    """Parse OpenCode's JSONL event stream into a RunResult."""
    events = list(iter_events(stdout))

    text_parts: list[str] = []
    total_tokens = 0
    session_id = ""
    fired = False
    first_ts: int | None = None
    last_ts: int | None = None

    for ev in events:
        ts = ev.get("timestamp")
        if isinstance(ts, int):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        sid = ev.get("sessionID")
        if isinstance(sid, str) and sid:
            session_id = sid

        etype = ev.get("type")
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}

        if etype == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            t = tokens.get("total")
            if isinstance(t, int):
                total_tokens += t
        elif etype == "text":
            text_value = part.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value)
        elif etype == "tool_use" and detect_skill:
            # Gate on completed frames so `fired` agrees with the process-facts trajectory.
            state = part.get("state") if isinstance(part.get("state"), dict) else {}

            if state.get("status") == "completed" and _tool_dispatches_skill(part, detect_skill):
                fired = True

    duration_ms = (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else 0
    result_text = "\n\n".join(text_parts) or _debug_tail(events)
    is_error = total_tokens == 0

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
