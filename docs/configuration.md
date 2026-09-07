# Configuring benchmarks

This is the configuration reference: the `[tool.benchspec]` tables in
`pyproject.toml` (eval sets, arms, the judge), the `benchspec` command line,
how the layers combine, exit codes, and environment variables. It is for someone
who has run the [quickstart](quickstart.md) and wants to shape a benchmark; the
terms (eval set, arm, baseline, judge, cell) are defined in
[concepts.md](concepts.md).

A benchmark is declared once, in `pyproject.toml`, as an **eval set**: the arms
(report columns), the defaults they share, and the baseline the deltas are
measured against. A run resolves exactly one set, and the CLI can then adjust it
(pick a different set, override a default, sweep models) without touching tracked
config.

## Eval sets

A minimal two-arm set:

```toml
[tool.benchspec]
default-set = "default"

[tool.benchspec.sets.default]
harness  = "claude-code"
model    = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial" },
]
```

Set-level keys are defaults every arm inherits; an arm overrides only what
differs. The full key set for `[tool.benchspec.sets.<name>]`:

| Key | Type | Notes |
|---|---|---|
| `arms` | array of tables | Required, non-empty. Each entry is one arm: `name` (required, unique in the set) plus any of `harness` / `model` / `effort` / `env` / `harness_args`. |
| `harness` | string | Default harness: `claude-code`, `codex`, or `opencode`. An arm with no harness (own or inherited) fails at config-read time. Arms may span harnesses within one set. |
| `model` | string | Default **task** model. Harness-specific: Claude Code takes aliases (`sonnet`, `opus`, `haiku`); OpenCode takes provider-qualified names (`anthropic/claude-sonnet-4-6`); Codex takes what `codex exec -m` accepts. Not validated by benchspec; a bad value fails loudly from the agent CLI. |
| `effort` | string | Default reasoning effort (default `medium`). Passed through to the harness unvalidated; see [`harnesses.md`](harnesses.md) for how each CLI receives it. |
| `env` | table of strings | Default environment injected into each cell's `setup.sh` **and** the agent invocation. Arm `env` shallow-merges over it (arm keys win). |
| `harness_args` | array of strings | Raw CLI tokens appended to the harness invocation. Arm-level args append *after* set-level args. Flags benchspec owns (model, effort, prompt delivery, output format, session and permission controls) are reserved and rejected by the adapter. |
| `baseline` | string | The arm every other arm's delta is measured against. Optional: without it, arms report absolute rates and no delta. Must name a declared arm. |
| `runner` | string | The test harness driving the set. `pytest` is the only supported value (and the default); anything else fails fast with exit `2`. |
| `sandbox` | string | The sandbox backend. `microsandbox` is the only implementation (and the default); `docker` is recognized but fails fast as not implemented. |

> **Key concept:** `runner` names the *test harness*, not the agent;
> agents are chosen per arm via `harness`. Both `runner` and `sandbox` are
> set-level only: no arm override, no CLI override.

Validation is structural and fail-fast: unknown harnesses, duplicate arm names, a
`baseline` or `default-set` naming an undeclared thing, a non-table `env`. Each
raises at config-read time (a pytest `UsageError` at collection, exit `2` from
the CLI), never a silent no-op mid-run. What benchspec deliberately does *not*
validate is harness × model semantics; models change too fast for an allow-list,
so a wrong model surfaces as a loud error from the agent CLI.

### Arm `env` and `$VAR` expansion

An `env` value of the form `$VAR` or `${VAR}` expands from the host environment
when the arm *executes*, never at collection, and an unset referenced variable
raises rather than expanding to an empty string. Literals pass through. This is
how an arm borrows a host secret without committing it:

```toml
arms = [
  { name = "via-openrouter", env = {
      ANTHROPIC_BASE_URL = "https://openrouter.ai/api/v1",
      ANTHROPIC_AUTH_TOKEN = "$OPENROUTER_API_KEY" } },
]
```

### Common set shapes

The arms are the experiment design. The recurring shapes:

- **Capability lift**: `baseline` installs nothing, `trial` installs the skill;
  same harness, model, and workspace. The delta is what the skill taught.
- **Harness comparison**: one arm per agent CLI on the same task.
- **Model or effort comparison**: same harness, arms differing only in `model`
  or `effort` (or use `--models` for a file-free sweep).
- **Environment comparison**: arms differing only in `env`, with `setup.sh`
  branching on what it finds.

## The judge

One run resolves exactly one judge: the host-side harness and model that grades
every punted assertion, deliberately independent of the task arms so the grader
is constant across the matrix. Declare it top-level, never per set:

```toml
[tool.benchspec.judge]
harness = "codex"
model   = "gpt-5.5"
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `harness` | string | `claude-code` | `claude-code`, `codex`, or `opencode`. Validated at collection. |
| `model` | string | `sonnet` | Harness-specific, like an arm's model. One structural check: an `opencode` judge needs a provider-qualified model (`anthropic/...`), enforced at collection. |
| `effort` | string | `medium` | Passed to the judge harness the same way an arm's effort is: `--effort` for Claude Code, `-c model_reasoning_effort=` for Codex, a `--variant` mapping for OpenCode. |
| `timeout` | integer | `300` | Judge subprocess timeout, seconds. |
| `harness_args` | array of strings | `[]` | Pass-through CLI tokens; benchspec-owned flags are rejected per harness. |
| `env` | table | `{}` | Judge-only env, `$VAR`-expanded at judge execution time by the same rule as arm env. |

> **Best practice:** judge cross-family. An Anthropic judge grading Anthropic
> arms can favor its own family's outputs. The default (`claude-code`/`sonnet`)
> is fine for iterating, but for a benchmark you publish, pick a judge from a
> different model family than the arms, as this repo's own `e2e` set does with a
> Codex judge over Claude arms.

The judge harness's CLI must be installed on the host with valid credentials; the
binary is preflighted when tests actually execute, not at collection.

## Top-level `[tool.benchspec]` keys

| Key | Type | Notes |
|---|---|---|
| `default-set` | string | The set a plain run resolves. Required once any set is declared; must name a declared set. |
| `sets.<name>` | table | One eval set (above). |
| `judge` | table | The run's judge (above). |
| `eval_paths` | array of strings | Discovery search paths, relative to the repo root (default `skills`, `tests`, `evals`, `benchmarks`). See [`writing-evals.md`](writing-evals.md#discovery). |
| `base_image` | string | OCI image the sandbox snapshot builds from (default `ubuntu:latest`). Must be an apt-family image with glibc. Changing it rebuilds the snapshot. See [`sandbox.md`](sandbox.md). |
| `environment_script` | string | Repo-relative shell script baked into the snapshot after the agent installs: the escape hatch for extra tools. Content-hashed into the cache identity; a missing file fails at config-read time. |
| `opencode_version` | string | Default OpenCode version pin when `BENCHSPEC_OPENCODE_VERSION` is unset. |

## The command line

`benchspec` has five subcommands. `run` and `sandbox:build` follow the full
exit-code contract below; `lint` and `analyze` never exit `5`: an empty root just
reports zero findings or classifications and exits `0`. `sandbox:clean` only
exits `0` (nothing to prune is success) or `2` on a malformed invocation.

```bash
benchspec lint [root]            # static assertion checks — no credentials, no sandbox
benchspec analyze [root]         # classify assertions deterministic / judge-backed
benchspec run [root] [flags] [-- pytest-args]
benchspec sandbox:build [--set S] [--config F] [root]
benchspec sandbox:clean [root]   # prune eval sandboxes + snapshots; stops running ones
```

### `benchspec run`

`run` resolves one set, runs its `(eval × arm)` cells as parametrized pytest
tests, grades, and reports. Each flag forwards to a `--benchspec-*` pytest
plugin option, so the same knobs work when driving pytest directly:

| Flag | Forwards to | Effect |
|---|---|---|
| `--set NAME` | `--benchspec-set` | Run this set instead of `default-set`. Unknown names fail fast. |
| `--config FILE` | `--benchspec-config` | Layer an untracked TOML's `[tool.benchspec.sets.*]` (and `judge`) over pyproject: a scratch set for a one-off comparison. Same table shape as pyproject. |
| `--model M` | `--benchspec-model` | Override the set's `model` default. Arms that declared their own model keep it. |
| `--models M1,M2` | `--benchspec-models` | Sweep: replace the set's arms with one arm per model (named after it), all inheriting the set defaults; the first model becomes the baseline. |
| `--harness H` | `--benchspec-harness` | Override the set's `harness` default. |
| `--effort E` | `--benchspec-effort` | Override the set's `effort` default. |
| `--env K=V` | `--benchspec-env` | Add or override a set-default env entry (repeatable; `$VAR` expands at execution). |
| `--eval-paths P1,P2` | `--benchspec-eval-paths` | Override discovery search paths. |
| `--fail-under PP` | `--benchspec-fail-under` | CI gate (below). |
| `--judge-harness` / `--judge-model` / `--judge-effort` | `--benchspec-judge-*` | Per-run judge overrides. |

Everything after a standalone `--` passes to pytest verbatim:

```bash
benchspec run -- -k greets-by-name     # one eval (substring match on the test id)
benchspec run -- -n 8                  # fan cells across 8 microVMs (pytest-xdist)
benchspec run -- --count 5             # 5 samples per cell (pytest-repeat)
benchspec run -- --collect-only -q     # list the cells without running
```

A few plugin options have no curated `run` flag and are reached the same way:
`--benchspec-judge-timeout`, `--benchspec-judge-env K=V`,
`--benchspec-judge-harness-arg` (which, unlike set/arm `harness_args`, fully
*replaces* the configured judge `harness_args` when given), and
`--benchspec-repo-root`.

### The `--fail-under` gate

`--fail-under PP` turns the run into a CI gate: any non-baseline arm whose delta
falls below the threshold fails the run with exit `1`. The gate is **per group**
even though the report is pooled, so a regression in one group is never averaged
away by another group's win. `--fail-under 0` means "every arm must at least
match baseline in every group". The gate uses the raw delta; check the
`within noise` label in `benchmark.md` before trusting small numbers
([`results.md`](results.md#noise-samples-and-flakiness)). Groups with no
computable delta are exempt.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A finding: lint warnings, a failed cell, a tripped `--fail-under` gate, or a sandbox build failure. |
| `2` | Usage error, caught before any paid arm runs: a bad flag, unknown `--set`, unreadable `--config`, unsupported `runner`/`sandbox`, or a failed host preflight. |
| `5` | Nothing to do: no evals discovered under `root` (`run` only; `lint`/`analyze` exit `0` on an empty root). |

> **Edge case:** `run` checks for emptiness *first*. With no evals discovered it
> exits `5` before validating the set, so a bad `--set` against an empty root
> reports `5`, not `2`. The exit-`2` contract holds for a populated repo.

## Precedence

Every knob that exists in multiple places resolves through the same chain, once,
at configure time:

```
CLI flag  >  environment variable  >  pyproject.toml  >  built-in default
```

Judge knobs insert the scratch file into that chain: CLI flag > `--config` file's
`[tool.benchspec.judge]` > pyproject's > built-in default. Not every knob has
every channel: `eval_paths` has no env var, and `--fail-under` is flag-only by
design (it is a CI decision, made where CI is configured).

## Environment variables

Host-side configuration. Every subcommand loads a `.env` found by walking up
from the current directory before it reads any of these; exported variables win
over `.env` values. Nothing in `.env` reaches the
guest (see [`sandbox.md`](sandbox.md#credentials)).

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | The binder. Required for every graded run and for `analyze`; empty counts as missing. |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude Code credential (OAuth token preferred; from `claude setup-token`). |
| `CODEX_API_KEY` / `CODEX_ACCESS_TOKEN` / `CODEX_AUTH_JSON_PATH` | Codex credential, in preference order; see [`harnesses.md`](harnesses.md). |
| `OPENROUTER_API_KEY` | OpenCode's preferred provider credential (falls back to `ANTHROPIC_API_KEY`, then `GEMINI_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`). |
| `BENCHSPEC_CLAUDE_VERSION` / `BENCHSPEC_CODEX_VERSION` / `BENCHSPEC_OPENCODE_VERSION` | Select a harness CLI version instead of `latest`; each value keys its own sandbox snapshot. Codex and OpenCode install exactly that version; the Claude Code installer always fetches the latest release, so its value only names the snapshot (the version that ran is recorded in `meta.json`). |
| `PROJECT_ROOT` | Repo-root override (below `--benchspec-repo-root`, above the pytest rootdir). |

Inside the sandbox, each cell's `setup.sh` additionally sees `BENCHSPEC_ARM`,
`BENCHSPEC_MODEL`, `BENCHSPEC_HARNESS`, and `BENCHSPEC_SET`, described in
[`writing-evals.md`](writing-evals.md#setupsh-what-differs-per-arm).

The authoritative parsers are `benchspec.config.arms` (sets and arms),
`benchspec.grading.judges.config` (the judge), `benchspec.runners.pytest`
(plugin options), and `benchspec.exit_codes`. If this page and those modules
ever disagree, the modules are right.
