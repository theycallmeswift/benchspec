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
| Binder | Hard-wired to Gemini's native REST API. | Needs an OpenAI-compatible transport plus an OpenRouter preflight. | M |

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

## Options

### A. Run-level `provider = "openrouter"` switch (recommended)

One knob, e.g. `[tool.benchspec] provider = "openrouter"` (or
`BENCHSPEC_PROVIDER`), consumed by the binder, the judge, and every adapter.
`OPENROUTER_API_KEY` becomes the one required credential; the Gemini/Anthropic/
OpenAI keys become unnecessary. Model names are OpenRouter slugs everywhere.

Work:

1. **Binder**: add `_call_openrouter` beside `_call_gemini` (OpenAI chat shape,
   JSON mode, temperature 0, provider pinned to `google-ai-studio`,
   `require_parameters`). Map 401/402 to `BinderAuthError` (a run must stop,
   not degrade to judge grading), 403 to `RuntimeError` (moderation), and
   treat `choices[0].error` on a 200 as a failure. `preflight_verify_binder_key`
   picks the env var by provider and can hit `/api/v1/key`. `binder_identity()`
   reports `provider: "openrouter"`, `model: "google/gemini-3.5-flash-lite"`.
   Corpus conftest: pick the transport by provider, keep the label as the
   upstream model. Mirror the transport tests.
2. **Claude adapter**: under the switch, `secrets()` yields
   `Credential("ANTHROPIC_AUTH_TOKEN", key, ("openrouter.ai",))`, `guest_env()`
   adds `ANTHROPIC_BASE_URL=https://openrouter.ai/api` and `ANTHROPIC_API_KEY=""`,
   `credential_error()` accepts `OPENROUTER_API_KEY`. Pass the arm's `model`
   through unchanged as a slug, or map `sonnet`/`opus`/`haiku` to
   `ANTHROPIC_DEFAULT_*_MODEL`. Judge path: same env in `judge()`.
3. **Codex adapter**: under the switch, write `[model_providers.openrouter]`
   (base_url, `env_key = "OPENROUTER_API_KEY"`, `wire_api = "responses"`) into
   `$CODEX_HOME/config.toml` at provision and add `-c model_provider=openrouter`
   to `build_command()` and `judge()`. Credential scoped to `openrouter.ai`.
   Host judge: write the same table to a benchspec-owned `CODEX_HOME` so the
   user's own config is untouched.
4. **OpenCode adapter**: no transport change. Optionally prefix `openrouter/`
   when the switch is on and the model lacks it.
5. **Judge preflight**: evaluate `host_credential_error()` against
   `{**os.environ, **config.env}` and accept the provider's credential name;
   skip the CLI login probe when the provider is OpenRouter (Codex reports
   "Not logged in" by design there).
6. **Docs**: README "What you need", `docs/configuration.md` env table and the
   base-URL fix, `docs/harnesses.md` credential row, `docs/sandbox.md`
   credentials, quickstart.

Rough size: binder ~1 day, Codex adapter ~1 day, Claude adapter + preflight
~half a day, docs/tests ~half a day.

### B. Per-component knobs, no global switch

Keep the existing `env` escape hatches, and only fix what blocks them:
preflight reading the judge's `env`, `credential_error()` accepting
`ANTHROPIC_AUTH_TOKEN`, a `[tool.benchspec.binder]` table with
`provider`/`model`, and a Codex `config.toml` mechanism. More flexible, more
surface, and the OpenRouter key stays a leaky arm env var under microsandbox
rather than a scoped secret.

### C. Auto-detect from the environment

If only `OPENROUTER_API_KEY` is set, every component routes to OpenRouter.
Least config, but the run's transport becomes implicit, which is the wrong
trade for a benchmark whose `meta.json` is supposed to say exactly what ran.

## Risks to carry into the decision

- **Binder accuracy** was tuned against direct Gemini Flash-Lite. Through
  OpenRouter the model is the same, but the route must be pinned to
  `google-ai-studio` and `require_parameters` set, or `temperature: 0` can be
  silently dropped. Re-run the binder corpus before switching the default.
- **Codex on OpenRouter** has a track record of intermittent Responses-schema
  rejections on `codex exec`. Expect some infra-error cells until upstream
  settles; the arm error taxonomy already records these as infra, not misses.
- **Claude Code on non-Anthropic models** is unsupported by both vendors.
  Anthropic ToS is fine with an API-key gateway path; OAuth subscription
  tokens through a gateway are not permitted.
- **Judge vendor independence**: the repo's own suite judges Claude arms with
  Codex to avoid grading with the graded vendor. One key does not change that;
  pick the judge slug from a different vendor than the arms.
- **Cost**: OpenRouter charges the provider's rate plus a 5.5% credit fee.
