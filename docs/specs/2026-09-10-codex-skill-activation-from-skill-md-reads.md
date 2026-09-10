**TL;DR** — Count a Codex `command_execution` that reads `<skills dir>/<name>/SKILL.md` as a dispatch of `<name>`, so `Skill X invoked` grades true on Codex arms the way it already does on Claude Code and OpenCode.

## Problem

- **Symptom:** every `Skill X invoked` assertion fails on a Codex arm with `` `X` not among fired [] ``, while the same cell passes its content assertions. In the in-repo suite, `hello/greets-by-name` on `trial-codex` in `e2e-openrouter` scores 67%: the greeting file and the judged warmth line pass, `Skill `hello` invoked` (`evals/e2e/hello/evals/hello/greets-by-name.eval.md:11`) fails. A five-eval suite for the `core-data-model` skill in `MLH/skills` reproduced it on every Codex cell (90% each, the activation line the only miss).
- **Symptom:** Codex 0.154.0 loads a skill by running a shell command. Its `session.jsonl` carries `item.started` / `item.completed` events of type `command_execution` with `command` = `/bin/bash -lc 'cat /home/benchspec/skills/core-data-model/SKILL.md'`, followed by further `cat` calls on the skill's reference files. No `skill_invocation` item and no `tool_call` item named `Skill` ever appears.
- **Symptom:** the adapter only recognises those two shapes. `_skill_dispatch_name` (`src/benchspec/agents/codex.py:530`) returns a name for `skill_invocation` and `tool_call` items and `None` for everything else, so `_item_dispatches_skill` (`:547`), `detect_fired` (`:346`), and the `fired` flag in `parse_codex_jsonl` (`:693`) never fire. `_codex_trajectory` (`:634`) records the read as a `tool_call` named `command_execution`, which `skills_dispatched` (`src/benchspec/grading/trajectory.py:124`) does not treat as a dispatch, so `fired_skills` in `execution.py:494` is empty and `_skill_invoked` (`src/benchspec/grading/checkers.py:212`) grades false.
- **Why it stayed hidden:** `make e2e` reports a matrix and exits `0` on a graded run; nothing asserts that `trial-codex` reaches 100%, so a 67% Codex column reads as "Codex is a bit worse" rather than as a checker false negative. The unit tests (`tests/agents/test_codex.py:360-413`) cover only the two shapes the adapter already handles.
- **Scope:** Codex activation detection. Claude Code emits a `Skill` tool_use and OpenCode a `skill` tool part; both grade correctly today.
- **Constraint:** activation is symmetric across arms. A baseline arm with no skill installed must keep failing the positive form, so the match must key on the skill's own path under the skills home, not on any `cat`.
- **Constraint:** no false positives. A command that reads a different skill, a file next to `SKILL.md`, or a `SKILL.md` outside the skills home is not a dispatch of `<name>`.

## Solution

```python
# src/benchspec/agents/codex.py
_SKILL_MD_READ_RE = re.compile(r"(?:/home/benchspec/skills|/root/\.codex/skills)/([^/\s'\"]+)/SKILL\.md\b")

def _skill_dispatch_name(item: dict) -> str | None:
    ...
    if item_type == "command_execution":
        match = _SKILL_MD_READ_RE.search(str(item.get("command") or ""))
        return match.group(1) if match else None
```

A `command_execution` whose command names `<skills home>/<name>/SKILL.md` dispatches `<name>`; `detect_fired`, `detect_dispatch`, the `fired` flag, and the trajectory all inherit it through the one helper.

## User Stories

1. As an eval author, I want **`Skill X invoked` to grade the same on Codex as on the other harnesses**, so a cross-harness matrix measures the skill and not the adapter.
2. As a benchmark reader, I want **a Codex activation miss to mean the agent did not open the skill**, so the routing finding the docs promise is real.
3. As a maintainer, I want **the in-repo suite to catch this class of gap**, so a harness changing how it loads skills shows up as red, not as a lower column.

## Implementation Decisions

```
session.jsonl ──► iter_events ──► _event_item ──► _skill_dispatch_name
                                                    ├─ skill_invocation.name
                                                    ├─ tool_call[name=Skill].arguments.skill
                                                    └─ command_execution.command ~ <skills home>/<name>/SKILL.md   (new)
                 ──► _item_dispatches_skill ──► parse_codex_jsonl.fired / detect_fired
                 ──► _codex_trajectory ──► {"name": "Skill", "arguments": {"skill": <name>}}
                 ──► skills_dispatched ──► GradeContext.fired_skills ──► _skill_invoked
```

- **One helper, every consumer.** The match lives in `_skill_dispatch_name` (`codex.py:530`) so `_item_dispatches_skill`, `_item_dispatches_any_skill`, `detect_dispatch`, `detect_fired`, and `parse_codex_jsonl` agree without a second detector.
  - The trajectory branch for `command_execution` (`codex.py:634`) emits a `Skill` tool_call with `arguments.skill` when the helper returns a name, and the plain `command_execution` record otherwise, so `skills_dispatched` (`trajectory.py:124`) needs no change.
- **Match on path, not on verb.** The pattern anchors on `FIXED_SKILLS_HOME` (`src/benchspec/agents/base.py:44`) and `skill_load_dir` (`codex.py:164`), both of which point at the same directory in the guest, and on the literal `SKILL.md` leaf. `cat`, `sed`, `head`, a here-string, or a plain path read all match; a reference file (`SPONSORS.md`) or a `SKILL.md` elsewhere does not.
  - Namespaced names keep flowing through `_skill_name_matches` (`codex.py:525`).
- **`detect_dispatch` stays early-stop safe.** `_item_dispatches_any_skill` (`codex.py:553`) already returns true for any name from the helper, so a `command_execution` read of some other skill still counts as "a skill dispatched" for the any-skill probe, matching the Claude Code semantics.
- **Docs say how Codex loads skills.** `docs/harnesses.md:47` gains a note that Codex reads `SKILL.md` through the shell and that benchspec counts that read as the dispatch.

## Testing Plan

### Logic
- **A SKILL.md read is a dispatch** — a `command_execution` item whose command names `<skills home>/<name>/SKILL.md` resolves to `<name>` for both the fixed home and the Codex load dir, bare and namespaced.
- **Neighbours are not dispatches** — a read of a sibling reference file, a `SKILL.md` outside the skills home, or an unrelated command resolves to no name.
- **Every consumer agrees** — `detect_fired`, `detect_dispatch`, the parsed `fired` flag, and the trajectory's `Skill` entry all report the read; a stream with no read still reports nothing.
- **Existing shapes are unchanged** — `skill_invocation` and `tool_call[name=Skill]` items resolve exactly as today.

### Behavior
- **The e2e Codex cell reaches 100%** — `hello/greets-by-name` on `trial-codex` in `e2e-openrouter` fails `Skill `hello` invoked` before the change and passes it after; the other two lines are unaffected.
- **The baseline stays honest** — a Codex arm with no skill installed still fails the positive activation line and passes `Skill `hello` not invoked`.

### Interface
- N/A — no CLI, config, or eval-format surface changes; the fix is internal to the Codex adapter.

## Documentation Plan

- **`docs/harnesses.md:47`**: note that Codex loads a skill by reading its `SKILL.md` and that benchspec grades that read as the dispatch.
- **`docs/writing-evals.md`** (activation paragraph): mention that "invoked" means the harness's native dispatch or, for Codex, a read of the skill's `SKILL.md`.

## Out of Scope

- Detecting a skill Codex used only from its system-prompt summary without opening `SKILL.md`; that is not observable in the stream.
- A `--fail-under`-style gate on `make e2e`; the red-then-green cell is checked by reading the matrix, and a gate is a separate decision.
- Any change to Claude Code or OpenCode detection.

## References

- `src/benchspec/agents/codex.py:346,335,525,530,547,553,634,693` — `detect_fired`, `detect_dispatch`, `_skill_name_matches`, `_skill_dispatch_name`, the two dispatch predicates, the trajectory branch, and the `fired` flag.
- `src/benchspec/agents/base.py:44` and `codex.py:164` — `FIXED_SKILLS_HOME` and the Codex `skill_load_dir`.
- `src/benchspec/grading/trajectory.py:124`, `src/benchspec/orchestration/execution.py:494`, `src/benchspec/grading/checkers.py:212` — `skills_dispatched`, `fired_skills`, `_skill_invoked`.
- `evals/e2e/hello/evals/hello/greets-by-name.eval.md:11` and `pyproject.toml` set `e2e-openrouter` — the cell that reproduces the miss at 67%.
- `tests/agents/test_codex.py:360-413` — the existing detection tests the new cases sit beside.
- A Codex 0.154.0 `session.jsonl` from a `core-data-model` cell: two `command_execution` items reading `/home/benchspec/skills/core-data-model/SKILL.md` and `SPONSORS.md`, no `skill_invocation` item.

## Verification

- `make test` — proves the helper resolves SKILL.md reads for both directories, rejects neighbours, and leaves the existing shapes byte-identical.
- `make lint` — proves style and types.
- `OPENROUTER_API_KEY=... uv run benchspec run --set e2e-openrouter --judge-provider openrouter --judge-model google/gemini-3.5-flash --binder-provider openrouter -- -k "greets-by-name and trial-codex"` — the `trial-codex` cell reads 67% before the change and 100% after, with `grading.json` showing `Skill `hello` invoked` passed on evidence naming the `SKILL.md` read.
