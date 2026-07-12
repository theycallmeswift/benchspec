# Quickstart — first eval in 5 minutes

Run a passing eval against a trivial skill. You'll end up with a workspace dir, a printed benchmark line, and per-arm artifacts on disk.

## Prerequisites

- **Apple Silicon Mac** or **Linux with `/dev/kvm`**.
- **Python 3.11+**.
- **Claude Code** on `$PATH` — the judge runs host-side. Quickstart also uses Claude as the task agent; for OpenCode see [`agents.md`](agents.md).
- **A provider credential**: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`, exported or in a repo-root `.env`.
- **`GEMINI_API_KEY`** — the binder's classifier credential, required for every graded run (exported or in a repo-root `.env`). Unrelated to the Claude Code credential above.

Missing any of the above and preflight fails with a remediation message before boot.

## Step 1 — Create the repo

```bash
mkdir hello-evals && cd hello-evals
python3 -m venv .venv
.venv/bin/pip install "evalspec[microsandbox,claude]"
```

## Step 2 — Write the skill and the eval set

```bash
mkdir -p skills/hello/evals
```

`skills/hello/SKILL.md`:

```markdown
---
name: hello
description: Write the contents of a file under a Greetings/ directory.
---

# Hello

When asked to greet someone, create `Greetings/<name>.md` under the current working directory with one line: `Hello, <name>!`. Nothing more.
```

Declare a `default` eval set in `pyproject.toml`. Two arms — a `baseline` that installs no skill and a `trial` that installs it — with `baseline` as the set's `baseline`, so Δ = trial − baseline. A set-level `harness`/`model` default keeps the arms to just a `name`. By default, output eval arms do not load the repo as a plugin; the trial skill appears only because `setup.sh` installs it per cell.

```toml
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial" },
]
```

If you intentionally want a Claude Code arm to see the repo's plugin surface, opt in with `harness_args` on that arm:

```toml
[tool.evalspec.sets.plugin-enabled]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [
  { name = "baseline" },
  { name = "trial", harness_args = ["--plugin-dir", "/project"] },
]
```

## Step 3 — Write the eval

Each output eval is one self-contained file, `eval.md` (id = its folder name) or a `<stem>.eval.md` sibling (id = the stem) — a folder can hold several `<stem>.eval.md` files, sharing one `workspace/` and one `setup.sh`. That folder — the eval's **group** — is also the report/artifact-path slot: it's what shows up as `<group>-<eval_id>-<arm>` in test ids and `<group>/eval-<eval_id>/` in artifact paths, so name it after the skill when you want every eval under one skill to land in the same benchmark row. There is no suite header.

```bash
mkdir -p skills/hello/evals/hello
```

`skills/hello/evals/hello/greets-by-name.eval.md` (group `hello`, eval id `greets-by-name`):

```markdown
---
---

## Prompt

You are working in a vault rooted at your current working directory. Greet Alice in ./Greetings.

## Assertions

- [ ] ./Greetings/Alice.md contains the text 'Hello, Alice!'
- [ ] Skill `hello` invoked
```

The `---`/`---` frontmatter delimiters are required even with nothing between them — this eval needs no `history:` prefix. The agent runs with its current working directory set to the workdir mount (`/workspace`) inside the microVM, so paths are `./`-relative. The agent writes there; the host reads the same dir for grading. Each `- [ ]` line is one plain-prose assertion (a line with indented `- [ ]` children is a display-only header whose children flatten to one assertion each) — keep the `./` anchor so the path reads as a workdir fact (`evalspec lint` warns on a bare, unanchored path). At grade time the binder maps each line to a deterministic host-side checker when confident (the file-content line binds to a checker; the `` Skill `hello` invoked `` line binds to `skill_invoked`), else punts to the judge — you write no checker syntax. See [`schema.md`](schema.md).

The skill is installed per cell by `skills/hello/evals/hello/setup.sh` — **auto-discovered** relative to the eval group's own directory (no config key wires it; that's the build-time `environment_script` escape hatch, a different mechanism). It runs once per cell with `cwd` at the group dir (`/project/skills/hello/evals/hello`) inside the VM and branches on `$EVALSPEC_ARM`: the trial arm copies the skill into the fixed skills home (`/home/evalspec/skills`, which the agent's skill dir symlinks to); the baseline arm no-ops.

`skills/hello/evals/hello/setup.sh`:

```bash
#!/usr/bin/env bash
set -e
if [ "$EVALSPEC_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/evalspec/skills/hello
cp ../../SKILL.md /home/evalspec/skills/hello/SKILL.md
```

Check the suite statically before booting a VM. `evalspec lint` flags assertions the judge can't fairly grade; `evalspec analyze` classifies each assertion as deterministic or judge-backed (it binds through the real binder, so it needs `GEMINI_API_KEY`):

```bash
.venv/bin/evalspec lint
.venv/bin/evalspec analyze
```

To confirm discovery without grading, `evalspec run` forwards everything after `--` straight to pytest — `--collect-only` lists the cells without running them:

```bash
.venv/bin/evalspec run -- -k hello --collect-only
```

You should see two collected items, `test_eval[hello-greets-by-name-baseline]` and `test_eval[hello-greets-by-name-trial]` — one per declared arm. A malformed suite fails here with the offending path quoted.

## Step 4 — Build the snapshot

```bash
.venv/bin/evalspec sandbox:build
```

First run takes a few minutes — downloads Ubuntu, installs Claude Code into the VM, snapshots it. Later runs reuse it. This step is optional: `evalspec run` builds the snapshot on demand too, so run it up front only when you'd rather pay the build cost before the first arm. `evalspec sandbox:build` also takes `--set`/`--config` to build through a specific set's sandbox backend — see [`configuration.md`](configuration.md#building-the-sandbox--evalspec-sandboxbuild).

## Step 5 — Run the eval

```bash
.venv/bin/evalspec run --set default
```

Two parametrized tests run (`test_eval[hello-greets-by-name-baseline]` and `test_eval[hello-greets-by-name-trial]`), then a run-level benchmark line — one Δ per contrast arm vs the baseline:

```
iteration_01: baseline 0% -> trial 100%  (delta +100pp)  -> tmp/evals/iteration_01/benchmark.md
```

Numbers will vary — the baseline arm may guess the right shape, the trial arm may miss an assertion. What matters is that both arms ran and produced graded output.

`--set default` names the eval set to resolve; since `default` is also the `default-set`, plain `evalspec run` resolves it too. A few more flags: `--config <file.toml>` layers a scratch set without editing tracked config, and `--fail-under <pp>` turns the run into a CI gate that exits `1` when any arm's Δ falls below the threshold. Everything after `--` still passes to pytest, so `evalspec run --set default -- -k hello -x` scopes and stops on first failure. See [`configuration.md`](configuration.md#running-a-benchmark--evalspec-run) for every run flag and the exit-code contract.

## Step 6 — Inspect the artifacts

```bash
tree tmp/evals/iteration_01
```

```
tmp/evals/iteration_01
├── meta.json                       # run manifest (format_version 2) — run_id/commit/config_hash identity, the planned `arms` roster + observed `observed_arms` runtime provenance, the resolved judge object, the binder identity, start time; the join key for cross-run aggregation
├── index.jsonl                     # flat per-sample results, each row carrying its arm's harness/model/effort — the aggregator's entry point; derivable from the tree, persisted so external tools never hardcode the layout
├── benchmark.json                  # ONE run-level report across every group in the run (format_version 3)
├── benchmark.md                    # the same, rendered — one matrix keyed <group>/<eval_id>, plus a Provenance section
└── skills
    └── hello
        └── eval-greets-by-name
            ├── trial
            │   └── sample-0
            │       ├── grading.json     # {"eval_id","skill","arm","sample","errored","assertions":[…]}  (each assertion carries its "type")
            │       ├── timing.json      # {"duration_ms","judge_ms","total_tokens","input_tokens","output_tokens","cache_read_tokens","cache_creation_tokens"}
            │       ├── transcript.json  # one entry per turn (prompt, result, fired, result_subtype, tool_call_count, workdir tree)
            │       ├── provenance.json  # this sample's RuntimeProvenance — guest actual_version + sandbox identity; aggregated into meta.json's observed_arms
            │       └── session.jsonl    # lossless raw stream, each turn behind a {"turn": N} line
            └── baseline
                └── sample-0
                    ├── grading.json
                    ├── timing.json
                    ├── transcript.json
                    ├── provenance.json
                    └── session.jsonl    # every arm streams, so the baseline gets one too
```

Each arm is sharded by sample (`sample-0`, `sample-1`, …) so a `--count N` run keeps every attempt's full artifacts side by side. A run writes exactly one `benchmark.md` at the iteration root, spanning every group. It opens with one Δ line per contrast arm vs the baseline arm (`baseline <ref%> → <arm> <pct%> (Δpp)`, or each arm's absolute rate when no baseline ran), then a single `## Matrix` table — rows keyed `<group>/<eval_id>` (down the left), `<arm> (<harness>)` columns across the top, baseline cells show the absolute rate and every non-baseline cell shows `<rate> (+Npp)` (absolute rate AND its ±pp delta), closed by an `All evals` footer that rolls each arm up to its headline pass rate — and per-arm sections carrying `- Harness: … · Model: …` / `- Env: …` (env redacted) plus tokens and duration, then a compact `## Provenance` section labeling each matrix-column arm's observed guest version/snapshot/digest, or `not observed` when the arm never produced a runtime record. `benchmark.json` is the same data, machine-readable, keyed by `arms` with a top-level `roster` plus the planned/observed provenance pair (`planned_arms`/`observed_arms`) mirroring `meta.json` (see [`schema.md`](schema.md#benchmarkjson)). `meta.json` at the iteration root records what produced the run — configured **and** observed (see [`schema.md`](schema.md#metajson)). The per-sample dirs let you diff transcripts across arms or samples. `session.jsonl` is the lossless source each arm streams; to read the structured tool-call trajectory it encodes, pass its text to `evalspec.trajectory.trajectory_from_session` (event shape in [`schema.md`](schema.md)).

## Common first-run failures

- **Preflight: x86_64 macOS is unsupported.** microsandbox needs Apple Silicon or Linux+KVM.
- **Preflight: microsandbox runtime not installed.** Run Step 4 — it installs the runtime on first use.
- **`microsandbox … database error: Migration file … is missing`.** The installed `microsandbox` package version doesn't match the migration state of the shared host store at `~/.microsandbox/db/` — usually because a newer microsandbox ran against it and applied migrations this version doesn't carry. Install the microsandbox version this evalspec pins (see `pip show microsandbox`), or reset the host store per microsandbox's own docs before re-running.
- **Preflight: no Claude credential.** Run `claude setup-token` (sets `CLAUDE_CODE_OAUTH_TOKEN`) or export `ANTHROPIC_API_KEY`. A repo-root `.env` with either is picked up automatically.
- **The trial arm's `` Skill `hello` invoked `` assertion fails** — the agent hand-rolled the task instead of dispatching the skill, so its activation assertion grades False. The skill's description or the eval's prompt isn't getting the skill engaged. See [`agents.md`](agents.md) for the dispatch flow.

## A cross-family judge

By default the judge is `claude-code`/`sonnet` — same-family to this quickstart's Claude arms (`baseline`/`trial`, both `claude-code`/`sonnet`), which is fine for iterating. But a judge grading its own model family can be biased toward that family's outputs, so **for a benchmark you publish or compare across harnesses, prefer a cross-family judge** — here, an OpenAI-family one via Codex:

```toml
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
```

Or override per run without editing `pyproject.toml`:

```bash
evalspec run --judge-harness codex --judge-model gpt-5.5
```

A Codex judge needs the `codex` CLI installed and on `PATH` with valid credentials (the run preflights the binary and fails loudly if it's missing). See [`configuration.md`](configuration.md#the-judge--toolevalspecjudge) for the full precedence chain and every `--evalspec-judge-*` flag.

## Next

- [`schema.md`](schema.md) — the Markdown eval format in full: prose assertions and the binder, activation assertions, the `history:` prefix, and `evalspec lint`.
- [`concepts.md`](concepts.md) — vocabulary (arm/set/baseline/fired/errored), lifecycle, artifact layout, honesty contract.
- [`configuration.md`](configuration.md) — every `--evalspec-*` flag, env var, and `[tool.evalspec]` key.
- [`agents.md`](agents.md) — `CodingAgent` protocol, switching to OpenCode (`EVALSPEC_AGENT=opencode`), running one suite against both.
