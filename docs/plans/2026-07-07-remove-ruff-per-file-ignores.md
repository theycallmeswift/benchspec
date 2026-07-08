# Remove Ruff Per-File Ignores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ruff file-scoped ignores from `pyproject.toml` while keeping the canonical Ruff lint command green.

**Architecture:** Keep PR #8's selected Ruff families enforced globally and clean the existing findings that required per-file exceptions. Set a documented project-wide 100-column line length, then remove `[tool.ruff.lint.per-file-ignores]` entirely.

**Tech Stack:** Python, Ruff, uv, Makefile.

---

### Task 1: Clean Ruff Baseline and Remove File-Scoped Ignores

**Files:**
- Modify: `pyproject.toml`
- Modify: Python files under `src/`, `tests/`, and `evals/`
- Test: Ruff configuration and project test suite

- [ ] **Step 1: Verify the current per-file baseline fails when disabled**

Run:

```bash
uv run ruff check . --config 'lint.per-file-ignores={}'
```

Expected: FAIL with existing baseline findings such as `ANN001`, `ANN201`, `D103`, and `E501`.

- [ ] **Step 2: Clean the current baseline findings**

Apply mechanical fixes for the rule families currently listed in `[tool.ruff.lint.per-file-ignores]`:

```bash
uv run ruff check . --config 'lint.per-file-ignores={}' --fix --unsafe-fixes
uv run ruff format src tests evals
```

Expected: Ruff fixes what it can automatically; remaining diagnostics are addressed directly.

- [ ] **Step 3: Set a project-wide line length and remove per-file ignores**

Edit `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py310"
```

Remove the entire `[tool.ruff.lint.per-file-ignores]` table. Do not add a global `ignore` list and do not narrow `select`.

- [ ] **Step 4: Verify Ruff has no per-file ignores and still passes**

Run:

```bash
! rg -n "per-file-ignores|\\[tool\\.ruff\\.lint\\.per-file-ignores\\]" pyproject.toml
uv run ruff check .
```

Expected: `rg` exits `1` because it finds no per-file ignore table, and Ruff prints `All checks passed!`.

- [ ] **Step 5: Run the project verification commands**

Run:

```bash
make test
make lint
```

Expected: test suite passes and Ruff prints `All checks passed!`.

- [ ] **Step 6: Commit**

Run:

```bash
git add pyproject.toml docs/plans/2026-07-07-remove-ruff-per-file-ignores.md src tests evals
git commit -m "chore: remove ruff per-file ignores"
```
