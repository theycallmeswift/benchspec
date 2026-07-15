# Phase 10 PR 2: Fresh-Eyes Docs Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redocument evalspec from scratch against the settled post-reorg tree — README rewritten to the vision-doc voice bar, the six project docs replaced by whatever doc set a fresh-eyes documentation plan defines — closing out issue #42.

**Architecture:** The spec (docs/specs/2026-07-14-package-reorg-and-docs-rewrite.md) mandates that the rewrite run as a single subagent dispatched with the appendix prompt **verbatim** (extracted to `.superpowers/sdd/docs-rewrite-prompt.md`). The subagent derives a documentation plan from the product before reading any old doc, mines the old docs for verified facts only, writes the new set ground-up, and deletes what it replaces. This plan wraps that dispatch with verification and review.

**Tech Stack:** Markdown, mermaid diagrams, `tests/test_readme_examples.py` as the README-accuracy gate, `make test`/`make lint`.

## Global Constraints

- **Scope in:** README.md (absorbing docs/research/evalspec-readme-vision.md, then deleting it) and the project-doc set under docs/ replacing docs/{quickstart,concepts,configuration,agents,schema,goals}.md.
- **Scope out (frozen):** docs/style/, CLAUDE.md, AGENTS.md, docs/specs/, docs/plans/, source code, Makefile.
- **Accuracy:** every import path, CLI command, config key, file path, and exit code verified against the tree; every code fence runnable/loadable as written; package names reflect the post-reorg layout.
- **README examples enforced by tests/test_readme_examples.py** — imports must stay true; that test's own logic is not edited (it may only be updated if the README's enforced literals legitimately change, and any such change is disclosed).
- **Voice bar:** Kleppmann-grade clarity; intuition before mechanism; layered skippable depth; callouts; real structure (tables, lists, mermaid) — no ASCII art.
- **Verification commands:** `make test`; `make lint`; a stale-path grep over the new docs (no pre-reorg module paths).

---

## Task 1: Dispatch the docs-rewrite subagent (spec appendix prompt, verbatim)

**Files:**
- Modify: `README.md` (ground-up rewrite)
- Create: the doc set under `docs/` that the subagent's documentation plan defines
- Delete: `docs/quickstart.md`, `docs/concepts.md`, `docs/configuration.md`, `docs/agents.md`, `docs/schema.md`, `docs/goals.md`, `docs/research/evalspec-readme-vision.md`
- Possibly modify: `tests/test_readme_examples.py` only if enforced README literals change (disclose)

**Interfaces:**
- Consumes: the post-reorg working tree at the tip of refactor/42-package-reorg; `.superpowers/sdd/docs-rewrite-prompt.md` (the spec appendix, verbatim).
- Produces: the documentation plan (one line per doc: reader, job), the new files, the per-file self-check list of verified factual claims.

- [ ] **Step 1:** Verify the extracted prompt is byte-identical to the spec appendix before dispatch: extract the four-backtick fenced block from docs/specs/2026-07-14-package-reorg-and-docs-rewrite.md (the lines between the two fence lines under "Appendix: Docs-Rewrite Subagent Prompt") and `diff` it against `.superpowers/sdd/docs-rewrite-prompt.md`. Expected: no diff. Then dispatch a subagent whose prompt is the extracted appendix verbatim, wrapped only with operational context (worktree path; do not commit; return the plan + file list + self-check list).
- [ ] **Step 2:** Gate the output: documentation plan present; every replaced file deleted; self-check list cites a verification (command or file:line) per factual claim, including at least one cited verification per non-mermaid code fence.
- [ ] **Step 3:** Run `make test` (README example tests must pass), `make lint`, and the public-import smoke test `uv run python -c "from evalspec.agents import CodingAgent, make_agent; from evalspec.testing import FakeSandbox"`. Expected: exactly `924 passed, 950 deselected` (both counts must match; any deviation is a regression to investigate, not a pass); lint exit 0; smoke test exit 0.
- [ ] **Step 4:** Stale-path grep scoped to README.md plus the newly written project docs only (explicitly excluding `docs/specs/`, `docs/plans/`, `docs/style/` — frozen history that legitimately contains old paths): `grep -rnE 'evalspec\.(plugin|arms|discovery|mdformat|schema|lint|execution|runner\b|binder|judge\b|report|analyze|backend|provenance|sandbox\.)' README.md <new docs/ files from the documentation plan>` — `\b` on `runner`/`judge` avoids the `runners`/`judges` prefix collision. Expected: zero hits.
- [ ] **Step 5:** Commit: `docs: rewrite README and project docs from fresh eyes (Phase 10 PR 2)`

## Task 2: Editorial + accuracy review loop

- [ ] **Step 1:** Codex adversarial review of the new docs (accuracy against tree, voice bar, scope discipline); independent validation of findings. If `tests/test_readme_examples.py` was touched, verify the diff changes only string literals (the asserted `-k` example and the negative-match string) and not assertion count, structure, or the parse-and-must-not-raise check — any broadening or deletion of an assertion is a gate-weakening finding, not a disclosed literal update.
- [ ] **Step 2:** Apply validated fixes via a fix subagent; re-run `make test`/`make lint`; commit `docs: address docs-rewrite review feedback`.

## Task 3: Roadmap bookkeeping (ship step)

- [ ] **Step 1:** Amend issue #1's stale Done-When wording (Make-target transition: bless the surviving `make evals`; cache-identity/image-digest phrasing) — disclosed in a PR comment before editing.
- [ ] **Step 2:** PR body carries `Closes #42`.

## Self-review checklist (plan-level)

- Spec appendix dispatched verbatim (constraint of the spec) — Task 1 Step 1.
- All six old docs + vision doc deleted — Task 1 Files.
- README example tests green — Task 1 Step 3.
- Issue #1 amendment — Task 3 (mandated by #42's Documentation Plan).
