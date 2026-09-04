**TL;DR** — Emit one run-level benchmark matrix across the selected set (rows from the eval roster, columns from configured arms), render non-baseline cells as `rate (+Npp)`, add an `All evals` footer, and **preserve the existing per-group `--evalspec-fail-under` gate** — Phase 6 of the #1 roadmap, sequenced after #25.

> **Revision (2026-07-10, post-review):** the first draft pooled the fail-under gate (a silent breaking change), mis-described the estimator as macro/micro, left the run-level artifact location undefined, and overreached into Phase 8 fact schemas. This version preserves per-group gates, names the estimator precisely, defines the run-root artifact contract, and narrows scope to report shape.

## Problem

- **Symptom:** The matrix is built once per group — `report.write_benchmark(skill_dir, …)` inside `for skill_dir in sorted(...)` (`plugin.py:608-624`) — so a multi-group run yields N disjoint tables, never one cross-set comparison.
- **Symptom:** Non-baseline cells are delta-only — `f"{(row['pass_rate_mean'] - ref_rate) * 100:+.0f}pp"` (`report.py:357`), while baseline cells show the rate (`report.py:352`). The contract is `rate (+Npp)`: both in one cell.
- **Symptom:** There is no `All evals` total — `_matrix_table` emits one line per eval (`report.py:329,359`) with no column roll-up; the per-arm section table (`report.py:407-421`) has no totals line.
- **Symptom:** Matrix rows and columns are read off surviving artifacts, not the roster. `_arm_stats` appends a `per_eval` row only when an eval has ≥1 non-errored sample (`report.py:129-135`), so an all-errored eval vanishes; arm columns with unequal surviving sample counts produce deltas over different populations.
- **Exposed by:** Roadmap #1 Phase 6 Done-When: aggregate at selected-set/run level; cells `rate (+Npp)`; matrix includes `All evals`.
- **Scope:** Report shape only — run-level aggregation, `rate (+Npp)` cells, `All evals` footer, roster-derived rows/columns, and the run-root artifact location. Not: new artifact *fields* for binder/sandbox/process/workspace facts — those don't exist in any artifact today and are Phase 8 metadata plumbing.
- **Constraint:** **The `--evalspec-fail-under` gate stays per-group.** Today it fails the run if any arm's Δ in any group drops below the threshold (`plugin.py:625-638`; `configuration.md:81`). One pooled report must not become one pooled gate — a strong group would mask another's regression. Per-group Δ is retained internally to feed the gate.
- **Constraint:** Keep the estimator — `_arm_stats`, delta, and `delta_noise_pp` math (`report.py:73-150,259-267,504-511`). Change the aggregation *unit* and the cell/footer *rendering*, not the scorer.

## Solution

```text
| Eval                | base (claude) | armB (codex)  | armC (opencode) |
| ------------------- | ------------- | ------------- | --------------- |
| ingest/capture      | 80%           | 70% (-10pp)   | 90% (+10pp)     |
| to-spec/write-spec  | 60%           | 60% (+0pp)    | 40% (-20pp)     |
| All evals           | 70%           | 65% (-5pp)    | 65% (-5pp)      |
```

One table per run keyed `group/eval_id` across the selected set — rows from the eval roster, columns from configured arms, non-baseline cells `rate (+Npp)`, closed by an `All evals` roll-up. The per-group gate is unchanged and still fires on `to-spec/opencode`'s −20pp.

## User Stories

1. As a benchmark maintainer, I want **one matrix across the selected set**, so comparisons read from a single table, not N per-group ones I combine by hand.
2. As a reader, I want **`rate (+Npp)` in every cell** and an **`All evals` footer**, so absolute rate, baseline delta, and per-arm standing are visible in one place.
3. As a CI owner, I want **the fail-under gate to keep catching a regression in any single group**, so pooling the report doesn't quietly weaken the gate.

## Implementation Decisions

```text
plugin.py: for skill_dir in ...: write_benchmark(skill_dir)     ← per-group (today)
   ▼ becomes
plugin.py: write_benchmark(iteration_root, eval_dirs=all)       ← one run-level benchmark
           + keep per-group Δ loop for the fail-under gate       (plugin.py:625-638, unchanged behavior)

report.build_benchmark(eval_dirs, arms)
   ├─ rows    = configured eval roster        (group/eval_id; all-errored evals still listed)
   ├─ columns = configured arms               (missing arm → column present, cells "—")
   ├─ _arm_stats  (unchanged estimator: mean of per-(eval×sample) assertion-fraction rates)
   └─ _matrix_table
        ├─ cell = f"{rate:.0%}"                    baseline / no-baseline
        ├─ cell = f"{rate:.0%} ({dpp:+.0f}pp)"     NEW rate (+Npp)
        └─ "All evals" footer = per-arm pass_rate + delta_pp   (report.py:504-511)

iteration_root/                     # skills_root.parent — where meta.json/index.jsonl already land
  benchmark.json  benchmark.md      # NEW here (format_version → 2); per-group files removed
```

- **Move the aggregation unit to the run's eval set.** `write_benchmark`/`build_benchmark` take the union of discovered `eval_dirs` plus the arm roster and emit one benchmark; the per-group loop in `plugin.py:608-624` collapses to a single call. The `_arm_stats` estimator is unchanged — it already means over `(eval × sample)` pairs.
- **Name the estimator precisely.** The headline `pass_rate` is the **mean of per-(eval×sample) assertion-fraction rates** (`report.py:107,139`), each surviving `(eval, sample)` pair weighted equally — not equal-per-eval macro, not equal-per-assertion micro. When sample counts differ across evals (errors drop samples), evals with more survivors weigh more; this is documented, not silently changed.
- **Derive rows from the roster, columns from arms.** Rows come from the discovered eval set keyed `group/eval_id` (the composite avoids the bare-id cross-group collision Phase 3 flagged); an all-errored eval still gets a row (cells `—` or an errored marker). Columns come from configured arms; a wholly-missing arm keeps its column with `—` cells rather than disappearing. Deltas are annotated `within noise` when support is thin (existing `delta_noise_pp`).
- **Render `rate (+Npp)` and the `All evals` footer.** Replace the delta-only branch (`report.py:357`) with `f"{rate:.0%} ({(rate - ref_rate) * 100:+.0f}pp)"`; baseline/no-baseline cells keep the bare rate. Add one footer row using each arm's headline `pass_rate` + precomputed `delta_pp` (`report.py:504-511`) in the same format.
- **Preserve the per-group fail-under gate.** Retain per-group Δ internally and keep the gate loop's semantics (`plugin.py:625-638`): any arm below threshold in any group fails the run. The report is pooled; the gate is not.
- **Define the run-root artifact contract.** Write one `benchmark.json`/`benchmark.md` at the iteration root (`skills_root.parent`, alongside the existing `meta.json`/`index.jsonl`); remove the per-group `benchmark.*` files (no shim). Bump benchmark `format_version` to `2`; store `group` and `eval_id` as separate machine fields and render `group/eval_id`. No new binder/sandbox/process/workspace fields — those are Phase 8.

## Testing Plan

### Logic
- **Estimator is stable and named** — the `All evals` per-arm rate equals `mean` over every surviving `(eval × sample)` assertion-fraction rate and equals the value the fail-under gate reads; unequal per-eval support weights by surviving samples, as documented.
- **`rate (+Npp)` math** — a non-baseline cell shows the arm's per-eval rate and its Δ vs that eval's baseline rate; baseline cells show rate only; a coerced-away baseline yields absolute-only cells.
- **Roster completeness** — an all-errored eval still appears as a row; a wholly-missing arm still appears as a column with `—` cells; two evals sharing a bare id in different groups render as two `group/eval_id` rows.
- **Gate isolation** — a run where one group regresses below threshold fails even when another group's improvement makes the pooled Δ positive.

### Behavior
- **A multi-group run renders one matrix** — a run spanning ≥2 groups produces a single table (per-eval rows + `All evals` footer, baseline column first) at the iteration root, and the per-group gate still fires on the regressing group.

### Interface
- **Artifact contract is versioned, not silently mutated** — the run-root `benchmark.json` carries `format_version: 2` with separate `group`/`eval_id` fields; per-group `benchmark.*` are gone; `meta.json`/`index.jsonl` are untouched by this phase; no empty binder/sandbox fields are written.

## Open Questions

- **Should the `All evals` footer also expose per-group subtotals** (a middle tier between per-eval rows and the grand total)? Recommendation: not in Phase 6 — keep the footer a single grand total; add subtotals only if a consumer needs them. Resolved by a report-consumer request.

## Documentation Plan

- **`docs/configuration.md`**: document run-level aggregation, the `rate (+Npp)` cell format, the `All evals` row, the named estimator, and that the fail-under gate stays per-group; note binder/sandbox columns arrive in Phase 8.
- **`docs/schema.md`**: document `benchmark.json` `format_version: 2` and the `group`/`eval_id` fields.
- **`README.md`** / **`docs/research/evalspec-readme-vision.md`**: reconcile the sample matrix with the shipped `rate (+Npp)` + `All evals` shape.

## Out of Scope

- New artifact fields for binder model/API path, sandbox backend, process facts, workspace hashes, and arm-aware `index.jsonl` rows — Phase 8 metadata (roadmap #1's Phase 6 "artifacts include…" wording is corrected to report shape only).
- Changing the `--evalspec-fail-under` semantics, the delta/noise-band math (`report.py:259-267`), or `meta.json`/`index.jsonl` shape.
- Per-group report files as a fallback — the run-level table replaces them (no shim).

## References

- #1 — roadmap umbrella; Phase 6 Done-When and the corrected Phase 6/8 fact-schema split.
- #25 — Phase 4; lands first (both edit `report.py` + the plugin finish loop; per-group reports also own trigger aggregation, removed in #25).
- `src/evalspec/report.py:73-150` — `_arm_stats` estimator (`:107,:139`).
- `src/evalspec/report.py:305-360,504-511` — `_matrix_table` (cells `:352,:357`) and canonical delta math.
- `src/evalspec/plugin.py:608-624,625-638` — the per-group loop to collapse and the fail-under gate to preserve.
- `docs/configuration.md:81` — the published per-skill gate contract.

## Verification

- `make test` — proves aggregation, cell math, footer roll-up, roster completeness, gate isolation, and versioned artifact writing.
- `make lint` — proves package and docs satisfy lint.
- `evalspec run --set <multi-group-set>` (or the pytest-backed run) produces one iteration-root matrix with per-eval rows, baseline second column, `rate (+Npp)` non-baseline cells, and an `All evals` footer — proving the matrix contract.
- A run where one group regresses below `--evalspec-fail-under` exits nonzero even when the pooled Δ is positive — proving the gate stayed per-group.
- Inspecting the run-root `benchmark.json` — proves `format_version: 2`, separate `group`/`eval_id`, and no per-group `benchmark.*` remain.
