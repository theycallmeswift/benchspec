**TL;DR** — Aggregate the benchmark matrix once per run across the whole selected set (not once per skill), render non-baseline cells as `rate (+Npp)` instead of delta-only, add an `All evals` footer row, and surface the arm/judge/process facts that already exist in artifacts — Phase 6 of the #1 roadmap.

## Problem

- **Symptom:** The matrix is built once per skill — `report.write_benchmark(skill_dir, …)` is called inside `for skill_dir in sorted(...)` (`plugin.py:608-624`), so `build_benchmark` (`report.py:453-526`) and `_matrix_table` (`report.py:305-360`) produce one table per skill. A multi-skill run yields N disjoint tables, never one cross-set comparison.
- **Symptom:** Non-baseline matrix cells are delta-only — `f"{(row['pass_rate_mean'] - ref_rate) * 100:+.0f}pp"` (`report.py:357`), while baseline cells show the absolute rate `f"{row['pass_rate_mean']:.0%}"` (`report.py:352`). The roadmap contract is `rate (+Npp)`: the reader sees both the absolute rate and its delta in one cell.
- **Symptom:** There is no `All evals` total — `_matrix_table` emits exactly one line per `eval_id` (`report.py:329,359`) with no column roll-up, and the per-arm section table (`report.py:407-421`) has no totals line either.
- **Symptom:** Artifacts omit resolved binder/sandbox/workspace/process facts. `meta.json` carries arm roster + full judge config (`plugin.py:451-549,478-487`) but no binder identity, sandbox backend, or process facts; `index.jsonl` rows carry only the arm *name* string (`report.py:193-251`); process facts are computed for grading (`render_process_facts`, `execution.py:372`) but never persisted as a structured field. Binder identity absent from artifacts was flagged as a Phase 8 gap in #1's Phase 2 comment — confirmed still absent.
- **Exposed by:** Roadmap #1 Phase 6 Done-When: reports aggregate at selected-set/run level; cells show `rate (+Npp)`; the matrix includes `All evals`.
- **Scope:** Report shape — run-level aggregation, `rate (+Npp)` cells, `All evals` footer — plus surfacing facts *already resolvable* in artifacts. Not: writing new artifact fields for binder model/API path, sandbox backend, or arm-aware index rows — those are Phase 8 metadata plumbing (the code confirms these fields exist nowhere today).
- **Constraint:** Keep the existing matrix machinery (`_arm_stats`, `build_benchmark`, delta/noise math) — change the aggregation unit and the cell/footer rendering, don't rewrite the scorer.

## Solution

```text
| Eval                     | base (claude) | armB (codex)  | armC (opencode) |
| ------------------------ | ------------- | ------------- | --------------- |
| ingest/capture           | 80%           | 70% (-10pp)   | 90% (+10pp)     |
| to-spec/write-spec       | 60%           | 60% (+0pp)    | 40% (-20pp)     |
| All evals                | 70%           | 65% (-5pp)    | 65% (-5pp)      |
```

One table per run keyed on `group/eval_id` across the whole selected set, baseline column first, every non-baseline cell showing `rate (+Npp)`, closed by an `All evals` roll-up row.

## User Stories

1. As a benchmark maintainer, I want **one matrix across the selected set**, so harness/model/effort comparisons read from a single table instead of N per-skill ones I combine by hand.
2. As a reader, I want **`rate (+Npp)` in every cell**, so I see both the absolute pass rate and its baseline delta without cross-referencing a second column.
3. As a reader, I want **an `All evals` row**, so the headline per-arm standing is visible at the bottom of the same table.

## Implementation Decisions

```text
plugin.py: for skill_dir in sorted(...): write_benchmark(skill_dir)   ← per-skill (today)
   ▼ becomes
plugin.py: write_benchmark(run_dirs=all_eval_dirs)                     ← once per run

report.build_benchmark(eval_dirs)                                      # union across groups
   ├─ _arm_stats(eval_dirs, …)          per-arm macro-mean over all (eval×sample) pairs
   ├─ rows keyed  group/eval_id         stable declared order, cross-group
   └─ _matrix_table(benchmark)
        ├─ cell = f"{rate:.0%}"                    baseline / no-baseline
        ├─ cell = f"{rate:.0%} ({dpp:+.0f}pp)"     NEW: rate (+Npp)
        └─ "All evals" footer = per-arm pass_rate + delta_pp   (already computed, report.py:504-511)
```

- **Move the aggregation unit from skill_dir to the run's eval set.** `write_benchmark` / `build_benchmark` take the union of discovered `eval_dirs` and emit one benchmark; the per-skill loop in `plugin.py:608-624` collapses to a single call. `_arm_stats` already macro-means over `(eval × sample)` pairs (`report.py:73-150,139`) — it just receives all eval_dirs instead of one skill's.
- **Key matrix rows on `group/eval_id`.** Cross-group aggregation means bare `eval_id` can collide (the Phase 3 handoff noted bare-basename groups collide across paths); rows use the `group/eval_id` composite for a stable, unambiguous identity in declared order (extends the `eval_ids` collection at `report.py:317-321`).
- **Render `rate (+Npp)`.** Replace the delta-only branch (`report.py:357`) with `f"{row['pass_rate_mean']:.0%} ({(row['pass_rate_mean'] - ref_rate) * 100:+.0f}pp)"`; baseline / no-baseline cells keep the bare rate (`report.py:352`). Missing cell stays `—`.
- **Add the `All evals` footer.** One row after the per-eval rows using each arm's headline `pass_rate` and precomputed `delta_pp` (`report.py:504-511`) in the same `rate (+Npp)` format — the column roll-up `_matrix_table` lacks today.
- **Surface only already-resolved facts.** The benchmark object / `meta.json` render the arm roster + judge config that already exist (`plugin.py:524-535,478-487`) and process facts reconstructable from persisted `session.jsonl` (`render_process_facts`, `execution.py:372`; `trajectory_from_session`). New fields for binder model/API path and sandbox backend are **not** invented here — they don't exist in any artifact and are Phase 8's job; Phase 6 depends on Phase 8 for those columns.

## Testing Plan

### Logic
- **Run-level aggregation matches per-sample facts** — the `All evals` per-arm rate equals the macro-mean of every `(eval × sample)` pair in the selected set, and equals the headline `pass_rate` used by the `--evalspec-fail-under` gate.
- **`rate (+Npp)` math** — a non-baseline cell shows the arm's absolute per-eval rate and its delta against that eval's baseline rate; baseline cells show rate only; a coerced-away baseline yields absolute-only cells with no delta.
- **Row identity is collision-free** — two evals sharing a bare id in different groups produce two distinct `group/eval_id` rows, not one merged row.

### Behavior
- **A multi-skill run renders one matrix** — a run spanning ≥2 groups produces a single table with per-eval rows plus one `All evals` footer, baseline column first.

### Interface
- **Artifact schema is stable and additive** — `benchmark.json` / `benchmark.md` / `meta.json` still parse; surfaced arm/judge/process facts appear without removing existing keys; binder/sandbox fields remain absent (deferred to Phase 8), not written empty.

## Open Questions

- Does the `All evals` row roll up as a macro-mean of eval rates (each eval weighted equally) or a micro-mean over all samples (each sample weighted equally)? Recommendation: macro-mean, matching the existing `_arm_stats` headline (`report.py:139`) so the footer equals the fail-under gate input. Resolved by confirming the gate's expected semantics against `plugin.py:625-638`.

## Documentation Plan

- **`docs/configuration.md`**: document run-level aggregation, the `rate (+Npp)` cell format, and the `All evals` row; note binder/sandbox metadata columns arrive in Phase 8.
- **`README.md`** / **`docs/research/evalspec-readme-vision.md`**: reconcile the sample matrix with the shipped `rate (+Npp)` + `All evals` shape.

## Out of Scope

- New artifact fields for binder model/API path, sandbox backend, and arm-aware `index.jsonl` rows — Phase 8 metadata.
- Changing the delta/noise-band math (`delta_noise_pp`, `report.py:259-267`) or the `--evalspec-fail-under` gate contract (`plugin.py:625-638`).
- Per-skill report files as a compatibility fallback — the run-level table replaces them (no shim, roadmap rule).

## References

- #1 — roadmap umbrella; Phase 6 Done-When and the Phase 6/8 metadata split.
- #19 / #1 Phase 2 comment — binder identity absent from artifacts, explicitly a Phase 8 gap; confirms Phase 6 must not invent those fields.
- `src/evalspec/report.py:305-360` — `_matrix_table` (cell render `:352,:357`, rows `:317-321`).
- `src/evalspec/report.py:73-150,453-526` — `_arm_stats` + `build_benchmark` (delta math `:504-511`).
- `src/evalspec/plugin.py:608-624` — the per-skill driver loop that collapses to one run-level call.
- `src/evalspec/plugin.py:451-549` — `meta.json` writer + `_judge_meta` (already-resolved facts to surface).
- `src/evalspec/execution.py:372` — `render_process_facts`, the process evidence to persist/surface.

## Verification

- `make test` — proves aggregation, cell math, footer roll-up, and artifact writing pass.
- `make lint` — proves package and docs satisfy lint.
- `evalspec run --set <multi-skill-set>` (or the pytest-backed run) produces one matrix with per-eval rows, baseline second column, `rate (+Npp)` non-baseline cells, and an `All evals` footer — proves the README matrix contract.
- Inspecting the run's `benchmark.json` / `meta.json` — proves surfaced arm/judge/process facts are present and existing keys still parse.
