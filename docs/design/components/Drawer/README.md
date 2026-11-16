# Drawer

The selected cell's detail, floating over the page: status, eval, arm, sample,
key numbers, assertions, phases, run path.

**Provide:** the selected sample's grading (status, assertion verdicts with
checker and evidence), phase timings, cost, last five output lines and path,
plus previous/next sample handlers.

| Part | Spec |
| --- | --- |
| panel, desktop | a floating panel over the right of the page (`position: fixed`), not docked and not a column beside the matrix; the page sets its `top` below the Counter so the Counter stays visible. `size-drawer` wide, `bg`, 1px `line` border, `radius-8`, `shadow-popover`, `space-24` padding and section gap; scrolls inside itself |
| panel, phone | `bs-sheet`: full-width bottom sheet, top corners `radius-8`, `shadow-sheet`, `max-height: 85vh`; same header lines (`bs-sub` in `bs-type-body-mono`) and sample nav |
| status line | `bs-type-status`; verdict word uppercase, `danger` for FAIL and ERROR |
| eval id | `bs-type-eval-id`: path in `fg-tertiary`, leaf in `fg` 500. Each segment is a `nowrap` span with `<wbr>` after its `/`: breaks only after `/`, never at a hyphen or mid-word |
| sample nav | after `sample 1 of 3`, `‹ ›` `bs-iconbtn` buttons in a `bs-nav`, `space-4` apart, `aria-label` `Previous sample` / `Next sample`, `disabled` at either end; `size-close`, `size-control-phone` in the sheet |
| close | `×` in a `size-close` `bs-iconbtn`, `aria-label="Close"` |
| section heads | `bs-section-label` captions: `[ assertions · 3 failed · 6 passed · 1 n/a ]` (scoped-off assertions are `n/a`, never "skipped"), `[ phases · 90.9s ]`, `[ recent output · last 5 lines ]`; `space-8` to content |
| assertions | `bs-alst` on `surface`, `radius-8`: ✗ in `danger`, ✓ in `muted`, failures first; evidence behind a disclosure |
| phases | `size-progress` bar with 2px `bg` gaps: bind, boot, setup in `fg-tertiary` (one prep tone), agent `accent`, grade `fg`. Labels on one row, `space-16` apart, each led by a `size-swatch` `radius-2` swatch in its segment colour. `role="img"` with the timings |
| path footer | `bs-path`, `bs-type-meta`, `fg-tertiary`, `space-24` below the last section; breaks only after `/` |

Opening moves focus into the drawer (contained on phone); Esc closes it and
returns focus to the cell.
