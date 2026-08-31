# Quickstart

This walkthrough takes you from an empty directory to a graded two-arm benchmark:
a `baseline` arm that runs the agent bare, a `trial` arm that installs a tiny
skill, and a `benchmark.md` showing the delta between them. It is written for
someone who has read the README and nothing else; the terms it uses (arm,
baseline, binder, judge, cell) are defined in [concepts.md](concepts.md).

## Prerequisites

- **An Apple Silicon Mac or Linux with `/dev/kvm`.** Evals run in microsandbox
  microVMs; x86_64 macOS is unsupported.
- **Python 3.11+.**
- **Claude Code on `$PATH`** with a credential: run `claude setup-token` (sets
  `CLAUDE_CODE_OAUTH_TOKEN`) or export `ANTHROPIC_API_KEY`. This walkthrough
  uses Claude Code for both the arms and the judge.
- **`GEMINI_API_KEY`**, the binder's credential. It is required for every graded
  run and is unrelated to the agent credential above.

Credentials can live in a repo-root `.env`; every subcommand loads it before
doing anything else, and variables you export win over `.env` values. Whatever
is in `.env` stays on the host: it is never copied
into the guest, and the staged repo the guest sees has every dotenv file
removed. If anything is missing, preflight fails with a remediation message
before any VM boots or any paid call is made.

## Step 1 — Create the project

```bash
mkdir hello-evals && cd hello-evals
python3 -m venv .venv
.venv/bin/pip install "harnessbench[microsandbox]"
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
[tool.harnessbench]
default-set = "default"

[tool.harnessbench.sets.default]
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
Create `skills/hello/evals/hello/greets-by-name.eval.md`:

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
> the clean room mounted into the microVM. It writes there; the host grades the
> same directory afterward. Everything in the eval, prompt and assertions alike,
> speaks in `./`-relative paths.

The skill reaches the trial arm through the eval's own setup script, which runs
inside the VM before the prompt with `$HARNESSBENCH_ARM` set to the arm's name.
Create `skills/hello/evals/hello/setup.sh`:

```bash
#!/usr/bin/env bash
set -e
if [ "$HARNESSBENCH_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/harnessbench/skills/hello
cp ../../SKILL.md /home/harnessbench/skills/hello/SKILL.md
```

`/home/harnessbench/skills` is the fixed skills home every harness's skill
directory is linked to, so the same `setup.sh` works whether the arm runs Claude
Code, Codex, or OpenCode. The script's working directory is the eval folder
itself, inside the read-only `/project` mount of your repo, which is why
`../../SKILL.md` resolves.

## Step 4 — Lint and preview

Two commands catch problems before you spend sandbox or model time:

```bash
.venv/bin/harnessbench lint      # static: flags assertions the judge can't fairly grade
.venv/bin/harnessbench analyze   # binds each assertion: deterministic or judge-backed?
```

`analyze` uses the real binder, so it needs `GEMINI_API_KEY` (exported or in `.env`). To
confirm discovery without running anything, forward `--collect-only` to pytest:

```bash
.venv/bin/harnessbench run -- --collect-only -q
```

You should see two collected items, one per arm, each filed under the eval file
that produced it:

```
skills/hello/evals/hello/greets-by-name.eval.md::test_eval[hello-greets-by-name-baseline]
skills/hello/evals/hello/greets-by-name.eval.md::test_eval[hello-greets-by-name-trial]
```

A malformed eval fails here, loudly, with the offending path quoted.

## Step 5 — Run

```bash
.venv/bin/harnessbench run
```

The first run builds the sandbox snapshot (a few minutes to download the base
image and install the agent CLI); later runs reuse it, or run
`harnessbench sandbox:build` once to pay that cost up front. Both cells then run
under their eval file's progress line, grade, and the session ends with the
benchmark table:

```
skills/hello/evals/hello/greets-by-name.eval.md ..                       [100%]

============================ harnessbench benchmark ============================
Eval                  baseline          trial
hello/greets-by-name        0%  100% (+100pp)
All evals                   0%  100% (+100pp)
Report: tmp/evals/iteration_01/benchmark.md
```

Your numbers will differ: the baseline arm may guess the right shape, the trial
arm may miss an assertion. What matters is that both arms ran and the table
shows a delta rather than a lone score.

## What a run costs

- `lint` is free: no credentials, no network.
- `analyze` makes one Gemini call per assertion, except bare existence lines
  ("./x exists"), which bind locally for free.
- `run` boots one microVM per cell (two here), makes the agent's model calls
  through the agent's provider (Anthropic here), one Gemini call per
  non-trivial assertion for the binder, and one judge call per cell that has at
  least one punted assertion, through the judge's provider (the default judge is
  Claude Code with `sonnet`, so this walkthrough pays Anthropic and Google only).

> **Why Gemini for the binder?** The binder is a fixed, cheap classifier call
> (`gemini-3.5-flash-lite`, temperature 0) that decides how an assertion is
> graded, not whether it passed. It is deliberately separate from both the agent
> under test and the judge, so the grading path is identical whatever harness or
> judge you configure.

## Step 6 — Look at what you got

```bash
cat tmp/evals/iteration_01/benchmark.md
```

Rendered, the report's headline and matrix for this walkthrough look like:

> **trial:** baseline 0% → trial 100% (**+100pp**)

| Eval | baseline (claude-code) | trial (claude-code) |
|------|------|------|
| hello/greets-by-name | 0% | 100% (+100pp) |
| All evals | 0% | 100% (+100pp) |

The full report continues with per-arm detail (timing, tokens, per-eval rates)
and a provenance section recording which agent version and sandbox snapshot
each arm ran on. Alongside it:

- `meta.json`: the run manifest (identity, planned arms, observed provenance).
- `index.jsonl`: one row per `(eval × arm × sample)`, for aggregation.
- `skills/hello/eval-greets-by-name/<arm>/sample-0/`: per-sample artifacts
  (`grading.json`, `timing.json`, `transcript.json`, `provenance.json`, and the
  lossless `session.jsonl` stream when the harness produced output).

[`results.md`](results.md) walks the whole tree.

## Common first-run failures

- **Preflight: x86_64 macOS is unsupported.** microsandbox needs Apple Silicon or
  Linux with KVM.
- **Preflight: microsandbox runtime not installed.** Install the sandbox extra,
  `pip install "harnessbench[microsandbox]"`, into the environment you run from.
- **Preflight: no Claude credential.** Run `claude setup-token` or export
  `ANTHROPIC_API_KEY`; a repo-root `.env` works too.
- **`GEMINI_API_KEY is required`.** The binder classifies every assertion via the
  Gemini API. An empty value counts as missing.
- **The trial arm fails `` Skill `hello` invoked ``.** The skill was installed but
  the agent hand-rolled the task instead of dispatching it: a real routing
  finding, not an infra error. Sharpen the skill's `description` or the prompt.

## Where to go next

- The in-repo [`evals/e2e/hello/`](../evals/e2e/hello/) suite is this walkthrough
  as living code: two evals, three arms, authored `history:`, and a seeded
  `workspace/`. It runs with `make e2e`.
- [`writing-evals.md`](writing-evals.md): the full eval format and grading model.
- [`configuration.md`](configuration.md): multi-harness sets, model sweeps, the
  judge, and every flag.
