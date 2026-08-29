**TL;DR** — Move the binder's prose→checker classification off host Claude onto a fixed direct Gemini API call (`gemini-3.1-flash-lite` via `GEMINI_API_KEY`), delete `agents/judge_cli.py`, and relocate the corpus suite to `evals/binder/` with latency and cost reporting — Phase 2 of the #1 roadmap.

## Problem

- **Symptom:** `src/evalspec/binder.py:13` hardcodes `BINDER_MODEL = "haiku"` and routes every classification through `agents/judge_cli.run_host_judge` — a host `claude -p` call — so every graded run requires a host Claude credential and binary even when both the task arms and the configured judge use other harnesses.
- **Symptom:** After Phase 1 (#2, merged via PR #6) moved grading onto per-harness adapters, `agents/judge_cli.py` survives solely as the binder's transport; the PR #6 review thread agreed its deletion is the first Phase-2 follow-up.
- **Symptom:** The corpus suite (`evals/binder_corpus.yaml`, `evals/test_binder_corpus*.py`, `evals/conftest.py`) reports retention, over-punt, and mismatch rates but not latency or cost, and it measures haiku — not the binder model the roadmap fixes.
- **Exposed by:** Roadmap #1 Phase 2 Done-When: fixed Gemini binder model, user-provided Gemini API key, corpus output with false-positive leaks / retention / over-punt / latency / cost, and integration tests proving binder wiring and artifact fields.
- **Scope:** The binder transport and failure taxonomy, the corpus suite location and metrics, and the `judge_cli.py` deletion. Grading-judge behavior and the eval format are untouched. `_BINDING_PROMPT` wording may be tuned to hold the zero-leak gate on the new model; its rule/primitive structure, examples-plus-rules shape, and `{assertion}` contract are preserved.
- **Constraint:** `bind()` keeps returning a checker-spec `dict` or a `None` punt, and degradation to judge grading remains the designed response to *transient* binder failure (`execution.py:161-168`) — but degradation must become visible (counted and warned) and must never swallow a credential failure.
- **Constraint:** No new runtime dependency — project convention (the package depends only on `pytest`, `python-dotenv`, and `pyyaml`); the Gemini call is one REST POST reachable with the stdlib, and the repo already carries a working stdlib reference implementation in `lib/style_lint/gemini.py`.

## Solution

```python
# binder.py — fixed transport, no production config surface
GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"

class BinderAuthError(Exception): ...   # credential rejected — NOT a RuntimeError: never degradable

@dataclass(frozen=True)
class GeminiReply:                      # mirrors lib/style_lint/types.GeminiResponse
    text: str; prompt_tokens: int; output_tokens: int; latency_ms: float

def bind(assertion_text, *, call_model=_call_gemini) -> dict | None: ...
def _call_gemini(prompt, *, timeout=60) -> GeminiReply: ...
    # POST …/v1beta/models/{GEMINI_BINDER_MODEL}:generateContent
    # x-goog-api-key: $GEMINI_API_KEY · temperature 0 · responseMimeType application/json
```

The binder becomes the only Gemini call site in the package, `agents/judge_cli.py` is deleted (its one shared helper moves to the sole remaining consumer), and the corpus suite moves to `evals/binder/` where its session summary gains latency and cost.

## User Stories

1. As a benchmark maintainer, I want **the binder off host Claude**, so a run whose arms and judge use Codex or OpenCode doesn't force a Claude binary and credential into the environment.
2. As an eval runner, I want **one fixed flash-tier binder model**, so assertion classification is identical and cheap across every arm × judge combination in the matrix.
3. As an eval runner, I want **binder infra failures visible, never silent**, so a revoked key or a rate-limit storm can't quietly reroute the whole matrix to expensive judge grading behind a green run.
4. As a binder-prompt maintainer, I want **corpus runs that report latency and cost next to leak/retention rates**, so prompt changes are judged on the quality and spend of the actual production binder model.
5. As a contributor, I want **exactly one host-Claude call site** (`ClaudeCodeAgent.judge`), so the `is_error` envelope contract lives in the adapter that owns it and dead transport code is gone.

## Implementation Decisions

```
assertion text ──► binder.bind ──► _bind_bare_exists (regex fast path, unchanged)
                        │ no match
                        ▼
                  _call_gemini ── POST generativelanguage.googleapis.com
                        │           /v1beta/models/gemini-3.1-flash-lite:generateContent
                        │           (x-goog-api-key: $GEMINI_API_KEY, temperature 0, JSON mime)
        ┌───────────────┼───────────────────────────┐
        ▼ GeminiReply   ▼ RuntimeError (transient)   ▼ BinderAuthError (credential)
  _parse_binding   execution.py:164 degrades to     propagates loudly — a bad key
        │          judge punt — now counted+warned  is never laundered into grading
        ▼
   checker spec dict | None punt ──► checkers.run_assertion | judge fallback
```

- **The binder model is fixed in production; the corpus suite alone can override it.** `GEMINI_BINDER_MODEL = "gemini-3.1-flash-lite"` is a module constant — no `[tool.evalspec.binder]` table, no CLI flag (the roadmap exposes no binder config surface). The corpus suite honors `EVALSPEC_BINDER_MODEL` (beside `EVALSPEC_BINDER_SAMPLES`) so binder quality decisions can compare a candidate model against the incumbent without editing source — satisfying the roadmap's `--model`-parameterized corpus verification without adding a production knob.
- **The transport returns a `GeminiReply`, not a string.** `_call_gemini` extracts `candidates[0].content.parts[*].text` plus `usageMetadata` token counts and measures per-attempt latency inside the call — the channel the corpus metrics need. `bind()` consumes `.text`; its outward `dict | None` contract is untouched. The shape mirrors `lib/style_lint/gemini.py`'s `GeminiResponse` (same model, same endpoint), which is precedent, not an import — the wheel ships only `src/evalspec`.
  - Adopt the linter's `generationConfig.responseMimeType: "application/json"` — structured output reduces fence/prose parse-punts, and the corpus retention gate measures the effect directly.
  - Diverge on auth: `x-goog-api-key` header, not the linter's key-in-URL — keys don't belong in URLs.
- **The failure taxonomy is total, with credential failures carved out as non-degradable.** `BinderAuthError` (deliberately not a `RuntimeError` subclass) on credential rejection — HTTP 400 `API_KEY_INVALID`, 401, 403. Everything else normalizes to `RuntimeError` via a catch-all inside `_call_gemini`: network-level exceptions (`urllib.error.URLError`, `TimeoutError`/`socket.timeout`, connection resets mid-read), unparseable bodies, and degenerate 200s (no candidates, empty `parts`, `finishReason` `SAFETY`/`MAX_TOKENS`, `promptFeedback` block) — the message carrying the HTTP status / `finishReason` / `blockReason` excerpt. No raw transport exception escapes to error a cell.
- **Degradation to the judge becomes visible.** `execution.py`'s grade path counts binder `RuntimeError` degradations per arm, and the plugin warns loudly when the count is nonzero — mirroring the corpus suite's infra-error guard. `BinderAuthError` is not caught there: a bad key propagates and fails the run instead of silently rerouting every assertion to paid judge grading behind a green result.
  - The `except` tuple at `execution.py:164` narrows to `RuntimeError` — the `subprocess.TimeoutExpired` leg dies with the subprocess transport (transport timeouts now arrive as `RuntimeError`), and `tests/test_execution.py`'s timeout-punts test is reworked to the new taxonomy. The corpus `_bind_resilient` retry tuple narrows the same way.
- **`_parse_binding` takes the reply text.** The `claude -p` `{"result": …}` envelope unwrap is removed — verified punt-safe: under the new transport, whole-JSON model text would have been mass-punted by the old unwrap, so the divergence is the fix. `_balanced_objects` scanning, `_validate_checker_obj` validation, and the never-raises punt contract are unchanged.
- **The injection point renames `call_host` → `call_model`, returning `GeminiReply`.** `tests/test_binder.py` fixtures switch from recorded claude envelopes to reply objects; the identity test pinning the old import (`tests/test_binder.py:188-190`) is deleted outright; one new test asserts `bind`'s `call_model` default *is* `_call_gemini`, closing the wiring proof offline. `test_prompt_carries_load_bearing_pieces` stays the prompt invariant through any Gemini-motivated wording tuning.
- **`GEMINI_API_KEY` preflights at both graded entry points via one helper.** `binder.preflight_gemini_key()` treats missing *and empty* as absent (so a set-but-empty var can't be repopulated from `.env` by `load_dotenv`). Call sites: beside `preflight_judge_binary` in the `judge_config` fixture (`cases.py:57-66`) — firing only when a run will grade, never for trigger-only sessions — and in `evals/binder/conftest.py`'s `pytest_configure` (controller-side, gated on `_binder_selected`), failing the corpus session once before any draw. No live validation ping: a wrong-but-present key fails loudly at first use via `BinderAuthError`.
- **`agents/judge_cli.py` is deleted — full inventory.** `run_host_judge` and `raise_for_judge_cli_failure` die with the module (the latter's only consumer is `run_host_judge` itself); `raise_for_is_error_envelope` moves into `agents/claude.py`, whose `ClaudeCodeAgent.judge` is its sole remaining consumer. Fallout the deletion must also touch: the `ClaudeCodeAgent.judge` docstring (`agents/claude.py:236`) still ends "…via judge_cli"; `tests/agents/test_judge_cli.py` — only its is_error-envelope test survives, rewritten at the helper's new home, while the three `run_host_judge` tests die with their subject; `tests/test_execution.py:1063` monkeypatches `evalspec.agents.judge_cli.subprocess.run` and is retargeted; the stale transport references in `judges/__init__.py:8-9` and `docs/agents.md:71` are rewritten. The PR #6 fallback option — folding the binder onto `ClaudeCodeAgent.for_host()` — is superseded: the Gemini transport replaces the host-Claude call outright, and the file dies either way.
- **The corpus suite moves to `evals/binder/`.** `corpus.yaml`, `conftest.py`, `test_corpus.py`, `test_corpus_integrity.py` — replacing the top-level `evals/binder_corpus.yaml` shape per the roadmap. The Makefile `evals` target's positional arg becomes `evals/binder` — pinned deliberately: the controller-side aggregation and infra-guard hooks load only as an *initial* conftest on the args' ancestry path, so leaving the arg at `evals` would silently drop the summary, the results wipe, and the ≥5% gate under xdist. The `binder_corpus` marker description in `pyproject.toml` updates (no longer "Haiku"); `evals/lib/` stays put; `EVALSPEC_BINDER_SAMPLES` and the xdist sharding are unchanged.
- **Corpus metrics extend, with API-bearing draws separated from regex fast-path draws.** Each per-draw record gains `source: "regex" | "gemini"`, `attempts`, per-attempt `latency_ms`, and the reply's token counts. Session-summary aggregates — mean/p95 latency, total tokens, estimated cost from an in-suite pricing constant (documented as approximate) — compute over Gemini-sourced draws only; the regex fast-path count is reported as its own line (two current corpus `file_exists` entries bind without any API call, and folding their near-zero latencies into the mean would corrupt it). The per-draw zero-leak assertion, heavy-cohort sample floors, retention/over-punt/mismatch rates, an explicit leak count, and the ≥5% infra-error guard are unchanged. The existing mismatch rate is this suite's complement of the roadmap's "model accuracy" metric — nothing is dropped, only named differently.
- **The corpus run is the acceptance gate for the model swap.** Binder quality decisions come from direct Gemini API corpus runs; prompt-wording adjustments to `_BINDING_PROMPT` needed to hold the zero-leak gate on `gemini-3.1-flash-lite` are in scope, corpus re-labeling is not.
- **Integration tests stay small and harness-level.** With an injected `call_model` (no network), they prove the grading path invokes the binder, records deterministic checker results with their artifact `type`/`result`/`error` fields, falls through to the judge on punts, degrades a binder `RuntimeError` to judge grading with the degradation counted, and propagates `BinderAuthError` — the roadmap's stated bound for this layer, plus the default-transport assertion above.

## Testing Plan

### Logic
- **The failure taxonomy is total** — every transport failure surfaces as `BinderAuthError` (credential) or `RuntimeError` (everything else, message diagnosable); no raw urllib/socket/JSON exception escapes, and no failure surfaces as a silent punt at the transport layer.
- **The parse contract survives the envelope change** — a valid checker object in reply text binds and validates; a punt object, hallucinated args, or garbage returns `None`; parsing never raises.
- **Corpus metric math is reproducible and source-aware** — leak count, retention, over-punt, mismatch, latency aggregates, and cost derive deterministically from the per-draw records, with latency/token/cost aggregates drawn only from Gemini-sourced draws.
- **Corpus integrity stays deterministic** — the relocated corpus loads, validates, and enforces its labeling invariants with no network.

### Behavior
- **A live corpus run measures the fixed binder end-to-end** — draws hit the real Gemini API, the zero-leak gate holds per draw, and the session summary section prints leaks, retention, over-punt, mismatch, latency, cost, and the regex fast-path count (the summary's presence doubles as proof the relocated conftest loaded).
- **The grading path wires the binder correctly** — with an injected transport, bound assertions record deterministic results with artifact `type`/`result`/`error` fields, punts reach the judge, a transient binder failure degrades to judge grading with a nonzero degradation count and a warning, and a credential failure fails the run rather than degrading.

### Interface
- **A missing or empty `GEMINI_API_KEY` fails fast** — a graded run exits with an actionable message before any paid arm; the corpus session fails once at configure time; a trigger-only run needs no key.
- **The corpus-only model override is honored and bounded** — `EVALSPEC_BINDER_MODEL` swaps the model for corpus runs; the production surface exposes no binder configuration.
- **The import surface sheds `agents/judge_cli`** — no code-tree consumer references the module; no compatibility shim, per the roadmap's no-shims rule.

## Documentation Plan

- **`docs/configuration.md`**: `GEMINI_API_KEY` requirement, the fixed `gemini-3.1-flash-lite` binder model, binder-degradation visibility, and the distinction between direct-API corpus runs and evalspec harness integration tests.
- **`docs/agents.md`**: rewrite the line-71 binder paragraph — the binder is a fixed Gemini API call, and `ClaudeCodeAgent.judge` is the only host-Claude call site.
- **`docs/quickstart.md`**: add `GEMINI_API_KEY` to the credential prerequisites for graded runs.
- **`docs/concepts.md`**: update the binder-contract description where it names the host-Claude/haiku transport (`concepts.md:15` "cheap (Haiku) classifier", `:117` "separate, unrelated host-Claude call").

## Out of Scope

- Recording binder model/API path in `meta.json` and index rows — Phase 8 (arm-aware metadata) owns that field. Interim gap acknowledged: runs landing between Phases 2 and 8 do not record binder identity in artifacts.
- Any production binder provider/model config surface — the model is fixed by design this phase; `EVALSPEC_BINDER_MODEL` is a corpus-suite measurement knob, not product config.
- Running the binder corpus through OpenCode, Claude Code, or Codex harnesses — a roadmap exclusion unless production binder execution intentionally uses one.
- Changes to judge behavior, `grade_run` retry/parse/mask semantics, or the judge config surface — Phase 1 froze these.
- The `make evals` → `make e2e` transition — tied to the contract-eval phase (roadmap item 9).
- Re-labeling the corpus or changing its cohort structure — prompt tuning may respond to corpus results; the gold labels do not.
- Phase 5's `evalspec analyze` — noted for its planners: analyze will inherit the `GEMINI_API_KEY` requirement and pay per-assertion flash-lite calls to explain bindings before a run (the `bind_cache` memoization pattern in `execution.py` is available to bound the spend).

## References

- #1 — roadmap Phase 2 Done-When and the binder implementation decisions this spec instantiates; its Phase-1 summary comment queues the `judge_cli.py` deletion for Phase 2.
- #2 / PR #6 — Phase 1: the judge abstraction that stranded `judge_cli.py` as binder-only transport; the review thread offering the `for_host()` fold "as the first Phase-2 follow-up".
- `src/evalspec/binder.py` — `BINDER_MODEL`, `_BINDING_PROMPT`, `bind`/`_parse_binding`, the `call_host` injection point, `_bind_bare_exists`.
- `lib/style_lint/gemini.py` + `bin/linters/style_lint.py:23` — the repo's existing stdlib Gemini transport against the same model: the `GeminiResponse(text, usage)` shape, parts extraction, and `responseMimeType` precedent this spec adopts (and the key-in-URL choice it deliberately rejects).
- `src/evalspec/agents/judge_cli.py` — the module to delete; `raise_for_is_error_envelope` relocates to `agents/claude.py`.
- `src/evalspec/agents/claude.py` — `ClaudeCodeAgent.judge`, the sole remaining `raise_for_is_error_envelope` consumer; its `:236` docstring is part of the deletion fallout.
- `src/evalspec/execution.py:161-168` — the binder-failure degradation path this spec narrows (drop `TimeoutExpired`), instruments (degradation count), and punctures for `BinderAuthError`.
- `tests/test_execution.py:1063`, `tests/test_binder.py:188-190` — the `judge_cli` monkeypatch and import-identity test the deletion must rework/remove.
- `src/evalspec/cases.py:57-66` — the `judge_config` fixture whose graded-run gate the key preflight joins.
- `evals/binder_corpus.yaml`, `evals/test_binder_corpus.py`, `evals/test_binder_corpus_integrity.py`, `evals/conftest.py` — the suite relocating to `evals/binder/` and gaining latency/cost.
- `Makefile`, `pyproject.toml` — the `evals`/`evals:binder` targets (positional arg → `evals/binder`) and `binder_corpus` marker to retarget.

## Verification

- `make lint` — the package and docs pass lint after the transport swap, deletion, and suite move.
- `make test` — offline suites green: failure-taxonomy and parse tests, the default-transport assertion, relocated corpus integrity, and grading integration (deterministic results, judge fallback, degradation counting, `BinderAuthError` propagation) with injected `call_model`.
- `grep -rEn "agents[./]judge_cli" src tests evals` — returns nothing: the code trees are fully scrubbed (`docs/agents.md`/`concepts.md` rewrites are the Documentation Plan's; historical plans under `docs/superpowers/` and this spec keep their references, and `plugin.py`'s unrelated `_parse_judge_cli_table` doesn't match the scoped pattern).
- `make evals:binder` (with `GEMINI_API_KEY` set) — live corpus run against `gemini-3.1-flash-lite`: zero punt-leaks, and the "binder corpus" summary section prints leaks, retention, over-punt, mismatch, latency, cost, and the regex fast-path count.
- `GEMINI_API_KEY= make evals:binder` — fails fast before any API call: empty counts as missing, and a set-but-empty var can't be repopulated from `.env` by `load_dotenv`.
- `EVALSPEC_BINDER_MODEL=<candidate> make evals:binder` — the corpus-only override runs the identical gate against a candidate binder model, no source edit.
- `uv run pytest tests/test_execution.py -k "bind or binder"` — proves degradation counting, judge fallback on punts, artifact `type`/`result`/`error` fields, and loud `BinderAuthError` propagation (the relevant tests are named `test_bind_*`).
