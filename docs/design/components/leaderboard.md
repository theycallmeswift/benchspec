# Leaderboard

Compares arms on the finished run: arms as columns in matrix order (baseline
first), one row per metric; no arms-as-rows variant. One card on phone.

**Provide:** per arm, its name and harness · model line; per metric, a value,
optional sub-line (n, p90, total, delta, ratio) and whether it is best in its
row. Pass rate pools only checks every arm runs (label tooltip: "Excludes checks
that don't run on every arm.").

| Part | Spec |
| --- | --- |
| heading | `[ leaderboard ]`, `bs-section-label` |
| table | native `<table class="bs-lb">` with a caption; `bs-type-body-mono`, tabular numerals |
| head row, label column | `lbl` ground; metric labels `muted` in `bs-type-body-mono`; arm name `fg` 500 |
| rows | height `size-cell`, `space-12` × `space-16` padding; even rows on `band` |
| values | `bs-type-value`, `fg`, right-aligned on one edge with the arm headers |
| best value | `◆` and value in `accent-ink`, plus the word `best` once in `muted` `bs-type-meta`, so colour is not the only cue; winner only |
| ratio | losers only, against the winner by name, `muted`: `1.53× baseline` |
| delta | only the signed figure in `success` (`+34pp`); "vs baseline" in `muted` |
| errors | sub-line in `danger` (`1 boot failure`) |
| all samples errored | `—` |
| metric scoped off the arm | `n/a`, `muted` |

**Phone:** one `bs-lbc` card (`surface`, 1px `line` border, `radius-8`), metric
sections separated by hairlines. No nested panels.

- Pass rate and Skill triggered: a `bs-lbm` section, `space-16` padding: the
  metric label (`muted`, `bs-type-body-mono`; Pass rate a button with the
  tooltip), then per arm a row: name, a `size-box-sm` bar on a `line` track to
  scale, the value (`bs-type-metric`; `bs-sm` sections at `text-14`). Every bar
  `fg-tertiary`. Under the rows: `trial is best · +37pp vs baseline`, `muted`,
  signed figure in `success`.
- Other metrics: a `bs-lbl` list, one item per metric (`space-12` × `space-16`):
  label, then arm name and value pairs (`bs-four`: two pairs per line); the best
  value with `◆` in `accent-ink` plus `best` in `muted`; errors in `danger`
  (`+1 errored`).

Don't: colour a value `accent` (fails as text); repeat "best" as colour, word
and badge; colour a whole delta sentence green; sort columns or rows.
