# Configuration reference

Every evalspec knob, with defaults and rationale. The **eval set** is the central knob: each `[tool.evalspec.sets.<name>]` declares its own `arms` (the report columns), set-level `harness`/`model`/`effort`/`env`/`harness_args` defaults arms inherit, and a `baseline` arm (the Δ column); `default-set` names the one a plain run resolves. A run resolves exactly one set — by `default-set`, `--evalspec-set`, or a scratch `--evalspec-config` file — and the combined report is one eval×arm matrix. Agent-specific knobs (model, effort, version pin, pass-through args) are validated by the agent at invoke time, not by core — see [`agents.md`](agents.md).

## Eval sets — `[tool.evalspec.sets.<name>]`

Each `[tool.evalspec.sets.<name>]` declares one self-contained eval set. A run resolves exactly one set; its arms become the parametrized `(eval × arm)` cells, uniform across every skill in the run.

| Key | Type | Notes |
|---|---|---|
| `arms` | array of tables | The columns. Each inline table is one arm — `name` (required, unique within the set) plus any of `harness`/`model`/`effort`/`env`/`harness_args` to override or extend the set-level default. An empty list, a missing/non-string `name`, a duplicate name, or an arm with no resolvable `harness` (no arm value and no set default) fails fast. |
| `harness` | string | Set-level default harness (`claude-code`, `opencode`, `codex`) every arm inherits unless it sets its own. Must be a registered harness, or config-read fails naming the known set. Columns may span harnesses — each output-eval arm runs on its own agent. (Trigger routing still resolves to the single run-level agent; see `--evalspec-agent`.) |
| `model` | string | Set-level default **task** model. Harness-specific: Claude Code takes aliases (`sonnet`/`haiku`/`opus`); OpenCode takes provider-qualified names (`anthropic/claude-sonnet-4-6`); Codex takes the model names its CLI accepts. An arm with no `model` and no set default fails fast. |
| `effort` | string | Set-level default reasoning effort (default `medium`). Per-harness and trust+record — not pre-validated; an unsupported value surfaces from the agent CLI. |
| `env` | table | Set-level default env injected into each arm's `setup.sh` AND the agent exec. Shallow-merged with an arm's own `env` (arm keys win). A value of the form `$VAR`/`${VAR}` expands from the host environment at arm *execution* time (an unset referenced var raises then, never at collection) — literals pass through. Use for a leaky-OpenRouter arm (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN = "$OPENROUTER_API_KEY"`). Must be a table. |
| `harness_args` | string[] | Raw CLI tokens appended by the selected harness adapter. Set-level args are inherited; arm-level args append after them, preserving order. Must be a list of strings. Use for harness-specific opt-ins such as `["--plugin-dir", "/project"]`; do not pass secrets here. Args that would override evalspec-owned identity/control (`model`, `effort`, prompt/print delivery, output/input format, resume/session handling, permission mode, etc.) are reserved and rejected by the adapter, including aliases and equals-form long flags. |
| `baseline` | string | Names the arm every other arm's Δ is measured against (Δ = arm − baseline, in pp). Optional: with no `baseline`, each arm reports its absolute pass rate. A `baseline` naming an undeclared arm fails fast. |

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

`EVALSPEC_BINDER_MODEL` overrides the binder model for the **corpus suite only** (`make evals:binder`, `evals/binder/`) — a candidate-model comparison knob with no production surface. Corpus runs call the real Gemini API directly and cost money; they are a separate concern from evalspec's own offline integration tests (`tests/test_execution.py -k bind`), which inject a fake `call_model` and never touch the network.

## CLI flags (`pytest`, or `make evals EVAL_ARGS=…`)

| Flag | Default | Notes |
|---|---|---|
| `--evalspec-set` | (`default-set`) | Name of the eval set to resolve for this run. Defaults to the pyproject `default-set`. An unknown name fails fast. |
| `--evalspec-config` | (none) | Path to an untracked TOML file whose `[tool.evalspec.sets.*]` layer over pyproject's — a scratch set for a one-off comparison without editing tracked config. The file uses the same `[tool.evalspec]` shape as pyproject; pair with `--evalspec-set` to pick the scratch set. |
| `--evalspec-model` | (none) | Scalar override of the resolved set's `model` **default** — every arm that inherited it picks up the new value; arms that declared their own `model` keep it. Also the model used for **trigger-routing** runs (falling back to `sonnet` when unset). Agent-specific: Claude Code takes aliases (`sonnet`/`haiku`/`opus`); OpenCode takes provider-qualified names; Codex takes the names accepted by `codex exec -m`. This does not affect the judge — see `--evalspec-judge-model` below. Core doesn't validate; the agent rejects at invoke time. For trigger evals this also selects the gated tier (`fails-on` relaxes the gate for its listed tiers; vary this to verify routing per tier — see `evalspec-trigger/v1` in schema.md). |
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
| `--evalspec-repo-root` | (rootdir) | Repository root used for repo-wide output-eval discovery and to resolve project-relative trigger and sandbox paths. Precedence: flag > `$PROJECT_ROOT` > pytest rootdir. |
| `--evalspec-agent` | `claude-code` | The coding agent for **trigger-routing** runs. Precedence: this flag > `EVALSPEC_AGENT` > `[tool.evalspec] agent` in `pyproject.toml` > `claude-code`. Output-eval task arms select their harness per arm (each arm's `harness`), so this flag no longer governs them. Unknown values fail at startup naming the source. |
| `--evalspec-trigger-mode` | `asymmetric` | Trigger scoring. `majority` = >half of 3 (reliable routing); `best-of` = ≥1 of 3 (lenient positives, strict negatives); `asymmetric` = best-of for should-trigger, majority for should-not. |
| `--evalspec-trigger-effort` | `low` | Effort for trigger routing — a snap "which skill fires?" decision, so the model dispatches fast. Agent-specific and passed through unvalidated: an unsupported value surfaces as an error from the agent CLI, not a pre-run `UsageError`. |
| `--evalspec-trigger-timeout` | `20` (seconds) | Per-pass routing budget. Streamed activity without dispatch = non-fire (agent worked, didn't route in time). No stream at all = launch stall, retried as `RoutingError`. |
| `--evalspec-eval-roots` | (default Claude layout) | **Trigger evals only.** Comma-separated paths (relative to repo root) to scan for skill dirs containing `evals/trigger-evals.md`. Default `skills,.claude/skills`; also settable via `[tool.evalspec] eval_roots`. Output evals ignore this setting and are discovered repo-wide. |
| `--evalspec-project-marker` | `.claude-plugin/plugin.json` | Marker file (relative to repo root) used by trigger routing to find the real plugin surface. Output evals do not infer plugin exposure from this marker; opt in per set or arm with `harness_args = ["--plugin-dir", "/project"]`. Override for OpenCode (`opencode.json`) or other layouts. |
| `--evalspec-fail-under` | (off) | CI gate: minimum acceptable arm-vs-baseline Δ in percentage points. Any skill whose Δ falls below it fails the run with exit 1. Example: `--evalspec-fail-under 0` means every contrast arm must at least match the baseline. Uses the **raw Δ** — check the `within noise` label in `benchmark.md` before treating small numbers as meaningful. Skills with no baseline Δ (trigger-only runs, or a set with no baseline arm) are exempt — the gate requires a finite Δ. |

Sampling for stability isn't an evalspec knob — it rides `pytest-repeat`: pass `--count N` to run each `(eval × arm)` N times, and the benchmark surfaces per-arm stddev across the samples.

## Environment variables

| Variable | Purpose |
|---|---|
| `EVALSPEC_AGENT` | Coding-agent fallback when `--evalspec-agent` is not passed: `claude-code` (default), `opencode`, or `codex`. Like the flag, governs **trigger-routing** runs; output-eval task arms select their harness per arm. Beaten by `--evalspec-agent`; beats `[tool.evalspec] agent`. Unknown values fail at startup naming the source. |
| `PROJECT_ROOT` | Project root override. Beaten by `--evalspec-repo-root`; beats the pytest rootdir default. |
| `EVALSPEC_CLAUDE_VERSION` | Pinned Claude Code version (default `latest`). Bumping produces a new microsandbox snapshot; versions coexist. |
| `EVALSPEC_OPENCODE_VERSION` | Pinned OpenCode version. Precedence: env > `[tool.evalspec] opencode_version` > `latest`. |
| `EVALSPEC_CODEX_VERSION` | Pinned Codex CLI version (default `latest`). Bumping produces a new microsandbox snapshot. |
| `EVALSPEC_ITERATION` | Internal. Iteration name (`iteration_NN`, e.g. `iteration_01`) chosen by the controller and inherited by workers; don't set by hand. |
| `EVALSPEC_ARM` | Per-cell. The arm name for this cell. Set by the harness adapter **inside the sandbox** (`cell_env`), read by the eval group's `setup.sh` to decide whether to install the skill (trial) or no-op (baseline). Don't set by hand. |
| `EVALSPEC_MODEL` | Per-cell. The arm's task model — **informational** for `setup.sh`; it does NOT route the task model (the harness already routes that). Set by the adapter inside the sandbox. |
| `EVALSPEC_HARNESS` | Per-cell. The harness that ran this cell (`self.id` — the agent owns it): the arm's `harness` for output-eval task cells, the resolved agent for trigger cells. Set by the adapter inside the sandbox; read by `setup.sh`. |
| `EVALSPEC_SET` | Per-cell. Names the explicitly-selected set (`--evalspec-set` / `make evals SET=`), so `setup.sh` can branch on which set is running (alongside `EVALSPEC_ARM`); empty when the run falls back to the pyproject `default-set`. Set by the adapter inside the sandbox; read by `setup.sh`. |
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
| `default-set` | string | Names the eval set a plain run resolves (the one `make evals` uses). Required. `--evalspec-set` overrides it per run. A `default-set` naming an undeclared set fails fast. |
| `sets.<name>` | table | One eval set — `arms` + set-level `harness`/`model`/`effort`/`env`/`harness_args` defaults + `baseline`. See [Eval sets](#eval-sets--toolevalspecsetsname) above. |
| `agent` | string | Default coding agent for **trigger-routing** runs (output-eval task arms select their harness per arm). Below `EVALSPEC_AGENT` and `--evalspec-agent`, above the built-in default (`claude-code`). Use to make a project default to `opencode` without setting env vars. |
| `eval_roots` | string[] | **Trigger evals only.** Where to scan for skill dirs containing `evals/trigger-evals.md`. Below `--evalspec-eval-roots`, above the built-in default. Output evals ignore this setting and are discovered repo-wide. |
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

This applies uniformly: `--evalspec-agent` > `EVALSPEC_AGENT` > `[tool.evalspec] agent` > `claude-code`; for trigger discovery, `--evalspec-eval-roots` > `[tool.evalspec] eval_roots` > built-in default. Not every setting exposes every channel: `eval_roots` has no env var, so its chain is flag > pyproject > default. Output-eval discovery is repo-wide and does not use that chain.

Not every knob has every channel. `--evalspec-fail-under` is **flag-only** — no env var, no `[tool.evalspec]` key; it's a CI decision, set by the CI command. The judge knobs (`--evalspec-judge-harness`/`-model`/`-effort`/`-timeout`/`-harness-arg`/`-env`) follow the full `CLI > scratch --evalspec-config > pyproject [tool.evalspec.judge] > built-in default` chain, unlike the flag-only knobs above.

For **task arms**, model and effort are validated by the *selected agent*, not core — a value valid for `claude-code` may be rejected under `EVALSPEC_AGENT=opencode`. The **judge** follows the same trust-and-record philosophy (its model is validated by the judge harness at run time, not core), with one structural exception enforced at collection — an `opencode` judge requires a provider-qualified model. See [The judge](#the-judge--toolevalspecjudge).

## Filter interactions

- `pytest -k <expr>` matches test ids: `<group>-<eval_id>-<arm>` for output evals (`<arm>` is the declared arm name), `<skill>-<slug>` for trigger evals. Examples:
  - `-k "archive and claude-sonnet"` — output cases whose group or eval id matches `archive`, limited to the `claude-sonnet` arm.
  - `-k "ingest-article or inbox-process"` — trigger queries by slug.
- `pytest -n N` — xdist workers. The controller picks one iteration and shares it via `EVALSPEC_ITERATION`.
- `pytest -x` — stop on first failure. Partial `benchmark.md` still writes for completed arms.
- `fails-on`-marked trigger queries run as non-strict xfail: a documented routing miss or over-fire is a green xfail; a future fix surfaces as XPASS.

## Make targets

In the host plugin's Makefile (external consumers replicate as needed):

| Target | Effect |
|---|---|
| `make evals EVAL_ARGS="-k …"` | Wraps `pytest -p evalspec.plugin`. Runs the `default-set`. |
| `make evals SET=<name>` | Resolve a named set instead of `default-set` (forwards `--evalspec-set <name>`). Combine with `EVAL_ARGS` for scope/overrides. |
| `make evals:build` | Builds the microsandbox snapshot up front for the current `EVALSPEC_AGENT`. |

## Cross-agent runs

An eval set's columns may span harnesses: declare a `claude-code` arm and an `opencode` arm in one set's `arms` (as the `popular-harnesses` example above does) and one run contrasts them — each output-eval task arm runs on its own `harness`, in one eval×arm matrix. Trigger routing still resolves to the single run-level agent set by `--evalspec-agent` / `EVALSPEC_AGENT`, so to get cross-harness trigger signal (or to run a whole suite's triggers on one agent), run each agent explicitly:

```bash
EVALSPEC_AGENT=claude-code make evals EVAL_ARGS="-k <skill>"
EVALSPEC_AGENT=opencode   make evals EVAL_ARGS="-k <skill>"
EVALSPEC_AGENT=codex      make evals EVAL_ARGS="-k <skill>"
```

Snapshots are agent-keyed and coexist on disk, so a multi-harness set reuses each harness's cached snapshot. CI wrappers (label-gated, cron, etc.) compose as needed.
