# TypeSafe (Jev) at the binder layer

Research, 2026-09-17. Can TypeSafe's System One model replace the Gemini model
we use in benchspec's binder? Tested head to head against our labeled corpus.

Written up so it can be shared with the TypeSafe team as feedback — the
background below exists because none of it is obvious from outside this repo. No
code changes; nothing here is wired into `src/`.

Sources: [docs.typesafe.ai](https://docs.typesafe.ai) (`/api`, `/primitives`,
`/models`), and two throwaway scripts run against `evals/binder/corpus.yaml`.

## Answer up front

**No — it binds two assertions that must be punted.** That's the one error the
binder isn't allowed to make. Gemini made zero on the same corpus.

It's 15x cheaper and noticeably faster, and its routing accuracy is otherwise
level with Gemini's. The blocker is those two, plus a structural gap that means
it can't do the whole job anyway.

## Background: what the binder is for

benchspec runs agent evals. You write assertions about what an agent should have
done, in plain English, in a Markdown file:

```
- ./report.md exists
- Exactly 6 files match ./.meta/templates/*.md
- The summary faithfully reflects the three key facts from the source
- Skill `archive` invoked
```

After the agent runs in a sandbox, every assertion has to be graded. Two ways:

- **A deterministic checker.** Free, instant, exact, reproducible. But only
  works for assertions that are mechanically checkable — does this path exist,
  do N files match this glob, does this file have a line matching this regex.
- **An LLM judge.** Costs money on every run, and is a judgment call. Needed for
  anything requiring you to read the content for meaning.

The **binder** is the classifier that decides which. It reads one assertion and
either emits a checker spec, or declines — we call declining a *punt*:

```
for assertion in spec.assertions:
    checker = bind(assertion.text)        # checker spec, or None to punt

    if checker is not None:
        result = run_checker(checker, workdir)   # free, exact
    else:
        result = llm_judge(assertion, workdir)   # paid, judgment
```

### The asymmetry that defines the problem

The two possible mistakes are wildly different in cost:

```
# Punted something it could have bound:
#   -> still graded correctly, by the judge. Costs one judge call. Fine.
#
# Bound something it should have punted:
#   -> a checker that passes on output a human would call WRONG.
#      The eval reports green. Nobody finds out.
```

A wrong punt is a rounding error on the bill. A wrong bind silently corrupts
benchmark results. So the binder is deliberately built as a conservative
classifier: **when in any doubt, punt.** Our gate enforces zero wrong binds and
sets no floor at all on how much it punts.

That's the property we were testing TypeSafe against. It's an unusual ask — we
don't want the most accurate classifier, we want one that is never confidently
wrong in one specific direction.

## How we tested

The corpus is 139 hand-labeled assertions: 53 that should bind (labeled with the
correct checker) and 86 that should punt. Each was run 10 times through each
model — 1,390 calls per side.

The test is one assertion in, one routing decision out:

```
def test_binder(assertion, gold_label):
    choice = route(assertion.text)

    if gold_label == "punt":
        assert choice == "punt"          # the gate. zero tolerance.
    else:
        record(choice == assertion.expected_checker)   # reported, not gated
```

Gemini ran through our production path: a ~1,500-token prompt with rules and
worked examples, `temperature: 0`, JSON out, parsed and schema-validated.

For TypeSafe we sent the assertion as `state` and asked a single Choice with
nine options — our eight checkers plus `punt`:

```
POST /v1/systemone
{
  "state": "<the assertion text>",
  "model": "jev-latest",
  "questions": {
    "route": {
      "type": "choice",
      "instructions": {
        "task":   "Can this assertion be verified mechanically by exactly one
                   deterministic checker, WITHOUT reading file content for meaning?",
        "stance": "You are a conservative classifier. Punting is always free —
                   a punted assertion goes to an LLM judge instead. The ONLY
                   unacceptable error is a checker that could pass on WRONG
                   output. When in any doubt, punt.",
        "punt_when": [
          "Needs content read for meaning — 'reflects the facts', 'is accurate'",
          "Bundles two or more facts — 'exists and contains all six templates'",
          "Claims something was NOT changed/removed/duplicated/added"
        ]
      },
      "criteria": {
        "file_exists":       "A single path exists or was created. Nothing else.",
        "not_file_exists":   "A single path is gone. A pure path-absence claim.",
        "glob_count":        "Exactly N, or at least N, files match a glob.",
        "regex":             "A named file has a line matching a stated pattern.",
        "frontmatter_has":   "A named file's frontmatter has a given key.",
        "sha256_match":      "A file is byte-identical to a named file.",
        "skill_invoked":     "A bare activation line: skill X was invoked.",
        "not_skill_invoked": "A bare non-activation line: skill X was not invoked.",
        "punt":              "Anything else."
      }
    }
  }
}
```

Both runs bypass `_bind_bare_exists`, a local regex shortcut that binds simple
existence assertions with no API call at all. Leaving it in would have measured
the shortcut rather than the model.

## Results

| | TypeSafe `jev-1.13.0` | Gemini `gemini-3.5-flash-lite` |
| --- | --- | --- |
| **bound something it must punt** | **2 of 86** | **0 of 86** |
| picked the right checker | 43 of 53 | 42 of 53 |
| punted when it should have bound | 9 of 53 | 11 of 53 |
| picked the wrong checker | 1 of 53 | 0 of 53 |
| average response time | 567 ms | 877 ms |
| cost for one pass over the corpus | **$0.005** | $0.074 |
| same answer on all 10 runs | 139 of 139 | 139 of 139 |

About 15x cheaper — $0.042 per million input tokens with output free, against
Gemini's rates already recorded in `evals/binder/conftest.py`.

**Both models are completely consistent.** Every assertion got the same answer
all ten times, from both. Worth knowing on its own: our gate samples each
assertion 5 times (20 on two cohorts) and that extra sampling caught nothing
here. Possibly a cheap win, but I wouldn't act on it yet — `temperature: 0` is
not a guarantee providers make, and the corpus may not cover the cases where it
breaks.

## Where it failed

### The two bad binds

Both routed to `regex`, and both need the file read to actually check:

```
assertion: "The new log entry has a 'source:' line naming the move of the
            'pycon-keynote' folder into its archived destination"

  jev    -> regex   { regex: 0.71, punt: 0.29 }   confidence 0.63–0.72
  gemini -> punt
  wanted -> punt

  why: "has a 'source:' line" is checkable. "naming the move" is not —
       you have to read the line and understand what it refers to.
```

```
assertion: "The Problem section uses labeled bullets (e.g. Symptom /
            Constraint), not multi-sentence paragraphs"

  jev    -> regex   { regex: 0.58, punt: 0.42 }   confidence 0.48–0.56
  gemini -> punt
  wanted -> punt

  why: no regex distinguishes a labeled bullet from a paragraph that
       happens to start with a word and a colon.
```

Both did it all ten times, so it's a consistent blind spot rather than bad luck.
The shared shape: an assertion that *contains* a mechanically checkable surface
feature, wrapped in a semantic claim. Jev latches onto the checkable part.
Notably it isn't wildly confident in either — 0.29 and 0.42 of the mass sat on
`punt` — it just didn't land there.

### One wrong checker

```
assertion: "No file named .gitkeep exists anywhere under ./"

  jev    -> not_file_exists  { not_file_exists: 0.57, glob_count: 0.37, ... }
  gemini -> punt
  wanted -> glob_count      (with count: 0 — it's a recursive search, not one path)
```

Harmless here, since `not_file_exists` would also fail correctly in most cases,
but it's the right kind of near-miss to notice: the distribution had the correct
answer in second place at 0.37.

### Where both models struggle

Both punt on the same cluster of `glob_count` assertions written in natural
prose rather than glob terms:

```
assertion: "No file was written to disk — the handoff exists only as the
            emitted message"

  jev    -> punt  { punt: 0.98 }    confidence 0.96–0.98
  gemini -> punt
  wanted -> glob_count  (count: 0)
```

```
assertion: "All three are flat markdown files under ./9. Archive/Sources/{TODAY}/
            (rust-async.md, wasm-components.md, edge-databases.md), not wrapped
            in per-source subfolders"

  jev    -> punt  { punt: 0.80, glob_count: 0.18 }
  gemini -> punt
  wanted -> glob_count  (count: 3)
```

TypeSafe missed 9 of these, Gemini 11, mostly the same ones. The corpus includes
them deliberately — they test whether the binder can see a countable glob claim
through complicated sentence structure. Neither model does well. This is the
clearest shared weakness, and it's the one costing us real judge spend today.

## The confidence knob doesn't rescue it

TypeSafe returns a 0–1 confidence with every answer, which Gemini doesn't give
us. The obvious move: ignore any bind below a cutoff.

It nearly works. The two bad binds sit around 0.5–0.7 while genuine binds are
mostly 0.96–1.0. But the cutoff that reliably clears both is 0.75, and there
it's also discarding 26 good binds — right-checker rate falls from 43/53 to
40/53, below Gemini's 42/53.

One detail worth flagging to the TypeSafe team: the *choice* never moved across
10 runs, but the *confidence value* jitters a few points (one leak ranged
0.63–0.72 across identical calls). At a single sample a 0.72 cutoff looked good
enough; across ten it isn't. If confidence is meant to be used as a threshold —
and the docs present it that way — that jitter is the thing that decides whether
a threshold is safe to deploy.

## The structural gap: it can't fill in arguments

Bigger than the leaks. A checker spec isn't just a name, it's a name plus
arguments:

```
{"checker": "glob_count", "glob": "./.meta/templates/*.md", "count": 6}
{"checker": "regex", "path": "./answer.sql", "pattern": "(?i)type\\s*=\\s*'Email'"}
```

System One only returns typed judgments — pick one of these options, rate this
0–1. There's no text generation, so it physically cannot produce that `glob`,
`path`, `pattern`, or `count`. It can only ever do the routing half.

That's fixable on our side. Our prompt already requires arguments be copied
verbatim from the assertion, so we could extract candidates with regex in code
and have Jev pick among them — which would make a hallucinated path structurally
impossible rather than merely penalized, a genuine improvement over what we do
now. But it's a real chunk of machinery, and none of it was built or measured
here.

## Recommendation

Don't swap the binder. Two bad binds is disqualifying, and confidence gating
costs more than it saves.

If it's worth revisiting, settle the argument-filling half first — routing is
already at rough parity, so there's no point tuning it further until we know the
other half can work at all. The 15x price difference is the reason to bother.

### What would change our answer

For the TypeSafe team, the two things that actually gate adoption here:

1. **The semantic-claim-wrapping-a-checkable-surface pattern.** Both failures
   are the same shape. If a stance instruction can reliably push that toward
   `punt` without collapsing everything else into `punt`, the leak problem goes
   away.
2. **Confidence stability.** The choice is deterministic; the confidence isn't
   quite. For a zero-tolerance gate the threshold is the whole safety mechanism,
   so how tightly confidence is expected to reproduce across identical calls is
   the number we'd need.
