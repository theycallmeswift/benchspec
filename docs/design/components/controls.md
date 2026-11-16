# Controls

Native buttons, selects and the theme toggle for the watch page.

**Provide:** native `<button>` and `<select>` with a visible label or
`aria-label`; never a `div` with a click handler.

| Control | Spec |
| --- | --- |
| button `bs-btn` | height `size-control`, `space-12` side padding, 1px `line` border, `radius-4`, `muted` text in `bs-type-meta`; hover: `fg` text, `muted` border |
| icon button `bs-iconbtn` | `size-close` square, no border, `radius-4`; hover on `raised` |
| select `bs-select` | `size-control` tall, `raised`, 1px `line` border, `radius-4`, `fg` |
| theme toggle `bs-toggle` | the button box (no pill), `aria-pressed`; label names the theme it switches to |
| disabled | `disabled` attribute; [README › Interaction states](../README.md#interaction-states); no hover response |
| focus | [README › Interaction states](../README.md#interaction-states) |

Button labels are a verb: Copy output, Clear filters, Close.
