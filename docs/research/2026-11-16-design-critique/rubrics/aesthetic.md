# benchspec: aesthetic critique rubric

## Context

You are giving **aesthetic** feedback on benchspec's visual identity: its design
system and the benchspec watch page built from it. Correctness and accessibility
were reviewed separately and are fixed. Here the question is taste, craft and
character.

benchspec is an open-source framework for benchmarking AI coding agents.
benchspec watch is a local page developers keep open beside their terminal while
a benchmark runs: a matrix of evals by arms, a progress counter, a leaderboard
and a detail drawer.

**The brief from the maintainer:** keep the current direction and refine it.
Don't reinvent it.

**Reference points the maintainer chose** (screenshots attached as REF-D, REF-G
and REF-I):

- **Warp (REF-D):** monospace headline, a dot-matrix grid, paper-white with a
  single strong accent. It reads like an engineering diagram.
- **Stripe Docs (REF-G):** crisp type, airy and precise, technical but friendly,
  code sitting naturally in the page.
- **Tailscale (REF-I):** warm off-white, small mono labels, a dot-grid logo,
  plain and confident.

All three are light or paper-toned. benchspec is dark by default, which is
settled, so translate their qualities into the dark theme, and let the light
theme draw on them most directly.

**Settled (don't argue against these):** dark theme by default; navy `#1B1F3B`
and orange `#E85D2F` as the core brand; one square box per sample, framed by
brackets like the bracket-matrix logo; distinct shapes per sample state; the
overall layout of watch (tabs, matrix, leaderboard, drawer).

**What you are given:**

- Design-system pages (`ds-*`): colors, type, logo, components and cover.
- watch frames: A (mid-run matrix), B (finished leaderboard), C (cell drawer)
  and D2 (phone leaderboard), in dark, plus A in light.
- Font comparison frames: the same frame A set in three monospace faces, FONT-1
  DM Mono (current), FONT-2 IBM Plex Mono and FONT-3 JetBrains Mono.
- The three references.

Every screenshot has a lettered grid (A–H across, 1–8 down).

## Criteria

| ID | Criterion | What good looks like |
| --- | --- | --- |
| E1 | Character | Has a point of view you'd recognize without the logo. Feels like benchspec, not a template. The bracket-matrix idea shows up beyond the logo. |
| E2 | Closeness to the references | Shares the references' qualities (precision, mono labels, diagram-like structure, warm plain confidence) without copying them. |
| E3 | Color harmony | Navy, orange and the status colors sit well together. Temperature is consistent, accents are used sparingly, and neither theme feels muddy, garish or flat. |
| E4 | Typography | The faces have character and pair well. Hierarchy comes from size, weight and spacing, not decoration. Numbers look good. |
| E5 | Rhythm and density | Spacing feels deliberate. Density suits a data tool without crowding. Alignment and edges line up. |
| E6 | Craft details | Borders, radii, shadows, strokes and small marks (brackets, dots, glyphs) are consistent and refined. Nothing looks default or unfinished. |
| E7 | Light theme | Stands on its own and is at least as good as dark, drawing on the paper tones of the references. |

## Instructions

1. Be specific and visual: name the element, the screenshot and the grid cell,
   and say what you see.
2. Report at most 12 observations, each either a problem or something to push
   further. Don't list what already works.
3. Every observation needs a concrete change at token level where you can: exact
   colors, sizes, weights, spacing, radii or strokes.
4. Stay within "refine, don't reinvent". Changes should feel like the same
   product, better.
5. Don't give numeric scores.
6. Answer the questions below.
7. End with the three refinements that would matter most.

## Questions

- **Q1, mono face:** of FONT-1 (DM Mono), FONT-2 (IBM Plex Mono) and FONT-3
  (JetBrains Mono), which suits benchspec best as the one monospace face for the
  wordmark, data and labels? Why?
- **Q2, references:** which one quality from the references would most improve
  benchspec if adopted, and exactly where would it go?
- **Q3, light theme:** should the light theme move toward the references' warm
  paper tones (off-white, not cool gray)? If so, give the exact background and
  surface colors.

## Output format

Reply with JSON only, in this shape:

```json
{
  "observations": [
    {
      "where": "A·C4, matrix cell boxes",
      "criterion": "E6",
      "kind": "problem",
      "severity": "medium",
      "observation": "One sentence on what you see.",
      "why": "One sentence on why it weakens the look.",
      "change": "A concrete change, with exact values.",
      "confidence": "high"
    }
  ],
  "questions": {
    "Q1": {"answer": "FONT-2", "why": "..."},
    "Q2": {"answer": "...", "where": "..."},
    "Q3": {"answer": "...", "values": "..."}
  },
  "top_three": ["...", "...", "..."]
}
```

`kind` is `problem` or `push` (something good that could go further). `severity`
is `high`, `medium` or `low`, and `confidence` is how sure you are this isn't
just personal taste.
