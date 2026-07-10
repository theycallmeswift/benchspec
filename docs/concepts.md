# Concepts + lifecycle

evalspec runs each `(eval × arm)` as a parametrized pytest case. The agent runs in a microsandbox microVM; the judge runs on the host. A run resolves one **eval set** (`[tool.evalspec.sets.<name>]`) whose `arms` are the report columns — each a `harness × model` cell; the benchmark reports each arm's score and, when the set names a `baseline` arm, its Δ against that baseline.

## Glossary

- **Eval** — one task definition (prompt + prose assertions, optionally a `history:` prefix). Each is one self-contained file under a `<group>/` directory beneath a configured search path (`eval_paths`, default `skills`, `tests`, `evals`, `benchmarks`): `eval.md` or a `<stem>.eval.md` sibling. The eval id is the folder name for `eval.md`, or the file stem for `<stem>.eval.md`, and `workspace/` beside it holds starting files. There is no suite header. See [`schema.md`](schema.md).
- **Suite** — the `eval.md` plus any `*.eval.md` siblings inside one eval folder. Suite identity is that folder's **group** — the value that lands in the `<skill>` artifact-path slot and test id, which need not match the name of an enclosing `skills/<name>/` dir. Each file is validated independently at discovery, not assembled into one combined doc.
- **Arm** — a `harness × model` cell: one column of the resolved set, declared as an inline table in the set's `arms` (`name` + any of `harness`/`model`/`effort`/`env`/`harness_args`, inheriting the set-level defaults for the rest) and carried verbatim into one parametrized `(eval × arm)` test. The arm's `harness` names the coding agent; its `model` is the **task** model. Same prompt, same eval — arms differ in harness and/or model, environment, and invocation-layer pass-through args. A set's columns may span harnesses (each output-eval arm runs on its own `harness`); trigger routing still resolves to the single run-level agent (`--evalspec-agent` / `EVALSPEC_AGENT`). See [`configuration.md`](configuration.md).
- **Set** — a named, self-contained `[tool.evalspec.sets.<name>]` carrying its own `arms` (the columns), set-level `harness`/`model`/`effort`/`env`/`harness_args` defaults each arm inherits, and a `baseline` arm. A run resolves exactly one set — `default-set`, `--evalspec-set`, or a scratch `--evalspec-config` file — and that set's arms are the same columns for every skill in the run (one eval×arm matrix). See [`configuration.md`](configuration.md).
- **Baseline** — the arm every other arm's Δ is measured against (`baseline = "<arm-name>"` in the set). Δ = arm − baseline, in percentage points. Optional: with no `baseline` declared, each arm reports its absolute pass rate and no Δ is computed.
- **Baseline / trial (install model)** — the two-layer install model. **Layer 1** is the system + task every arm shares (empty workdir + workspace + prompt). **Layer 2** is the skill install, performed per cell by the eval's own `setup.sh` (which branches on `$EVALSPEC_ARM`). A **baseline** cell installs nothing in Layer 2 (`setup.sh` exits 0); a **trial** cell installs the skill into the fixed skills home. Pointing the set's `baseline` key at the install-nothing arm (conventionally named `baseline`) makes Δ = trial − baseline.
- **Assertion type** — every checklist item is plain prose; the author types nothing. The **binder** decides at grade time whether each assertion is graded **deterministically** (mapped to a checker on the host) or **semantically** (punted to the LLM judge). See [`schema.md`](schema.md).
- **Checker** — a host-side deterministic grader (`file_exists`, `glob_count`, `sha256_match`, `frontmatter_has`, `regex`, `skill_invoked`) the binder maps an assertion to — zero variance, zero judge cost. Writes a grading entry interchangeable with a judged one. Five grade against the workdir; `skill_invoked` is the exception, grading against process facts (which skills the arm dispatched, via `GradeContext`) rather than files.
- **Binder** — an author-invisible, cheap (`gemini-3.1-flash-lite`, a direct Gemini API call) classifier that maps each plain-prose assertion to one of the deterministic **checkers** at grade time when confident, else **punts** to the judge. Wired into the grading path (`execution._grade_mixed`): every output-eval assertion goes through it. Tuned for false-negatives — over-punting is free (the judge was already going to grade it), while a false-positive (a surface check passing on wrong output) is the one outcome worse than judging. Presence/persistence/negation assertions ("still present", "not duplicated") always punt. Shipped with its own labeled-corpus eval (`make evals:binder`, gate: `false_positive_rate` → 0). See **The binder** below.
- **Capabilities** — an agent's `AgentCapabilities` (`efforts`, `multi_turn`, `token_split`): what the harness can honestly do with it. See [`agents.md`](agents.md) for each field's consumer (`efforts` is documentation-only today — effort is no longer pre-validated).
- **Trigger eval** — one routing query (`query` + `should_trigger`; polarity from section membership in `trigger-evals.md`). Tests whether the configured agent dispatches to the skill. Lives in `<skill>/evals/trigger-evals.md`.
- **Iteration** — one full evalspec run. Artifacts land under `tmp/evals/iteration_NN/`, zero-padded and incrementing per run (e.g. `iteration_01`, `iteration_41`). The plugin picks the name once on the controller and shares it with xdist workers via `EVALSPEC_ITERATION`, so `-n 8` writes one iteration tree, not eight.
- **meta.json** — the run manifest written at the iteration root (`tmp/evals/iteration_NN/meta.json`) once per run: `run_id`/`commit`/`config_hash` identity, agent + versions, the eval `set` name + per-arm `arms` roster (each arm's harness/model/effort/env/harness_args), the resolved `judge` object (harness/model/effort/timeout/env/harness_args), `trigger_effort`, trigger mode, start time, `format_version`. The join key for post-hoc aggregation across runs.
- **index.jsonl** — flat per-sample results at the iteration root (`tmp/evals/iteration_NN/index.jsonl`): one line per (eval × arm × sample) and (trigger query × sample). The aggregator's entry point; derivable from the tree, persisted so external tools never hardcode the layout.
- **Label** — the benchmark's human-readable title, `"iteration_NN · <skill>"`. It's `benchmark["label"]` in the JSON and the `# Benchmark — …` heading in the Markdown. The arms it scores are keyed by their declared arm name under `benchmark["arms"]`.
- **Fired** — an arm actually invoked the skill (didn't hand-roll the task). Each turn's stream is scanned via `agent.detect_dispatch`; the arm's dispatched-skills set drives the `skill_invoked` activation assertion. A trial arm normally fires; a baseline arm does not, and fails the activation assertion accordingly.
- **Errored** — the agent run (`claude -p`, `opencode run`, etc.) crashed, timed out, or exited non-zero, OR the cell's `setup.sh` exited non-zero, OR the judge CLI failed at the infra level. Excluded from the benchmark — infra failures, not measurements.
- **Trajectory** — a turn's ordered `tool_call`/`tool_result` events, derived from its raw stream (every arm streams). Not persisted: `evalspec.trajectory.trajectory_from_session` regenerates it deterministically from `session.jsonl`. Feeds the judge's process facts and the `tool_call_count` / `skills_dispatched` summary in `transcript.json`. Event shape in [`schema.md`](schema.md).
- **Snapshot** — a microsandbox VM image with the agent CLI preinstalled. Cached at `~/.microsandbox/snapshots/evalspec-<agent.id>-<agent.version>/`. Built once per agent/version; reused across arms, iterations, and concurrent xdist workers (file-locked). A snapshot is sealed in five steps: **chosen base image** (`[tool.evalspec] base_image`, default `ubuntu:latest`) → **agent provision** (`CodingAgent.provision` installs the CLI + deps) → **skills-home bridge** (`agent.bridge_skills_home_script()` symlinks the agent's `skill_load_dir` to the fixed `/home/evalspec/skills`) → **host environment script** (`[tool.evalspec] environment_script`, run under `set -e` if declared) → **seal**. The host config folds into the cache key, so a changed base image or edited script auto-rebuilds.
- **Clean room** — the per-cell `tempfile.mkdtemp` workdir outside the project, bind-mounted into the VM at `/workspace`. The agent reads/writes there; the host gathers facts from the same dir for the judge. Outside-the-project containment is what keeps every arm honest.
- **Trigger mode** — `majority` / `best-of` / `asymmetric`. Sets the fire threshold across 3 routing passes. See `evalspec.trigger.fire_threshold`.
- **Routing budget** — per-pass timeout for trigger routing. A pass that streamed model activity but didn't dispatch in time is a clean non-fire; a pass that streamed nothing is a launch stall, retried as `RoutingError`.
- **Noise band** — the sampling-noise floor for an arm-vs-baseline Δ, in percentage points: the standard error of the difference of the two arms' per-sample pass rates. A Δ inside the band is a **within noise** wiggle, not a lift. Needs ≥2 samples per arm; the `--evalspec-fail-under` gate uses the raw Δ, not the band. See **Sampling noise and the noise band**.

## Lifecycle

```
Preflight
  └─ platform check (Apple Silicon / Linux+KVM)
  └─ microsandbox installed
  └─ credential set for each arm's harness
        │
Set resolution (once per run, at collection)
  └─ parse [tool.evalspec.sets.*] + default-set; pick the set (--evalspec-set, else
     default-set); apply scalar overrides (--evalspec-model/-harness/-effort/-env) to
     its defaults, or expand --evalspec-models into one arm per value; materialize arms
     with inheritance + env merge + resolved harness args (set args, then arm args) →
     the resolved set's columns (same for every output eval).
        │
Snapshot resolve (per run, per harness)
  └─ cache hit → reuse
  └─ cache miss → build (provision → skills-home bridge → environment script → seal,
     file-locked, one-shot per concurrent run)
        │
Per (eval × arm × sample) test
  └─ Seed clean room: copy workspace/ (if any), substitute {TODAY} in
     file contents + paths, snapshot original SHAs for the judge.
  └─ Boot VM from snapshot (fresh per cell), bind /workspace (rw) + /project (ro).
  └─ Run /project/<eval-dir-relative-to-repo>/setup.sh with cwd there
     under the cell env (`EVALSPEC_ARM`, `EVALSPEC_MODEL`, `EVALSPEC_HARNESS`,
     `EVALSPEC_SET`); it branches on $EVALSPEC_ARM or $EVALSPEC_SET to install the skill
     into the fixed home (trial) or no-op (baseline). A non-zero exit aborts the cell.
  └─ Render the prompt: history transcript block (if any) + {TODAY}-substituted ## Prompt.
  └─ Invoke agent (cwd=/workspace, paths ./-relative) with evalspec-managed identity
     flags plus the arm's resolved `harness_args`. These are invocation-layer config;
     they do not mutate the setup.sh environment.
  └─ Gather facts: workdir tree, file contents, SHA-256s, agent final message,
     dispatched-skills set.
  └─ Tear down VM.
        │
Grading (on host, AFTER the run)
  └─ For each prose assertion: binder.bind → deterministic checker on the host when
     confident (skill_invoked grades against the dispatched-skills set), else punt
     the remainder to the judge in one call. Merge back in assertion order.
        │
Artifacts per (eval × arm × sample)
  └─ <repo_root>/tmp/evals/iteration_NN/skills/<skill>/eval-<eval_id>/<arm>/sample-<k>/
     ├─ grading.json     # {eval_id, skill, arm, sample, errored, assertions: [{text, passed, evidence, type}]}
     ├─ timing.json      # {duration_ms, judge_ms, total_tokens, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}
     ├─ transcript.json  # [{turn, prompt, result, is_error, fired, result_subtype, tool_call_count, workdir_tree, skills_dispatched?}]
     └─ session.jsonl    # lossless raw stream — the turn's verbatim stream-json behind a {"turn": N} line
        │
Benchmark aggregation (sessionfinish, controller only)
  └─ <repo_root>/tmp/evals/iteration_NN/meta.json         # run manifest
  └─ <repo_root>/tmp/evals/iteration_NN/index.jsonl       # flat per-sample results
  └─ <repo_root>/tmp/evals/iteration_NN/skills/<skill>/benchmark.json + benchmark.md
  └─ benchmark.md leads with one Δ line per contrast arm vs the baseline arm
     ("<arm>: baseline <ref%> → <arm> <pct%> (Δpp)"; absolute per-arm rates when no
     baseline ran), then a "## Matrix" eval×arm table (evals down the left,
     "<arm> (<harness>)" across the top) and per-arm sections with
     "- Harness: … · Model: …" / "- Env: …" (env redacted), tokens, and duration
  └─ One summary line per skill: per-arm Δ vs baseline and/or trigger routing ("trigger
     N/M")  label: "iteration_NN · <skill>"
```

`meta.json` is the run manifest at the iteration root (one per run, above `skills/`): `run_id`/`commit`/`config_hash` identity plus agent + versions, the eval `set` name + per-arm `arms` roster (including resolved `harness_args`), the resolved `judge` object (harness/model/effort/timeout/env/harness_args), `trigger_effort`, trigger mode, start time, and `format_version`. It's the join key a post-hoc aggregator uses to stitch separate runs back together on metadata alone.

`session.jsonl` is the lossless source for everything downstream, and the only persisted record of the agent's turn. Every arm streams, so every cell writes one — keeping the trajectory and the judge's process facts symmetric across arms (the only artifact a turn omits is one that never streamed, e.g. a launch error). The structured trajectory is **derived, not stored**: `evalspec.trajectory.trajectory_from_session` regenerates it deterministically from `session.jsonl`, and the run already folds its summary into `transcript.json` (`tool_call_count`, `skills_dispatched`) and the judge's process facts. `result_subtype` is the CLI result event's `subtype` (e.g. `success`, `error_max_turns`), not the API `stop_reason`.

Trigger evals follow a simpler lifecycle: no workspace, no workdir, no setup.sh, no grading. The agent runs against `query`; the stream is scanned for dispatch; `fired == should_trigger` is asserted. Timing lands at `tmp/evals/iteration_NN/skills/<skill>/trigger-<slug>/sample-<k>/timing.json`, persisting `fired`, `passed`, and the `query` text — so a report never reimplements the threshold rule and a failing query is diagnosable from artifacts alone. At session end, all `trigger-<slug>` dirs aggregate into `benchmark.json` (under `trigger`) and `benchmark.md` (`## Trigger routing — N/M queries as expected`). Skills with only trigger evals (no `eval-*` dirs) get a benchmark and a terminal line without the Δ segment: `<skill>: trigger N/M  -> benchmark.md`.

## The binder

Authors write assertions as plain prose — there is no checker syntax to learn. The **binder** derives the deterministic check from the assertion text at grade time, mapping it to one of six checkers: five that grade the workspace (`file_exists`, `glob_count`, `sha256_match`, `frontmatter_has`, `regex`) and `skill_invoked`, which grades a process fact — whether the skill was dispatched — rather than a file. When it isn't confident, it **punts** the assertion back to the judge. It is wired into the grading path: `execution._grade_mixed` binds every assertion, runs the bound ones on the host against the final workdir, and punts the rest to the judge in a single call, merging results back in the original order.

For deterministic path checkers, the binder preserves author-written relative paths, including hidden path components such as `./.meta/...`; a leading `./` is only a workdir anchor for the checker resolver.

The binder is tuned for false-negatives over false-positives. A punt costs nothing — the judge was already going to grade that assertion — so over-punting is free, and the binder only has to beat the single judge call it replaces (a deterministic check is ≈1.0 reliable against a judge's ≈0.9). The one outcome worse than judging is a false-positive: a surface check passing on wrong output. The documented sole leak is the **A9 rule** — presence, persistence, and negation assertions ("still present", "not duplicated") always stay semantic, because the binder is blind to the invisible "…and was not replaced" clause that those assertions carry. The one carve-out is `sha256_match` against a named pre-run file, where exact-bytes equality is decidable without reading the assertion's intent.

Verification is its own labeled-corpus eval, `make evals:binder`, which gates `false_positive_rate` → 0. Determinism retention (how many assertions the binder confidently bound rather than punting) is reported with no floor: a punt-only binder is an accepted outcome, since it's strictly ≥ the judge it replaces.

Decomposition is an authoring convenience layered over this model, not a new assertion type. A `- [ ]` item with indented `- [ ]` children flattens to one standalone prose assertion per child (the parent line is a display-only header, never graded); each child then goes through the binder exactly like any top-level line. It only changes how a compound assertion is *written* — so a failure pinpoints the clause — never how an atom is graded. No inline determinism tags ride on the checkbox; the binder still owns every child's checker.

A compound assertion always punts — the binder maps one object to one checker, so "and" always defers to the judge. Authors buy back deterministic grading by writing one fact per assertion: split "`X` exists and contains `Y`" into a bare `X exists` (binds `file_exists`) plus a separate compound-content assertion (stays a punt, since "contains `Y`" still needs the judge to verify).

## Why the judge is on the host

The judge spawns a fresh host process for the configured judge harness (`evalspec.judges.run_judge`), not a sandboxed call. Two reasons:

1. **Grading needs the host's reasoning budget.** The judge does real reading and evidence-checking; a fresh microVM per arm doubles wall-clock and provider spend for no signal benefit.
2. **One consistent grader across harnesses.** Running the task under a different harness (`claude-code` vs `opencode`) leaves the rubric stable — only the *task* differs.

The judge harness is resolved once per run (`[tool.evalspec.judge]`, `--evalspec-judge-*` — see [`configuration.md`](configuration.md)), independent from every task arm's own harness — grading a Codex or OpenCode matrix no longer requires Claude Code installed. `binder.py`'s prose→checker classifier is a separate, unrelated direct Gemini API call — not a harness call at all — unaffected by the configured judge harness.

## Activation is an assertion, not a gate

There is no hard invocation gate. Activation — "did the skill actually run?" — is an ordinary prose assertion, written `` - [ ] Skill `X` invoked ``, that the binder maps to the `skill_invoked` checker. It grades against the arm's dispatched-skills set: True on a trial arm where the skill fired, False on a baseline arm where it didn't. Because it's graded symmetrically across arms, a flat Δ tells you which case you're in directly — the baseline arm shows the activation assertion failing (it had no skill to fire), the trial arm shows it passing. A trial arm that *fails* its own activation assertion (the skill was installed but never engaged) is visible in `grading.json` as that one assertion's miss, not a separate `gated` flag.

## Why the baseline arm runs at all

An arm's pass rate alone is not a measurement — it's a number. The signal is the *Δ* against the baseline arm. A 60% trial that came from a 40% baseline is a real lift; a 60% that came from 65% is a regression. Name a `baseline` arm in the set and keep it in the columns — drop it (omit the `baseline` key, or resolve a set with only contrast arms) only when you're debugging routing or sampling drift in isolation. With no `baseline` in the resolved set, the benchmark reports absolute per-arm rates and computes no Δ — that's a valid run, not an error.

## Sampling noise and the noise band

A Δ that's smaller than the sampling noise it rides on isn't evidence of a lift — it's a coin flip. evalspec computes a noise band (in percentage points) for each arm-vs-baseline Δ: the standard error of the difference of the two arms' per-sample pass rates, `SE = sqrt(s_arm²/n_arm + s_ref²/n_ref)`. This is a macro-mean approximation (averaged over eval×sample pairs, not a properly paired test) — a rough guardrail against over-reading a small Δ, not a substitute for statistical rigour. It needs at least two samples per arm to exist (a one-sample arm has no stdev, so the band is omitted and no label is shown). When `|Δ| ≤ SE`, the headline in `benchmark.md` and the terminal summary line both add a **within noise** label. The `--evalspec-fail-under` CI gate uses the **raw Δ**, not the noise-adjusted one — the label is there to keep you from shipping a noisy win, not to suppress the gate.
