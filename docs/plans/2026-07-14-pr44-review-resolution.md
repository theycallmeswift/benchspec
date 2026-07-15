# PR 44 Review Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the sandbox-isolation and baseline-comparison contracts in PR 44's documentation.

**Architecture:** Change documentation only. Preserve the readable `/project` mount, optional baseline configuration, and absolute-only report behavior.

**Tech Stack:** Markdown, pytest documentation checks, Ruff, houserules

## Global Constraints

- `/workspace` is clean and writable; `/project` is readable and immutable.
- A configured baseline enables deltas; a set without one reports absolute rates.
- Do not change runtime behavior.

---

### Task 1: Correct the user-facing contracts

**Files:**
- Modify: `README.md:17-36,91-100,153-160`
- Modify: `docs/sandbox.md:3-9`
- Modify: `docs/writing-evals.md:125-140`

**Interfaces:**
- Consumes: sandbox mounts in `src/evalspec/sandbox/backend.py`; optional-baseline reporting in `src/evalspec/reporting/report.py`
- Produces: documentation consistent with those runtime contracts

- [ ] **Step 1:** Rewrite the README comparison introduction to say multi-arm sets produce a matrix and configured baselines produce deltas.
- [ ] **Step 2:** Rewrite the README sandbox paragraph to state that `/project` is readable but immutable.
- [ ] **Step 3:** Rewrite the README comparison benefit to scope delta reporting to baseline-configured sets.
- [ ] **Step 4:** Rewrite the sandbox and eval-authoring guides so they do not claim the repository is unreadable.
- [ ] **Step 5:** Run `make test`; expect `924 passed, 950 deselected`.
- [ ] **Step 6:** Run `make lint`; expect exit 0.
- [ ] **Step 7:** Commit the documentation corrections.
- [ ] **Step 8:** Push the commits to PR 44 and resolve both inline review threads.

