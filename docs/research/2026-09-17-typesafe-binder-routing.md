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
        "task": "Decide whether this single eval assertion can be verified mechanically by exactly one deterministic checker, WITHOUT reading file content for meaning. Do not judge whether the assertion is true — only whether it is mechanically checkable.",
        "stance": "You are a conservative classifier. Declining (punt) is always free: a punted assertion is graded by an LLM judge instead. The ONLY unacceptable error is choosing a checker that could pass on WRONG output. When in any doubt, punt.",
        "punt_when": [
          "Needs content read for meaning — 'reflects the facts', 'is accurate', 'the summary states X'.",
          "Bundles two or more facts — 'exists and contains all six templates'; a skill line that also says what was written.",
          "Claims something was NOT changed, removed, duplicated or added — 'still present', 'left intact', 'not duplicated', 'no new entry was written'. A surface check cannot see the '...and nothing else happened' half. The one exception is an explicit byte-identical/sha256 claim."
        ]
      },
      "criteria": {
        "file_exists": "The assertion says a single path (file or directory) exists or was created. Nothing else.",
        "not_file_exists": "The assertion says a single path is gone or never existed. A pure path-absence claim.",
        "glob_count": "The assertion says exactly N, or at least N, files match a glob pattern.",
        "regex": "The assertion says a named file has a line matching a stated pattern or literal prefix.",
        "frontmatter_has": "The assertion says a named file's YAML frontmatter has a given key (optionally a given value).",
        "sha256_match": "The assertion explicitly claims a file is byte-identical to a named file or to its own pre-run content.",
        "skill_invoked": "A bare activation line: skill X was invoked. Nothing about what it wrote.",
        "not_skill_invoked": "A bare non-activation line: skill X was not invoked. Nothing about what it wrote.",
        "punt": "Anything else. Choose this when the assertion needs content read for meaning, bundles two or more facts, or claims something was NOT changed/removed/duplicated/added."
      }
    }
  }
}
```

That payload is verbatim — it is the prompt these results were measured with.
Every example below carries a **[Playground link][pg-payload]** that opens it. They only restore the payload if you are
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

[pg-leak1]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABOjgB3PgBtUAcz70GCAJ59uYJHzB8A5ElRUEFOFnVioggWBrGpDXnxqpc-VADM+V-uohyKGALQBrOHLoqAxwho6ooqSIfMYMqDEMKmC63FD2pHxRSAzGYDkYxCAQCKg0EIks7JzAADrofHw1ICVUIU1YfLX1DY0gDHIQcO29FNyoUHpNRHU9vcbZCFQU+ehIw12zDU0Myr7DTQAicBRQUXxCvK4ILqkqKOgSovxwuGCiqkhIiCt8FGD1ACN+PYEFBHFA4Bk6KN-hM3qIFACFGwwMsEXwMPwoiEEOZ0FBshNfrwKP4EEQ+AB1ACSTAAEgB5ACqTD4CDgYFIFj44KevwwIUYPNQ1zosIeADo+Ad4kEGHwAFZUUgSfgXOBXG78ZRfBA-AkuRb8QAoBBj0Oj1ZqoPKDdClPi-qJ0aNjr4wACnhKpjNNk1sv9JthegBNbSqdmqfmrRCvHL2X6iHVgiEIKVHCiiYzcgAUECojAAlDEkqIhGA5CpHOz9JG84xIR9dfqVBIEJyG0jVPUADLdgCyiuVqpiqxCnKlPH4DIAct3g3x86i9OV3XzECVrgbRqhUPcpGoXaTolY8vyqJE+BAdWaqQAlGcAcQxrTzDCllN49WMXYU5CoAIYCk6zfb1ulmJpgIAfXVdBhgAbR9TZemnOBIRULx6yFdlOWFUUOXxB4+FNdR2UcJ5lhUVweVRRJ1ApdQDSXHQ8lCeiqKQWgaGSBR-RCFQAA11C9YhEN9EAACF81IJ5KKEeIRRsEV+EcGiVGItgCUSLsMgwnZ5lUJ0+BQVgXDgMpEz49QAG5IyQXwoEMzMTBPeU3i0IzyxUC5TzLLzQQYQVhOmMCxIAYUTKAaDuUoNVSQjfL4acGVZGEHkhCl2VsdIKVIKgIEzP4QgyBTOSiDJiMJQzijgL5GDojQnkcG1GBo+r1DlTI8oKljSDaoIBGEaRGHkc5lHOfzBSEvgAEEjJ0FS9GJV1fn+Dqvn4KihIlCV-gyOU4qkOBRC+RRIEGQRetO0RHAnaxMWkVhl2bLsHvyiZrT4JEQm8U4ZDhUQAHokCUAAmABWAA2BMwEi4TRIaABdUSAF9gvAkAKH8xAoDAdZ4d6Xk4EgjTsjWIMmknRtvjQeokE82yLD5K8rD4bNCYxa4uXZZYRTkIsSa0hSEsxjkiqladggO6RjrgIL8aaOVIMJ4nWE0smOgp6wdWpjAPIrBmHiZvJuGLPgJHuhTBBBB7NMhKVZrzCNme4bx3VqxaMxhmg5ZCrYQEeVAAUgrx8wYfYuC1z4ddp+mUTRBRpwpEr5SeZR5UTnkHJqmw8lGSMA4BS88hxdAfaQpp2VVVhw8p7W9RpvWklMOgMnZpQm6c-guIYUZuTUXiG2ZkuOaMHE3kvUioFYMuxKrAVu5xSD25ryOmwbun9bUdAzAbQnND4YNpr7bseRKRgF+idv87Seg+H8BRs1QcoafhBQ1AkG-6leUQqDgAsZ-RsDMA4MIaQW7qMFe2oo7111mwN6JwGDOgilFSM7MDRfTgD9KIjB-ouHiFvHercs4jziAkFQqAhD1Gqt4RY9RdIyAAT0P09knSQWMLgVA-hSDh1mgCZI2plhpDyA3TuHQ7IOXePxUaKh2GcLtolSWfcATaHlN5G0qiSghEYX7RW4jWGyK4Twz6-CBA+BokIn4oijIsMkdI0xzUOFcPFoowi7oVHnCUOo8awRZagXLkUUOPD0ByCsNyI6XwpShTGLuDatwPG3yonXH4gg0JRkFPKbCrcFJigIhICkAIpIyRcHJEetgIwLUSEna4ntIrRToKE+KY0kopXtKqUgANMp2EhADXK8CeoA1KnbJoKMfTIzqMjQoXxyJFT7KgKIx1sBwRAAqF43gLI1Q4AjZGQA
[pg-leak2]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABAAUEqAEYAbODT5I4FBmnR8qMpHzFgRcCaT4iqYiQ1UAKOADoA5mb4BlAJ40IDVFID0fAMIYkDBGCiMAJREfOioDHw0+vIAtDKM9BT8EGB+Fn4Q3EjEIBDCjkYs7JzAADqKfKUgwlQMcFVYfGUVfJUgDHYQ9dhtFNyoUElVROWtrVUBPghUcgpIDU2jY+PtYEgA1gtVACKyUKT8AO68DLwIfKdQqijoFhJ8cLhgYnxrMgjyGHwUYIqafLhEFAAGZQOA6Oh9X6DZ5iOy6eFsMByOF8DD8A51BA0AJXeQUb68CjrRAhADqAEkmAAJADyAFUmHwEHAwKQAhY+KD7hQMHVGFzUOc6NDbtZtqhQuE+AArKikCxHE5nC68V5Id6fRRXC7TfiAFAI0ehUcc4KdEHwoBEdZDuNCfgZ4X1ZOsNBIzMMlssqj5fkMelUAJqoKivFmvb7eRBPeSA77qDUgsEIcWyMS4258YwQKhBS2qZ6HMB2VTAllwRpgPg5hI6N6ILX5vjpNnghGvRQAGU7AFlZfLFZb0D5WaRrDx+LSAHKdwNKdDIpJON38RDCc46vqoVA3TlV53Ei2nMARXn6HQpDVGvhkgBK04A4mjajmGNYybxtYpfvDyFQRAwIQ1m+notGMVTAQA+qa6ALAA2l6yxtFOcDgqovIJAKLJsoKwqsugHJ8IaADkLLAhIciqOaXLIkYxEhMROqLlQfh1PRfDEdRSC0DQqTwr6dSqAAGsRHrEIh3ogAAQrmpASFRhySkKkRCvwwK0aoJFsHiBboDoGEMP4w6vAY0hQKwFySBA6iCcRADcEYbFApnpug-DHhEzxIJKSDFqoxwnnwRb+QgVr8mJIxgZJHjqFANDXC4ZrcIRwV8FOtJMlCtzgiELI0KggKkCEpBUNZMJ1DoylsgcOgkT4zkvHkcDxAw7HERIwLWowtFtWEEQlWVPwVb1kpuYcDyMAg8KpYcoUMPyol8AAgtILHqUkhIut8vx9dIqGqvwolmGYvw6H1yWZloMh8HaEBdG5pDETdzzAuOarog8rBLo2TGKGwg1WgidQxPs9D4s8rhIHaABMACsABs8b+DQYkSXwAC6iEAL6RZJFBzUCYALM0SFtNycCQdpPjzAGXBqvWHwKNIfmORy9wpKcWbk2i5zsiychCnYgSfTpPNBWs3zYRV1hTuEF2cld5igaTVR9ZB5OU6wOlbHT-AM42vklqztzsye3BNhYH3KW5gLnFT0vLdWLHJGbMQaPEG0ULFKPK0hVR3KIkFnowOsTuqmpM4bqhIii8JTiEVURBIawRPHXLOc1kQnn0EYByI1Ynli6ARWjVQsoqrCh-TGoNpHLNVgudA6NzdoFmoAT8LxDB9IRVYCW2HNF2L6ZYs81ZkeZJdRWTwiMF3WKQa3Vd6zXjNfFHEaN225PEaogZLT2nZcrPDDzxare51AgKKCS8LGKgTgKLC8JVhYV-0ACzxUHAgRTyrIBQzAHDeGkEu59GXuHWuXx-rpgoFaVEXtkZt25jqEQdhgag0YDCF4zhN5gCbune4ylcFWlUKgQ4igmoxGmIoAyYM-5+wAesBqkEAi4FQCSUgOsVoiFSHrWYMYmauQrNIZhpkhLi1UGwjh4IZZy17iIEMEQArWmUcIOoDDJJqycgYVh6B2GcO4boPhUp0BuwESeIRHdGg6JeBI1Ku1pGcLkZcTMGglFBTtKooK6ilbiWnhBXMDBuHoHQfLB4YgZDWA8P0Hc7lkr+U-AdSBa9FBuTQpGTCERsLN2UiKAitwQh6D0vJC4ikxb5XDOtIwCdziILiglOgrjOSpXSplO02VSCuDygVcErgBqwJPH06qsiqjYyWFjcoWMcgyAohVHsqADiROwHBEAMpHgxBss1Dg6MsZAA
[pg-gitkeep]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF7owdUrwB0AcygMA1nDgRebKEgZJeYdAE8A7gAtEoqulKIZAemIgICVDQjaW7TsAA66Xry8gTlQMcAFYvN6+fv4gDPoQodgxFMaoUBSJRD7RMVDoWghUFAxo+WER2TkxDGBIiuUBACJwFFBWvCZwDKYIvN2avCjosiLquGBCukhIiCUYvBR6vABGoriIUIJwUnQpeukTQvorx2xgxUe8GKJWIQg0eZolFAumFMoIRLwA6gCSTAAJADyAFUmLwEHAwKQ8rIBMJRBQMCFGAJUL06PthtJeI0+OhUAxeAArKikWSiTrdazUqYzBBzXwDBiFUSAFAIruhLlSerwFHydLtjPtFkJLikWoowMsRNIAlkojkAlo9BkGiAAJqoKi6SG6BYYenjErrBZCWooLYIHHNChCR7DXgACggFgYAEoBbohIYwPodPxIXBwmBeK7GNs6bNSl7ZAhoZHlsclgAZFMAWRJZIpfPyIWhOKYpl4QK4KY1vAs5wyrmlo0QTl6AxSqFQQzhoYl7xpwqJSKoQikEAtnJ+ACVSwBxK7BV0MHHfUxM3x6Y7kKjLBhfcPz+WVJUON0AfU66HKAG191UAlwVKQdEiI6jIdC0RioehYbwOQBySH8ERih0Wl+HObQfy+H8BmrKh4xCCDeB-WkkFoGgwAQY4VRCHQAA0fzlYgrwPAAhCxSBEYDDD4dFeBodFRFAoDv0QjQtB0PQpEfGo8nYsVBigVg+jgFxzWwn8AG59TqYRJntdBRG6MAiQmJA+CQP0dBMJSOlqDoEAUFECIVKpogCABhc0oBoHRVLofpHV9HQuCBcE9mGbYvkhOj1lIL5SCoCB7UWEIpBo6ErCkX8tBksNIRmRgEJ-ER+CJPIamKRKCSJfzAoOELMvxOBDHURgMJ0zT9IYFF8N4ABBQZYMYxE3kUBY9CywYVD6Yt8OkaQOLEQljC-OAhBmXhhQgBJ5NIH8JomfhC2La51FYGtGS9JY2Fy1oiSTEIAFo2noZ4JhsJBhQAJgAVgANjNMArIIojeAAXSvABfYzTJAChKo2MBykiEyAkEEQj1Y7R1SLUQLWjeZ1P9KTYVGYdumdMHRBomFIWKdF9E9SGdBoxyFhfEKcR4ey4VGmYjJegIsqPTGIdYJ4kGh4s4YZGNEfY-jhlRpTjFjFaaPk9Zekh7YcXq109TR4wDuleKMgep690VH6RlQZYj37RhOdh6Z4d8PnVrAy4uC+MKiREWoiWt+EKNopSUn1HXljDJS7nQemtb8AJIQpVgjajHmEY0-VxEkZ3RGFfm5NENCGBSL9QywyM0d9q5entO4JliuBBFYf2QZAQNkRTu4jwTsPuY283QxjyNMZ-HQNVq9MUwEJxGGr6wE49qB1l8ZRjidVBXFKQ5k14eRR94cYhCoOB3TL68QAusAbtuo8U5SeuTYj3xtqChRxUs6z9Uxr19rgI6rEYA5JgYPhm4kVuEVzvo+AUYnDC+EcA-QovguInQ3geaSYojx5FwKgZQpB1T1WWOhWGxQR5KRjEncI0DJg4XKrmeBiDKZDXTssbURItKpSoU4EIkCfpMzwbA9AxDtjIJWGgwa6BlYYONNgvIwZBiKBigQ0mHU4EIJlrwKmw1HTSkoR0XsfJaGEjgAwwOh5DZJACLVAw1N1BjXUbwMyqQ2wKWGppJc3Vjb0g2vJbYD5kQnQhFCKQ-AaKYk-MML4ywyIuwYFRH+dE9SMW0DbXodpHrX1sl0ORcJSbOVcsKdypAbBeVQD5GwOUgpKW2DYcKMsAifUqB9HwH17AzEAiFdMqArBjWwOeEAxI4C4AOqJOAWgQCvQ+kAA
[pg-overpunt]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF4B3MEnEIoDBvV4M+pKEgDWvQCgEigBaidYdOX79ebVQykYhAT16TdouDVnzSvOkiRgA5nGIgEAioNBCWLOycwAA66Ly80SDBVPKJWLwxcfEJIAw2EHBpORQ6qFAUhcSx2TlQ6EgMCFQUDGj1RZk18YkMkmpFiQAicBRQpKJiegx6CLqqvCjoPiJmuGBC9l6IrRi8FIa8AEaiuIhQgnDudCWG5eu2R3ZsYC0PGKLj8ggu6Bble3oKGpEEReAB1ACSTAAEgB5ACqTF4CDgYBUSwEwlEFAw8kYAlQszotyWADpeIM+OhUAxeAArKikPziKYzRybJDbNq8eaNKiibTWOyTODTRA82nza4GX77IQPEojNRgQ4iUmJIjVLqJBqGCoDEAATVQVHsKPsewwnIQa1apz2QkkKAuCHJwwoQjqdR8vAAFBAqIwAJQ8qTrCQ2KT8FFwdJgXgBxiXDlc3bzHwINHJw52A4AGTzAFl6YzmXUGqjSOSmHpeLCuHnDbxAy8KmEVStEMFZvMSqhUIsffHFUDxdMwLScVQhO4IE7eLswQAlesAcQXKQDDHJYL0cTq9nQdnIVEODFBie3Gq1NUSl4A+iL0EUANo37XcOCXKQ4pP4lFogSRKor8GLaAA5Ci-AiC0UhigILyWOBoLgfMrZUJm8jIbw4HwUgtA0GACB2Lq8hSAAGuB6pVFkXQ5AAQoGpAiHBYh8ISHiEqI-CIVIEHmA0YZGJajBgOW9jygsUCsIozgQI6ZHgQA3Ba6jCBsnroKI460usSB8N4kYshO4gOGIMhyPQ1GarRH4AMKOlANBSPpdDTN6plSFwsJIjcSyXKCKI0KgpykKCpBUPJdxuAusxouM7gQQ06kJiinKMNh4EiPwkqiS0mXUrSEVRfsbgFVScBiGYjDEZ50iuPQVG8AAggsGE8RUAJKnshiFQsX7slRpKkoY7iFToHlwEInK8AYEAFFppDgbN6z8NWtbvGYrBtjs+5CVtJWyI88gALRjPQrRygA9EgBgAEwAKwAGwOmJNDUe+8QALrvgAvjZt4gBQFlnGAHSfTkggiPeAmWAaNaiE6qZxIZYZSUsKxztMfpQ6IHEqCiLSEjYIaw1YswSD+AFuOSPDuRiU2ctZEOJIV964zDrAWEg8O1kjCC7QsYBGfGg6YxOOihrwPibRxWmnLMsOXOSrUBuaWM6CdKrpZ1HpvcztndCAyyoIc95TowvOI1sAvcqjW2IQ8XCghxJkiJItLO5iLEeBOJQWibhwJhOXzoAbdGJCifisFbKa27s9vxugYB0O4uMrWjmmiIRDAlB5ou9DFWOh7FvCel86ypXAgisOHH7RriOdfPeBg89gOQI3HguJ7wyep97cDgVIhrNYWeYCMEjBN+KrcB1ApxxMCdi+qgYRtPcubS-PChrEI-JBnXgO3WAj1PfeOclLH-OC2wh0MAqjnORa6fzDmp3nYwdwbEoFp98m6ccR-rIKwYg4hBDgCdJocRfx4ivDRCOIA1LynvHUXAqBgSkANK1Q4RFEYtHnhObkWd0hII2OROqqD0HK14HTCaGIVQmlpJMEyR1zI0jgIfbIrMaT3lISg9AaCMFYKOLg3uGAtb4NtEQuosYFhqBSuQymYjcqCOobQ-OhxGHGUlEw4I8hOFG0vFgo89MfSMw4bwOypQBzaQmlIJ87Jr7ci0t+ESsDkSViAh4EC3pQSHCYj7BgbFS7BXNB1SwLtZh6yci5EIoo6E+iUd5XyMo-CkCukFEKlwrrFU9KVbJ8VlaJD+lqX6sRfoBE5DBNwhZUDjGmtgF8IA6RwFwCdBScAGggC+r9IAA
[pg-payload]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgMAWcABADoA9ACMqUADakhfUlARwKDVAgCefdKgDufNlCQMkfMOlJ8KGBmCjojYCRL49+FmtAlwzYJEjiHiIBAIqG6GLOycwAA66Hx8USDBVAxwCVh80bFx8VyqEKnYORTcqFAUBUQx2Tk2BghUSmi2aRlV1TlWSADWLQkAIopQpPxavM4ITtz6fCjoAOYeurj2xj6IDE3mJnwi-LiIUABmUJ58dMUmZfYS6iLqbGBKN3wY-MMpCDQ2+hsU5rwULqIIh8ADqAEkmAAJADyAFUmHwFGA5PM+MdFhZGPQGOiVGc4Jd5gI+H1UBpULiAFZUUhzEZjXgTZyrXwIDYYPjTBj1fiAFAIXuhnqM-EyubjpuduJcKNd1MVFF0wCIPAIEpUstUEgYTOVeiAAJqoKjGBTGcwYNnLDb7cwSbwoY6IEkDCgSb5ogAUECojAAlFy7BItGBVEZDgo4OkwHwfdivGt2ZtpnMECjTndjLEADLZgCyfBpdP4tRSKJJTF4fBhADlswa+L7HuUIFYVfxEMEJtNiqhULM5uaFYDEJMwLiLFQpLGHYKwQAlWsAcReyR9DBJoN4sRsWfU5CoIgYILjG-VbS1gV9DAA+iL0C0ANoX9oJGtwTxGLEpRhIwlmQ58ToIlBwFAByBRDg8JQjBZQ5HkMMCQTA6ZmyoNMUiQvgwJZJBaBoMA1BmKwUiMAANMC1WIF9LwAIV9UgPFgrRySAlR+HgmC+HAvQDDsUwLUYaxbGMBwZigVgnDgNx7VIsCAG5zW6SRHHddB+B4cdRKQckkFDIxRi0kMDIQKAGB-KiNXabIEgAYXtKAaCMHS6B4GxB2MvgaxhRELnmTwQQUGhUH2UgQVIKgIHdWUUjMfEUWGMxwIMFTYwUXxGCwsCPEOCUhKULLNFxCKoquWLCvJdSdBxIjPK0UzzPoSi+AAQRmdDOJcAEui2dAipmD9Jn4SiBAEEwzCKqY0TgCRfD4aUIHydTSDA+b7EOCsq1eXRWBbDkd34nbSooMydlUFIAFohhxK4JCEJBpQAJgAVgANjtawaComi+AAXRfABfKybJACgGoOMAWkyayEgxOAb14wx9UrfgHXWTY9LDJT3MWCBx24PhPThl4JjkBQlBUVQA0Rox8U8sHCVikka0pKbBxm3xLJ+hIipvOGEdYH4kGRqs0aTTlMbscT5lx-HAz4OZtvxdT9gmRHPBJNqfTNPGeAu5UMvKD7HK5zUQYWVARBvSdGBF1HE32mZ9J2hDnhrEF4txDxvFxd30UkOAjAIhhinNC2RBnRqEHQU2YcSOB6VYO3WXRiXnZjdAwDoACA7WqW1P4YPincpSSNOXWPlifF3Q+FYgjgY5WFj18QAjSxg4+G9pWFwoEhRlPxdiSXzUz7P-Y8MCjANFq82zdFgkYDvR27sOoH2WIgXUT1UFbJo5VX9e+GWCQqDgP1m8vB6wBe16byL7hk7Fx22GOsznjdT6peJ6Y7ku67GFuk4ckGcs6nGJviZQ4paZaFiPXC69RYjfhxBfEGykHA3hsLgVAQJSD6jaiIQiqNGjWk2AXdIaDHBkT4J5TB2CNZeVZiXZUxpcSGQlKw4IKQUFxB5pSG8FCMHoCwTgvBOxCEUnQPrYh45SE2CjDMLoqUqGeX6rQnBzNGFomYckah0p2HUM4XAbhORTx4PQOdNmuhZpGL4LZEo-YNJTAMtuIaA9HbqU-IJH8uJkQASAoSdA7kQRiFMExJwLESZnHYuiBCSAPYTA-o5ZyIRRQl08t5Xy0p-LSCCiFTwQgSrRXHPkhKGsEiAzaADGIAMAi+GgrFPMqBhizWwI+EAVI4C4AurJQOHBfoAyAA


---

## Addendum, 2026-09-18: prompt tuning removes the false positives

The three dangerous results above are fixable by prompt alone, and the honest
version of that fix is *smaller* than the prompt it replaces. Tuned over the same
corpus with a stratified 72/67 train/test split (seed 20260918).

| prompt | words | corpus-specific strings | leaks | mismatch | retention | cost/pass |
| --- | --- | --- | --- | --- | --- | --- |
| original | 306 | none | 2/86 | 1/53 | 0.811 | $0.0049 |
| heavily tuned | 906 | **5** | 0/86 | 0/53 | 0.925 | $0.0098 |
| **principles only** | **317** | **none** | **0/86** | **0/53** | 0.774 | **$0.0048** |
| gemini-3.5-flash-lite | — | — | 0/86 | 0/53 | 0.792 | $0.0738 |

**Take the third row.** The heavily tuned prompt reaches 0.925 retention by
naming corpus items outright — `.gitkeep`, `docs/specs`, `index.md`, `{TODAY}`,
"archive category". That extra retention is memorization and will not transfer to
new assertions, so the number is not real. The principles-only prompt contains no
file, path or phrase from the corpus, is *cheaper than the prompt we started
with*, and lands within one assertion of Gemini on retention while being 15x
cheaper and a third faster.

### What actually generalizes

Every rule that survived is either a statement about the checkers themselves or a
statement about assertions in general. Two ideas carry it:

**One adversarial question**, which is just the false-positive condition stated
directly:

> For each checker you consider: imagine it runs and PASSES. Could the assertion
> still be false? If yes, that checker is wrong — punt. You must also be able to
> write its arguments from the words of the assertion; if you would be guessing,
> punt.

**Criteria that describe the tool, not the test set.** Replacing "a pure
path-absence claim" with what `not_file_exists` mechanically does — *"tests one
exact path for non-existence; it looks at that path and nowhere else"* — fixed the
`.gitkeep` scope error on held-out data with no example needed. Same for
`glob_count` ("counts files only, never directories, and the pattern cannot
express an exception") and `regex` ("cannot tell what the matched text means").

Plus the three original punt classes, restated without quoted corpus phrasing:
content that must be read for meaning, more than one independent fact, and claims
that something did not change.

### What the experiments showed

- Tuning against the whole corpus would have shipped a regression. One
  intermediate prompt scored zero dangerous on train while introducing two
  *different* leaks on held-out data.
- Dropping the punt classes entirely (a 231-word minimum) reintroduced leaks on
  compound assertions. Simplicity has a floor: "bundles two facts" is a real
  principle, not tuning.
- Mechanical criteria beat descriptive ones. The ablation isolating them is the
  clearest single win in the whole loop.

### Stability

Across four full passes, **no punt-labeled assertion ever bound** — 0 of 344
punt-draws. One assertion of 139 moves at all, between its correct checker and
punt, which only costs a judge call. So the safety property is stable even though
the prompt is not perfectly deterministic, unlike the original prompt which was
139/139 fixed.

### Caveats

- Retention is 0.774, below the original prompt's 0.811. Safety is bought with
  roughly three extra judge calls per corpus pass.
- Still selected on one 139-assertion corpus. Nothing here names a corpus item,
  which is the point, but transfer is an argument until a fresh corpus tests it.
- Does not change the recommendation on its own. The argument-filling gap is
  still the thing that decides whether any of this can ship.

[pg-leak1]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABOjgB3PgBtUAcz70GCAJ59uYJHzB8A5ElRUEFOFnVioggWBrGpDXnxqpc-VADM+V-uohyKGALQBrOHLoqAxwho6ooqSIfMYMqDEMKmC63FD2pHxRSAzGYDkYxCAQCKg0EIks7JzAADrofHw1ICVUIU1YfLX1DY0gDHIQcO29FNyoUHpNRHU9vcbZCFQU+ehIw12zDU0Myr7DTQAicBRQUXxCvK4ILqkqKOgSovxwuGCiqkhIiCt8FGD1ACN+PYEFBHFA4Bk6KN-hM3qIFACFGwwMsEXwMPwoiEEOZ0FBshNfrwKP4EEQ+AB1ACSTAAEgB5ACqTD4CDgYFIFj44KevwwIUYPNQ1zosIeADo+Ad4kEGHwAFZUUgSfgXOBXG78ZRfBA-AkuRb8QAoBBj0Oj1ZqoPKDdClPi-qJ0aNjr4wACnhKpjNNk1sv9JthegBNbSqdmqfmrRCvHL2X6iHVgiEIKVHCiiYzcgAUECojAAlDEkqIhGA5CpHOz9JG84xIR9dfqVBIEJyG0jVPUADLdgCyiuVqpiqxCnKlPH4DIAct3g3x86i9OV3XzECVrgbRqhUPcpGoXaTolY8vyqJE+BAdWaqQAlGcAcQxrTzDCllN49WMXYU5CoAIYCk6zfb1ulmJpgIAfXVdBhgAbR9TZemnOBIRULx6yFdlOWFUUOXxB4+FNdR2UcJ5lhUVweVRRJ1ApdQDSXHQ8lCeiqKQWgaGSBR-RCFQAA11C9YhEN9EAACF81IJ5KKEeIRRsEV+EcGiVGItgCUSLsMgwnZ5lUJ0+BQVgXDgMpEz49QAG5IyQXwoEMzMTBPeU3i0IzyxUC5TzLLzQQYQVhOmMCxIAYUTKAaDuUoNVSQjfL4acGVZGEHkhCl2VsdIKVIKgIEzP4QgyBTOSiDJiMJQzijgL5GDojQnkcG1GBo+r1DlTI8oKljSDaoIBGEaRGHkc5lHOfzBSEvgAEEjJ0FS9GJV1fn+Dqvn4KihIlCV-gyOU4qkOBRC+RRIEGQRetO0RHAnaxMWkVhl2bLsHvyiZrT4JEQm8U4ZDhUQAHokCUAAmABWAA2BMwEi4TRIaABdUSAF9gvAkAKH8xAoDAdZ4d6Xk4EgjTsjWIMmknRtvjQeokE82yLD5K8rD4bNCYxa4uXZZYRTkIsSa0hSEsxjkiqladggO6RjrgIL8aaOVIMJ4nWE0smOgp6wdWpjAPIrBmHiZvJuGLPgJHuhTBBBB7NMhKVZrzCNme4bx3VqxaMxhmg5ZCrYQEeVAAUgrx8wYfYuC1z4ddp+mUTRBRpwpEr5SeZR5UTnkHJqmw8lGSMA4BS88hxdAfaQpp2VVVhw8p7W9RpvWklMOgMnZpQm6c-guIYUZuTUXiG2ZkuOaMHE3kvUioFYMuxKrAVu5xSD25ryOmwbun9bUdAzAbQnND4YNpr7bseRKRgF+idv87Seg+H8BRs1QcoafhBQ1AkG-6leUQqDgAsZ-RsDMA4MIaQW7qMFe2oo7111mwN6JwGDOgilFSM7MDRfTgD9KIjB-ouHiFvHercs4jziAkFQqAhD1Gqt4RY9RdIyAAT0P09knSQWMLgVA-hSDh1mgCZI2plhpDyA3TuHQ7IOXePxUaKh2GcLtolSWfcATaHlN5G0qiSghEYX7RW4jWGyK4Twz6-CBA+BokIn4oijIsMkdI0xzUOFcPFoowi7oVHnCUOo8awRZagXLkUUOPD0ByCsNyI6XwpShTGLuDatwPG3yonXH4gg0JRkFPKbCrcFJigIhICkAIpIyRcHJEetgIwLUSEna4ntIrRToKE+KY0kopXtKqUgANMp2EhADXK8CeoA1KnbJoKMfTIzqMjQoXxyJFT7KgKIx1sBwRAAqF43gLI1Q4AjZGQA
[pg-leak2]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYhMAWcABAAUEqAEYAbODT5I4FBmnR8qMpHzFgRcCaT4iqYiQ1UAKOADoA5mb4BlAJ40IDVFID0fAMIYkDBGCiMAJREfOioDHw0+vIAtDKM9BT8EGB+Fn4Q3EjEIBDCjkYs7JzAADqKfKUgwlQMcFVYfGUVfJUgDHYQ9dhtFNyoUElVROWtrVUBPghUcgpIDU2jY+PtYEgA1gtVACKyUKT8AO68DLwIfKdQqijoFhJ8cLhgYnxrMgjyGHwUYIqafLhEFAAGZQOA6Oh9X6DZ5iOy6eFsMByOF8DD8A51BA0AJXeQUb68CjrRAhADqAEkmAAJADyAFUmHwEHAwKQAhY+KD7hQMHVGFzUOc6NDbtZtqhQuE+AArKikCxHE5nC68V5Id6fRRXC7TfiAFAI0ehUcc4KdEHwoBEdZDuNCfgZ4X1ZOsNBIzMMlssqj5fkMelUAJqoKivFmvb7eRBPeSA77qDUgsEIcWyMS4258YwQKhBS2qZ6HMB2VTAllwRpgPg5hI6N6ILX5vjpNnghGvRQAGU7AFlZfLFZb0D5WaRrDx+LSAHKdwNKdDIpJON38RDCc46vqoVA3TlV53Ei2nMARXn6HQpDVGvhkgBK04A4mjajmGNYybxtYpfvDyFQRAwIQ1m+notGMVTAQA+qa6ALAA2l6yxtFOcDgqovIJAKLJsoKwqsugHJ8IaADkLLAhIciqOaXLIkYxEhMROqLlQfh1PRfDEdRSC0DQqTwr6dSqAAGsRHrEIh3ogAAQrmpASFRhySkKkRCvwwK0aoJFsHiBboDoGEMP4w6vAY0hQKwFySBA6iCcRADcEYbFApnpug-DHhEzxIJKSDFqoxwnnwRb+QgVr8mJIxgZJHjqFANDXC4ZrcIRwV8FOtJMlCtzgiELI0KggKkCEpBUNZMJ1DoylsgcOgkT4zkvHkcDxAw7HERIwLWowtFtWEEQlWVPwVb1kpuYcDyMAg8KpYcoUMPyol8AAgtILHqUkhIut8vx9dIqGqvwolmGYvw6H1yWZloMh8HaEBdG5pDETdzzAuOarog8rBLo2TGKGwg1WgidQxPs9D4s8rhIHaABMACsABs8b+DQYkSXwAC6iEAL6RZJFBzUCYALM0SFtNycCQdpPjzAGXBqvWHwKNIfmORy9wpKcWbk2i5zsiychCnYgSfTpPNBWs3zYRV1hTuEF2cld5igaTVR9ZB5OU6wOlbHT-AM42vklqztzsye3BNhYH3KW5gLnFT0vLdWLHJGbMQaPEG0ULFKPK0hVR3KIkFnowOsTuqmpM4bqhIii8JTiEVURBIawRPHXLOc1kQnn0EYByI1Ynli6ARWjVQsoqrCh-TGoNpHLNVgudA6NzdoFmoAT8LxDB9IRVYCW2HNF2L6ZYs81ZkeZJdRWTwiMF3WKQa3Vd6zXjNfFHEaN225PEaogZLT2nZcrPDDzxare51AgKKCS8LGKgTgKLC8JVhYV-0ACzxUHAgRTyrIBQzAHDeGkEu59GXuHWuXx-rpgoFaVEXtkZt25jqEQdhgag0YDCF4zhN5gCbune4ylcFWlUKgQ4igmoxGmIoAyYM-5+wAesBqkEAi4FQCSUgOsVoiFSHrWYMYmauQrNIZhpkhLi1UGwjh4IZZy17iIEMEQArWmUcIOoDDJJqycgYVh6B2GcO4boPhUp0BuwESeIRHdGg6JeBI1Ku1pGcLkZcTMGglFBTtKooK6ilbiWnhBXMDBuHoHQfLB4YgZDWA8P0Hc7lkr+U-AdSBa9FBuTQpGTCERsLN2UiKAitwQh6D0vJC4ikxb5XDOtIwCdziILiglOgrjOSpXSplO02VSCuDygVcErgBqwJPH06qsiqjYyWFjcoWMcgyAohVHsqADiROwHBEAMpHgxBss1Dg6MsZAA
[pg-gitkeep]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF7owdUrwB0AcygMA1nDgRebKEgZJeYdAE8A7gAtEoqulKIZAemIgICVDQjaW7TsAA66Xry8gTlQMcAFYvN6+fv4gDPoQodgxFMaoUBSJRD7RMVDoWghUFAxo+WER2TkxDGBIiuUBACJwFFBWvCZwDKYIvN2avCjosiLquGBCukhIiCUYvBR6vABGoriIUIJwUnQpeukTQvorx2xgxUe8GKJWIQg0eZolFAumFMoIRLwA6gCSTAAJADyAFUmLwEHAwKQ8rIBMJRBQMCFGAJUL06PthtJeI0+OhUAxeAArKikWSiTrdazUqYzBBzXwDBiFUSAFAIruhLlSerwFHydLtjPtFkJLikWoowMsRNIAlkojkAlo9BkGiAAJqoKi6SG6BYYenjErrBZCWooLYIHHNChCR7DXgACggFgYAEoBbohIYwPodPxIXBwmBeK7GNs6bNSl7ZAhoZHlsclgAZFMAWRJZIpfPyIWhOKYpl4QK4KY1vAs5wyrmlo0QTl6AxSqFQQzhoYl7xpwqJSKoQikEAtnJ+ACVSwBxK7BV0MHHfUxM3x6Y7kKjLBhfcPz+WVJUON0AfU66HKAG191UAlwVKQdEiI6jIdC0RioehYbwOQBySH8ERih0Wl+HObQfy+H8BmrKh4xCCDeB-WkkFoGgwAQY4VRCHQAA0fzlYgrwPAAhCxSBEYDDD4dFeBodFRFAoDv0QjQtB0PQpEfGo8nYsVBigVg+jgFxzWwn8AG59TqYRJntdBRG6MAiQmJA+CQP0dBMJSOlqDoEAUFECIVKpogCABhc0oBoHRVLofpHV9HQuCBcE9mGbYvkhOj1lIL5SCoCB7UWEIpBo6ErCkX8tBksNIRmRgEJ-ER+CJPIamKRKCSJfzAoOELMvxOBDHURgMJ0zT9IYFF8N4ABBQZYMYxE3kUBY9CywYVD6Yt8OkaQOLEQljC-OAhBmXhhQgBJ5NIH8JomfhC2La51FYGtGS9JY2Fy1oiSTEIAFo2noZ4JhsJBhQAJgAVgANjNMArIIojeAAXSvABfYzTJAChKo2MBykiEyAkEEQj1Y7R1SLUQLWjeZ1P9KTYVGYdumdMHRBomFIWKdF9E9SGdBoxyFhfEKcR4ey4VGmYjJegIsqPTGIdYJ4kGh4s4YZGNEfY-jhlRpTjFjFaaPk9Zekh7YcXq109TR4wDuleKMgep690VH6RlQZYj37RhOdh6Z4d8PnVrAy4uC+MKiREWoiWt+EKNopSUn1HXljDJS7nQemtb8AJIQpVgjajHmEY0-VxEkZ3RGFfm5NENCGBSL9QywyM0d9q5entO4JliuBBFYf2QZAQNkRTu4jwTsPuY283QxjyNMZ-HQNVq9MUwEJxGGr6wE49qB1l8ZRjidVBXFKQ5k14eRR94cYhCoOB3TL68QAusAbtuo8U5SeuTYj3xtqChRxUs6z9Uxr19rgI6rEYA5JgYPhm4kVuEVzvo+AUYnDC+EcA-QovguInQ3geaSYojx5FwKgZQpB1T1WWOhWGxQR5KRjEncI0DJg4XKrmeBiDKZDXTssbURItKpSoU4EIkCfpMzwbA9AxDtjIJWGgwa6BlYYONNgvIwZBiKBigQ0mHU4EIJlrwKmw1HTSkoR0XsfJaGEjgAwwOh5DZJACLVAw1N1BjXUbwMyqQ2wKWGppJc3Vjb0g2vJbYD5kQnQhFCKQ-AaKYk-MML4ywyIuwYFRH+dE9SMW0DbXodpHrX1sl0ORcJSbOVcsKdypAbBeVQD5GwOUgpKW2DYcKMsAifUqB9HwH17AzEAiFdMqArBjWwOeEAxI4C4AOqJOAWgQCvQ+kAA
[pg-overpunt]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgByqABAGZQANnF4B3MEnEIoDBvV4M+pKEgDWvQCgEigBaidYdOX79ebVQykYhAT16TdouDVnzSvOkiRgA5nGIgEAioNBCWLOycwAA66Ly80SDBVPKJWLwxcfEJIAw2EHBpORQ6qFAUhcSx2TlQ6EgMCFQUDGj1RZk18YkMkmpFiQAicBRQpKJiegx6CLqqvCjoPiJmuGBC9l6IrRi8FIa8AEaiuIhQgnDudCWG5eu2R3ZsYC0PGKLj8ggu6Bble3oKGpEEReAB1ACSTAAEgB5ACqTF4CDgYBUSwEwlEFAw8kYAlQszotyWADpeIM+OhUAxeAArKikPziKYzRybJDbNq8eaNKiibTWOyTODTRA82nza4GX77IQPEojNRgQ4iUmJIjVLqJBqGCoDEAATVQVHsKPsewwnIQa1apz2QkkKAuCHJwwoQjqdR8vAAFBAqIwAJQ8qTrCQ2KT8FFwdJgXgBxiXDlc3bzHwINHJw52A4AGTzAFl6YzmXUGqjSOSmHpeLCuHnDbxAy8KmEVStEMFZvMSqhUIsffHFUDxdMwLScVQhO4IE7eLswQAlesAcQXKQDDHJYL0cTq9nQdnIVEODFBie3Gq1NUSl4A+iL0EUANo37XcOCXKQ4pP4lFogSRKor8GLaAA5Ci-AiC0UhigILyWOBoLgfMrZUJm8jIbw4HwUgtA0GACB2Lq8hSAAGuB6pVFkXQ5AAQoGpAiHBYh8ISHiEqI-CIVIEHmA0YZGJajBgOW9jygsUCsIozgQI6ZHgQA3Ba6jCBsnroKI460usSB8N4kYshO4gOGIMhyPQ1GarRH4AMKOlANBSPpdDTN6plSFwsJIjcSyXKCKI0KgpykKCpBUPJdxuAusxouM7gQQ06kJiinKMNh4EiPwkqiS0mXUrSEVRfsbgFVScBiGYjDEZ50iuPQVG8AAggsGE8RUAJKnshiFQsX7slRpKkoY7iFToHlwEInK8AYEAFFppDgbN6z8NWtbvGYrBtjs+5CVtJWyI88gALRjPQrRygA9EgBgAEwAKwAGwOmJNDUe+8QALrvgAvjZt4gBQFlnGAHSfTkggiPeAmWAaNaiE6qZxIZYZSUsKxztMfpQ6IHEqCiLSEjYIaw1YswSD+AFuOSPDuRiU2ctZEOJIV964zDrAWEg8O1kjCC7QsYBGfGg6YxOOihrwPibRxWmnLMsOXOSrUBuaWM6CdKrpZ1HpvcztndCAyyoIc95TowvOI1sAvcqjW2IQ8XCghxJkiJItLO5iLEeBOJQWibhwJhOXzoAbdGJCifisFbKa27s9vxugYB0O4uMrWjmmiIRDAlB5ou9DFWOh7FvCel86ypXAgisOHH7RriOdfPeBg89gOQI3HguJ7wyep97cDgVIhrNYWeYCMEjBN+KrcB1ApxxMCdi+qgYRtPcubS-PChrEI-JBnXgO3WAj1PfeOclLH-OC2wh0MAqjnORa6fzDmp3nYwdwbEoFp98m6ccR-rIKwYg4hBDgCdJocRfx4ivDRCOIA1LynvHUXAqBgSkANK1Q4RFEYtHnhObkWd0hII2OROqqD0HK14HTCaGIVQmlpJMEyR1zI0jgIfbIrMaT3lISg9AaCMFYKOLg3uGAtb4NtEQuosYFhqBSuQymYjcqCOobQ-OhxGHGUlEw4I8hOFG0vFgo89MfSMw4bwOypQBzaQmlIJ87Jr7ci0t+ESsDkSViAh4EC3pQSHCYj7BgbFS7BXNB1SwLtZh6yci5EIoo6E+iUd5XyMo-CkCukFEKlwrrFU9KVbJ8VlaJD+lqX6sRfoBE5DBNwhZUDjGmtgF8IA6RwFwCdBScAGggC+r9IAA
[pg-payload]: https://console.typesafe.ai/decode#share/N4IghgDglgagpgJwM5QPYDsQC4QDcCMIANCACaoDGArgLZzoAuAKnAB4PYgMAWcABADoA9ACMqUADakhfUlARwKDVAgCefdKgDufNlCQMkfMOlJ8KGBmCjojYCRL49+FmtAlwzYJEjiHiIBAIqG6GLOycwAA66Hx8USDBVAxwCVh80bFx8VyqEKnYORTcqFAUBUQx2Tk2BghUSmi2aRlV1TlWSADWLQkAIopQpPxavM4ITtz6fCjoAOYeurj2xj6IDE3mJnwi-LiIUABmUJ58dMUmZfYS6iLqbGBKN3wY-MMpCDQ2+hsU5rwULqIIh8ADqAEkmAAJADyAFUmHwFGA5PM+MdFhZGPQGOiVGc4Jd5gI+H1UBpULiAFZUUhzEZjXgTZyrXwIDYYPjTBj1fiAFAIXuhnqM-EyubjpuduJcKNd1MVFF0wCIPAIEpUstUEgYTOVeiAAJqoKjGBTGcwYNnLDb7cwSbwoY6IEkDCgSb5ogAUECojAAlFy7BItGBVEZDgo4OkwHwfdivGt2ZtpnMECjTndjLEADLZgCyfBpdP4tRSKJJTF4fBhADlswa+L7HuUIFYVfxEMEJtNiqhULM5uaFYDEJMwLiLFQpLGHYKwQAlWsAcReyR9DBJoN4sRsWfU5CoIgYILjG-VbS1gV9DAA+iL0C0ANoX9oJGtwTxGLEpRhIwlmQ58ToIlBwFAByBRDg8JQjBZQ5HkMMCQTA6ZmyoNMUiQvgwJZJBaBoMA1BmKwUiMAANMC1WIF9LwAIV9UgPFgrRySAlR+HgmC+HAvQDDsUwLUYaxbGMBwZigVgnDgNx7VIsCAG5zW6SRHHddB+B4cdRKQckkFDIxRi0kMDIQKAGB-KiNXabIEgAYXtKAaCMHS6B4GxB2MvgaxhRELnmTwQQUGhUH2UgQVIKgIHdWUUjMfEUWGMxwIMFTYwUXxGCwsCPEOCUhKULLNFxCKoquWLCvJdSdBxIjPK0UzzPoSi+AAQRmdDOJcAEui2dAipmD9Jn4SiBAEEwzCKqY0TgCRfD4aUIHydTSDA+b7EOCsq1eXRWBbDkd34nbSooMydlUFIAFohhxK4JCEJBpQAJgAVgANjtawaComi+AAXRfABfKybJACgGoOMAWkyayEgxOAb14wx9UrfgHXWTY9LDJT3MWCBx24PhPThl4JjkBQlBUVQA0Rox8U8sHCVikka0pKbBxm3xLJ+hIipvOGEdYH4kGRqs0aTTlMbscT5lx-HAz4OZtvxdT9gmRHPBJNqfTNPGeAu5UMvKD7HK5zUQYWVARBvSdGBF1HE32mZ9J2hDnhrEF4txDxvFxd30UkOAjAIhhinNC2RBnRqEHQU2YcSOB6VYO3WXRiXnZjdAwDoACA7WqW1P4YPincpSSNOXWPlifF3Q+FYgjgY5WFj18QAjSxg4+G9pWFwoEhRlPxdiSXzUz7P-Y8MCjANFq82zdFgkYDvR27sOoH2WIgXUT1UFbJo5VX9e+GWCQqDgP1m8vB6wBe16byL7hk7Fx22GOsznjdT6peJ6Y7ku67GFuk4ckGcs6nGJviZQ4paZaFiPXC69RYjfhxBfEGykHA3hsLgVAQJSD6jaiIQiqNGjWk2AXdIaDHBkT4J5TB2CNZeVZiXZUxpcSGQlKw4IKQUFxB5pSG8FCMHoCwTgvBOxCEUnQPrYh45SE2CjDMLoqUqGeX6rQnBzNGFomYckah0p2HUM4XAbhORTx4PQOdNmuhZpGL4LZEo-YNJTAMtuIaA9HbqU-IJH8uJkQASAoSdA7kQRiFMExJwLESZnHYuiBCSAPYTA-o5ZyIRRQl08t5Xy0p-LSCCiFTwQgSrRXHPkhKGsEiAzaADGIAMAi+GgrFPMqBhizWwI+EAVI4C4AurJQOHBfoAyAA

---

## Addendum, 2026-09-18: prompt tuning removes the false positives

The three dangerous results above are fixable by prompt alone. A tuning loop over
the same corpus, with a stratified 72/67 train/test split (seed 20260918), landed
on a variant with **zero leaks and zero mismatches, and higher retention than
either baseline**.

| | leaks | mismatches | retention | cost/pass |
| --- | --- | --- | --- | --- |
| jev, original prompt | 2/86 | 1/53 | 0.811 | $0.0049 |
| **jev, tuned prompt** | **0/86** | **0/53** | **0.925** | $0.0098 |
| gemini-3.5-flash-lite | 0/86 | 0/53 | 0.792 | $0.0738 |

Still deterministic: two independent passes of the tuned prompt agreed on
139/139 assertions, so one sample per assertion is still enough. The prompt is
about twice as long, so cost doubles — still 7.5x under Gemini.

Four additions to `instructions` did the work, in the order they were found:

1. **A sufficiency test.** "Would that ONE checker verify EVERY clause? If any
   clause is left unverified — a qualifier, a purpose, what something names or
   refers to — the checker is insufficient; punt." This alone cleared the
   `regex` leak at no retention cost.
2. **Counting guidance.** Prose phrasings of a countable fact are `glob_count`,
   with the sharp line that absence of a *file* is countable while absence of
   *content inside an existing file* is not.
3. **Elaboration vs. second fact.** Apply the sufficiency test to facts, not
   clauses — a clause restating the same fact, or spelling out what the path
   already encodes, adds nothing to verify. This took retention 0.857 → 0.929.
4. **A glob-writability test.** A count-zero claim binds only if you can write
   the one literal glob from the words of the assertion. Additions 2 and 3 were
   too permissive without it.

### What the split caught

After addition 3 the train split showed zero dangerous results and looked
solved. The held-out split had two — *different* ones: "no duplicate or backup
of the profile page" and "no archive category folder other than 'Sources'" both
bound to `glob_count`, neither expressible as a single glob. Tuning on the whole
corpus would have shipped that regression. Addition 4 fixed it, and the fix held
on both splits.

### Caveats

- Addition 4 was written after seeing the held-out failures, so the test split
  is no longer a clean holdout. The 0.925 is *selected on this corpus* and needs
  fresh assertions to confirm.
- Retention was deliberately not pushed past 0.925. The four remaining
  over-punts include cases where binding looks unsafe: `glob_count` matches
  files only, so "no `docs/specs/` directory was created" is not covered by a
  count of 0, and `{TODAY}-*.md` does not exclude a `-design` suffix. Jev
  punting those is correct.
- This changes the outlook but not yet the recommendation. Confirm on a fresh
  corpus, and settle argument-filling, before revisiting the swap.
