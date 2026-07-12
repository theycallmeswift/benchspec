# Coding agents

evalspec runs each eval inside a microsandbox microVM and drives a **coding agent** (a headless CLI like Claude Code or OpenCode). Agent-specific code lives behind the `CodingAgent` interface in `evalspec.agents`; the sandbox lifecycle, grading, and honesty contract never name a concrete agent.

`--evalspec-agent` / `EVALSPEC_AGENT` sets the run-level agent (flag > env > pyproject > default). It backs the `__route__` sandbox path and `make_agent()` when no explicit harness is given. The default is `claude-code`. Unknown values fail at startup naming the source.

```bash
pytest --evalspec-agent opencode   # one-off override
```

To compare harnesses in output evals, define a multi-harness eval set — arms carrying different `harness` values — rather than separate cross-agent CLI runs. Adding another agent is additive — implement the protocol, register in `evalspec.agents._REGISTRY`.

## The boundary

```
src/evalspec/agents/
  base.py       # CodingAgent protocol — the contract sandbox.py depends on
  claude.py     # ClaudeCodeAgent
  codex.py      # CodexAgent
  opencode.py   # OpenCodeAgent
  __init__.py   # make_agent() factory + credential_preflight_error()
```

`sandbox.py` calls only the protocol plus `make_agent()` / `credential_preflight_error()`; it never names a concrete sandbox runtime either — the sandbox lifecycle (host preflight, snapshot existence, cache fingerprint, build, session creation) sits behind a separate `SandboxBackend` adapter (`evalspec.backend`), resolved from the set's `sandbox` value (`microsandbox` today; `docker` is registered but fails fast as not implemented). Snapshots key on `evalspec-{backend.id}-{agent.id}-{agent.version()}-{fingerprint}`, where the fingerprint folds the backend id, the declared base-image reference (the tag/ref as configured — not resolved to a digest), the agent's `install_fingerprint()`, and the environment script bytes — so each backend/agent/version/install/env combination caches its own image and multiple coexist on one host. A moved floating tag does not rebuild; the backend records the actual pulled digest in Phase-8 artifacts. `make_agent(harness)` selects a specific agent per arm (an eval set's columns may span harnesses); `make_agent()` with no argument reads the run-level agent (`--evalspec-agent` / `EVALSPEC_AGENT`) for the `__route__` sandbox path. Grading selects its adapter from its own `JudgeConfig`, independent from the task agent (see [Grading uses the same adapter](#grading-uses-the-same-adapter-in-a-different-environment) below).

## The `CodingAgent` protocol

Implement every member (signatures in `base.py`):

| Member | Responsibility |
|---|---|
| `id: str` | Stable slug — snapshot-cache key and report label (e.g. `"claude-code"`). |
| `guest_home: str` | The agent's `HOME` inside the guest; asset staging target and `__route__`-run cwd. |
| `skill_load_dir: str` | Absolute guest path the agent auto-loads skills from (e.g. `/root/.claude/skills`). Must differ from `FIXED_SKILLS_HOME` and `guest_home` — the bridge `rm -rf`s it before symlinking. |
| `agent_bin: str` | The binary this **instance** runs — an instance is bound to one execution environment. The default binding is the guest install path (`build_command`'s argv[0] for sandbox runs); `for_host()` returns an instance bound to the host name PATH resolves (judge mode's argv[0], the judge binary preflight, `binary_version()`). |
| `capabilities: AgentCapabilities` | Typed capability declaration — see the field table below for each field's consumer (`efforts` is documentation-only today). |
| `version() -> str` | Cache-key input — pinned version or `"latest"`. Bumping produces a new snapshot. |
| `provision_script() -> str` | The fully-resolved commands that install the CLI in the guest, with any instance state (e.g. a pinned version) baked in. `provision()` runs exactly this. |
| `install_fingerprint() -> str` | Cache-key input for the CLI install: a hash of `provision_script()`. Because that's the single source of truth for what installs the CLI, any change to the installer (revision, package list, pinned version, bootstrap commands) rebuilds the snapshot — nothing to fold by hand. (The agent version also appears directly in the snapshot name, so a version bump rebuilds even for an installer that fetches `latest` rather than pinning.) |
| `bridge_skills_home_script() -> str` | POSIX-sh run once at provision: symlinks `skill_load_dir` → `FIXED_SKILLS_HOME` (`/home/evalspec/skills`) so a per-cell `setup.sh` that installs into the fixed home lands where the agent auto-loads. The fixed home is agent-neutral; `skill_load_dir` is the only agent-specific fact. |
| `cell_env(*, arm, model, eval_set) -> dict` | The env one cell's `setup.sh` runs under: `guest_env()` plus `EVALSPEC_ARM` / `EVALSPEC_MODEL` / `EVALSPEC_HARNESS` / `EVALSPEC_SET`. `EVALSPEC_HARNESS` is `self.id` (the agent owns it). `EVALSPEC_SET` names the explicitly-selected set (`--evalspec-set` / `make evals SET=`), for `setup.sh` branching — empty when the run falls back to the pyproject `default-set`. `EVALSPEC_MODEL` is informational for `setup.sh` — it does NOT route the task model. |
| `artifact_dirs() -> list[str]` | Guest dirs where the agent scaffolds skills (e.g. `~/.claude/skills`) — outside the workdir mount. The session snapshots them per turn and merges the agent-authored files (diffed against the staged baseline) into the judge's facts, so a skill written there isn't invisible. Return `[]` if the agent writes only to the workdir. |
| `provision(sandbox)` | Install CLI + system deps into a booted sandbox. Runs once, captured as the snapshot; raise on failure. |
| `secrets() -> list` | `microsandbox.Secret` entries to inject (e.g. an API key scoped to the provider host). Value never enters the guest. |
| `guest_env() -> dict` | Per-exec env (e.g. `HOME`, sandbox flags). No credential values — those ride as secrets. |
| `stage_project_assets(sandbox, project_mount)` | Copy project-side assets (canonically skills) into the guest. **`__route__` path only** — output evals install per-cell via `setup.sh` + the skills-home bridge, so this no longer fires for them. No-op if nothing to stage. |
| `build_command(prompt, *, plugin_dir, model, effort, resume_session_id, detect_skill, harness_args=None)` | Build the headless argv. With `detect_skill` set, emit a streamable format so firing is detectable; the in-tree agents stream unconditionally (both arms), so `detect_skill` gates only firing detection downstream, not the format. Validate `harness_args` against the adapter's reserved identity/control flags, append supported pass-through args in a position that preserves the CLI shape, and fail loudly if the adapter cannot support them. |
| `invoke(sandbox, prompt, *, ..., extra_env=None, harness_args=None) -> RunResult` | Run one turn in the sandbox; parse stdout into a `RunResult` (see `runner.py`). Surface failures as `is_error=True`, not exceptions. `extra_env` (an arm's per-arm `env`, already `$VAR`-expanded) merges over `guest_env()` for the agent exec — arm keys win, so a set's leaky-OpenRouter arm reaches the agent and not just `setup.sh`. `harness_args` are the resolved set+arm pass-through tokens for the invocation layer, not environment mutation. |
| `detect_dispatch(line, skill_name) -> bool` | Given one streamed line, return True if it shows a skill dispatch. Keeps `trigger.py` and `sandbox.py` agent-agnostic. |
| `detect_fired(lines, skill_name) -> bool` | Given the whole routing stream, return True if *our* skill fired. Pairs with `detect_dispatch` (single-line) for the full firing tally on the `__route__` sandbox path. |
| `streamed_activity(lines) -> bool` | True if the model began a turn (an `assistant` event), distinguishing a clean non-fire from a retryable launch stall so a budget timeout isn't misread. |

`AgentCapabilities` fields:

| Field | Type | Consumer |
|---|---|---|
| `efforts` | `tuple[str, ...]` | Documentation-only — the values the agent's CLI accepts for the effort flag. Not validated here; an unsupported value passes through and surfaces as an error from the agent CLI. |
| `multi_turn` | `bool` | `execution.run_eval_arm` — raises `RuntimeError` before any turn runs if the eval has more than one turn and the agent does not honor `resume_session_id`. OpenCode's `invoke` accepts the argument for protocol parity but starts a fresh session each call, so a multi-turn eval on OpenCode would silently measure the wrong thing; the gate fails loudly instead. |
| `token_split` | `bool` | Recorded in the run manifest (`meta.json`) so aggregators know whether per-run cost is computable from the `input_tokens` / `output_tokens` split each sample writes to `timing.json`. `False` for OpenCode: its usage events carry only `tokens.total`, no cache breakdown. |

Plus the class-level conveniences callers rely on:

- `cls.from_env() -> CodingAgent` — build from host env (version pin + credential), bound to the guest binary.
- `cls.for_host() -> CodingAgent` — an instance bound to the host environment (judge mode); `agent_bin` resolves from PATH.
- `cls.credential_error() -> str | None` — `None` if a usable credential is set, else a preflight remediation string.

## Grading uses the same adapter, in a different environment

One adapter per harness, two entry points: `invoke` runs the harness inside the sandbox for task arms; `judge` grades with the same harness on the host. Where a process runs is an **execution environment** (`evalspec.environments`): `GuestSandbox` wraps a live microVM session's exec, `Host` runs a fresh host process, and both return the same `ProcResult` — the adapter builds commands and parses output without knowing which one it got, so sandbox-vs-host is a parameter of the call, not a code path per harness.

Judging stays independent from the task *arms*: a run resolves one `JudgeConfig` (`evalspec.judges`, configured via `[tool.evalspec.judge]`; see [`configuration.md`](configuration.md)) naming a judge **harness** and model. `judge.py`'s `grade_run` calls `evalspec.judges.run_judge(prompt, config=judge_config)`, which expands the judge env and runs `agent_class(harness).for_host().judge(prompt, config)` — a host-bound instance in a fresh `Host` environment, never the task arm's sandbox or session. Each adapter's `judge` reuses the same output parser as its sandbox path (Claude's `--output-format json` emits the `{"result": "<judge-json-string>"}` envelope natively; Codex/OpenCode parse with `parse_codex_jsonl`/`parse_opencode_jsonl` and wrap) and raises `RuntimeError` for its harness's infra-failure shapes (missing binary, nonzero exit, error events / zero-token runs).

`binder.py`'s prose→checker classifier is a fixed, direct Gemini API call (`gemini-3.1-flash-lite`, stdlib `urllib` — no harness, no host CLI), independent from the configured judge harness and from every task arm's own harness. `ClaudeCodeAgent.judge` is the only host-Claude call site left in the package.

## What `invoke` returns

`invoke` parses one turn's stdout into a `RunResult` (`runner.py`). Beyond `result_text` / `is_error` / `session_id`, three fields carry post-hoc and judge signal:

| Field | Meaning |
|---|---|
| `trajectory` | Ordered `tool_call`/`tool_result` events for the turn. Both arms stream, so both populate it; `[]` only when nothing streamed (e.g. a launch error before the first event). Shape in [`schema.md`](schema.md). |
| `result_subtype` | The CLI result event's `subtype` (`success`, `error_max_turns`, …) — NOT the API `stop_reason`. |
| `cache_read_tokens` / `cache_creation_tokens` | Prompt-cache usage from the result event; summed into `timing.json`. |

The trajectory feeds **process facts**: `render_process_facts` collapses cross-turn tool and sub-skill activity into a compact block, and `build_judge_prompt` hands it to the judge as evidence a final-message-only grader can't see — a `Skill(name)` dispatch proves the agent used a sub-skill even when its message is silent. `tool_call_count` and `skills_dispatched` in `transcript.json` come from the same trajectory.

## Activation and the routing primitives

Skill activation is graded as an **ordinary assertion**, not a separate eval type. An eval writes `` - [ ] Skill `X` invoked `` (or `` not invoked ``); the binder maps it to the `skill_invoked` checker, which grades against the arm's dispatched-skills set derived from the trajectory. There is no separate trigger-eval file, no versioned trigger schema, and no routing-score modes — activation rides the same discovery, binding, and grading path as every other assertion (see [`schema.md`](schema.md#assertions) and [`concepts.md`](concepts.md#activation-is-an-assertion-not-a-gate)).

The skill-detection/routing helpers in `evalspec.trigger` **remain** — they back the `__route__` sandbox path and each adapter's `detect_dispatch` / `detect_fired`:

- `detect_skill_fired` — whole-stream fire detection.
- `dispatches_skill` — single-line dispatch detection.
- `streamed_activity` — did the model begin a turn (a clean non-fire vs. a retryable launch stall).
- `RoutingError` — raised (in `sandbox.py`) when a routing probe streams no activity before its budget.

The trigger-**eval scoring** helpers were removed outright — no compatibility shims: `fire_threshold`, `trigger_record`, `count_fires`, `xfail_applies`, and `first_dispatched_skill` no longer exist.

## `ClaudeCodeAgent` — worked example

| Field | Value |
|---|---|
| `id` | `claude-code` |
| Command | `claude -p <prompt> --output-format stream-json --verbose --permission-mode bypassPermissions --model <m> --effort <e> [harness_args...]` |
| Effort taxonomy | `low / medium / high / xhigh / max` |
| Stream format | `--output-format stream-json --verbose`, always (both arms) — the trajectory is captured symmetrically |
| Version pin | `EVALSPEC_CLAUDE_VERSION` env var (default `latest`) |
| Provision | `curl -fsSL https://claude.ai/install.sh \| bash` |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN` (preferred), `ANTHROPIC_API_KEY` (fallback) |
| Secret scope | `api.anthropic.com` only |
| Guest env | `HOME=/root IS_SANDBOX=1 TZ=UTC` |
| Cell env | `guest_env` + `EVALSPEC_ARM` / `EVALSPEC_MODEL` / `EVALSPEC_HARNESS=claude-code` / `EVALSPEC_SET` — the env each cell's `setup.sh` runs under |
| Skills bridge | `skill_load_dir=/root/.claude/skills` symlinked to `/home/evalspec/skills` once at provision |
| Output-eval install | per-cell `setup.sh` writing into `/home/evalspec/skills` (the bridge target); no implicit copy or plugin dir |
| Harness args | Appended after evalspec-managed flags. Reserved: prompt/print flags (`-p`, `--print`, `--prompt`), model, effort, output/input format, permission mode, and resume/session controls (`-r`, `--resume`, `-c`, `--continue`, `--session-id`, `--fork-session`, `--no-session-persistence`). Use `["--plugin-dir", "/project"]` to opt an output arm into a Claude plugin surface. |
| Project assets (`__route__` path) | `cp -r {project}/.claude/skills {HOME}/.claude/skills` |
| Detect dispatch | Claude stream-json `Skill` tool_use, or a tool_use whose name is the skill (namespaced fallback) |

Why `bypassPermissions` over `acceptEdits`: the microVM is the containment boundary, so the agent runs with full autonomy. The `--allowedTools Bash` workaround the host-spawn version needed isn't required inside the VM.

## `OpenCodeAgent` — worked example

| Field | Value |
|---|---|
| `id` | `opencode` |
| Command | `opencode run --format json --variant <v> -m <model> [harness_args...] <prompt>` |
| Effort taxonomy | `low / medium / high` (mapped to OpenCode's variants `fast / default / thorough`) |
| Stream format | Same `--format json` — JSONL with `tool_use` events (no separate stream flag) |
| Version pin | `EVALSPEC_OPENCODE_VERSION` env var > `[tool.evalspec] opencode_version` in `pyproject.toml` > `latest` |
| Provision | `apt-get install nodejs npm && npm i -g opencode-ai@${EVALSPEC_OPENCODE_VERSION}` |
| Credentials | `OPENROUTER_API_KEY` (preferred), `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (in that fallback order) |
| Secret scope | `openrouter.ai`, `api.anthropic.com`, or `generativelanguage.googleapis.com` based on which credential is set |
| Guest env | `HOME=/root TZ=UTC EVALSPEC_OPENCODE_VERSION=<pinned>` |
| Cell env | `guest_env` + `EVALSPEC_ARM` / `EVALSPEC_MODEL` / `EVALSPEC_HARNESS=opencode` / `EVALSPEC_SET` — the env each cell's `setup.sh` runs under |
| Skills bridge | `skill_load_dir=/root/.config/opencode/skills` symlinked to `/home/evalspec/skills` once at provision |
| Output-eval install | per-cell `setup.sh` writing into `/home/evalspec/skills` (the bridge target); no implicit copy |
| Harness args | Inserted before the trailing prompt positional. Reserved: model, variant/effort, format, session controls (`-c`, `--continue`, `-s`, `--session`, `--fork`), and prompt-position controls (`--command`, `--prompt`). |
| Project assets (`__route__` path) | `.opencode/skills` (native); also reads `.claude/skills` for repos that ship the Claude layout |
| Detect dispatch | JSONL `tool_use` whose `name` is the skill (exact or namespaced); no generic Skill dispatcher today |

`build_command` accepts `plugin_dir` and `resume_session_id` for protocol parity but ignores them — OpenCode v1 has no equivalents. Multi-turn evals under OpenCode therefore start a fresh session per turn; chained sessions are Claude-only. Non-empty `harness_args` are supported only when they can be placed before the prompt without changing evalspec-owned model, variant, format, or parsing semantics.

## `CodexAgent` — worked example

| Field | Value |
|---|---|
| `id` | `codex` |
| Command | `codex exec --json -m <model> -C <workdir> --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check [harness_args...] <prompt>` |
| Effort taxonomy | none declared; Codex `exec` has no stable evalspec-owned effort flag in the supported CLI surface |
| Stream format | `--json` JSONL with `thread.started`, turn events, `item.*`, and `turn.completed` usage |
| Version pin | `EVALSPEC_CODEX_VERSION` env var (default `latest`) |
| Provision | `apt-get install nodejs npm && npm i -g @openai/codex@${EVALSPEC_CODEX_VERSION}`, then assert `/usr/local/bin/codex` exists |
| Credentials | `CODEX_API_KEY` (preferred), `CODEX_ACCESS_TOKEN`, or `CODEX_AUTH_JSON_PATH` pointing at a Codex `auth.json` from `codex login` |
| Secret scope | `api.openai.com` for `CODEX_API_KEY`; `chatgpt.com` and `auth.openai.com` for `CODEX_ACCESS_TOKEN`; `CODEX_AUTH_JSON_PATH` is mounted read-only and copied inside the sandbox as `/root/.codex/auth.json` |
| Guest env | `HOME=/root CODEX_HOME=/root/.codex TZ=UTC EVALSPEC_CODEX_VERSION=<pinned>` |
| Cell env | `guest_env` + `EVALSPEC_ARM` / `EVALSPEC_MODEL` / `EVALSPEC_HARNESS=codex` / `EVALSPEC_SET` |
| Skills bridge | `skill_load_dir=/root/.codex/skills` symlinked to `/home/evalspec/skills` once at provision |
| Output-eval install | per-cell `setup.sh` writing into `/home/evalspec/skills` (the bridge target); no plugin marketplace install |
| Harness args | Inserted before the trailing prompt positional. Reserved: model, cwd, JSON/output controls, config/profile controls, sandbox/approval controls, `resume`, `review`, and prompt-position controls. |
| Project assets (`__route__` path) | merges `skills`, `.agents/skills`, and `.claude/skills` into `/root/.codex/skills` |
| Detect dispatch | Codex JSONL item skill-invocation shapes, plus `Skill` tool-call and namespaced-tool fallbacks |

`build_command` accepts `plugin_dir`, `resume_session_id`, `effort`, and `detect_skill` for protocol parity, but only model, workdir, harness args, and prompt affect the current command. `capabilities.multi_turn=False` until Codex resume semantics are verified inside the eval sandbox. Use `CODEX_AUTH_JSON_PATH` when you want Codex runs to use an existing ChatGPT/Codex subscription login instead of an API-key billing path.

## Adding another agent

1. `agents/<name>.py` — implement `CodingAgent`, including `skill_load_dir`, `bridge_skills_home_script()` (symlink it to `FIXED_SKILLS_HOME`), and `cell_env()` (`guest_env` + `EVALSPEC_*`). Reuse `runner.parse_run_json` / `parse_stream_run` if the CLI's output resembles Claude's; otherwise write a parser returning a `RunResult`.
2. Add `from_env()` + `credential_error()` classmethods.
3. Register in `_REGISTRY` in `agents/__init__.py`.
4. `tests/agents/test_<name>.py` — mirror `test_opencode.py`: command building, `harness_args` pass-through and reserved-flag rejection, JSONL parsing, secrets, credential preflight, provision, stage, plus the bridge/cell-env/load-dir members (`test_*_bridge_script_symlinks_fixed_home`, `test_*_cell_env_carries_evalspec_vars`, `test_*_skill_load_dir`). Use `evalspec.testing.FakeSandbox` (no real VM).
5. `make test` + `make lint`, then `EVALSPEC_AGENT=<name> make evals:build` and `EVALSPEC_AGENT=<name> make evals -k <one-fast-skill>` to prove end-to-end boot and run.
6. Document it here under a worked-example table.

## What you do NOT touch

Sandbox lifecycle, bind mounts, snapshot cache + file lock, judge call site, the skills-home bridge + per-cell `setup.sh` install path, the grading/binder path — all agent-agnostic. If a new agent tempts you to change one, the seam is wrong; push the difference behind a protocol member.

The host's `base_image` and `environment_script` (`[tool.evalspec]`) are applied by the **build layer**, not the agent: the build chooses the base image and runs the environment script after `provision()` and before the snapshot is sealed. Both participate in the snapshot cache identity (the name carries a content hash of the resolved config), so changing either auto-rebuilds. The `CodingAgent` boundary is unchanged — an agent still only declares `provision()`; it never sees or composes the host environment.
