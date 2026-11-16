# Contrast

WCAG 2.2 ratios for every text and meaningful-mark pair; rules in [README ›
Contrast](README.md#contrast). Cells read dark/light. Every pair passes. Opaque
pairs hold at every animation frame (the running dot pulses by scale).

## Text on grounds (target ≥4.6:1)

| Token | `bg` | `surface` | `raised` | `hover` | `band` | `lbl` | `popover` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fg` | 14.85/14.63 | 13.56/15.68 | 12.78/13.51 | 11.40/12.79 | 14.21/14.01 | 14.41/13.04 | 11.40/15.68 |
| `muted` | 7.93/7.14 | 7.24/7.65 | 6.83/6.60 | 6.09/6.25 | 7.59/6.84 | 7.70/6.37 | 6.09/7.65 |
| `fg-tertiary` | 6.28/5.27 | 5.74/5.65 | 5.41/4.87 | 4.82/4.61 | 6.01/5.05 | 6.10/4.70 | 4.82/5.65 |
| `accent-ink` | 6.21/5.60 | 5.67/6.00 | 5.34/5.17 | 4.76/4.90 | 5.94/5.36 | 6.02/4.99 | 4.76/6.00 |
| `danger` | 6.23/5.27 | 5.69/5.65 | 5.36/4.87 | 4.78/4.61 | 5.97/5.05 | 6.05/4.70 | 4.78/5.65 |
| `warn` | 9.97/5.39 | 9.10/5.78 | 8.58/4.98 | 7.65/4.72 | 9.54/5.16 | 9.67/4.81 | 7.65/5.78 |
| `success` | 6.12/5.66 | 5.58/6.07 | 5.26/5.23 | 4.69/4.95 | 5.85/5.42 | 5.93/5.05 | 4.69/6.07 |

## Marks on grounds (target ≥3.2:1)

| Token | Where | `bg` | `surface` | `raised` | `hover` | `band` | `lbl` | `popover` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `accent` | live dot, running/booting rings, selected ring, focus, tab underline | 6.21/4.04 | 5.67/4.33 | 5.34/3.73 | 4.76/3.53 | 5.94/3.87 | 6.02/3.60 | 4.76/4.33 |
| `status-pass` | pass box fill | 6.28/4.37 | 5.74/4.68 | 5.41/4.03 | 4.82/3.82 | 6.01/4.18 | 6.10/3.89 | 4.82/4.68 |
| `status-fail-1` | fail box, step 1 | 7.84/3.83 | 7.16/4.10 | 6.75/3.54 | 6.02/3.35 | 7.51/3.67 | 7.61/3.41 | 6.02/4.10 |
| `status-fail-2` | fail box, step 2 | 5.85/5.10 | 5.34/5.47 | 5.04/4.71 | 4.49/4.46 | 5.60/4.89 | 5.68/4.55 | 4.49/5.47 |
| `status-fail-3` | fail box, step 3 | 4.37/6.86 | 3.99/7.35 | 3.76/6.34 | 3.35/6.00 | 4.18/6.57 | 4.24/6.12 | 3.35/7.35 |
| `status-timeout` | timeout box fill | 9.97/5.39 | 9.10/5.78 | 8.58/4.98 | 7.65/4.72 | 9.54/5.16 | 9.67/4.81 | 7.65/5.78 |
| `status-error` | error ring, inside and out | 6.23/5.27 | 5.69/5.65 | 5.36/4.87 | 4.78/4.61 | 5.97/5.05 | 6.05/4.70 | 4.78/5.65 |
| `status-quiet` | quiet ring and bar | 9.97/5.39 | 9.10/5.78 | 8.58/4.98 | 7.65/4.72 | 9.54/5.16 | 9.67/4.81 | 7.65/5.78 |
| `status-queued` | queued outline; the bracket frame (`fg-tertiary`) | 6.28/5.27 | 5.74/5.65 | 5.41/4.87 | 4.82/4.61 | 6.01/5.05 | 6.10/4.70 | 4.82/5.65 |
| `status-na` | n/a outline and slash | 6.28/5.27 | 5.74/5.65 | 5.41/4.87 | 4.82/4.61 | 6.01/5.05 | 6.10/4.70 | 4.82/5.65 |

## Other pairs

| Token | On | Kind | Where | Dark/light |
| --- | --- | --- | --- | --- |
| `warn` | `warn-tint` | text | disconnected banner | 7.29/5.26 |
| `accent` | `line` | mark | progress fill against its track | 4.49/3.28 |
| `success` | `line` | mark | done progress fill against its track | 4.43/4.60 |
| `fg-tertiary` | `line` | mark | finished group bar against its track | 4.55/4.28 |
| `status-error` | `danger-tint` | mark | error ring, inside and out | 4.99/4.74 |

## Graded fail steps

Neighbouring steps are ≥1.3:1 apart in both themes.

| Pair | Dark | Light |
| --- | --- | --- |
| `status-fail-1` vs `status-fail-2` | 1.34 | 1.33 |
| `status-fail-2` vs `status-fail-3` | 1.34 | 1.35 |

## Brand art

Fixed values, not themed. Text floor 4.5:1; `brand-orange` is a mark (floor
3:1).

| Token | On | Ratio | Where |
| --- | --- | --- | --- |
| `brand-navy` | `brand-paper` | 14.63 | wordmark text, social card |
| `brand-slate` | `brand-paper` | 7.14 | social tagline |
| `brand-orange` | `brand-paper` | 3.16 | mark squares |
| `term-text` | `term-body` | 14.85 | terminal |
| `term-muted` | `term-body` | 5.73 | terminal |
| `term-prompt` | `term-body` | 6.21 | terminal |
| `term-green` | `term-body` | 7.53 | terminal |
| `term-yellow` | `term-body` | 9.97 | terminal |
| `term-red` | `term-body` | 5.02 | terminal |

## Exempt

Logotype and decorative marks (WCAG 1.4.11). They carry no meaning alone; never
reuse them as UI text or status marks.

| Pair | Dark | Light | Why |
| --- | --- | --- | --- |
| `brand-gray` on `brand-paper` | 2.07 | 2.07 | logotype: mark's left squares, light ground |
| `brand-salmon` on `brand-paper` | 1.83 | 1.83 | logotype: mark's middle right square |
| `wm-left` on `bg` | 3.99 | 2.07 | logotype: mark in UI chrome |
| `wm-mid` on `bg` | 8.78 | 1.83 | logotype: mark in UI chrome |
| `line` on `surface` | 1.26 | 1.32 | decorative: hairlines, panel borders, outlines of text-labelled cells |
| `shade` on `surface` | 1.20 | 1.63 | decorative: sticky-column depth gradient |
| `dot-2` on `raised` | 7.56 | 1.69 | decorative: title-bar dots |
| `dot-3` on `raised` | 3.44 | 1.91 | decorative: title-bar dots |
