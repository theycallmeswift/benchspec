# benchspec design system: synthesis of 12 critiques

Six models, two runs each, reviewed the design system against rubric D1–D8.
Their 170 issues collapse into 29 clusters raised by two or more models, plus 7
ideas worth a look that only one model raised (one, K3, is two models on
different elements). Every contrast claim was recomputed from the token hex
values in design-system.md.

## Summary

- **Accessibility is the largest gap.** No focus style or keyboard model exists
  in code (6/6), status boxes have no text equivalent and legend definitions
  live in `title` (6/6), and pass, fail and timeout differ only by hue at
  identical luminance (5/6; verified 1.00:1).
- **Watch cannot be built from three components.** The drawer, hover card, tabs,
  leaderboard, page states (loading, empty, disconnected) and the phone layout
  are all unspecified (6/6 each).
- **The system contradicts itself where agents will copy it.** bundle.css loads
  Google Fonts under a `default-src 'self'` page (6/6); an undecided rate ramp
  ships as live tokens and the README says to fill boxes with it (6/6); the
  `value` style says to colour text with `accent` (3/6); the hover selector
  overrides the selected ring (change 18; verified).
- **The logo is not one logo.** The wordmark is live platform text that rendered
  as a sans on the cover (6/6), and there is no dark-ground mark file (5/6). All
  six models say adopt a named mono and outline it; the font vote ties JetBrains
  Mono and IBM Plex Mono.
- **The contrast table is right; the critiques' proposed values are where errors
  were.** All 115 rows of contrast.md recompute exactly. Four colour values the
  models proposed as passing actually fail WCAG (light band, hover and yellow);
  8 claims in total were false.

## Prioritized changes

Ranked by number of models (of 6), then modal severity, then mean confidence
(high = 3, medium = 2, low = 1). "Both runs" lists the models that raised it
twice. Effort: S under a day, M one to three days, L more.

| # | Change | Models | Severity | Effort |
| --- | --- | --- | --- | --- |
| 1 | Self-host the fonts and delete the Google Fonts import | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | S |
| 2 | Implement focus and keyboard access for matrix cells | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | M |
| 3 | Specify the watch surfaces beyond the three components | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | L |
| 4 | Define page states: loading, empty, disconnected, stopped, disabled | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | M |
| 5 | Make phone a real breakpoint, and keep the brackets on it | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | M |
| 6 | Give status boxes text equivalents and replace `title` tooltips | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | high | M |
| 7 | Pull the undecided pass-rate ramp out of production tokens | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | medium (max high) | S |
| 8 | Outline the wordmark in a named font | 6/6: Opus, Gemini, Muse, Kimi, GPT, Grok | medium | M |
| 9 | Give every sample state a distinct shape | 5/6: Opus, Muse, Kimi, GPT, Grok | high | M |
| 10 | Ship `benchspec-mark-dark.svg` | 5/6: Gemini, Muse, Kimi, GPT, Grok | medium (max high) | S |
| 11 | Delete tokens with no job or a duplicate job | 5/6: Opus, Gemini, Muse, Kimi, Grok | medium (max high) | S |
| 12 | Separate light `band` from `raised` | 5/6: Opus, Gemini, Muse, Kimi, Grok | medium (max high) | S |
| 13 | Make bundle.css read tokens only | 4/6: Opus, Gemini, GPT, Grok | medium | M |
| 14 | Make the weight rule match the tokens | 4/6: Gemini, Muse, Kimi, Grok | medium | S |
| 15 | Close the type scale: integer sizes, fewer steps | 4/6: Opus, Muse, Kimi, Grok | medium | M |
| 16 | Make the cover card obey the logo rules | 4/6: Opus, Gemini, Muse, Kimi | medium | S |
| 17 | Fix the `value` style: the best value uses `accent-ink` | 3/6: Gemini, GPT, Grok | high | S |
| 18 | Separate hover, selected and focus | 3/6: Opus, GPT, Grok | high | S |
| 19 | Name colour tokens by role | 3/6: Opus, Gemini, Grok | medium | M |
| 20 | Define MatrixCell aggregation and mixed-state rules | 3/6: Gemini, Muse, GPT | medium (max high) | M |
| 21 | Theme the shadows and give floating panels an edge | 3/6: Opus, Gemini, Grok | medium (max high) | S |
| 22 | Scope the uppercase state-word rule | 3/6: Opus, Muse, Grok | medium | S |
| 23 | Snap spacing to a 4px scale | 3/6: Opus, Muse, Kimi | medium | M |
| 24 | Pulse the centre dot, not the whole running box | 2/6: GPT, Grok | high | S |
| 25 | Prefix the type classes | 2/6: Opus, Gemini | medium | S |
| 26 | Stop every animation under reduced motion | 2/6: Muse, Kimi | medium | S |
| 27 | Put `tabular-nums` in the numeric type styles | 2/6: Gemini, Muse | medium | S |
| 28 | Drop all-caps tracked section labels | 2/6: Opus, Muse | medium | S |
| 29 | Add UI copy patterns | 2/6: Opus, Kimi | low | S |

### 1. Self-host the fonts and delete the Google Fonts import

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Gemini, Muse, Kimi,
GPT, Grok · D1/D2/D6 · severity high · confidence 3.0 · effort S

- **Problem:** components/bundle.css @imports DM Mono and DM Sans from
  fonts.googleapis.com, the README says the watch page sets `default-src 'self'`
  (which blocks that request), and tokens.json `type.fonts` is an empty array.
- **Why it matters:** An agent copies the import, the fonts fail silently under
  the page's own CSP, and watch ships in the system fallback stack with no
  record of which files it should serve.
- **Fix:** Delete the `@import`. Ship four woff2 files under `/fonts/` (DM Mono
  400 and 500, DM Sans 400 and 500 with opsz 9–40, or the Q2 replacement) with
  `@font-face { font-family: "DM Mono"; src: url("/fonts/dm-mono-400.woff2") format("woff2"); font-weight: 400; font-style: normal; font-display: swap; }`
  and matching faces, Latin `unicode-range`. Fill `type.fonts` with
  `{family, weight, file, license: "OFL-1.1"}` entries. Add the rule: "watch
  never loads fonts from a third-party origin; the family stacks are fallbacks
  only for missing files." Add a render test with the CSP on and the network
  off.
- **Check:** Spot-checked: true. The @import, the `default-src 'self'` sentence
  and `"fonts": []` are all in design-system.md.

### 2. Implement focus and keyboard access for matrix cells

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Muse, Kimi, GPT,
Grok · D3 · severity high · confidence 3.0 · effort M

- **Problem:** The README specifies a 2px `accent` focus outline, but bundle.css
  has no `:focus-visible` rule, and MatrixCell is a `div` with `cursor: pointer`
  and no role, tabindex or key handling.
- **Why it matters:** An agent copying the components ships a matrix keyboard
  users cannot reach or see focus on (WCAG 2.1.1 and 2.4.7 fail).
- **Fix:** Add to bundle.css:
  `.bs-cell:focus-visible, .bs-tab:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`.
  Add to MatrixCell/README: "Each interactive cell is
  `<button type="button" class="bs-cell" aria-haspopup="dialog" aria-expanded aria-controls>`
  with `aria-label="{eval} on {arm}: {label}"`. The matrix is `role=grid` with a
  roving tabindex; arrow keys move between cells, Enter or Space opens the
  drawer, Esc closes it and returns focus to the cell. `not run` cells are plain
  text and not focusable. Never set `outline: none`; containers must not clip
  the outline." Reset native button styles in `.bs-cell`.
- **Check:** Spot-checked: true. bundle.css has no focus rule; the preview cells
  are `<div class="bs-cell">`.

### 3. Specify the watch surfaces beyond the three components

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Gemini, Kimi, GPT,
Grok · D2 · severity high · confidence 3.0 · effort L

- **Problem:** Token usage strings name about ten surfaces (cell drawer, phone
  bottom sheet, hover card, sample popover, tabs, leaderboard, top bar, theme
  toggle, segmented control, select, phase bar, sample navigation), but only
  StatusBox, MatrixCell and Counter have specs.
- **Why it matters:** An agent invents the size, spacing and states of most of
  the page, so every build of watch looks different.
- **Fix:** Add a README and preview.html per component, each with a state table
  (default, hover, focus, selected, disabled, empty), sizes, tokens and a
  do/don't list. Starting values the models converged on: **Drawer**: desktop
  panel 480px wide on `bg`, `radius-8`, padding `space-24`, sections `space-28`
  apart, header is the eval id, a 32×32 `×` close and `‹ ›` sample navigation;
  Esc closes, focus is contained and restored. Phone: full-width bottom sheet,
  top `radius-8`, `shadow-sheet`, `max-height: 85vh`. **Hover card**:
  `radius-8`, padding `space-12`, max-width 280px, `shadow-hovercard`, 1px
  border, opens on hover and focus after 150ms; on touch, tap opens the drawer
  instead. **Tabs**: `tab` style (14px sans 500), height 36px, gap `space-28`,
  inactive `muted`, active `fg` with a 2px `accent` underline, arrow-key
  navigation. **Leaderboard**: native table, head row and label column on `lbl`,
  even rows on `band`, row height `size-cell`, best value in `accent-ink` with ◆
  and the word `best`, header click sorts with ▼ in `muted` and updates
  `aria-sort`. **Top bar**: mark at `size-mark-ui`, wordmark, 7px `accent` live
  dot, theme toggle on `radius-pill` with an accessible name. **Controls**:
  native button and select, 36px tall on desktop, 44px on phone.
- **Check:** Spot-checked: true. The surfaces appear only in tokens.json usage
  strings.

### 4. Define page states: loading, empty, disconnected, stopped, disabled

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Muse, Kimi, GPT,
Grok · D2 · severity high · confidence 3.0 · effort M

- **Problem:** Nothing specifies watch before the first event, a run with zero
  cells, an empty filter result, a lost connection, an interrupted run or a
  disabled control.
- **Why it matters:** An agent invents copy and visuals for every edge case and
  can render missing data as 0%, PASS or Finished.
- **Fix:** Add an app-state table to the README. **Loading**: "Loading run…",
  `aria-busy="true"`, queued boxes, no percentage. **Zero cells**: "No cells in
  this run." and no progress bar. **Empty filter**: "No evals match these
  filters." plus a Clear filters button. **Disconnected**: keep the last data,
  remove the live dot, show a `yellow`-on-`yellow-tint` banner "Lost connection
  to benchspec run · retrying". **Stopped**: "Run stopped · N of M finished".
  Show Finished only after the run-end event. **Disabled**: `faint` text,
  `cursor: not-allowed`, the `disabled` attribute, no hover response and no
  opacity fade (an opacity fade breaks the contrast table). Rule: "Never show
  loading, missing data or disconnection as PASS, 0% or Finished." Announce
  state changes in one polite status region.
- **Check:** Spot-checked: true. No such states exist in any README or token.

### 5. Make phone a real breakpoint, and keep the brackets on it

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Gemini, GPT, Grok ·
D2 · severity high · confidence 3.0 · effort M

- **Problem:** Phone values (counter 36px, finished counter 24px, heading 24px,
  cell 32px, status 15px, 16px padding, the 640px threshold) live only in usage
  prose; there is no breakpoint token or media query; `bs-sm` shrinks boxes but
  never drops the brackets the README says it drops; the phone matrix layout is
  unspecified. The README's own phone rule also drops the brackets, which breaks
  the settled one-box-per-sample-in-brackets decision.
- **Why it matters:** Every agent picks its own breakpoint and phone sizes, and
  the shipped phone matrix loses the bracket frame that defines the brand.
- **Fix:** Add `breakpoint-phone: 640px` and phone tokens
  (`size-cell-phone: 32px`, `counter-phone: 36px`,
  `counter-finished-phone: 24px`, `heading-phone: 24px`, `status-phone: 15px`,
  `space-app-x-phone: 16px`), emitted in tokens.css under
  `@media (max-width: 639px)`. Keep brackets at every width: delete "where the
  brackets are dropped" and make `.bs-bx.bs-sm` tighten the bracket padding
  instead. Phone matrix: only the matrix scrolls horizontally, a sticky
  eval-label column about 120px wide with the 8px `shade` gradient, arm columns
  at least 160px, the rest of the page reflows at 320px. Short labels follow
  `{done}/{total} · {state}` (`2/3 · running`, `booting`, `queued`, `n/a`,
  `not run`). Markers shorten (`err`, `t/o`, `quiet`) but never disappear; drop
  only the ETA.
- **Check:** Spot-checked: true. `640px` appears only in the heading usage
  string; `.bs-sm` has no bracket rule.

### 6. Give status boxes text equivalents and replace `title` tooltips

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Gemini, Grok · D3 ·
severity high · confidence 2.89 · effort M

- **Problem:** Sample boxes are empty `<i>` elements with no accessible name;
  the nine state definitions exist only as `title` attributes in the preview,
  not in the StatusBox spec; per-sample detail is hover-only with no keyboard,
  touch or dismissal rule.
- **Why it matters:** Screen-reader, keyboard and touch users get no state
  information, and an agent ships a hover card that fails WCAG 1.4.13.
- **Fix:** Add to StatusBox/README: "`.bs-bx` has `role="img"` and
  `aria-label="3 samples: fail, running, quiet 52s"`; individual boxes are
  `aria-hidden="true"`." Add a Definition column to the state table with the
  nine sentences now in the preview's `title` attributes. Legend entries are
  visible text, or buttons that open a tooltip on hover and focus
  (`role="tooltip"`, `aria-describedby`, Esc dismisses, stays open while
  hovered); never `title`. Rule: "Everything in a hover card is also in the cell
  drawer; on touch, tap opens the drawer."
- **Check:** Spot-checked: true. Boxes are `<i class="bs-b ...">` with no label;
  definitions are only in `title`.

### 7. Pull the undecided pass-rate ramp out of production tokens

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Opus, Gemini, Muse, Kimi,
GPT, Grok · D1/D2 · severity medium (max high) · confidence 3.0 · effort S

- **Problem:** `rate-0` … `rate-100` ship as live tokens whose usage says "not
  yet chosen over status colour", while the README paragraph says "Fill sample
  boxes with it", contradicting StatusBox. The red-to-green ramp also conflicts
  with "pass is not green", and adjacent dark stops are nearly identical (rate-0
  vs rate-10 is 1.04:1).
- **Why it matters:** An agent sees two competing encodings for the same box and
  picks either one.
- **Fix:** Add to README › Color and every `rate-*` usage: "watch fills sample
  boxes with `status-*` only. Do not use `rate-*` until frame A′ is adopted."
  Move the ramp to `tokens.experimental.json`, excluded from tokens.css. If it
  is ever adopted: at most 5 stops, neighbours at least 1.3:1 apart, never on
  the same screen as status fills, pooled rate always printed beside it in `fg`.
- **Check:** Verified: rate-0 vs rate-10 is 1.04:1 (dark) and 1.06:1 (light);
  every stop does clear 3:1 on all six grounds, as the doc claims.

### 8. Outline the wordmark in a named font

6/6: Opus, Gemini, Muse, Kimi, GPT, Grok · both runs: Kimi, GPT, Grok · D7 ·
severity medium · confidence 3.0 · effort M

- **Problem:** The wordmark is live SVG `<text>` in the platform monospace
  stack, so its glyphs and width change by OS (Consolas is narrower than Menlo
  inside the fixed 250×64 viewBox), raster art uses Menlo 700, and the cover
  rendered `benchspec` as a proportional sans.
- **Why it matters:** There is no single logo; an agent placing the wordmark
  gets whatever font the machine has.
- **Fix:** Pick the brand mono (see Q1). Convert the `<text>` in both wordmark
  SVGs to outlined paths from the pinned font file (30px, x = 72, baseline y =
  43), keep `aria-label="benchspec"`, and render raster art from the same paths
  in `render_assets.py`. Replace the rule "uses the platform monospace stack"
  with "Never typeset the wordmark as live text; use the SVG files, or the
  inline SVG bound to `wm-*` in UI."
- **Check:** Verified against ds-cover.png: the cover's `benchspec` renders in a
  proportional sans.

### 9. Give every sample state a distinct shape

5/6: Opus, Muse, Kimi, GPT, Grok · both runs: Opus, GPT, Grok · D3 · severity
high · confidence 3.0 · effort M

- **Problem:** The README says each state differs in shape, but pass, fail and
  timeout are all plain filled squares that differ only by hue, and their
  luminance is identical: pass vs fail is 1.00:1 in dark; pass, fail and timeout
  are all 1.00:1 apart in light. Running and quiet are both a 2px ring with a
  centre dot; the pulse that separates them stops under reduced motion, and
  phone hides the text markers.
- **Why it matters:** In greyscale or with red-green colour blindness pass and
  fail look the same, so watch fails WCAG 1.4.1 on its core view.
- **Fix:** Keep one square per sample. **pass**: plain solid fill, the only
  plain filled square. **fail**: fill with a knocked-out ✗ (two diagonals from
  42% to 58%). **timeout**: fill with a knocked-out 2px horizontal bar at 50%
  height. **quiet**: 2px ring with a 6×2px centre bar instead of running's dot.
  Draw knockouts with a CSS `mask` so the real ground shows through on any
  surface. Add to StatusBox/README: "No two states share both shape and
  luminance. Every state is identifiable in greyscale at `size-box-phone`."
  Replace "Phone hides markers" with "Phone shortens markers to `err`, `t/o` and
  `quiet` but never hides them." Update the legend and the README's Status
  colors paragraph.
- **Check:** Verified: pass/fail 1.001:1 (dark); pass/fail 1.003, pass/timeout
  1.004, fail/timeout 1.001 (light). The contrast-table values the models quoted
  (5.86/5.87, 5.19/5.17/5.17) match the tokens.

### 10. Ship `benchspec-mark-dark.svg`

5/6: Gemini, Muse, Kimi, GPT, Grok · both runs: Gemini, Muse, GPT, Grok · D7 ·
severity medium (max high) · confidence 3.0 · effort S

- **Problem:** Only a light-ground mark ships; on dark grounds the docs say to
  recolour it with `wm-*` tokens, which an `<img>` in Markdown cannot do.
- **Why it matters:** An agent drops the only mark file on navy and the navy
  brackets vanish, which the logo rules forbid.
- **Fix:** Generate `assets/Logos/benchspec-mark-dark.svg` in `render_assets.py`
  with the same geometry: brackets #E8ECF1, left squares #6E7891, right squares
  1 and 3 #F0764B, middle #F2A48C. Add to Logos/README: "Use benchspec-mark.svg
  on paper, white and the light theme; benchspec-mark-dark.svg on navy,
  term-body and the dark theme; inline SVG bound to `wm-*` in UI chrome." Add a
  `<picture>` example.
- **Check:** Spot-checked: true. Logos/README says "There is no standalone
  dark-ground mark file".

### 11. Delete tokens with no job or a duplicate job

5/6: Opus, Gemini, Muse, Kimi, Grok · both runs: Muse, Kimi · D4 · severity
medium (max high) · confidence 2.71 · effort S

- **Problem:** `accent-tint` is "not yet used by a component"; `wm-text` equals
  `wm-bracket`; `dot-1/2/3` duplicate `wm-right`/`wm-mid`/`brand-gray`; dark
  `accent-ink` equals `accent`; the fixed `brand-*` and `term-*` values are
  redeclared in both theme blocks as if they were themable.
- **Why it matters:** An agent has two names for one job and no rule for which
  to use, and will use an unassigned tint inconsistently.
- **Fix:** Delete `accent-tint`. Merge `wm-text` into `wm-bracket` (rename
  `wm-ink`). Make `dot-*` aliases of `wm-*`. Emit `brand-*` and `term-*` once in
  `:root`, outside the theme blocks. Keep `accent-ink` separate: light needs it.
- **Check:** Spot-checked: true in tokens.css. If accent-tint is kept instead:
  accent-ink on accent-tint is 4.71:1 dark, 5.13:1 light (verified).

### 12. Separate light `band` from `raised`

5/6: Opus, Gemini, Muse, Kimi, Grok · both runs: Opus, Muse, Kimi, Grok · D4 ·
severity medium (max high) · confidence 2.67 · effort S

- **Problem:** In the light theme `band` and `raised` are both #E9EBF2.
- **Why it matters:** Banded leaderboard rows vanish in light, and an agent
  invents a third gray to fix it.
- **Fix:** Set light `band: #EEF0F5`, between `surface` #FFFFFF and `raised`
  #E9EBF2. On it every text token clears 4.97:1 and every mark 3.33:1. Do not go
  darker: #E0E3EC, #DDE1EB, #DBDEE9 and #D9DDE9 (all proposed) push `note`,
  `faint` and `red` under 4.5:1 and `rate-100` under 3:1. Regenerate
  contrast.md.
- **Check:** Verified: #EEF0F5 min text 4.97 (note), min mark 3.33 (rate-100).
  #EDEFF5 and #F5F6FA also pass.

### 13. Make bundle.css read tokens only

4/6: Opus, Gemini, GPT, Grok · both runs: Opus, Gemini, GPT · D1/D4 · severity
medium · confidence 3.0 · effort M

- **Problem:** bundle.css hard-codes spacing, type and stroke values
  (`gap: 10px`, `padding: 3px 6px`, `font: 500 11.5px`, the 20px Finished word,
  1.5px strokes, 0.7 bracket opacity, a 2.5px dot); several have no token;
  `.bs-cell` inherits body line-height 1.55 instead of the `cell` style's 1.2.
- **Why it matters:** An agent changing a token leaves the components unchanged,
  and copies untokened values into new components.
- **Fix:** Add `stroke-hair: 1px`, `stroke-outline: 1.5px`, `stroke-ring: 2px`,
  `space-3: 3px` (or round marker padding to `space-4 space-6`),
  `size-dot: 5px`, and a `finished-label` style (DM Sans 500 20px/1). Emit each
  type style as a custom property
  (`--type-cell: 400 13.5px/1.2 var(--font-mono);`) and consume it. Set bracket
  opacity to 1: at 0.7 the light bracket is 2.75:1 on `raised`. Rule:
  "bundle.css has no px literal except 0 and 1px hairlines."
- **Check:** Spot-checked: true. Verified: faint brackets at 0.7 opacity are
  2.75:1 on light raised, 3.20:1 on dark raised.

### 14. Make the weight rule match the tokens

4/6: Gemini, Muse, Kimi, Grok · both runs: Grok · D7/D6 · severity medium ·
confidence 2.8 · effort S

- **Problem:** The README allows weights 400 and 500 only, with 600 for the
  wordmark, but `social-wordmark` is 700 (Menlo has no 600).
- **Why it matters:** An agent cannot tell which rule wins and picks a weight
  per asset.
- **Fix:** Once the wordmark is outlined (Q1), set `social-wordmark` to 600 from
  the same paths. Rule: "UI uses 400 and 500. Brand art uses 600. No other
  weight."
- **Check:** Spot-checked: true. `.social-wordmark` is `font-weight: 700`.

### 15. Close the type scale: integer sizes, fewer steps

4/6: Opus, Muse, Kimi, Grok · both runs: Opus, Muse · D4/D6 · severity medium ·
confidence 2.17 · effort M

- **Problem:** 16 type roles sit on 11 sizes, including 13.5px (`cell`) and
  11.5px (`marker`), and many roles differ only by name; the 20px Finished word
  is off-scale.
- **Why it matters:** An agent has no principle for choosing a size and adds
  more one-off sizes.
- **Fix:** Use the scale 12 / 13 / 14 / 16 / 18 / 24 / 30 / 44. Round `cell`
  13.5 → 14, `marker` 11.5 → 12 (line-height 1.4) and `body` 15 → 14. Make roles
  aliases of scale steps. Set the Finished word to `title` (18px sans 500).
  Rule: "Use only scale steps; no half-pixel sizes."
- **Check:** Spot-checked: true.

### 16. Make the cover card obey the logo rules

4/6: Opus, Gemini, Muse, Kimi · both runs: Opus, Gemini · D5 · severity medium ·
confidence 2.0 · effort S

- **Problem:** The cover sets `benchspec` as 76px live text with -0.02em
  tracking and no mark (it renders as a proportional sans); in dark its ink slab
  binds to `wm-bracket` and becomes a near-white block; the gray block uses
  `brand-gray` instead of `wm-left`; `stroke-width` reads `space-4`; orange and
  salmon boxes inside a pass matrix read as failed or running samples.
- **Why it matters:** The cover is the first example an agent copies, and it
  breaks the system's own logo and status rules.
- **Fix:** Replace `.name` with the inline wordmark SVG bound to `wm-*`, no
  letter-spacing. Bind `.gray` to `wm-left` and the slab to `brand-navy` (light)
  / `raised` (dark). Add `stroke-mark: 4px`. In the art matrix, use
  `status-pass` boxes plus real states only. Add to Imagery: "Status boxes in
  brand art follow StatusBox semantics; `wm-*` colours appear only in the mark."
- **Check:** Verified against ds-cover.png and Cover/preview.html.

### 17. Fix the `value` style: the best value uses `accent-ink`

3/6: Gemini, GPT, Grok · both runs: Grok · D3 · severity high · confidence 3.0 ·
effort S

- **Problem:** tokens.json `value` usage says "the best per row takes accent",
  contradicting the rule that accent text is always `accent-ink`.
- **Why it matters:** An agent follows the machine-readable token and ships
  light-theme best values at 3.25–4.09:1, under the 4.5:1 text floor.
- **Fix:** Change the usage to "Leaderboard cell value; the best per row takes
  accent-ink, never accent." Add `.bs-best { color: var(--accent-ink); }` for
  the best value, its ◆ and stars, and print the word `best` so the ◆ is not the
  only cue.
- **Check:** Verified: light accent as text is 3.72 (bg), 4.09 (surface), 3.44
  (raised), 3.25 (hover), 3.44 (band), 3.31 (lbl).

### 18. Separate hover, selected and focus

3/6: Opus, GPT, Grok · both runs: Opus, GPT · D3/D4 · severity high · confidence
2.83 · effort S

- **Problem:** Hover (1px accent ring), selected (2px inset accent ring), focus
  (2px accent outline) and running boxes (2px accent ring) all use the same
  orange ring. `.bs-cell:not(.bs-k-out):hover` (specificity 0,3,0) beats
  `.bs-cell.bs-sel` (0,2,0), so hovering the selected cell shrinks its ring to
  1px.
- **Why it matters:** An agent ships states users cannot tell apart, and the
  selected cell loses its selection mark under the pointer.
- **Fix:** Hover changes the ground only: `background: var(--hover)`, no ring.
  Selected keeps `box-shadow: inset 0 0 0 2px var(--accent)`, declared after
  hover as `.bs-cell.bs-sel, .bs-cell.bs-sel:hover`. Focus is the outer 2px
  outline with 2px offset, so it stacks with selected. Add a combined-state
  table (hover+selected, focus+selected, focus+hover) to MatrixCell. Rewrite the
  accent rule: "accent marks live progress (live dot, progress fill, running and
  booting boxes), the selected cell and focus. Nothing else."
- **Check:** Verified: specificity computed from bundle.css selectors; the
  override is real.

### 19. Name colour tokens by role

3/6: Opus, Gemini, Grok · both runs: none · D4 · severity medium · confidence
3.0 · effort M

- **Problem:** Base colour tokens are named by look (`red`, `yellow`, `faint`);
  light `yellow` is olive #826200; `faint` does three jobs (tertiary text, pass
  fill, queued and n/a outlines), so `status-pass` cannot move without moving
  text.
- **Why it matters:** An agent reaches for `--red` or `--yellow` outside their
  roles, which D1 names as the failure case.
- **Fix:** Rename `red` → `danger`, `red-tint` → `danger-tint`, `yellow` →
  `warn`, `yellow-tint` → `warn-tint`, `ok` → `success`, `faint` →
  `fg-tertiary`. Give `status-pass` its own value instead of aliasing text.
  Rename cleanly with no deprecated aliases (pre-1.0).
- **Check:** Spot-checked: true.

### 20. Define MatrixCell aggregation and mixed-state rules

3/6: Gemini, Muse, GPT · both runs: Gemini, GPT · D2/D1 · severity medium (max
high) · confidence 2.8 · effort M

- **Problem:** Nothing says which marker wins when samples mix (error plus
  quiet), how timeout and n/a count toward rate, completion and tally, or what
  the bare `—` for an all-errored arm means; the README's running label
  (`2/3 · running · agent 1m 21s`) disagrees with the preview (`1/3 · agent`).
- **Why it matters:** An agent derives rates from box colours and writes its own
  label grammar.
- **Fix:** Rule: pooled rate, finished count and tally come from the backend and
  are never derived from box colours. Denominator 0 shows `—` plus an `error`
  marker. One marker per cell, priority `error` > `timeout` > `quiet Ns`
  (longest silence). Label template `{done}/{total} · {phase} {elapsed}`; when a
  marker is present, drop `{elapsed}`. Map timeout, quiet, booting and n/a to
  tally buckets explicitly. Add fixtures for mixed cells.
- **Check:** Spot-checked: true. The label mismatch is in MatrixCell/README vs
  preview.html.

### 21. Theme the shadows and give floating panels an edge

3/6: Opus, Gemini, Grok · both runs: Gemini · D4 · severity medium (max high) ·
confidence 2.75 · effort S

- **Problem:** All three shadows are one unthemed `rgba(0,0,0,0.35)`; floating
  panels sit on `bg`, which is only 1.10:1 from `surface`, and a black shadow
  barely shows on #14172E.
- **Why it matters:** Hover cards and sheets blend into the page in dark and
  look muddy in light.
- **Fix:** Light: `shadow-popover: 0 8px 24px rgba(27,31,59,0.14)`,
  `shadow-hovercard: 0 10px 28px rgba(27,31,59,0.16)`,
  `shadow-sheet: 0 -12px 32px rgba(27,31,59,0.16)`. Dark: raise the alpha to
  0.6. Every floating panel gets a 1px `line` border so its edge does not depend
  on the shadow.
- **Check:** Verified: bg vs surface is 1.096:1 (dark) and 1.099:1 (light).

### 22. Scope the uppercase state-word rule

3/6: Opus, Muse, Grok · both runs: Grok · D8 · severity medium · confidence 2.5
· effort S

- **Problem:** Voice says state words are uppercase (PASS, FAIL, TIMEOUT,
  ERROR), but the legend, markers and tally use lowercase.
- **Why it matters:** An agent either uppercases the legend or ignores the rule
  everywhere.
- **Fix:** Replace the bullet with: "Uppercase PASS, FAIL, TIMEOUT, ERROR only
  in the drawer status line, assertion verdicts and the terminal report
  (`FAIL · 6/9 assertions`). Legend labels, cell markers, tallies and unfinished
  labels are lowercase: pass, fail, timeout, error, queued, running, booting,
  quiet 52s, n/a, not run, 4 failed. The number in `quiet Ns` is live."
- **Check:** Spot-checked: true.

### 23. Snap spacing to a 4px scale

3/6: Opus, Muse, Kimi · both runs: Opus, Muse · D4 · severity medium ·
confidence 2.0 · effort M

- **Problem:** Spacing has 12 steps "not a strict grid" (4, 6, 8, 10, 12, 14,
  16, 20, 24, 28, 36, 40) and no rule for choosing between neighbours.
- **Why it matters:** An agent picks 10 or 12, 28 or 36 at random, and adds new
  steps.
- **Fix:** Use 4 / 8 / 12 / 16 / 24 / 32 / 40. Map 6 → 8, 10 → 8, 14 → 12, 20 →
  24, 28 → 32, 36 → 32. Rule: "Use only scale steps; a new step needs two uses."
- **Check:** Spot-checked: true.

### 24. Pulse the centre dot, not the whole running box

2/6: GPT, Grok · both runs: GPT, Grok · D3 · severity high · confidence 2.75 ·
effort S

- **Problem:** `bs-pulse` fades the whole running box to opacity 0.45, so its
  accent ring drops to 2.10:1 on dark `raised` and 1.77:1 on light `raised`,
  below 3:1 for roughly 40% (dark) and 65% (light) of each 1.6s cycle.
- **Why it matters:** The only non-text running signal fails the 3:1 mark floor
  the contrast table claims, and the box flickers toward looking queued.
- **Fix:** Keep the 2px ring at opacity 1. Draw the dot as `.bs-b-run::after`
  (5px, `status-running`) and animate only it (scale 1 → 0.7). Under
  `prefers-reduced-motion` the dot stops at full size. Check contrast at both
  animation endpoints, not only on opaque token pairs.
- **Check:** Verified: blended accent at 45% is 2.10:1 on dark raised (#7D4945)
  and 1.77:1 on light raised (#E4A390).

### 25. Prefix the type classes

2/6: Opus, Gemini · both runs: Gemini · D4 · severity medium · confidence 3.0 ·
effort S

- **Problem:** tokens.css emits global `.cell`, `.counter`, `.status`, `.title`,
  `.body`, `.value`, `.tab`, `.meta`, which collide with the unprefixed mockup
  classes and common names.
- **Why it matters:** An agent's own `.status` or `.title` silently picks up
  type styles.
- **Fix:** Emit `--type-*` custom properties and `.bs-type-*` classes. Rule:
  "All benchspec classes start with `bs-`; mockup class names are not for
  production."
- **Check:** Spot-checked: true.

### 26. Stop every animation under reduced motion

2/6: Muse, Kimi · both runs: none · D3 · severity medium · confidence 2.5 ·
effort S

- **Problem:** The README says the 0.15s disclosure turn stops under reduced
  motion, but bundle.css has no disclosure rule and its media query stops only
  the pulse.
- **Why it matters:** An agent adds the drawer and disclosure transitions with
  its own durations and forgets reduced motion.
- **Fix:** Add `dur-disclosure: 0.15s` and `dur-pulse: 1.6s` tokens and use
  them. Replace the media query with
  `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; transition-duration: 0.01ms !important; } }`.
- **Check:** Spot-checked: true.

### 27. Put `tabular-nums` in the numeric type styles

2/6: Gemini, Muse · both runs: Muse · D6 · severity medium · confidence 2.0 ·
effort S

- **Problem:** The README says to turn on tabular numerals wherever numbers
  stack, but no type class sets it; only `.bs-app` and `.bs-counter` do.
- **Why it matters:** An agent using the type classes outside those wrappers
  gets proportional figures in DM Sans numbers.
- **Fix:** Add `font-variant-numeric: tabular-nums` to `counter`,
  `counter-finished`, `metric`, `value`, `cell`, `body-mono`, `meta` and
  `marker`. (DM Mono digits are already fixed-width; the rule matters for sans
  numbers and any font swap.)
- **Check:** Spot-checked: true.

### 28. Drop all-caps tracked section labels

2/6: Opus, Muse · both runs: none · D5 · severity medium · confidence 2.0 ·
effort S

- **Problem:** `section-label` is uppercase 12px mono, 0.08em tracked, `muted`:
  the stock AI-dashboard look D5 calls out.
- **Why it matters:** Every section head in watch reads as a generic template.
- **Fix:** Sentence case, 13px DM Sans 500, `muted`, no tracking. Optionally set
  the label in faint brackets (`[ Assertions ]`) so the mark's motif replaces
  the caps.
- **Check:** Spot-checked: true.

### 29. Add UI copy patterns

2/6: Opus, Kimi · both runs: Opus, Kimi · D8 · severity low · confidence 2.0 ·
effort S

- **Problem:** Voice covers the tagline, nouns, separators and state words, but
  not buttons, empty states, errors or tooltips.
- **Why it matters:** The copy an agent writes most has no pattern.
- **Fix:** Add: "UI labels are sentence case. Buttons are a verb (Copy output,
  Close). Tooltips are one sentence under 80 characters, ending in a period.
  Errors say what happened and the next command:
  `Lost connection to benchspec run · retrying in 5s`. Empty states name the
  command: `No run yet. Start one with benchspec run.` Never sorry, oops, please
  or exclamation marks."
- **Check:** Spot-checked: true.

## Consider

Raised by one model, or by two models about different elements. Not ranked.

### K1. Separate dark `accent` from dark `red` (Opus, D3, high)

- **Problem:** Dark accent #F0764B and red #E6726D are 1.06:1 apart in luminance
  and 13° apart in hue, so the selected and running rings read as error rings.
- **Fix:** Move dark `red` to #F27089 (4.80:1 on hover, 5.10:1 on red-tint).
  That gives a 27° hue gap; push slightly further if you adopt the model's "30°
  minimum" rule.
- **Check:** Verified: 1.058:1; #F27089 ratios confirmed.

### K2. Split light `yellow` into a mark tone and an ink tone (Gemini, D4, medium)

- **Problem:** Light `yellow` #826200 reads as brown when used as the timeout
  fill and quiet ring; in light, fail and timeout fills also have identical
  luminance (1.00:1).
- **Fix:** Keep #826200 for text. The model's proposed mark value #B88600 is
  false (2.59:1 on hover; fails 3:1 on 5 of 6 grounds). #A57A00 is the lightest
  amber tested that clears 3:1 on all six light grounds (3.10:1). The shape fix
  (change 9) matters more.
- **Check:** False value in the original fix; corrected value verified.

### K3. Add margin to pairs that pass at the floor (Muse, Kimi, D3, low)

- **Problem:** Several pairs pass by under 0.02: dark `faint` on hover 4.500,
  dark `red` on hover 4.503, light `note` on hover 4.502, light `accent` on
  `line` 3.016, light `rate-100` on hover 3.01. Separately, the 11.5px/1 marker
  is tight (muse).
- **Fix:** Add to contrast.md: "No passing pair below 3.2:1 (marks) or 4.6:1
  (text); regenerate after any token change." Candidate values: dark `faint`
  #939AB0 (4.82 on hover), light `accent` #D8430F (3.27 on line, ≥3.53 on all
  grounds). Two models raised two different pairs, so this is kept out of the
  ranked list.
- **Check:** Verified. kimi's "≈3.4:1 on line" for #D8430F is actually 3.27;
  still passes.

### K4. Rename CSS classes to the state words (Kimi, D1, medium)

- **Problem:** Classes don't match the vocabulary: quiet is `stale`, timeout
  `to`, queued `wait`, not run `out`; `size-box-phone` is also used for four-arm
  desktop cells.
- **Fix:** Rename to `bs-b-quiet`, `bs-b-timeout`, `bs-b-queued`, `bs-k-notrun`,
  `size-box-sm`; make token, class and word three columns of one table row. No
  deprecated aliases (pre-1.0).
- **Check:** Spot-checked: true.

### K5. Give the progress bar progress semantics (GPT, D3, medium)

- **Problem:** The Counter bar has no ARIA role or announcement rule.
- **Fix:**
  `role="progressbar" aria-label="Run completion" aria-valuemin="0" aria-valuemax="{M}" aria-valuenow="{N}" aria-valuetext="{N} of {M} cells finished"`;
  a polite, atomic status region announces count changes at most every 10s and
  the terminal state immediately; never elapsed time.
- **Check:** Spot-checked: true.

### K6. Check icon glyph coverage in the UI mono (Grok, D6, medium)

- **Problem:** Iconography sets ✓ ✗ › ‹ ▸ ▼ ◆ in the mono face; the model says
  DM Mono lacks them.
- **Fix:** Check the chosen mono's cmap. If it lacks a glyph, either pick a mono
  that has it (Q2) or ship 12px `currentColor` SVGs (check, x, chevrons, carets,
  diamond, close).
- **Check:** Unverified offline. ‹ › are standard Latin punctuation and almost
  certainly present, so the claim is at least partly wrong.

### K7. Raise the minimum mark size and theme the light mark (Grok, D7, low)

- **Problem:** At 22px the 4-unit bracket stroke is 1.4px; on light grounds the
  left squares (2.07:1) and middle square (1.83:1) are faint.
- **Fix:** Optional: minimum mark 32px (2px stroke); light `wm-left` #6E7891
  (4.02:1 on paper), `wm-mid` #C46A52 (3.45:1). Logotypes are exempt from
  1.4.11, so this is brand polish, not compliance.
- **Check:** Verified ratios.

## Q1: brand font

| Model | Adopt? | Font | Reason |
| --- | --- | --- | --- |
| anthropic/claude-opus-5.5 | yes | JetBrains Mono (700 run 1, 600 run 2), outlined | Platform stack varies by OS and fell back to sans on the cover; JetBrains has real 600–700; squared forms match the mark. |
| google/gemini-4-argon | yes | JetBrains Mono 600, outlined (run 2: or IBM Plex Mono) | Menlo and Consolas lack a true 600, so the weight snaps; a pinned face fixes glyphs and advance width. |
| meta/muse-spark-1.3 | yes | IBM Plex Mono 600 (both runs), outlined | Stable metrics lock the 250×64 lockup; squared technical forms echo the bracket matrix; stays distinct from UI DM Mono. |
| moonshotai/kimi-k3 | yes | Split: JetBrains Mono (run 1), IBM Plex Mono 600 (run 2), outlined | The most reproduced asset is the least stable; both have true 600; Plex stays distinct from DM Mono. |
| openai/gpt-6-astra | yes | DM Mono 500 (both runs), outlined | The problem is reproducibility; reusing DM Mono avoids a third family and ties the logo to watch's data type. |
| x-ai/grok-4.7 | yes | IBM Plex Mono 600 (both runs), outlined | The system stack draws different logos; Plex has a real 600, tabular figures and a tone close to the current stack. |

**Consensus:** 6/6 adopt a named font and outline the wordmark to paths. Font
split: JetBrains Mono 2 models (Opus, Gemini), IBM Plex Mono 2 (Muse, Grok),
Kimi split between them, DM Mono 500 1 (GPT). By runs: JetBrains 5, Plex 5, DM
Mono 2.

**Recommendation:** Adopt IBM Plex Mono SemiBold 600 for the wordmark and brand
art, and outline it to paths (change 8). The vote is tied with JetBrains Mono;
Plex wins the tie-break on D5: JetBrains Mono is the default mono of developer
tools and AI UIs, Plex is less common, has a true 600, and its squared forms
suit the bracket mark. DM Mono 500 (GPT) is the low-change fallback but has no
600, so the wordmark would lose weight.

## Q2: UI pairing

| Model | DM Sans | DM Mono | Alternative | Reason |
| --- | --- | --- | --- | --- |
| anthropic/claude-opus-5.5 | keep | replace | JetBrains Mono for data (both runs) | DM Mono's swash f is conspicuous in 'failed' and 'finished'; it stops at 500 and can never match the wordmark. |
| google/gemini-4-argon | replace | replace | JetBrains Mono + IBM Plex Sans or Inter (both runs) | DM Mono is wide with quirky terminals, wastes space in dense cells and clashes with the wordmark. |
| meta/muse-spark-1.3 | keep | split | Split: run 1 replace DM Mono with Plex or JetBrains; run 2 keep both | Run 1: DM Mono is light and weak at 11.5–13.5px. Run 2: the DM pair is clean and OFL; just self-host it. |
| moonshotai/kimi-k3 | keep | split | Split: run 1 replace DM Mono with JetBrains; run 2 keep both | Run 1: DM Mono is spindly on navy and caps at 500. Run 2: DM Mono's texture avoids the JetBrains-everywhere dev-tool look. |
| openai/gpt-6-astra | keep | keep | Keep both (both runs) | Swapping fonts fixes none of the real failures (font loading, contradictory rules, missing components). |
| x-ai/grok-4.7 | replace | replace | IBM Plex Sans + IBM Plex Mono (both runs) | DM Sans is a default AI-dashboard face; DM Mono is wide, lacks icon glyphs and has no 600; one Plex family unifies logo, data and prose. |

**Consensus:** Keep DM Sans: 4/6 models (Opus, Muse, Kimi, GPT). Replace DM
Mono: 3/6 firmly (Opus, Gemini, Grok), 2 split (Muse, Kimi), 1 keep (GPT); 8 of
12 runs replace it. Named replacement: JetBrains Mono in 5 runs, IBM Plex Mono
in 3.

**Recommendation:** Keep DM Sans for prose, tabs and tooltips. Replace DM Mono
with the Q1 brand mono, IBM Plex Mono, at 400 and 500 for data, so one mono
covers the wordmark, terminal art and watch data and the "never set the wordmark
in DM Mono" rule goes away. Self-host it (change 1) and check its cmap for the
iconography glyphs (Consider K6). Do this after changes 1–6: GPT is right that
the font is not what breaks watch today.

## Rejected and false claims

IDs are model, run and zero-based issue index: `opus1-04` is Opus run 1, fifth
issue. ds-synthesis.json lists the IDs behind every cluster.

**Against settled decisions**

- **Drop the bracket frame on phone (and on four-arm desktop cells).** Breaks
  the settled decision: one square box per sample, framed by brackets. Change 5
  keeps the brackets at every width; the design system's own phone rule has to
  change too. (opus1-04, gemini1-02, gemini2-02, muse1-04, muse1-07, grok1-05,
  grok2-01)
- **Make the quiet box a circle (`border-radius: 50%`).** Breaks the settled
  one-square-box decision. Change 9 uses a square with a centre bar. (kimi2-04)

**False (dropped)**

- gemini2-09: "Light `yellow` #B88600 is 3.15:1 on `hover`." It is 2.59:1, and
  fails 3:1 on 5 of 6 light grounds (2.59–3.26).
- kimi1-05: "Light `hover` #D9DDE9 keeps every text token above 4.5:1." `note`
  4.17, `faint`, `red` and `yellow` also fall under 4.5:1; `rate-100` drops to
  2.79:1.
- muse1-08: "Light `band` #E0E3EC still passes 4.5:1 text and 3:1 marks." `note`
  4.42, `red` 4.43, `faint` 4.44; `rate-100` 2.96.
- grok2-11: "On light `band` #DDE1EB, `fg` and `faint` stay above 4.5:1."
  `faint` is 4.36:1 (`red` 4.34, `yellow` 4.34, `note` 4.33); `rate-100` is
  2.90. The stated reason (a darker ground) lowers contrast for dark text.
- kimi2-12: "DM Mono has no italic." DM Mono ships italics at 300, 400 and 500.
- gemini1-11: "The cover's 14px regular tagline breaks the
  bold-under-the-wordmark rule." That rule is scoped to the README and the
  social card, not the cover card.
- kimi2-11: "`space-14` is used exactly once." Its usage lists two jobs, and
  bundle.css uses 14px in both the legend gap and the counter row gap.
- muse1 Q2: "DM Mono has weak tabular alignment." DM Mono is monospaced; its
  digits are already uniform width.

**Unsafe fix and corrected numbers (cluster kept)**

- kimi2-08: "Light `band` #E2E5EF with `hover` moved to #DBDEE9." No pass claim
  was made, but #DBDEE9 fails: `note` 4.22, `rate-100` 2.82. Dropped.
- grok2-07: "The 45% pulse blends the ring to about 2.9:1 on dark `raised`." It
  is 2.10:1. The conclusion (below 3:1) holds; change 24 is kept.
- grok2-05: "`.bs-cell:hover` has specificity 0,2,1." It is 0,3,0. The
  conclusion (it overrides `.bs-sel`) holds; change 18 is kept.

**Verified contrast claims:** pass/fail/timeout luminance (Opus, GPT, Grok,
Muse); light `accent` as text 3.25–4.09:1 (Gemini, GPT, Grok); pulse below 3:1
(GPT, Grok); `faint` on dark hover 4.50 and `#939AB0` at 4.82 (Muse); `accent`
on light `line` 3.02 (Kimi); bg vs surface 1.10 (Grok); rate-0 vs rate-10 1.04
(Opus); faint 5.35 on dark surface (Grok); accent vs red 1.06 and `#F27089` at
4.80/5.10 (Opus); accent-ink on accent-tint 4.71/5.13 (Kimi); light mark squares
2.07/1.83 and `#6E7891`/`#C46A52` at 4.02/3.45 (Grok); light band `#EEF0F5`,
`#EDEFF5`, `#F5F6FA` pass (Opus, Muse, Kimi, Gemini). The design system's own
table: 115 rows recomputed, 0 mismatches.

## Run stats

All 12 runs returned valid JSON with issues, both questions and a top three.

| Model | Cost (2 runs) | Seconds per run | Issues per run | High / medium / low |
| --- | --- | --- | --- | --- |
| anthropic/claude-opus-5.5 | $1.13 | 132 / 148 | 15 / 15 (avg 15) | 13 / 15 / 2 |
| google/gemini-4-argon | $0.28 | 74 / 70 | 12 / 13 (avg 12.5) | 8 / 14 / 3 |
| meta/muse-spark-1.3 | $0.16 | 114 / 79 | 15 / 15 (avg 15) | 18 / 12 / 0 |
| moonshotai/kimi-k3 | $0.33 | 73 / 40 | 14 / 14 (avg 14) | 9 / 13 / 6 |
| openai/gpt-6-astra | $1.21 | 104 / 102 | 15 / 14 (avg 14.5) | 7 / 22 / 0 |
| x-ai/grok-4.7 | $0.76 | 716 / 618 | 14 / 14 (avg 14) | 13 / 14 / 1 |
| **Total** | **$3.87** | | 170 issues | |
