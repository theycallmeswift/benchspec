# Style Lint Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Ruff style coverage plus an advisory Gemini-backed custom style checker without slowing the default lint path.

**Architecture:** Ruff owns fast, deterministic checks through `pyproject.toml` and `make lint`, with existing legacy files scoped by `per-file-ignores` so this issue does not become a repo-wide cleanup. The custom checker keeps evalspec-specific policy in `bin/linters/style_lint.py` and uses reusable framework code under `lib/style_lint/` for file collection, source chunking, Gemini transport, strict JSON/schema validation, optional verification, advisory error handling, and output formatting. Gemini receives numbered source chunks plus rule definitions and remains the source of advisory findings.

**Tech Stack:** Python 3.10+, Ruff, pytest, Make, Gemini REST API over `GEMINI_API_KEY` using stdlib `urllib.request` and `json`.

---

## File Structure

- Modify `pyproject.toml`: add commented Ruff configuration, Google pydocstyle convention, and scoped legacy `per-file-ignores` generated from the current baseline.
- Modify `Makefile`: add `lint:custom` while keeping `lint` Ruff-only.
- Create `lib/style_lint/`: reusable submodules sliced by linter function: shared types, source collection/chunking, detector prompt/parsing/formatting, Gemini transport, optional verification, and the advisory runner API.
- Create `bin/linters/style_lint.py`: repository-specific CLI containing evalspec default paths, rule definitions, model defaults, prompt instructions, advisory `run()`, and `main()`.
- Create `tests/lib/style_linter/`: focused tests for the custom checker and Makefile-facing behavior.

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

Then add a temporary baseline table to avoid rewriting the current codebase as part of this issue. Generate the exact file/rule pairs from Ruff output, and keep each ignore list as narrow as the current diagnostics allow:

```toml
[tool.ruff.lint.per-file-ignores]
"src/evalspec/legacy_file.py" = [
    "ANN001", # Existing function arguments lack annotations; defer typed cleanup.
    "D103",   # Existing public functions lack docstrings; defer docstring cleanup.
]
```

Use real paths from the current Ruff diagnostics. Do not use `"**/*.py"` or broad rule-family ignores like `"ANN"` or `"D"` because that would make the new policy non-enforcing for new files.

- [ ] **Step 2: Run Ruff and inspect any newly surfaced violations**

Run:

```bash
uv run ruff check .
```

Expected: concrete diagnostics caused by the newly enabled rules before the scoped baseline is added; `All checks passed!` after the baseline is complete.

- [ ] **Step 3: Add scoped baseline ignores instead of rewriting existing files**

Use Ruff diagnostics to add `per-file-ignores` entries for existing violations. Keep each entry to the specific rule codes already present in that file. Fix only tiny mechanical violations when the fix is obviously safer than carrying an ignore, such as `I001` import order or an auto-fixable stale suppressions issue. Do not rewrite the repository to add annotations, docstrings, or line wrapping in this issue.

- [ ] **Step 4: Verify fast lint still passes**

Run:

```bash
make lint
```

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml <any tiny mechanical Ruff fixes>
git commit -m "chore: configure ruff style rules"
git push
```

---

### Task 2: Add Custom Lint CLI Contract Tests

**Files:**
- Create: `tests/lib/style_linter/`

- [ ] **Step 1: Write failing tests for paths, rules, prompt shape, and advisory output**

Create `tests/lib/style_linter/` with tests named:

```python
def test_evalspec_package_does_not_own_style_lint_framework(): ...
def test_cli_owns_repo_specific_rules_prompt_and_default_paths(): ...
def test_collect_python_files_defaults_to_configured_roots(tmp_path, monkeypatch): ...
def test_chunk_source_formats_numbered_source_lines(tmp_path): ...
def test_build_detector_prompt_includes_rules_chunks_and_schema(tmp_path): ...
def test_parse_findings_rejects_unknown_rule_ids(tmp_path): ...
def test_parse_findings_rejects_boolean_indexes_and_lines(tmp_path): ...
def test_parse_findings_rejects_lines_outside_referenced_chunk(tmp_path): ...
def test_call_gemini_rejects_malformed_api_payload_shapes(monkeypatch): ...
def test_cli_run_skips_cleanly_without_gemini_api_key(monkeypatch, capsys): ...
def test_cli_run_catches_malformed_model_output_and_stays_advisory(tmp_path, monkeypatch, capsys): ...
def test_cli_run_prints_findings_and_stays_advisory(tmp_path, monkeypatch, capsys): ...
def test_cli_verify_findings_uses_verify_model_when_enabled(tmp_path, monkeypatch, capsys): ...
def test_cli_verify_findings_defaults_to_detector_model(tmp_path, monkeypatch, capsys): ...
def test_cli_verify_model_does_not_enable_verification_by_itself(tmp_path, monkeypatch): ...
```

Use inline `tmp_path` file writers. Monkeypatch the Gemini call function so tests never require network access or `GEMINI_API_KEY` except where intentionally checking the skip path.

- [ ] **Step 2: Run tests to verify they fail on missing module**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/lib/style_linter/ -q
```

Expected: failure due to missing `lib.style_lint` and missing repo-specific policy in `bin/linters/style_lint.py`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/lib/style_linter/
git commit -m "test: cover custom style lint contract"
git push
```

---

### Task 3: Implement Custom Style Linter

**Files:**
- Create: `lib/style_lint/types.py`
- Create: `lib/style_lint/source.py`
- Create: `lib/style_lint/detector.py`
- Create: `lib/style_lint/gemini.py`
- Create: `lib/style_lint/verifier.py`
- Create: `lib/style_lint/runner.py`
- Create: `lib/style_lint/__init__.py`
- Create: `lib/__init__.py`
- Create/modify: `bin/linters/style_lint.py`
- Test: `tests/lib/style_linter/`

- [ ] **Step 1: Add reusable rule, chunk, and finding models**

In `lib/style_lint/types.py`, define:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Rule:
    id: str
    description: str

@dataclass(frozen=True)
class SourceChunk:
    index: int
    path: Path
    line_start: int
    line_end: int
    numbered_source: str

@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    column: int
    rule_id: str
    message: str
```

Keep `lib/style_lint/__init__.py` limited to export control. Define `DEFAULT_MODEL`, `DEFAULT_PATHS`, `RULES`, and the evalspec policy instructions in `bin/linters/style_lint.py`, not in the reusable framework. Use these exact rule IDs: `no-suppression-comments`, `section-header-comments`, `provenance-comments`, `descriptive-names`, `dedented-multiline-strings`, `semantic-block-newlines`.

- [ ] **Step 2: Implement path collection**

Implement `collect_python_files(paths: list[Path] | None, *, default_paths: tuple[Path, ...]) -> list[Path]` so the caller owns default roots while explicit file or directory arguments scope the walk.

- [ ] **Step 3: Implement source chunking instead of deterministic rule extraction**

Implement `chunk_source_files(paths: list[Path], *, max_lines: int = 120) -> list[SourceChunk]` so Gemini receives source snippets with stable chunk indexes and numbered source lines. Do not keep hand-rolled AST/token rule detectors in the framework; the model receives source chunks and rule definitions directly.

- [ ] **Step 4: Implement Gemini prompt, call, parse, and optional verification**

Implement:

```python
def build_detector_prompt(*, chunks: list[SourceChunk], rules: list[Rule], instructions: str) -> str: ...
def call_gemini(*, prompt: str, api_key: str, model: str) -> str: ...
def parse_findings(response: str, *, chunks: list[SourceChunk], rules: list[Rule]) -> list[Finding]: ...
def verify_findings(*, findings: list[Finding], chunks: list[SourceChunk], rules: list[Rule], instructions: str, api_key: str, model: str) -> list[Finding]: ...
def format_findings(findings: list[Finding]) -> list[str]: ...
```

Use strict JSON response expectations. Reject unknown rule IDs and malformed finding references with `ValueError`. Implement `call_gemini()` with stdlib `urllib.request` against the Gemini REST API, not a new SDK dependency, so `pyproject.toml` and `uv.lock` do not need Gemini package changes.

- [ ] **Step 5: Implement library runner API and CLI run path**

In `lib/style_lint/runner.py`, define `StyleLintConfig`, `StyleLintResult`, and:

```python
def run_advisory_lint(config: StyleLintConfig) -> StyleLintResult: ...
```

The runner owns the ordered framework pipeline: collect files, chunk source, build the prompt with the framework default system prompt plus caller policy, call Gemini, parse findings, optionally verify findings, format diagnostics, and return warnings instead of raising advisory model errors.

Implement `run(paths: list[Path] | None = None, *, model: str = DEFAULT_MODEL, verify_findings: bool = False, verify_model: str | None = None) -> int` and `main(argv: list[str] | None = None) -> int`.

Required behavior:
- if `GEMINI_API_KEY` is absent, print a clear skip message and return `0`;
- print findings as `path:line:col: rule-id message`;
- return `0` even when findings exist;
- catch Gemini transport errors, malformed JSON, unknown rule IDs, and malformed finding references; print one warning line and return `0` because `make lint:custom` is advisory;
- accept `--model`, `--verify-findings`, `--verify-model`, and optional path arguments.

- [ ] **Step 6: Add repository-specific CLI**

Create `bin/linters/style_lint.py` with evalspec-specific policy:

```python
"""Run repository-specific advisory style lint checks."""

from __future__ import annotations

from pathlib import Path

import lib.style_lint as style_lint

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PATHS = (Path("src"), Path("tests"), Path("evals"), Path("bin"), Path("lib"))
RULES = [...]
POLICY_INSTRUCTIONS = "..."

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Verify targeted tests pass**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/lib/style_linter/ -q
```

Expected: all tests in `tests/lib/style_linter/` pass.

- [ ] **Step 8: Commit**

```bash
git add lib bin/linters/style_lint.py tests/lib/style_linter/
git commit -m "feat: add advisory style linter"
git push
```

---

### Task 4: Wire Makefile and Smoke Commands

**Files:**
- Modify: `Makefile`
- Test: `tests/lib/style_linter/`

- [ ] **Step 1: Update phony targets and add `lint:custom`**

Change `.PHONY` to include `lint\:custom`, keep `lint` as Ruff-only, and add:

```make
lint\:custom:  ## Run custom advisory style checks
	uv run python bin/linters/style_lint.py
```

- [ ] **Step 2: Add Makefile contract tests**

In `tests/lib/style_linter/`, add tests that read `Makefile` and assert:

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
git add Makefile tests/lib/style_linter/
git commit -m "chore: add custom lint make target"
git push
```

---

### Task 5: Full Verification and Cleanup

**Files:**
- Modify only files needed to fix test, lint, or review findings.

- [ ] **Step 1: Run targeted tests**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/lib/style_linter/ -q
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

- Spec coverage: Ruff config, inline comments, Google docstrings, scoped baseline ignores to avoid a repo-wide rewrite, custom rule IDs, Gemini 3.1 Flash Lite default, stdlib Gemini REST transport, `GEMINI_API_KEY` skip behavior, optional verification gate/model, advisory exit semantics, default paths, and Makefile speed boundary are covered.
- Placeholder scan: no `TBD`, `TODO`, vague "add tests", or undefined task dependencies remain.
- Type consistency: `Rule`, `SourceChunk`, `Finding`, `collect_python_files`, `chunk_source_files`, `build_detector_prompt`, `parse_findings`, `verify_findings`, `format_findings`, `run`, and `main` names are consistent across tasks.
