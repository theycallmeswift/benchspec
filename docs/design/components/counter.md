# Counter

The run's headline: percent of samples finished, a matching progress bar, and a
one-line tally.

**Provide:** finished and total sample counts (M = applicable samples; n/a
excluded, [MatrixCell › Counting](matrix-cell.md)), elapsed time, ETA when
known, tally counts per MatrixCell buckets (passed, failed, errored, running,
quiet, waiting; they sum to M) and the worker count (`-n 4`).

| Part | Spec |
| --- | --- |
| percent | digits (`bs-num`) in `bs-type-counter`, `fg`; `%` (`bs-pct`) at `text-24` in `muted`, baseline-aligned |
| N of M | `31 of 78 samples finished`, 16px mono, `muted` |
| elapsed · ETA | right end of the row: `elapsed 6m 41s · ~10m left`; phone keeps `elapsed 6m 41s`, drops the ETA |
| bar | `size-progress` on a `line` track, `radius-2`, `accent` fill while live, inside `bs-progbx`: the [README's bracket](../README.md#layout-shape-and-depth) as caps, `size-bracket-cap` tall |
| tally | `bs-tally`, `muted`; failed and errored above zero in `danger` (`bs-att`), quiet above zero in `warn` (`bs-warn`): `3 running · 1 quiet`; waiting `bs-wait`; worker count `fg-tertiary` (`bs-wk`) |
| finished | `✓ Finished · 100% · 78 of 78` in `bs-type-counter-finished`, `%` at `text-18`; the word in `bs-type-title`, `success`; bar `success` at full opacity; `took 19m 42s` |

- Bar:
  `role="progressbar" aria-label="Run completion" aria-valuemin="0" aria-valuemax="{M}" aria-valuenow="{N}" aria-valuetext="{N} of {M} samples finished"`.
- One polite, atomic status region announces count changes at most every 10s and
  the terminal state immediately; never elapsed time.
- Phone: the same tally items, wrapping; drop zero buckets except failed;
  `bundle.css` hides waiting and the worker count. No `N/M passed` fraction:
  `N of M` is the only one.
- Show Finished only after the run-end event. Other non-running states:
  PageState.
