# Package Reorg and Docs Rewrite

**TL;DR** — Reorganize `src/evalspec/`'s 24 flat modules into seven lifecycle packages behind the frozen public surface, then redocument the project from fresh eyes in the promoted README voice — closing Phase 10, the final phase of roadmap #1.

## Problem

- **Symptom:** `src/evalspec/` is a flat directory of ~24 modules. Ownership is not legible from the tree — sandbox machinery sits beside report rendering, and `plugin.py` (770 lines) mixes pytest hook wiring with set resolution, session aggregation, and orchestration glue.
- **Exposed by:** Phases 1–9 landed durable boundaries (eval format/discovery, arm config, sandbox backend + provenance, grading, reporting) that the flat layout doesn't express; the Phase 8 and Phase 9 handoff comments on issue #1 enumerate them.
- **Scope:** module and test moves, `plugin.py` decomposition, and a ground-up rewrite of the project docs from a fresh-eyes documentation plan. Behavior is frozen — no feature work rides along.
- **Constraint:** documented entrypoints keep working: the `evalspec` CLI (`__main__.py`), the `pytest11` entrypoint *name* `evalspec`, the exit-code contract (`ExitCode` 0/1/2/5 in `exit_codes.py`), the frozen artifact schemas (`meta.json` v2, `benchmark.json` v3, `index.jsonl`, `provenance.json`), and the public import paths `evalspec.agents` and `evalspec.testing`. No compatibility shims for old internal import paths.
- **Constraint:** the core must stay test-harness-agnostic — pytest is one adapter behind the existing `runner` config field (`SUPPORTED_RUNNERS = {"pytest"}`, `arms.py:40`), so a future second runner lands as a sibling without touching the core.

## Solution

```
src/evalspec/
├── __main__.py  exit_codes.py  testing.py       # root: stable public surface
├── agents/          base, claude, codex, opencode  # documented adapter API
├── specs/           discovery, mdformat, schema, lint
├── config/          arms
├── runners/         pytest (né plugin.py, thin hooks), run
├── orchestration/   execution, cases, room, workspace,
│                    environments, results (né runner.py)
├── sandbox/         sandbox, backend, provenance
├── grading/         binder, checkers, judge, judges/, trajectory, trigger
└── reporting/       report, analyze
```

One child issue, two PRs: PR 1 is the mechanical move plus `plugin.py` extraction, gated on a zero-behavior-diff green run; PR 2 redocuments the project from scratch against the settled tree, promoting the vision doc into the README.

## User Stories

1. As a maintainer, I want **source and tests organized by product concern**, so runner, sandbox, grading, and benchmark changes land in predictable folders.
2. As a future contributor, I want **pytest isolated as a thin adapter in `runners/`**, so a second test harness lands as a sibling module without core changes.
3. As an external `CodingAgent` implementer, I want **`evalspec.agents` and `evalspec.testing` import paths unchanged**, so my harness and its tests survive the reorg untouched.
4. As a newcomer, I want **a README and project docs written with layered, skippable depth**, so I can go from zero to a first eval run without reading source.

## Implementation Decisions

```
[tool.evalspec] ─► config/arms.py ─► runners/pytest.py ─► orchestration/execution.py
                                       (pytest11 hooks,         │
                                        né plugin.py)           ├─► sandbox/sandbox.py ◄─ sandbox/backend.py
specs/discovery.py ─► specs/mdformat.py ────────────────────────┤        └─ sandbox/provenance.py
                                                                ▼
                                    grading/binder.py ─► reporting/report.py ─► out/<run>/benchmark.json
```

- **Lifecycle cut, seven packages.** `specs/` (find, parse, validate, lint eval definitions), `config/` (run configuration), `runners/` (test-harness adapters), `orchestration/` (run one `(eval, arm)`), `sandbox/` (microVM lifecycle, backend seam, provenance), `grading/`, `reporting/`.
  - `specs/` deliberately dodges the repo-root `evals/` name collision; the grep adjacency with `docs/specs/` is accepted.
  - `judges/` moves whole into `grading/judges/` — its config and registry stay together.
  - `testing.py` stays at root: it is documented public API for external `CodingAgent` authors (`docs/agents.md`).
- **`plugin.py` splits: pure logic out, hooks stay thin.** The pytest hook wiring moves to `runners/pytest.py`; extracted pure responsibilities land in the package they belong to (set resolution → `config/`, session report glue → `reporting/`, case orchestration → `orchestration/`).
  - The set-resolution seam (`_has_configured_set` / `run_set_when_needed` / `session_run_set`, `plugin.py:295-323`) survives extraction intact: the planned roster is a config fact, not an output-dir fact — an all-fail run still records its set.
  - `pyproject.toml`'s `[project.entry-points.pytest11]` value updates to the new module path; the entrypoint *name* `evalspec` does not change.
- **Renames are limited to collisions.** `plugin.py` → `runners/pytest.py`; `runner.py` → `orchestration/results.py` (clears the `runner`/`runners` adjacency; `runner` stays a config-field word). Every other module moves basename-intact, and test files rename only to match.
- **Single-source invariants survive the split.** `report.planned_arms()` (`report.py:43`) remains the sole planned-arm builder feeding `meta.json`, `index.jsonl`, and `benchmark.json`; `SandboxBackend.fingerprint_inputs()` remains the sole cache-identity hasher behind both `snapshot_name()` and provenance.
- **Tests mirror the packages.** `tests/{specs,config,runners,orchestration,sandbox,grading,reporting}/` plus the existing `tests/agents/`; cross-cutting suites (`test_main`, `test_exit_codes`, `test_e2e_suite`, `test_readme_examples`, `conftest.py`, `support.py`, `fixtures/`) stay top-level.
- **PR 2 redocuments from fresh eyes — the current docs are not the bar.** A subagent first derives a documentation plan from the product itself (source tree, `pyproject.toml`, Makefile, CLI, `evals/e2e/`, the vision doc) *before* reading any existing project doc; then mines the old docs for verified facts and caveats only, discarding their structure and prose; then writes the README and the new doc set from the ground up, deleting the files its plan replaces. The new doc set need not mirror today's six files.
  - The voice bar: Kleppmann-grade clarity and flow; deep background for newcomers that experienced readers can skip; intuition before mechanism; concrete examples with toy data; callouts for key concepts and edge cases; real structure (lists, tables, rendered diagrams) — no ASCII art.
  - The rewrite runs as a subagent dispatched with the prompt in the appendix, verbatim.
- **Make targets are already in their end state.** `make e2e` (contract suite) and `make evals` (binder corpus) both stay; issue #1's Done-When wording is amended to bless the surviving `make evals` instead of removing it.

## Testing Plan

### Logic
- **Extracted plugin logic is behavior-identical** — set resolution distinguishes collection-time from session-finish exactly as before, including the all-fail-run-records-its-set property.
- **Cache identity and planned-arm construction are unchanged** — the same inputs produce the same snapshot names and the same planned-arm rosters as before the move.

### Behavior
- **The unit suite passes with no test-logic edits** — only import paths and file locations change; any assertion edit signals reopened behavior.
- **An end-to-end run produces artifacts byte-compatible with the frozen schemas** — `meta.json` v2, `benchmark.json` v3, `index.jsonl`, `provenance.json` shapes unchanged.

### Interface
- **The pytest plugin auto-loads under its entrypoint name** — a pytest run resolves the `evalspec` `pytest11` entrypoint at its new module path with no consumer-visible change.
- **CLI contract unchanged** — `evalspec lint|analyze|sandbox:build|run` accept the same args and return the same 0/1/2/5 exit codes.
- **Public imports survive** — `evalspec.agents`, `evalspec.testing`, and every import shown in README/doc examples resolve after the move.

## Documentation Plan

- **README.md**: rewritten from scratch to the voice bar, absorbing the vision doc's content; all paths/imports reflect the new tree.
- **docs/ project docs**: replaced by whatever doc set the fresh-eyes documentation plan defines; the current `docs/{quickstart,concepts,configuration,agents,schema,goals}.md` are deleted as superseded, not edited in place.
- **docs/research/evalspec-readme-vision.md**: retire after promotion (delete; git history preserves it).
- **Issue #1**: amend the stale Done-When wording (Make-target transition, cache-identity/image-digest phrasing) before closing the roadmap.

## Out of Scope

- The five deferred runtime gaps (per-cell timeout, VM CPU-spin on host sleep, signal forwarding, stage-level timing, `sandbox:clean` CLI) — tracked as separate issues, not smuggled into the move.
- Style guides (`docs/style/development.md`), instruction files (`CLAUDE.md`, `AGENTS.md`), and historical specs/plans — frozen as records.
- Compatibility shims or re-export stubs for old internal import paths — external code importing internals breaks by design.
- Any behavior change in PR 1, including drive-by refactors beyond the named extractions and renames.
- New sandbox backends or new test runners — this phase builds the seams, not the second implementations.

## References

- Issue #1 — the roadmap; Phase 10's Done-When, the proposed-layout decision, and User Story 6 ground the reorg.
- Issue #1 Phase 8 and Phase 9 handoff comments — enumerate the durable boundaries, the `plugin.py` extraction guidance, and the "no-op is valid" bar this spec had to clear.
- `docs/research/evalspec-readme-vision.md` — the vision doc PR 2 promotes into the README.
- `src/evalspec/plugin.py`, `src/evalspec/report.py`, `src/evalspec/backend.py`, `src/evalspec/arms.py` — the seams and single-source invariants the move must preserve.
- `docs/specs/2026-07-12-arm-aware-metadata.md`, `docs/specs/2026-07-12-e2e-evals-and-config.md` — the frozen artifact contract and e2e suite the reorg must not disturb.

## Verification

- `make test` — unit suite green with only import-path and location diffs in tests.
- `make lint` — Ruff and house rules pass on the moved tree.
- `make e2e` — the contract suite passes end-to-end on real microVMs; artifacts match the frozen schemas.
- `make evals EVAL_ARGS="--collect-only -q"` — binder-corpus collection still resolves through the plugin entrypoint.
- `uv run python -c "from evalspec.agents import CodingAgent, make_agent; from evalspec.testing import FakeSandbox"` — the documented public imports survive.

## Appendix: Docs-Rewrite Subagent Prompt

PR 2 dispatches the rewrite with this prompt, verbatim:

````
<role>
You are a technical writer designing evalspec's documentation from scratch after the Phase 10 package reorganization. Bar: Martin Kleppmann — clear, flowing, classic prose; engaging, never cute. The existing docs are not the standard to meet; the product is. Accuracy against the current tree outranks elegance.
</role>

<process>
1. Fresh eyes first: before opening any existing project doc, derive a documentation plan from the product itself — the source tree, pyproject.toml, the Makefile, `evalspec --help` and each subcommand, the evals/e2e/ suite, and docs/research/evalspec-readme-vision.md. Decide which docs exist, for which reader, in what reading order. Write the plan down.
2. Only after the plan exists, read the old project docs as a fact mine: harvest verified facts and hard-won caveats; discard their structure, order, and prose.
3. Write the README and the new doc set from the ground up per the plan. Delete the project docs the plan replaces (git history preserves them).
</process>

<scope>
in: README.md (absorbing the vision doc, which you then delete) and the project-doc set under docs/ that the plan defines — it replaces docs/{quickstart,concepts,configuration,agents,schema,goals}.md and need not mirror it.
out: docs/style/, CLAUDE.md, AGENTS.md, docs/specs/, docs/plans/ (frozen history), source code, Makefile.
input: the post-reorg working tree.
</scope>

<voice>
- Intuition before mechanism: lead each topic with its essence and a concrete toy example (a tiny eval, a two-arm set), then the details.
- Layered depth: open sections with the background a newcomer needs, marked so an experienced reader can skip it.
- Smooth transitions; never a heading followed by an unmotivated bullet dump.
- Callouts — blockquotes with a bold lead (**Key concept:**, **Edge case:**) — for definitions and traps.
- Real structure: markdown tables and lists for enumerable things; mermaid for diagrams. Never ASCII-art boxes or arrows.
</voice>

<accuracy>
- Verify every import path, CLI command, config key, file path, and exit code against the tree — grep or run it; never trust prose, old docs included.
- Every code fence must be runnable or loadable as written (README examples are enforced by tests/test_readme_examples.py — keep them import-true).
- Package names must reflect the post-reorg layout (specs/, config/, runners/, orchestration/, sandbox/, grading/, reporting/).
</accuracy>

<output>
1. The documentation plan: each doc, its reader, its job, one line each.
2. The new files written, and the replaced files deleted.
3. A self-check list per file: each factual claim verified and how (command run or file:line read).
</output>

<example>
Before (the old configuration.md's register):
> The `runner` field selects the runner. Only pytest is supported.

After (the register to hit):
> An eval set needs an engine to drive it. Today that engine is pytest — evalspec collects each `(eval × arm)` pair as a parametrized test:
>
> ```toml
> [tool.evalspec.sets.e2e]
> runner = "pytest"   # the only supported value
> ```
>
> **Key concept:** `runner` names the *test harness*, not the coding agent — agents are chosen per-arm (`harness = "claude-code"`).
</example>
````
