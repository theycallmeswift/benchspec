# Multi-model design critique of benchspec watch

Research, 2026-11-16. How the [design system](../design/README.md) and the
`benchspec watch` mockups were reviewed before implementation, and how to rerun
the review. The mockups and the design system drafts were private review
artifacts; the inputs and results that matter are in
[`2026-11-16-design-critique/`](2026-11-16-design-critique/).

## What was done

Three critique passes, each by six models through OpenRouter:
`meta/muse-spark-1.3`, `moonshotai/kimi-k3`, `openai/gpt-6-astra`,
`anthropic/claude-opus-5.5`, `google/gemini-4-argon` and `x-ai/grok-4.7`.

| Pass | Rubric | Synthesis | Result |
| --- | --- | --- | --- |
| Design system | [`design-system.md`](2026-11-16-design-critique/rubrics/design-system.md) | [`design-system.md`](2026-11-16-design-critique/syntheses/design-system.md) | 29 fixes |
| Aesthetic | [`aesthetic.md`](2026-11-16-design-critique/rubrics/aesthetic.md) | [`aesthetic.md`](2026-11-16-design-critique/syntheses/aesthetic.md) | 19 refinements |
| Page | [`page.md`](2026-11-16-design-critique/rubrics/page.md) | [`page.md`](2026-11-16-design-critique/syntheses/page.md) | 21 changes |

## Method

1. Write a rubric per pass: who the work serves, the settled decisions not to
   argue, weighted criteria, and a JSON reply schema.
2. Package screenshots with a lettered grid drawn on them, so findings cite
   cells, plus the design system as text.
3. Run every model twice in parallel on the same package.
4. Cluster findings by element and problem, ranked by how many distinct models
   agree. Recompute every contrast claim from token values and check every
   visual claim against the screenshots; drop the false ones.
5. Drop findings that conflict with a settled decision.

## Outcome

All 69 changes were applied to the design system and the mockups. The
decisions they forced are recorded in
[#180](https://github.com/theycallmeswift/benchspec/issues/180). Raw replies
are in [`outputs/`](2026-11-16-design-critique/outputs/). The three passes
cost about $11.70 in total.

Two runs per model was enough to tell a model's noise from a shared finding,
and checking claims mattered: the syntheses record proposed colour values that
failed WCAG and visual claims the screenshots contradict.

## Rerunning

[`run_critique.py`](2026-11-16-design-critique/run_critique.py) needs Python
3.11 and `OPENROUTER_API_KEY`. Give it a rubric, a package directory
(`manifest.json`, the PNGs it names, optional `*.md` context) and an output
directory:

```sh
python3 -I run_critique.py rubrics/page.md package/ out/ --runs 2 --cap 10
```

It writes one JSON record per model run and prints the spend against the cap.
Model slugs live in its `MODELS` list; check them against OpenRouter's catalog
before a rerun.
