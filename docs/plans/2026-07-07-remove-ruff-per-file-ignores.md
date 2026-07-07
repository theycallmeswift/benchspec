# Remove Ruff Per-File Ignores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ruff file-scoped ignores from `pyproject.toml` while keeping the canonical Ruff lint command green.

**Architecture:** Keep PR #8's selected Ruff families, but move existing baseline debt out of `[tool.ruff.lint.per-file-ignores]` into a single documented `ignore` list. This preserves a no-file-exceptions lint contract and avoids a broad, behavior-neutral rewrite of thousands of existing docstring, annotation, and line-length findings.

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

- [ ] **Step 2: Replace `[tool.ruff.lint.per-file-ignores]` with rule-level ignores**

Edit `pyproject.toml` so `[tool.ruff.lint]` contains this list immediately after `select`:

```toml
ignore = [
    "ANN001", # Existing baseline: missing argument annotations.
    "ANN002", # Existing baseline: missing *args annotations.
    "ANN003", # Existing baseline: missing **kwargs annotations.
    "ANN201", # Existing baseline: missing public function return annotations.
    "ANN202", # Existing baseline: missing private function return annotations.
    "ANN204", # Existing baseline: missing special-method return annotations.
    "ANN205", # Existing baseline: missing staticmethod return annotations.
    "ANN401", # Existing baseline: dynamically typed Any annotations.
    "B905",   # Existing baseline: zip calls without explicit strict mode.
    "D100",   # Existing baseline: missing module docstrings in tests.
    "D101",   # Existing baseline: missing class docstrings.
    "D102",   # Existing baseline: missing method docstrings.
    "D103",   # Existing baseline: missing function docstrings.
    "D104",   # Existing baseline: missing package docstrings.
    "D105",   # Existing baseline: missing magic-method docstrings.
    "D107",   # Existing baseline: missing __init__ docstrings.
    "D205",   # Existing baseline: missing blank line after docstring summary.
    "D209",   # Existing baseline: closing triple quotes not on their own line.
    "D301",   # Existing baseline: raw strings needed for backslashes.
    "E501",   # Existing baseline: lines over 88 columns.
    "PT011",  # Existing baseline: broad pytest.raises matches.
    "PT018",  # Existing baseline: compound assertions in tests.
]
```

Remove the entire `[tool.ruff.lint.per-file-ignores]` table.

- [ ] **Step 3: Verify Ruff has no per-file ignores and still passes**

Run:

```bash
rg -n "per-file-ignores|\\[tool\\.ruff\\.lint\\.per-file-ignores\\]" pyproject.toml
uv run ruff check .
```

Expected: `rg` finds no per-file ignore table and Ruff prints `All checks passed!`.

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
