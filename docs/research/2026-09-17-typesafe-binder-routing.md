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
binder isn't allowed to make. Gemini made zero on the same corpus. A third
answer picks a checker whose scope is wrong in a way that also passes on failing
output, so three of its results here would report a broken run as green.

It's 15x cheaper and noticeably faster, and its routing accuracy is otherwise
level with Gemini's. The blocker is those three, plus a structural gap that
means it can't do the whole job anyway.

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

Every example below carries a **[Playground link][pg-payload]** that opens the
exact state and question we sent. They only restore the payload if you are
already signed in — a cold link bounces through `/login?returnTo=%2Fdecode`,
which drops the `#share/` fragment. TypeSafe's own docs links behave the same
way.

Both runs bypass `_bind_bare_exists`, a local regex shortcut that binds simple
existence assertions with no API call at all. Leaving it in would have measured
the shortcut rather than the model.

## Results

| | TypeSafe `jev-1.13.0` | Gemini `gemini-3.5-flash-lite` |
| --- | --- | --- |
| **bound something it must punt** | **2 of 86** | **0 of 86** |
| picked the right checker | 43 of 53 | 42 of 53 |
| punted when it should have bound | 9 of 53 | 11 of 53 |
| picked the wrong checker *(also a false pass)* | 1 of 53 | 0 of 53 |
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

[Reproduce in the Playground][pg-leak1]

```
assertion: "The Problem section uses labeled bullets (e.g. Symptom /
            Constraint), not multi-sentence paragraphs"

  jev    -> regex   { regex: 0.58, punt: 0.42 }   confidence 0.48–0.56
  gemini -> punt
  wanted -> punt

  why: no regex distinguishes a labeled bullet from a paragraph that
       happens to start with a word and a colon.
```

[Reproduce in the Playground][pg-leak2]

Both did it all ten times, so it's a consistent blind spot rather than bad luck.
The shared shape: an assertion that *contains* a mechanically checkable surface
feature, wrapped in a semantic claim. Jev latches onto the checkable part.
Notably it isn't wildly confident in either — 0.29 and 0.42 of the mass sat on
`punt` — it just didn't land there.

### A third false pass, which our own gate cannot see

```
assertion: "No file named .gitkeep exists anywhere under ./"

  jev    -> not_file_exists  { not_file_exists: 0.57, glob_count: 0.37, ... }
  gemini -> punt
  wanted -> glob_count  (count: 0)
```

This reads as a cosmetic near-miss and is not. `not_file_exists` is
`_negated(_file_exists)`, and `_file_exists` resolves **exactly one** path —
while the assertion is a recursive claim about the whole tree. Verified against
the real checkers, with the agent having left `docs/.gitkeep`:

```
not_file_exists  path=".gitkeep"             -> PASS   ** false pass **
                                                ".gitkeep absent" — only looked at the root
glob_count       glob="**/.gitkeep" count=0  -> FAIL   correct
                                                "1 match(es), expected exactly 0"
```

So three of Jev's outputs on this corpus would let a failing run report green,
not two. Our gate only catches the first two: it asserts on `punt`-labeled
entries, and this one is `bind`-labeled, so a wrong checker lands in the
`bind_mismatch` statistic rather than failing the gate. **That is a hole in our
gate as much as a finding about Jev** — a bind-labeled assertion that binds to
the wrong checker can be a false pass, and nothing currently fails on it. Worth
fixing on our side independently of TypeSafe.

The encouraging read: the correct answer sat second at 0.37 and `punt` at 0.05,
so the right shape was in view.

[Reproduce in the Playground][pg-gitkeep]

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

[Reproduce in the Playground][pg-overpunt]

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

Don't swap the binder. Three results that would report a broken run as green is
disqualifying, and confidence gating costs more than it saves.

If it's worth revisiting, settle the argument-filling half first — routing is
already at rough parity, so there's no point tuning it further until we know the
other half can work at all. The 15x price difference is the reason to bother.

### What would change our answer

For the TypeSafe team, the three things that actually gate adoption here:

1. **The semantic-claim-wrapping-a-checkable-surface pattern.** Both bad binds
   are the same shape. If a stance instruction can reliably push that toward
   `punt` without collapsing everything else into `punt`, the leak problem goes
   away.
2. **Scope: a claim about a tree is not a claim about a path.** The `.gitkeep`
   miss is a different shape — the model read "anywhere under `./`" and still
   chose a checker whose rubric says *a single path*. The right answer was
   second at 0.37, so the distinction is available to it but not decisive.
   Whether option rubrics can carry that kind of hard constraint is the question.
3. **Confidence stability.** The choice is deterministic; the confidence isn't
   quite. For a zero-tolerance gate the threshold is the whole safety mechanism,
   so how tightly confidence is expected to reproduce across identical calls is
   the number we'd need.

Also worth a look on their side: the Playground share links lose their payload
for a logged-out reader. `/decode#share/<blob>` redirects to
`/login?returnTo=%2Fdecode`, dropping the fragment, so the reader lands on an
empty Playground. Their own docs links behave the same way — confirmed in a
browser against both.

[pg-leak1]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABOjgB3PgBtUAcz70GCAJ59uYJHzB8A5ElRUEFOFnVioggWBrGpDXnxqpc-VADM+V-uohyKGALQBrOHLoqAxwho6ooqSIfMYMqDEMKmC63FD2pHxRSAzGYDkYxCAQCKg0EIks7JzAADrofHw1ICVUIU1YfLX1DY0gDHIQcO29FNyoUHpNRHU9vcbZCFQU+ehIw12zDU0Myr7DTQDCYPVWUElISIgrfABG-PYIUI5QcBl0o8cTYKKiCjcKbDAy1+fAw-CiIQQ5nQZxyFD4ozgFH8CCIfAA6gBJJgACQA8gBVJh8BBwMCkCx8Z6ifheRgyKmoBA2Mkw9ASAD8Uxmmya2WOk2wvQAmtpVKTVAiMJcELg8mlaaJlChnogAHR8AAKVEYlLOqlEQjAchUjlJ-EAKASSiA6kIZZVXND1CSoOAqOKqeoAGS9AFk+AArKikCT8eYhckanj8PEAOS9wr4OqBenKYBuNOkCBKzP1akRyOiVjyUqokT4EGVoPq6IASnGAOKg1o2hga9G8erGT0KchUG4MNE2xhq7ndWZNYcMAD6Qk7wwA2jzNr1Y3BXio6SFGCSyRlwsy6J92XwrepSY4act3dZHEDEuo0eo8xRqAg8qExyutiAAEI60gaXdIR4iZGwmX4O9r1PDQ2FhJJ0AyLcwHmA1RD4FBWBcOAyiVEIkHUL9v0OJUoBoFQtDoU4TyNFRYzxYkPnZV4AHpSVsdIWNIKgIFEL47RY8kolIJplx6ABdMSAF9pnHEZHkhKAwHWMSf2pOBpzg7I1iFJoAEEMIsTNKysaRWHg0FmVohFSQ-UgNVjYJUhPOBREuUdiFU3oghndTNPM7T9hAAyUHZYy8m4GIVBdQQNQMm0JRM7hvHTS50D0BFSJoDzZJXJoJHEG5py8W0goAUVYe8QVjNEwJLGllAYPgaqpKAgJsPJRklArUBuHKvKaUlQ1YIKDPQMxXlazMlCSIwTBoTrnKkNR+TtCs8khdB+rktSSkYBaGEhacZtG0w6H3NrQlNPaGAOyFFGUbqFXqfw5G2vKQCQJQACYAFYADZpwO0ZTvUqLbjkEJvCgKJdQob4XHiNRxvOqa4He3lPt8NrRGnYxcFQfwRN04LbmSfh7zSeUMDm-QMOxn4+AADT4az8cJ14MYnEAfOnJAGdx9midOm5yYEHxKbla4+MEDp+Zx5nWcenyYnQAmia5npJ1Kkm9PQSGlukNz0dEuSpJ5c30CkwpLivO1fVQKI3OwBcQADOBcG8PC3Q4cSpKAA
[pg-leak2]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABAAUEqAEYAbODT5I4FBmnR8qMpHzFgRcCaT4iqYiQ1UAKOADoA5mb4BlAJ40IDVFID0fAMIYkDBGCiMAJREfOioDHw0+vIAtDKM9BT8EGB+Fn4Q3EjEIBDCjkYs7JzAADqKfKUgwlQMcFVYfGUVfJUgDHYQ9dhtFNyoUElVROWtrVUBPghUcgpIDU2jY+PtYEgA1gtVHmCKDNxQqmsyCPIYuvy4iFAAZlBwOnR9u4NgBna6H2xgcmIfGPxSHA6ggaAFDvIKHw+rJ1ogQgB1ACSTAAEgB5ACqTD4CDgYFIAQsfDuEmhGDqjBJqAQkXx6CJAH5hktllUfLshj0qgBNVBUPipfhgcnoE64MDyK7Q9RIFB3RDWARURhEviHQViADuYDsqhueP4gBQCQV8CAquo6Y6IM6KCyoOCqZyCxQAGVdAFk+AArKikCz8SZ1AnWHj8dEAOVdPKU6B+SScGjJiGEtI1IphFDhtP2kvJ+h0KTlfHOCIASpGAOIl2rmhjWBG8RQBF0fchUEQMELmxhmFktMZVHsMAD6WqbCwA2qzlm0I3AHqoKBT6BE8QTqbS6C90MSTQByPE3CRyJ28Ek-Iz7kL79MUah+Or7-uzwcgABCKtIEidWtQJa3Gl+BuS9VAPNgISOdAdGXRh-DFTUxGkKBWD4OpHHUOokGfYgZzZEAPHUKAaFUJAXGBA5dz4HVVAjdEcWeXcHlcPEaFQK5SFcUgqAgMRXktVwCSBUgqjwvgAF0ZwAXxGAcqgoBAoBBKAwAWZpXyqUk4BHCCfHmbkQAAQWQ3cyRSfY+F0owAOotZoXXS1rAjcJKOJLQZD7XCBxWMJRy0nTWEgrYjJMiwzMlbh1VUe10HMPhjPNPEzQimINHiJIZX8GhPNkjSQDC0QR2XC1goAUVYS8-j4CMQhpQUIgkNYIhqkkoB-SJJT6U0CpEHKxKqPEA1YYLjLjOgdC0vhuDskU+NijqGD6NURQ5S1koYEF0D67y2gNCkaElEER2m-TGiqUawHG1qJH3fVhEYA6NsQKaZr4CwoCuRQ4Tsba8qQaaACYAFYADYR0evoRuuwNVBEOw6hiKAgVVCg3jQ-8RTGh5od+2d2XWNqxBHAJcFQOERIM4yRCFQVZglW01ACOBGg2Qm+AADVs1QSbJh5cfw3yR1ZgxifQUnyah6mkrCdBUrpyUFEZ2KWYJgwOa50JwnVMXedIfm32HEb0Hh1zLLEDzRIHKSlmt9ApJyGQT0tD1UCBc3sEnEBvTgXAYkwx0OHEqSgA
[pg-gitkeep]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF7owdUrwB0AcygMA1nDgRebKEgZJeYdAE8A7gAtEoqulKIZAemIgICVDQjaW7TsAA66Xry8gTlQMcAFYvN6+fv4gDPoQodgxFMaoUBSJRD7RMVDoWghUFAxo+WER2TkxDGBIiuUBAMJ6vAzGmrpISIglGLwARqK4iFCCcFJ0KXrpYEJC+gMLbGDF87wYolYhCDR5miUUvClwFMoIRLwA6gCSTAASAPIAqky8CHBgpHmyAsKiFBgQowBKgELw6NN0LIAPwBLJRHIBLR6DINEAATVQVF0710Rww3QQuDAJWGRyEtRQYwQ0l4AAULCUobwOrNDGB9Dp+O9RIAUAjxEEZ406hN6vlkqDgOgYfBaABk5QBZXgAKyopFkojyWg+pFpTFMvAeXDl6N4FhWGVcYH6InUCCcYNZR1Mp2sbRJ+KoQikEEp618lwASsaAOLrYKChi0y6mXx5XQGXjkKj9BgXQWMaRwyqIhyMgD6Jno5QA2rmqgEuCpSDoAYx6Aw3rqQWCIehvrx+QByd78ETFaWG-grbTdi7d1kUagIElwbs5hGVkAAIQspBE0sMfFB4NBohHg67vG7Gi0Oj0UnrNW1ujmvBQrFacBcFJCSAXxArecaFKgNB0JBnDgNpO3ZHQuAeV4pihcYbHeGhUGGUgbFIKgICEGYQhQz4rFIAJv14ABdCsAF94TzCgEAUEYwHKSIqhiQQRALM9tDRABBB9vjtP02nUVh9h0XdwKOd45z1XgeFA5k4CEbpsy-Jc-ACdBUAYAtmLgVjBPPTjuKhXiSWMFkdAldA4FpLjBVxPjjAAWhtbp0AyckwH-RSKOXWQhFQfoCwBRk0QAUVYUc1i4C5d09ERaibSLfk3cESRSPEfL8zzCICd5NVYfTxEkRLRGMWo8UwizkoYFJOzAB8amw3g+O2dBMuUpinEYGgSW2AsSqQfKJGFLTuy5DqGC6hhtl4Pq0qgYZfGUfRWsYpESoAJgAVgANgLCaUn0rTTMWEJ7KgKxGBmIRWllMRBqkLTluXOphCEAs8lwVBlHwpIAi4-owFxUc5pJUpeHKuBwme+8AA1eFE97PvGR68zUjSodehGvv0-7cTU9BHOKYGxTBvIIYfRQXt4WHRNRll0A+r7keiAJMwYTiDBkn45IUgil1Iyp+fQUj7G6AdsMVVArHk7BSxAFU4Fwey3ylDgiNIoA
[pg-overpunt]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF4B3MEnEIoDBvV4M+pKEgDWvQCgEigBaidYdOX79ebVQykYhAT16TdouDVnzSvOkiRgA5nGIgEAioNBCWLOycwAA66Ly80SDBVPKJWLwxcfEJIAw2EHBpORQ6qFAUhcSx2TlQ6EgMCFQUDGj1RZk18YkMkmpFiQDChrqq9l6IrRi8AEaiuIhQgnDudCWG5WBCtrN2bGAtOxiipHDyCC7oFuW8JXAUaohEvADqAJJMABIA8gCqTLwEHAwCp0D4BMJRBQMPJGAJUAgPMCrmCAPyJIjVLqJBqGCoDEAATVQVHsQPstwwSEQuDArQWtyEkhQywQADpeAAFKiMOrgsZbCQ2KT8IGibRgXgQHlucbUhBTOI+VBwKRKexxAAymoAsrwAFZUUh+Xh1BrA0gcph6XjfLiawm8HkHCphMAzERmBDBREC256B6IXR0ylUITuCDM3jTF4AJTtAHFoylpQwOS89HE6hq7OQqDMGM9pYw2RisTVEsWGAB9MSZooAbXL2O4cBWUmhjHoDEBFvhiLoGzBWl4AHIgfwRC01Tb+AdLKPnqOBRRqAg6XBR2Wsl0cgAhHmkERqsR8BEeBGiOfTkej8wNKSGdyd3pm+zbXgoViKZwQJnyJAtyqHcW0GJkoBoKQkBCM4dD5cQHC4b4AXWMEVgAeiBGhUAWUh0NIKg-02Nx0JBU5SESZt4gAXWbABfTEQMSCgZHOKAwA6KickEERq3vSwCQAQU-PlPUjBgdDMVgLCsREJA7IEN0tXgeAk+C4CEalS2A3dEnQVAax4uA+Okh8hJEsExLpSSxmVdA4A5YTpXJcSdAAWndal0AqRkwAg7TGN0kAfCEVAZmraEZQJABRVh5x2LhnnPEMREkHtEohY8PDpEoKRCsKAq4xIgT8VhzPQMA6HcIzeAMR9eCEOpRBoHK4OHSVcVlcTznQQqQO6EBRRhFq5EQas6vKyqVkyzcRWCRgRvOWqHElHwoAWOJHhsPqgqQAwACYAFYADZqxGkpzJqsYZhseQ3KgU5eQoLZFD4SUKqqmadpbdRhCEas6lwVBHgo7AcmEmYwHJed1rpNoGqa9Jfo-AANBCpEB4GVm+isQH0mtkf+zGQfMyHyX09APJaWHFQR+ykbUP7eDR+TeHx010CBkGceySsorBxJBPQW62vBDStMokC6KxaX0DogJqSnNwdVQU5NOwBsQH1OBcDc-9VQ4ai6KAA
[pg-payload]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgMAWcABADoA9ACMqUADakhfUlARwKDVAgCefdKgDufNlCQMkfMOlJ8KGBmCjojYCRL49+FmtAlwzYJEjiHiIBAIqG6GLOycwAA66Hx8USDBVAxwCVh80bFx8VyqEKnYORTcqFAUBUQx2Tk2BghUSmi2aRlV1TlWSADWLQkAwiZO3PrGPogMTXwi-LiIUABmUJ58dMUmZfYS6iLqbGBKW3wY-KR+iDQ2+hMU5rwUXYhEfADqAJJMABIA8gCqTHwKMBydAAcz4iw85ks9AY4JUKzg61BAH4EpUstUEgYTOVeiAAJqoKjGBTGKG2RC4MATWbmCTeFCLRACPgABSojBsYJG9i0YFURnmCn4gBQCMkQDkpLxjBATDB8EGoOBGZTGWIAGXVAFk+AArKikEH8WopIEspi8PhfABy6vxfA5+3KECsIkhiGCCD4PNuigeXp41KhVCkfAgDKOsWeACUbQBxI7JCUMFnPXixGxq9TkKgiBhPCWMARotqYwKSgD6WnTLQA2qX2glrXBPEYLIwYQDEWZ5vC6EiwWKAOQKeYeJQqy3zfaGIdPIc8ijUBDUuBDksYxsgABCHNIHhVWlQRy9NBU-GnE74w70BjspnJVlqxgcfBQrCccDc9JSSHXxAbMs+npKAaCMJAQj8YZQT4PkjGtL5-jWUFPCEBQz1maRSCoCAJA2KUhCBU5SASQC+AAXQbABfdEywoBAoBSBiwBaTJ2hyCE4ArW9DDxABBN8uUhcMeF0VgriMeE4PMQEpRZa1UB4LldAkXxiwAzc4gSTQGArTjuPEu9+ME0FhOpbhvSMRV0DgFkBIlUkRO4ABaMARF8dByjpawaHU2itxBCRUBECsLElPEAFFWBnQ5rSeeEgw8bxYTi8FJGVFZqWKMlAuCvyyISBQjVYYz0DAOge3SvhuG8Mk8JszKGGKZSwDfKwpTDakmPQfLNI44JGBoLrEArGqkFK8rlk4odBQGhghoYJjqtq1qQSgWZYgeVRevYrEaoAJgAVgANgrBbimMzjLKmVQUmcqBTk5Ch7CcY9WrKiq0o8Hat26SQJArGxcFQB4SMKBIBJEMBSRndbqUmeq4HSP7XwADVg2qgZBzwfrLHSKxRgGsdB4yodJTR0FcxoqTlWJEeRrp-r4dHpJ0710GB0HceyBJCwYfj0Fu6CwTgVTbNIzcqLaKX0CogJfHHKUtVQU5VOwWsQF1OBcGcn9lQ4ciqKAA
