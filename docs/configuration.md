# Configuring benchmarks

This is the configuration reference: the `[tool.benchspec]` tables in
`pyproject.toml` (eval sets, arms, the judge, the binder), the `benchspec` command line,
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
| `arms` | array of tables | Required, non-empty. Each entry is one arm: `name` (required, unique in the set) plus any of `harness` / `provider` / `model` / `effort` / `timeout` / `env` / `harness_args`. |
| `harness` | string | Default harness: `claude-code`, `codex`, or `opencode`. An arm with no harness (own or inherited) fails at config-read time. Arms may span harnesses within one set. |
| `provider` | string | Default transport the harness reaches its model through: `default` (the vendor's own API or CLI login; the default) or `openrouter` (every request through OpenRouter on `OPENROUTER_API_KEY`). Under `openrouter` the model must be a vendor-qualified slug (`anthropic/claude-sonnet-4.6`; `openrouter/anthropic/...` for OpenCode). See [Providers](#providers). |
| `model` | string | Default **task** model. Harness-specific: Claude Code takes aliases (`sonnet`, `opus`, `haiku`); OpenCode takes provider-qualified names (`anthropic/claude-sonnet-4-6`); Codex takes what `codex exec -m` accepts. Not validated by benchspec; a bad value fails loudly from the agent CLI. |
| `effort` | string | Default reasoning effort (default `medium`). Passed through to the harness unvalidated; see [`harnesses.md`](harnesses.md) for how each CLI receives it. |
| `timeout` | integer | Default wall-clock cap on one graded agent turn, seconds (default `600`). A turn that exceeds it is recorded as an errored sample, never a failed one. Arm-level `timeout` overrides it. |
| `env` | table of strings | Default environment injected into each cell's `setup.sh` **and** the agent invocation. Arm `env` shallow-merges over it (arm keys win). |
| `harness_args` | array of strings | Raw CLI tokens appended to the harness invocation. Arm-level args append *after* set-level args. Flags benchspec owns (model, effort, prompt delivery, output format, session and permission controls) are reserved and rejected by the adapter. |
| `baseline` | string | The arm every other arm's delta is measured against. Optional: without it, arms report absolute rates and no delta. Must name a declared arm. |
| `runner` | string | The test harness driving the set. `pytest` is the only supported value (and the default); anything else fails fast with exit `2`. |
| `sandbox` | string | The sandbox backend: `docker` (the default) or `microsandbox`. Docker needs a reachable daemon; microsandbox needs Apple Silicon or Linux with KVM. See [`sandbox.md`](sandbox.md). |

> **Key concept:** `runner` names the *test harness*, not the agent;
> agents are chosen per arm via `harness`. Both `runner` and `sandbox` are
> set-level only: no arm override, no CLI override.

Validation is structural and fail-fast: unknown harnesses or providers, duplicate arm
names, a bare model alias under `openrouter`, a `baseline` or `default-set` naming an
undeclared thing, a non-table `env`. Each
raises at config-read time (a pytest `UsageError` at collection, exit `2` from
the CLI), never a silent no-op mid-run. What benchspec deliberately does *not*
validate is harness × model semantics; models change too fast for an allow-list,
so a wrong model surfaces as a loud error from the agent CLI.

### Arm `env` and `$VAR` expansion

An `env` value of the form `$VAR` or `${VAR}` expands from the host environment
when the arm *executes*, never at collection, and an unset referenced variable
raises rather than expanding to an empty string. Literals pass through. This is
how an arm borrows a host secret without committing it, for a gateway benchspec
has no `provider` for:

```toml
arms = [
  { name = "via-my-gateway", env = {
      ANTHROPIC_BASE_URL = "https://gateway.example.com/api",
      ANTHROPIC_AUTH_TOKEN = "$MY_GATEWAY_TOKEN" } },
]
```

Under Docker that token is a plain container variable the agent can read; for
OpenRouter, prefer `provider = "openrouter"` ([Providers](#providers)), which
injects the key as a host-scoped credential instead.

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
| `provider` | string | `default` | `default` (the harness vendor's own API or CLI login) or `openrouter` (grade through OpenRouter on `OPENROUTER_API_KEY`; no CLI login involved). See [Providers](#providers). |
| `model` | string | `sonnet` | Harness-specific, like an arm's model. Structural checks at collection: an `opencode` judge needs a provider-qualified model (`anthropic/...`), and any judge under `openrouter` needs a vendor-qualified slug (`openrouter/...` for OpenCode). |
| `effort` | string | `medium` | Passed to the judge harness the same way an arm's effort is: `--effort` for Claude Code, `-c model_reasoning_effort=` for Codex, a `--variant` mapping for OpenCode. |
| `timeout` | integer | `300` | Judge subprocess timeout, seconds. |
| `harness_args` | array of strings | `[]` | Pass-through CLI tokens; benchspec-owned flags are rejected per harness. |
| `env` | table | `{}` | Judge-only env, `$VAR`-expanded at judge execution time by the same rule as arm env. |

> **Best practice:** judge cross-family. An Anthropic judge grading Anthropic
> arms can favor its own family's outputs. The default (`claude-code`/`sonnet`)
> is fine for iterating, but for a benchmark you publish, pick a judge from a
> different model family than the arms, as this repo's own `e2e` set does with a
> Codex judge over Claude arms.

The judge harness's CLI must be installed on the host with its credential (the
harness's own under `default`, `OPENROUTER_API_KEY` under `openrouter`; see
[`harnesses.md`](harnesses.md#the-in-tree-harnesses)). The credential may live in
the host environment or in the judge's own `env` table; preflight evaluates the
same merged view the judge runs with. The `run` command preflights both before
spawning pytest; inside pytest they are checked again when tests actually
execute, not at collection. Funding is not preflighted: an unfunded key fails at
grading, after the arms have run.

## The binder

The binder is the fixed classifier that decides, per assertion, whether a line
grades deterministically or goes to the judge ([`writing-evals.md`](writing-evals.md#how-assertions-are-graded)).
It is not a harness: it is one direct API call per non-trivial assertion, to the
same Gemini Flash-Lite model whichever transport carries it. Declare it
top-level:

```toml
[tool.benchspec.binder]
provider = "openrouter"                     # default "gemini"
model    = "google/gemini-3.5-flash-lite"   # optional; each provider has a default
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `provider` | string | `gemini` | `gemini` (Gemini's own API on `GEMINI_API_KEY`) or `openrouter` (OpenRouter's chat-completions API on `OPENROUTER_API_KEY`, with `temperature: 0`, JSON mode, and `require_parameters` so a route that would drop either is refused). |
| `model` | string | per provider | `gemini-3.5-flash-lite` under `gemini`, `google/gemini-3.5-flash-lite` under `openrouter`. Under `openrouter` the slug must be vendor-qualified. Recorded in `meta.json` under `binder.model`. |

A rejected or unfunded key stops the run outright on either provider (never a
silent degrade to judge grading); a transient failure reroutes that assertion to
the judge and is reported as `binder_degraded`.

## Providers

`provider` appears on three surfaces, each chosen independently: the binder
(`gemini` | `openrouter`), the judge, and every arm (`default` | `openrouter`).
`default` is today's behavior, the vendor's own API or CLI login. `openrouter`
routes that component through OpenRouter on `OPENROUTER_API_KEY`, applied at
execution time only (the guest env, a per-cell credential scoped to
`openrouter.ai`, or CLI flags), so snapshots stay provider-neutral and a mixed
set builds no extra images. Set all three and a run needs exactly one credential:

```toml
[tool.benchspec.binder]
provider = "openrouter"

[tool.benchspec.judge]
harness  = "codex"
provider = "openrouter"
model    = "google/gemini-3.5-flash"

[tool.benchspec.sets.single-key]
harness  = "claude-code"
provider = "openrouter"
model    = "anthropic/claude-sonnet-4.6"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial" },
  { name = "direct", provider = "default", model = "sonnet" },   # mixed sets are fine
]
```

```bash
OPENROUTER_API_KEY=sk-or-... benchspec run --set single-key   # the only credential read
benchspec run --set single-key                                # preflight names it once per component, exit 2
```

Per harness, `openrouter` means: Claude Code gets `ANTHROPIC_BASE_URL=https://openrouter.ai/api`,
an empty `ANTHROPIC_API_KEY`, and the key as `ANTHROPIC_AUTH_TOKEN`; Codex gets
benchspec-owned `-c model_provider="openrouter"` and `model_providers.openrouter.*`
overrides (no `config.toml` written anywhere); OpenCode takes the key directly
and needs an `openrouter/`-prefixed model. Bare aliases (`sonnet`, `opus`) are a
config error under `openrouter`: OpenRouter does not map them, so every model
must be a vendor-qualified slug. `meta.json` records `provider` on the binder,
the judge, and every arm. Cross-family judging still applies: this repo's
`make e2e` puts Anthropic and OpenAI arms under a Codex judge on a Google slug.

## Top-level `[tool.benchspec]` keys

| Key | Type | Notes |
|---|---|---|
| `default-set` | string | The set a plain run resolves. Required once any set is declared; must name a declared set. |
| `sets.<name>` | table | One eval set (above). |
| `judge` | table | The run's judge (above). |
| `binder` | table | The run's binder (above). |
| `eval_paths` | array of strings | Discovery search paths, relative to the repo root (default `skills`, `tests`, `evals`, `benchmarks`). See [`writing-evals.md`](writing-evals.md#discovery). |
| `base_image` | string | OCI image the sandbox snapshot builds from (default `ubuntu:latest`). Must be an apt-family image with glibc. Changing it rebuilds the snapshot. See [`sandbox.md`](sandbox.md). |
| `environment_script` | string | Repo-relative shell script baked into the snapshot after the agent installs: the escape hatch for extra tools. Content-hashed into the cache identity; a missing file fails at config-read time. |
| `opencode_version` | string | Default OpenCode version pin when `BENCHSPEC_OPENCODE_VERSION` is unset. |

## The command line

`benchspec` has five subcommands. `run` and `sandbox:build` follow the full
exit-code contract below; `lint` and `analyze` never exit `5`: an empty root just
reports zero findings or classifications and exits `0`. `sandbox:clean` only
exits `0` (nothing to prune is success) or `2` on a malformed invocation.

Every subcommand preflights what it needs before spending anything: `analyze`
the binder credential; `sandbox:build` the host and agent credential; `run` all
of those plus the judge binary and the resolved set, before pytest spawns. A
failed preflight is a single `error: ...` line on stderr and exit `2`.
Forwarding `--collect-only` to `run` skips the environment preflight, since
nothing paid follows.

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
| `--config FILE` | `--benchspec-config` | Layer an untracked TOML's `[tool.benchspec.sets.*]` (and `judge` / `binder`) over pyproject: a scratch set for a one-off comparison. Same table shape as pyproject. |
| `--model M` | `--benchspec-model` | Override the set's `model` default. Arms that declared their own model keep it. |
| `--models M1,M2` | `--benchspec-models` | Sweep: replace the set's arms with one arm per model (named after it), all inheriting the set defaults; the first model becomes the baseline. |
| `--harness H` | `--benchspec-harness` | Override the set's `harness` default. |
| `--effort E` | `--benchspec-effort` | Override the set's `effort` default. |
| `--timeout S` | `--benchspec-timeout` | Override the set's `timeout` default (seconds). Arms that declared their own keep it. |
| `--env K=V` | `--benchspec-env` | Add or override a set-default env entry (repeatable; `$VAR` expands at execution). |
| `--eval-paths P1,P2` | `--benchspec-eval-paths` | Override discovery search paths. |
| `--fail-under PP` | `--benchspec-fail-under` | CI gate (below). |
| `--judge-harness` / `--judge-provider` / `--judge-model` / `--judge-effort` | `--benchspec-judge-*` | Per-run judge overrides. |
| `--binder-provider` / `--binder-model` | `--benchspec-binder-*` | Per-run binder overrides. |

Everything after a standalone `--` passes to pytest verbatim:

```bash
benchspec run -- --count 3             # 3 samples per cell (pytest-repeat) so deltas carry a noise band
benchspec run -- -n 8                  # fan cells across 8 sandboxes (pytest-xdist)
benchspec run -- -k greets-by-name     # one eval (substring match on the test id)
benchspec run -- --collect-only -q     # list the cells without running
```

The in-repo `make e2e` and `make evals` targets pass `-n $(WORKERS)` (default
6; each e2e worker reserves a 2 GB sandbox), and `EVAL_ARGS` appends further
pytest arguments after it, so `make e2e WORKERS=1` or `EVAL_ARGS="-n 1"`
restores a sequential run.

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
match baseline in every group":

```bash
benchspec run --fail-under 0 -- --count 3 -n 6
```

The gate uses the raw delta, and a banded (multi-sample) run is the
precondition for reading it: a single-sample gate on a three-assertion eval
moves in 33pp steps and flaps. Before trusting small numbers, read the run's
own flags: a one-sample run prints `WARN samples:` under the terminal matrix
and its `benchmark.md` headline says `single sample, no noise band`; a banded
run labels a delta `within noise` when it sits inside its band
([`results.md`](results.md#noise-samples-and-flakiness)). Groups with no
computable delta are exempt.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A finding: lint warnings, a failed cell, a tripped `--fail-under` gate, or a sandbox build failure. |
| `2` | Usage error, caught before any paid arm runs: a bad flag, unknown `--set`, unreadable `--config`, unsupported `runner`/`sandbox`, an unknown `provider`, or a failed preflight: an unready host, a missing agent credential, a missing judge binary, a missing or empty binder key (`GEMINI_API_KEY` or `OPENROUTER_API_KEY`, by provider), or (`analyze` only) a rejected key or binder transport failure. Printed as a single `error: ...` line on stderr. |
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

Judge and binder knobs insert the scratch file into that chain: CLI flag > `--config`
file's `[tool.benchspec.judge]` / `[tool.benchspec.binder]` > pyproject's > built-in
default. Not every knob has
every channel: `eval_paths` has no env var, and `--fail-under` is flag-only by
design (it is a CI decision, made where CI is configured).

## Environment variables

Host-side configuration. Every subcommand loads a `.env` found by walking up
from the current directory before it reads any of these; exported variables win
over `.env` values. Nothing in `.env` reaches the
guest (see [`sandbox.md`](sandbox.md#credentials)).

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | The binder under its default `gemini` provider. Required for every graded run and for `analyze` unless the binder is on `openrouter`; empty counts as missing. |
| `OPENROUTER_API_KEY` | Every component set to `provider = "openrouter"`: the binder, the judge, and any arm, on any harness. With all three set, the only credential a run reads. Under `default` it is also OpenCode's preferred credential (falling back to `ANTHROPIC_API_KEY`, then `GEMINI_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`). |
| `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude Code credential under `default` (OAuth token preferred; from `claude setup-token`). |
| `CODEX_API_KEY` / `CODEX_ACCESS_TOKEN` / `OPENAI_API_KEY` / `CODEX_AUTH_JSON_PATH` | Codex credential under `default`, in preference order; see [`harnesses.md`](harnesses.md). |
| `BENCHSPEC_CLAUDE_VERSION` / `BENCHSPEC_CODEX_VERSION` / `BENCHSPEC_OPENCODE_VERSION` | Select a harness CLI version instead of `latest`; each value keys its own sandbox snapshot. Codex and OpenCode install exactly that version; the Claude Code installer always fetches the latest release, so its value only names the snapshot (the version that ran is recorded in `meta.json`). |
| `BENCHSPEC_BASE_IMAGE` | OCI image the sandbox snapshot builds from, above `[tool.benchspec] base_image`. For a host whose base needs something the config shouldn't carry, such as a proxy CA; see [`sandbox.md`](sandbox.md#customizing-the-image). |
| `PROJECT_ROOT` | Repo-root override (below `--benchspec-repo-root`, above the pytest rootdir). |

Inside the sandbox, each cell's `setup.sh` additionally sees `BENCHSPEC_ARM`,
`BENCHSPEC_MODEL`, `BENCHSPEC_HARNESS`, and `BENCHSPEC_SET`, described in
[`writing-evals.md`](writing-evals.md#setupsh-what-differs-per-arm).

The authoritative parsers are `benchspec.config.arms` (sets and arms),
`benchspec.grading.judges.config` (the judge), `benchspec.grading.binder_config`
(the binder), `benchspec.runners.pytest` (plugin options), and
`benchspec.exit_codes`. If this page and those modules ever disagree, the
modules are right.
