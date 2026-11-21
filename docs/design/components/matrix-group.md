# MatrixGroup

The matrix structure around the cells: one bracketed caption, a summary row per
folder group, and eval rows beneath it.

**Provide:** per group, its folder name, eval count, the count of checks scoped
off each arm, and per arm the pooled rate, finished count and whether it is
still running; per eval, its id relative to the group.

| Part | Spec |
| --- | --- |
| matrix caption | one right-aligned `bs-mxcap` above the matrix: `[ 13 evals × 2 arms · live ]`, `bs-type-section-label`, `fg-tertiary` |
| arm header | arm name over `harness · model`, `bs-type-meta`; above `breakpoint-phone` the second line wraps, never truncates |
| group label, line 1 | folder name, `bs-type-group-name`, `fg` |
| group label, line 2 | always present: `5 evals · [trial-only]`, `bs-type-meta`, `fg-tertiary`, `space-4` below; scope note as bracket text in its own `<span>`, which never breaks; wraps on phone. `[trial-only]` only when every check in the group skips the other arm(s); otherwise `[1 check trial-only]`, `[1 check skips bare]`; `1 eval`, never `1 evals` |
| group row | min-height `size-cell` + `space-24`; `space-16` above and below |
| summary per arm | `bs-type-cell` over a `size-group-bar` bar on a `line` track; the bar shows completion (`done/total`), never pass rate |
| eval row | id in `muted` (`bs-eval`), indented `space-24`, so group names lead; cells per MatrixCell |

### Summary states

| State | Text | Bar |
| --- | --- | --- |
| live | `57% · 4/15` | `accent`, width `done/total` |
| nothing started | `queued · 0/12`, `muted` | empty |
| started, nothing graded | `running · 0/12`, `muted` | `accent`, width `done/total` |
| finished | `62%` | full, `fg-tertiary` |
| every check scoped off the arm | `n/a`, no count | empty |
| denominator 0 after errors | `—` | full, `fg-tertiary` |

Trigger-only groups use the same text (`100% · 12/12`); never `routed`.

- No group draws a rule brighter than `line`; the summary bars replace
  full-width rules.
- In a full suite, groups start collapsed; the active group and any group with
  an error or timeout stay open.
- Don't: colour a finished bar `success`, fade a bar, or put a pill around the
  scope note.
