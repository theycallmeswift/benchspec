# MatrixCell

One eval × arm cell of the watch matrix: sample boxes, then one text label. No
markers or badges.

**Provide:**

- `<button type="button" class="bs-cell" aria-haspopup="dialog" aria-expanded aria-controls="drawer">`
  with `aria-label="{eval} on {arm}: {label}; {N} samples: {states}"`, each fail
  with its checks:
  `plan-implement-task on baseline: 1/3 · agent 2m 04s; 3 samples: fail, 7 of 9 checks; running; quiet 52s`.
- Inside it, a StatusBox frame (`aria-hidden`; the button's label carries the
  states) and one `.bs-t` label.
- `not run` cells: a plain `div`, not focusable.
- Pooled rate, finished count and tally from the backend; never derive them from
  box colours.

**Size:** `size-cell` (`size-cell-phone` below `breakpoint-phone`), `radius-4`,
on `raised`, `space-12` side padding (`space-8` from four arms, above
`breakpoint-phone`), `space-8` gap, `bs-type-cell`.

**Counting:** a cell's rate, its hover-card headline and rows count every check
that cell's graded samples ran; errored samples add none. Only the Leaderboard
pass rate pools shared checks. Scoped-off samples are `bs-b-na` from
`run.start`, never queued, and count nowhere: rates, `done/total`, tally, the
Counter's M.

**Problems:** shown by the boxes and the lowered pooled rate; named in words
only in the HoverCard and Drawer. Never put a marker, badge, pill or icon in a
cell (no `error`, `timeout`, `quiet 52s`, `err`, `t/o`).

### Label (desktop, one per cell)

| Cell | Label |
| --- | --- |
| finished | pooled rate: `56%`; `muted` (`bs-q`) only when every sample passed; any error or timeout keeps `fg`, even at 100%. Trigger-only evals too: no `N/M pass` |
| running | always `{done}/{total} · {phase} {elapsed}`: `1/3 · agent 1m 21s`, `0/3 · agent 9s` |
| booting | `0/3 · setup 1s` |
| queued | `queued` |
| every line scoped off the arm | `n/a` |
| outside the run | `not run` |
| denominator 0 (every sample errored) | `—` |

### States

Default ground `raised`; hover, selected and focus follow [README › Interaction
states](../README.md#interaction-states) (selected = drawer open). Exceptions:

| Cell | Ground | Ring |
| --- | --- | --- |
| queued | transparent | none |
| n/a | transparent | none |
| not run | transparent, not focusable | 1px `line` inset |

### Tally buckets

| Sample state | Bucket |
| --- | --- |
| pass | passed |
| fail, timeout (graded at its cap) | failed |
| error | errored |
| running, booting | running |
| quiet | quiet |
| queued | waiting |
| n/a | none |

### Keyboard

`role="grid"`, roving tabindex: arrows move, Enter or Space opens the drawer,
Esc closes it and refocuses the cell. Never `outline: none`; containers must not
clip the outline.

### Phone (below `breakpoint-phone`)

- A cell shows only its boxes (`bs-sm`, brackets kept) plus, once finished, the
  short rate (`<span class="bs-t bs-rate">`, at most 4 characters: `56%`,
  `100%`, `—`). `bundle.css` hides every other label.
- Arm columns `size-arm-phone`; the eval-id column takes the rest, at least
  `size-label-phone`; `space-8` column gaps. Eval ids truncate from the start
  (`…implement-task`); the drawer header shows the full id.
- Two arms fit 390px without horizontal scroll. Only when arms don't fit (e.g.
  four), wrap the rows in `.bs-mx` > `.bs-mx-scroll`: the matrix scrolls
  sideways under a pinned `size-label-phone` eval-id column with a 1px `line`
  edge; add `.bs-scrolled` to `.bs-mx` once scrolled to show the `size-shade`
  `shade` gradient. The page never scrolls sideways.

Eval rows and groups: MatrixGroup.
