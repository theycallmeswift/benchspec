# MatrixGroup

The matrix structure around the cells: one bracketed caption, a summary row per
folder group, and eval rows beneath it.

**Provide:** per group, its folder name, eval count, the count of checks scoped
off each arm, and per arm the pooled rate, finished count and whether it is
still running; per eval, its id relative to the group.

| Part | Spec |
| --- | --- |
| matrix caption | one right-aligned `bs-mxcap` above the matrix: `[ 13 evals × 2 arms · live ]`, `bs-type-section-label`, `fg-tertiary` |
| width | under four arms, at 1280px and up, the matrix, caption and legend stop `size-hovercard` + `space-16` short of the right edge, leaving room for the HoverCard; from four arms they span the content width |
| arm header | arm name and, once graded, its pooled pass rate and delta as the Leaderboard shows them (`trial 97% (+35pp)`), over `harness · model`, `bs-type-meta`, top-aligned; above `breakpoint-phone` the second line wraps after ` · ` and `/`, elsewhere only when a part doesn't fit |
| group label, line 1 | folder name, `bs-type-group-name`, `fg`; when the run spans skills, led by the skill in `fg-tertiary` (`to-spec/triggers/`) and each skill's groups kept together |
| group label, line 2 | always present: `5 evals · [trial-only]`, `bs-type-meta`, `fg-tertiary`, `space-4` below; scope note as bracket text in its own `<span>`, which never breaks; wraps on phone. `[trial-only]` only when every check in the group skips the other arm(s); otherwise `[1 check trial-only]`, `[1 check skips bare]`; `1 eval`, never `1 evals` |
| group row | min-height `size-cell` + `space-24`; `space-16` above and below |
| summary per arm | `bs-type-cell` over a `size-group-bar` bar on a `line` track; the bar shows completion (`done/total`), never pass rate; under it the samples' tally in `bs-type-meta` (`3 passed · 1 failed · 2 running`, zero buckets dropped, failed and errored `danger`, quiet `warn`) |
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
- Groups start collapsed, so their summary rows are the overview; a run with
  one group starts open. A tally filter opens the groups it matches.
- Don't: colour a finished bar `success`, fade a bar, or put a pill around the
  scope note.
