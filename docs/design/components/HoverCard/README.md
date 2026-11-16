# HoverCard

Per-sample results for one cell on hover or keyboard focus: a preview of the
Drawer, never a replacement. Where a cell's problems are named in words.

**Provide:** eval id, arm, pooled rate with checks passed, the cell's worst
problem if any, and one row per sample (verdict, checks, duration).

| Part | Spec |
| --- | --- |
| panel | `popover`, 1px `line` border, `radius-8`, `space-12` padding, max width `size-hovercard`, `shadow-hovercard` |
| placement | right of the matrix's last arm column, top-aligned with the hovered row; flips upward near the viewport bottom; never covers a cell |
| headline | the cell's rate in `bs-type-metric`, then `14 of 18 checks passed` in `muted`; it equals the sum of the graded rows ([MatrixCell › Counting](../MatrixCell/README.md)) |
| problem line | worst problem only, by priority `error` > `timeout` > `quiet Ns` (longest silence): `error · sample 2 crashed before grading` in `danger`; timeout and quiet in `warn` (`bs-hprob`); omitted when nothing went wrong |
| sample rows | `bs-type-body-mono`, one per sample: `✓ 9/9` in `fg` for a pass, `✗ 5/9` (checks passed of checks) in `danger` for a fail, `ERROR`, `TIMEOUT` or `quiet 52s` in words; a fail's figure matches its box step |
| note | `bs-type-help`, `muted`: `Open the cell for assertions and output.`; trigger evals: `3 of 3 samples triggered the skill` |

- Opens after `dur-hover-delay` on hover and on focus; stays open while hovered;
  Esc dismisses. `role="tooltip"`, referenced by the cell's `aria-describedby`.
- Phone and touch: no hover card; tap opens the Drawer sheet.
- The Drawer shows everything the card shows.
