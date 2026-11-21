# benchspec design system

**Benchmark what your agent does, not what it says.**

benchspec is a framework for evaluating AI agents with repeatable, isolated
benchmarks. Write evals as Markdown, run each eval across named benchmark arms,
and compare how agent behavior changes by harness, model, effort, and
environment.

Identity: navy ground (warm paper in light); orange only for live, selected,
focus and best; JetBrains Mono for data; the mark's bracket on every sample
group and caption. `benchspec watch` is dark-first; light (`data-theme="light"`)
is its own design, not inverted dark.

Rules live once: brand-wide here; per component in `components/<name>.md`; per
token in its `usage` ([`tokens.json`](foundations/tokens.json)); ratios in
[`contrast.md`](foundations/contrast.md).

## Voice and content

- **Tagline:** the bold line above, verbatim; no paraphrase, no second tagline.
  Bold under the wordmark in the README; two lines on the social card
  ("Benchmark what your agent does," / "not what it says.").
- **Long description:** the paragraph under the tagline, word for word: GitHub
  repo and `pyproject.toml` description, and any one-paragraph summary (package
  index, repo About, link previews).
- **Short explainer** (README lede): "benchspec runs an agent (Claude Code,
  Codex, or OpenCode) against a task in a fresh sandbox, checks what it actually
  did in the workspace, and reports the result as a comparison."
- **Voice:** direct, plain, developer-facing; short declarative sentences
  leading with what the tool does. Name real commands (`benchspec run`), files
  (`benchmark.md`), flags (`--count 3`). Second person ("your agent"). No hype,
  superlatives, exclamation marks or emoji.
- **Name:** always lowercase `benchspec`, even starting a sentence or heading;
  code style for the command.
- **Product words:** *eval*: a Markdown task. *arm*: a named configuration
  (harness × model, e.g. `baseline`, `trial`). *sample*: one run of an eval on
  an arm. *cell*: eval × arm. Compare arms in percentage points: `100% (+50pp)`.
- **State words:** uppercase PASS, FAIL, TIMEOUT, ERROR only in the drawer
  status line, assertion verdicts and terminal report (`FAIL · 6/9 assertions`).
  Lowercase elsewhere (legend, tallies, hover-card problem lines, unfinished
  labels): pass, fail, timeout, error, queued, running, booting, quiet 52s, n/a
  (never "skipped"), not run, 4 failed, stale. `quiet Ns` counts live.
- **Separators:** middle dot with spaces (`claude-code · sonnet`). Durations
  `6m 41s`, `90.9s`; money `$0.073`.
- **Captions:** section heads and the matrix caption are lowercase mono in
  brackets: `[ assertions · 3 failed · 6 passed ]`. The only exception to
  sentence case.
- **UI copy:** sentence-case labels; buttons are a verb (Copy output, Close);
  tooltips one sentence under 80 characters, ending in a period. Errors give
  what happened, then the next step; empty states name the command (copy:
  PageState). Never "sorry", "oops", "please" or exclamation marks.

## Logo

Files: [`docs/assets/`](../assets/), listed in [Logos](brand/logos.md).
`scripts/render_assets.py` generates them (`make assets`).

### Construction

64×64 grid, identical to `MARK` in `scripts/render_assets.py`:

| Part | Geometry |
| --- | --- |
| Left bracket | polyline (14,8) → (6,8) → (6,56) → (14,56), stroke 4, round caps |
| Right bracket | polyline (50,8) → (58,8) → (58,56) → (50,56), stroke 4, round caps |
| Squares | 10×10, corner radius 2.5; columns at x = 19 and 35, rows at y = 14, 27, 40 |
| Left column | three `brand-gray` squares |
| Right column | `brand-orange`, `brand-salmon`, `brand-orange` |

Joins: miter in the SVG, round in the raster renderer; both are correct.

**Wordmark:** mark plus `benchspec`, 250×64 viewBox, JetBrains Mono 600, 30px,
-0.01em, x = 72, baseline y = 43, outlined to paths. Never live text, in any
face: use the SVG files, or in UI the inline SVG bound to `wm-*`. Size: 188×48
in the README, `size-wordmark-ui` tall in the watch sheet.

### Light and dark

| Part | Light ground | Dark ground |
| --- | --- | --- |
| Brackets, text (`wm-ink`) | `brand-navy` #1B1F3B | `term-text` #E8ECF1 |
| Left squares | `brand-gray` #A5ACBE | #6E7891 |
| Right squares 1, 3 | `brand-orange` #E85D2F | `term-prompt` #F0764B |
| Right square 2 | `brand-salmon` #F2A48C | `brand-salmon` #F2A48C |

- `benchspec-mark.svg` on paper, white and the light theme;
  `benchspec-mark-dark.svg` on navy, `term-body` and the dark theme.
- UI chrome: draw the mark inline, bound to `wm-ink`, `wm-left`, `wm-right`,
  `wm-mid` (classes `bs-wm-*`), which follow the theme.
- Markdown: switch files with `<picture>` and `prefers-color-scheme` (snippet in
  [Logos](brand/logos.md)).
- Clear space on every side: at least the wordmark's bracket-to-text gap (12
  grid units, about 19% of the mark's height).
- Minimum size: 22px (`size-mark-ui`). Smaller, write `benchspec` alone.

**Don't:**

- Put the light-ground mark on navy or `term-body`.
- Recolour the squares outside the two sets above, reorder the right column, or
  add a column.
- Use the social card's five-row graphic as the logo.
- Stretch, rotate, outline, shadow or animate the mark.

## Color

- `brand-*` and `term-*` (from `scripts/render_assets.py`) are fixed in both
  themes. Other UI tokens are tuned for contrast, not brand colours.
- Text ramp: `fg`, then `muted`, then `fg-tertiary`.
- Orange marks only live, selected, focus and best: `accent` for live progress,
  the selected cell and focus (never text); `accent-ink` for the best value.
  Nothing else.
- Status boxes: StatusBox. Fail steps (`status-fail-1/2/3`) are the only graded
  colours; rates get no colour ramp.

### Contrast

- Every text colour, existing or new, clears 4.6:1, and every meaningful mark
  (box fills, rings, bars, focus) 3.2:1, on all seven grounds (`bg`, `surface`,
  `raised`, `hover`, `band`, `lbl`, `popover`) in both themes; banner text also
  on its tint. (WCAG floors: 4.5:1, 3:1.) Regenerate
  [`contrast.md`](foundations/contrast.md) after any token change.
- Never fade text or marks with opacity; check animation endpoints too.
- `line` is decorative: never the only boundary or mark that carries meaning.
- Logo colours (`brand-gray`, `brand-salmon`, `wm-left`, `wm-mid`) are exempt as
  logotype; never use them for UI text or status marks.

## Type

- **JetBrains Mono** (OFL-1.1): data and brand: numbers, ids, cells, tables,
  captions, wordmark, raster art.
- **DM Sans** (variable, optical size 9–40, OFL-1.1): prose, tabs, the drawer
  status line, tooltips.
- **Weights:** 400 and 500 in UI; 600 for the wordmark; 700 for the social-card
  wordmark only. No others.
- **Loading:** self-hosted from `fonts/` (`type.fonts`; `tokens.css` writes
  `@font-face`). Never from a third-party origin; family stacks only cover
  missing files. The woff2 files aren't committed yet: they ship with the watch
  implementation
  ([#180](https://github.com/theycallmeswift/benchspec/issues/180)). Brand art
  renders from the TTFs in `scripts/fonts/`.
- **Ligatures:** `font-variant-ligatures: none` on `.bs-app` and `.bs-brand`, so
  `--count`, `->` and `>=` render as typed.
- **Swapping a family:** new woff2 files in `fonts/`, its `type.fonts` entries,
  and the first name in `type.families.mono` or `.sans`. Nothing else:
  components read only `--font-mono`/`--font-sans` via `--type-*`.
- **Scale:** 12 / 13 / 14 / 16 / 18 / 24 / 30 / 44 (`text-*`); steps only, no
  half pixels. Each `bs-type-*` style uses one step (phone sizes in its usage).
  Brand art styles sit outside the scale.
- **Tabular numerals** (`font-variant-numeric: tabular-nums`): counter,
  counter-finished, metric, value, cell, body-mono, meta.
- **Classes:** `tokens.css` emits `.bs-type-*`; `bundle.css` adds `--type-*`
  shorthands (`font: var(--type-cell)`). All classes start with `bs-`; mockup
  class names are not for production.

## Layout, shape and depth

- **Spacing:** 4 / 8 / 12 / 16 / 24 / 32 / 40 (`space-*`); steps only, and a new
  step needs two uses. App padding `space-32` (phone `space-16`); sections
  `space-40` apart; matrix columns `space-16` apart.
- **Phone:** below `breakpoint-phone` (`@media (max-width: 639px)`),
  `bundle.css` swaps `size-cell` → `size-cell-phone`, `size-control` →
  `size-control-phone`, and the counter, counter-finished, heading and status
  sizes. Brackets stay at every width. Phone matrix: MatrixCell.
- **Radii:** `radius-*` per its usage. No pills.
- **Brackets:** the mark's bracket (square corners, round caps on the arm tips,
  1.5px `fg-tertiary`, 4px arms) frames every sample group and caps the run
  progress track; nowhere else in chrome. Captions use bracket characters.
- **Strokes:** `stroke-hair` 1px hairlines and borders; `stroke-outline` 1.5px
  outlines, the bracket frame, the selected ring; `stroke-ring` 2px rings,
  focus, the tab underline; `stroke-mark` 4px, the mark drawn as art.
- **Depth:** surface steps. Shadows (`shadow-*`) only on floating panels, each
  also with a 1px `line` border.
- **Lines:** 1px `line` hairlines separate rows.
- **Motion:** the running box's dot pulses (`dur-pulse`, scale only; the ring
  never fades); disclosures turn in `dur-disclosure`. `prefers-reduced-motion`
  stops all animation and transitions.
- **Page states:** PageState.

### Interaction states

| State | Treatment |
| --- | --- |
| hover | ground changes to `hover`; nothing else |
| selected | 1.5px inset `accent` ring, kept on hover |
| focus | 2px `accent` outline, 2px offset, on every interactive element; on a selected cell it replaces the ring, so at most one orange ring shows |
| disabled | `fg-tertiary`, `cursor: not-allowed`, no opacity fade |

## Iconography

No icon set. Status boxes, the bracket frame and mono glyphs carry meaning: `✓`
and `✗` assertion verdicts, `›` disclosures, `‹ ›` sample navigation, `×` close,
`▸` collapsed group, a 7px `accent` dot for live. No emoji.

## Imagery

- Use only the generated art ([Marketing](brand/marketing.md)).
- Screenshots of output: real runs, or shaped on real runs; never invented
  numbers.
- Show the terminal on `term-body` with its three-dot title bar.
- Status boxes in brand art follow StatusBox (pass plus real states, graded
  fails included).
- `wm-*` colours appear only in the mark and the cover blocks that echo it;
  cover blocks sit `space-24` inside the card's edges.

## Components

[StatusBox](components/status-box.md), [MatrixCell](components/matrix-cell.md),
[MatrixGroup](components/matrix-group.md), [Counter](components/counter.md),
[TopBar](components/top-bar.md), [Tabs](components/tabs.md),
[Leaderboard](components/leaderboard.md), [HoverCard](components/hover-card.md),
[Drawer](components/drawer.md), [Controls](components/controls.md),
[PageState](components/page-state.md). Approved `benchspec watch` frames:
[Mockups](mockups/README.md). Every class is in
[`components/bundle.css`](components/bundle.css) (prefix `bs-`), which reads
tokens only: no px literal except 0, 1px hairlines and the one breakpoint.
