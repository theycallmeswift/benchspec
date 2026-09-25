# Harnesses

This page covers the three agent CLIs benchspec can drive, what differs between
them (models, effort, credentials, install, skill directories), how the judge
reuses the same adapters, and how to add a harness of your own. It is for anyone
configuring a multi-harness set or integrating a new CLI; terms (harness, arm,
judge, snapshot) are defined in [concepts.md](concepts.md).

A **harness** is the agent CLI an arm drives: the thing under test.
benchspec keeps everything harness-specific (how to install the CLI into a
sandbox, which credentials it needs, how to build its headless command, how to
parse its stream) behind one interface, the `CodingAgent` protocol, so the
sandbox lifecycle, grading, and reporting never name a concrete agent. Three
harnesses ship in-tree; adding another is one adapter file plus a registry entry.

Arms pick their harness individually, so one set can put harnesses side by side
in the same matrix:

```toml
[tool.benchspec.sets.popular-harnesses]
effort   = "medium"
baseline = "claude-code"
arms = [
  { name = "claude-code", harness = "claude-code", model = "sonnet" },
  { name = "codex",       harness = "codex",       model = "gpt-5.4" },
  { name = "opencode",    harness = "opencode",    model = "anthropic/claude-sonnet-4-6" },
]
```

> **Edge case:** cross-harness pass rates are an integration signal, not a
> leaderboard. Harnesses differ in system prompts, tool sets, and default
> behavior, more than just the model, so read cross-harness columns as "does my
> suite hold up here", not as a ranking.

## The in-tree harnesses

| | `claude-code` | `codex` | `opencode` |
|---|---|---|---|
| CLI | Claude Code | OpenAI Codex CLI | [sst/opencode](https://github.com/sst/opencode) |
| Model names | Aliases: `sonnet`, `opus`, `haiku` | What `codex exec -m` accepts (e.g. `gpt-5.4`) | Provider-qualified: `anthropic/claude-sonnet-4-6` |
| How effort is passed | `--effort <value>`, unvalidated | `-c model_reasoning_effort=<value>`, unvalidated | `--variant`: `low` → `fast`, `medium` → `default`, `high` → `thorough` (anything else → `default`) |
| Token split (input/output) | yes | yes | yes; reasoning counts as output |
| Credentials (`provider = "default"`) | `CLAUDE_CODE_OAUTH_TOKEN` (preferred; from `claude setup-token`) or `ANTHROPIC_API_KEY` | `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, `OPENAI_API_KEY`, or `CODEX_AUTH_JSON_PATH` (a `codex login` auth.json, mounted into the guest), in that order | `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_GENERATIVE_AI_API_KEY`, in that order |
| `provider = "openrouter"` | `OPENROUTER_API_KEY`, injected as `ANTHROPIC_AUTH_TOKEN` with `ANTHROPIC_BASE_URL=https://openrouter.ai/api` and an empty `ANTHROPIC_API_KEY`; model is a vendor slug (`anthropic/claude-sonnet-4.6`) | `OPENROUTER_API_KEY`, with benchspec-owned `-c model_provider="openrouter"` and `model_providers.openrouter.*` overrides on every invocation; model is a vendor slug (`openai/gpt-5.5`) | `OPENROUTER_API_KEY` only; model is `openrouter/<vendor>/<model>` |
| Version pin | `BENCHSPEC_CLAUDE_VERSION` | `BENCHSPEC_CODEX_VERSION` | `BENCHSPEC_OPENCODE_VERSION`, then `[tool.benchspec] opencode_version` |
| Guest install | `curl -fsSL https://claude.ai/install.sh \| bash` | `npm i -g @openai/codex@<version>` | `npm i -g opencode-ai@<version>` |
| Skill directory (linked to `/home/benchspec/skills`) | `/root/.claude/skills` | `/root/.codex/skills` | `/root/.config/opencode/skills` |

Notes that matter in practice:

- **Effort is trust-and-record.** benchspec passes the arm's `effort` to the
  harness in the form above and does not pre-validate the value; an unsupported
  one surfaces as a loud error from the CLI, not a silent downgrade. The Claude
  Code installer always fetches the latest release, so `BENCHSPEC_CLAUDE_VERSION`
  changes the snapshot name without pinning the binary; the version that actually
  ran is recorded in `meta.json`.
- **`history:` works on every harness.** History renders into the prompt as a
  transcript block rather than relying on session resumption, so multi-turn
  context does not depend on the CLI.
- **Credentials ride differently per backend.** microsandbox injects each
  credential at the sandbox's network boundary, scoped to the provider's hosts
  and never readable in the guest; Docker injects it as a plain container
  environment variable the agent and `setup.sh` can read (see
  [`sandbox.md`](sandbox.md#credentials)). Either way, `CODEX_AUTH_JSON_PATH` is
  a read-only mount of the `auth.json`, not an env var. Three renames, to the
  names the CLIs actually read: OpenCode injects a host `GEMINI_API_KEY` as
  `GOOGLE_GENERATIVE_AI_API_KEY`; Codex, as arm or judge, injects
  `OPENAI_API_KEY` as `CODEX_API_KEY`; Claude Code under `provider = "openrouter"`
  injects `OPENROUTER_API_KEY` as `ANTHROPIC_AUTH_TOKEN`.
- **`provider` is per arm and per judge.** `openrouter` changes only what happens
  at execution time (the credential, the guest env, CLI flags), never the
  snapshot, so mixing providers in one set builds nothing extra. Under
  `openrouter` no CLI login is consulted: `claude auth status` and `codex login
  status` are skipped, and only `OPENROUTER_API_KEY` counts. See
  [`configuration.md`](configuration.md#providers).
- **Every harness runs with its own approvals bypassed.** Claude Code gets
  `--permission-mode bypassPermissions`, Codex
  `--dangerously-bypass-approvals-and-sandbox`, and OpenCode a
  `permission = "allow"` in the guest `opencode.json` the adapter bakes into the
  snapshot. A headless run cannot answer a permission prompt, so one would end
  the turn with nothing written; the sandbox, not harness approvals, is the
  boundary (see [`sandbox.md`](sandbox.md)).
- **`harness_args` are pass-through with a reserved list.** Each adapter appends
  your tokens to its invocation but rejects flags benchspec owns (model, effort,
  prompt delivery, output format, session, and permission controls) including
  their aliases and `--flag=value` forms. The canonical use is opting a Claude
  Code arm into the repo's plugin surface: `harness_args = ["--plugin-dir", "/project"]`.

## The judge uses the same adapters

Grading reuses the harness adapters in a different execution environment: a task
arm runs its harness *inside* the sandbox, while the judge runs its harness as a
fresh process *on the host*, with the host's own credentials. That is why any of
the three harnesses can judge (configure `[tool.benchspec.judge]`; see
[`configuration.md`](configuration.md#the-judge)), why the judge CLI must be
installed on the host, and why an `opencode` judge needs a provider-qualified
model. The judge's `provider` is chosen independently of the arms', so it can
grade through OpenRouter while the arms run natively, or the reverse. The binder
is not a harness call at all: it is a direct API call to a fixed Gemini model,
through Gemini's own API or OpenRouter by `[tool.benchspec.binder] provider`,
unaffected by either the task or judge harness.

## Custom harnesses

A custom harness is used exactly like a built-in one: register it under a name,
and that name becomes legal everywhere a `harness` appears — an arm, a set-level
default, or the judge:

```toml
[tool.benchspec.sets.mine]
baseline = "claude"
arms = [
  { name = "claude", harness = "claude-code", model = "sonnet" },
  { name = "mine",   harness = "my-agent",    model = "my-model-name" },
]
```

There is no plugin entry point yet, so an adapter lives in-tree: a custom
harness today means a fork of benchspec (and, ideally, a pull request — an
adapter for a real agent CLI is very welcome).

The `CodingAgent` protocol (`src/benchspec/agents/base.py`) is the whole
integration surface. An adapter declares:

- **Identity and layout**: `id` (the registry name, snapshot-cache key, and
  report label), `guest_home`, `skill_load_dir`, `agent_bin`, and a typed
  `AgentCapabilities` (`multi_turn`, `token_split`). Effort is not a capability:
  every adapter forwards the configured value and lets the CLI reject what it
  does not accept, the same way model names are handled.
- **Provisioning**: `provision_script()`, the commands that install the CLI in
  the guest. It is hashed into the snapshot fingerprint, so any installer change
  rebuilds the image automatically.
- **Credentials**: `from_env()` / `credential_error()` on the host side,
  `secrets()` for scoped injection into the guest.
- **Invocation**: `build_command(...)` (validate `harness_args` against your
  reserved flags here) and `invoke(...)`, which parses the CLI's stream into a
  `RunResult`, surfacing agent failures as `is_error=True`, not exceptions.
  Given a `watch` (a trigger-only turn), `invoke` hands the command and its
  parser to `BaseAgent.invoke_watched`, which streams it and stops it early.
- **Detection**: `stream_tool_calls` / `streamed_activity`, the stream probes
  behind an early stop. `stream_tool_calls` returns the trajectory tool calls
  one raw output line carries, exactly as the adapter's parser records them for
  the whole stream, so the stop and the grade agree on whether a skill fired. A
  dispatch is whatever the CLI observably does to load a skill: its native
  skill event where it has one, otherwise a read of
  `<skill_load_dir>/<name>/SKILL.md` (the Codex adapter works this way).
- **Judging**: `judge(...)`, the host-side grading entry point.

The steps:

1. Write `src/benchspec/agents/<name>.py` as a `BaseAgent` subclass implementing
   the protocol. `BaseAgent` carries the shared helpers and the class-level
   factory contract the registry calls (`from_env`, `for_host`,
   `credential_error`), so a missing one fails at import rather than mid-run.
   Reuse the existing parsers where the CLI's output resembles one already
   supported.
2. Register the class in `_REGISTRY` in `src/benchspec/agents/__init__.py`; the
   name becomes a legal `harness` value everywhere at once.
3. Test against `benchspec.testing.FakeSandbox`, a recording sandbox double, so
   command construction, secret injection, and stream parsing are unit-tested
   without booting a sandbox. Mirror an existing suite under `tests/agents/`.
4. Verify with `make test` and `make lint`, then prove an end-to-end boot by
   running a real set with an arm on the new harness.

What you do *not* touch: the sandbox lifecycle, mounts, snapshot cache, the
skills-home bridge, the judge call site, and the binder/grading path are all
agent-agnostic. If a new harness seems to require changing one of them, the seam
is in the wrong place; push the difference behind a protocol member instead.

The authoritative adapters are `benchspec.agents.claude`,
`benchspec.agents.codex`, and `benchspec.agents.opencode`. If this page
and those modules ever disagree, the modules are right.
