# Configuration reference

Every evalspec knob, with defaults and rationale. The **eval set** is the central knob: each `[tool.evalspec.sets.<name>]` declares its own `arms` (the report columns), set-level `harness`/`model`/`effort`/`env`/`harness_args` defaults arms inherit, and a `baseline` arm (the Δ column); `default-set` names the one a plain run resolves. A run resolves exactly one set — by `default-set`, `--evalspec-set`, or a scratch `--evalspec-config` file — and the combined report is **one run-level `eval×arm` matrix** across the whole selection: rows keyed `group/eval_id` from the discovered eval roster, columns from the configured arms, non-baseline cells rendered `rate (+Npp)` (absolute rate AND Δ), closed by an `All evals` footer. It writes as a single `benchmark.json` / `benchmark.md` at the iteration root, beside `meta.json` / `index.jsonl` (see [`schema.md`](schema.md#benchmarkjson)); `meta.json`/`benchmark.json` also carry planned-vs-observed provenance (per-arm sandbox identity, guest harness version) and binder identity, but that adds no matrix columns — see [`schema.md`](schema.md#metajson). Agent-specific knobs (model, effort, version pin, pass-through args) are validated by the agent at invoke time, not by core — see [`agents.md`](agents.md).

## Eval sets — `[tool.evalspec.sets.<name>]`

Each `[tool.evalspec.sets.<name>]` declares one self-contained eval set. A run resolves exactly one set; its arms become the parametrized `(eval × arm)` cells, uniform across every skill in the run.

| Key | Type | Notes |
|---|---|---|
| `arms` | array of tables | The columns. Each inline table is one arm — `name` (required, unique within the set) plus any of `harness`/`model`/`effort`/`env`/`harness_args` to override or extend the set-level default. An empty list, a missing/non-string `name`, a duplicate name, or an arm with no resolvable `harness` (no arm value and no set default) fails fast. |
| `harness` | string | Set-level default harness (`claude-code`, `opencode`, `codex`) every arm inherits unless it sets its own. Must be a registered harness, or config-read fails naming the known set. Columns may span harnesses — each arm runs on its own agent. |
| `model` | string | Set-level default **task** model. Harness-specific: Claude Code takes aliases (`sonnet`/`haiku`/`opus`); OpenCode takes provider-qualified names (`anthropic/claude-sonnet-4-6`); Codex takes the model names its CLI accepts. An arm with no `model` and no set default fails fast. |
| `effort` | string | Set-level default reasoning effort (default `medium`). Per-harness and trust+record — not pre-validated; an unsupported value surfaces from the agent CLI. |
| `env` | table | Set-level default env injected into each arm's `setup.sh` AND the agent exec. Shallow-merged with an arm's own `env` (arm keys win). A value of the form `$VAR`/`${VAR}` expands from the host environment at arm *execution* time (an unset referenced var raises then, never at collection) — literals pass through. Use for a leaky-OpenRouter arm (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN = "$OPENROUTER_API_KEY"`). Must be a table. |
| `harness_args` | string[] | Raw CLI tokens appended by the selected harness adapter. Set-level args are inherited; arm-level args append after them, preserving order. Must be a list of strings. Use for harness-specific opt-ins such as `["--plugin-dir", "/project"]`; do not pass secrets here. Args that would override evalspec-owned identity/control (`model`, `effort`, prompt/print delivery, output/input format, resume/session handling, permission mode, etc.) are reserved and rejected by the adapter, including aliases and equals-form long flags. |
| `baseline` | string | Names the arm every other arm's Δ is measured against (Δ = arm − baseline, in pp). Optional: with no `baseline`, each arm reports its absolute pass rate. A `baseline` naming an undeclared arm fails fast. |
| `runner` | string | Set-level, default `pytest`. The test runner for the set — today the runner *is* the pytest plugin, so `pytest` is the only supported value. Any other value fails fast (exit `2`) naming the field and the supported values. |
| `sandbox` | string | Set-level, default `microsandbox`. The sandbox backend the set runs in — only `microsandbox` is implemented. `sandbox = "docker"` is registered but explicitly rejected as not implemented; any other unknown value fails fast (exit `2`) naming the field and the supported values. |

`runner` and `sandbox` are **set-level only** — there is no arm-level override and no `--evalspec-*` scalar-override flag for either. Both validate fail-fast at config-read time, the same as every other structural check in this table.

`default-set` (a top-level `[tool.evalspec]` key) names the set a plain run resolves. Required; a `default-set` naming an undeclared set fails fast.

Validation is **trust + record**: structure only (registered harness, unique arm names, `env` is a table, `harness_args` is a list of strings, `baseline`/`default-set` name declared things), no harness×model semantic policing. Any structural defect raises `SchemaError` at config-read time (surfaced as a pytest `UsageError` at collection), never a silent no-op mid-run. A pre-migration flat config (`[[tool.evalspec.arms]]` + top-level `reference`, no `[tool.evalspec.sets.*]`) is rejected fail-fast with a pointer to the eval-sets shape. Reserved `harness_args` fail later at agent invocation, where the selected adapter knows its CLI surface.

## The judge — `[tool.evalspec.judge]`

One run resolves exactly one **judge**: the host-side harness + model that grades every arm's assertions, independent from the task arms under test (grading a Codex or OpenCode run no longer requires installing Claude Code). Declare it under top-level `[tool.evalspec]`, never under a set or an arm — a fixed grader is what makes arm deltas comparable.

| Key | Type | Default | Notes |
|---|---|---|---|
| `harness` | string | `claude-code` | One of `claude-code`, `codex`, `opencode`. Unsupported values fail at collection, before any paid task arm runs. |
| `model` | string | `sonnet` | Harness-specific, like an arm's `model`. Core does not validate the model against the harness (models change too fast for a useful allow-list); a wrong or fake model surfaces as a loud infra error when the judge harness rejects it at run time. One structural exception: a `harness = "opencode"` judge requires a provider-qualified model (e.g. `anthropic/claude-sonnet-4-6`, not bare `sonnet`), which fails at collection. |
| `effort` | string | `medium` | Passed through to the judge harness's reasoning-effort flag where one exists (Codex has none — accepted for parity, unused). |
| `timeout` | integer | `300` | Judge subprocess timeout in seconds. Must be a positive integer. |
| `harness_args` | string[] | `[]` | Raw CLI tokens appended to the judge invocation. Reserved (evalspec-owned) flags are rejected per harness, same rule as an arm's `harness_args`. |
| `env` | table | `{}` | Judge-only env, injected at judge **execution** time (never at collection). `$VAR`/`${VAR}` values expand from the host environment via the same rule as arm `env` (`evalspec.arms.expand_env`) — an unset referenced var raises, literals pass through. |

```toml
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
effort = "medium"
timeout = 300
harness_args = ["--color", "never"]
env = { CODEX_HOME = "$CODEX_HOME" }
```

> **Best practice: judge cross-family.** Prefer a judge whose model family differs from the arms it grades — an Anthropic judge (`claude-code`/`sonnet`) grading Anthropic arms can be biased toward its own family's outputs. evalspec does not enforce this (the default judge is `claude-code`/`sonnet`, same-family with the common Claude-arm setup, which is fine for iterating), but for a benchmark you publish or compare across harnesses, pick a judge from a different family than the arms under test. See [the quickstart's cross-family example](quickstart.md#a-cross-family-judge).

## The binder

The binder (`binder.py`) maps each prose assertion to a deterministic checker or punts to the judge, via a fixed, direct call to `gemini-3.1-flash-lite` — no `[tool.evalspec.binder]` table, no CLI flag; the model is a module constant, not a config surface. Every graded run needs `GEMINI_API_KEY` (see Environment variables above); a missing or empty key fails fast, before any paid arm runs.

A transient binder infra failure degrades that one assertion to judge grading rather than erroring the cell — this is by design (see [`concepts.md`](concepts.md#the-binder)) — but is never silent: each degraded assertion increments `binder_degraded` in the arm's `grading.json`, and the run prints a `WARN binder: N assertion(s) degraded…` summary line when the total is nonzero. A credential rejection (`BinderAuthError`) is never degraded — it fails the run outright.

`EVALSPEC_BINDER_MODEL` overrides the binder model for the **corpus suite only** (`make evals`, `evals/binder/`) — a candidate-model comparison knob with no production surface. Corpus runs call the real Gemini API directly and cost money; they are a separate concern from evalspec's own offline integration tests (`tests/test_execution.py -k bind`), which inject a fake `call_model` and never touch the network.

## Pre-run analysis — `evalspec analyze`

`evalspec analyze [root]` (`python -m evalspec analyze [root]`) classifies every discovered assertion by how it will be graded, **before** you spend on a live run. It binds each assertion exactly as the runner does and prints a per-file table labeling each one:

- `deterministic` — the binder mapped it to a host-side checker (a workdir checker like `file_exists`/`glob_count`/`regex`, or an activation checker `skill_invoked`/`not_skill_invoked` that grades a *process fact*: which skills the arm dispatched). Zero-variance, no judge cost — see [`schema.md`](schema.md#assertions).
- `judge-backed` — the binder punts; the LLM judge grades it (the nondeterministic path).

A fully-classified suite exits 0 (a classification is a report, not a warning — unlike `evalspec lint`, `analyze` never exits nonzero on a non-empty suite). Discovery, parse, and schema errors propagate and exit nonzero.

Because binding reuses the real binder, `analyze` needs **`GEMINI_API_KEY`** set (same key the graded run needs — see [Environment variables](#environment-variables)); it fails fast if the key is missing or empty. Spend follows the binder's own fast-path split: the local bare-exists recognizer (`file_exists`) bills **nothing**, while every other assertion — activation lines included — pays one Gemini punt-or-bind call.

## Running a benchmark — `evalspec run`

`evalspec run [root]` (`python -m evalspec run [root]`) resolves one eval set, runs its `(eval × arm)` cells under pytest, and grades them. Each flag is shorthand for the underlying `--evalspec-*` pytest option; the option's semantics are documented in [CLI flags](#cli-flags-pytest) below.

| `evalspec run` flag | pytest option |
|---|---|
| `--set` | `--evalspec-set` |
| `--config` | `--evalspec-config` |
| `--model` | `--evalspec-model` |
| `--models` | `--evalspec-models` |
| `--harness` | `--evalspec-harness` |
| `--effort` | `--evalspec-effort` |
| `--eval-paths` | `--evalspec-eval-paths` |
| `--fail-under` | `--evalspec-fail-under` |
| `--judge-harness` | `--evalspec-judge-harness` |
| `--judge-model` | `--evalspec-judge-model` |
| `--judge-effort` | `--evalspec-judge-effort` |
| `--env` | `--evalspec-env` (repeatable `KEY=VAL`) |

Anything after a standalone `--` passes through to pytest verbatim, so power-user selectors and pytest options still work: `evalspec run --set default -- -k archive -x --collect-only`. The curated surface is deliberately narrower than the full `--evalspec-*` set — reach for `--` (or drive `pytest` directly) for the rest.

### Exit codes

All four `evalspec` subcommands (`lint`, `analyze`, `run`, `sandbox:build`) share one contract:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | a finding or gate failure — `lint` warnings, or a `run --fail-under` gate tripped |
| `2` | usage error — a bad flag, an unknown `--set`, an unreadable `--config`, or a resolved set whose `runner`/`sandbox` names an unsupported value (e.g. `sandbox = "docker"`), caught before any paid arm runs |
| `5` | nothing to do — no evals discovered under `root` |

The unsupported-`runner`/`sandbox` case exits `2` through **both** `evalspec run` and `evalspec sandbox:build` — set resolution (and therefore this validation) happens the same way in each.

`run` detects an empty selection **up front** — it reuses the plugin's own discovery before spawning pytest — so a root with no evals returns `5` with a readable message and never boots a VM. One consequence of that ordering: because emptiness short-circuits first, a bad `--set` (or an unreadable `--config`) against an **empty** root also returns `5`, not `2` — the set and config are only validated once there are cells to parametrize. The exit-`2`-for-a-bad-set contract therefore holds for a **populated** repo.

## Building the sandbox — `evalspec sandbox:build`

`evalspec sandbox:build [root]` builds (or reuses) the sandbox snapshot for the repo's agent up front, so the first `evalspec run` doesn't pay the build cost. With neither `--set` nor `--config`, it resolves the agent and environment from `root` alone and builds through the default `microsandbox` backend — the bare form is unchanged. A failed host preflight exits `2`, a build failure exits `1`.

`--set <name>` resolves that eval set and builds through *its* `sandbox` backend and `env` instead of the bare defaults; `--config <file>` layers a scratch TOML over pyproject for that set resolution (same shape as `--evalspec-config`). A `--set` whose `sandbox` names an unsupported value (e.g. `docker`) exits `2` (usage) before any preflight runs.

## CLI flags (`pytest`)

| Flag | Default | Notes |
|---|---|---|
| `--evalspec-set` | (`default-set`) | Name of the eval set to resolve for this run. Defaults to the pyproject `default-set`. An unknown name fails fast. |
| `--evalspec-config` | (none) | Path to an untracked TOML file whose `[tool.evalspec.sets.*]` layer over pyproject's — a scratch set for a one-off comparison without editing tracked config. The file uses the same `[tool.evalspec]` shape as pyproject; pair with `--evalspec-set` to pick the scratch set. |
| `--evalspec-model` | (none) | Scalar override of the resolved set's `model` **default** — every arm that inherited it picks up the new value; arms that declared their own `model` keep it. Agent-specific: Claude Code takes aliases (`sonnet`/`haiku`/`opus`); OpenCode takes provider-qualified names; Codex takes the names accepted by `codex exec -m`. This does not affect the judge — see `--evalspec-judge-model` below. Core doesn't validate; the agent rejects at invoke time. |
| `--evalspec-harness` | (none) | Scalar override of the resolved set's `harness` default (same inherit-vs-declared rule as `--evalspec-model`). An unknown harness fails fast at collection. |
| `--evalspec-effort` | (none) | Scalar override of the resolved set's `effort` default. Trust+record — passed through unvalidated. |
| `--evalspec-env` | (none) | `KEY=VAL` env entry added to / overriding the resolved set's `env` default (repeatable). A `$VAR` value expands from the host environment at arm execution. |
| `--evalspec-models` | (none) | Comma-separated model **sweep** — expand the resolved set into one arm per value (each arm named by its model, all inheriting the set defaults), with baseline = the first value. A file-free single-axis comparison; overrides the set's declared arms. |
| `--evalspec-judge-harness` | (none) | Scalar override of the judge `harness`. Precedence: this flag > `--evalspec-config` `[tool.evalspec.judge]` > project `[tool.evalspec.judge]` > built-in default (`claude-code`). |
| `--evalspec-judge-model` | (none, resolves to `sonnet`) | Scalar override of the judge `model`. **Defaults to `None`, not `sonnet`** — a hardcoded flag default would always beat `[tool.evalspec.judge]`, breaking precedence. Recorded in `meta.json["judge"]["model"]`. |
| `--evalspec-judge-effort` | (none, resolves to `medium`) | Scalar override of the judge `effort`. |
| `--evalspec-judge-timeout` | (none, resolves to `300`) | Scalar override of the judge subprocess timeout in seconds. |
| `--evalspec-judge-harness-arg` | (none) | Repeatable. When given at all, **fully replaces** `[tool.evalspec.judge] harness_args` — unlike a set/arm's `harness_args`, which append, the judge's CLI override is a full swap per precedence layer. |
| `--evalspec-judge-env` | (none) | Repeatable `KEY=VAL`. Shallow-merges over `[tool.evalspec.judge] env` (CLI keys win), same merge rule across every layer (pyproject → scratch → CLI). |
| `--evalspec-repo-root` | (rootdir) | Repository root that output-eval search paths resolve against, and used to resolve project-relative sandbox paths. Precedence: flag > `$PROJECT_ROOT` > pytest rootdir. |
| `--evalspec-agent` | `claude-code` | The run-level default coding agent, used by the `__route__` sandbox path and by `make_agent()` when no explicit harness is given. Precedence: this flag > `EVALSPEC_AGENT` > `[tool.evalspec] agent` in `pyproject.toml` > `claude-code`. Task arms select their harness per arm (each arm's `harness`), so this flag does not govern them. Unknown values fail at startup naming the source. |
| `--evalspec-eval-paths` | `skills,tests,evals,benchmarks` | The **sole discovery knob.** Comma-separated paths (relative to repo root) whose trees are walked for `eval.md` / `*.eval.md`. Also settable via `[tool.evalspec] eval_paths`. Precedence: flag > pyproject > default. |
| `--evalspec-project-marker` | `.claude-plugin/plugin.json` | Marker file (relative to repo root) whose presence marks the repo as a host plugin worth mounting in the sandbox as `--plugin-dir`. Task arms do not infer plugin exposure from this marker; opt in per set or arm with `harness_args = ["--plugin-dir", "/project"]`. Override for OpenCode (`opencode.json`) or other layouts. |
| `--evalspec-fail-under` | (off) | CI gate: minimum acceptable arm-vs-baseline Δ in percentage points. The gate stays **per-group** even though the report is pooled run-level: evalspec recomputes each group's arm-vs-baseline Δ independently and any single group whose Δ falls below the threshold fails the run with exit 1 — a group's regression is never averaged away by other groups' wins. Example: `--evalspec-fail-under 0` means every contrast arm must at least match the baseline in every group. Each arm's headline rate is the **pooled, sample-weighted mean** over its surviving `(eval × sample)` assertion-fraction rates (errored samples excluded; an eval with more surviving samples weighs more — not a true per-eval macro-mean). Uses the **raw Δ** — check the `within noise` label in `benchmark.md` before treating small numbers as meaningful. Groups with no baseline Δ (a set with no baseline arm, or a group that never ran the baseline arm) are exempt — the gate requires a finite Δ. |

Sampling for stability isn't an evalspec knob — it rides `pytest-repeat`: pass `--count N` to run each `(eval × arm)` N times, and the benchmark surfaces per-arm stddev across the samples.

## Environment variables

| Variable | Purpose |
|---|---|
| `EVALSPEC_AGENT` | Coding-agent fallback when `--evalspec-agent` is not passed: `claude-code` (default), `opencode`, or `codex`. Like the flag, sets the run-level default agent (the `__route__` sandbox path and `make_agent()` default); task arms select their harness per arm. Beaten by `--evalspec-agent`; beats `[tool.evalspec] agent`. Unknown values fail at startup naming the source. |
| `PROJECT_ROOT` | Project root override. Beaten by `--evalspec-repo-root`; beats the pytest rootdir default. |
| `EVALSPEC_CLAUDE_VERSION` | Pinned Claude Code version (default `latest`). Bumping produces a new microsandbox snapshot; versions coexist. |
| `EVALSPEC_OPENCODE_VERSION` | Pinned OpenCode version. Precedence: env > `[tool.evalspec] opencode_version` > `latest`. |
| `EVALSPEC_CODEX_VERSION` | Pinned Codex CLI version (default `latest`). Bumping produces a new microsandbox snapshot. |
| `EVALSPEC_ITERATION` | Internal. Iteration name (`iteration_NN`, e.g. `iteration_01`) chosen by the controller and inherited by workers; don't set by hand. |
| `EVALSPEC_ARM` | Per-cell. The arm name for this cell. Set by the harness adapter **inside the sandbox** (`cell_env`), read by the eval group's `setup.sh` to decide whether to install the skill (trial) or no-op (baseline). Don't set by hand. |
| `EVALSPEC_MODEL` | Per-cell. The arm's task model — **informational** for `setup.sh`; it does NOT route the task model (the harness already routes that). Set by the adapter inside the sandbox. |
| `EVALSPEC_HARNESS` | Per-cell. The harness that ran this cell (`self.id` — the agent owns it): the arm's `harness`. Set by the adapter inside the sandbox; read by `setup.sh`. |
| `EVALSPEC_SET` | Per-cell. Names the explicitly-selected set (`--evalspec-set` / `evalspec run --set`), so `setup.sh` can branch on which set is running (alongside `EVALSPEC_ARM`); empty when the run falls back to the pyproject `default-set`. Set by the adapter inside the sandbox; read by `setup.sh`. |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude credential (preferred over `ANTHROPIC_API_KEY`). From `claude setup-token`. |
| `ANTHROPIC_API_KEY` | Claude credential fallback. Also OpenCode's second-choice provider when `OPENROUTER_API_KEY` is unset. |
| `OPENROUTER_API_KEY` | OpenCode credential (highest precedence among OpenCode providers). |
| `GEMINI_API_KEY` | Two independent consumers. (1) **The binder** (`binder.py`) — required unconditionally for any graded run; classification always calls `gemini-3.1-flash-lite` directly, regardless of the task or judge harness. Empty counts as missing. (2) **OpenCode's** Google/Gemini provider credential — conditional, used only when neither `OPENROUTER_API_KEY` nor `ANTHROPIC_API_KEY` is set, and only for OpenCode arms. |
| `CODEX_API_KEY` | Codex credential, preferred by `CodexAgent`. |
| `CODEX_ACCESS_TOKEN` | Codex credential fallback when `CODEX_API_KEY` is unset. |
| `CODEX_AUTH_JSON_PATH` | Path to a local Codex `auth.json` created by `codex login`. Used when `CODEX_API_KEY` and `CODEX_ACCESS_TOKEN` are unset; evalspec copies it into the sandbox as `/root/.codex/auth.json` so Codex can use ChatGPT/Codex subscription auth. |
| `PYTEST_XDIST_WORKER` | Internal. Set by xdist; tags per-worker sandbox names so concurrent runs don't collide. |

`.env` at the repo root is auto-loaded via `python-dotenv` — no manual export needed.

## `[tool.evalspec]` table in `pyproject.toml`

| Key | Type | Notes |
|---|---|---|
| `default-set` | string | Names the eval set a plain `evalspec run` resolves. Required. `--evalspec-set` overrides it per run. A `default-set` naming an undeclared set fails fast. |
| `sets.<name>` | table | One eval set — `arms` + set-level `harness`/`model`/`effort`/`env`/`harness_args` defaults + `baseline` + `runner`/`sandbox`. See [Eval sets](#eval-sets--toolevalspecsetsname) above. |
| `agent` | string | Run-level default coding agent (the `__route__` sandbox path and `make_agent()` default; task arms select their harness per arm). Below `EVALSPEC_AGENT` and `--evalspec-agent`, above the built-in default (`claude-code`). Use to make a project default to `opencode` without setting env vars. |
| `eval_paths` | string[] | The sole discovery knob — directories (relative to repo root) whose trees are walked for `eval.md` / `*.eval.md`. Below `--evalspec-eval-paths`, above the built-in default (`skills`, `tests`, `evals`, `benchmarks`). |
| `base_image` | string | OCI image ref for the eval sandbox; default `ubuntu:latest`. Swaps the pre-baked base before the agent provisions. Must be a Debian/apt-family image with glibc — the agent's provision step runs `apt-get` and installs glibc-linked CLIs. Folds into the snapshot cache identity: changing it auto-rebuilds. Opt-in; absent ⇒ `ubuntu:latest`. |
| `environment_script` | string | Repo-relative path to a shell script run AFTER the agent installs (the escape hatch for extra tools/config). Resolved to bytes at config-read time; a missing/unreadable file fails fast with `SchemaError`. Runs under `set -e` (the guest `/bin/sh` is dash) — the first failing command aborts the build loudly. Folds into the snapshot cache identity by CONTENT: an in-place edit (same path, new bytes) forces a rebuild. Opt-in; absent ⇒ no extra step. |
| `opencode_version` | string | Default OpenCode version when `EVALSPEC_OPENCODE_VERSION` is unset. |
| `judge` | table | The run's judge — `harness`/`model`/`effort`/`timeout`/`harness_args`/`env`. See [The judge](#the-judge--toolevalspecjudge) above. |

Example:

```toml
[tool.evalspec]
default-set = "default"
base_image = "python:3.12-slim"        # still top-level
environment_script = "evals/setup.sh"  # still top-level

[tool.evalspec.judge]
harness = "claude-code"   # built-in default; explicit here for illustration
model = "sonnet"

[tool.evalspec.sets.default]
harness = "claude-code"   # set-level default; arms with no `harness` inherit it
model = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },   # installs no skill — measures what the agent already knows
  { name = "trial" },      # installs the skill
]

[tool.evalspec.sets.plugin-enabled]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial", harness_args = ["--plugin-dir", "/project"] },
]

[tool.evalspec.sets.popular-harnesses]
effort = "medium"
baseline = "claude-code"
arms = [
  { name = "claude-code", harness = "claude-code", model = "sonnet" },
  { name = "opencode", harness = "opencode", model = "anthropic/claude-sonnet-4-6" },
  { name = "codex", harness = "codex", model = "gpt-5.4" },
  { name = "leaky-openrouter", harness = "claude-code", model = "sonnet", env = { ANTHROPIC_BASE_URL = "https://openrouter.ai/api/v1", ANTHROPIC_AUTH_TOKEN = "$OPENROUTER_API_KEY" } },
]
```

The sets and `default-set` are pyproject-only at config-read time; a scratch `--evalspec-config` file layers extra sets, and `--evalspec-set` / the `--evalspec-model`/`-harness`/`-effort`/`-env` scalar overrides / the `--evalspec-models` sweep adjust the resolved set per run. `base_image` and `environment_script` stay top-level `[tool.evalspec]` keys (not per-set) and expose no env-var or CLI channel — per-set setup needs are met by `setup.sh` branching on `EVALSPEC_SET`.

## Precedence rules

Every knob that appears in multiple places follows the same chain — resolved once at configure time:

```
CLI flag  >  environment variable  >  pyproject.toml  >  built-in default
```

This applies uniformly: `--evalspec-agent` > `EVALSPEC_AGENT` > `[tool.evalspec] agent` > `claude-code`; for eval discovery, `--evalspec-eval-paths` > `[tool.evalspec] eval_paths` > built-in default. Not every setting exposes every channel: `eval_paths` has no env var, so its chain is flag > pyproject > default.

Not every knob has every channel. `--evalspec-fail-under` is **flag-only** — no env var, no `[tool.evalspec]` key; it's a CI decision, set by the CI command. The judge knobs (`--evalspec-judge-harness`/`-model`/`-effort`/`-timeout`/`-harness-arg`/`-env`) follow the full `CLI > scratch --evalspec-config > pyproject [tool.evalspec.judge] > built-in default` chain, unlike the flag-only knobs above.

For **task arms**, model and effort are validated by the *selected agent*, not core — a value valid for `claude-code` may be rejected under `EVALSPEC_AGENT=opencode`. The **judge** follows the same trust-and-record philosophy (its model is validated by the judge harness at run time, not core), with one structural exception enforced at collection — an `opencode` judge requires a provider-qualified model. See [The judge](#the-judge--toolevalspecjudge).

## Filter interactions

- `pytest -k <expr>` matches test ids `<group>-<eval_id>-<arm>` (`<arm>` is the declared arm name). Example:
  - `-k "archive and claude-sonnet"` — cases whose group or eval id matches `archive`, limited to the `claude-sonnet` arm.
- `pytest -n N` — xdist workers. The controller picks one iteration and shares it via `EVALSPEC_ITERATION`.
- `pytest -x` — stop on first failure. Partial `benchmark.md` still writes for completed arms.

## Make targets

In the host plugin's Makefile (external consumers replicate as needed):

| Target | Effect |
|---|---|
| `make e2e` | Runs evalspec's own `e2e` set and validates its generated artifacts. |
| `make evals EVAL_ARGS="…"` | Runs the paid binder corpus; `EVAL_ARGS` passes pytest collection options. |

Use [`evalspec run`](#running-a-benchmark--evalspec-run) for project skill evals and
[`evalspec sandbox:build`](#building-the-sandbox--evalspec-sandboxbuild) to build a
sandbox snapshot explicitly.

## Cross-agent runs

An eval set's columns may span harnesses: declare a `claude-code` arm and an `opencode` arm in one set's `arms` (as the `popular-harnesses` example above does) and one run contrasts them — each task arm runs on its own `harness`, in the one run-level eval×arm matrix. Snapshots are agent-keyed and coexist on disk, so a multi-harness set reuses each harness's cached snapshot. CI wrappers (label-gated, cron, etc.) compose as needed.
