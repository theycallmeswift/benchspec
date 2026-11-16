# TopBar

The watch page's first row: mark, run id and suite, live state, theme toggle.

**Provide:** the iteration id, suite or skill name, run state (live with time
since the last event, or `ended · exit N`) and current theme.

| Part | Spec |
| --- | --- |
| mark | inline SVG at `size-mark-ui`, `bs-wm-*` classes ([README › Logo](../../README.md#logo)); `aria-hidden` |
| run id | `<b>`, `fg`, weight 500 |
| suite | `muted` |
| live | `bs-live`: a `size-live-dot` `accent` dot, then `live · last event 2s ago` |
| ended | `ended · exit 1`, no dot |
| disconnected | remove the dot and show the PageState banner; never keep the dot when no events arrive |
| theme toggle | `<button class="bs-toggle" aria-pressed>` (Controls); label names the theme it switches to (`Light theme`) |

- Row: `space-16` gap, `bs-type-body-mono`; wraps on phone.
- Don't animate the live dot or add any other accent to the bar.
