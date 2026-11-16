# StatusBox

One square per sample, showing its state, inside brackets with the mark's
geometry, at every width.

**Provide:** per sample, in order,
`<i class="bs-b bs-b-{state}" aria-hidden="true">` (a fail adds its step class)
inside `<span class="bs-bx" role="img" aria-label="…">`. The label is the only
text equivalent; never omit it: every state in order, a fail with its checks
(`3 samples: fail, 5 of 9 checks; running; quiet 52s`).

| Word | Class | Token | Box | Meaning |
| --- | --- | --- | --- | --- |
| pass | `bs-b-pass` | `status-pass` | solid square, quiet | Every check passed. |
| fail | `bs-b-fail` + `bs-f1` / `bs-f2` / `bs-f3` | `status-fail-1` / `-2` / `-3` | solid square, same size as pass | At least one check failed. |
| timeout | `bs-b-timeout` | `status-timeout` | warn fill, knocked-out horizontal bar | Hit the time cap; checks still graded. |
| error | `bs-b-error` | `status-error` | 2px danger ring on `danger-tint` | Crashed before grading. |
| running | `bs-b-running` | `status-running` | 2px accent ring, 5px centre dot (only the dot pulses) | The agent is working. |
| booting | `bs-b-booting` | `status-running` | 2px dashed accent ring | Sandbox starting or `setup.sh` running. |
| quiet | `bs-b-quiet` | `status-quiet` | 2px warn ring, 6×2px centre bar | Running agent silent for 30s or more. |
| queued | `bs-b-queued` | `status-queued` | empty 1.5px outline | Waiting for a free worker. |
| n/a | `bs-b-na` | `status-na` | 1.5px outline, diagonal slash | Every check runs only on another arm. |

Pass vs fail is distinguished by colour only (a deliberate WCAG 1.4.1
exception). The cell rate label, hover card (`✗ 5/9`) and accessible name
(`fail, 5 of 9 checks`) carry it in text.

### Graded fails

| Step | Checks passed |
| --- | --- |
| `bs-f1` | ≥67% |
| `bs-f2` | 34–66% |
| `bs-f3` | under 34%; always for a failed single-check eval |

Darker = fewer checks passed. Same danger hue, lightness only (ratios:
[`contrast.md`](../../contrast.md)); the lightest never reads as pass or warn.

### Rules

- Every other state has its own shape; never weaken one. Knockouts use a CSS
  `mask`, so the real ground shows through.
- Brackets: the [README's bracket](../../README.md#layout-shape-and-depth)
  (`stroke-outline`, `space-4` arms), never faded or removed; a mask
  (`--bs-bracket`) on the frame's `::before` and `::after`.
- Sizes: `size-box` in a cell and the legend; `size-box-sm` (add `bs-sm`) on
  phone and in four-arm cells.
- Legend: a band under the matrix: 1px `line` top rule, `space-12` above the
  row, `space-16` margin. The fail entry is a three-step swatch
  (`bs-b-failsteps`) in a button whose tooltip reads "Darker = fewer checks
  passed." (`role="tooltip"`, `aria-describedby`, hover and focus open it, Esc
  dismisses; never `title`).
- Don't: add states, recolour pass green, grade pass or other states, add a
  pass-rate colour ramp, fade boxes with opacity, or draw an outline in `line`.
