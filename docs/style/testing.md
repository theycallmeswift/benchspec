# Testing

Apply this guide after [development.md](development.md).

## Behavior first

Test the flows users care about, not how the code is split. E2e and
integration tests carry most of the coverage, and the e2e suite is the
contract. Cover what's essential, not every line. A slower e2e suite is an
acceptable trade.

| Tier | Proves | Lives in |
| --- | --- | --- |
| E2e | A user's flow works against the real thing | `test/e2e/` at the root |
| Integration | The same flow works fast, edges stubbed, localhost only | `test/integration/` at the root |
| Unit | Pure logic handles its many inputs | Each package's `test/unit/` |

- **E2e tests script the flow like a user:** run the real entry point with
  real input and assert on what the user sees.
- **Unit tests cover only pure logic with many inputs,** such as parsers and
  validators, as input/output tables with no mocking. Don't unit test wiring.
- **The default test gate needs no credentials.** E2e tests that spend money
  or touch real accounts run on their own target.
- **A test's path mirrors the file or flow it covers,** under its tier.

## What a change needs

- **Every fix comes with a test that fails without it.** Revert the fix and
  watch it go red before trusting it.
- **A new or changed user flow updates its e2e test.**

## What to fake

- **Don't mock your own code.** Fake only painful edges you don't own: paid or
  remote services and time.
- **Fakes implement the real interface** and record what they were asked, so
  tests assert on requests, not implementation details.
- **Control the clock for durations;** never wait in a test.
- **Build test data with factories:** a function with a default for every
  field, so a call names only what the test is about. Dates are relative to a
  given today.

## How a test reads

- **DRY the plumbing, DAMP the story.** Extract builders and assertion
  helpers; keep the triggering input and asserted value inline, so each test
  reads top to bottom on its own.
- **Four phases:** setup, exercise, verify, teardown, separated by blank
  lines. One exercise per test.
- **Name the behavior,** in plain words, with no requirement ID.
- **Skip with a guard and a reason** as the test's first line, so the reason
  shows in the report.

## In this repository

| Tier | Lives in | Runs |
| --- | --- | --- |
| E2e | `evals/e2e/hello/`, the in-repo suite, and `tests/test_e2e_live.py`, which checks its artifacts | `make e2e`; spends model credit |
| Integration | `tests/cli/`, which runs the real `python -m benchspec`, and the `tests/test_*.py` files at the root | `make test` |
| Unit | `tests/<package>/`, mirroring `src/benchspec/<package>/` | `make test` |

- **`make test` deselects the `e2e` and `binder_corpus` markers,** so it needs
  no credentials. `make evals` runs the binder corpus in `evals/binder/`,
  which measures binder quality, not framework function, and spends model
  credit.
- **The painful edges here** are the network, time, paid model APIs, and the
  sandbox.
- `tests/support/` holds shared helpers and `tests/fixtures/` test data;
  neither is a tier.
