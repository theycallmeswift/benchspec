# Tabs

Switches the watch page between Matrix and Leaderboard.

**Provide:** a `role="tablist"` of
`<button role="tab" aria-selected aria-controls>` and their `role="tabpanel"`
panels.

| State | Text | Underline |
| --- | --- | --- |
| inactive | `muted` | none |
| hover | `fg` | none |
| selected | `fg` | 2px `accent` |
| focus | unchanged | plus the focus outline |

- `bs-type-tab`, height `size-control`, `space-32` between tabs, 1px `line` rule
  under the row.
- Keyboard: roving tabindex; Left and Right move and select; Home and End jump.
- Matrix is selected while the run is live. On the run-end event, switch to
  Leaderboard unless the viewer already picked a tab.
- No Show or Problems filter: problems read from the matrix boxes.
