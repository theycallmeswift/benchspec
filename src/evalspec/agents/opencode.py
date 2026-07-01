"""OpenCode (sst/opencode) implementation of the CodingAgent interface.

Provisions OpenCode into a microVM via `npm i -g opencode-ai@<pinned>`, passes the
provider credential as a host-substituted secret scoped to the provider host, builds
the `opencode run --format json` command, and parses its JSONL output. Pinning,
model surface, and effort taxonomy are documented in `agents.md`.

Event shape (verified live, OpenCode 1.15.13): JSONL, one event per line, nested
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
import sys
from pathlib import Path

from evalspec.agents.base import AgentCapabilities, BaseAgent
from evalspec.agents.judge_cli import run_host_judge
from evalspec.runner import RunResult
from evalspec.trajectory import iter_events

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
_RESERVED_HARNESS_SHORT_FLAGS = {arg for arg in _RESERVED_HARNESS_ARGS if arg.startswith("-") and not arg.startswith("--")}


def _validate_harness_args(harness_args: list[str] | None) -> list[str]:
    if harness_args is None:
        return []
    for arg in harness_args:
        if arg in _RESERVED_HARNESS_ARGS or any(
            arg.startswith(f"{flag}=") for flag in _RESERVED_HARNESS_LONG_FLAGS
        ) or any(arg.startswith(flag) and len(arg) > len(flag) for flag in _RESERVED_HARNESS_SHORT_FLAGS):
            raise ValueError(f"reserved harness arg for OpenCode: {arg}")
    return harness_args


# Minimal OpenCode plugin baked into the snapshot. Mirrors superpowers'
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

_BOOTSTRAP_PACKAGE_JSON = (
    '{"name":"evalspec-bootstrap","version":"0.0.0","type":"module","main":"index.js"}'
)

_OPENCODE_CONFIG_JSON = (
    '{"$schema":"https://opencode.ai/config.json",'
    '"plugin":["/root/.config/opencode/plugins/evalspec-bootstrap"]}'
)


class OpenCodeAgent(BaseAgent):
    id = "opencode"
    guest_home = "/root"
    skill_load_dir = "/root/.config/opencode/skills"
    # multi_turn: invoke() accepts resume_session_id for protocol parity but does not
    # honor it — each call is a fresh session. token_split: OpenCode usage events
    # carry no cache split, so cost would be a guess.
    capabilities = AgentCapabilities(
        efforts=("low", "medium", "high"),
        multi_turn=False,
        token_split=False,
    )
    OPENCODE_BIN = "/usr/local/bin/opencode"  # npm i -g installs the symlink here
    PROVISION_SCRIPT = (
        "apt-get update && apt-get install -y curl ca-certificates nodejs npm && "
        'npm i -g "opencode-ai@${EVALSPEC_OPENCODE_VERSION:-latest}" && '
        # Bake the evalspec bootstrap plugin into the snapshot, plus a global
        # opencode.json that registers it. Mirrors superpowers' approach so the
        # `skill` tool is actually considered by models that wouldn't reach for
        # it unprompted (e.g. Gemini Flash).
        "mkdir -p /root/.config/opencode/plugins/evalspec-bootstrap && "
        'cat > /root/.config/opencode/plugins/evalspec-bootstrap/package.json <<\'EOF_PKG\'\n'
        + _BOOTSTRAP_PACKAGE_JSON
        + '\nEOF_PKG\n'
        'cat > /root/.config/opencode/plugins/evalspec-bootstrap/index.js <<\'EOF_JS\'\n'
        + _BOOTSTRAP_PLUGIN_JS
        + 'EOF_JS\n'
        'cat > /root/.config/opencode/opencode.json <<\'EOF_CFG\'\n'
        + _OPENCODE_CONFIG_JSON
        + '\nEOF_CFG\n'
        # Warm OpenCode's one-time SQLite migration at snapshot-build time. On first
        # invocation OpenCode prints "Performing one time database migration..." and
        # creates ~/.local/share/opencode/opencode.db; deferred to a measured run
        # that banner is captured as the agent result, erroring every arm. `auth list`
        # triggers the migration with no provider credential and no network, so the
        # migrated DB bakes into the snapshot and real runs boot past it.
        + "/usr/local/bin/opencode auth list > /dev/null 2>&1 && "
        # Fail provisioning loudly if the migration didn't bake the DB into the
        # snapshot — otherwise a future opencode that changes the migration trigger
        # silently reintroduces the per-VM banner that errors every arm.
        + "test -f /root/.local/share/opencode/opencode.db"
    )

    def __init__(self, auth_value: str = "", *, auth_env: str = "ANTHROPIC_API_KEY",
                 version: str = "latest"):
        self._auth_value = auth_value
        self._auth_env = auth_env
        self._version = version

    @classmethod
    def from_env(cls) -> OpenCodeAgent:
        """Build the agent from the host env: pinned version (env var > pyproject >
        'latest') + the preferred credential."""
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
        `Path.cwd()` so direct `cli_build` invocations from the repo root still find
        the pin. Returns None if the file is missing or the value isn't a string."""
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
        """None if a usable credential is set, else a remediation message for preflight."""
        if any(os.environ.get(v) for v in AUTH_ENV_VARS):
            return None
        return (
            "no OpenCode provider credential — set one of "
            + ", ".join(AUTH_ENV_VARS)
        )

    def version(self) -> str:
        return self._version

    def artifact_dirs(self) -> list[str]:
        # OpenCode discovers (and the agent scaffolds) personal skills under
        # $HOME/.config/opencode/skills — outside the workdir mount. Snapshot it so a skill
        # the agent writes here reaches the judge; the staged skills drop out of the
        # session's baseline diff, leaving only the agent's own work.
        return [f"{self.guest_home}/.config/opencode/skills"]

    def guest_env(self) -> dict:
        # HOME so OpenCode finds its config dir; TZ=UTC pins the guest clock to the
        # zone the host computes {TODAY} in. EVALSPEC_OPENCODE_VERSION feeds the
        # provision script's `npm i -g opencode-ai@${EVALSPEC_OPENCODE_VERSION}` at
        # snapshot-build time.
        return {
            "HOME": self.guest_home,
            "TZ": "UTC",
            "EVALSPEC_OPENCODE_VERSION": self._version,
        }

    def secrets(self) -> list:
        from microsandbox import Secret

        allow_host = _PROVIDER_HOSTS[self._auth_env]  # loud KeyError beats a silent wrong-host route
        guest_env_name = _GUEST_ENV_NAMES.get(self._auth_env, self._auth_env)
        return [
            Secret.env(guest_env_name, value=self._auth_value, allow_hosts=[allow_host]),
        ]

    def build_command(
        self, prompt, *, plugin_dir, model, effort, resume_session_id, detect_skill,
        harness_args: list[str] | None = None,
    ) -> list[str]:
        if "/" not in model:
            raise ValueError(
                f"OpenCode needs a provider-qualified model (e.g. 'google/gemini-3.5-flash'), "
                f"got {model!r}. The --evalspec-model default 'sonnet' is a Claude-only alias; "
                f"pass --evalspec-model explicitly under EVALSPEC_AGENT=opencode."
            )
        # Map the agent-neutral effort tier to OpenCode's --variant. plugin_dir and
        # resume_session_id are accepted for protocol parity but unused — OpenCode v1 has
        # no equivalents.
        variant = {"low": "fast", "medium": "default", "high": "thorough"}.get(effort, "default")
        return [
            self.OPENCODE_BIN, "run",
            "--format", "json",
            "--variant", variant,
            "-m", model,
            *_validate_harness_args(harness_args),
            prompt,
        ]

    async def provision(self, sb) -> None:
        res = await sb.shell(self.PROVISION_SCRIPT, env=self.guest_env())
        if res.exit_code != 0:
            raise RuntimeError(
                f"opencode provision failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
            )

    async def stage_project_assets(self, sb, project_mount: str) -> None:
        # OpenCode auto-discovers personal skills from $HOME/.config/opencode/skills/<skill>/
        # and project skills from <project>/.opencode/skills/<skill>/. Staging to the
        # personal location guarantees discovery regardless of the agent's cwd.
        #
        # Stage from ALL three plugin-shaped layouts (merging into one dest):
        #   <project>/skills/             — canonical Claude Code plugin layout
        #                                   (no leading dot; lives under plugin root)
        #   <project>/.opencode/skills/   — OpenCode's project-skills convention
        #   <project>/.claude/skills/     — repos that put non-plugin skills here
        #
        # Claude's stage_project_assets only copies .claude/skills/ because Claude
        # plugins use --plugin-dir to surface the canonical skills/. OpenCode has no
        # equivalent plugin-dir flag, so we copy everything ourselves. Same
        # per-arm isolation: a copy, not a mount.
        dest = f"{self.guest_home}/.config/opencode/skills"
        await sb.shell(
            f"mkdir -p {dest} && "
            f"for src in {project_mount}/skills {project_mount}/.opencode/skills {project_mount}/.claude/skills; do "
            f"  if [ -d $src ]; then cp -r $src/. {dest}/ 2>/dev/null || true; fi; "
            f"done",
            env=self.guest_env(),
        )

    async def invoke(
        self, sb, prompt, *, eval_id, config, workdir, plugin_dir, model, effort,
        resume_session_id, detect_skill, harness_args: list[str] | None = None,
        extra_env: dict | None = None,
        timeout: int = 600,
    ) -> RunResult:
        from microsandbox.errors import MicrosandboxError

        cmd = self.build_command(
            prompt, plugin_dir=plugin_dir, model=model, effort=effort,
            resume_session_id=resume_session_id, detect_skill=detect_skill,
            harness_args=harness_args,
        )
        try:
            res = await sb.exec(
                # Per-arm extra_env merges over guest_env(), arm env winning.
                cmd[0], cmd[1:], cwd=workdir,
                env={**self.guest_env(), **(extra_env or {})}, timeout=timeout,
                # Force EOF on stdin: `opencode run` blocks reading stdin forever
                # without it (microsandbox's default leaves a pipe open), which
                # bins the whole arm against the per-eval timeout for no work.
                stdin=b"",
            )
        except (MicrosandboxError, asyncio.TimeoutError, OSError) as e:
            return RunResult(eval_id, config, f"<sandbox-error> {e}"[-2000:], 0, 0, is_error=True)
        if res.exit_code != 0:
            return RunResult(eval_id, config, res.stderr_text[-2000:], 0, 0, is_error=True)
        return parse_opencode_jsonl(res.stdout_text, eval_id, config, detect_skill)

    def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str:
        """Delegate judging to the host's Claude CLI so grading quality stays
        consistent across the matrix — task arms differ; grading should not. See
        run_host_judge for the RuntimeError-on-infra-failure contract (a missing
        `claude` on PATH included) that keeps an infra failure from being mistaken
        for assertion failures."""
        return run_host_judge(prompt, model=model, timeout=timeout)

    def detect_dispatch(self, line: str, skill_name: str | None) -> bool:
        """True if the line shows ANY skill being routed to (early-stop: routing is
        decided, don't wait out the turn). The tally (detect_fired) re-checks for OUR
        skill, so loosening here can't widen the fire count."""
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

    def detect_fired(self, lines, skill_name: str) -> bool:
        """Tally whether OUR skill fired. Uses the strict `_tool_dispatches_skill`
        matcher directly (NOT detect_dispatch, which now early-stops on any skill)."""
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

    def streamed_activity(self, lines) -> bool:
        """True if the model began a turn. OpenCode emits step_start/text/tool_use/
        step_finish (never Claude's `assistant`), so any of those proves the agent
        worked — a budget timeout after one is a clean non-fire, not a launch stall."""
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
    """The skill targeted by a `skill` dispatcher tool_use (part.state.input.name),
    or None if this isn't a skill dispatch. Single source for both fired-detection
    and trajectory normalization so they can't drift."""
    if part.get("tool") != "skill":
        return None
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    name = inp.get("name")
    return name if isinstance(name, str) and name else None


def _tool_dispatches_skill(part: dict, skill_name: str) -> bool:
    """True if this tool_use event's `part` block dispatches the named skill.

    Two fire shapes (mirroring trigger.py's Claude detector):
    - Primary: `part.tool == "skill"` (OpenCode's native skill dispatcher) with
      `part.state.input.name` matching the skill (exact or namespaced).
    - Fallback: `part.tool` itself is the skill name (some agents register skills
      directly as tools instead of going through a dispatcher)."""
    name = _skill_dispatch_name(part)
    if name is not None and (name == skill_name or name.endswith(f":{skill_name}")):
        return True
    tool = part.get("tool")
    return isinstance(tool, str) and (tool == skill_name or tool.endswith(f":{skill_name}"))


def _part_dispatches_any_skill(part: dict, skill_name: str | None) -> bool:
    """True if this part routes to ANY skill (the `skill` dispatcher), or — as the
    namespaced-tool fallback — OUR skill registered directly as a tool. Mirrors
    Claude's `dispatches_skill`: early-stop on any route; the tally filters to ours."""
    tool = part.get("tool")
    if not isinstance(tool, str):
        return False
    if tool == "skill":
        return True
    return bool(skill_name) and (tool == skill_name or tool.endswith(f":{skill_name}"))


def _opencode_trajectory(events: list[dict]) -> list[dict]:
    """Canonical trajectory (schema: see evalspec.trajectory) from OpenCode events.

    OpenCode models a tool use as a single completed `tool_use` event (input under
    part.state.input) with NO separate tool_result frame, so we emit tool_call
    events only. A skill dispatch (part.tool == "skill", state.input.name) is
    normalized to the canonical Skill shape (name="Skill", arguments={"skill": ...})
    so skills_dispatched()/render_process_facts() stay agent-agnostic. OpenCode
    exposes no per-call id we link against, so id is "" (there is no result to link)."""
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
            traj.append({"kind": "tool_call", "id": "", "name": "Skill",
                         "arguments": {"skill": skill} if skill else {}})
        else:
            traj.append({"kind": "tool_call", "id": "", "name": tool, "arguments": inp})
    return traj


def _debug_tail(events: list[dict]) -> str:
    """Last few parsed events re-serialized as JSON, for when the model emits no
    final text. Always valid UTF-8 (unlike a raw stdout slice, which can land on
    binary bytes); empty when there are no events to show."""
    return json.dumps(events[-3:]) if events else ""


def parse_opencode_jsonl(
    stdout: str, eval_id: str, config: str, detect_skill: str | None,
) -> RunResult:
    """Parse OpenCode's JSONL event stream into a RunResult.

    OpenCode has no terminal `result` event the way Claude Code does — the agent
    emits `step_start` / `text` / `tool_use` / `step_finish` events and exits.
    The final agent message concatenates every non-empty `part.text` event (joined
    with blank lines) so the judge sees mid-run narration, not just the wrap-up;
    when none is present (a tool-only run), it falls back to a re-serialized tail
    of parsed events (`_debug_tail`). Totals come from `step_finish.part.tokens.total`;
    duration is the timestamp span. `errored` keys on zero tokens — proof the agent
    never reached the wire — NOT on a missing text event (concise models routinely
    finish via tool calls without a wrap-up message)."""
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
            # Gate on completed frames like _opencode_trajectory, so this `fired` and
            # the process-facts trajectory can't disagree. The trigger router's
            # detect_fired stays status-agnostic on purpose: routing is decided at
            # dispatch, not completion.
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            if state.get("status") == "completed" and _tool_dispatches_skill(part, detect_skill):
                fired = True

    duration_ms = (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else 0
    return RunResult(
        eval_id=eval_id, config=config,
        # No text event isn't a failure — concise models (e.g. Gemini) often skip a wrap-up
        # when tool calls accomplished the goal, and the judge can still grade from the
        # workdir. Fall back to the last few PARSED events (re-serialized as JSON, always
        # valid UTF-8) for debugging — never a raw stdout slice, which can cut mid-frame into
        # binary bytes (snapshot diffs, etc.) and poison any assertion that reads the message.
        result_text="\n\n".join(text_parts) or _debug_tail(events),
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        # Errored ⇒ the agent never made an API call (auth failure, launch crash,
        # immediate exit). `total_tokens > 0` proves at least one model call hit
        # the wire, regardless of whether it produced a final summary text.
        is_error=(total_tokens == 0),
        session_id=session_id,
        fired=fired,
        raw=stdout,
        trajectory=_opencode_trajectory(events),
        # cache_*/result_subtype stay at defaults: OpenCode's verified token object
        # exposes only tokens.total (no cache breakdown), and the stream has no
        # terminal result event (no subtype/finish-reason). Revisit if a live run
        # surfaces either.
    )
