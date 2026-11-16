# benchspec watch page critique: synthesis

Six models reviewed the watch mockups (frames A, A-light, B, C, D, D2, D3, E)
against the page rubric (C1–C8). That gave 11 usable runs and 151 issues. Grok
run 1 returned one sentence of preamble and no issues. Issues are clustered by
element and underlying problem. "n/6" counts distinct models, and two runs of
one model count once. Every visual claim was checked against the screenshots and
every contrast claim against `design-system.md` v13 tokens. Grid refs read
`frame·cell`. Jobs: 1 during a run, 2 after a run, 3 drill-in.

## Summary

- **The phone summary hides failures (6/6).** `27/31 passed · 4 running` drops
  `4 failed`, and its denominators don't match the counter. Use the desktop
  tally items on phone.
- **The numbers don't reconcile.** n/a samples count as passed and done (4/6).
  Group bars are stubs (4/6). The counter's "78" is samples while the spec says
  cells (4/6). The hover-card headline disagrees with its rows (5/6). Timeouts
  get their own tally bucket against the spec (3/6). One counting model fixes
  all five.
- **One word per state.** `—` means all-errored, not started, and not run (5/6).
  Trigger evals use `3/3 pass` and `routed 12/12 · 12/12` (3/6). Scope notes
  claim `[trial-only]` over baseline results (3/6). `1.53× best` sits under the
  losing value (3/6).
- **Frame A points at the wrong cell (4/6).** The selected ring and the hover
  card sit on the passing trial cell while the pointer is on the failing
  baseline cell. Desktop content also clips at the right edge (4/6).
- **Q1: B, arms as columns (5/6 models, 10/11 runs).** Drop B-alt. Muse's one
  B-alt vote rests on a false claim that B's headers are rotated.

## Prioritized changes

Ranked by models, then severity, then criterion weight, then confidence. Where:
**M** = mockup, **DS** = design system, **#180** = implementation spec.

| # | Change | Models | Sev | Effort | Where |
| --- | --- | --- | --- | --- | --- |
| 1 | Phone tally shows failures, same items as desktop | 6/6 | high | S | M, DS |
| 2 | One word per empty state; `—` only for all-errored | 5/6 | medium | M | M, DS, #180 |
| 3 | Hover-card headline equals its rows | 5/6 | medium | S | M, DS, #180 |
| 4 | n/a samples never count as passed, done or waiting | 4/6 | medium | M | M, DS, #180 |
| 5 | Group bars to scale (done/total) | 4/6 | medium | S | M, DS |
| 6 | Counter names its unit: samples | 4/6 | medium | S | M, DS, #180 |
| 7 | Frame A: ring and hover card on the hovered cell | 4/6 | medium | S | M |
| 8 | No right-edge clipping on desktop | 4/6 | medium | S | M, #180 |
| 9 | Hover card follows its spec vocabulary | 4/6 | medium | S | M |
| 10 | Sample nav: full-size on desktop, present on phone | 4/6 | medium | S | M, DS |
| 11 | Trigger evals use the standard labels | 3/6 | medium | S | M, DS, #180 |
| 12 | "best" only under the winning value | 3/6 | medium | S | M, DS |
| 13 | Timeouts count as failed in the tally | 3/6 | medium | S | M, #180 |
| 14 | Scope notes only when true | 3/6 | medium | S | M, DS, #180 |
| 15 | Phone pinned column: opaque, no bleed | 3/6 | medium | S | M, #180 |
| 16 | Phone eval ids readable | 3/6 | medium | S | M, DS |
| 17 | Theme toggle names its destination | 3/6 | low | S | M |
| 18 | No muted 100% when a sample errored | 2/6 | medium | S | M, DS |
| 19 | Quiet agents counted in the tally | 2/6 | medium | S | M, DS, #180 |
| 20 | Phone leaderboard: no accent bars; spec matches mock | 2/6 | medium | M | M, DS |
| 21 | Legend fail entry: three steps plus tooltip | 2/6 | low | S | M |

### 1. Phone tally shows failures, same items as desktop

- **Problem:** D·A2 `27/31 passed · 4 running`, D2·A3 `62/77 passed · 1 errored`
  and D3·A3 `15/69 passed · …` all drop the failed count (4, 15 and 54+). Their
  denominators (31, 77, 69) also differ from the counter beside them (31 of 78,
  78 of 78, 70 of 117). Verified.
- **Job:** 1, and 2 on D2.
- **Fix:** on phone, render the desktop tally items in a `flex-wrap` row:
  `{passed} passed · {failed} failed · {errored} errored · {running} running`.
  Drop zero buckets except failed, and show failed and errored above zero in
  `danger`. Drop the `N/M passed` fraction; the counter's `N of M finished` is
  the only fraction. Omit waiting and `-n 4` on phone. DS: add a "Phone" line to
  Counter that says this.
- **Models:** 6/6 (Claude, Gemini, Muse, Kimi, GPT, Grok); 8 of 11 runs.

### 2. One word per empty state; `—` only for all-errored

- **Problem:** `—` means three things. At A·E6 (`— · 0/12`) and B2·B8
  (`— · 0/39`) it means not started. At B·D3 (Skill triggered, baseline `— ⓘ`)
  it means scoped off. The spec reserves it for every sample errored, as at
  E·E7. D2·B5 writes `not run` for the same leaderboard fact. Verified.
- **Job:** 1, 2.
- **Fix:** one mapping, used by cells, group summaries, the desktop leaderboard
  and the phone card:
  - `—` only at denominator 0 after errors, with the error boxes beside it.
  - Nothing graded yet: `queued · 0/12` while nothing has started,
    `running · 0/12` once something has, both in `muted`.
  - Every line scoped off the arm: `n/a` (Skill triggered on baseline, desktop
    and phone).
  - `not run` only for cells outside the run (E·E6).
  - DS: add an `n/a` row to the Leaderboard table and a summary-state table to
    MatrixGroup.
- **Models:** 5/6 (Claude, Kimi, Grok, GPT, Gemini); 8 runs. They split on the
  leaderboard word: `n/a` (Kimi, GPT, Gemini) or `not run` (Claude, Grok). `n/a`
  matches the DS state words.

### 3. Hover-card headline equals its rows

- **Problem:** E·G5 reads `94% · 17 of 18 checks passed` over rows `× 9/10`,
  error and `✓ 10/10`, which sum to 19 of 20. Verified. The headline pools only
  shared lines; the rows count every line.
- **Job:** 3.
- **Fix:** a cell's rate, its hover headline and its rows count every check that
  cell ran, here `95% · 19 of 20 checks passed`. The shared-lines-only rule
  stays on the leaderboard's Pass rate, where arms are compared. Add a fixture
  test: headline = sum of graded rows. This also gives trigger-only cells a rate
  (#11). The alternative, rows over shared lines only (`8/9`, `9/9`) plus a
  note, keeps cross-arm parity in the matrix but leaves trigger cells with no
  rate.
- **Models:** 5/6 (Claude, Muse, Kimi, GPT, Grok); 9 runs. Kimi, GPT r2 and Grok
  sum the rows; Claude and GPT r1 use shared lines.

### 4. n/a samples never count as passed, done or waiting

- **Problem:**
  - A·A2 `27 passed` = 15 real passes + 12 baseline n/a samples from
    `not-triggers/` (verified by counting).
  - A·C5 `n/a · 12/12` draws a full bar, while A·C6 `n/a · 0/12` sits over
    queued boxes.
  - B2·C6 `2/3 · agent 14s` counts two n/a boxes as done.
  - A·C6–C7: the baseline `triggers/` cells render as `queued` outlines although
    the group is n/a.
- **Job:** 1.
- **Fix:** apply the MatrixCell tally rule (n/a: "none; excluded from rates and
  counts") everywhere.
  - Render scoped-off samples as `bs-b-na` from `run.start`, never queued.
  - Exclude them from passed, waiting, finished and the counter's M. Frame A
    becomes 15 passed and M = 54.
  - Exclude them from cell `done/total`: B2·C6 becomes `0/1 · agent 14s`.
  - An all-n/a arm summary reads `n/a` with no count and an empty track.
- **Models:** 4/6 (Claude, Kimi, GPT, Muse); 5 runs.

### 5. Group bars to scale (done/total)

- **Problem:** A·C4 `57% · 4/15` and `100% · 3/15` sit over 2–4px stubs. The
  same happens at B2·C6/E6 (to-spec) and D3·B6. Verified.
- **Job:** 1.
- **Fix:** live bar width = `done / total × 100%` (26.7% and 20% here),
  `accent`. A finished bar is 100% `fg-tertiary`, matching B2's finished groups.
  DS MatrixGroup: state "bar = completion, not pass rate". Grok wanted pass-rate
  width; the finished-bars-full convention rules that out.
- **Models:** 4/6 (Claude, Gemini, GPT, Grok); 6 runs.

### 6. Counter names its unit: samples

- **Problem:** A·B2 `31 of 78 finished` counts samples (13 × 2 × 3), and the
  tally sums to 78. The Counter README says cells
  (`M from run.start.cell_count`, `aria-valuetext "cells finished"`). Verified:
  the DS contradicts itself.
- **Job:** 1.
- **Fix:** count samples, since the tally is per sample and gives finer
  progress. Show `31 of 78 samples finished`. Use
  `aria-valuetext="{N} of {M} samples finished"` and M = applicable samples
  (after #4). Rewrite the Counter README and preview. Muse and GPT r2 preferred
  counting cells; the tally would then need a second unit.
- **Models:** 4/6 (Claude, Gemini, Muse, GPT); 5 runs.

### 7. Frame A: ring and hover card on the hovered cell

- **Problem:** A·E4 puts the `.bs-sel` accent ring on trial × codex-logged-out
  (100%) with no drawer open. The hover card at A·G4 floats beside that cell
  while it describes baseline (52%), where the pointer is (D4). Verified in A
  and A-light.
- **Job:** 1, 3.
- **Fix:** remove `.bs-sel` (selected means the drawer is open). Anchor the card
  to the hovered cell's box: below it, left edges aligned, `space-8` gap; flip
  above when there's no room. Never place it beside another cell.
- **Models:** 4/6. Ring: Gemini, Kimi, Grok. Card anchor: Claude. 6 runs.

### 8. No right-edge clipping on desktop

- **Problem:** at A·H2, B·H2, B2·H1 and E·H2–H3, the theme toggle, `~10m le…`,
  `took 19m 4…`, the progress bar's right cap and the matrix caption's `]` are
  cut at the window edge. Verified. This is a mockup render bug; guard against
  it in the build.
- **Job:** 1.
- **Fix:** `.bs-app { box-sizing: border-box; max-width: 100%; }` with
  `space-32` padding inside. Lay the counter row out with flex, `min-width: 0`
  and `flex-wrap`, so `.bs-el` wraps under the count before it clips. Check at
  1280 and 1024px.
- **Models:** 4/6 (Claude, Gemini, Kimi, GPT); 7 runs.

### 9. Hover card follows its spec vocabulary

- **Problem:**
  - E·G5 uses a `!` glyph (outside the iconography list) and puts
    `opencode exited 1: provider returned 429` in muted between rows.
  - A·G4 uses `×` instead of `✗` and colours the `Sample N of 3` labels red.
  - Neither card has the problem line or the footer note.
- **Job:** 3.
- **Fix:** per HoverCard README:
  - Problem line under the headline, in `danger`:
    `error · sample 2 exited 1: provider returned 429`.
  - Rows `Sample 1` in `fg`, then `✗ 9/10` in `danger` (or `✓ 10/10` in `fg`)
    and the duration.
  - `ERROR` in words for the errored sample.
  - Footer `Open the cell for assertions and output.` in `muted`.
- **Models:** 4/6 (Gemini, Kimi, Muse, Claude); 6 runs.

### 10. Sample nav: full-size on desktop, present on phone

- **Problem:** the D·D3 sheet has no `‹ ›`, so a phone user can't reach samples
  1 and 3. At C·F3 the glyphs sit about 25px apart, so they are smaller than the
  `size-close` 32px `bs-iconbtn` the spec requires, and `‹` isn't disabled on
  sample 1. Verified.
- **Job:** 3.
- **Fix:**
  - Desktop: `‹ ›` as `bs-iconbtn` at `size-close` (32×32), `space-4` apart,
    with `disabled` at either end and `aria-label` `Previous sample` /
    `Next sample`.
  - Phone sheet: the same buttons at `size-control-phone` (44px) after
    `sample 2 of 3`. Set the sheet's `.bs-sub` lines in `bs-type-body-mono`, as
    on desktop.
  - DS Drawer: add the phone rule.
- **Models:** 4/6 (Claude, Gemini, GPT, Kimi); 7 runs.

### 11. Trigger evals use the standard labels

- **Problem:** finished trigger cells read `3/3 pass` and `2/3 pass` (C·E4–E7,
  B2·E3, E6). `2/3 pass` sits next to a red box. Group summaries read
  `routed 12/12 · 12/12` (A·E5, C·E5–E6), which is jargon and repeats the count.
  Verified. Frame A's note calls `3/3 pass` deliberate, and this reverses it.
- **Job:** 1, 2.
- **Fix:** a finished trigger cell shows its pooled rate (`100%` muted, `67%`),
  which works once #3 counts the cell's own checks. Summaries read
  `100% · 12/12` while live and drop `routed`. Put
  `3 of 3 samples triggered the skill` in the hover card. DS MatrixCell: no
  `N/M pass` variant.
- **Models:** 3/6 (Claude, Kimi, Grok); 5 runs.

### 12. "best" only under the winning value

- **Problem:** B·E4, E5 and E6 put `1.53× best`, `1.44× best` and `2.24× best`
  under the losing values, so both arms' cells contain "best". Verified.
- **Job:** 2.
- **Fix:** on the loser, write the ratio against the winning arm by name, in
  `muted`: `1.53× baseline`, `2.24× baseline`. Keep `◆` and the word `best` on
  the winner only. DS Leaderboard: add the sub-line format.
- **Models:** 3/6 (Claude, Kimi, Grok); 5 runs.

### 13. Timeouts count as failed in the tally

- **Problem:** the E·B3 and D3·A3 tallies add a `1 timed out` bucket. The
  MatrixCell tally rule puts timeout (graded at its cap) under failed, and
  "timed out" isn't a DS state word. Verified.
- **Job:** 1.
- **Fix:** E becomes `27 passed · 86 failed · 4 errored`. The timeout stays
  named in words in the hover card's problem line
  (`timeout · sample 1 hit the 600s cap`, `warn`) and in the drawer.
- **Models:** 3/6 (Kimi, Muse, Claude); 4 runs.

### 14. Scope notes only when true

- **Problem:** A·B4 tags `delegating-to-codex/` `[trial-only]` while baseline
  shows 52% and 1/3. Every B2 group carries it, and E's `[skips bare]` sits over
  bare cells at 33–56%. Verified. Only the skill-trigger line is scoped.
- **Job:** 2.
- **Fix:** show the group note only when every check in the group skips the arm.
  Otherwise write `[1 check trial-only]` / `[1 check skips bare]`, using a
  per-group count of scoped lines from the backend. DS MatrixGroup: state the
  rule.
- **Models:** 3/6 (Claude, Kimi, GPT); 3 runs.

### 15. Phone pinned column: opaque, no bleed

- **Problem:** in the D3 inset (B5–B6), red slivers show left of `built-…-since`
  and `event-winners`, and `%` fragments peek right of the pinned column.
  Verified.
- **Job:** 1.
- **Fix:** give the pinned id cell and its header
  `position: sticky; left: 0; z-index: 1; background: var(--surface)` across the
  full column, padding included, up to its 1px `line` edge. Draw the
  `size-shade` gradient outside that edge, only with `.bs-scrolled`.
- **Models:** 3/6 (Claude, Gemini, GPT); 5 runs.

### 16. Phone eval ids readable

- **Problem:** D·A4–A7 cut ids in the middle to about 6 characters
  (`plan-…t-task`, `pytho…issing`, `deleg…codex/`). Each 112px arm column holds
  only about 80px of content (boxes plus a 4-character rate). Verified. D3's
  note recommends middle cuts, and this reverses it.
- **Job:** 1, 3.
- **Fix:** at ≤3 samples, set `size-arm-phone` to 96px and give the freed 32px
  to the id column. Truncate from the start so the distinctive end shows
  (`…implement-task`, `…logged-out`). The full id stays in the sheet header. DS
  MatrixCell phone: update the token and the truncation rule.
- **Models:** 3/6 (Claude, Muse, Grok); 5 runs. Claude r2 and Muse r1 suggested
  two-line wraps at `/` or `-` instead, which costs row height.

### 17. Theme toggle names its destination

- **Problem:** A-light·H2 reads `Light theme` in light mode. The TopBar and
  Controls specs say the label names the theme it switches to. Verified.
- **Job:** none directly (C4).
- **Fix:** `Dark theme` with `aria-pressed="true"` when `data-theme="light"`.
  Mockup only; the DS preview is already right.
- **Models:** 3/6 (Gemini, GPT, Grok); 5 runs.

### 18. No muted 100% when a sample errored

- **Problem:** B2·E3 (new-python-repo × trial) and C·E4
  (plan-review-judges-findings × trial) are [pass, pass, error] cells showing a
  muted `100%`, styled the same as a clean pass. Verified.
- **Job:** 1, 3.
- **Fix:** apply `bs-q` only when every sample passed. If any sample is error or
  timeout, render the rate in `fg`, even at 100%. DS MatrixCell: amend the
  finished-label row.
- **Models:** 2/6 (Claude, Gemini); 4 runs.

### 19. Quiet agents counted in the tally

- **Problem:** at A·C4, a sample quiet for 52s shows only as one 14px warn ring.
  The tally lumps it into `4 running`, and the phone shows nothing. Verified.
- **Job:** 1.
- **Fix:** add a `quiet` tally bucket, shown in `warn` when above zero:
  `3 running · 1 quiet`. The buckets still sum to M. Show it on phone too (#1).
  DS: update the MatrixCell tally table and the Counter README.
- **Models:** 2/6 (Claude, GPT); 2 runs.

### 20. Phone leaderboard: no accent bars; spec matches mock

- **Problem:** D2·B4–B5 fill the winning arm's bars in `accent`. The DS Color
  rule says accent marks live, selected and focus, "nothing else". The D2 layout
  (metric sections with per-arm bars) also differs from the Leaderboard README's
  `bs-lbc` (one row per arm). Verified. The bars are to scale and have a track.
- **Job:** 2.
- **Fix:** keep D2's metric-grouped card, because it carries all six metrics and
  `bs-lbc` carries one. Draw every bar in `fg-tertiary` (4.55:1 on the `line`
  track). Put `◆` and the value in `accent-ink` on the best value only. Rewrite
  the Leaderboard README phone section to this layout. Also reconcile README's
  identity line ("live, selected or best") with Color ("nothing else").
- **Models:** 2/6 (Gemini, Muse); 4 runs.

### 21. Legend fail entry: three steps plus tooltip

- **Problem:** the A·B7 legend shows fail as a two-band swatch with no
  affordance. The StatusBox spec requires the three-step `bs-b-failsteps` in a
  `bs-lgbtn` with the tooltip `Darker = fewer checks passed.` Verified.
- **Job:** 1.
- **Fix:** follow the StatusBox legend rule: three steps, a button,
  `role="tooltip"`, open on hover and focus, Esc dismisses. Mockup only.
- **Models:** 2/6 (Gemini, Kimi); 3 runs.

## Consider

Single-model items, verified unless noted.

| Idea | Model | Note |
| --- | --- | --- |
| Provisional per-arm rate under each matrix header, labelled `so far` | GPT | Job 1 "which arm is ahead"; uses the leaderboard's shared-line calc |
| Verdict line above the leaderboard: `trial leads by +37pp` | GPT | Claude backs it in its Q1 answer; D2 already has `trial is best · +37 pts` |
| Running label always `{done}/{total} · …`: B2·E6 `running · agent 9s` → `0/3 · agent 9s` | Gemini | Spec divergence |
| Finished group summaries drop `· done/total` (C·C3 `62% · 15/15`, E) | Muse | MatrixGroup spec: suffix only while live |
| Failing-eval count in collapsed group meta: `15 evals · 9 failing` | Claude | Job 1 at 232 cells; text, not a badge |
| Stronger error box at E·E7 | Claude | `danger-tint` vs `raised` is 1.08:1, so the tint barely shows; a knockout shape changes a StatusBox state |
| Wider fail-step contrast (1.34:1 today, verified) | Claude | No headroom: the proposed fail-1 `#FDB3B5` is 1.03:1 to `warn` and 1.17:1 to `brand-salmon`; fail-3 is already 3.35:1 on `hover` |
| `pts` vs `pp` | GPT | DS conflict: README says `+50pp`, Leaderboard says `+34 pts`. Pick `pp` |
| `[ assertions · … · 1 skipped ]` → `1 not run`, and drop the `–` glyph (C·F4, D) | Kimi | "skipped" isn't a DS state word |
| `FAIL · 4 of 9 assertions failed` | Grok | Contradicts the DS voice example `FAIL · 6/9 assertions`; ambiguity is real |
| Phone `6m 41s` → `elapsed 6m 41s` | Muse | Bare duration |
| Phone recent output wraps (`pre-wrap`) | GPT | Phone has no hover to reveal full lines |
| Pass-rate explanation line above leaderboard | GPT | Exists behind `How these numbers are counted` |
| Snapshot banner copy shorter | Muse | Low |
| Counter hierarchy (N of M smaller) | Muse | Low; already 16px muted |
| Replace `ⓘ` with a `How counted` text button; sort header in `fg`, not orange | Claude | `ⓘ` is outside the iconography list; the header point is moot if B-alt goes |
| Booting dashed ring renders solid at `size-box-sm` (D·E4 trial) | none (found in verification) | Booting then looks like running without its dot |

## Q1: leaderboard layout

| Choice | Models | Runs |
| --- | --- | --- |
| **B, arms as columns** | 5/6: Claude, Gemini, Kimi, GPT, Grok (Muse r2) | 10 |
| B-alt, arms as rows | Muse r1 only (Muse split 1–1) | 1 |

**Why B:**
- It keeps the matrix's arm order (baseline first), so nothing remaps between
  tabs (all five).
- One metric per row means you compare 2–4 values in a single line.
- B-alt's ratio-to-best (`1.00×`, `0.63×`) is a second unit to decode, and its
  direction flips by metric: `1.00×` wins on both rate and time (Grok, Kimi,
  GPT).
- B-alt repeats "best" across rows.
- B-alt already clips the cost column at two arms (B·H7, verified).

**Against:** Muse r1's case for B-alt ("no rotated headers") rests on a false
reading. B's headers are horizontal.

**Recommendation:** ship B and delete B-alt from the mockup. Optionally add a
one-line verdict above the table (see Consider). Revisit rows only past four
arms.

## Rejected

**Settled-decision conflicts (3, partial).** No model re-raised pass vs fail by
colour, badges, the Problems filter, tabs, fonts, the drawer or row bands.

- Claude r1: make the `1 quiet` tally item a button that jumps to the quietest
  cell. It drifts toward a problems filter; only the tally item was kept (#19).
- Gemini r2 alternative fix: label `100% (2/3)`. It breaks the phone's
  4-character rate; unmuting was used instead (#18).
- Grok: fill the phone quiet box in `warn`. That is the timeout box's fill, so
  it changes the settled box set.

**False claims (12).**

1. Muse ×2: B's leaderboard arm headers are rotated. They are horizontal.
2. Muse ×2, Grok: D3's four-arm headers are truncated or overlapping. `cod` is
   arm 3 peeking under the scroll fade, with `arms 1–2 of 4 →` above, and the
   columns are fixed width.
3. Muse: A's hover rows are all red though only two failed. All three failed
   (8/9, 5/9, 1/9).
4. Muse: C's phase legend relies on colour alone. Every swatch has a text label
   and duration.
5. Muse: C's drawer assertions have no disclosure. Every row has a `›`, and the
   first is open.
6. Muse: D2's bars are not to scale. 62% ≈ 94 of 152px; 99% ≈ 151 of 152px.
7. Kimi: D2's bars have no track. A dark track is visible.
8. GPT: C's agent phase segment is small. It spans about 70% of the bar,
   matching 63.8 of 90.9s.
9. Gemini: A's `4 running` contradicts three visible samples. Four are visible:
   running, quiet and two booting.
10. Gemini: collapsed groups drop `1 error`. No collapsed errored group is
    shown, and the spec keeps them open.
11. Grok: phone running and quiet are both warm rings. Running is an orange ring
    with a dot; quiet is a yellow ring with a bar (1.61:1 apart, plus shape).
12. Gemini ×2, Kimi: C's `‹ ›` fail WCAG 2.5.8. At about 25px pitch, the spacing
    exception likely holds. They are still under the 32px spec, which is kept in
    #10.

**Page artifacts (1).** Gemini r2: the counter is missing in C. The frame is
cropped to show the drawer. Confirm in #180 that the counter stays visible when
the drawer is open.

**DS inconsistencies found:**
- Counter README says cells, but its preview counts samples.
- Accent: README identity says "live, selected or best"; Color says "nothing
  else".
- `pp` vs `pts`.
- Frame notes in A (`3/3 pass` without pooling) and D3 (middle-cut ids)
  contradict #11 and #16.

## Run stats

| Model | Runs parsed | Issues | Cost |
| --- | --- | --- | --- |
| claude-opus-5.5 | 2/2 | 15, 15 | $1.255 |
| gemini-4-argon | 2/2 | 12, 14 | $0.348 |
| muse-spark-1.3 | 2/2 | 14, 12 | $0.176 |
| kimi-k3 | 2/2 | 15, 11 | $0.450 |
| gpt-6-astra | 2/2 | 13, 15 | $1.300 |
| grok-4.7 | 1/2 (r1 invalid JSON; raw has only a preamble, no issues) | 15 | $0.502 |
| **Total** | 11/12 | 151 | **$4.03** |
