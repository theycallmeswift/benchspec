# One OpenRouter key for the binder, the judge, and the harness

Research, 2026-09-09. What it would take for a single `OPENROUTER_API_KEY` to
be the only credential a graded run needs. No code changes were made.

## Answer up front

Three components, three different distances from "works today":

| Component | Today | Blocker | Size |
|---|---|---|---|
| Harness `opencode` (arm or judge) | Works. `OPENROUTER_API_KEY` is its first-choice credential; model `openrouter/anthropic/claude-sonnet-4.6`. | None | 0 |
| Harness `claude-code` | Half works via the documented `via-openrouter` arm `env`, but only if an Anthropic credential is *also* set. | Preflight demands `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`; doc example has the wrong base URL; key leaks into the guest as plain env. | S |
| Harness `codex` | Does not work. | `-c` is a reserved harness arg; Codex ignores `OPENAI_BASE_URL`; provider must be declared in `~/.codex/config.toml`, which benchspec never writes. | M |
| Judge (any harness) | `opencode` judge works. `claude-code`/`codex` judges fail preflight. | `host_credential_error()` checks `os.environ` and CLI login, never the judge's `env`. | S |
| Binder | Hard-wired to Gemini's native REST API. | Needs an OpenAI-compatible transport plus an OpenRouter preflight. Same Gemini models are on OpenRouter; only the request shape changes. | M |

The binder is the only piece that needs a new transport. Everything else is
credential plumbing and preflight.

## What OpenRouter offers (verified against live docs/API)

- **Anthropic Messages skin**: `POST https://openrouter.ai/api/v1/messages`.
  Claude Code's documented setup is `ANTHROPIC_BASE_URL=https://openrouter.ai/api`
  (no `/v1`; Claude Code appends `/v1/messages`), `ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY`,
  and `ANTHROPIC_API_KEY=""` explicitly empty. Prompt caching, thinking, tools,
  and streaming pass through. OpenRouter only guarantees Anthropic-provider
  routing; pin it. Model aliases (`sonnet`) resolve client-side to Anthropic
  IDs; whether the skin maps bare IDs is undocumented, so set
  `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` to `anthropic/...` slugs or pass
  full slugs as `--model`.
  Sources: <https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration>,
  <https://code.claude.com/docs/en/llm-gateway>, <https://code.claude.com/docs/en/authentication>.
- **Responses API**: `POST https://openrouter.ai/api/v1/responses` (stateless;
  `store`/`previous_response_id` rejected). Codex now requires
  `wire_api = "responses"` (chat removed Feb 2026), so this is the only path.
  Codex config must be user-level `~/.codex/config.toml` or `-c` overrides:
  `model_provider="openrouter"`, `model_providers.openrouter.base_url="https://openrouter.ai/api/v1"`,
  `model_providers.openrouter.env_key="OPENROUTER_API_KEY"`. Models must carry the
  vendor prefix (`openai/gpt-5.5`). `codex login status` reports "Not logged in"
  with a custom provider even when requests work. Known intermittent
  `invalid_prompt` schema rejections on `codex exec` (openai/codex #12114, #18228).
  Sources: <https://openrouter.ai/docs/cookbook/coding-agents/codex-cli>,
  <https://learn.chatgpt.com/docs/config-file/config-reference>,
  <https://openrouter.ai/docs/api_reference/responses/overview>.
- **Chat Completions** for the binder: `POST https://openrouter.ai/api/v1/chat/completions`,
  `Authorization: Bearer`, `response_format: {"type": "json_object"}` or
  `json_schema`, `temperature: 0`. `google/gemini-3.5-flash-lite` is listed
  ($0.30/M in, $2.50/M out, same as direct). Gotchas: by default a provider
  that lacks a parameter silently ignores it, so send
  `provider: {"only": ["google-ai-studio"], "allow_fallbacks": false, "require_parameters": true}`
  (the `google-vertex` endpoints for 3.5-flash-lite do not list `temperature`).
  Errors: 401 bad key, 402 no credits, 403 is *moderation* not auth, 429 rate
  limit; a provider failure can arrive inside a 200 as `choices[0].error` with
  `finish_reason: "error"`. Preflight: `GET https://openrouter.ai/api/v1/key`
  returns label, limit, and remaining credit.
  Sources: <https://openrouter.ai/docs/guides/routing/provider-selection>,
  <https://openrouter.ai/docs/api_reference/errors-and-debugging>,
  <https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key>.
- **OpenCode**: `OPENROUTER_API_KEY` via models.dev, model
  `openrouter/<slug>`, `--format json` headless. Open issue #41810: the
  per-model `provider` routing object may not be forwarded, so provider
  pinning through OpenCode is unreliable. Issue #31435: in containers the
  JSONL stream can drop the final `text`/`step_finish` events.
  Sources: <https://openrouter.ai/docs/cookbook/coding-agents/opencode-integration>,
  <https://github.com/anomalyco/opencode/issues/41810>.

## Where the code stands

- `src/benchspec/grading/binder.py`: `_call_gemini` posts to
  `generativelanguage.googleapis.com` with `x-goog-api-key`, parses Gemini's
  `candidates`/`usageMetadata`/`promptFeedback` shape, and classifies auth
  failures on 401/403 or a 400 naming `API_KEY_INVALID`. `preflight_verify_gemini_key`
  is called from `orchestration/cases.py` and `reporting/analyze.py`.
  `binder_identity()` (provider/model/api_path) lands in `meta.json`.
  `evals/binder/conftest.py` wraps `_call_gemini`, labels rows `source == "gemini"`,
  and carries Flash-Lite pricing constants. ~15 tests in
  `tests/grading/test_binder.py` exercise the transport.
- `src/benchspec/agents/claude.py`: `from_env()`/`credential_error()` accept
  only `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`; `secrets()` scopes the
  credential to `api.anthropic.com`. Arm `env` merges over `guest_env()` at
  exec time (the "leaky OpenRouter base URL" path), so under microsandbox the
  OpenRouter key rides as a readable env var, not a network-scoped secret.
- `src/benchspec/agents/codex.py`: credential scoped to `api.openai.com`;
  `-c`/`--config` are reserved harness args; no `config.toml` is written in the
  guest (`CODEX_HOME=/root/.codex`).
- `src/benchspec/agents/opencode.py`: already prefers `OPENROUTER_API_KEY`,
  scoped to `openrouter.ai`.
- `src/benchspec/grading/judges/registry.py` + `agents/base.py`:
  `preflight_verify_judge_credential` calls `host_credential_error()`, which
  reads `os.environ` and runs `claude auth status`/`codex login status` with the
  inherited host env. `Host.exec` merges `config.env` over `os.environ` only at
  grading time, so a judge that authenticates purely through `[tool.benchspec.judge].env`
  cannot pass preflight.
- `docs/configuration.md` shows `ANTHROPIC_BASE_URL = "https://openrouter.ai/api/v1"`;
  OpenRouter's cookbook says `https://openrouter.ai/api`. With `/v1` Claude Code
  requests `/api/v1/v1/messages`. Doc bug regardless of this work.
- Out of scope but same key family: `make lint:houserules` needs
  `GEMINI_API_KEY` for a dev-side tool, unrelated to benchspec's runtime.

## Design: `provider` per binder, judge, and arm

Decision (Swift, 2026-09-09): configure the provider separately for each
component rather than one run-level switch. Same Gemini models are on
OpenRouter; the binder only has to name the model in the request.

### Config shape

```toml
[tool.benchspec.binder]
provider = "openrouter"                     # default "gemini"
model    = "google/gemini-3.5-flash-lite"   # optional; each provider has a default

[tool.benchspec.judge]
harness  = "codex"
provider = "openrouter"                     # default "default"
model    = "openai/gpt-5.5"

[tool.benchspec.sets.e2e]
harness  = "claude-code"
provider = "openrouter"                     # set-level default, arm override
model    = "anthropic/claude-sonnet-4.6"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "direct", provider = "default", model = "sonnet" },   # mixed-vendor sets are fine
]
```

Provider values: `default` (the vendor's own API or CLI login, today's
behavior) or `openrouter` for judge and arms; `gemini` or `openrouter` for the
binder. Model strings are passed through unvalidated, as today; under
`openrouter` an unqualified model (no `/`) is a config error, mirroring the
existing OpenCode check.

Single-key runs set all three to `openrouter` and export only
`OPENROUTER_API_KEY`. Nothing forces that: a run can judge natively and bind
through OpenRouter, or the reverse.

### Wiring per component

All OpenRouter wiring happens at exec time (guest env, per-cell secret, CLI
flags). Snapshots stay provider-neutral: the fingerprint covers installer
inputs only, and `make_agent` already runs per arm, so a per-arm `provider`
adds no snapshot builds.

**Binder** (`grading/binder.py`, new `BinderConfig` resolved like `JudgeConfig`
from pyproject > `--config` scratch > CLI):

- `_call_openrouter`: `POST https://openrouter.ai/api/v1/chat/completions`,
  `Authorization: Bearer $OPENROUTER_API_KEY`, `temperature: 0`,
  `response_format: {"type": "json_object"}`, `model` from config, and
  `provider: {"require_parameters": true}` so a route that would drop
  `temperature` is refused rather than silently used. Reply → the existing
  `GeminiReply` shape (rename to `BinderReply`): `choices[0].message.content`,
  `usage.prompt_tokens`, `usage.completion_tokens`.
- Failure taxonomy: 401 and 402 → `BinderAuthError` (run stops; never degrade
  to paid judge grading). 403 is moderation on OpenRouter, 429, 5xx, and a 200
  carrying `choices[0].error` or `finish_reason == "error"` → `RuntimeError`.
- `preflight_verify_binder_key(config)` picks the env var by provider;
  optionally `GET /api/v1/key` to fail on a dead key before any arm runs.
- `binder_identity()` becomes config-driven: `{provider, model, api_path}`.
- `bind()` takes the transport from config; `call_model` injection stays.
- Corpus (`evals/binder/conftest.py`): choose the transport by the same
  config, keep `source` as the model label rather than `"gemini"`, pricing by
  provider.

**Claude Code adapter** (`agents/claude.py`), `provider == "openrouter"`:

- `secrets()` → `Credential("ANTHROPIC_AUTH_TOKEN", key, ("openrouter.ai",))`.
  microsandbox then scopes the key to OpenRouter at the network boundary
  instead of it riding as a readable arm env var.
- `guest_env()` adds `ANTHROPIC_BASE_URL=https://openrouter.ai/api` and
  `ANTHROPIC_API_KEY=""` (must be explicitly empty per OpenRouter's cookbook).
- `credential_error()` / `host_credential_error()` accept `OPENROUTER_API_KEY`
  and skip the `claude auth status` probe.
- Judge: `judge()` merges the same two env vars plus the token over
  `config.env`. `-p` mode always uses an env credential when present.
- Model: pass the slug through (`anthropic/claude-sonnet-4.6`). Aliases like
  `sonnet` resolve client-side to Anthropic IDs and OpenRouter does not
  document mapping them, so reject unqualified models under `openrouter`.

**Codex adapter** (`agents/codex.py`), `provider == "openrouter"`:

- `build_command()` and `judge()` add `-c` overrides, no config file written
  anywhere:
  `-c model_provider="openrouter"`,
  `-c model_providers.openrouter.name="OpenRouter"`,
  `-c model_providers.openrouter.base_url="https://openrouter.ai/api/v1"`,
  `-c model_providers.openrouter.env_key="OPENROUTER_API_KEY"`,
  `-c model_providers.openrouter.wire_api="responses"`.
  `-c` stays reserved for user `harness_args`; benchspec owns these tokens.
- `secrets()` → `Credential("OPENROUTER_API_KEY", key, ("openrouter.ai",))`.
- Credential checks accept `OPENROUTER_API_KEY` and skip `codex login status`
  (it reports "Not logged in" by design with a custom provider).
- Model: vendor-prefixed slug required (`openai/gpt-5.5`).

**OpenCode adapter** (`agents/opencode.py`), `provider == "openrouter"`:

- Force the credential to `OPENROUTER_API_KEY` rather than "first of four
  set"; require the `openrouter/` model prefix. `default` keeps today's
  fallback chain.

**Config and preflight plumbing**:

- `config/arms.py`: `Arm.provider`, `provider` in `_SET_DEFAULT_KEYS`,
  validated against `{"default", "openrouter"}`.
- `grading/judges/config.py`: `JudgeConfig.provider`, same validation, plus
  the unqualified-model check under `openrouter`.
- `agents/__init__.py`: `make_agent(harness, provider=...)`,
  `agent_class(harness).for_host(provider=...)`,
  `credential_preflight_error(harness, provider)`. `sandbox.preflight_errors`
  dedupes on `(harness, provider)` so a mixed set reports each credential once.
- `grading/judges/registry.py`: `preflight_verify_judge_credential` evaluates
  against `{**os.environ, **config.env}` so a judge authenticated through its
  own `env` table can pass.
- `reporting/manifest.py` and `report.planned_arms`: record `provider` on the
  binder, the judge, and each arm in `meta.json`.

**Docs**: README "What you need" and the credential table, `configuration.md`
(binder table, judge and arm `provider` keys, env table, and the
`https://openrouter.ai/api` base-URL fix), `harnesses.md` credential row,
`sandbox.md` credentials, quickstart.

### Order of work

1. Config surface + preflight plumbing (arms, judge, binder config, factory,
   manifest). Pure refactor with `default`/`gemini` defaults; no behavior
   change.
2. Binder OpenRouter transport + tests + corpus wiring.
3. Claude Code adapter + judge path.
4. Codex adapter + judge path.
5. OpenCode tightening.
6. Docs, then an e2e run with all three set to `openrouter` and only
   `OPENROUTER_API_KEY` exported.

Rough size: about three days. Binder one day, Codex one day, Claude and
plumbing half a day each, docs and e2e half a day.

## Risks

- **Codex on OpenRouter** has intermittent Responses-schema rejections on
  `codex exec` (openai/codex #12114, #18228). The arm error taxonomy already
  records these as infra, not misses, but expect some retries.
- **Claude Code on non-Anthropic models** is unsupported by both vendors;
  OpenRouter guarantees the Anthropic skin only for Anthropic-provider
  routing. An API-key gateway path is within Anthropic's ToS; OAuth
  subscription tokens through a gateway are not.
- **Routing**: `require_parameters` is what keeps `temperature: 0` honest for
  the binder. Without it OpenRouter silently drops unsupported parameters on
  fallback endpoints.
- **Judge vendor independence** is unchanged: one key does not remove the
  reason the in-repo suite judges Claude arms with Codex. Pick the judge slug
  from a different vendor than the arms.
- **Cost**: provider rate plus OpenRouter's 5.5% credit fee.
