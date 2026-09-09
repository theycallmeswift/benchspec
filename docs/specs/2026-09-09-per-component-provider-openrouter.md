Tracked in #101 (sub-issues #102–#107). Follow-through on the research in `docs/research/2026-09-09-openrouter-unified-provider.md`. Builds on the backend-neutral `Credential` seam from #75 and the preflight consolidation in PR #97. Related: #95 (Codex accepts `OPENAI_API_KEY`), #94 (judge host-credential preflight).

**TL;DR** — Add a `provider` key to the binder, the judge, and each arm (`default` or `openrouter`; `gemini` or `openrouter` for the binder), so any combination of the three can run through OpenRouter and a run that sets all three needs exactly one credential, `OPENROUTER_API_KEY`.

## Problem

- **Symptom:** a graded run needs up to three vendor credentials. `GEMINI_API_KEY` for the binder (`src/benchspec/grading/binder.py:297`), the arm harness's own key (`src/benchspec/agents/claude.py:28`, `agents/codex.py:23`, `agents/opencode.py:39`), and the judge harness's host credential or CLI login (`src/benchspec/grading/judges/registry.py:51`). README line 38 and `docs/configuration.md:240` document that as the price of entry.
- **Symptom:** the binder is hard-wired to one transport. `_call_gemini` (`binder.py:170`) posts to `generativelanguage.googleapis.com` with `x-goog-api-key`, parses Gemini's `candidates`/`usageMetadata`/`promptFeedback` shape, and classifies auth failures on 401/403 or a 400 naming `API_KEY_INVALID` (`binder.py:23`). `binder_identity()` (`binder.py:312`) reports `provider: "gemini"` unconditionally.
- **Symptom:** the documented OpenRouter escape hatch for Claude Code arms (`docs/configuration.md:69-71`) is wrong and leaky. It sets `ANTHROPIC_BASE_URL = "https://openrouter.ai/api/v1"`; Claude Code appends `/v1/messages`, so requests hit `/api/v1/v1/messages`. OpenRouter's cookbook uses `https://openrouter.ai/api`. And the key rides as a plain arm env var merged over `guest_env()` (`agents/claude.py:335`), so under microsandbox it is guest-readable rather than a network-scoped secret.
- **Symptom:** a Claude Code arm cannot run on OpenRouter alone. `credential_error()` (`agents/claude.py:150`) accepts only `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, so preflight fails without an Anthropic credential even when the arm env carries the OpenRouter token.
- **Symptom:** a Codex arm cannot reach OpenRouter at all. Codex needs `model_provider` plus a `[model_providers.<id>]` table in user-level config or `-c` overrides; `-c`/`--config` are reserved harness args (`agents/codex.py:50-51`), Codex ignores `OPENAI_BASE_URL` (openai/codex #16719), and benchspec never writes a `config.toml` into `CODEX_HOME` (`agents/codex.py:206`).
- **Symptom:** a Claude Code or Codex judge cannot authenticate through its own `env`. `Host.exec` merges `config.env` over `os.environ` at grading time (`src/benchspec/orchestration/environments.py:93`), but `preflight_verify_judge_credential` (`registry.py:51`) calls `host_credential_error()`, which reads `os.environ` and runs `claude auth status` / `codex login status` via `host_probe` with the inherited host env (`src/benchspec/agents/base.py:189`). `codex login status` reports "Not logged in" by design with a custom provider.
- **Why it stayed hidden:** the OpenCode adapter already prefers `OPENROUTER_API_KEY` (`agents/opencode.py:39-46`), so an all-OpenCode run on OpenRouter works today and nobody hit the gaps in the other two adapters, the judge preflight, or the binder.
- **Constraint:** snapshots stay provider-neutral. The fingerprint folds installer inputs and the environment script (`src/benchspec/sandbox/backend.py:279`), not runtime env or secrets, and `make_agent` already runs per arm (`src/benchspec/orchestration/execution.py:420`). Every OpenRouter change must land at exec time (guest env, per-cell secret, CLI flags) so a per-arm `provider` adds no snapshot builds.
- **Constraint:** `-c` stays reserved for users. Benchspec-owned Codex overrides are appended by the adapter, never accepted through `harness_args`.
- **Constraint:** the binder failure taxonomy is total. A rejected credential must raise `BinderAuthError` (`binder.py:147`) so `execution.py` never degrades every assertion to paid judge grading behind a green run; every other failure is a `RuntimeError`.
- **Constraint:** OpenRouter silently drops parameters a routed endpoint does not support unless the request sets `provider.require_parameters`. The binder's `temperature: 0` and JSON mode must be enforced, not hoped for.
- **Scope:** the `provider` key on three config surfaces, one new binder transport, OpenRouter wiring in the three adapters' guest and judge paths, preflight that understands the key, `meta.json` recording it, and docs. No change to the `default` path's behavior.

## Solution

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
  { name = "direct", provider = "default", model = "sonnet" },   # mixed sets are fine
]
```

```text
$ OPENROUTER_API_KEY=sk-or-... benchspec run --set e2e     # the only credential the run reads
$ benchspec run --set e2e                                   # preflight names the one missing key, exit 2
```

Same evals, arms, matrix, and exit codes. `provider` is recorded on the binder, the judge, and every arm in `meta.json`.

## User Stories

1. As a first-time user, I want **one key to cover binder, judge, and harness**, so getting to a first graded run does not mean signing up with three vendors.
2. As a benchmark maintainer, I want **provider chosen per component**, so I can judge natively and bind through OpenRouter, or the reverse, without a global switch forcing all three.
3. As an eval operator on microsandbox, I want **the OpenRouter key scoped to `openrouter.ai` at the network boundary**, not readable by the agent as an arm env var.
4. As a results consumer, I want **`meta.json` to say which provider each component used**, so a run through OpenRouter is distinguishable from a direct one.
5. As a Codex user, I want **OpenRouter to work without touching my `~/.codex/config.toml`**, so the judge on my host and the arm in the guest both route by benchspec's own flags.

## Implementation Decisions

```text
[tool.benchspec.binder]  ──► BinderConfig(provider, model)      (new: grading/binder_config.py or in binder.py)
                                │ gemini     ──► _call_gemini      (unchanged)
                                │ openrouter ──► _call_openrouter  (new: chat/completions, JSON mode, temp 0)
                                ▼
                         bind(text, config) ──► BinderReply ──► _parse_binding

[tool.benchspec.judge].provider ──► JudgeConfig.provider ──► agent_class(h).for_host(provider)
[tool.benchspec.sets.<s>].provider / arm.provider ──► Arm.provider ──► make_agent(h, provider)
                                │
                                ▼
  adapter(provider="openrouter")
    secrets()      Credential(<token env>, $OPENROUTER_API_KEY, ("openrouter.ai",))
    guest_env()    claude: ANTHROPIC_BASE_URL=https://openrouter.ai/api, ANTHROPIC_API_KEY=""
    build_command  codex: -c model_provider=openrouter -c model_providers.openrouter.{name,base_url,env_key,wire_api}
    judge()        same env / flags merged over config.env
    credential_error / host_credential_error   OPENROUTER_API_KEY present; no CLI login probe
```

- **`provider` is a config key, not an environment sniff.** Which transport ran must be visible in `pyproject.toml` and `meta.json`. Auto-detecting from whichever key is set was rejected: it makes the run's transport implicit, which is the wrong trade for a benchmark whose manifest is supposed to say what ran.
- **Values.** Judge and arms: `default` (today's behavior, the vendor's own API or CLI login) or `openrouter`. Binder: `gemini` or `openrouter`. Validation is structural at config-read time, raising `SchemaError` naming the bad value, in `parse_sets` (`config/arms.py:91`), `_FIELD_VALIDATORS` (`judges/config.py:59`), and the new binder resolver.
- **Unqualified models are a config error under `openrouter`.** OpenRouter slugs carry a vendor prefix (`anthropic/...`, `openai/...`, `google/...`). Claude Code resolves `sonnet` client-side to an Anthropic ID and OpenRouter does not document mapping bare IDs on its Messages skin, so `sonnet` behind OpenRouter is a guess. Reuse the existing `"/" not in model` check (`judges/config.py:129`, `agents/opencode.py:339`) for every harness when `provider == "openrouter"`.
- **Binder transport.** `_call_openrouter` posts to `https://openrouter.ai/api/v1/chat/completions` with `Authorization: Bearer $OPENROUTER_API_KEY`, `temperature: 0`, `response_format: {"type": "json_object"}`, the configured `model`, and `provider: {"require_parameters": true}` so a route that would drop `temperature` or JSON mode is refused (HTTP 503) rather than silently used. Reply parsing reads `choices[0].message.content`, `usage.prompt_tokens`, `usage.completion_tokens` into the existing reply dataclass, renamed `BinderReply` (`binder.py:158`). Default model `google/gemini-3.5-flash-lite`, the same upstream model as `GEMINI_BINDER_MODEL` (`binder.py:18`).
- **Binder failure taxonomy per provider.** OpenRouter: 401 (bad key) and 402 (no credits) raise `BinderAuthError`; 403 is moderation there, not auth, so it and 429, 5xx, transport errors, and a 200 whose `choices[0].error` is set or whose `finish_reason == "error"` raise `RuntimeError`. Gemini keeps `_AUTH_HTTP_CODES` (`binder.py:23`) unchanged.
- **Binder preflight by provider.** `preflight_verify_gemini_key` (`binder.py:297`) becomes `preflight_verify_binder_key(config)`, checking `GEMINI_API_KEY` or `OPENROUTER_API_KEY`, called from `cases.py:109` and `analyze.py:91`. Optionally follow with `GET https://openrouter.ai/api/v1/key` to fail on a dead key before any arm runs. `binder_identity()` (`binder.py:312`) takes the config and reports `{provider, model, api_path}`.
- **Binder config resolution mirrors the judge.** pyproject `[tool.benchspec.binder]` > `--config` scratch > CLI, per field, same `SchemaError`-at-collection contract as `resolve_judge_config` (`judges/config.py:136`). The corpus suite (`evals/binder/conftest.py:121-125`) selects the transport by the same config and labels rows by the resolved model instead of the literal `"gemini"`; pricing constants (`conftest.py:133-134`) become per-provider.
- **Claude Code under `openrouter`.** `secrets()` (`agents/claude.py:195`) yields `Credential("ANTHROPIC_AUTH_TOKEN", key, ("openrouter.ai",))`; `guest_env()` (`agents/claude.py:183`) adds `ANTHROPIC_BASE_URL=https://openrouter.ai/api` and `ANTHROPIC_API_KEY=""` (OpenRouter's cookbook requires the empty override so Claude Code cannot fall back to a direct-Anthropic credential). `from_env` (`:140`) reads `OPENROUTER_API_KEY`; `credential_error` (`:150`) and `host_credential_error` (`:159`) accept it and skip `claude auth status`. `judge()` (`:257`) merges the same two env vars plus the token under `config.env`, so a user `env` still wins. `-p` mode always uses an env credential when present.
- **Codex under `openrouter`.** `build_command()` (`agents/codex.py:224`) and `judge()` (`:387`) append benchspec-owned overrides: `-c model_provider="openrouter"`, `-c model_providers.openrouter.name="OpenRouter"`, `-c model_providers.openrouter.base_url="https://openrouter.ai/api/v1"`, `-c model_providers.openrouter.env_key="OPENROUTER_API_KEY"`, `-c model_providers.openrouter.wire_api="responses"`. No config file is written in the guest or on the host, and `-c` stays in `_RESERVED_HARNESS_ARGS` for user tokens. `secrets()` (`:215`) yields `Credential("OPENROUTER_API_KEY", key, ("openrouter.ai",))`. Credential checks accept the key and skip `codex login status`.
- **OpenCode under `openrouter`.** `from_env` (`agents/opencode.py:259`) picks `OPENROUTER_API_KEY` deterministically instead of "first of four set", and the model must carry the `openrouter/` prefix. `default` keeps today's fallback chain untouched.
- **Factory and preflight carry the pair.** `make_agent(harness, provider=)` (`agents/__init__.py:89`), `agent_class(h).for_host(provider=)`, and `credential_preflight_error(harness, provider)` (`:102`). `sandbox.preflight_errors` (`sandbox/sandbox.py:68`) dedupes on `(harness, provider)` so a mixed set reports each missing credential once. `preflight_verify_judge_credential` (`registry.py:51`) evaluates against `{**os.environ, **config.env}`, matching what `Host.exec` gives the judge (`environments.py:93`).
- **Manifest.** `judge_meta` (`reporting/manifest.py:70`), `planned_arms` (`reporting/report.py:43`), and the binder entry (`manifest.py:119`) each carry `provider`. `redact_env` (`report.py:35`) already masks `*_KEY`/`*_TOKEN`, so `ANTHROPIC_AUTH_TOKEN` in a judge env is masked with no change.
- **Fix the documented base URL.** `docs/configuration.md:69-71` changes to `https://openrouter.ai/api` and is reframed as the pre-`provider` escape hatch, or replaced by the `provider` example.

## Testing Plan

### Logic
- **Config parsing** — `provider` on set, arm, judge, and binder tables parses, inherits set to arm, defaults to `default`/`gemini`, and rejects an unknown value with a `SchemaError` naming it. An unqualified model under `openrouter` is a `SchemaError` for every harness.
- **Binder OpenRouter transport** — mirrors the `_call_gemini` cases in `tests/grading/test_binder.py:279-435`: request headers, body (model, temperature, JSON mode, `require_parameters`), reply parsing, 401/402 to `BinderAuthError`, 403/429/5xx/transport/malformed JSON to `RuntimeError`, and a 200 carrying `choices[0].error` to `RuntimeError`.
- **Binder preflight and identity** — missing or empty `OPENROUTER_API_KEY` under `openrouter` raises with a remediation naming it; `binder_identity` reports the configured provider and model.
- **Adapter wiring** — for each adapter under `openrouter`: `secrets()` scopes to `openrouter.ai` under the right env name, `guest_env()`/`build_command()` carry the base URL or `-c` overrides, `credential_error()` accepts `OPENROUTER_API_KEY` alone, and `default` output is byte-identical to today's.
- **Judge path** — `judge()` for each adapter under `openrouter` renders the same env or flags; user `config.env` still wins; `preflight_verify_judge_credential` passes with the key only in `config.env`.
- **Preflight dedupe** — a set with `claude-code/default` and `claude-code/openrouter` arms reports two credential errors, one per pair.

### Behavior
- **Binder corpus through OpenRouter** — `make evals` with `[tool.benchspec.binder] provider = "openrouter"` reaches the same false-positive gate as the Gemini transport; rows record the resolved model.
- **A cell runs end to end on each harness through OpenRouter** — Docker and microsandbox; under microsandbox the guest cannot read the key.
- **`default` is unchanged** — the existing `e2e` set produces the same `meta.json` shape plus `provider: "default"` fields.

### Interface
- **Single-key run** — with only `OPENROUTER_API_KEY` exported and all three providers set to `openrouter`, `benchspec run --set e2e` grades and lands a matrix; with the key unset, preflight exits `2` naming exactly that one variable for the binder, the judge, and each arm.
- **`meta.json`** — `binder.provider`, `judge.provider`, and `arms[].provider` are present and correct.

## Open Questions

- **Alias mapping for Claude Code.** Reject `sonnet`/`opus` under `openrouter` (proposed) or map them through `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` to `anthropic/...` slugs? Rejecting is simpler and the manifest stays honest; revisit if the friction shows up.
- **Routing pins.** Should the binder also pin `provider.only = ["google-ai-studio"]`? `require_parameters` already refuses an endpoint that would drop temperature, which is the property that matters. Leave the pin out unless corpus results differ.
- **Key preflight call.** Is one `GET /api/v1/key` per run worth the network dependency at collection time? Default proposal: yes for `run`, skip for `analyze`.

## Documentation Plan

- **`README.md:38`**: the credential table gains the single-key path; `README.md:217,234` mention the binder key by provider.
- **`docs/configuration.md`**: a `[tool.benchspec.binder]` table; `provider` rows in the judge table (after line 92) and the set/arm keys; the env table (`:240-243`) gains `OPENROUTER_API_KEY` as a first-class credential for all three components; the base-URL fix at `:69-71`.
- **`docs/harnesses.md:43,65`**: the credentials row gains the `openrouter` column per harness and the rename notes gain `ANTHROPIC_AUTH_TOKEN`.
- **`docs/sandbox.md:166-167,229`**: credentials section names the OpenRouter host scope.
- **`docs/quickstart.md:17,34,42,155,255`**, **`docs/concepts.md:52`**, **`docs/writing-evals.md:182,248`**: "the binder needs `GEMINI_API_KEY`" becomes "the binder's credential, by provider".
- **`.env.example:3-21`**: an `OPENROUTER_API_KEY` slot with the single-key note.

## Out of Scope

- Any provider other than OpenRouter. The `provider` key is an enum, not a plugin seam; a third value is a follow-up.
- Alias mapping, provider pinning, and cost accounting for OpenRouter's 5.5% fee.
- The `houserules` lint dependency on `GEMINI_API_KEY` (`Makefile:38`); a dev tool, not benchspec's runtime.
- Changing the in-repo suite's judge vendor. One key does not remove the reason to judge Claude arms with a non-Claude model.

## References

- `docs/research/2026-09-09-openrouter-unified-provider.md` — the research this spec implements, with OpenRouter, Claude Code, Codex, and OpenCode source URLs.
- `src/benchspec/grading/binder.py:18,23,147,158,170,297,312,325` — `GEMINI_BINDER_MODEL`, `_AUTH_HTTP_CODES`, `BinderAuthError`, the reply dataclass, `_call_gemini`, the key preflight, `binder_identity`, and `bind`.
- `src/benchspec/agents/claude.py:28,140,150,159,183,195,224,257,335` — auth env names, `from_env`, both credential checks, `guest_env`, `secrets`, `build_command`, `judge`, and the arm-env merge.
- `src/benchspec/agents/codex.py:23,50-51,161,174,182,206,215,224,387` — provider hosts, the reserved `-c`, `from_env`, both credential checks, `guest_env`, `secrets`, `build_command`, `judge`.
- `src/benchspec/agents/opencode.py:39-46,259,299,321,339,433` — the credential chain, `from_env`, `credential_error`, `secrets`, the qualified-model check, `judge`.
- `src/benchspec/agents/base.py:117,189` — `Credential` and `host_probe`.
- `src/benchspec/agents/__init__.py:75,89,102` — `agent_class`, `make_agent`, `credential_preflight_error`.
- `src/benchspec/config/arms.py:24,35,91,191` — `Arm`, `_SET_DEFAULT_KEYS`, `parse_sets`, `expand_env`.
- `src/benchspec/grading/judges/config.py:59,70,111,129,136` — `_FIELD_VALIDATORS`, `JudgeConfig`, the structural preflight, the OpenCode model check, `resolve_judge_config`.
- `src/benchspec/grading/judges/registry.py:51,64` — `preflight_verify_judge_credential`, `run_judge`.
- `src/benchspec/orchestration/environments.py:93`, `orchestration/cases.py:109,115`, `orchestration/execution.py:420`, `reporting/analyze.py:91` — the host env merge, the grading preflights, per-arm agent creation, the analyze preflight.
- `src/benchspec/sandbox/sandbox.py:68`, `sandbox/backend.py:279`, `sandbox/microsandbox.py:86` — credential preflight, fingerprint inputs, secret scoping.
- `src/benchspec/reporting/manifest.py:70,119`, `reporting/report.py:35,43` — judge and binder meta, `redact_env`, `planned_arms`.
- `evals/binder/conftest.py:107-125,133-134` — the corpus transport wrapper and pricing.
- `tests/grading/test_binder.py:279-435` — the Gemini transport cases the OpenRouter ones mirror.
- OpenRouter: Claude Code cookbook (`ANTHROPIC_BASE_URL=https://openrouter.ai/api`, empty `ANTHROPIC_API_KEY`), Codex cookbook (`model_providers.openrouter`, Responses only), provider routing (`require_parameters`), errors (401/402/403 semantics, `choices[0].error` on 200), `GET /api/v1/key`. URLs in the research doc.

## Verification

- `make test` — proves config parsing, both binder transports and their taxonomies, adapter wiring under both providers, judge paths, preflight dedupe, and manifest fields.
- `make lint` — proves style and types across the new module and edits.
- `OPENROUTER_API_KEY=... uv run benchspec run --set e2e` with all three providers set to `openrouter` in a scratch `--config` — proves a graded matrix from one key; `meta.json` shows `provider: "openrouter"` on binder, judge, and arms.
- `env -u OPENROUTER_API_KEY uv run benchspec run --set e2e` with the same config — exits `2`, naming `OPENROUTER_API_KEY` once each for the binder, the judge, and the harness.
- `make evals` with `[tool.benchspec.binder] provider = "openrouter"` — binder corpus passes its false-positive gate through OpenRouter.

## Done When

- `provider` parses on set, arm, judge, and binder tables with the defaults above, rejects unknown values, and rejects unqualified models under `openrouter`.
- The binder runs through OpenRouter with `temperature: 0`, JSON mode, and `require_parameters`; 401/402 stop the run as `BinderAuthError`; `binder_identity` reports the provider and model.
- Claude Code, Codex, and OpenCode arms run through OpenRouter with the key injected as a `Credential` scoped to `openrouter.ai`, base URL and `-c` overrides applied at exec time, and no snapshot change.
- Claude Code, Codex, and OpenCode judges run through OpenRouter on the host, and judge preflight passes with the key in the host env or the judge's `env`.
- A run with all three set to `openrouter` grades with only `OPENROUTER_API_KEY` exported; preflight names that one variable when it is missing.
- `meta.json` carries `provider` on the binder, the judge, and every arm.
- The `default`/`gemini` paths produce byte-identical commands, env, secrets, and requests to today's.
- README, `configuration.md` (including the base-URL fix), `harnesses.md`, `sandbox.md`, `quickstart.md`, `concepts.md`, `writing-evals.md`, and `.env.example` describe the key and the single-credential path.
