# benchspec aesthetic critique: synthesis

Six models critiqued design system v8 and the watch frames against the aesthetic
rubric (E1–E7). There were 9 usable runs and 99 observations. Observations are
clustered by element and point, and each cluster counts distinct models. Every
color below was checked for WCAG AA against `design-system.md` values: text
needs at least 4.5:1, with the system's own 4.6:1 target, and marks need at
least 3:1, with a 3.2:1 target. Where a model's value failed, this file says so
and gives a corrected value.

## Summary

- **Light theme: all six models said move to warm paper.** Three models
  independently landed on the same background, `#F6F4EF`. The full verified set
  is under Q3. The only text change it forces is a lighter amber `warn`. Every
  other text token passes as it is.
- **One mono face (5/6).** The wordmark uses the platform stack while the UI
  uses DM Mono, and the type page's tagline falls back to a Courier-like face.
  Every model voted IBM Plex Mono for that one face. The maintainer picked
  JetBrains Mono, and the same unification applies with it. All six models
  flagged JetBrains as the "IDE default" look. The rest of the system has to
  give it character (see Q1).
- **The bracket from the mark is the identity, and it's underused (5/6).** Draw
  the cell brackets with the mark's real geometry: square corners and round
  caps. Then carry the bracket to page scale as lowercase bracketed mono
  captions (4/6).
- **Accent discipline.** Orange and green both pile onto the leaderboard's best
  values (5/6), the phase bar's agent segment is the loudest orange on screen
  (4/6), and selection stacks two orange rings (3/6). Say "best" once, use one
  ring, and give the phase bar a neutral ramp.
- **Quiet the structure.** Fix the bright white group-summary rules (4/6), the
  inverted hover-card elevation (4/6), the mixed radii and pill toggle (4/6),
  the candy-pink fail color in dark (3/6), the outlined wall of queued cells and
  the banded leaderboard (2/6 each). Almost every fix is a token or one CSS rule
  (effort S).

## Direction

Refined benchspec is the same navy instrument, quieter and more exact. JetBrains
Mono carries everything that is data, wordmark included, and it is drawn with
the brackets the mark already owns. Each sample sits in brackets with the mark's
square corners and round caps. Sections are labelled like figures in an
engineering drawing (`[ assertions · 3 failed · 6 passed ]`). Structure comes
from hairlines and three neutral greys, not from banded fills, bright rules or a
wall of outlined cells. Orange appears only where something is live, selected or
best, once each, and failures are a true red rather than candy pink. The light
theme sits on a restrained warm paper (`#F6F4EF`) with the same navy ink,
lighter pass squares and an amber rather than olive warning. It stands next to
Tailscale as a theme in its own right, not as dark mode inverted.

## Prioritized refinements

Ranked by model count, then severity, then confidence. "n/6" counts distinct
models; repeat runs of one model count once.

| # | Title | Kind | Models | Severity | Effort |
| --- | --- | --- | --- | --- | --- |
| 1 | Warm the light theme to restrained paper | problem | 6/6 · Gemini, Muse, Kimi, GPT, Grok, Claude | high | S |
| 2 | One mono face for wordmark, data and labels (JetBrains Mono) | problem | 5/6 · Gemini, Muse, Kimi, GPT, Claude | high | M |
| 3 | Draw the cell brackets with the mark's geometry | push | 5/6 · Gemini, Muse, GPT, Grok, Claude | high | S |
| 4 | Leaderboard: say "best" once | problem | 5/6 · Muse, Kimi, GPT, Grok, Claude | medium | S |
| 5 | Quiet the group-summary rules | problem | 4/6 · Gemini, GPT, Grok, Claude | medium | S |
| 6 | Phase bar: legible segments, one accent | problem | 4/6 · Gemini, Muse, Kimi, Grok | medium | S |
| 7 | Section labels as bracketed mono captions | push | 4/6 · Gemini, Muse, Kimi, Claude | medium | S |
| 8 | Hover card: lift it above the cells | problem | 4/6 · Muse, Kimi, GPT, Grok | medium | S |
| 9 | One radius scale, no pill | problem | 4/6 · Kimi, GPT, Grok, Claude | low | S |
| 10 | Fail is red, not candy pink (dark) | problem | 3/6 · Muse, GPT, Grok | high | S |
| 11 | Light status marks: quieter pass, amber warn | problem | 3/6 · Gemini, Kimi, Claude | medium | S |
| 12 | One ring for selection | problem | 3/6 · Muse, Kimi, Grok | medium | S |
| 13 | Group-name column: one anatomy | problem | 3/6 · Gemini, Grok, Claude | medium | S |
| 14 | Counter: weight and a quieter unit | problem | 2/6 · Muse, Grok | high | S |
| 15 | Quieter queued cells | problem | 2/6 · GPT, Claude | medium | S |
| 16 | Leaderboard table: rules, not fills | problem | 2/6 · Gemini, Claude | medium | S |
| 17 | Drawer section rhythm | problem | 2/6 · Muse, GPT | medium | S |
| 18 | Bracket caps on the run progress track | push | 2/6 · Muse, Kimi | medium | S |
| 19 | Legend as its own band | problem | 2/6 · Gemini, Kimi | low | S |

### 1. Warm the light theme to restrained paper

- **Kind:** problem · **Models:** 6/6 (Gemini, Muse, Kimi, GPT, Grok, Claude;
  all 9 runs) · **Severity:** high · **Effort:** S
- **Seen:** A-light, the whole page, and DS-colors light·A6–E7. The light theme
  uses `bg #F3F4F8`, `surface #FFFFFF`, `raised #E9EBF2` and `line #DADDE7`.
  These blue-grays make it read as dark mode inverted, or a generic SaaS
  dashboard. Verified.
- **Why:** Tailscale (REF-I) is warm off-white and Warp (REF-D) is paper-white.
  The cool grays make the orange look pasted on and leave the light theme
  without a character of its own. Stripe Docs (REF-G) is actually cool white, so
  the warmth comes mostly from Tailscale. Keep the chroma low ("not cream", as
  GPT and Grok put it).
- **Change:** the light grounds below, verified in Q3. Keep the navy text ramp
  (`fg #1B1F3B`, `muted #4A5262`, `fg-tertiary #5E657B`) unchanged: it passes on
  every new ground.

  | Token | Now | New |
  | --- | --- | --- |
  | `bg` | #F3F4F8 | **#F6F4EF** |
  | `surface` | #FFFFFF | **#FDFCF9** |
  | `raised` | #E9EBF2 | **#EEEBE4** |
  | `hover` | #E2E5EF | **#E9E5DC** |
  | `band` | #EEF0F5 | **#F2EFE9** (retired if #16 lands) |
  | `lbl` | #E4E7EF | **#EBE7DF** (retired if #16 lands) |
  | `line` | #DADDE7 | **#E2DDD3** |

  Optional: set `brand-paper` to `#F6F4EF` (Kimi, Grok) so the social card
  matches. `brand-orange` on it stays 3.16:1, the same as today. The pass and
  warn changes that go with this are in #11.

### 2. One mono face for wordmark, data and labels (JetBrains Mono)

- **Kind:** problem · **Models:** 5/6 (Gemini, Muse, Kimi, GPT, Claude). Grok
  backs it in its Q1 answer. · **Severity:** high · **Effort:** M
- **Seen:** DS-type·A2–E4 shows three families: DM Mono, DM Sans and the
  platform stack for the wordmark. The wordmark renders as SF Mono or Menlo
  depending on the machine. The social tagline at DS-type·C5 falls back to a
  Courier-like face (verified). In the top bar at A·B1, the mark's wordmark face
  sits next to the run id in DM Mono (Kimi).
- **Why:** the brand's most visible type is its least controlled. Two monos in
  one line read as a rendering bug.
- **Change:** use the maintainer's pick, JetBrains Mono (OFL-1.1, weights
  100–800).
  - Self-host `jetbrains-mono-400/500/600.woff2` in `fonts/`. Add 700 for the
    social art only.
  - Set `--font-mono` and `--font-wordmark` to
    `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace`.
  - Set `bs-type-wordmark` to 600, 30px, `letter-spacing: -0.01em`;
    `bs-type-social-wordmark` to 700; `bs-type-social-tagline` and
    `bs-type-terminal` to 400.
  - Convert the wordmark text in `benchspec-wordmark-*.svg` to outlined paths
    (Gemini), so it doesn't depend on installed fonts.
  - Render the PNGs in `render_assets.py` with the bundled JetBrains Mono TTF
    instead of Menlo.
  - Add `font-variant-ligatures: none` on `.bs-app` and the brand art. JetBrains
    Mono turns code ligatures on by default.
  - Delete the logo rule "Don't set the wordmark in DM Mono or any other face"
    and rewrite the Type section to name one mono.

### 3. Draw the cell brackets with the mark's geometry

- **Kind:** push · **Models:** 5/6 (Gemini, Muse, GPT, Grok, Claude) ·
  **Severity:** high · **Effort:** S
- **Seen:** A·C4–E7 and DS-components·B2. The bracket frames read as thin
  punctuation beside the solid squares, not as the mark (verified). Two of the
  stated causes are wrong. The brackets are not text glyphs (Gemini) and they
  are not 1px (Claude). They are CSS borders: `stroke-outline` 1.5px in
  `fg-tertiary`, 4px arms, `radius-2` outer corners. The real difference from
  the mark is shape. The mark has square (mitre) corners and round caps on the
  arm tips; the cells have rounded corners and square tips.
- **Why:** the bracket is the one shape that makes the matrix benchspec rather
  than a template grid (E1).
- **Change:** draw `.bs-bx::before` and `::after` as an SVG polyline mask with
  mitre corners and round caps, using `stroke-outline` (1.5px) and `space-4`
  arms over the full frame height. The mark's stroke is 4/48 of its height,
  about 1.8px on a 22px frame, so keep 1.5px rather than GPT's 1px. Keep the
  color `fg-tertiary` (dark at least 4.82:1, light at least 4.61:1 on every
  ground) and add no opacity: Claude's 70% and Grok's 80% would break the "never
  fade marks" rule. Pair with #15, so the brackets, not the cell containers,
  become the matrix's structure (GPT).

### 4. Leaderboard: say "best" once

- **Kind:** problem · **Models:** 5/6 (Muse, Kimi, GPT, Grok, Claude) ·
  **Severity:** medium · **Effort:** S
- **Seen:** at B·E3–F5, a best value carries an orange ◆, an orange figure, an
  orange word "best" and a green "+34 pts vs baseline". On the phone, at
  D2·A4–A5, "trial is best · trial +34 pts vs baseline" is all green. Verified.
- **Why:** three or four decorations on one number make the table look decorated
  rather than measured, and green competes with orange for the same meaning.
- **Change:** keep ◆ and the value in `accent-ink`. Set the word `best` in
  `muted` at `bs-type-meta`; keep it, because the DS needs a cue that isn't
  color. In deltas, color only the signed figure (`+34 pts`) `success` and set
  "vs baseline" in `muted`. On the phone, set the sentence in `muted` with only
  the signed delta in `success`. `muted` is at least 6.09:1 in dark and 6.25:1
  on the new light grounds. Grok's idea of a 5px square instead of ◆ is under
  Consider.

### 5. Quiet the group-summary rules

- **Kind:** problem · **Models:** 4/6 (Gemini, GPT, Grok, Claude) ·
  **Severity:** medium · **Effort:** S
- **Seen:** at B′ (part 2)·C4–F6 and A·C5–E5, finished collapsed groups draw a
  near-white full-width rule, and the stack of them makes a bright ladder down
  the page (verified). These are the brightest lines on the page, and they carry
  the least news.
- **Change:** draw the summary bar 2px tall on a `line` track, inset to the cell
  column width. Fill finished groups with `fg-tertiary`: dark #939AB0 on the
  #2A3053 track is 4.55:1, light #5E657B on the new #E2DDD3 line is 4.28:1.
  In-progress groups keep the `accent` fill. Three proposals were corrected:
  Claude's 50% opacity fill becomes solid `fg-tertiary`, GPT's #626B83 fails
  against the track (2.40:1), and Grok's `line`-only fill can't carry meaning
  because `line` is decorative only.

### 6. Phase bar: legible segments, one accent

- **Kind:** problem · **Models:** 4/6 (Gemini, Muse, Kimi, Grok) · **Severity:**
  medium · **Effort:** S
- **Seen:** at C·F6, five phases share one strip. bind, boot, setup and grade
  can barely be told apart: `muted` #A8AEBD (boot) and `note` #A5ACBE (grade)
  are 1.02:1 apart. The labels run on one line below, unconnected to the
  segments, and the agent segment is the loudest orange on the screen. Verified.
- **Change:**
  - Bar 4px, the same as `size-progress`; retire `size-phase`. Keep the existing
    2px `bg` gaps.
  - bind, boot and setup in `fg-tertiary`, as one "prep" tone. agent in
    `accent`, grade in `fg`.
  - Prefix each label with an 8px square swatch (`radius-2`) in its segment
    color, all on one row, `space-16` apart.
  - Retire the `note` token.
  - Amend the accent rule to list the agent segment. The DS currently
    contradicts itself: accent marks live progress and "nothing else", but the
    Drawer spec puts agent in `accent`.

  Contrast on the drawer's `bg`: `fg-tertiary` 6.28:1, `accent` 6.21:1, `fg`
  14.85:1 in dark; 5.27, 4.04 and 14.63:1 in light. Kimi r1's alternative takes
  accent off agent entirely; choose it only if a strict accent rule matters
  more. Not adopted: Grok's boot in `warn` yellow and grade in `success` green,
  which reuse status meanings, and Gemini's bind #4A5262, which is 2.24:1 on
  `bg` and fails 3:1.

### 7. Section labels as bracketed mono captions

- **Kind:** push · **Models:** 4/6 (Gemini, Muse, Kimi, Claude) · **Severity:**
  medium · **Effort:** S
- **Seen:** "Assertions", "Phases" and "Recent output" (C·E3–E7) and
  "Leaderboard" (B·A3) are 13px DM Sans 500 in sentence case. They read as
  generic app headings. Verified.
- **Why:** Warp's `[ fig. 1 — the factory ]` and Tailscale's small mono labels
  are the references' clearest shared trait, and the brackets are already
  benchspec's.
- **Change:** set `bs-type-section-label` to mono 12px (`text-12`), weight 500,
  `letter-spacing: 0.04em`, lowercase, `fg-tertiary`, in brackets:
  - `[ assertions · 3 failed · 6 passed ]`
  - `[ phases · 90.9s ]`
  - `[ recent output · last 5 lines ]`
  - `[ leaderboard ]`

  Add one right-aligned caption above the matrix, `[ 13 evals × 2 arms · live ]`
  (Claude, Kimi). Most models proposed 11px, which is off the type scale; 12px
  is the smallest step. Gemini and Muse's tracked uppercase was not chosen,
  because the voice reserves uppercase for PASS, FAIL, TIMEOUT and ERROR, and
  lowercase matches `benchspec`. Add an exception for captions to the README
  rule "labels are sentence case". This is also the main way to keep JetBrains
  Mono from looking generic (Q1).

### 8. Hover card: lift it above the cells

- **Kind:** problem · **Models:** 4/6 (Muse, Kimi, GPT, Grok) · **Severity:**
  medium · **Effort:** S
- **Seen:** at A·G4, the card sits on `bg` #14172E, darker than the `raised`
  #1F2440 cells it floats over (Kimi, Grok, Muse), so it reads as a hole rather
  than a layer. In light, GPT finds the shadow heavy next to the hairlines. Two
  premises don't match the frame: Muse's "translucent, blurred" and Grok's "no
  edge". The card is opaque and has its 1px `line` border.
- **Change:** add `--popover`, set to `hover` #262C4E in dark and `surface`
  #FDFCF9 in light, and use it for `.bs-hc`. Keep the border. Change light
  `shadow-hovercard` to `0 4px 12px rgba(27,31,59,0.08)` and leave dark as it
  is. Every text token already passes on dark `hover`: `fg-tertiary` 4.82:1,
  `accent-ink` 4.76:1, new `danger` 4.78:1. On light `surface`, every token is
  at least 5.65:1.

### 9. One radius scale, no pill

- **Kind:** problem · **Models:** 4/6 (Kimi, GPT, Grok, Claude) · **Severity:**
  low · **Effort:** S
- **Seen:** at A·H2 and DS-components (Controls), the theme toggle is the only
  pill among 4–5px controls. The scale has eight steps: 2, 3, 4, 5, 6, 8, 10 and
  pill.
- **Change:** collapse to five steps:
  - `radius-2`: bars
  - `radius-3`: status boxes, which matches the mark's 2.5/10 ratio
  - `radius-4`: buttons, selects, the segmented control, the toggle, cells and
    chips
  - `radius-8`: hover card, drawer, banners and lists
  - `radius-10`: window frames

  Retire `radius-5`, `radius-6` and `radius-pill`, and drop "Only the theme
  toggle is a pill".

### 10. Fail is red, not candy pink (dark)

- **Kind:** problem · **Models:** 3/6 (Muse, GPT, Grok) · **Severity:** high ·
  **Effort:** S
- **Seen:** at A·C4 (fail boxes) and A·B2 ("4 failed"), dark `danger` #F27089 is
  a magenta-pink (hue 348°). Light `danger` #B53A37 is brick (hue 1°), so the
  two themes disagree. Verified.
- **Change:** dark `danger` **#F07378** (hue 358°; at least 4.78:1 on all six
  dark grounds; the error ring on its tint is 4.99:1). Dark `danger-tint`
  **#39262D** (GPT). Shapes stay as they are. Corrected proposals:
  - Grok's `term-red` #E25E59 fails as text (3.85:1 on `hover`, 4.32:1 on
    `raised`).
  - Muse's #E5484D (3.46:1) and #D64545 (3.09:1) fail.
  - GPT's #EE7D79 passes (at least 5.05:1) but at hue 2° sits 4° closer to the
    orange accent. #F07378 keeps 18° of separation.

### 11. Light status marks: quieter pass, amber warn

- **Kind:** problem · **Models:** 3/6 (Gemini, Kimi, Claude) · **Severity:**
  medium · **Effort:** S
- **Seen:** at A-light·E4, pass squares (#5E657B) are the darkest marks on the
  page, which inverts the "quiet pass" idea (Kimi, Claude). In the legend at
  A-light·A7, timeout and quiet use olive #806100 (Gemini, Kimi, Claude).
  Verified.
- **Change:**
  - Light `status-pass`: **#6B7286**, at least 3.82:1 on the new grounds.
  - Light `warn`: **#8A5A00**, amber at hue 39° rather than olive at 45.5°. As
    text it is at least 4.72:1; on `warn-tint` #FBF1D3 it is 5.26:1.

  Corrected proposals:
  - #7C8399 (Kimi r1, Claude) fails 3:1 on the new `hover` (2.92:1).
  - #9A6700 (Claude) and #8F6D00 (Kimi) fail as text (3.77 and 3.73:1).
  - #B45309 (Gemini r1) fails as text (3.88:1).

### 12. One ring for selection

- **Kind:** problem · **Models:** 3/6 (Muse, Kimi, Grok) · **Severity:** medium
  · **Effort:** S
- **Seen:** at A·C4 and DS-components·E4, the 2px inset `accent` ring is heavier
  than the 1.5px brackets inside it, and selected plus focus stacks two orange
  rings.
- **Change:** draw the selected inset ring at `stroke-outline` (1.5px). Add
  `.bs-cell.bs-sel:focus-visible { box-shadow: none; }` so only the 2px focus
  outline shows. Focus stays 2px with a 2px offset, and contrast is unchanged.
  Not adopted: Grok's 1px gray selection. Kimi's navy ring for light is under
  Consider.

### 13. Group-name column: one anatomy

- **Kind:** problem · **Models:** 3/6 (Gemini, Grok, Claude) · **Severity:**
  medium · **Effort:** S
- **Seen:** at A·A4–A6 and B′·A3–A8, the eval count and the `trial-only` chip
  sometimes sit inline and sometimes wrap. `claude-cloud-orchestrator/` pushes
  "1 evals" onto its own line. Group names and eval ids share one color (Grok).
  Verified. Also a copy bug: "1 evals" should read "1 eval".
- **Change:**
  - Line 1: the group name (`bs-type-group-name`, 14/500, `fg`).
  - Line 2, always: `5 evals · [trial-only]` in `bs-type-meta`, `fg-tertiary`,
    with a 4px gap. Drop the bordered pill and use bracket text (Gemini r2).
  - Fix the group row height.
  - Eval ids in `muted` (#A8AEBD dark, #4A5262 light), so group names lead.

### 14. Counter: weight and a quieter unit

- **Kind:** problem · **Models:** 2/6 (Muse, Grok) · **Severity:** high ·
  **Effort:** S
- **Seen:** at A·A2 and DS-type·B6, the 44px counter at weight 400 reads thin
  (Muse), and `%` has the same size and color as the digits (Grok).
- **Change:**
  - `--type-counter`: `500 44px/1`. Keep `letter-spacing: -0.03em`.
  - `%` in `text-24`, `muted`, baseline-aligned.
  - Finished counter: 30px/500 with `%` in `text-18`.

  500 stays inside the "400/500 in UI" rule; Muse's 600 is reserved for the
  wordmark. This is also where JetBrains Mono's tall x-height gets a designed
  moment (Q1).

### 15. Quieter queued cells

- **Kind:** problem · **Models:** 2/6 (GPT, Claude) · **Severity:** medium ·
  **Effort:** S
- **Seen:** at A·C5–E7 and B′·C7–E8, queued cells carry a 1px `line` outline, so
  the lower half of the matrix becomes a wall of rectangles louder than the
  finished cells. Verified.
- **Change:** remove the inset from `.bs-k-queued` and leave it transparent. The
  outlined boxes, brackets and `muted` "queued" label stay. Keep the inset on
  `not run`, which has no boxes.

### 16. Leaderboard table: rules, not fills

- **Kind:** problem · **Models:** 2/6 (Gemini, Claude) · **Severity:** medium ·
  **Effort:** S
- **Seen:** at B·A3–F5, the `lbl` label column, `band` stripes and rounded block
  make a heavy striped slab around a few numbers. There is about 250px of empty
  space between "Pass rate" and the baseline value. Verified.
- **Change:**
  - One `surface` ground, with 1px `line` rules between rows and under the head.
  - Label column 180px, arm columns 200px, values right-aligned on one edge.
  - Metric labels in `muted`.
  - `band` and `lbl` then go unused. Retire them, which shrinks the contrast
    table from six grounds to four.

### 17. Drawer section rhythm

- **Kind:** problem · **Models:** 2/6 (Muse, GPT) · **Severity:** medium ·
  **Effort:** S
- **Seen:** at C·F3–H7, the assertion rows are tight but the gaps into Phases,
  Recent output and the path are loose. Muse's "4px padding" is off: drawer
  padding is 24px.
- **Change:**
  - Sections `space-24` apart, down from `space-32`.
  - Section label to content `space-8`, down from `space-12`.
  - Path footer `space-24` from the section above.

### 18. Bracket caps on the run progress track

- **Kind:** push · **Models:** 2/6 (Muse, Kimi) · **Severity:** medium ·
  **Effort:** S
- **Seen:** at A·A2, the run progress bar is a plain bar drawn the way every
  dashboard draws it (Kimi). Kimi suggested caps on the track or the column
  header row, and Muse suggested endcaps on group rows.
- **Change:** use one placement only. Add bracket caps on the run progress
  track, with the same geometry as #3: 1.5px, `fg-tertiary`, 4px arms, 12px
  tall. Not on every row, which would double the per-cell brackets.

### 19. Legend as its own band

- **Kind:** problem · **Models:** 2/6 (Gemini, Kimi) · **Severity:** low ·
  **Effort:** S
- **Seen:** at A·A7 and C·A6, the legend sits directly under the last row, and
  its 12px boxes are smaller than the cells'.
- **Change:**
  - A 1px `line` top rule, with `space-12` padding above it and `space-16`
    margin.
  - Legend boxes at `size-box` (14px), so they match the cells. Retire
    `size-box-legend`.

## Consider

Single-model ideas, or two-model ideas with weak or conflicting reasons.

- **Docked full-height drawer** (Gemini, both runs): flush to the tab rule, the
  right edge and the bottom, with `border-left` only and radius 0. It touches
  the settled layout, so the maintainer decides.
- **Best marker as a 5×5px accent square** (Grok) instead of ◆, echoing the
  mark's squares. If adopted, keep the word "best" in `muted`.
- **Hairline arm-column rules** (Grok): a 1px `line` on the left edge of each
  arm column, from the header to the last row.
- **Finished progress bar in a neutral** (Claude), with green only on "✓
  Finished".
- **Cover blocks inset 24px** (Claude): the orange block touches the card's top
  edge and the slab runs past the right edge at DS-cover·E3 and G3–H4
  (verified).
- **Drawer path breaks only after `/`** (Claude): `<wbr>`, with the path in
  `fg-tertiary` and the leaf in `fg` 500. The title breaks mid-word at C·E3
  (verified).
- **Tighter header spacing** (GPT): 24px from the tally to the tabs, and a 40px
  tab strip.
- **Phone leaderboard as one card** (Grok): drop the nested Samples slab at
  D2·A6 (verified).
- **Dot grid only on PageState empty states and the cover** (Kimi). See Q2.
- **Segmented multi-state progress bar** (Gemini r1). It fights "accent means
  live progress".
- **Progress fill leading-edge tick** (Kimi r2).
- **Navy selection ring in light** (Kimi r2): orange runs louder on paper.
- **Dark `status-pass` to #A5ACBE** (Grok; Kimi r2 suggested #A8B0C2). It passes
  (at least 5.95:1) and separates pass from the queued outline. But it makes
  passes louder in dark, the opposite of "quiet pass", and Grok's logo argument
  holds only for the light-ground mark. The dark mark's left squares are
  #6E7891.

## Q1: mono face

**Vote: FONT-2 IBM Plex Mono, 6/6 models and all 9 runs.** **The maintainer's
pick is FONT-3, JetBrains Mono, and it stands.** The vote informs that pick; it
doesn't decide it.

Why the models chose Plex:
- Engineered, diagram-like letterforms with squarer terminals that echo Warp and
  the square mark (all six).
- A weight range up to 600/700, so the wordmark can share the face (Claude, GPT,
  Gemini).
- It holds at 12–13px on navy (Kimi, Muse).
- Distinct 0/O and 1/l, and a dotted zero that's calmer at 44px (GPT, Gemini,
  Muse).
- It pairs well with DM Sans (Kimi, Grok, GPT).

Against DM Mono:
- Thin and wispy at 12–13px on navy (Muse, Kimi, Claude).
- Tops out at 500, so it can't carry the wordmark (Claude).
- Too round or casual (Gemini, Muse, Grok).
- A busy slashed zero at 44px (GPT).

**What the maintainer should know about JetBrains Mono:**

- **Every model raised the same risk unprompted.** JetBrains Mono reads as "the
  IDE default". The phrasings:
  - "default IDE face… every dev tool" (Kimi r1)
  - "IDE skin… every other agent tool" (Grok)
  - "dense IDE code editor" (Gemini)
  - "code-editor default" (Muse)
  - "insistently code-editor-like" (GPT)
  - "tall x-height makes the counter and labels feel generic" (Claude)

  The concern is borrowed familiarity rather than legibility.
- **The models' practical arguments for Plex mostly hold for JetBrains too.**
  JetBrains Mono has weights 100–800, so the wordmark can be set in it at 600
  and the social art at 700. It is sturdy at small sizes. Moving off DM Mono
  still fixes the two real problems: the split wordmark (#2) and thin meta text.
- **The width claim doesn't hold up.** Kimi r1 said Plex's narrower advance fits
  more of each eval id, but `plan-review-judges-findings` ends at the same x in
  FONT-2 and FONT-3 (A·B4).
- **Turn ligatures off.** JetBrains Mono ships code ligatures on by default. Set
  `font-variant-ligatures: none` so `--count`, `->` and `>=` render as typed.
- **How to counter the generic look in the rest of the system.** Each of these
  carries identity the face doesn't:
  1. Bracket frames drawn with the mark's geometry (#3).
  2. Lowercase bracketed captions with 0.04em tracking (#7).
  3. The counter at 500 with a smaller, muted `%` (#14).
  4. An outlined wordmark at 600 with -0.01em tracking (#2).
  5. The warm-paper light theme (#1).
  6. Orange spent only on live, selected and best (#4, #6, #12).

## Q2: one quality from the references

All nine runs drew from Warp (REF-D). Gemini r2 also named Tailscale's
micro-labels.

| Quality | Models | Where they'd put it |
| --- | --- | --- |
| Bracketed mono figure captions (`[ fig. 1 — the factory ]`) | 4/6 · Gemini, Muse, Kimi, Claude | drawer section heads, leaderboard heading, a caption above the matrix, `[trial-only]` |
| Diagrammatic restraint: hairlines over fills, color used once | 3/6 · Gemini r1, GPT, Grok | matrix arm columns, leaderboard, group rules, selection |
| Faint dot-matrix ground | 3/6 · Muse, Kimi, Claude (optional) | behind the matrix, empty regions, PageState, cover. GPT argued against it behind live data. |

**Recommendation:**
1. **Bracketed mono captions** (#7), on the drawer heads, the leaderboard
   heading and one right-aligned caption above the matrix.
2. **Diagrammatic restraint**, which #5, #12, #15 and #16 already deliver.

Keep the dot grid off live data. If it is used at all, put it only on PageState
empty states and the cover.

## Q3: light theme paper tones

**Tally: all six models said yes, in all 9 runs.** Their proposals cluster at
hue 40–45° with low chroma. Kimi r1, GPT and Claude independently chose
`bg #F6F4EF`.

| Model | bg | surface | raised | hover | line |
| --- | --- | --- | --- | --- | --- |
| Gemini r1 / r2 | #F6F5F1 / #F4F2EE | #FCFBF9 / #FAF9F6 | #EFECE6 | #E6E2DA / #E5E1D9 | #DCD8CE / #DCD8CF |
| Muse r1 / r2 | #FAF6ED / #F4EFE6 | #FFFFFF / #FFFEFA | #F1EAD9 / #FFFFFF | #EFE7D3 / #EFE7D6 | #E2D8C2 / #E2DACA |
| Kimi r1 / r2 | #F6F4EF / #F7F5F0 | #FDFCF8 / #FFFFFF | #EEEAE1 / #F0EDE6 | #EBE6DA / #EDEAE2 | #E4DFD2 / #E3DFD5 |
| GPT | #F6F4EF | #FFFDFA | #EFEEE8 | #E7E5DD | #D8D6CE |
| Grok | #F7F4EF | #FCFAF7 | #FFFFFF | #F0EBE4 | #E4DDD4 |
| Claude | #F6F4EF | #FDFCF9 | #EEEBE4 | #E7E3DA | #DCD7CC |

**Proposed and verified:** `bg #F6F4EF`, `surface #FDFCF9`, `raised #EEEBE4`,
`hover #E9E5DC`, `band #F2EFE9`, `lbl #EBE7DF`, `line #E2DDD3`. This keeps
today's order, with `surface` lightest, then `bg` and `band`, then `raised`,
`lbl` and `hover`. It also keeps today's hover-to-raised step.

Minimum ratio across the six grounds, with text tokens unchanged except `warn`:

| Token | Kind | Min | Worst ground |
| --- | --- | --- | --- |
| `fg` #1B1F3B | text | 12.79 | hover |
| `muted` #4A5262 | text | 6.25 | hover |
| `fg-tertiary` #5E657B | text | 4.61 | hover (today's minimum is also 4.61) |
| `accent-ink` #A93F18 | text | 4.90 | hover |
| `danger` #B53A37 | text | 4.61 | hover |
| `success` #1F6E45 | text | 4.95 | hover |
| `warn` **#8A5A00** (new) | text | 4.72 | hover |
| `accent` #D8430F | mark | 3.53 (3.28 on `line` track) | hover |
| `status-pass` **#6B7286** (new) | mark | 3.82 | hover |

Two traps for the final hover value. Hover at #E6E2D9 (Gemini and Claude's
range) pushes `fg-tertiary`, `danger` and today's `warn` to 4.48:1, below the
4.5 floor, so #E9E5DC is as dark as hover can go. Line at #DCD7CC (Claude)
leaves the `accent` progress fill at 3.09:1 on its track, so #E2DDD3 is used
(3.28:1).

Rejected light text values: Grok's `muted` #6B655C (4.46:1) and `fg-tertiary`
#8A8478 (2.87:1), and Claude's `fg-tertiary` #6B7182 (3.77:1). Keep the navy
ramp.

## Rejected

12 items: one contradicts a maintainer decision, two fail AA outright, and the
rest rest on false premises or break a DS rule.

1. **IBM Plex Mono as the one mono face** (6/6, in Q1). The maintainer picked
   JetBrains Mono.
2. **"Fix" the light accent hue** (Claude: `accent` #E85D2F, `accent-ink`
   #B84A1E; Muse: #C64E26 and #C44A22). The premise is wrong: light `accent`
   #D8430F has hue 15.5°, brand #E85D2F has 14.9°, so only lightness differs.
   The values also fail AA: #E85D2F is 2.69:1 on hover and 2.92:1 on raised; as
   text, #B84A1E is 4.02:1 and #C64E26 is 3.59:1.
3. **Warmed light text ramp** (Grok's `muted` #6B655C at 4.46:1 and
   `fg-tertiary` #8A8478 at 2.87:1; Claude's `fg-tertiary` #6B7182 at 3.77:1).
   Fails AA text.
4. **Light timeout and quiet #D97706** (Gemini r2). 2.46:1 on hover and 2.68:1
   on raised, below 3:1.
5. **Take orange off the live progress fill**, using "white 40%" (Muse r2).
   Opacity breaks the "never fade" rule, and every other model keeps orange for
   live progress.
6. **Re-step the dark grounds** (Muse r2). The values were misread: `surface` is
   already #1B1F3B. The proposed `raised` #262A4E would also equal `hover`
   #262C4E and remove the hover state.
7. **10px StatusBoxes at radius 2** (Muse r2). The premise is wrong: boxes are
   14px with `radius-3`, the mark's 2.5/10 ratio, and 1.5–2px strokes. The
   change would shrink the settled one-box-per-sample.
8. **Recolor the live dot** (Muse r1). It is already `accent` #F0764B.
9. **Add a progress track** (Muse r1). The track exists: `line` #2A3053, 4px.
10. **Phone baseline bar fill** (Kimi r2). In D2·B4 the bar is already a light
    neutral on a dark track.
11. **Variable row heights by state** (Kimi r2). Rows would jump height mid-run
    as samples change state.
12. **Cover blocks as per-arm color keys and a drawer spine** (Muse r1). This
    reuses logo-exempt colors (`brand-salmon`, `brand-gray`) as UI marks, which
    the DS forbids, and adds per-arm color coding the matrix doesn't use.

Values corrected rather than rejected:

| Proposed | Model | Problem | Used instead |
| --- | --- | --- | --- |
| `term-red` #E25E59 | Grok | 3.85:1 on hover | #F07378 |
| #E5484D, #D64545 | Muse | 3.46:1, 3.09:1 | #F07378 |
| light pass #7C8399 | Kimi, Claude | 2.92:1 on the new hover | #6B7286 |
| light warn #9A6700, #8F6D00, #B45309 | Claude, Kimi, Gemini | fail as text | #8A5A00 |
| group fill #626B83 | GPT | 2.40:1 on its track | `fg-tertiary` |
| opacity on brackets and fills | Claude, Grok | breaks the no-fade rule | solid tokens |
| 11px sizes | several | off the type scale | 12px |

## Run stats

| Model | Usable runs | Observations | Cost |
| --- | --- | --- | --- |
| google/gemini-4-argon | run 1 (invalid JSON: unescaped quotes in a font-family string; extracted by hand from `raw`) and run 2 | 11 + 11 | $0.193 |
| meta/muse-spark-1.3 | runs 1 and 2 | 10 + 10 | $0.053 |
| moonshotai/kimi-k3 | runs 1 and 2 | 11 + 12 | $0.263 |
| openai/gpt-6-astra | run 1 (run 2 failed: credit error, 402) | 10 | $0.832 |
| x-ai/grok-4.7 | run 2 (no run 1 file) | 12 | $0.205 |
| anthropic/claude-opus-5.5 | run 1 (run 2 failed: credit error, 402) | 12 | $0.396 |
| **Total** | 9 runs, 6 models | 99 | **$1.94** |

### Inconsistencies spotted in design-system.md

- The README Color section says the light accent is "a deeper #DF4A19", but
  `tokens.css` has #D8430F.
- The accent rule says accent marks live progress and "nothing else", but the
  Drawer spec colors the agent phase `accent` (see #6).
- The Tabs README still describes the Show filter's `Problems (2)` option, which
  the maintainer has dropped.
