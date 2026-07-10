**TL;DR** — Retire the parallel `trigger-evals.md` subsystem, make `Skill X invoked` bind to the `skill_invoked` checker deterministically offline (no Gemini call), key "which skill dispatched" off the assertions instead of the group-folder name, and ship a minimal `evalspec analyze` that classifies each assertion before a paid run — Phase 4 of the #1 roadmap.

## Problem

- **Symptom:** Skill activation lives in a separate schema and code path — `trigger-evals.md` files parsed by `mdformat.parse_trigger` (`mdformat.py:280-302`), validated as `evalspec-trigger/v1` (`schema.py:236-309`), discovered via a second knob set (`resolve_eval_roots` / `_skill_dirs` / `_DEFAULT_EVAL_ROOTS = ["skills", ".claude/skills"]`, `discovery.py:100,131-145,230-254,348-368`), and run by a dedicated `test_trigger` (`cases.py:168-246`). The roadmap folds activation into ordinary `## Assertions`, making this whole axis redundant.
- **Symptom:** Activation *is* already assertable as `` - Skill `X` invoked `` (the `skill_invoked` checker exists, `checkers.py:203-211`), but the prose→checker routing for it runs through the paid Gemini binder — `bind()` only fast-paths file-existence locally (`_bind_bare_exists`, `binder.py:343-359`); everything else, including `Skill X invoked`, is a Gemini call (`binder.py:328-331`). There is no offline recognizer, so a pre-run classifier cannot see it for free.
- **Symptom:** "Which skill an eval targets" is inferred only from the group-folder name — `EvalCase.skill` is a property returning `self.group` (`discovery.py:32-34`), and the trajectory scan passes it as the namespaced-fallback hint: `skills_dispatched(arm_run.trajectory, eval_case.skill)` (`execution.py:353`). Once groups are arbitrary folder names (Phase 3), that hint stops naming a skill.
- **Symptom:** `evalspec analyze` does not exist — `__main__.py` registers only `lint` (`__main__.py:16-27`); `grep -rni analyze src/` is empty. The roadmap requires analyze to classify process-fact assertions before a run.
- **Exposed by:** Roadmap #1 Phase 4 Done-When: `Skill X invoked` inside ordinary `## Assertions`; `trigger-evals.md` no longer required for new suites; `evalspec analyze` classifies process-fact assertions before a run.
- **Scope:** Retire the trigger subsystem; add offline process-fact binding + classification; decouple the dispatched-skill identity from the group name. Not: the full CLI surface (`run`, `sandbox:build`, shared arg/exit-code conventions) — that is Phase 5.
- **Constraint:** No compatibility shims (roadmap rule). The `evalspec-trigger/v1` schema, `trigger-evals.md` discovery, `parse_trigger`, and `test_trigger` are removed outright.
- **Note:** No authored trigger evals exist to migrate — the repo has zero `trigger-evals.md` files and no `skills/`/`.claude/skills/` tree. The subsystem lives only as code, the trigger test fixtures that construct `evalspec-trigger/v1` docs (`tests/test_plugin.py`, `test_discovery.py`, `test_mdformat.py`), and README/docs prose. Phase 4 deletes the machinery and its fixtures; there is no eval-content restructuring.

## Solution

```markdown
## Assertions
- [ ] Skill `ingest` invoked          # process-fact: binds offline to skill_invoked
- [ ] A file exists at `./notes.md`   # deterministic: existing _bind_bare_exists path
- [ ] The summary is accurate         # semantic: punts to the judge
```

```text
$ evalspec analyze .
skills/ingest-activation/capture.eval.md
  process-fact  Skill `ingest` invoked
  deterministic A file exists at ./notes.md
  semantic      The summary is accurate
```

Activation becomes one row among the eval's ordinary assertions — recognized locally so `analyze` (and the grading fast path) classify it with no Gemini call — and the `trigger-evals.md` file/schema/runner are deleted.

## User Stories

1. As an eval author, I want **to write `` Skill `X` invoked `` in `## Assertions`**, so activation is one assertion among the rest instead of a separate `trigger-evals.md` with its own schema.
2. As an eval author, I want **`evalspec analyze` to label each assertion process-fact / deterministic / semantic before I pay for a run**, so I catch a mis-phrased activation check without spending on a harness pass.
3. As a maintainer, I want **one discovery/parse/schema path**, so activation and output evals stop drifting across two knob sets and two Markdown grammars.

## Implementation Decisions

```text
eval.md `## Assertions`
   │
   ▼
binder.bind(text) ──► _bind_bare_exists                      (file-exists, offline)
                 └──► _bind_skill_invoked  ← NEW recognizer  (Skill `X` invoked, offline)
                 └──► Gemini _BINDING_PROMPT                  (everything else, paid)
   │
   ▼ deterministic spec {"checker":"skill_invoked","skill":"X"}
checkers._skill_invoked(spec, …, context.fired_skills)
   ▲
   └─ fired_skills = skills_dispatched(trajectory, hints=asserted_skill_names)   (execution.py)

evalspec analyze  ──►  binder.classify(assertion)  ──►  {process-fact | deterministic | semantic}
   (new __main__ subcommand, mirrors lint.run / lint_repo shape)
```

- **Delete the trigger subsystem, not deprecate it.** Remove `discover_trigger_cases` + `_skill_dirs` + `resolve_eval_roots` + `_DEFAULT_EVAL_ROOTS` + `_pyproject_eval_roots` (`discovery.py:100,121-145,230-254,348-368`), `TriggerCase` (`discovery.py:69-86`), `parse_trigger` / `_parse_trigger_section` / trigger regexes (`mdformat.py:35-38,232-302`), `_validate_trigger_v1` + dispatch + `_TRIGGER_V1` (`schema.py:18,236-309,312-324`), the `trigger_query` param branch + trigger CLI options + `test_trigger` (`plugin.py:105-136,686-706`; `cases.py:72-88,168-246`), and the trigger scorer surface no longer reached (`trigger.py`). Update the package docstring (`__init__.py:5-6`).
  - `--evalspec-eval-roots` and `[tool.evalspec] eval_roots` go away with it; the single remaining discovery knob is `eval_paths` (Phase 3).
- **Add an offline `skill_invoked` recognizer beside `_bind_bare_exists`.** A local matcher on the canonical `` Skill `X` invoked `` shape in `bind()` (`binder.py:328`) returns `{"checker":"skill_invoked","skill":"X","type":"deterministic"}` without a Gemini call. The Gemini prompt keeps teaching the primitive (`binder.py:60-62,123-124`) as a fallback for looser phrasings, but the canonical shape never pays.
- **Key the dispatched-skill hint off the assertions, not the group.** Collect target skill names from the eval's `skill_invoked` assertions and pass them as the `skills_dispatched` fallback hints (replacing `eval_case.skill` at `execution.py:353`). `EvalCase.skill`'s remaining role is the artifact-path slot (`discovery.py:32-34`); it no longer implies the skill under test.
- **Ship a minimal `evalspec analyze`.** A new `__main__` subcommand mirroring `lint.run` / `lint_repo` (`lint.py:69-90`): walk `discover_eval_cases`, classify each assertion offline (bare-exists / process-fact / else semantic), print the label table, exit nonzero on an unclassifiable-but-malformed assertion. Full CLI conventions (shared args, `run`/`sandbox:build`) are Phase 5; this ships only the classifier + a thin command so the Done-When is verifiable.

## Testing Plan

### Logic
- **`Skill X invoked` binds offline** — the canonical shape resolves to the `skill_invoked` spec with no Gemini call; a looser phrasing still falls through to the (mocked) binder.
- **Dispatch hints come from assertions** — an eval whose group name differs from its asserted skill still detects the skill firing; `fired_skills` reflects the asserted names, not the group.
- **`skill_invoked` grading is unchanged** — exact and namespaced (`plugin:ingest`) matches still pass on a firing arm and fail on a non-firing arm.
- **Trigger schema is gone** — parsing a former `trigger-evals.md` / `evalspec-trigger/v1` doc raises, not silently accepts.

### Behavior
- **`evalspec analyze .` classifies a real suite** — each assertion in a discovered eval is labeled process-fact / deterministic / semantic offline, and the command exits nonzero on a malformed assertion.
- **Activation grades end-to-end from `## Assertions`** — an eval with `` Skill `X` invoked `` (no `trigger-evals.md`) runs through discovery→grading and records the activation result on both arms.

### Interface
- **`analyze` exit codes** — clean suite exits 0, malformed assertion exits nonzero with a readable diagnostic (mirrors `lint`).
- **Discovery knobs** — only `eval_paths` remains; `--evalspec-eval-roots` / `eval_roots` are rejected/absent, and no discovery path reads `trigger-evals.md`.

## Open Questions

- Should `evalspec analyze` live fully in Phase 5 instead, with Phase 4 shipping only `binder.classify()` as a library? Recommendation: ship the thin command now so the Phase 4 Done-When is independently verifiable; Phase 5 absorbs it into the unified CLI surface. Resolved if Phase 5 is sequenced immediately after — then defer the command.

## Documentation Plan

- **`docs/schema.md`**: remove the `trigger-evals.md` / `evalspec-trigger/v1` grammar; document `` Skill `X` invoked `` as an ordinary assertion.
- **`docs/configuration.md`**: drop `eval_roots` / `--evalspec-eval-roots`; `eval_paths` is the sole discovery knob.
- **`docs/agents.md`**: update trigger-routing notes to the process-fact model.
- **`docs/concepts.md`**, **`src/evalspec/__init__.py`**: strike the "each skill's `evals/trigger-evals.md`" language.

## Out of Scope

- The full CLI surface — `evalspec run`, `evalspec sandbox:build`, and shared arg/exit-code conventions are Phase 5.
- New process-fact assertion shapes beyond `Skill X invoked` (e.g. tool-call counts) — added only when a concrete observable justifies one.
- Multi-turn trajectory replay and any change to how trajectories are captured (`trajectory.py`).

## References

- #1 — roadmap umbrella; Phase 4 Done-When and dependency graph (3 → 4 → 9).
- #23 / `docs/specs/2026-07-09-markdown-eval-format-and-discovery.md` — Phase 3; its handoff flagged the `detect_skill`/`group` coupling and the `eval_roots` retirement this phase completes.
- `src/evalspec/binder.py:328,343-359,60-62` — the bind fork where the offline recognizer lands.
- `src/evalspec/execution.py:310,349-354` — `detect_skill`/`fired_skills` wiring to re-key off assertions.
- `src/evalspec/checkers.py:203-211,23-31` — the `skill_invoked` checker + `GradeContext.fired_skills` (kept, becomes primary).
- `src/evalspec/trajectory.py:113-139` — `skills_dispatched`, the observed-dispatch source.
- `src/evalspec/lint.py:69-90` — the shape `analyze` mirrors.

## Verification

- `make test` — proves grading, offline binding, discovery, and the removed trigger path all pass.
- `make lint` — proves the package and docs satisfy lint after the trigger deletion.
- `evalspec analyze .` — proves each assertion in the in-repo evals is classified process-fact / deterministic / semantic before any paid run.
- `evalspec lint .` — proves no in-repo suite still references `trigger-evals.md` or `eval_roots`.
