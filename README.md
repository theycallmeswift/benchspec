<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/harnessbench-wordmark-dark.svg">
  <img alt="harnessbench" src="docs/assets/harnessbench-wordmark-light.svg" width="228" height="48">
</picture>

**Benchmark what your agent does, not what it says.**

[![CI](https://github.com/theycallmeswift/harnessbench/actions/workflows/ci.yml/badge.svg)](https://github.com/theycallmeswift/harnessbench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/harnessbench)](https://pypi.org/project/harnessbench/)
[![Python](https://img.shields.io/pypi/pyversions/harnessbench)](https://pypi.org/project/harnessbench/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

harnessbench runs an agent (Claude Code, Codex, or OpenCode) against a task
in a fresh sandbox — a microVM, or a container — checks what it actually did in
the workspace, and reports the result as a comparison: with your skill versus
without, one model versus another, one harness versus another.

<img src="docs/assets/benchmark-terminal.gif" width="800" alt="Animated terminal output: harnessbench run prints a benchmark matrix with evals as rows, arms as columns, color-coded rates, and percentage-point deltas">

*End-of-run summary for a two-arm run of the in-repo [`hello`](evals/e2e/hello/)
suite (illustrative numbers).* Rows are evals, columns are arms (`baseline` ran
the agent bare, `trial` installed the skill), and every non-baseline cell shows
its assertion pass rate plus the delta against the baseline in percentage
points. The same matrix lands in `benchmark.md`, with machine-readable artifacts
alongside.

Teams pick harnesses, models, and prompts by anecdote: run it once, eyeball the
transcript, trust the vibe. harnessbench turns that guess into a measurement.
Write the goal once, run it across the configurations you care about, and read
off — in percentage points — how good each one actually is at accomplishing it.

## Getting Started

```bash
pip install "harnessbench[microsandbox]"
```

> **Pre-1.0.** The eval format and the artifact schemas are the surfaces most
> likely to change. Two sandbox backends ship: `microsandbox` (microVMs; needs
> Apple Silicon or Linux+KVM) and `docker` (containers; needs a reachable Docker
> daemon, weaker isolation — see [docs/sandbox.md](docs/sandbox.md)).

An eval is one Markdown file: a prompt, then a checklist of plain-prose claims
about the workspace after the agent is done. There is no checker syntax to learn;
the wording is the spec. `evals/hello/greets-by-name.eval.md`:

```markdown
---
---

## Prompt

You are working in a workspace rooted at your current working directory.
Greet Alice by name.

## Assertions

- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'
- [ ] Skill `hello` invoked
- [ ] The greeting feels warm and personable, not curt or robotic
```

The benchmark is a block in `pyproject.toml`. Arms are the report columns; the
baseline is what the others are measured against:

```toml
[tool.harnessbench]
default-set = "default"

[tool.harnessbench.sets.default]
harness  = "claude-code"
model    = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },   # installs nothing
  { name = "trial" },      # setup.sh installs the skill
]
```

Then:

```bash
harnessbench lint      # static checks on the assertions
harnessbench analyze   # which assertions grade deterministically, which go to the judge
harnessbench run       # every (eval × arm) in its own sandbox, graded, reported
```

## What you need

| | |
|---|---|
| Platform | Apple Silicon Mac, or Linux with `/dev/kvm`. Python 3.11+. |
| Agent CLI | `claude`, `codex`, or `opencode` on `PATH`, with its credential (for Claude Code, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`). |
| `GEMINI_API_KEY` | The binder: a fixed Gemini call that classifies each assertion. Required for every `analyze` and `run`. |
| Judge credential | The judge runs on the host through an agent CLI; the default is `claude-code` with `sonnet`. Prefer a different vendor from the arms (this repo's own suite judges Claude arms with Codex). |

Credentials can live in a repo-root `.env`. A graded run can touch up to three
vendors: the agent's, Gemini for the binder, and the judge's. `lint` is free;
`analyze` and `run` spend API calls, and `run` also boots sandboxes. Preflight lists
every missing piece and exits before anything is spent.

## How a run works

```mermaid
flowchart LR
    E["greets-by-name.eval.md<br/>prompt + assertions"] --> A1["arm: baseline<br/>fresh sandbox,<br/>setup.sh installs nothing"]
    E --> A2["arm: trial<br/>fresh sandbox,<br/>setup.sh installs the hello skill"]
    A1 --> F1["facts: files, SHAs,<br/>final message, tool calls"]
    A2 --> F2["facts"]
    F1 --> G["binder: deterministic checkers<br/>everything else: LLM judge"]
    F2 --> G
    G --> R["benchmark.md + benchmark.json<br/>meta.json + index.jsonl"]
```

Each `(eval × arm)` pair is one parametrized pytest test. A cell:

1. **Boots a sandbox** — a microVM under microsandbox, a container under docker —
   from a cached snapshot with the agent CLI already installed. The first run
   builds the snapshot (a few minutes); later runs reuse it, or pay the cost up
   front with `harnessbench sandbox:build`.
2. **Seeds the clean room** — the eval's optional `workspace/` files land in a
   fresh directory mounted at `/workspace`, the agent's working directory.
3. **Runs `setup.sh`**, where arms diverge: it sees `$HARNESSBENCH_ARM`, so the
   baseline branch exits early and the trial branch copies the skill into place.
4. **Invokes the agent** on the eval's prompt.
5. **Collects the facts** — file tree, contents, SHA-256s, the final message,
   the tool calls.
6. **Grades** — the binder maps each assertion to a deterministic checker where
   it can do so without risk; the judge grades everything else from the
   collected evidence alone.

Two guarantees hold; the second is backend-dependent. Nothing in the sandbox
can write back to your checkout: `setup.sh` reaches the skill under test through
a read-only staged copy of your repo at `/project` (what a `git clone` would
contain — never `.env`, `.git`, or earlier runs' artifacts). Under the default
`microsandbox` backend, provider credentials are injected at the network
boundary, never a readable environment variable in the sandbox; `docker` has no
equivalent, so there the credential IS a readable environment variable (see
[docs/sandbox.md](docs/sandbox.md)).

`harnessbench run` is pytest underneath, and everything after `--` goes to
pytest verbatim: `harnessbench run -- -k greets-by-name` (equivalently
`pytest -k greets-by-name`) runs one eval, `-n 8` fans cells across eight
sandboxes, and `--count 5` samples each cell five times so the report can flag a
delta that sits within noise.

## Why harnessbench

- **Comparison is first-class.** A single pass rate is a number without a
  reference point. Arms and a baseline make the headline a delta; skip the
  baseline when absolute rates are what you want.
- **Deterministic where possible, judged where necessary.** The binder is tuned
  so a false positive, a surface check passing on wrong output, is the one
  unacceptable error; anything doubtful goes to the judge, which sees the
  collected evidence and never grades from recall.
- **Self-describing artifacts.** Every run writes `meta.json` (planned config
  plus observed provenance, down to the agent version inside the guest),
  `index.jsonl` (one row per sample), and `benchmark.json`, so other tools can
  aggregate runs without knowing the directory layout.

## Documentation

| | |
|---|---|
| [`docs/quickstart.md`](docs/quickstart.md) | Empty directory to a graded two-arm run. |
| [`docs/concepts.md`](docs/concepts.md) | The vocabulary: eval, arm, set, baseline, binder, judge. |
| [`docs/writing-evals.md`](docs/writing-evals.md) | The eval format, workspaces, `setup.sh`, and how grading decides what binds. |
| [`docs/configuration.md`](docs/configuration.md) | Sets, arms, the judge, every CLI flag, exit codes. |
| [`docs/sandbox.md`](docs/sandbox.md) | Snapshots, mounts, credentials, host requirements. |
| [`docs/results.md`](docs/results.md) | Reading `benchmark.md` and the machine-readable artifacts. |
| [`docs/harnesses.md`](docs/harnesses.md) | `claude-code`, `codex`, `opencode`, and adding your own. |

## Contributing

Issues and pull requests are welcome at
[github.com/theycallmeswift/harnessbench](https://github.com/theycallmeswift/harnessbench).
Until `CONTRIBUTING.md` lands, [`docs/style/development.md`](docs/style/development.md)
is the code style, and `make test` plus `make lint` are the bar.

## License

[MIT](LICENSE).
