# Quickstart

This walkthrough takes you from an empty directory to a graded two-arm benchmark:
a `baseline` arm that runs the agent bare, a `trial` arm that installs a tiny
skill, and a `benchmark.md` showing the delta between them. It is written for
someone who has read the README and nothing else; the terms it uses (arm,
baseline, binder, judge, cell) are defined in [concepts.md](concepts.md).

## TL;DR

The whole walkthrough, for a reader who wants the commands first and the
explanation as needed:

```bash
mkdir hello-evals && cd hello-evals
python3 -m venv .venv && .venv/bin/pip install benchspec
printf 'GEMINI_API_KEY=...\nCLAUDE_CODE_OAUTH_TOKEN=...\n' > .env   # Prerequisites
# Steps 2–3: write skills/hello/SKILL.md, pyproject.toml, the eval, and setup.sh
.venv/bin/benchspec lint                    # free static checks
.venv/bin/benchspec run -- --collect-only   # confirm discovery, nothing spent
.venv/bin/benchspec run -- --count 3        # two cells × 3 samples, graded, reported
cat tmp/evals/iteration_01/benchmark.md
```

## Prerequisites

- **A running Docker daemon.** Any host works as long as `docker info` succeeds.
  (For stronger isolation, an Apple Silicon Mac or Linux with `/dev/kvm` can opt
  into microsandbox microVMs — see [`sandbox.md`](sandbox.md).)
- **Python 3.11+.**
- **Claude Code on `$PATH`** with a credential: run `claude setup-token` (sets
  `CLAUDE_CODE_OAUTH_TOKEN`) or export `ANTHROPIC_API_KEY`. This walkthrough
  uses Claude Code for both the arms and the judge.
- **`GEMINI_API_KEY`**, the binder's credential. It is required for every graded
  run and is unrelated to the agent credential above. (The binder, the judge, and
  the arms can each run through OpenRouter instead, on one `OPENROUTER_API_KEY`;
  see [`configuration.md`](configuration.md#providers). This walkthrough stays on
  the defaults.)

If with-versus-without on Claude Code is the whole question, none of this is
needed: `claude plugin eval` ships inside Claude Code and answers it on one
credential. The README's
[comparison](../README.md#benchspec-vs-the-alternatives) says when benchspec is
the step up.

Credentials can live in a repo-root `.env`; every subcommand loads it first, and
exported variables win over `.env` values. For this walkthrough that file is two
lines:

```bash
GEMINI_API_KEY=...             # the binder
CLAUDE_CODE_OAUTH_TOKEN=...    # from `claude setup-token`; or ANTHROPIC_API_KEY=...
```

The repo's [`.env.example`](../.env.example) lists every variable benchspec
reads, including the other harnesses' credentials. Nothing in `.env` reaches the
guest — the staged repo the guest sees has every dotenv file removed. If anything
is missing, preflight fails with one message naming every missing piece, before
any sandbox boots or any paid call is made.

## Step 1 — Create the project

```bash
mkdir hello-evals && cd hello-evals
python3 -m venv .venv
.venv/bin/pip install benchspec
```

## Step 2 — Write a skill and a benchmark

The thing under test is a skill: an instruction file the trial arm will install
and the baseline arm will not. Write `skills/hello/SKILL.md`:

```markdown
---
name: hello
description: Write a greeting file under a Greetings/ directory.
---

# Hello

When asked to greet someone, create `Greetings/<name>.md` under the current
working directory with one line: `Hello, <name>!`. Nothing more.
```

Then declare the benchmark in `pyproject.toml`. An eval set names its arms (the
report columns) and a `baseline` (the delta reference); set-level defaults keep
each arm to just a `name`:

```toml
[tool.benchspec]
default-set = "default"

[tool.benchspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial" },
]
```

## Step 3 — Write the eval

An eval is one self-contained Markdown file under a discovered search path
(`skills`, `tests`, `evals`, and `benchmarks` by default). Its parent folder is
the eval's **group**; a file named `<stem>.eval.md` gets the stem as its id.
Keep evals beside the skill they exercise rather than inside it, as
`evals/<skill>/*.eval.md`, so the skill folder holds only what `setup.sh`
installs. Create `evals/hello/greets-by-name.eval.md`:

```markdown
---
---

## Prompt

You are working in a workspace rooted at your current working directory.
Greet Alice by name.

## Assertions

- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'
- [ ] Skill `hello` invoked
```

The empty `---`/`---` frontmatter is required even when the eval has no
`history:`. Each `- [ ]` line is one plain-prose assertion. Keep the `./` anchor
on paths so they read as workspace facts (the linter warns otherwise).

> **Key concept:** the agent runs with its working directory set to `/workspace`,
> the clean room mounted into the sandbox. It writes there; the host grades the
> same directory afterward. Everything in the eval, prompt and assertions alike,
> speaks in `./`-relative paths.

The skill reaches the trial arm through the eval's own setup script, which runs
inside the sandbox before the prompt with `$BENCHSPEC_ARM` set to the arm's name.
Create `evals/hello/setup.sh`:

```bash
#!/usr/bin/env bash
set -e
if [ "$BENCHSPEC_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/benchspec/skills/hello
cp ../../skills/hello/SKILL.md /home/benchspec/skills/hello/SKILL.md
```

`/home/benchspec/skills` is the fixed skills home every harness's skill
directory is linked to, so the same `setup.sh` works whether the arm runs Claude
Code, Codex, or OpenCode. The script's working directory is the eval folder
itself, inside the read-only `/project` mount of your repo, which is why
`../../skills/hello/SKILL.md` resolves.

## Step 4 — Lint and preview

Two commands catch problems before you spend sandbox or model time:

```bash
.venv/bin/benchspec lint      # static: flags assertions the judge can't fairly grade
.venv/bin/benchspec analyze   # binds each assertion: deterministic or judge-backed?
```

`analyze` uses the real binder, so it needs the binder's credential, `GEMINI_API_KEY`
here (exported or in `.env`). To
confirm discovery without running anything, forward `--collect-only` to pytest:

```bash
.venv/bin/benchspec run -- --collect-only -q
```

You should see two collected items, one per arm, each filed under the eval file
that produced it:

```
evals/hello/greets-by-name.eval.md::test_eval[hello-greets-by-name-baseline]
evals/hello/greets-by-name.eval.md::test_eval[hello-greets-by-name-trial]
```

A malformed eval fails here, loudly, with the offending path quoted.

## Step 5 — Run

```bash
.venv/bin/benchspec run -- --count 3
```

The first run builds the sandbox snapshot (about a minute to download the base
image and install the agent CLI); later runs reuse it, or run
`benchspec sandbox:build` once to pay that cost up front. Both cells then run
three times each under their eval file's progress line, grade, and the session
ends with the benchmark table:

```
evals/hello/greets-by-name.eval.md ......                                 [100%]

============================= benchspec benchmark ==============================
Eval                  baseline   trial
hello/greets-by-name        0%    100%
--------------------------------------
All evals                   0%    100%
vs baseline                     +100pp
Report: tmp/evals/iteration_01/benchmark.md
```

On a color terminal, rates are color-coded by band (green from 80%, yellow from
50%, red below) and the `vs baseline` delta by sign.

Your numbers will differ: the baseline arm may guess the right shape, the trial
arm may miss an assertion. What matters is that both arms ran and the table
shows a delta rather than a lone score.

A plain `benchspec run` (no `--count`) takes one sample per cell, and one sample
yields a delta with no noise band. Such a run says so: a
`WARN samples: 1 per cell; deltas carry no noise band` line under the matrix,
and a `— single sample, no noise band` suffix on the `benchmark.md` headline.
Visibility only; the exit status is unchanged.

## What a run costs

- `lint` is free: no credentials, no network.
- `analyze` makes one Gemini call per assertion, except bare existence lines
  ("./x exists"), which bind locally for free.
- `run` boots one sandbox per sample (six here), makes the agent's model calls
  through the agent's provider (Anthropic here), one Gemini call per
  non-trivial assertion for the binder, and one judge call per cell that has at
  least one punted assertion, through the judge's provider (the default judge is
  Claude Code with `sonnet`, so this walkthrough pays Anthropic and Google only).

> **Why Gemini for the binder?** The binder is a fixed, cheap classifier call
> (`gemini-3.5-flash-lite`, temperature 0) that decides how an assertion is
> graded, not whether it passed. It is deliberately separate from both the agent
> under test and the judge, so the grading path is identical whatever harness or
> judge you configure. The same model is available through OpenRouter, which is
> what `[tool.benchspec.binder] provider = "openrouter"` selects.

## Step 6 — Look at what you got

```bash
cat tmp/evals/iteration_01/benchmark.md
```

Rendered, the report's headline and matrix for this walkthrough look like:

> **trial:** baseline 0% → trial 100% (**+100pp**) — noise band ±0pp

| Eval | baseline (claude-code) | trial (claude-code) |
|------|------|------|
| hello/greets-by-name | 0% | 100% (+100pp) |
| All evals | 0% | 100% (+100pp) |

The band is zero here because every sample agreed; a real run's band is wider,
and a delta inside it is labeled `within noise`. The full report continues with
per-arm detail (timing, tokens, per-eval rates) and a provenance section
recording which agent version and sandbox snapshot each arm ran on. Alongside
it:

- `meta.json`: the run manifest (identity, planned arms, observed provenance).
- `index.jsonl`: one row per `(eval × arm × sample)`, for aggregation.
- `skills/hello/eval-greets-by-name/<arm>/sample-<n>/`: per-sample artifacts
  (`grading.json`, `timing.json`, `transcript.json`, `provenance.json`, and the
  lossless `session.jsonl` stream when the harness produced output).

[`results.md`](results.md) walks the whole tree.

## Common first-run failures

- **Preflight: `docker CLI not found`.** Install Docker Engine or Docker
  Desktop, or set `BENCHSPEC_DOCKER_PATH` to the binary.
- **Preflight: `Docker daemon unreachable`.** Start Docker Desktop or the
  docker service.
- **Preflight: no Claude credential.** Run `claude setup-token` or export
  `ANTHROPIC_API_KEY`; a repo-root `.env` works too.
- **`GEMINI_API_KEY is required`.** The binder classifies every assertion via the
  Gemini API. An empty value counts as missing. (With `[tool.benchspec.binder]
  provider = "openrouter"` the message names `OPENROUTER_API_KEY` instead.)
- **The trial arm fails `` Skill `hello` invoked ``.** The skill was installed but
  the agent hand-rolled the task instead of dispatching it: a real routing
  finding, not an infra error. Sharpen the skill's `description` or the prompt.

The microsandbox opt-in (`sandbox = "microsandbox"`) adds two of its own:

- **Preflight: x86_64 macOS is unsupported.** microsandbox needs Apple Silicon or
  Linux with KVM.
- **Preflight: microsandbox runtime not installed.** Install the extra,
  `pip install "benchspec[microsandbox]"`, into the environment you run from.

## Where to go next

- The in-repo [`evals/e2e/hello/`](../evals/e2e/hello/) suite is this walkthrough
  as living code: three evals, three arms, authored `history:`, and a seeded
  `workspace/`. It runs with `make e2e`, which fans the cells across six
  sandboxes by default (`WORKERS=N` to change).
- [`writing-evals.md`](writing-evals.md): the full eval format and grading model.
- [`configuration.md`](configuration.md): multi-harness sets, model sweeps, the
  judge, and every flag.
