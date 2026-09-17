# TypeSafe (Jev) at the binder layer

Research, 2026-09-17. Measures TypeSafe's System One model against the binder's
own labeled corpus, head to head with the production Gemini binder. Routing
only — the bind/punt call and the choice of checker. No code changes were made;
nothing here is wired into `src/`.

Sources: the TypeSafe docs at [docs.typesafe.ai](https://docs.typesafe.ai)
(`/api`, `/primitives`, `/models`), and two throwaway probe scripts run against
`evals/binder/corpus.yaml` at 139 entries (53 gold-bind, 86 gold-punt).

## Answer up front

TypeSafe is **15x cheaper per pass, ~1.8x faster, and deterministic** — but it
**leaks**, so it is not a drop-in replacement under the current zero-tolerance
gate. Two gold-punt assertions bound to `regex`, and both reproduce on every
draw. Gemini leaked none.

The determinism is the finding worth keeping. Jev returned an identical choice
on 12/12 assertions across 4 draws each. The current gate samples every entry 5
times (20 on the `persistence` and `skill_invoked` cohorts) *because* the Gemini
binder is stochastic and "0 leaks observed" is only meaningful across repeats. A
deterministic router does not need that, which is where the headline cost gap
comes from: ~$0.005 a pass against ~$0.51 for the gate as it runs today.

| | TypeSafe `jev-1.13.0` | Gemini `gemini-3.5-flash-lite` |
| --- | --- | --- |
| leak rate (gold-punt → bound) | **0.023** (2/86) | **0.000** (0/86) |
| determinism_retention | 0.811 (43/53) | 0.792 (42/53) |
| over_punt_rate | 0.170 (9/53) | 0.208 (11/53) |
| bind_mismatch | 0.019 (1/53) | 0.000 (0/53) |
| latency mean / p95 | 520 ms / 883 ms | 936 ms / 1333 ms |
| tokens in / out | 117,677 / 13,544 | 210,934 / 4,201 |
| cost per 139-call pass | **$0.0049** | $0.0738 |
| stable across repeat draws | yes (12/12) | not measured; gate assumes no |

Cost uses TypeSafe's published $0.042/Mtok input with output tokens free, and
the Gemini rates already in `evals/binder/conftest.py`. Jev returns a full
probability distribution, so it emits ~3x the output tokens — free under that
rate card, but not free under a metered one.

## A structural limit, not a tuning problem

TypeSafe only returns typed judgments: Choice, Noul, Score. There is no text
generation. It cannot emit `path`, `pattern`, `glob`, or `count`, so it can
never do the whole binder job — only the routing half. Filling arguments would
need code-side candidate extraction plus a second Choice to select among them.

That is less limiting than it sounds. The binding prompt already requires
arguments to be verbatim copies from the assertion ("Copy paths exactly as
written"), and `_is_drifted_regex` enforces it for patterns. Extraction in code
would make a hallucinated path structurally impossible rather than merely
punished. But it is real extra machinery, and it was not built or measured here.

## Method

Both runs bypass `_bind_bare_exists`, the local regex fast path that binds bare
existence assertions with no API call. Leaving it in would have flattered
whichever side it fired for and measured the fast path rather than the model.
One sample per entry per model; scored with the metric definitions in
`evals/binder/conftest.py` so the numbers line up with what `make evals` prints.

Gemini ran through the production path: `_BINDING_PROMPT` rendered, reply parsed
by `_parse_binding`, so schema-invalid output counts as a punt exactly as it
does in a graded run. TypeSafe got one Choice with nine options — the eight
checkers plus `punt` — with the binding prompt's conservative-classifier stance
and its three punt rules carried over into structured `instructions`, and one
rubric line per checker as `criteria`. The assertion text was the `state`.

The token counts are not like-for-like and should not be read as a prompt-
efficiency result: Gemini receives the full prompt with its worked examples,
TypeSafe receives state plus instructions and criteria. Each is the natural
shape for that API.

## What leaked

Both leaks chose `regex`, and both are genuinely semantic:

- *"The new log entry has a `source:` line naming the move of the
  'pycon-keynote' folder into its archived destination"* — confidence 0.71. The
  `source:` line is checkable; **naming the move** is not.
- *"The Problem section uses labeled bullets (e.g. Symptom / Constraint), not
  multi-sentence paragraphs"* — confidence 0.51.

Both reproduce on all four repeat draws, so they are model disagreements rather
than unlucky samples. That cuts both ways: reproducible means diagnosable, but
it also means re-running will not make them go away.

Confidence separates them from the true binds cleanly. Correct binds sit at
median confidence 1.00 and p10 0.96; the two leaks sit at 0.71 and 0.51. Gating
binds at confidence ≥ 0.72 drops both leaks and costs 3.7 points of retention
(0.811 → 0.774), which lands just under Gemini's 0.792. So a thresholded Jev is
roughly retention-equivalent to Gemini at zero observed leaks, 15x cheaper and
twice as fast — on this corpus, at n=1. That is a promising result, not a
validated one; the threshold is fit on the same 139 rows it is evaluated on,
and would need a held-out split before anyone trusted the number.

## An observation about the corpus, not the models

Both models over-punt the same cluster, and 7 of Jev's 9 over-punts are
`glob_count`. Two of those entries are worth a second look:

- *"No `docs/specs/` directory was created and no spec file was written during
  the session — the handoff is in-conversation only."* (gold: bind/`glob_count`)
- *"No file was written to disk — the handoff exists only as the emitted
  message"* (gold: bind/`glob_count`)

Jev punted both at 0.98 confidence, and Gemini punted them too. They read as
bundling two facts, and the binding prompt's own rules say to punt a compound
assertion. They may still be correctly labeled — a single `glob_count` with
`count: 0` does cover "nothing was written" — but the agreement between two
unrelated models at high confidence suggests the prompt does not communicate
that, and the shared over-punt is a corpus-clarity question rather than a model
defect. Worth resolving before either model's retention number is treated as
final, since it moves both.

## Recommendation

Do not swap the binder. The leak gate is zero-tolerance for a good reason, and
Jev fails it as configured.

Two things are worth a follow-up, in this order:

1. **Confidence-gated routing on a held-out split.** The threshold result is the
   real finding and the cheapest to firm up. Jev exposes a calibrated knob that
   Gemini does not, and the gate's hard constraint is exactly the kind of
   constraint a threshold is for. Needs a train/test split of the corpus.
2. **Argument extraction**, only if (1) holds up. Routing is the half that
   carries the safety property; arguments are the half that decides whether this
   can ship at all.

The determinism is worth noting regardless of whether TypeSafe is ever adopted:
it is the property that makes the gate cheap, and it is a reason to prefer a
classifier with a fixed decision rule at this layer over a sampled one.
