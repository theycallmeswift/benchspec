**TL;DR** — Parse the harness stream before giving up on a non-zero exit in the Codex and OpenCode adapters, the way the Claude Code adapter already does, so an errored sample still lands its `session.jsonl`, trajectory, and token counts instead of an empty transcript.

## Problem

- **Symptom:** an errored OpenCode sample carries no evidence. In the `MLH/skills` `dev-queries` suite, `early-user-activity` on the Gemini Flash arm errored twice in a row: `transcript.json` shows `"result": ""`, `is_error: true`, `tool_call_count: 0`, `timing.json` shows `duration_ms: 0` and `total_tokens: 0`, and no `session.jsonl` was written. Yet `workdir_tree` lists `answer.sql`, so the agent ran, called tools, and wrote its file before OpenCode exited non-zero.
- **Symptom:** the OpenCode adapter discards stdout on a non-zero exit. `invoke` (`src/benchspec/agents/opencode.py:490-491`) returns `RunResult(eval_id, config, res.stderr[-2000:], 0, 0, is_error=True)` without calling `parse_opencode_jsonl`; `raw` stays `""`, so `execution.py:551` writes no `session.jsonl`, and the trajectory, duration, and usage are lost. When stderr is empty, as it was here, the sample has no diagnostic at all.
- **Symptom:** the Codex adapter has the same shape. `codex.py:473-474` returns stderr and drops stdout on any non-zero exit.
- **Symptom:** the Claude Code adapter already does the right thing. `claude.py:431-436` parses the stream first and only swaps `result_text` for stderr when the parsed result is an error and stderr is non-empty, keeping `raw` and the trajectory. The other two adapters never received the same treatment.
- **Why it stayed hidden:** the unit tests for the three adapters (`tests/agents/test_opencode.py:749`, `test_codex.py:896`, `test_claude.py:414`) all assert only that a non-zero exit yields `is_error=True`; none asserts that the stream survives. And a non-zero harness exit with a meaningful stdout is rare on Claude models, which is where most runs happen.
- **Scope:** the non-zero-exit branch of `invoke` in the Codex and OpenCode adapters, and their tests. Sandbox-boundary failures (`TimeoutError`, `SandboxError`) stay as they are: there is no stream to keep.
- **Constraint:** `is_error` semantics do not change. A non-zero exit still marks the sample errored so the report excludes it; the change is only what the errored sample records.
- **Constraint:** stderr stays the headline when it has content. A crash message on stderr is more useful than a debug tail; the stream is kept beside it, not instead of it.

## Solution

```python
# src/benchspec/agents/opencode.py — invoke, replacing the exit_code != 0 early return
result = parse_opencode_jsonl(res.stdout, eval_id, config, detect_skill)
if res.exit_code != 0:
    text = res.stderr[-2000:].strip() or result.result_text
    return replace(result, result_text=text, is_error=True)
return result
```

The same shape lands in `codex.py` around `parse_codex_jsonl`. A non-zero exit is still an error; the parsed stream, trajectory, tokens, and duration ride along, and `session.jsonl` is written whenever the harness produced output.

## User Stories

1. As a benchmark maintainer, I want **an errored sample to keep its stream**, so I can read what the agent did before the harness died instead of inferring it from the workspace.
2. As a results reader, I want **`timing.json` and the token counts on an errored sample to be real**, so a crash late in a long run is not indistinguishable from a crash at startup.
3. As a harness integrator, I want **all three adapters to treat a non-zero exit the same way**, so the artifact contract in `docs/results.md` holds regardless of harness.

## Implementation Decisions

```
GuestSandbox.exec ──► res(exit_code, stdout, stderr)
      │
      ├─ sandbox exception ──► RunResult("<sandbox-error> …", is_error=True)        (unchanged)
      │
      └─ parse_opencode_jsonl / parse_codex_jsonl(res.stdout) ──► result(raw, trajectory, tokens)
               │
               ├─ exit_code == 0 ──► result                                          (unchanged)
               └─ exit_code != 0 ──► replace(result, result_text=stderr or result.result_text, is_error=True)
                                            │
                                            ▼
                     execution.py:307 run_acc.errored ── execution.py:318 run_acc.raw ── :551 session.jsonl
```

- **Parse first, then decide.** Mirror `claude.py:431-436`: the stream is parsed unconditionally, and the non-zero branch only overrides `result_text` and forces `is_error`. `parse_opencode_jsonl` and `parse_codex_jsonl` already tolerate an empty or truncated stdout, so parsing a crashed run is safe.
  - Forcing `is_error=True` on the non-zero branch keeps today's contract even when the parser would have called the run healthy (tokens present, text present).
  - `result_text` prefers stderr when it is non-blank; otherwise the parser's own text or `_debug_tail` (`opencode.py:673`) stands in, so the transcript is never empty when there was a stream.
- **`raw` and the trajectory flow through untouched.** `execution.py:318` copies `result.raw` into the arm run and `:551` writes `session.jsonl` when it is non-empty; no orchestration change is needed.
- **Codex gets the identical shape.** `codex.py:473-474` becomes a parse-then-replace; `parse_codex_jsonl` already handles a stream that ends before `turn.completed` (`codex.py:660`).
- **The three tests learn what they were missing.** Each `test_invoke_nonzero_exit_is_error` feeds a `FakeExecOutput` with a non-zero exit and a real stream on stdout, and asserts `is_error`, that `raw` equals the stdout, that the trajectory is non-empty, and that `result_text` is stderr when present and the parsed text otherwise.

## Testing Plan

### Logic
- **A non-zero exit with a stream keeps the stream** — `raw`, `trajectory`, `total_tokens`, and `duration_ms` on the returned result equal what parsing the stdout alone would give, and `is_error` is true.
- **stderr wins the headline when present** — `result_text` is the stderr tail when stderr is non-blank, and the parser's text or debug tail otherwise.
- **A sandbox-boundary failure is unchanged** — a timeout or `SandboxError` still yields the `<sandbox-error>` result with no stream.
- **A zero exit is unchanged** — the parsed result is returned as today, byte for byte.
- **All three adapters agree** — the same inputs produce the same shape of result from Claude Code, Codex, and OpenCode.

### Behavior
- **An errored sample lands its artifacts** — a cell whose harness exits non-zero after producing output writes `session.jsonl`, a `transcript.json` with non-zero `tool_call_count`, and a `timing.json` with real duration and tokens, and the matrix still shows `—` for it.

### Interface
- N/A — no CLI, config, or eval-format change; `RunResult` gains no fields and `is_error` keeps its meaning.

## Documentation Plan

- **`docs/results.md:30,181`**: the `session.jsonl` note reads "only when the harness produced output", which becomes true for errored samples too; add one sentence that an errored sample keeps its stream and that `result_text` carries stderr when the harness wrote any.

## Out of Scope

- Diagnosing why OpenCode exited non-zero on the Gemini Flash cell; with the stream kept, the next occurrence explains itself.
- Retrying an errored cell; `errored` stays excluded from rates and surfaced in the report.
- Changing what counts as `is_error` for a zero-exit run (`total_tokens == 0`, a rejection-ended turn); those rules are untouched.

## References

- `src/benchspec/agents/opencode.py:476-491,673,682` — `invoke`'s sandbox-error and non-zero branches, `_debug_tail`, `parse_opencode_jsonl`.
- `src/benchspec/agents/codex.py:460-474,660` — the same branches and `parse_codex_jsonl`.
- `src/benchspec/agents/claude.py:404-437` — the parse-then-fallback pattern to copy.
- `src/benchspec/orchestration/results.py:43-44` and `execution.py:307,318,548-553` — the `raw` field, where it is copied into the arm run, and the `session.jsonl` writer.
- `tests/agents/test_opencode.py:749`, `test_codex.py:896`, `test_claude.py:414` — the non-zero-exit tests that assert only `is_error`.
- `docs/results.md:30,176-181` — the artifact contract.
- `MLH/skills` run artifacts `iteration_11` and `iteration_12`, `dev-queries/eval-early-user-activity/gemini-flash/sample-0`: `is_error: true`, empty result, zero tokens, no `session.jsonl`, `answer.sql` present in the workspace.

## Verification

- `make test` — proves the three adapters keep the stream on a non-zero exit, prefer stderr for the headline, and leave zero-exit and sandbox-error paths unchanged.
- `make lint` — proves style and types.
- `uv run benchspec run --set e2e-openrouter --judge-provider openrouter --judge-model google/gemini-3.5-flash --binder-provider openrouter -- -k trial-opencode` with `harness_args` pointing OpenCode at a model slug that fails after the first tool call — the cell shows `—` in the matrix and its run directory contains `session.jsonl` and a `timing.json` with non-zero tokens.
