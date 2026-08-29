# Reading results

A run leaves two kinds of output: a human report (`benchmark.md`) whose headline
is the delta between arms, and a machine layer (`meta.json`, `index.jsonl`,
`benchmark.json`, per-sample files) designed so external tooling can aggregate
runs without hardcoding the directory layout. This document walks both, starting
from the tree a run writes.

## The artifact tree

Every run is one **iteration** — a zero-padded counter, chosen once per run and
shared across parallel workers — under `tmp/evals/` in your repo:

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

Samples are zero-indexed — a plain run writes `sample-0/`; `--count N`
(pytest-repeat) adds `sample-1/` … side by side so every attempt keeps its full
artifacts.

## `benchmark.md`, section by section

**The headline.** One line per non-baseline arm:
`**trial:** baseline 72% → trial 86% (**+14pp**)`, with a noise band appended
when the run has enough samples to compute one. With no baseline configured, the
headline lists each arm's absolute rate.

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

**Per-arm sections.** One per arm: pass rate, harness and model, pass-through
args, env (redacted — keys that look secret, like `*TOKEN*` or `*KEY*`, have
their values masked), time per sample (task +
judge), tokens per sample, an errored-sample count, and a per-eval table with
sample counts and flakiness (the stdev across samples).

**Provenance.** One line per arm: the agent version observed *inside the guest*,
the snapshot it ran from, and the pulled image digest. An arm that never produced
a runtime record is labeled `not observed` — never shown with fabricated
identity.

<a id="noise"></a>
## Noise, samples, and flakiness

A delta smaller than its sampling noise is a coin flip, not a lift. With at least
two samples per arm (`--count N`), evalspec computes a noise band for each
arm-vs-baseline delta — the standard error of the difference of the two arms'
per-sample rates — and labels the headline **within noise** when `|Δ|` falls
inside it. Treat the band as a guardrail against over-reading small numbers, not
a significance test: rates are pooled, sample-weighted means, not a paired
analysis.

The `--fail-under` CI gate deliberately uses the **raw** delta
([`configuration.md`](configuration.md#the---fail-under-gate)); the label exists
so a human doesn't ship a noisy win.

> **Key concept — errored ≠ failed.** A *failed* assertion is a measurement: the
> agent ran and the claim didn't hold. An *errored* sample is infrastructure —
> the agent CLI crashed or timed out, `setup.sh` exited non-zero, the judge
> failed at the transport level. Errored samples are excluded from pass rates,
> but counted and surfaced in the report and in `grading.json`'s `errored` flag,
> so a half-crashed run can't read like a clean one.

Two other visibility lines can follow the summary: `FAIL fail-under: …` when the
gate trips, and `WARN binder: N assertion(s) degraded to judge grading` when a
transient binder infrastructure failure rerouted assertions to the judge (never
silent, never an error — but a rejected `GEMINI_API_KEY` fails the run outright).

## The machine layer

### `meta.json` — the run manifest

Written once per run, `format_version: 2`. Its central design split is **planned
versus observed**:

| Field | Contents |
|---|---|
| `run_id` / `commit` / `config_hash` | Identity for cross-run joins. `config_hash` covers only planned selectors — never what happened to run or which binary versions were probed — so two runs of identical config hash identically. |
| `iteration` / `started_at` / `evalspec_version` | Run bookkeeping. |
| `set` / `runner` | The resolved set name and runner. |
| `arms` | The **planned** roster: every configured arm (name, harness, model, effort, redacted env, harness_args, `requested_version` — the install selector, e.g. `latest` — and `capabilities`), whether or not it ran. |
| `observed_arms` | The **observed** side, keyed by arm name: only arms with a persisted runtime record appear. Each carries the guest-probed `actual_version` and the sandbox identity (backend, snapshot, fingerprint and its inputs, pulled `image_digest`). Probes that fail record an explicit `*_status: "unavailable"` plus an error — never a silent null. |
| `judge` | The resolved judge (harness, model, effort, timeout, redacted env, args) plus its host-probed `actual_version`. |
| `binder` | The binder's transport identity: provider, model, API path. Never key material. |

### `index.jsonl` — the aggregator's entry point

One JSON object per `(eval × arm × sample)` row: identity (`skill` — the group —
`eval_id`, `arm`, `sample`), the arm's three core axes (`harness`, `model`,
`effort`) denormalized onto the row so no join with `meta.json` is needed,
`errored`, `passed`/`total` assertion counts, and the timing/token figures from
the sample's `timing.json`. Derivable from the tree; persisted so tools never
hardcode the layout.

### `benchmark.json`

The matrix, machine-readable (`format_version: 3`, versioned independently of
`meta.json`): the `label`, the `baseline` arm (or `null`), the eval `roster`,
per-arm stats under `arms` (pass rate, stdev, `delta_pp`, `delta_noise_pp`,
per-eval rows), and the same `planned_arms`/`observed_arms` provenance pair as
`meta.json`. Note the naming: `arms` here is *result stats*; `planned_arms` is
configuration.

### Per-sample files

- `grading.json` — the verdict: one entry per assertion with its text, pass/fail,
  evidence, and `type` (`deterministic` or judge-graded), plus the sample's
  `errored` flag and a `binder_degraded` count.
- `timing.json` — `duration_ms`, `judge_ms`, and the token split
  (`total_tokens`, `input_tokens`, `output_tokens`, cache figures where the
  harness reports them).
- `transcript.json` — a per-turn summary: prompt, result text, whether the skill
  fired, the CLI's result subtype, tool-call count, and the final workdir tree.
- `provenance.json` — this sample's runtime record, the source that aggregates
  into `observed_arms`.
- `session.jsonl` — the lossless raw stream, each turn behind a `{"turn": N}`
  marker line. Everything else is derivable from it; it is the artifact to reach
  for when a grade looks wrong. To get the structured tool-call trajectory, feed
  its text to `evalspec.grading.trajectory.trajectory_from_session` — events
  follow the OpenTelemetry GenAI naming (`gen_ai.tool.name`,
  `gen_ai.tool.call.id`, …).

The authoritative shapes live in `evalspec.reporting.manifest`,
`evalspec.reporting.report`, and `evalspec.sandbox.provenance`. When this
document drifts, the code wins.
