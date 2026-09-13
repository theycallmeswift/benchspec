**TL;DR** — Treat an `unavailable` version or digest probe as unknown rather than as an identity claim in `aggregate_observed`, so one timed-out guest probe cannot make the whole run exit `1` with no matrix after every cell has graded.

## Problem

- **Symptom:** a 20-cell run on the `MLH/skills` suite graded every cell, then died in `pytest_sessionfinish` with `ValueError: conflicting runtime provenance for arm `codex`: `actual_version` differs (None vs '0.154.0')`. No `benchmark.md`, no terminal matrix, exit `1`. The per-cell `grading.json` files were all present and had to be aggregated by hand.
- **Symptom:** the "conflict" was two Codex cells whose guest version probe timed out. Their `provenance.json` reads `actual_version: null`, `actual_version_status: "unavailable"`, `actual_version_error: "guest version probe timed out after 30.0s"`; the other three Codex cells read `0.154.0`, `available`. Same snapshot, same fingerprint, same image digest on all five.
- **Symptom:** the aggregator treats a failed probe as a different environment. `_CONFLICT_FIELDS` (`src/benchspec/sandbox/provenance.py:253-261`) lists `actual_version` and `actual_version_status`, and `aggregate_observed` (`provenance.py:273-309`) raises on any inequality across records for one arm; its docstring says records that disagree on "either status" describe unlike environments. A timeout is not a different environment; it is the same environment with a missing reading.
- **Symptom:** the failure is placed where it costs the most. `pytest_sessionfinish` (`src/benchspec/runners/pytest.py:345`) calls `manifest.aggregate_observed_arms` (`src/benchspec/reporting/manifest.py:165`) before writing the manifest, with a comment that a conflict "raise[s] here and abort[s] the write, which is the intended loud failure". That is right for two genuinely different snapshots under one arm name. It is wrong for a probe that lost a race with a busy container.
- **Why it stayed hidden:** the existing test `test_unavailable_diagnostics_do_not_conflict_for_same_identity` (`tests/sandbox/test_provenance.py:289`) covers two `unavailable` records with different error text; no test pairs an `unavailable` record with an `available` one. And the probe rarely times out on a quiet host; it did here because four sandboxes were running build-shaped tasks that saturate the container.
- **Scope:** conflict detection in `aggregate_observed` for the two probe-backed fields, `actual_version` and `image_digest`, and their status fields. Snapshot, fingerprint, and install identity stay strict.
- **Constraint:** a real conflict still raises. Two `available` values that differ for one arm remain an error at session finish.
- **Constraint:** the observed record stays honest. When one sample knew the version and another did not, `meta.json` reports the known value; it does not invent availability for the sample that failed.

## Solution

```python
# src/benchspec/sandbox/provenance.py — aggregate_observed
for field_name in _CONFLICT_FIELDS:
    existing_value = _conflict_field_value(existing, field_name)
    new_value = _conflict_field_value(record, field_name)
    if existing_value == new_value or _either_unavailable(existing, record, field_name):
        continue
    raise ValueError(...)
if _more_available(record, existing):
    by_arm[record.arm] = record   # keep the record with the most available probe fields
```

A probe-backed field only conflicts when both records carry an `available` reading and the readings differ. The arm's observed entry is the record with the most available fields, so one successful probe is enough for `meta.json` to name the version.

## User Stories

1. As a benchmark operator, I want **a graded run to always land its matrix and manifest**, so a 30-second probe timeout on one cell cannot cost me 20 minutes of sandbox time.
2. As a results reader, I want **`meta.json` to name the guest version whenever any sample observed it**, so a partially failed probe degrades to "known" rather than "crash".
3. As a maintainer, I want **two genuinely different environments under one arm to still fail loudly**, so the safeguard that motivated the check is kept.

## Implementation Decisions

```
provenance.json per cell ──► manifest.aggregate_observed_arms ──► provenance.aggregate_observed
                                                                      │
                                     ┌── snapshot / fingerprint / install / env_script differ ──► ValueError (unchanged)
                                     ├── actual_version or image_digest: both available and differ ──► ValueError (unchanged)
                                     ├── either side unavailable ──► not a conflict; keep the more-available record
                                     └── all equal ──► keep first
                                                                      ▼
                             pytest_sessionfinish ──► write_manifest ──► benchmark.md + terminal matrix
```

- **Status-aware comparison for probe-backed fields only.** `actual_version`/`actual_version_status` and `image_digest`/`image_digest_status` are the two value/status pairs `_check_available_unavailable` (`provenance.py:26`) already models as "reading or reason". The comparison uses that model: an `unavailable` side has no reading to disagree with. The four static identity fields keep exact equality.
- **Pick the most informative record.** Today the first record wins (`provenance.py:307`). With unavailable sides tolerated, the winner must be the record whose probes succeeded, so `to_observed_dict` reports the version and digest when any sample had them. Ties keep the first.
- **The loud failure stays where it is.** `pytest_sessionfinish` (`pytest.py:345`) and `aggregate_observed_arms` (`manifest.py:165`) are unchanged; only the definition of "conflict" narrows.
- **A probe timeout is worth a note in the manifest.** `to_observed_dict` gains nothing new; the per-cell `provenance.json` already carries the error text for the sample that failed, and `docs/results.md` points readers there.

## Testing Plan

### Logic
- **Available beside unavailable is not a conflict** — for the same arm, one record with `actual_version` `0.154.0`/`available` and one with `None`/`unavailable` aggregate to a single observed entry reporting `0.154.0`; the same holds for `image_digest`.
- **Two available readings that differ still conflict** — `0.154.0` beside `0.153.0` for one arm raises naming the field.
- **Static identity stays strict** — a differing `snapshot` or `fingerprint` raises even when every probe field is unavailable.
- **The most-available record wins regardless of order** — unavailable-then-available and available-then-unavailable both report the available value.
- **All-unavailable still aggregates** — the existing behavior for two unavailable records is unchanged.

### Behavior
- **A run with one timed-out probe lands its artifacts** — with one cell's `provenance.json` marked unavailable and the rest available, session finish writes `meta.json` naming the version, writes `benchmark.md`, prints the matrix, and exits `0`.

### Interface
- N/A — no CLI, config, or artifact-schema change; `meta.json` keeps its shape.

## Documentation Plan

- **`docs/results.md`** (provenance section): one sentence that `observed_arms` reports a probe value when any sample observed it, and that a sample whose probe failed keeps its error text in its own `provenance.json`.

## Out of Scope

- Raising the 30-second probe timeout or retrying the probe; a faster fix, but it leaves the crash in place for the next slow host.
- Writing a partial manifest on a genuine conflict; two different environments under one arm name should still stop the report.

## References

- `src/benchspec/sandbox/provenance.py:26,253-261,266-270,273-309` — `_check_available_unavailable`, `_CONFLICT_FIELDS`, `_conflict_field_value`, `aggregate_observed`.
- `src/benchspec/reporting/manifest.py:165` and `src/benchspec/runners/pytest.py:345` — the call chain from session finish to the raise.
- `src/benchspec/orchestration/execution.py:361` — where the guest probe result is folded into the per-cell record.
- `tests/sandbox/test_provenance.py:289` — the unavailable-vs-unavailable test the new cases sit beside.
- `MLH/skills` minimal-prompt trial run: 20 cells graded, two Codex `provenance.json` files with `actual_version_error: "guest version probe timed out after 30.0s"`, session finish raised `conflicting runtime provenance for arm codex`, exit `1`, no `benchmark.md`.

## Verification

- `make test` — proves the five aggregation guarantees above and that the existing conflict and unavailable tests still pass.
- `make lint` — proves style and types.
- `uv run benchspec run --set e2e -- -n 6` on a loaded host, or with one cell's `provenance.json` hand-edited to `unavailable` before session finish — proves the matrix and `meta.json` land and the exit code is `0`.
