# benchspec watch: design critique rubric

## Context

You are reviewing static mockups of **benchspec watch**, a local web page that
shows a benchmark run live. benchspec runs AI coding-agent evals across *arms*
(a harness and model pairing, e.g. `baseline` vs `trial`). Each eval runs on
every arm, 1–3 times (*samples*), and each sample is graded against a list of
checks. One eval on one arm is a *cell*.

**Who uses it:** developers and eval authors, with the page open beside their
terminal. Real runs are narrow and tall: 2–4 arms, 13–232 evals, 1–3 samples.

**The page's three jobs, in priority order:**

1. **During a run:** see how far along it is, and spot failing or stuck cells at
   a glance.
2. **After a run:** see which arm won and by how much.
3. **Drill in:** open a problem cell and understand why it failed.

The main action is opening a problem cell.

**Settled decisions (do not argue against these):** dark theme by default;
benchspec navy and orange brand; JetBrains Mono for data and DM Sans for prose;
one square box per sample, framed by brackets like the logo; pass is a quiet
gray square and fail is a solid red square whose shade deepens as fewer checks
pass (pass and fail differ by color only, by choice); no text badges inside
cells; problems shown by box color and a lower pass rate; detail in the hover
card and the cell drawer; Matrix and Leaderboard tabs that switch to Leaderboard
when the run ends; no Problems filter; phone cells show boxes and a short rate
only.

**What you are given:** one screenshot per frame, each with a lettered grid
(columns A–H, rows 1–8) drawn on it, plus the benchspec design system as text
(`design-system.md`). The screenshots are static, so you cannot test hover,
scrolling or animation. Where a frame shows a hover or scrolled state, it is
drawn into the image.

## Criteria

Judge each frame against these criteria. Weight sets priority when you rank
issues.

| ID | Criterion | Weight | What good looks like |
| --- | --- | --- | --- |
| C1 | Glanceability and hierarchy | High | Within three seconds you can tell: progress %, whether anything is wrong, and which arm is ahead. One clear focal point per view. |
| C2 | Problem salience | High | Failures, timeouts, errors and quiet agents stand out without hunting. Passing cells stay quiet. Nothing looks alarming when it isn't. |
| C3 | Honest data encoding | High | Rates, sample counts and states are unambiguous and consistent across the matrix, leaderboard, hover card and drawer. "Failed", "not applicable", "not run" and "still running" can't be confused. Bars and color scales are to scale. |
| C4 | Usability heuristics (Nielsen) | Medium | Especially: visibility of system status, consistency and standards, recognition over recall, minimalist design, and helping users diagnose errors. |
| C5 | Accessibility (WCAG 2.2 AA) | Medium | Text contrast ≥ 4.5:1 (≥ 3:1 for large text and UI shapes). Color is never the only signal, which matters for red and green. Phone tap targets ≥ 24px. |
| C6 | Brand and design-system fit | Medium | Uses the benchspec tokens, type scale and spacing consistently, and reads as a developer tool. Avoids generic AI-UI looks (neon-on-black, identical rounded cards, all-caps labels everywhere). |
| C7 | Phone (390px) | Medium | Each phone frame does its job one-handed: readable without zooming, nothing cramped, horizontal scroll only inside the matrix. |
| C8 | Copy | Low | Plain, short, developer-facing words. No internal jargon. Labels say what they mean. |

## Severity

- **High:** blocks one of the three jobs, or fails WCAG on primary information.
- **Medium:** slows a job down or invites a wrong reading.
- **Low:** polish.

## Instructions

1. Report only problems. Don't comment on what works.
2. Report at most 15 issues, ranked by severity, then weight.
3. Point at exact spots: the frame letter, the grid cell (e.g. `B·C4`), and the
   element's name or visible text.
4. Every issue needs a concrete fix that a developer could build without a
   follow-up question.
5. Don't give numeric scores.
6. Answer the comparison below by choosing one option and saying why.
7. End with the three changes that would matter most.

## Comparison

- **Q1, leaderboard layout:** frame B (arms as columns) vs frame B-alt (arms as
  rows). Which is easier to read for 2–4 arms?

## Output format

Reply with JSON only, in this shape:

```json
{
  "issues": [
    {
      "frame": "A",
      "grid": "C4",
      "element": "matrix cell, trial × codex-logged-out",
      "criterion": "C2",
      "severity": "high",
      "problem": "One sentence on what is wrong.",
      "why": "One sentence on which job it hurts and how.",
      "fix": "A concrete, buildable change.",
      "confidence": "high"
    }
  ],
  "comparisons": {
    "Q1": {"choice": "B", "why": "..."}
  },
  "top_three": ["...", "...", "..."]
}
```

`confidence` is `high`, `medium` or `low`: how sure you are this is a real
problem and not taste.

## Example issue

```json
{
  "frame": "D",
  "grid": "B6",
  "element": "phone matrix, label next to the running cell's boxes",
  "criterion": "C7",
  "severity": "medium",
  "problem": "The boxes and the 'quiet 52s' label are squeezed into one 120px cell, so neither reads at a glance.",
  "why": "Job 1: a stuck agent is the thing to spot on a phone, and it's lost in the clutter.",
  "fix": "On phones, show boxes only and signal quiet with the box border. Move the duration into the sheet that opens on tap.",
  "confidence": "high"
}
```
