# Reading results

This page explains what a run writes and how to read it: the human report
(`benchmark.md`) whose headline is the delta between arms, and the machine layer
(`meta.json`, `index.jsonl`, `benchmark.json`, per-sample files) designed so
external tooling can aggregate runs without hardcoding the directory layout. It
is for anyone looking at their first `tmp/evals/` tree. Terms (iteration, cell,
sample, baseline) are defined in [concepts.md](concepts.md).

## The artifact tree

Every run is one **iteration**, a zero-padded counter chosen once per run and
shared across parallel workers, under `tmp/evals/` in your repo:

```
tmp/evals/iteration_01/
├── meta.json         # run manifest: identity, planned config, observed provenance
├── index.jsonl       # one row per (eval × arm × sample) — the aggregator's entry point
├── benchmark.json    # the run-level report, machine-readable
├── benchmark.md      # the same, rendered
└── skills/
    └── hello/                      # the eval's group
        └── eval-greets-by-name/    # eval-<eval_id>
            ├── baseline/           # one directory per arm
            │   └── sample-0/
            │       ├── grading.json     # per-assertion verdicts + evidence
            │       ├── timing.json      # duration, judge time, token counts
            │       ├── transcript.json  # per-turn summary: prompt, result, tool counts
            │       ├── provenance.json  # what actually ran: guest version, snapshot
            │       └── session.jsonl    # the lossless raw agent stream (only when the harness produced output)
            └── trial/
                └── sample-0/ …
```

Samples are zero-indexed: a plain run writes `sample-0/`; `--count N`
(pytest-repeat) adds `sample-1/` and up side by side, so every attempt keeps its
full artifacts.

## `benchmark.md`, section by section

**The headline.** One line per non-baseline arm:
`**trial:** baseline 72% → trial 86% (**+14pp**)`, with a noise band appended
when the run has enough samples to compute one, or
`— single sample, no noise band` when it has one sample per cell. With no
baseline configured, the headline lists each arm's absolute rate.

**The matrix.** Rows are `group/eval_id` (from the run's roster, so an eval whose
every sample errored still appears as a row of `—`); columns are the arms,
baseline first, each header naming its harness. Baseline cells show the absolute
assertion pass rate; every other cell shows its rate *and* delta:

| Eval | baseline (claude-code) | trial (claude-code) |
|------|------|------|
| hello/greets-by-name | 67% | 100% (+33pp) |
| hello-file/writes-greeting-file | 50% | 83% (+33pp) |
| All evals | 58% | 92% (+34pp) |

The `All evals` footer is each arm's pooled rate across every eval in the run.

**Scoped assertions.** Present only when some line carried an `if:` / `unless:`
clause that did not hold in some arm. Such a line is pooled in *no* arm (the
rates above cover only lines graded in every arm of the run) and is reported
here instead, one row per line with each arm's own verdict: `skipped` where the
clause did not hold, `pass` / `fail` where every sample agreed, `k/n` across
samples otherwise, `—` for an arm with no graded sample:

| Eval / assertion | baseline | trial |
|------|------|------|
| hello/greets-by-name · Skill `hello` invoked | skipped | pass |

If every line of an eval is scoped out of an arm, that matrix cell is `—`, like
an all-errored one.

**Per-arm sections.** One per arm: pass rate, harness, model and turn timeout,
pass-through args, env (redacted: keys containing `TOKEN`, `KEY`, `SECRET`,
`PASSWORD`, or `AUTH` have their values masked), time per sample (task + judge),
tokens per sample, an errored-sample count, and a per-eval table with sample
counts and flakiness (the stdev across samples).

**Provenance.** One line per arm: the agent version observed *inside the guest*,
the snapshot it ran from, and the pulled image digest. An arm that never produced
a runtime record is labeled `not observed`, never shown with fabricated identity.

## Noise, samples, and flakiness

A delta smaller than its sampling noise is a coin flip, not a lift. With at least
two samples per arm (`--count N`), benchspec computes a noise band for each
arm-vs-baseline delta (the standard error of the difference of the two arms'
per-sample rates) and labels the headline **within noise** when `|Δ|` falls
inside it. A one-sample run has no band even across many evals — its
`delta_noise_pp` is `null` — because eval-to-eval spread is not rerun noise.
Treat the band as a guardrail against over-reading small numbers, not
a significance test: rates are pooled, sample-weighted means, not a paired
analysis. The band, like the rates, is computed over pooled lines only; a line
some arm's clause skipped contributes to neither.

The `--fail-under` CI gate deliberately uses the **raw** delta
([`configuration.md`](configuration.md#the---fail-under-gate)); the label exists
so a human does not ship a noisy win.

`--count 3` is the documented normal run; the numeric default stays one sample
per cell (each cell boots a 2 GB sandbox). A one-sample run with a baseline says
so on three surfaces: the `benchmark.md` headline ends in
`— single sample, no noise band`, the terminal summary prints a `WARN samples:`
line, and `benchmark.json` carries `unbanded: true`, so an aggregator can
exclude unbanded runs from a trend without re-deriving it from `max_samples`.
`unbanded` is the broader flag: it is also `true` for a multi-sample run whose
bands were lost to errored samples, which prints neither text marker.

> **Key concept: errored is not failed.** A *failed* assertion is a measurement:
> the agent ran and the claim did not hold. An *errored* sample is
> infrastructure: the agent CLI crashed or timed out, `setup.sh` exited
> non-zero, a harness ended the turn on a rejected permission prompt, the judge
> failed at the transport level. Errored samples are
> excluded from pass rates, but counted and surfaced in the report and in
> `grading.json`'s `errored` flag, so a half-crashed run cannot read like a
> clean one.

## The terminal summary

While cells run, pytest's progress line names the authored eval file each cell
came from — `evals/e2e/hello/evals/hello/greets-by-name.eval.md ...` — rather
than the internal test module, and each cell's id keeps its
`test_eval[<group>-<eval_id>-<arm>]` tail for `-k` and `-v`. When the session
ends, the run prints the same rows and rates `benchmark.md` holds, as a width-aligned
plain-text table under a `benchspec benchmark` banner:

```
============================ benchspec benchmark ============================
Eval                             baseline  trial  trial-overrides
hello/greets-by-name                  17%    67%              83%
hello-file/writes-greeting-file       17%    83%             100%
-----------------------------------------------------------------
All evals                             17%    75%             100%
vs baseline                                +58pp            +83pp
Report: tmp/evals/iteration_02/benchmark.md
```

Rows and rates follow the Markdown matrix: roster rows (including all-errored evals
as `—`), baseline column first, and the pooled `All evals` footer. Every cell is a
bare rate so each column lines up on one right edge; the per-eval `(+Npp)` deltas
live only in `benchmark.md`. When a baseline arm exists, a `vs baseline` line under
the footer carries each other arm's pooled delta as `+Npp` (blank under the baseline
column); without a baseline the table ends at `All evals`. The terminal table carries
no harness labels or noise bands; `Report:` points at the `benchmark.md` that does.
A one-sample run with a baseline prints one line between `vs baseline` and `Report:`,
`` WARN samples: 1 per cell; deltas carry no noise band (run with `--count 3` or more) ``
(never silent, never an error: the exit status is untouched).
A rule separates the eval rows from the pooled footer, and on a terminal that
supports color, rates are color-coded by band (green from 80%, yellow from 50%,
red below) and the `vs baseline` deltas by sign (green up, red down, yellow zero);
files and pipes always get plain text. The table is presentation only:
`benchmark.json` and `benchmark.md` are the artifacts, and the terminal never
changes their contents.

Two other visibility lines can follow the table: `FAIL fail-under: …` when the
gate trips, and `WARN binder: N assertion(s) degraded to judge grading` when a
transient binder infrastructure failure rerouted assertions to the judge (never
silent, never an error; a rejected binder key, by contrast, fails the run
outright).

## The machine layer

### `meta.json`: the run manifest

Written once per run, `format_version: 2`. Its central design split is **planned
versus observed**:

| Field | Contents |
|---|---|
| `run_id` / `commit` / `config_hash` | Identity for cross-run joins. `config_hash` covers only planned selectors, never what happened to run or which binary versions were probed, so two runs of identical config hash identically. |
| `iteration` / `started_at` / `benchspec_version` | Run bookkeeping. |
| `set` / `runner` | The resolved set name and runner. |
| `arms` | The **planned** roster: every configured arm (name, harness, model, effort, timeout, redacted env, harness_args, `requested_version`, the install selector such as `latest`, and `capabilities`), whether or not it ran. |
| `observed_arms` | The **observed** side, keyed by arm name: only arms with a persisted runtime record appear. Each carries the guest-probed `actual_version` and the sandbox identity (backend, snapshot, fingerprint and its inputs, pulled `image_digest`). Probes that fail record an explicit `*_status: "unavailable"` plus an error, never a silent null. |
| `judge` | The resolved judge (harness, model, effort, timeout, redacted env, args) plus its host-probed `actual_version`. |
| `binder` | The binder's transport identity: provider, model, API path. Never key material. |

### `index.jsonl`: the aggregator's entry point

One JSON object per `(eval × arm × sample)` row: identity (`skill`, which is the
group, then `kind`, `eval_id`, `arm`, `sample`), the arm's three core axes
(`harness`, `model`, `effort`) denormalized onto the row so no join with
`meta.json` is needed, `errored`, `passed`/`total` assertion counts (`total`
counts graded lines), `scoped` (lines carrying a clause) and `skipped` (lines
whose clause did not hold) counts, and the timing and token figures from the
sample's `timing.json`. Derivable from the tree; persisted so tools never
hardcode the layout.

### `benchmark.json`

The matrix, machine-readable (`format_version: 3`, versioned independently of
`meta.json`): the `label`, the `baseline` arm (or `null`), `max_samples`,
`unbanded` (`true` when a baseline exists, at least one arm has a `delta_pp`
against it, and no arm's `delta_noise_pp` could be computed; `false` otherwise),
the eval `roster`, per-arm stats under `arms`
(pass rate, stdev, `delta_pp`, `delta_noise_pp`, errored and binder-degraded
counts, per-eval rows), `scoped` (one row per line some arm skipped: `group`,
`eval_id`, `index`, `text`, and per-arm `{passed, total}` / `"skipped"` /
`null` cells), the `runner` and `binder` identity, and the same
`planned_arms`/`observed_arms` provenance pair as `meta.json`. Note the naming:
`arms` here is *result stats*; `planned_arms` is configuration.

### Per-sample files

- `grading.json`: the verdict. One entry per assertion with its text, `passed`,
  `evidence`, and `type` (`deterministic` for a checker, `semantic` for the
  judge), plus the sample's `errored` flag and a `binder_degraded` count. An
  entry whose line carries a clause adds `scoped: true`; one whose clause did
  not hold in this arm is `{"text", "passed": null, "skipped": true,
  "scoped": true, "reason": "<the clause>"}`, in its document position.
- `timing.json`: `duration_ms`, `judge_ms`, and the token split
  (`total_tokens`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`; zero where the harness does not report them).
- `transcript.json`: a per-turn summary: prompt, result text, `is_error`, the
  CLI's `result_subtype`, `tool_call_count`, the final `workdir_tree`, and the
  skills dispatched (when any).
- `provenance.json`: this sample's runtime record, the source that aggregates
  into `observed_arms`.
- `session.jsonl`: the lossless raw stream, each turn behind a `{"turn": N}`
  marker line. Everything else is derivable from it; it is the artifact to reach
  for when a grade looks wrong. A sample whose harness exited non-zero keeps its
  stream too, and its transcript `result` is the harness's stderr when it wrote
  any. To get the structured tool-call trajectory, feed its text to
  `benchspec.grading.trajectory.trajectory_from_session`; events
  follow the OpenTelemetry GenAI naming (`gen_ai.tool.name`,
  `gen_ai.tool.call.id`, and so on).

The authoritative shapes live in `benchspec.reporting.manifest`,
`benchspec.reporting.report`, and `benchspec.sandbox.provenance`. If this
page and those modules ever disagree, the modules are right.
