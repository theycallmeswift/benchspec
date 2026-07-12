# evalspec

**A pytest-native framework for grading AI skills.** Write declarative eval cases, let a judge model score the agent's transcript, and run them like any test suite — `pytest -k my-eval`. evalspec runs each eval across the `harness × model` **arms** of a named eval set, inside isolated microVMs, and reports each arm's score plus its delta against the set's baseline arm.

> evalspec is like pytest, but for agent behaviors.

> **Status: alpha.** One production consumer (the `knowledge-base` Claude Code plugin). Breaking changes are possible before 1.0; the [schema](docs/schema.md) is the most likely surface to change.

## How it works

A single pass rate isn't a measurement — it's a number. The signal is the *Δ* between arms. So every eval runs across the arms of a resolved **eval set** (`[tool.evalspec.sets.<name>]`) — each arm a `harness × model` cell — against the same prompt, and the benchmark reports each arm's score plus its Δ against the set's **baseline** arm:

```
                       ┌──────────────────────────────┐
                       │ baseline   (baseline arm)    │
                  ┌───▶│ fresh microVM, setup.sh      │──▶ transcript ──┐
  one eval        │    │ installs no skill            │                │
  (prompt +  ─────┤    └──────────────────────────────┘                ├──▶  LLM judge  ──▶  Δpp
   assertions)    │    ┌──────────────────────────────┐                │     grades each   (arm% − baseline%)
                  └───▶│ trial      (another arm)     │──▶ transcript ──┘     assertion     per contrast arm
                       │ same VM + setup.sh installs  │
                       │ the skill                    │
                       └──────────────────────────────┘
```

Each arm runs in a clean room outside your project — it can't reach the skill docs, peer skills, or anything the agent didn't write itself. The per-cell `setup.sh` (branching on `$EVALSPEC_ARM`) decides whether to stage the skill in. That's the **honesty contract**: a baseline arm that installs nothing measures what the agent already knows, so its Δ against a trial arm is what the skill actually taught it. (Pointing the set's `baseline` key at the install-nothing arm makes Δ = trial − baseline.)

## What is an eval?

An eval has three moving parts: the **skill under test**, the **eval case** (a prompt plus assertions, in Markdown), and the **judge** (a model that grades the resulting transcript). A checklist assertion passes or fails — that's what makes it a test.

Output evals are discovered by walking a configured set of search paths (`skills`, `tests`, `evals`, `benchmarks` by default; set your own with `[tool.evalspec] eval_paths`). Each is one self-contained file under any `<group>/` directory beneath a search path: use `eval.md`, or a `<stem>.eval.md` sibling when a group holds more than one eval. The eval id is the folder name for `eval.md`, or the file stem for `<stem>.eval.md`. A sibling `workspace/` holds the starting files, and a sibling `setup.sh` handles per-eval sandbox setup. There is no suite header. The shape of one — `skills/archive/evals/archive-source-from-inbox/eval.md`:

```markdown
---
history:
- role: user
  content: Here's a capture for later — ./Inbox/openai-o1-launch.md.
- role: assistant
  content: Got it — say the word and I'll file it.
---

## Prompt

You are working in a knowledge-base vault rooted at your current working directory.
An unprocessed capture sits at './Inbox/openai-o1-launch.md'. Archive it: move it
into the vault's archive as a dated source, and record the move in the activity log.

## Assertions

- [ ] The capture now lives at ./9. Archive/Sources/{TODAY}/openai-o1-launch.md — a flat markdown file under a dated Sources folder (NOT a per-source subfolder)
- [ ] The activity log names both the Inbox source path and the archived destination
- [ ] Skill `archive` invoked
```

Every assertion is one plain-prose `- [ ]` line — you write no checker syntax. The `history:` frontmatter (optional, the only key) renders as a transcript prefix of prior context before the graded prompt. At grade time the **binder** classifies each assertion: when confident it maps the prose to a deterministic **checker** (`file_exists`, `glob_count`, `sha256_match`, `frontmatter_has`, `regex`, `skill_invoked`) run on the host — zero variance, zero judge cost; otherwise it **punts** the line to the LLM judge. The split is invisible from the suite.

The judge never grades from recall: it reasons from the agent's final message, the workdir file tree, file contents, SHA-256s, and process facts (the tools and sub-skills the agent invoked).

### Trigger evals

Output evals test what a skill *does*. **Trigger evals** test whether the skill *fires* for the right requests — `skills/<name>/evals/trigger-evals.md`:

```markdown
---
skill_name: archive
---
## Description
Positives move files into the archive; negatives lean on near-miss siblings.

## Trigger
- archive-transcript: archive this transcript into the vault
- inbox-capture: file this Inbox capture away under Sources for today
  - fails-on [sonnet, haiku]: routes on opus; sonnet and haiku miss the semantic intent. Cross-verified 2026-05-30.

## No Trigger
- ingest-near-miss: ingest this URL into my vault and make a resource note
- zip-archive: create a zip archive of my project's source files
```

Section determines polarity (`## Trigger` → should fire; `## No Trigger` → should not). The `<slug>:` prefix is the query's identity for artifacts and `-k` filtering. An indented `  - fails-on [tiers]: reason` marks a known routing miss or over-fire on specific tiers — regressions still fail, documented boundaries don't.

## Quickstart

```bash
pip install "evalspec[microsandbox,claude]"
```

evalspec registers itself as a pytest plugin — no `-p` flag needed. In a repo with `skills/<name>/SKILL.md`, an output eval under a search path (`skills/…/<group>/`), a per-eval `setup.sh` that branches on `$EVALSPEC_ARM`, and an eval set in `pyproject.toml`:

```toml
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "opus"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial" },
]
```

```bash
# 1. Lint the suite — static, no credentials or sandbox required:
evalspec lint

# 2. Build the agent snapshot once, then run — a fresh microVM per arm, graded by the judge:
evalspec sandbox:build
evalspec run
```

Output-eval test ids are `<group>-<eval_id>-<arm>`, so `-k` filters by the group or eval id — not by an enclosing skill directory. `evalspec run` forwards anything after `--` to pytest, so `evalspec run -- -k archive-source-from-inbox` (equivalently `pytest -k archive-source-from-inbox`) scopes a run to one group.

Start with `evalspec lint`: it flags assertions the judge can't fairly grade *before* you spend a token. `evalspec sandbox:build` warms the agent's microVM snapshot (a few minutes); `evalspec run` builds it on demand too, then reuses it. Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

## Host requirements

- **Sandbox**: Apple Silicon Mac or Linux with `/dev/kvm`. microsandbox doesn't ship for x86_64 macOS or Linux without KVM.
- **Agent CLI** (at least one): [Claude Code](https://claude.ai/install) on `$PATH` — required even for OpenCode and Codex runs, since the host-side judge uses it — plus [OpenCode](https://github.com/sst/opencode) or Codex CLI for matrix runs on those harnesses.
- **Provider credential**: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, or `CODEX_AUTH_JSON_PATH` for subscription-backed Codex CLI auth. Loaded from `.env` if present.
- **Python**: 3.11+.

## Features

- **Named eval sets, one signal.** Declare a `[tool.evalspec.sets.<name>]` whose `arms` are `harness × model` columns; each arm runs the same prompt in an isolated microVM, and the Δpp of each arm vs the set's `baseline` arm is the headline. A set's columns may span harnesses; `--evalspec-set` / `--evalspec-models` pick or sweep without editing tracked config. Each set also names its `runner` (`pytest`, the only supported value) and `sandbox` backend (`microsandbox`; `docker` fails fast as not implemented) — see [`docs/configuration.md`](docs/configuration.md).
- **Prose assertions, bound at grade time.** Every assertion is one plain `- [ ]` line (or a display-only parent `- [ ]` with indented `- [ ]` children that each flatten to one assertion — one nesting level). The binder maps it to a deterministic host-side checker when confident, else punts to the judge — the author writes no checker syntax.
- **A linter for unjudgeable assertions.** `evalspec lint` flags vague adverbs, files outside the workdir, and unanchored comparatives. Warnings exit non-zero.
- **Judge fed facts, not recall.** The judge reasons from the final message, workdir tree, file contents, SHA-256s, and the tools/sub-skills the agent invoked.
- **Trigger evals with `fails-on`.** Test skill routing separately from skill behavior; document known small-model boundaries without masking regressions.
- **Delta-headline report.** Every run writes one run-level `benchmark.md` at the iteration root, opening with one Δ line per contrast arm vs the baseline arm — baseline % → arm % → Δpp with a noise band (absolute per-arm rates when no baseline ran) — followed by a single `## Matrix` table spanning the whole run (rows keyed `<group>/<eval_id>`, `<arm> (<harness>)` columns; non-baseline cells show `<rate> (+Npp)`, closed by an `All evals` footer) and per-arm metadata (harness, model, redacted env), tokens, and duration. `--evalspec-fail-under` gates CI on the raw delta, per group.
- **A self-describing machine layer.** `meta.json` (run manifest — planned arm config plus observed per-arm runtime provenance) and `index.jsonl` (one row per sample, carrying its arm's harness/model/effort) are stable aggregator entry points, so external tools never hardcode the artifact tree — see [`docs/schema.md`](docs/schema.md#metajson).
- **Parallel by default.** Eval arms run as ordinary pytest tests, so `pytest -n` fans them out across microVMs. Runs are API-latency-bound and prompt-cached (~94% cache hits), so add workers until you hit your host's VM budget or your API rate limit.
- **Pluggable coding agent.** `CodingAgent` is a protocol; `claude-code`, `opencode`, and `codex` ship in-tree. Another agent is one file plus a registry entry — see [`docs/agents.md`](docs/agents.md).

## Why evalspec?

Other eval tools (promptfoo, DeepEval, Inspect) grade a model's *output*. evalspec grades a coding agent's *behavior* inside a sandboxed workdir, and comparatively: the gradee runs twice in isolated microVMs, so the result is the skill's marginal contribution, not the agent's baseline competence. If you ship skills, slash commands, or agent instructions and need to know whether they move the needle, that paired-arm, sandboxed delta is the wedge.

A few deliberate bets adopters inherit:

- **One sandbox backend: microsandbox.** A `SandboxBackend` seam exists so a second backend is additive, but Docker is explicitly not implemented (`sandbox = "docker"` fails fast) — Apple Silicon or Linux+KVM, sub-1.0 runtime.
- **Cross-agent task arms are an integration test, not a benchmark.** OpenCode running the same suite as Claude Code proves the protocol abstraction holds; pass rates across agents aren't directly comparable.
- **The judge runs on the host** for a consistent grader across the matrix, behind the agent protocol so an agent can override it.

## Documentation

- [`docs/quickstart.md`](docs/quickstart.md) — your first eval in 5 minutes.
- [`docs/schema.md`](docs/schema.md) — the Markdown eval format, prose assertions and the binder, `evalspec-trigger/v1`, and the linter.
- [`docs/concepts.md`](docs/concepts.md) — glossary, lifecycle, artifact layout, the honesty contract.
- [`docs/agents.md`](docs/agents.md) — the `CodingAgent` protocol, in-tree implementations, adding another agent.
- [`docs/configuration.md`](docs/configuration.md) — every CLI flag, env var, and `[tool.evalspec]` pyproject key.
- [`docs/goals.md`](docs/goals.md) — standing objectives and the harness compatibility targets.
- [`docs/style/development.md`](docs/style/development.md) — code style guide: conventions, error handling, testing, and Python specifics.
