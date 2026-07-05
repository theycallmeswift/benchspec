# Style Lint Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Ruff style coverage plus an advisory Gemini-backed custom style checker without slowing the default lint path.

**Architecture:** Ruff owns fast, deterministic checks through `pyproject.toml` and `make lint`. The custom checker is a thin script wrapper over an importable `evalspec.style_lint` module so tests can stub the Gemini boundary cleanly. Deterministic code gathers candidate snippets and prompt context; Gemini remains the source of advisory findings.

**Tech Stack:** Python 3.10+, Ruff, pytest, Make, Gemini API over `GEMINI_API_KEY`.

---

## File Structure

- Modify `pyproject.toml`: add commented Ruff configuration and Google pydocstyle convention.
- Modify `Makefile`: add `lint:custom` while keeping `lint` Ruff-only.
- Create `bin/linters/style_lint.py`: executable CLI wrapper that imports `evalspec.style_lint`.
- Create `src/evalspec/style_lint.py`: rule dataclasses, path collection, candidate extraction, Gemini prompt/call/parsing, optional verification, output formatting, and `run()`/`main()`.
- Create `tests/test_style_lint.py`: focused tests for the custom checker and Makefile-facing behavior.

---

### Task 1: Pin Fast Ruff Policy

**Files:**
- Modify: `pyproject.toml`
- Test: command-level `uv run ruff check .`

- [ ] **Step 1: Add Ruff configuration**

Add this block after `[tool.uv]` in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py310"

[tool.ruff.lint]
select = [
    "E",      # pycodestyle errors: syntax-adjacent readability and whitespace.
    "F",      # Pyflakes: undefined names, unused imports, and invalid constructs.
    "I",      # isort: stdlib, third-party, local import grouping and ordering.
    "UP",     # pyupgrade: modern Python syntax for the supported version range.
    "FA",     # flake8-future-annotations: require postponed annotations.
    "D",      # pydocstyle: module, class, and function docstring requirements.
    "ANN",    # flake8-annotations: function argument and return annotations.
    "TID",    # flake8-tidy-imports: absolute imports and banned import shapes.
    "PT",     # flake8-pytest-style: pytest-specific readability rules.
    "B",      # flake8-bugbear: likely bugs and surprising Python behavior.
    "A",      # flake8-builtins: avoid shadowing Python builtins.
    "RUF100", # Ruff: remove stale noqa suppressions while custom lint bans suppressions.
]

[tool.ruff.lint.pydocstyle]
convention = "google"
```

- [ ] **Step 2: Run Ruff and inspect any newly surfaced violations**

Run:

```bash
uv run ruff check .
```

Expected: either `All checks passed!` or concrete diagnostics caused by the newly enabled rules.

- [ ] **Step 3: If Ruff reports repo violations, fix only violations needed for `make lint`**

Use the diagnostic paths from Ruff. Keep changes mechanical: docstrings, annotations, imports, stale suppressions, and built-in shadowing only. Do not add size, complexity, preview DOC, or unrelated style rules.

- [ ] **Step 4: Verify fast lint still passes**

Run:

```bash
make lint
```

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml <any files mechanically fixed for Ruff>
git commit -m "chore: configure ruff style rules"
git push
```

---

### Task 2: Add Custom Lint CLI Contract Tests

**Files:**
- Create: `tests/test_style_lint.py`

- [ ] **Step 1: Write failing tests for paths, rules, prompt shape, and advisory output**

Create `tests/test_style_lint.py` with tests named:

```python
def test_run_skips_cleanly_without_gemini_api_key(monkeypatch, capsys): ...
def test_collect_targets_defaults_to_src_tests_evals(tmp_path, monkeypatch): ...
def test_collect_targets_respects_explicit_path_args(tmp_path): ...
def test_build_detector_prompt_includes_stable_rule_ids_and_descriptions(tmp_path): ...
def test_parse_findings_rejects_unknown_rule_ids(tmp_path): ...
def test_find_candidates_flags_single_letter_bindings_but_allows_unused_underscore(tmp_path): ...
def test_find_candidates_flags_suppression_comments(tmp_path): ...
def test_find_candidates_flags_section_headers_but_not_why_comments(tmp_path): ...
def test_find_candidates_flags_indented_triple_quoted_strings_without_dedent(tmp_path): ...
def test_verify_model_is_not_called_by_default(tmp_path, monkeypatch): ...
def test_verify_model_can_filter_findings_when_enabled(tmp_path, monkeypatch): ...
def test_run_prints_findings_in_path_line_col_rule_format_and_stays_advisory(tmp_path, monkeypatch, capsys): ...
```

Use inline `tmp_path` file writers. Monkeypatch the Gemini call function so tests never require network access or `GEMINI_API_KEY` except where intentionally checking the skip path.

- [ ] **Step 2: Run tests to verify they fail on missing module**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_style_lint.py -q
```

Expected: failure due to missing `evalspec.style_lint`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_style_lint.py
git commit -m "test: cover custom style lint contract"
git push
```

---

### Task 3: Implement Custom Style Linter

**Files:**
- Create: `src/evalspec/style_lint.py`
- Create: `bin/linters/style_lint.py`
- Test: `tests/test_style_lint.py`

- [ ] **Step 1: Add importable rule and finding model**

In `src/evalspec/style_lint.py`, define:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PATHS = (Path("src"), Path("tests"), Path("evals"))

@dataclass(frozen=True)
class Rule:
    id: str
    description: str

@dataclass(frozen=True)
class Candidate:
    path: Path
    line: int
    column: int
    rule_id: str
    text: str

@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    column: int
    rule_id: str
    message: str
```

Define `RULES` with these exact IDs: `no-suppression-comments`, `section-header-comments`, `provenance-comments`, `descriptive-names`, `dedented-multiline-strings`.

- [ ] **Step 2: Implement path collection**

Implement `collect_targets(paths: list[Path] | None = None) -> list[Path]` so `None` defaults to existing Python files under `src`, `tests`, and `evals`, while explicit file or directory arguments scope the walk.

- [ ] **Step 3: Implement deterministic candidate extraction**

Implement `find_candidates(path: Path) -> list[Candidate]` using AST, tokenize, and line scanning:

- single-letter argument, assignment, loop, lambda, and comprehension bindings except `_`;
- suppression comments containing `noqa`, `type: ignore`, `pyright: ignore`, `pylint: disable`, `ruff: noqa`, or `mypy:`;
- section-header comments that are only labels or visual dividers;
- provenance comments mentioning issues, PRs, commits, callers, planning docs, or `see docs/`;
- indented triple-quoted string expressions not wrapped by `textwrap.dedent`, excluding module/class/function docstrings.

- [ ] **Step 4: Implement Gemini prompt, call, parse, and optional verification**

Implement:

```python
def build_detector_prompt(candidates: list[Candidate], rules: list[Rule] = RULES) -> str: ...
def call_gemini(prompt: str, *, model: str) -> str: ...
def parse_findings(response: str, candidates: list[Candidate]) -> list[Finding]: ...
def detect_findings(candidates: list[Candidate], *, model: str = DEFAULT_MODEL) -> list[Finding]: ...
def verify_findings(findings: list[Finding], candidates: list[Candidate], *, verify_model: str | None) -> list[Finding]: ...
```

Use strict JSON response expectations. Reject unknown rule IDs and malformed finding references with `ValueError`.

- [ ] **Step 5: Implement CLI run path**

Implement `run(paths: list[Path] | None = None, *, model: str = DEFAULT_MODEL, verify_model: str | None = None) -> int` and `main(argv: list[str] | None = None) -> int`.

Required behavior:
- if `GEMINI_API_KEY` is absent, print a clear skip message and return `0`;
- print findings as `path:line:col: rule-id message`;
- return `0` even when findings exist;
- accept `--model`, `--verify-model`, and optional path arguments.

- [ ] **Step 6: Add script wrapper**

Create `bin/linters/style_lint.py`:

```python
"""Run repository-specific advisory style lint checks."""

from __future__ import annotations

from evalspec.style_lint import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Verify targeted tests pass**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_style_lint.py -q
```

Expected: all tests in `tests/test_style_lint.py` pass.

- [ ] **Step 8: Commit**

```bash
git add src/evalspec/style_lint.py bin/linters/style_lint.py tests/test_style_lint.py
git commit -m "feat: add advisory style linter"
git push
```

---

### Task 4: Wire Makefile and Smoke Commands

**Files:**
- Modify: `Makefile`
- Test: `tests/test_style_lint.py`

- [ ] **Step 1: Update phony targets and add `lint:custom`**

Change `.PHONY` to include `lint\:custom`, keep `lint` as Ruff-only, and add:

```make
lint\:custom:  ## Run custom advisory style checks
	uv run python bin/linters/style_lint.py
```

- [ ] **Step 2: Add Makefile contract tests**

In `tests/test_style_lint.py`, add tests that read `Makefile` and assert:

```python
assert "lint\\:custom" in makefile_text
assert "uv run ruff check ." in makefile_text
assert "uv run python bin/linters/style_lint.py" in makefile_text
```

Also assert the `lint` target body does not include `style_lint.py`.

- [ ] **Step 3: Verify dry-run command shape**

Run:

```bash
make -n lint
make -n lint:custom
```

Expected: `lint` prints only `uv run ruff check .`; `lint:custom` prints only `uv run python bin/linters/style_lint.py`.

- [ ] **Step 4: Verify custom lint skip path**

Run:

```bash
make lint:custom
```

Expected without `GEMINI_API_KEY`: clear skip message and exit `0`. Expected with `GEMINI_API_KEY`: advisory diagnostics may print, exit `0`.

- [ ] **Step 5: Commit**

```bash
git add Makefile tests/test_style_lint.py
git commit -m "chore: add custom lint make target"
git push
```

---

### Task 5: Full Verification and Cleanup

**Files:**
- Modify only files needed to fix test, lint, or review findings.

- [ ] **Step 1: Run targeted tests**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_style_lint.py -q
```

Expected: pass.

- [ ] **Step 2: Run full tests**

```bash
make test
```

Expected: all selected tests pass.

- [ ] **Step 3: Run fast lint**

```bash
make lint
```

Expected: `All checks passed!`

- [ ] **Step 4: Run advisory lint**

```bash
make lint:custom
```

Expected: exit `0`; without `GEMINI_API_KEY`, the command prints a clear skip message.

- [ ] **Step 5: Commit any verification fixes**

```bash
git add <specific files changed>
git commit -m "fix: address style lint verification"
git push
```

Skip this commit if there are no verification fixes.

---

## Self-Review

- Spec coverage: Ruff config, inline comments, Google docstrings, custom rule IDs, Gemini 3.1 Flash Lite default, `GEMINI_API_KEY` skip behavior, optional verification model, advisory exit semantics, default paths, and Makefile speed boundary are covered.
- Placeholder scan: no `TBD`, `TODO`, vague "add tests", or undefined task dependencies remain.
- Type consistency: `Rule`, `Candidate`, `Finding`, `collect_targets`, `find_candidates`, `build_detector_prompt`, `detect_findings`, `verify_findings`, `run`, and `main` names are consistent across tasks.
