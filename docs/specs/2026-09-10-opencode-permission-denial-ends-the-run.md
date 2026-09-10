**TL;DR** — Give the guest OpenCode a `permission: allow` config so a read outside `/workspace` no longer ends the run, and mark a run that ended on a rejected tool call as errored rather than scoring its empty workspace as failed.

## Problem

- **Symptom:** an OpenCode arm that touches any path outside `/workspace` produces nothing. In the `MLH/skills` `core-data-model` suite the Gemini Flash arm scored 10%, 8%, and 10% on three evals; each `session.jsonl` shows the skill invoked and its references read, then a `glob` on `/` answered with `The user rejected permission to use this specific tool call.`, a `step_finish` with `reason: "tool-calls"`, and no further events. `./answer.sql` was never written.
- **Symptom:** it is the harness, not the model. A one-eval reproduction whose prompt says "list the entries at `/` with your file-listing tool, then write ./done.txt" scores 0% on `openrouter/anthropic/claude-sonnet-5`: four events, a rejected `read` of `/`, then the stream ends 78 ms after it started. Claude Code and Codex arms run with permissions bypassed (`--permission-mode bypassPermissions` at `src/benchspec/agents/claude.py:294`, `--dangerously-bypass-approvals-and-sandbox` at `src/benchspec/agents/codex.py:329`); OpenCode gets no equivalent.
- **Symptom:** the guest config declares nothing about permissions. `_OPENCODE_CONFIG_JSON` (`src/benchspec/agents/opencode.py:221`) is `{"$schema": ..., "plugin": [...]}` and is baked into `/root/.config/opencode/opencode.json` by `provision_script` (`:258`). OpenCode's `permission` config defaults reads outside the project directory (`external_directory`) to `ask`, and `opencode run` cannot ask, so the call is rejected and the turn ends.
- **Symptom:** the cell is graded as a clean failure. `parse_opencode_jsonl` sets `is_error = total_tokens == 0` (`opencode.py:695`); the rejected run spent 10,673 tokens, so `errored` is `false` and the report scores every assertion against an empty workspace. `docs/results.md:83` promises "errored is not failed": a run the harness cut short is infrastructure, not a measurement.
- **Why it stayed hidden:** the in-repo `hello` suite never leaves `/workspace`, and OpenCode arms on Claude models rarely explore outside the working directory. Gemini Flash does, so the first cross-model run surfaced it.
- **Scope:** the OpenCode adapter's guest config and its run-outcome classification. The permission model of OpenCode itself is out of reach.
- **Constraint:** the sandbox is the isolation boundary. Every harness already runs with its own approvals bypassed inside the guest; OpenCode should match, not become the one arm whose model is scored on whether it stayed inside `/workspace`.
- **Constraint:** the fix must not change snapshots for the other harnesses. `opencode.json` is written by OpenCode's own `provision_script`, so only the OpenCode snapshot fingerprint moves.

## Solution

```python
# src/benchspec/agents/opencode.py
_OPENCODE_CONFIG_JSON = json.dumps(
    {
        "$schema": "https://opencode.ai/config.json",
        "plugin": ["/root/.config/opencode/plugins/benchspec-bootstrap"],
        "permission": "allow",
    },
    separators=_COMPACT_SEPARATORS,
)
```

Inside the guest every tool call is pre-approved, matching the other two harnesses. As a second guard, a run whose last tool call was rejected and that produced no final text is returned with `is_error=True`, so a future OpenCode default cannot silently score as a failed measurement.

## User Stories

1. As a benchmark maintainer, I want **an OpenCode arm judged on what it wrote, not on whether it peeked outside `/workspace`**, so a model column measures the model.
2. As a results reader, I want **a run the harness cut short to show as errored**, so a 0% cell is never mistaken for an agent that tried and failed.
3. As a harness integrator, I want **all three adapters to run under the same approval posture in the guest**, so a cross-harness matrix compares agents, not permission defaults.

## Implementation Decisions

```
provision_script ──► /root/.config/opencode/opencode.json  {"plugin": [...], "permission": "allow"}   (snapshot)
opencode run --format json ──► session.jsonl ──► parse_opencode_jsonl
                                                   ├─ tool_use part.state.status == "error"
                                                   │    and error names a rejected permission ──► last_rejected = True
                                                   └─ is_error = total_tokens == 0
                                                        or (last_rejected and no text_parts)    (new)
                                                 ──► RunResult(is_error) ──► run_acc.errored (execution.py:307) ──► grading.json errored
```

- **`permission: "allow"` at the top level.** OpenCode accepts a bare string for the whole tree, so one key covers `read`, `glob`, `grep`, `edit`, `bash`, `external_directory`, and any tool added later; a per-tool map would drift.
  - The plugin registration in the same file is untouched.
  - The config lands in the snapshot through `provision_script` (`opencode.py:241-260`), so the OpenCode snapshot rebuilds once; Claude Code and Codex snapshots are unchanged.
- **Rejection is classified in the parser, not in `execution.py`.** `parse_opencode_jsonl` (`opencode.py:649-720`) already walks every `tool_use` part; it records whether the final tool part carried `state.status == "error"` with an error text containing `rejected permission`, and folds that into `is_error` when no assistant text followed.
  - The existing `total_tokens == 0` rule stays; the new clause only widens the errored set.
  - `result_text` keeps `_debug_tail` so the rejection is readable in `transcript.json`.
- **The trajectory keeps the rejected call.** `_opencode_trajectory` (`opencode.py:611`) skips non-`completed` parts today; the rejected call is added with `"status": "rejected"` so the process facts show what the agent asked for.
- **The e2e suite gets an eval that leaves the workspace.** A new `evals/e2e/hello/evals/hello-outside/reads-outside-workspace.eval.md` asks the agent to list `/` with its file-listing tool before greeting Alice, and asserts `./Greetings/Alice.md` exists. It scores 0% on `trial-opencode` today and 100% after the config change; on Claude Code and Codex it passes both before and after.

## Testing Plan

### Logic
- **A rejected final tool call with no text marks the run errored** — a stream ending in a `tool_use` part whose error names a rejected permission, with tokens spent and no assistant text, yields `is_error=True`.
- **A rejection the agent recovered from is not an error** — a rejected call followed by assistant text or a completed write yields `is_error=False`.
- **Token-free runs still error** — the existing rule is unchanged.
- **The guest config carries the permission key** — the provisioning script writes `permission: "allow"` alongside the plugin registration.
- **Rejected calls appear in the trajectory** — with their tool name, input, and a `rejected` status.

### Behavior
- **The new e2e eval passes on OpenCode** — `hello-outside/reads-outside-workspace` on `trial-opencode` in `e2e-openrouter` fails before the change (0%, `./Greetings/Alice.md` absent) and passes after (100%).
- **Other harnesses are unaffected** — the same eval passes on `trial` (Claude Code) and `trial-codex` before and after.
- **An errored run is excluded from the rate** — with the permission fix reverted, the OpenCode cell shows `errored: true` in `grading.json` and the matrix cell shows `—`, not 0%.

### Interface
- N/A — no CLI, config key, or eval-format change; the guest `opencode.json` is benchspec-owned and not user-facing.

## Open Questions

- Should the second guard stay once the config fix lands? It only fires if OpenCode changes its defaults again or a user overrides the guest config. Keeping it costs one branch in the parser; dropping it leaves the class of failure undetectable. Default: keep it.

## Documentation Plan

- **`docs/harnesses.md`** (notes after the table): state that all three harnesses run with approvals bypassed inside the guest, and name the OpenCode `permission` config.
- **`docs/sandbox.md`**: one line in the isolation section that the sandbox, not harness approvals, is the boundary.
- **`docs/results.md:83`**: add "a harness that ended the turn on a rejected permission" to the errored examples.

## Out of Scope

- Retrying or re-prompting the agent after a rejection; the run is over once OpenCode ends the turn.
- Scoping permissions per path (allow `/`, deny `/etc`); the guest is disposable and the other harnesses already allow everything.
- Any change to how Claude Code or Codex handle permissions.

## References

- `src/benchspec/agents/opencode.py:221,241-260,378-410,435-486,611,649-720` — the guest config, `provision_script`, `build_command`, `invoke`, the trajectory, and `parse_opencode_jsonl`.
- `src/benchspec/agents/claude.py:294` and `src/benchspec/agents/codex.py:329` — the bypass flags the other harnesses run with.
- `src/benchspec/orchestration/execution.py:307` — where `RunResult.is_error` becomes the sample's `errored` flag.
- `docs/results.md:83` — the "errored is not failed" contract this restores.
- OpenCode permissions docs (`opencode.ai/docs/permissions`) — `permission` accepts a bare `allow`; `opencode run` blocks any rule that would ask.
- `MLH/skills` run artifacts (Gemini Flash, three cells) and the one-eval Sonnet reproduction: rejected `glob`/`read` of `/`, `step_finish` with `reason: "tool-calls"`, no further events.

## Verification

- `make test` — proves the parser marks a rejection-ended run errored, leaves recovered runs alone, and the provisioning script carries the permission key.
- `make lint` — proves style and types.
- `OPENROUTER_API_KEY=... uv run benchspec run --set e2e-openrouter --judge-provider openrouter --judge-model google/gemini-3.5-flash --binder-provider openrouter -- -k reads-outside-workspace` — the `trial-opencode` cell reads 0% before the change and 100% after; `trial` and `trial-codex` read 100% both times.
- `uv run benchspec sandbox:build --set e2e-openrouter` — the OpenCode snapshot rebuilds with the new config and the other two are cache hits.
