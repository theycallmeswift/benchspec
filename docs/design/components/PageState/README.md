# PageState

The watch page's non-running states. This component renders only the copy and
banner.

**Provide:** the run's connection state and event history.

| State | What shows |
| --- | --- |
| loading | `Loading run…`, `aria-busy="true"`, queued boxes, no percentage |
| no run | `No run yet.` / `Start one with benchspec run.` |
| zero cells | `No cells in this run.`, no progress bar |
| empty filter | `No evals match these filters.` and a Clear filters button |
| disconnected | last data kept, live dot removed, a `bs-banner` (`warn` on `warn-tint`, `radius-8`): `Lost connection to benchspec run · retrying in 5s` |
| stopped | `Run stopped · N of M finished` |
| finished | Counter |

- Never show loading, missing data or disconnection as PASS, 0% or Finished.
- Announce state changes in one polite status region (`role="status"`).
- Empty states: `bs-empty`; the line in `bs-type-group-name`, the hint in
  `bs-type-help`, `muted`.
