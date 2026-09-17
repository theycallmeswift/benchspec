# TypeSafe (Jev) at the binder layer

Research, 2026-09-17. Can TypeSafe replace Gemini as the binder? Tested against
`evals/binder/corpus.yaml`, head to head with the binder we ship today. No code
changes; nothing here is wired into `src/`.

Sources: the TypeSafe docs at [docs.typesafe.ai](https://docs.typesafe.ai)
(`/api`, `/primitives`, `/models`), plus two throwaway scripts run against the
corpus.

## Answer up front

**No — it's cheaper and faster, but it binds two assertions that must be
punted.** That's the one error the binder is not allowed to make, so it fails
the gate. Gemini made zero.

Everything else about it looks good, which is what makes it annoying.

## The numbers

Every one of the 139 corpus assertions was run 10 times through each model —
1,390 calls per side. The corpus has 53 assertions that should bind and 86 that
should punt.

| | TypeSafe `jev-1.13.0` | Gemini `gemini-3.5-flash-lite` |
| --- | --- | --- |
| **bound something it must punt** | **2 of 86** | **0 of 86** |
| picked the right checker | 43 of 53 | 42 of 53 |
| punted when it should have bound | 9 of 53 | 11 of 53 |
| picked the wrong checker | 1 of 53 | 0 of 53 |
| average response time | 567 ms | 877 ms |
| cost for one pass over the corpus | **$0.005** | $0.074 |
| same answer on all 10 runs | 139 of 139 | 139 of 139 |

TypeSafe is about 15x cheaper. It charges $0.042 per million input tokens and
nothing for output; Gemini's rates are the ones already in
`evals/binder/conftest.py`.

**Both models are completely consistent.** Every assertion got the same answer
all ten times, from both. That surprised me — I assumed Gemini would wobble —
but the binder pins `temperature: 0` and on this corpus it never moved. Worth
knowing on its own: the gate currently runs 5 samples per assertion and 20 on
the `persistence` and `skill_invoked` cohorts, and on this corpus that extra
sampling caught nothing. Might be a cheap win, but I'd want to see it hold on
assertions the corpus doesn't cover before touching the gate — temperature 0 is
not a guarantee providers make.

## The two bad binds

Both got routed to `regex`, and both are things you'd need to actually read the
file to check:

- *"The new log entry has a `source:` line naming the move of the
  'pycon-keynote' folder into its archived destination"* — the `source:` line is
  checkable, but **naming the move** isn't.
- *"The Problem section uses labeled bullets (e.g. Symptom / Constraint), not
  multi-sentence paragraphs"*

Both did it all ten times. So it's a consistent blind spot, not bad luck — and
re-running won't clear it.

## The confidence knob doesn't rescue it

TypeSafe returns a 0–1 confidence with every answer, which Gemini doesn't give
us. The obvious idea: ignore any bind below some cutoff.

It nearly works. The two bad binds come back around 0.5–0.7 while genuine binds
are mostly 0.96–1.0. But the cutoff that reliably kills both is 0.75, and at
that point it's also throwing away 26 good binds — right-checker rate drops from
43/53 to 40/53, which is worse than Gemini's 42/53.

Worth noting: the *choice* never moved across runs, but the *confidence number*
jitters a few points either way. At a single sample a 0.72 cutoff looked good
enough; across ten it isn't. That's the main thing N=10 bought us.

## It can't fill in the arguments

Bigger problem than the leaks. TypeSafe only returns typed judgments — pick one
of these options, rate this 0–1. There's no text generation at all, so it
physically cannot produce a `path`, a `pattern`, a `glob`, or a `count`. It can
only ever do the routing half of the binder.

That's fixable — the binding prompt already demands arguments be copied verbatim
from the assertion, so we could pull them out with regex in code and have
TypeSafe pick among candidates. It'd actually make a made-up path impossible
rather than just penalized. But it's a real chunk of machinery, and none of it
was built or measured here.

## Where both models struggle

Both punt on the same cluster of complex `glob_count` assertions — the ones
written as "no spec file was written under `./docs/specs/`" rather than "zero
files match this glob". TypeSafe missed 9, Gemini missed 11, mostly the same
ones. The corpus is deliberately testing whether the binder can see through that
sentence structure, and neither model does well at it. That's the corpus working
as intended, and it's the clearest shared weakness in both.

## Recommendation

Don't swap the binder. Two bad binds is disqualifying and confidence gating
costs more than it saves.

If it's worth revisiting later, the argument-filling gap is the thing to settle
first — routing is already roughly at parity, so there's no point tuning it
further until we know whether the other half can work at all. The 15x price
difference is the reason to bother.
