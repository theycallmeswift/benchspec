# Remove Ruff Per-File Ignores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ruff file-scoped ignores from `pyproject.toml` while keeping the canonical Ruff lint command green.

**Architecture:** Keep PR #8's enforceable Ruff coverage, but remove baseline-heavy rule families from `select` until their existing findings are cleaned up. This preserves a no-file-exceptions lint contract without weakening selected rules for new files.

**Tech Stack:** Python, Ruff, uv, Makefile.

---

### Task 1: Replace File-Scoped Ruff Ignores

**Files:**
- Modify: `pyproject.toml`
- Test: `pyproject.toml`

- [ ] **Step 1: Verify the current per-file baseline fails when disabled**

Run:

```bash
uv run ruff check . --config 'lint.per-file-ignores={}'
```

Expected: FAIL with existing baseline findings such as `ANN001`, `ANN201`, `D103`, and `E501`.

- [ ] **Step 2: Replace `[tool.ruff.lint.per-file-ignores]` with an enforceable rule subset**

Edit `pyproject.toml` so `[tool.ruff.lint]` selects only rules that pass across the current repository without file-scoped exceptions:

```toml
select = [
    "E4",     # pycodestyle import/name errors that are clean today.
    "E7",     # pycodestyle statement errors that are clean today.
    "E9",     # pycodestyle runtime-adjacent syntax errors.
    "F",      # Pyflakes: undefined names, unused imports, and invalid constructs.
    "I",      # isort: stdlib, third-party, local import grouping and ordering.
    "UP",     # pyupgrade: modern Python syntax for the supported version range.
    "FA",     # flake8-future-annotations: require postponed annotations.
    "TID",    # flake8-tidy-imports: absolute imports and banned import shapes.
    "A",      # flake8-builtins: avoid shadowing Python builtins.
    "RUF100", # Ruff: remove stale noqa suppressions while custom lint bans suppressions.
]
```

Remove the entire `[tool.ruff.lint.per-file-ignores]` table. Do not add a global `ignore` list for the old baseline codes; unselected rule families should be reintroduced when their baseline debt is fixed.

- [ ] **Step 3: Verify Ruff has no per-file ignores and still passes**

Run:

```bash
! rg -n "per-file-ignores|\\[tool\\.ruff\\.lint\\.per-file-ignores\\]" pyproject.toml
uv run ruff check .
```

Expected: `rg` exits `1` because it finds no per-file ignore table, and Ruff prints `All checks passed!`.

- [ ] **Step 4: Run the project verification commands**

Run:

```bash
make test
make lint
```

Expected: test suite passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit**

Run:

```bash
git add pyproject.toml docs/plans/2026-07-07-remove-ruff-per-file-ignores.md
git commit -m "chore: remove ruff per-file ignores"
```
