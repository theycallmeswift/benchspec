# Harnesses

This page covers the three agent CLIs harnessbench can drive, what differs between
them (models, effort, credentials, install, skill directories), how the judge
reuses the same adapters, and how to add a harness of your own. It is for anyone
configuring a multi-harness set or integrating a new CLI; terms (harness, arm,
judge, snapshot) are defined in [concepts.md](concepts.md).

A **harness** is the agent CLI an arm drives: the thing under test.
harnessbench keeps everything harness-specific (how to install the CLI into the
sandbox, which credentials it needs, how to build its headless command, how to
parse its stream) behind one interface, the `CodingAgent` protocol, so the
sandbox lifecycle, grading, and reporting never name a concrete agent. Three
harnesses ship in-tree; adding another is one adapter file plus a registry entry.

Arms pick their harness individually, so one set can put harnesses side by side
in the same matrix:

```toml
[tool.harnessbench.sets.popular-harnesses]
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
| Token split (input/output) | yes | yes | no; totals only |
| Credentials | `CLAUDE_CODE_OAUTH_TOKEN` (preferred; from `claude setup-token`) or `ANTHROPIC_API_KEY` | `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, or `CODEX_AUTH_JSON_PATH` (a `codex login` auth.json, mounted into the guest), in that order | `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_GENERATIVE_AI_API_KEY`, in that order |
| Version pin | `HARNESSBENCH_CLAUDE_VERSION` | `HARNESSBENCH_CODEX_VERSION` | `HARNESSBENCH_OPENCODE_VERSION`, then `[tool.harnessbench] opencode_version` |
| Guest install | `curl -fsSL https://claude.ai/install.sh \| bash` | `npm i -g @openai/codex@<version>` | `npm i -g opencode-ai@<version>` |
| Skill directory (linked to `/home/harnessbench/skills`) | `/root/.claude/skills` | `/root/.codex/skills` | `/root/.config/opencode/skills` |

Notes that matter in practice:

- **Effort is trust-and-record.** harnessbench passes the arm's `effort` to the
  harness in the form above and does not pre-validate the value; an unsupported
  one surfaces as a loud error from the CLI, not a silent downgrade. The Claude
  Code installer always fetches the latest release, so `HARNESSBENCH_CLAUDE_VERSION`
  changes the snapshot name without pinning the binary; the version that actually
  ran is recorded in `meta.json`.
- **`history:` works on every harness.** History renders into the prompt as a
  transcript block rather than relying on session resumption, so multi-turn
  context does not depend on the CLI.
- **Credentials are declared once, injected per backend.** Each adapter
  declares its credential and the hosts it may reach. Under `microsandbox` that
  becomes a host-scoped secret at the network boundary — never a readable
  sandbox variable. Under `docker`, with no scoping equivalent, it's a plain
  container variable the sandbox can read. Either way, OpenCode's quirk holds: a
  host `GEMINI_API_KEY` injects under the SDK's expected
  `GOOGLE_GENERATIVE_AI_API_KEY` name.
- **`harness_args` are pass-through with a reserved list.** Each adapter appends
  your tokens to its invocation but rejects flags harnessbench owns (model, effort,
  prompt delivery, output format, session, and permission controls) including
  their aliases and `--flag=value` forms. The canonical use is opting a Claude
  Code arm into the repo's plugin surface: `harness_args = ["--plugin-dir", "/project"]`.

## The judge uses the same adapters

Grading reuses the harness adapters in a different execution environment: a task
arm runs its harness *inside* the sandbox, while the judge runs its harness as a
fresh process *on the host*, with the host's own credentials. That is why any of
the three harnesses can judge (configure `[tool.harnessbench.judge]`; see
[`configuration.md`](configuration.md#the-judge)), why the judge CLI must be
installed on the host, and why an `opencode` judge needs a provider-qualified
model. The binder is not a harness call at all: it is a direct Gemini API call,
unaffected by either the task or judge harness.

## Custom harnesses

A custom harness is used exactly like a built-in one: register it under a name,
and that name becomes legal everywhere a `harness` appears — an arm, a set-level
default, or the judge:

```toml
[tool.harnessbench.sets.mine]
baseline = "claude"
arms = [
  { name = "claude", harness = "claude-code", model = "sonnet" },
  { name = "mine",   harness = "my-agent",    model = "my-model-name" },
]
```

There is no plugin entry point yet, so an adapter lives in-tree: a custom
harness today means a fork of harnessbench (and, ideally, a pull request — an
adapter for a real agent CLI is very welcome).

The `CodingAgent` protocol (`src/harnessbench/agents/base.py`) is the whole
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
- **Detection**: `detect_dispatch` / `detect_fired` / `streamed_activity`, the
  stream probes behind skill-activation grading.
- **Judging**: `judge(...)`, the host-side grading entry point.

The steps:

1. Write `src/harnessbench/agents/<name>.py` implementing the protocol. Reuse the
   existing parsers where the CLI's output resembles one already supported.
2. Register the class in `_REGISTRY` in `src/harnessbench/agents/__init__.py`; the
   name becomes a legal `harness` value everywhere at once.
3. Test against `harnessbench.testing.FakeSandbox`, a recording sandbox double, so
   command construction, secret injection, and stream parsing are unit-tested
   without booting a sandbox. Mirror an existing suite under `tests/agents/`.
4. Verify with `make test` and `make lint`, then prove an end-to-end boot by
   running a real set with an arm on the new harness.

What you do *not* touch: the sandbox lifecycle, mounts, snapshot cache, the
skills-home bridge, the judge call site, and the binder/grading path are all
agent-agnostic. If a new harness seems to require changing one of them, the seam
is in the wrong place; push the difference behind a protocol member instead.

The authoritative adapters are `harnessbench.agents.claude`,
`harnessbench.agents.codex`, and `harnessbench.agents.opencode`. If this page
and those modules ever disagree, the modules are right.
