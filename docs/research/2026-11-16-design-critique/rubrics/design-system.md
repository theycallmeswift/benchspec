# benchspec design system: critique rubric

## Context

You are reviewing the **benchspec design system**. benchspec is an open-source
framework for benchmarking AI coding agents. Developers write evals in Markdown,
run them across *arms* (pairings of an agent harness and a model), and compare
the results. Its main UI is **benchspec watch**, a local web page that shows a
run live and opens dark by default.

**Who uses the design system, in priority order:**

1. **Coding agents** that will build benchspec watch from it, with no human
   designer in the loop. They read the token files, component docs and rules as
   text.
2. **Maintainers** keeping benchspec's README art, social image and UI
   consistent.

**Its job:** let an agent build benchspec UI that looks and reads as one
product, is accessible, and doesn't drift into generic AI-generated design.

**What exists today:**

- Brand palette: navy `#1B1F3B`, orange `#E85D2F`, salmon `#F2A48C`, gray
  `#A5ACBE`, and terminal colors.
- UI tokens: dark-first, with light counterparts. Includes status colors for
  pass, fail, timeout, error, running, booting, queued, not applicable and
  quiet, plus an 11-step pass-rate ramp.
- Type: DM Mono and DM Sans for the UI. The wordmark is set in the system
  monospace font. There is no named brand font.
- Logo: a bracket-matrix mark and a wordmark, with clear space and minimum size
  estimated from the artwork.
- Components: a status box, a matrix cell, and a progress counter.
- Tagline and voice rules.

**Settled decisions (do not argue against these):** dark theme by default; navy
and orange as the core brand colors; one square box per sample, framed by
brackets like the logo; developer-tool tone.

**What you are given:** the design system's token files and docs as text, plus
screenshots of its pages, each with a lettered grid (columns A–H, rows 1–8)
drawn on it.

## Criteria

| ID | Criterion | Weight | What good looks like |
| --- | --- | --- | --- |
| D1 | Agent-readable | High | Tokens are machine-readable and named by role, not by look (`--fail`, not `--red-2`). Each component has rules an agent can follow without guessing: states, sizes, spacing, do and don't. No rule exists only in a picture. |
| D2 | Complete enough to build watch | High | Every state, surface and interaction watch needs is specified: the cell states above, hover, focus, selected, disabled, empty, loading, both themes, phone and desktop. Nothing forces an agent to invent a value. |
| D3 | Accessible by construction | High | Every text and UI pairing the tokens allow meets WCAG 2.2 AA in both themes. Status never relies on color alone. Focus is visible. Motion respects reduced-motion. |
| D4 | Coherent token system | Medium | One spacing scale, one type scale, one radius set. Every token has a job and no two do the same job. The light theme is designed, not inverted. |
| D5 | Distinctive brand | Medium | Reads as benchspec, not a generic template. The bracket-matrix mark's idea carries into the UI. Avoids common AI-UI looks: neon on black, identical rounded cards, all-caps labels everywhere. |
| D6 | Typography | Medium | The fonts suit a developer tool and pair well with the wordmark. Roles (headings, UI text, data, code) are clear. Numbers use tabular figures where they line up. |
| D7 | Logo and assets | Low | Clear space, minimum size, light and dark variants, and misuse rules are defined, with no gaps such as a missing dark-background version. |
| D8 | Voice and copy | Low | The tagline and voice rules are specific enough that an agent writes UI copy that sounds like benchspec. |

## Severity

- **High:** an agent building watch from this system would produce wrong,
  inaccessible or off-brand UI.
- **Medium:** an agent would have to guess, or the system contradicts itself.
- **Low:** polish.

## Instructions

1. Report only problems. Don't comment on what works.
2. Report at most 15 issues, ranked by severity, then weight.
3. Point at exact spots: the file and token or section name, or the screenshot
   and grid cell (e.g. `tokens.css: --muted` or `Colors·D3`).
4. Every issue needs a concrete fix: the exact token values, rule text or
   missing spec to add.
5. Don't give numeric scores.
6. Answer the two questions below.
7. End with the three changes that would matter most.

## Questions

- **Q1, brand font:** benchspec has no named brand font, and the wordmark uses
  the system monospace font. Should it adopt one? If so, which one, and why?
  Name a free font (e.g. on Google Fonts).
- **Q2, UI pairing:** are DM Mono and DM Sans the right UI pairing for this
  brand? If not, what should replace them, and why?

## Output format

Reply with JSON only, in this shape:

```json
{
  "issues": [
    {
      "where": "tokens.css: --muted",
      "criterion": "D3",
      "severity": "high",
      "problem": "One sentence on what is wrong.",
      "why": "One sentence on what an agent building watch would get wrong.",
      "fix": "A concrete change, with exact values or rule text.",
      "confidence": "high"
    }
  ],
  "questions": {
    "Q1": {"answer": "...", "why": "..."},
    "Q2": {"answer": "...", "why": "..."}
  },
  "top_three": ["...", "...", "..."]
}
```

`confidence` is `high`, `medium` or `low`: how sure you are this is a real
problem and not taste.

## Example issue

```json
{
  "where": "components/matrix-cell.md",
  "criterion": "D2",
  "severity": "high",
  "problem": "The matrix cell spec covers pass and fail but not the quiet state the UI tokens define.",
  "why": "An agent building watch has to invent how a stalled agent looks, so it will differ from the mockups.",
  "fix": "Add a quiet row to the state table: running box with a 2px dashed --quiet border and no fill change, plus hover text 'No output for Ns'.",
  "confidence": "high"
}
```
