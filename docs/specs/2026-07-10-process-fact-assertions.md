**TL;DR** — Retire the `trigger-evals.md` *eval* subsystem (keeping the skill-detection primitives the normal run and sandbox routing depend on), grade activation as an ordinary assertion in both polarities via `skill_invoked` with an `expected` boolean, decouple dispatched-skill evidence from the group name, and ship a binder-backed `evalspec analyze` — Phase 4 of the #1 roadmap.

> **Revision (2026-07-10, post-review):** the first draft claimed `trigger.py` becomes unreachable, proposed a fully-offline analyzer, changed only one `skills_dispatched` call site, and omitted negative activation. Review disproved all four against the tree. This version scopes the real trigger-*eval* removal surface, makes `analyze` binder-backed, removes the singular `detect_skill`/`fired` coupling from ordinary execution, and adds the negative form.

## Problem

- **Symptom:** Skill activation lives in a separate schema and code path — `trigger-evals.md` parsed by `mdformat.parse_trigger` (`mdformat.py:280-302`), validated as `evalspec-trigger/v1` (`schema.py:236-309`), discovered via a second knob set (`resolve_eval_roots`/`_skill_dirs`/`_DEFAULT_EVAL_ROOTS`, `discovery.py:100,131-145,230-254,348-368`), run by `test_trigger` (`cases.py:168-246`), and reported through its own machinery (`report._trigger_qdirs`/`_trigger_rows`/routing section, `report.py:63-68,168-189,425-433,574-577`; `workspace.trigger_dir`, `workspace.py:89`; manifest `trigger_effort`/`trigger_mode`). The roadmap folds activation into ordinary `## Assertions`, making this eval axis redundant.
- **Symptom:** Activation grades via `skill_invoked` (`checkers.py:203-211`), but the prose→checker routing runs through the paid Gemini binder — `bind()` only fast-paths file-existence locally (`_bind_bare_exists`, `binder.py:343-359`). A fully-offline classifier can't see it, and would mislabel Gemini-bindable assertions (`glob_count`, `regex`, `sha256_match`, `frontmatter_has`; `binder.py:52-62`) as semantic when they actually bind deterministically at runtime.
- **Symptom:** Dispatched-skill identity is coupled to the group name in a way one call site can't fix. `detect_skill = eval_case.skill` (`execution.py:310`) — a *singular string* — is threaded into every adapter's harness detection (`claude.py:278`, `codex.py:553`, `opencode.py:640`) and sets `ArmOutcome.fired` off the transcript (`execution.py:422`). Grading could report the asserted skill fired while the transcript says it didn't, and a singular hint can't represent multiple asserted skills.
- **Symptom:** The current trigger schema models both activation polarities (`should_trigger` true/false), but the binder's A9 rule **always punts negation** (`binder.py:69`). Deleting triggers without a negative assertion form drops deterministic coverage the vision keeps — `Capability ingest not activated` (`evalspec-readme-vision.md:146`).
- **Symptom:** `evalspec analyze` does not exist — `__main__.py` registers only `lint` (`__main__.py:16-27`).
- **Exposed by:** Roadmap #1 Phase 4 Done-When: `Skill X invoked` inside ordinary `## Assertions`; `trigger-evals.md` no longer required; `evalspec analyze` classifies process-fact assertions before a run.
- **Scope:** Retire the trigger-*eval* subsystem; add both-polarity activation binding + a binder-backed classifier; decouple dispatched-skill evidence from the group. Not: the full CLI surface (`run`, `sandbox:build`, shared conventions) — Phase 5.
- **Constraint:** No compatibility shims (roadmap rule). The `evalspec-trigger/v1` schema, `trigger-evals.md` discovery/parse/run/report are removed outright.
- **Constraint:** Keep the skill-detection primitives the *normal* path and sandbox routing use — `detect_skill_fired`, `dispatches_skill`, `streamed_activity` (`trigger.py`, imported by `runner.py:17`, `claude.py:19`) and `RoutingError` (`sandbox.py:25`). `trigger.py` shrinks; it is not deleted.
- **Note:** No authored trigger evals exist to migrate — zero `trigger-evals.md` files, no `skills/`/`.claude/skills/` tree. Removal touches code, the trigger fixtures in `tests/test_plugin.py`/`test_discovery.py`/`test_mdformat.py`, and docs; there is no eval-content restructuring.

## Solution

```markdown
## Assertions
- [ ] Skill `ingest` invoked           # positive → {"checker":"skill_invoked","skill":"ingest","expected":true}
- [ ] Skill `codex` not invoked        # negative → {"checker":"skill_invoked","skill":"codex","expected":false}
- [ ] A file exists at `./notes.md`    # deterministic (existing local fast path)
- [ ] The summary is accurate          # binder punts → judge-backed
```

```text
$ evalspec analyze .          # binder-backed: local fast-paths, then the real binder, then judge-backed
skills/ingest-activation/capture.eval.md
  activation    Skill `ingest` invoked           (local)
  activation    Skill `codex` not invoked        (local)
  deterministic A file exists at ./notes.md       (local)
  judge-backed  The summary is accurate           (binder punt)
```

Activation is one assertion among the rest, gradable in both polarities offline, and `trigger-evals.md` and its report/schema are deleted.

## User Stories

1. As an eval author, I want **`` Skill `X` invoked `` (and `` not invoked ``) in `## Assertions`**, so activation — both polarities — is one assertion, not a separate `trigger-evals.md` schema.
2. As an eval author, I want **`evalspec analyze` to label each assertion deterministic / activation / judge-backed before I pay for a run**, so I catch a mis-phrased check cheaply — reusing the real binder so its verdict matches runtime.
3. As a maintainer, I want **one discovery/parse/report path**, so activation and output evals stop drifting across two knob sets, two grammars, and two report renderers.

## Implementation Decisions

```text
eval.md `## Assertions`
   │
   ▼
binder.bind(text) ──► _bind_bare_exists            (offline, unchanged)
                 ├──► _bind_skill_invoked  ← NEW   (offline: `Skill X invoked` / `not invoked`)
                 └──► Gemini _BINDING_PROMPT        (everything else)
   │
   ▼ {"checker":"skill_invoked","skill":"X","expected":<bool>}
checkers._skill_invoked(spec, …, context.dispatched_skills)   # membership test, honors `expected`
   ▲
   └─ dispatched = skills_dispatched(trajectory)   ← target-INDEPENDENT (no group hint)

evalspec analyze ──► binder-backed classify() ──► {deterministic | activation | judge-backed}
   (new __main__ subcommand; local fast-paths first, real binder for the rest)
```

- **Delete the trigger-eval subsystem; keep the detection primitives.** Remove `discover_trigger_cases`/`_skill_dirs`/`resolve_eval_roots`/`_DEFAULT_EVAL_ROOTS`/`_pyproject_eval_roots`/`TriggerCase` (`discovery.py:69-86,100,121-145,230-254,348-368`), `parse_trigger`/`_parse_trigger_section`/trigger regexes (`mdformat.py:35-38,232-302`), `_validate_trigger_v1`+dispatch+`_TRIGGER_V1` (`schema.py:18,236-309,312-324`), the `trigger_query` param + trigger CLI options + `test_trigger` (`plugin.py:105-136,686-706`; `cases.py:72-88,168-246`), `xfail_applies` (`trigger.py`; imports at `report.py:23`, `plugin.py:38`), the report trigger surface (`report.py:63-68,168-189,231-243,425-433,525,574-577`), `workspace.trigger_dir` (`workspace.py:89`), and manifest `trigger_effort`/`trigger_mode` (`plugin.py`). **Keep** `detect_skill_fired`/`dispatches_skill`/`streamed_activity`/`RoutingError` — the normal run and `__route__` sandbox path (`sandbox.py:488`) depend on them. `--evalspec-eval-roots`/`eval_roots` go away; `eval_paths` (Phase 3) is the sole discovery knob.
- **Add an offline `skill_invoked` recognizer, both polarities.** A local matcher in `bind()` (`binder.py:328`) maps `` Skill `X` invoked `` → `expected:true` and `` Skill `X` not invoked `` → `expected:false`, no Gemini call. `_skill_invoked` (`checkers.py:203-211`) gains the `expected` field: it passes when membership equals `expected`. The Gemini prompt keeps teaching the primitive for looser phrasings but the canonical shapes never pay.
- **Make dispatched-skill evidence target-independent.** Remove singular `detect_skill = eval_case.skill` and `ArmOutcome.fired` from ordinary eval execution (`execution.py:310,353,422`); capture the full dispatched-skill set with `skills_dispatched(trajectory)` (no group hint) and let each `skill_invoked` assertion resolve its own bound target against that set. `EvalCase.skill` remains only the artifact-path slot (`discovery.py:32-34`). The `__route__` sandbox path and its primitives are untouched.
- **Ship a binder-backed `evalspec analyze`.** A new `__main__` subcommand mirroring `lint.run`/`lint_repo` (`lint.py:69-90`): walk `discover_eval_cases`, classify each assertion via local fast-paths then the real binder, label it `deterministic`/`activation`/`judge-backed`, and print the table. It reads `GEMINI_API_KEY` and incurs per-punt binder spend (the sanctioned Phase 2 cost surface); the local fast-paths keep activation/exists assertions free. Full CLI conventions are Phase 5.
- **Define the contracts.** `type` in `grading.json` stays the *checker family* (`deterministic`); `analyze`'s label is the *evidence domain* (`activation` is a deterministic checker with a process-fact source) — documented as two distinct axes. `analyze` exits `0` on a fully-classified suite and nonzero only on a discovery/parse error (there is no "malformed assertion" — assertions are free prose). A committed fixture eval gives `analyze` and the activation path something to run against. Binder-corpus accounting adds the new local `skill_invoked` fast-path as a non-billed draw.

## Testing Plan

### Logic
- **Both polarities bind offline** — `` Skill `X` invoked `` and `` not invoked `` resolve to `skill_invoked` with the right `expected`, no Gemini call; looser phrasings still fall through to the (mocked) binder.
- **`expected` grading** — a firing arm passes `invoked`/fails `not invoked`; a non-firing arm the reverse; exact and namespaced (`plugin:ingest`) membership both count.
- **Evidence is target-independent** — an eval whose group name differs from its asserted skills still grades every asserted skill correctly; no singular group-derived `detect_skill` remains in ordinary execution.
- **Trigger schema is gone** — a former `evalspec-trigger/v1` doc raises; retained primitives (`detect_skill_fired`, routing) still import and run.

### Behavior
- **`evalspec analyze .` classifies a fixture suite** — each assertion is labeled deterministic/activation/judge-backed; local fast-paths bill nothing; a discovery/parse error exits nonzero.
- **Activation grades end-to-end from `## Assertions`** — a fixture eval with positive and negative activation (no `trigger-evals.md`) runs discovery→grading and records both results on both arms.

### Interface
- **`analyze` exit codes** — clean suite exits 0; a malformed eval file exits nonzero with a readable diagnostic.
- **Discovery knobs** — only `eval_paths` remains; `eval_roots`/`--evalspec-eval-roots` are absent; no path reads `trigger-evals.md`; the run-level report has no trigger section.

## Open Questions

- **Assertion naming: `Skill X invoked` (current code/binder) vs the vision's `Capability X activated` (`evalspec-readme-vision.md:121,146`)?** Recommendation: keep `Skill … invoked/not invoked` (matches the shipped checker and binder prompt) and note `Capability … activated` as documentation phrasing to reconcile in `schema.md`. Resolved by a naming call before implementation.
- **Does `analyze` ship in Phase 4 or defer wholly to Phase 5?** Recommendation: ship the thin binder-backed command now so the Phase 4 Done-When is verifiable; Phase 5 absorbs it into the unified CLI. Resolved if Phase 5 is sequenced immediately after.

## Documentation Plan

- **`docs/schema.md`**: remove the `trigger-evals.md`/`evalspec-trigger/v1` grammar; document `` Skill `X` invoked ``/`` not invoked `` and the `expected` semantics; reconcile the `Capability activated` naming.
- **`docs/configuration.md`**: drop `eval_roots`/`--evalspec-eval-roots`; document `evalspec analyze` and its `GEMINI_API_KEY` requirement.
- **`docs/agents.md`**: replace trigger-routing notes with the activation-assertion model; clarify which `trigger.py` primitives remain (routing/detection) vs removed (trigger-eval scoring).
- **`docs/concepts.md`**, **`src/evalspec/__init__.py`**: strike the "each skill's `evals/trigger-evals.md`" language.

## Out of Scope

- The full CLI surface — `evalspec run`, `evalspec sandbox:build`, shared arg/exit-code conventions are Phase 5.
- New process-fact shapes beyond skill activation (tool-call counts, etc.) — added only when a concrete observable justifies one.
- Removing sandbox routing or the `__route__` detection path — those keep the retained `trigger.py` primitives.
- Multi-turn trajectory replay and any change to trajectory capture (`trajectory.py`).

## References

- #1 — roadmap umbrella; Phase 4 Done-When; corrected sequencing (#25 lands before #26).
- #23 / `docs/specs/2026-07-09-markdown-eval-format-and-discovery.md` — Phase 3; flagged the `detect_skill`/`group` coupling and `eval_roots` retirement.
- `docs/research/evalspec-readme-vision.md:121,146` — target activation forms incl. the negative case.
- `src/evalspec/binder.py:52-62,69,328,343-359` — checker primitives, A9 negation punt, the bind fork.
- `src/evalspec/execution.py:310,353,422` — the singular `detect_skill`/`fired` coupling to remove.
- `src/evalspec/checkers.py:203-214` — `skill_invoked` (gains `expected`).
- `src/evalspec/trigger.py`, `src/evalspec/agents/claude.py:19`, `src/evalspec/runner.py:17`, `src/evalspec/sandbox.py:25,488` — retained detection/routing primitives.
- `src/evalspec/report.py:63-68,168-189,425-433`, `src/evalspec/workspace.py:89` — trigger report/artifact surface to remove.
- `src/evalspec/lint.py:69-90` — the shape `analyze` mirrors.

## Verification

- `make test` — proves both-polarity binding, target-independent evidence, discovery, and the removed trigger path pass.
- `make lint` — proves package and docs satisfy lint after the trigger-eval deletion.
- `evalspec analyze <fixture-suite>` — proves each assertion classifies deterministic/activation/judge-backed before a paid run, with activation/exists billing nothing.
- `evalspec lint .` — proves no in-repo suite references `trigger-evals.md` or `eval_roots`.
