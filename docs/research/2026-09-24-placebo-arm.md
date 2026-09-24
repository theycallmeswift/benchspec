# A placebo arm instead of `if:`

Research, 2026-09-24. Would installing a noop "stub" skill in the baseline arm keep
the numbers comparable better than scoping the trigger line off the baseline with
`- if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}` (#139)? One run of the in-repo `hello`
suite with a third, placebo arm, then the headline recomputed each way.

## Verdict

Keep `if:`. The stub triggers exactly as often as the real skill (15/15 each), because
triggering depends only on the description, and the two share one. Pooling the trigger
line against a stub doesn't cancel the bias `if:` removes. It dilutes the headline in
the other direction: **+56pp** against the stub versus **+83pp** under `if:`, from the
same samples, with no difference in what the skill actually did. A placebo is worth
having as an extra arm that separates a skill's content from its presence, not as a
replacement for the baseline.

## Setup

- Set `e2e-placebo` in `pyproject.toml`: Claude Code on `anthropic/claude-sonnet-4.6`
  through OpenRouter, effort `medium`, `GREETING_STYLE=formal`, `GREETING_LOCALE=en-US`.
- Three arms, `baseline = "baseline"`:
  - `baseline` installs nothing.
  - `placebo` installs [`evals/e2e/hello/placebo/SKILL.md`](../../evals/e2e/hello/placebo/SKILL.md),
    with the real skill's `name` and `description` and the body "This skill has no
    instructions. Carry out the request as you normally would."
  - `trial` installs the real skill.
- Each eval's `setup.sh` branches on `BENCHSPEC_ARM`.
- The four `hello` evals, 5 samples per cell: 60 samples, 0 errored.
- Judge: Codex on `google/gemini-3.5-flash`. Binder: OpenRouter.

```
uv run benchspec run --set e2e-placebo --judge-provider openrouter \
  --judge-model google/gemini-3.5-flash --binder-provider openrouter -- --count 5 -n 6
```

## Results

The headline, recomputed from the same `grading.json` files. The rates pool lines
across evals. The report's `All evals` footer averages per-eval rates instead, so it
reads 12% / 12% / 100%, but it tells the same story.

| View | Control | Treatment | Δ |
|---|---|---|---|
| 1. Today: `if:` drops the trigger line | baseline 17% | trial 100% | **+83pp** |
| 2. No `if:`, no stub (the bias #139 fixed) | baseline 11% | trial 100% | +89pp |
| 3. Stub as baseline, trigger line pooled | placebo 44% | trial 100% | **+56pp** |
| 4. Stub as baseline, trigger line dropped | placebo 17% | trial 100% | +83pp |
| 5. Placebo effect alone | baseline 17% | placebo 17% | +0pp |

Every noise band is ±0pp: every line was unanimous across its 5 samples.

Per line:

| Eval · assertion | baseline | placebo | trial |
|---|---|---|---|
| greets-by-name · `Alice.md` contains 'Hello, Alice!' | 0/5 | 0/5 | 5/5 |
| greets-by-name · Skill `hello` invoked | skipped | 5/5 | 5/5 |
| greets-by-name · greeting feels warm (judge) | 5/5 | 5/5 | 5/5 |
| allows-filesystem-traversal · `Alice.md` exists | 0/5 | 0/5 | 5/5 |
| allows-filesystem-traversal · `Alice.md` contains 'Hello, Alice!' | 0/5 | 0/5 | 5/5 |
| allows-filesystem-traversal · Skill `hello` invoked | skipped | 5/5 | 5/5 |
| greets-from-transcript · `Carol.md` contains 'Hello, Carol!' | 0/5 | 0/5 | 5/5 |
| greets-from-transcript · Skill `hello` invoked | skipped | 5/5 | 5/5 |
| writes-greeting-file · `Bob.md` matches 'Hello, Bob!' | 0/5 | 0/5 | 5/5 |

## What it shows

- **The stub triggers as often as the skill.** Placebo and trial both invoked `hello`
  15/15. The trigger line therefore adds a pass to both arms and never contributes to
  the delta. That is view 3's 27pp shrink relative to view 1. Pooling it measures
  nothing about the skill and makes the headline depend on how many trigger lines an
  eval set happens to carry.
- **Dropping the trigger line against a stub gives the same answer as `if:` (view 4 = view 1).**
  So the stub buys nothing on the headline that `if:` doesn't already give, and it
  costs a second `SKILL.md` per skill whose description must track the real one.
- **The placebo cost nothing here (view 5), but only because this suite has no room to show it.**
  Without the skill's instructions, the agent greets in chat and writes no file, with
  or without the stub. Baseline and placebo fail the same lines, and the judge passes
  "warm" everywhere. The stub was read and then ignored. Measuring whether a skill's
  mere presence helps or hurts needs evals that the baseline can partly pass.
- **The trigger rate belongs in its own row.** "Did the skill fire?" is an absolute
  property of the treatment arm. The Scoped assertions table already reports it per
  arm, which is where it belongs.

## Where a placebo arm is still useful

As a third arm, with `if:` kept for the trigger line. Trial vs placebo isolates what
the skill's content adds beyond its listing, its context cost, and the turn spent
invoking it. Trial vs baseline stays the total effect. That needs no framework change:
another arm plus a `setup.sh` branch, as here. It pays off on a suite whose
baseline scores well above zero.
