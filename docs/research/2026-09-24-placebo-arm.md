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

## Follow-up: harnesses that might not load the skill

The counterargument: some harness or model might never load the skill, and a stub
would put the trigger line on equal footing across arms. The next check ran set
`e2e-placebo-harness`: baseline, placebo, and trial arms on Codex (`openai/gpt-5.5`)
and on OpenCode (`anthropic/claude-sonnet-4.6`), 3 samples each, 72 samples, 0 errored.

| Eval · assertion | baseline-codex | placebo-codex | trial-codex | baseline-opencode | placebo-opencode | trial-opencode |
|---|---|---|---|---|---|---|
| Skill `hello` invoked (3 evals) | skipped | 9/9 | 9/9 | 0/9 | 9/9 | 9/9 |
| greets-by-name · greeting feels warm (judge) | 1/3 | 3/3 | 3/3 | 3/3 | 0/3 | 3/3 |
| Every file-content line (6 lines) | 0/18 | 0/18 | 18/18 | 0/18 | 0/18 | 18/18 |

- **Both harnesses loaded both skills every time.** Nothing here fails to load, so
  the stub and the real skill tie on the trigger line again.
- **A harness that didn't load skills would fail the trigger line in the stub arm and
  the trial arm alike.** Pooled against a stub, that line would add no difference in
  either direction, so the stub can't expose the failure. What exposes it is the trial
  arm's own trigger rate in the Scoped assertions table (`0/n` where it should be
  `n/n`), plus the outcome lines staying at the baseline's level.
- **Triggering happens before the body is read.** The stub shares the skill's `name`
  and `description`, so its trigger rate can't tell "didn't load" from "didn't trigger"
  any better than the trial arm's trigger rate already does.
- **The stub isn't a noop, because its description carries instructions.** The
  description says "a warm, deterministic greeting". Codex's baseline wrote "Hi Alice."
  and passed "warm" 1/3. With the stub it wrote "Hello, Alice. It's good to see you."
  and passed 3/3. A placebo that keeps the real description measures part of the skill.
- **The `if:` idiom only covers one no-skill arm.** The set's baseline was
  `baseline-codex`, so `{BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}` held for
  `baseline-opencode`, and its trigger line was graded 0/9. That is the structural
  penalty #139 was meant to remove. A set with more than one no-skill arm needs a
  property clause instead: an arm `env` key such as `HELLO_SKILL = "none"` and
  `if: {HELLO_SKILL} != "none"`.
- The OpenCode "warm" 3/3 → 0/3 is judge variance, not a placebo effect. Both arms
  answered "Hello, Alice!" in chat, and the judge passed it in one arm and failed it in
  the other.

## Follow-up: older and smaller models

The frontier models above fired every time, so they can't show whether the stub and the
skill part ways when firing is uncertain. Set `e2e-placebo-weak` ran placebo and trial
arms for eight weaker models through OpenRouter: the three evals with a trigger line,
5 samples each, 180s turn timeout, 240 samples, 0 errored. Qwen 2.5 7B was dropped
after it hung to the timeout in a smoke run.

| Harness · model | stub fired | skill fired | p (Fisher) | stub outcome | skill outcome |
|---|---|---|---|---|---|
| Claude Code · Claude 3 Haiku | 10/15 | 9/15 | 1.00 | 5/25 | 5/25 |
| Claude Code · Haiku 4.5 | 15/15 | 15/15 | 1.00 | 5/25 | 24/25 |
| Claude Code · Sonnet 4 | 15/15 | 15/15 | 1.00 | 4/25 | 23/25 |
| OpenCode · GPT-3.5 Turbo | 12/15 | 14/15 | 0.60 | 2/25 | 6/25 |
| OpenCode · GPT-4o mini | 15/15 | 15/15 | 1.00 | 5/25 | 6/25 |
| OpenCode · Llama 3.1 8B | 14/15 | 11/15 | 0.33 | 4/25 | 8/25 |
| OpenCode · Ministral 8B | 15/15 | 15/15 | 1.00 | 1/25 | 20/25 |
| Codex · GPT-4.1 nano | 0/15 | 0/15 | 1.00 | 5/25 | 3/25 |
| **All** | **96/120** | **94/120** | | | |

"Outcome" counts the five non-trigger lines (three greeting-content checks, one
file-exists check, one judge line) across the 5 samples.

- **The stub and the skill still fire at the same rate.** No model shows a
  significant difference, and the misses point both ways (Llama: stub ahead;
  GPT-3.5: skill ahead). Weak models do sit at intermediate rates (Claude 3 Haiku
  63%, GPT-3.5 87%), which is where a trigger line pooled into both arms adds the
  most noise and the least signal.
- **This is the "doesn't load" case, and the stub doesn't help.** GPT-4.1 nano under
  Codex never fired either skill (0/15 and 0/15). Pooled, its trigger line fails in
  both arms, adding nothing to the difference. The trial arm's `0/15` on its own
  trigger line is what shows it, and `if:` already reports that per arm.
- **Firing isn't following.** GPT-4o mini fired 15/15 and passed 6/25 outcome lines,
  roughly its no-content stub rate. Only the outcome lines tell a skill that works
  apart from one that is merely loaded. That's the lift a pooled trigger line would
  water down.

## Where a placebo arm is still useful

As a third arm, with `if:` kept for the trigger line. Trial vs placebo isolates what
the skill's content adds beyond its listing, its context cost, and the turn spent
invoking it. Trial vs baseline stays the total effect. That needs no framework change:
another arm plus a `setup.sh` branch, as here. It pays off on a suite whose
baseline scores well above zero.
