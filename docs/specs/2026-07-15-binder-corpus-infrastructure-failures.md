**TL;DR** — Make exhausted Gemini retries fail the binder corpus visibly, cap corpus calls at 15 seconds, and stop after three failures so broken infrastructure cannot masquerade as a slow green run.

## Problem

- **Symptom:** `make evals` can appear hung at the end, then report Gemini infrastructure failures as pytest skips (`s`) while the overall run remains green.
- **Why it stayed hidden:** failed calls produce no `GeminiReply`, so their elapsed time and error are omitted from the JSONL records and latency summary; only successful-call latency is reported.
- **Exposed by:** a 950-item run finished with 934 passes and 16 skips after 11 minutes; every skipped draw exhausted two attempts, and all 16 accumulated on one xdist worker.
- **Scope:** the live binder-corpus harness in `evals/binder/` and its `make evals` entrypoint.
- **Constraint:** production binding keeps its existing 60-second request timeout and failure behavior; the shorter timeout and circuit breaker apply only to the corpus quality eval.

## Solution

```make
BINDER_TIMEOUT_SECONDS ?= 15
BINDER_MAX_FAILURES ?= 3

evals:
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) \
		--maxfail=$(BINDER_MAX_FAILURES) evals/binder $(EVAL_ARGS)
```

The corpus caps each Gemini attempt at 15 seconds, retries once, then fails the affected pytest item with the recorded infrastructure error. Pytest stops the distributed run after three failed items, bounding the unhealthy-service tail while preserving diagnostics from already-running draws.

## User Stories

1. As an eval author, I want **infrastructure exhaustion reported as failure**, so a corpus run cannot look green when it failed to measure part of the corpus.
2. As an eval operator, I want **a bounded unhealthy-service tail**, so an unavailable Gemini endpoint does not consume minutes per draw across the remaining corpus.
3. As a maintainer, I want **failed-attempt diagnostics in the result artifacts**, so the terminal summary explains time spent outside the successful latency distribution.

## Implementation Decisions

```
make evals ──► pytest --maxfail=3 ──► _bind_resilient ──► _recording_call_model
                                             │                     │
                                             │                     └─► binder._call_gemini(timeout=15)
                                             └─► record(error + elapsed_ms) ──► pytest failure
```

- **Infrastructure exhaustion is a failed measurement, not a skip.** `_bind_resilient` retains one retry for transient `RuntimeError`s and returns the final error information after the second failed attempt; each corpus test records that result and fails with the underlying exception type and message.
  - `BinderAuthError` continues to bypass the retry taxonomy and fail immediately, preserving the credential fail-fast contract.
  - Assertion-quality failures remain distinct from transport failures in pytest output and corpus records.
- **The 15-second cap is corpus-only.** `_recording_call_model` substitutes a named corpus timeout when delegating to `binder._call_gemini`; `bind()` and production callers keep the 60-second timeout at `src/evalspec/grading/binder.py:370`.
  - One unhealthy draw is bounded to approximately 30 seconds plus local overhead.
  - The timeout is a suite constant, not a new public configuration surface.
- **Three failed items form the circuit breaker.** `make evals` passes `--maxfail=3`; xdist may finish calls already in flight but stops scheduling new work once pytest observes the threshold.
  - The threshold counts exhausted draws, not individual attempts.
  - `EVAL_ARGS` remains available for collection and focused runs; callers can explicitly override pytest behavior when diagnosing.
- **Failure records carry their own cost.** Per-draw JSONL records include elapsed time for all attempts plus the final exception type and message when exhausted. Successful Gemini latency and token aggregates remain based on replies, while the terminal summary reports infrastructure-failure count and elapsed time separately.
- **The percentage-based green-run guard becomes redundant.** Once every exhausted draw is a pytest failure and `--maxfail=3` bounds the run, the session-finish rule that waits for a 5% infrastructure-error rate no longer decides success and should be removed rather than maintain two competing failure policies.

## Testing Plan

### Logic
- **Retry exhaustion retains the final cause** — two transient infrastructure errors produce a failed-measurement result containing two attempts, the final exception details, and total elapsed time.
- **Successful retry remains measurable** — one transient error followed by a valid reply succeeds and reports both attempts without tripping failure handling.
- **Corpus timeout is isolated** — live-corpus delegation uses 15 seconds while production binding continues to request 60 seconds.

### Behavior
- **Infrastructure errors are visibly red** — an exhausted live-corpus draw appears as a pytest failure with a useful Gemini transport diagnostic, never as a skip.
- **The unhealthy run is bounded** — pytest stops scheduling corpus work after three exhausted draws, subject only to xdist work already in flight.
- **Summary separates success latency from failure time** — terminal output does not imply that successful-response latency accounts for time lost to failed attempts.

### Interface
- **`make evals` remains the operator entrypoint** — it keeps worker and passthrough controls while applying the three-failure circuit breaker by default.
- **Production binder consumers are unchanged** — `bind()` retains its callable contract, timeout, and exception taxonomy outside the corpus harness.

## Documentation Plan

- **Makefile**: update the binder-worker comment and `evals` help text to state the 15-second corpus timeout and three-failure stop policy.
- **README.md**: document that `make evals` is a paid live Gemini quality run and that exhausted infrastructure retries fail the run.

## Out of Scope

- Changing production binder timeout, retry, or degrade-to-judge behavior.
- Adding a general retry library, exponential backoff, or Gemini rate-limit coordinator.
- Making timeout or failure threshold part of evalspec's public CLI or `[tool.evalspec]` configuration.
- Guaranteeing immediate process cancellation for xdist calls already in flight when the failure threshold is reached.

## References

- `evals/binder/test_corpus.py:40-60` — current two-attempt retry loop and `_ERROR` sentinel.
- `evals/binder/test_corpus.py:84-145` — current record-then-skip behavior in both live corpus tests.
- `evals/binder/conftest.py:79-102` — corpus Gemini adapter and successful-reply-only recording.
- `evals/binder/conftest.py:145-195` — current aggregate summary and 5% infrastructure-error gate.
- `src/evalspec/grading/binder.py:201-256` — Gemini transport timeout and normalized failure taxonomy.
- `src/evalspec/grading/binder.py:356-371` — production `bind()` contract and 60-second call timeout.
- `Makefile:16-19` — current worker rationale and `make evals` pytest invocation.

## Verification

- `make test` — the unit and corpus-integrity suites pass with the new failure-result and diagnostic contracts.
- `make lint` — Ruff and house rules accept the implementation and documentation changes.
- `uv run pytest -p pytester evals/binder/test_corpus_integrity.py -q` — deterministic corpus harness coverage passes without live Gemini calls.
- `make evals EVAL_ARGS="--collect-only -q"` — the paid suite still collects through the default entrypoint and circuit-breaker invocation.
