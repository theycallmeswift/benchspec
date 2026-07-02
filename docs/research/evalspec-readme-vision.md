# evalspec

evalspec is a framework for evaluating AI agents with repeatable, isolated benchmarks.
Write evals as Markdown, run each eval across named benchmark arms, and compare how
agent behavior changes by harness, model, effort, and environment.

> evalspec is like a test runner for agent behavior.

## What it measures

A single pass rate is just a number. evalspec is built around comparisons: the same
eval runs across a benchmark's arms, then reports each arm's assertion pass rate. When
a benchmark names a baseline arm, every other cell also shows the relative difference
from that baseline in percentage points.

```text
benchmark
  runner:  pytest
  sandbox: microsandbox

  evals                      baseline             trial
  ------------------------   ------------------   ------------------
  archive-source             67%                  100% (+33pp)
  summarize-long-transcript  50%                  83% (+33pp)
  choose-right-capability    100%                 75% (-25pp)
  ------------------------   ------------------   ------------------
  All evals                  72%                  86% (+14pp)
```

The matrix is the primary output contract: evals are rows, arms are columns. If a
baseline is configured, it is always the second column after the eval name. Cells show
the percent of assertions met; non-baseline cells also include the relative change from
baseline.

## Core concepts

- **Runner** - The test runner that discovers and executes evals, such as PyTest,
  Jest, or RSpec.
- **Sandbox** - The isolated environment an eval runs within, such as microsandbox,
  Docker, or exe.dev.
- **Harness** - The agent interface under test: Claude Code, Codex, OpenCode,
  Antigravity, Cursor, Copilot, or another agent runner.
- **Model** - The task model used by the harness.
- **Effort** - The reasoning budget or effort tier passed to the harness.
- **Environment** - The agent context surface for an arm: skills, hooks, context
  files, plugins, environment variables, setup differences, and other controlled
  context.
- **Eval** - A structured task: prompt, optional chat history, and assertions.
- **Arm** - A benchmark column: a named harness + model + effort + environment
  configuration.
- **Benchmark** - One or more evals run against multiple arms.
- **Baseline** - A special arm used as the reference for percentage-point deltas.
- **Eval set** - A named benchmark configuration: runner, sandbox, arms, and optional
  baseline. One eval set can be marked as the default.

Runner and sandbox belong to the benchmark configuration. Arms vary the agent-facing
dimensions: harness, model, effort, and environment.

## Capability matrix

| Dimension | First-class concept | Current target |
|---|---:|---|
| Runner | Yes | PyTest; designed to support other runners such as Jest and RSpec |
| Sandbox | Yes | microsandbox; designed to support other sandboxes such as Docker and exe.dev |
| Harness | Yes | `claude-code`, `codex`, `opencode`; more harnesses can be added |
| Model | Yes | Harness-specific model names and aliases |
| Effort | Yes | Harness-specific effort or reasoning settings |
| Environment | Yes | Skills, hooks, context files, plugins, env vars, and setup differences |
| Assertions | Yes | Prose checklist assertions bound to deterministic checks or judged semantically |
| Artifacts | Yes | Machine-readable run metadata and per-sample results |
| CLI | Yes | `evalspec lint`, `evalspec analyze`, and related authoring tools |

## Eval layout

An eval can live under any `evals/` directory:

```text
**/*/evals/
  summarize-transcript/
    workspace/
      transcript.md
    setup.sh
    eval.md

  to-spec-activation/
    write-spec.eval.md
    prd-migration.eval.md
    ingest-near-miss.eval.md
```

evalspec discovers `eval.md` and `*.eval.md` files. Use `eval.md` for the common
single-eval folder. Use sibling `*.eval.md` files when a folder contains a compact
suite of related cases, such as activation probes.

- **`workspace/`** - Files copied into the sandbox workspace before the agent runs.
- **`setup.sh`** - Optional sandbox setup script. It can read evalspec-provided
  variables such as the selected arm, model, harness, and eval set.
- **`eval.md` / `*.eval.md`** - The eval definition: prompt, assertions, and optional
  chat history.

## Eval format

```markdown
---
history:
  - role: user
    content: Here is the transcript we captured yesterday.
  - role: assistant
    content: Got it. Say the word and I will summarize it.
---

## Prompt

Summarize `./transcript.md` into a concise executive brief.

## Assertions

- [ ] A file exists at `./brief.md`
- [ ] The brief names the three decisions from the transcript
- [ ] The brief separates open questions from completed decisions
- [ ] Capability `summarize` activated
```

Assertions are plain prose checklist items. Authors do not write checker syntax. At
grade time, evalspec binds assertions to deterministic checks when it can do so
confidently; otherwise, it asks the judge model to grade the assertion from the
collected facts.

The judge grades from evidence, not recall: the agent's final message, workspace file
tree, file contents, checksums, process facts, and activation facts.

## Activation evals

Activation evals are ordinary evals whose assertions focus on process facts: whether
the agent activated the expected capability, tool, skill, workflow, command, or context
bundle.

```markdown
## Prompt

Write up the design we landed on as a spec.

## Assertions

- [ ] Capability `to-spec` activated
- [ ] Capability `ingest` not activated
```

There is no separate trigger schema and no `type: activation`. Activation is just one
kind of assertion. An eval can assert only activation, only task output, or both.

## Benchmark configuration

A benchmark resolves one eval set. The set chooses the runner, sandbox, arms, and
baseline. Arms are the report columns. Eval sets can define defaults shared by every
arm, and each arm can override only the dimensions that differ.

```toml
[tool.evalspec]
default-set = "context-comparison"

[tool.evalspec.sets.context-comparison]
runner = "pytest"
sandbox = "microsandbox"
harness = "codex"
model = "gpt-5.5"
effort = "medium"
baseline = "minimal-context"
arms = [
  { name = "minimal-context", env = { CONTEXT_PROFILE = "minimal" } },
  { name = "full-context", env = { CONTEXT_PROFILE = "full" } },
  { name = "opencode-full-context", harness = "opencode", model = "anthropic/claude-sonnet-4-6", env = { CONTEXT_PROFILE = "full" } },
]
```

In this example, all arms inherit `runner`, `sandbox`, `harness`, `model`, and
`effort`. The `opencode-full-context` arm overrides `harness` and `model`; every arm
defines only its environment difference.

Common benchmark shapes:

- **Capability lift** - Compare with-capability and without-capability arms on the
  same harness, model, effort, and workspace.
- **Harness comparison** - Compare Claude Code, Codex, OpenCode, Antigravity, or other
  harnesses on the same task.
- **Model comparison** - Compare models from different families or power tiers under
  the same harness and environment.
- **Effort comparison** - Compare low, medium, and high reasoning effort for the same
  task.
- **Environment comparison** - Compare different context surfaces: with or without
  skills, hooks, plugins, instructions, workspace files, or setup.

## Output

Every run produces a benchmark table with evals as rows and arms as columns:

```text
eval                         baseline             full-context         opencode-full-context
--------------------------   ------------------   ------------------   ------------------
to-spec/write-spec           50%                  100% (+50pp)         75% (+25pp)
to-spec/prd-migration        75%                  100% (+25pp)         75% (+0pp)
to-spec/ingest-near-miss     100%                 100% (+0pp)          50% (-50pp)
--------------------------   ------------------   ------------------   ------------------
All evals                    75%                  100% (+25pp)         67% (-8pp)
```

Every benchmark table includes a final `All evals` row. It calculates each arm's pass
rate across all run evals in the table and, when a baseline is configured, the
percentage-point difference from that baseline.

When a baseline is configured:

- The baseline is the second column.
- Baseline cells show the absolute assertion pass rate.
- Other cells show absolute pass rate plus percentage-point delta from baseline.
- The final `All evals` row shows the aggregate pass rate and delta across every run
  eval.

When no baseline is configured, every arm reports only its absolute pass rate, including
the final `All evals` row.

Machine-readable artifacts include:

- **`meta.json`** - Run identity, resolved eval set, runner, sandbox, arms, models,
  harnesses, effort, redacted environment, and versions.
- **`index.jsonl`** - One row per eval sample and arm, suitable for aggregation.
- **Per-sample artifacts** - Transcript, grading results, timing, process facts, and
  workspace facts.

## Supported harnesses

evalspec's harness seam isolates agent-specific command construction, credentials,
stream parsing, activation detection, and sandbox provisioning. In-tree harnesses:

| Harness | Notes |
|---|---|
| `claude-code` | Claude Code CLI |
| `codex` | Codex CLI; Codex Cloud is out of scope |
| `opencode` | OpenCode CLI |

Adding a harness is an adapter change, not a rewrite of the runner, sandbox lifecycle,
assertion model, or benchmark report.

## Sandboxes

Sandboxes are built automatically on demand. The first run for a runner + sandbox +
harness + version combination builds or updates the sandbox image, then later runs reuse
the cached image. Users do not need to run a separate build command for the normal path.

evalspec also exposes an explicit build command for CI warmup, debugging, and offline
preparation:

```bash
evalspec sandbox:build
```

A sandbox build includes the base image, harness CLI installation, credential bridge,
skills or capability home bridge, and any configured environment setup needed before
per-eval `setup.sh` runs.

## CLI

The `evalspec` command is the authoring and maintenance surface around the runner:

```bash
evalspec lint
evalspec analyze
evalspec sandbox:build
evalspec run
```

- **`evalspec lint`** - Validate eval folders, Markdown shape, required sections,
  assertions, benchmark configuration, and references before spending sandbox or model
  time.
- **`evalspec analyze`** - Inspect assertions and report how evalspec expects to grade
  them: deterministic checker, process-fact activation check, or semantic judge. This
  helps authors tighten assertions before running a benchmark.
- **`evalspec sandbox:build`** - Prebuild or refresh the sandbox images a run would
  otherwise build lazily.
- **`evalspec run`** - Run the selected eval set through the configured runner. For
  pytest-backed projects this is equivalent to invoking pytest with evalspec enabled.

## Development

The project development loop uses a Makefile:

```bash
make test
make e2e
make lint
```

- **`make test`** - Fast, deterministic tests for parser, config, discovery, binder,
  report aggregation, CLI behavior, and harness adapters. These tests do not require
  live agent credentials or full sandbox runs.
- **`make e2e`** - End-to-end evalspec runs across representative configurations:
  baseline/trial arms, multi-harness sets, model or effort overrides, environment
  differences, activation assertions, sandbox setup, and artifact generation.
- **`make lint`** - Code and docs linting for the evalspec package itself.

End-to-end tests are the contract tests for the product shape described here: they
confirm that eval sets resolve correctly, sandboxes build or reuse as expected, evals
run inside isolation, reports include the matrix plus aggregate row, and CLI commands
surface useful failures.

## Why evalspec?

Most eval frameworks grade model outputs. evalspec grades agent behavior: what an agent
does inside an isolated workspace, which capability it activates, what files it changes,
which tools it uses, and whether the result satisfies task-specific assertions.

The important signal is comparative. A benchmark can show whether adding a skill helps,
whether a stronger model matters, whether a different harness changes behavior, whether
higher effort pays off, or whether a richer environment improves outcomes.

evalspec makes those comparisons explicit, repeatable, and inspectable.
