# Issue 16 Docstring Style-Lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exempt docstrings from the `dedented-multiline-strings` advisory rule so ordinary indented docstrings stop producing verified false positives.

**Architecture:** Keep the fix narrow. Update the rule description in `bin/linters/style_lint.py` because that text is serialized into both detector and verifier prompts. Add tests that pin the exact rule text and confirm both prompts carry the exemption. Sync the quoted rule literals in the specs so the documentation matches the shipped policy.

**Tech Stack:** Python, `pytest`, `uv`, existing `bin/linters/style_lint.py` CLI, `docs/specs/2026-07-05-style-lint-rules.md`.

---

### Task 1: Add Failing Coverage For The New Rule Wording

**Files:**
- Modify: `tests/lib/style_linter/test_cli.py`
- Modify: `tests/lib/style_linter/test_detector.py`
- Create: `tests/lib/style_linter/test_verifier.py`

- [ ] **Step 1: Tighten the CLI rule-text assertion**

Add an exact description assertion for `dedented-multiline-strings` in `test_cli_owns_repo_specific_rules_prompt_and_default_paths`:

```python
rules = {rule.id: rule.description for rule in style_lint_cli.RULES}
assert rules["dedented-multiline-strings"] == (
    "Use textwrap.dedent for indented multiline string values. "
    "Docstrings are exempt — indented multiline docstrings are correct."
)
```

- [ ] **Step 2: Tighten detector-prompt coverage**

Add a new detector test that passes the `dedented-multiline-strings` rule directly to `build_detector_prompt` and asserts that the generated prompt includes both the in-scope wording and the exemption:

```python
def test_build_detector_prompt_includes_docstring_exemption(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    """Serialize caller-owned docstring exemptions into detector prompts."""
    source = tmp_path / "sample.py"
    source.write_text('"""Module docstring."""\n')
    chunks = framework.chunk_source_files([source], max_lines=80)

    prompt = framework.build_detector_prompt(
        chunks=chunks,
        rules=[
            framework.Rule(
                id="dedented-multiline-strings",
                description=(
                    "Use textwrap.dedent for indented multiline string values. "
                    "Docstrings are exempt — indented multiline docstrings are correct."
                ),
            )
        ],
        instructions="Use the repository style guide.",
    )

    assert "indented multiline string values" in prompt
    assert "Docstrings are exempt" in prompt
```

- [ ] **Step 3: Add verifier-prompt coverage**

Create `tests/lib/style_linter/test_verifier.py` with a test that stubs `framework.call_gemini`, invokes `verify_findings`, and asserts the captured verifier prompt includes both the in-scope wording and exemption:

```python
"""Tests for second-pass advisory lint verification."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


def test_verify_findings_includes_docstring_exemption_in_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Serialize caller-owned docstring exemptions into verifier prompts."""
    source = tmp_path / "sample.py"
    source.write_text('"""Module docstring."""\n')
    chunks = framework.chunk_source_files([source], max_lines=80)
    finding = framework.Finding(
        path=source.resolve(),
        line=1,
        column=1,
        rule_id="dedented-multiline-strings",
        message="Use textwrap.dedent.",
    )
    captured_prompts: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        captured_prompts.append(str(kwargs["prompt"]))
        return json.dumps({"keep_indexes": []})

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    framework.verify_findings(
        findings=[finding],
        chunks=chunks,
        rules=[
            framework.Rule(
                id="dedented-multiline-strings",
                description=(
                    "Use textwrap.dedent for indented multiline string values. "
                    "Docstrings are exempt — indented multiline docstrings are correct."
                ),
            )
        ],
        instructions="Use the repository style guide.",
        api_key="test-key",
        model="gemini-test",
    )

    assert len(captured_prompts) == 1
    assert "indented multiline string values" in captured_prompts[0]
    assert "Docstrings are exempt" in captured_prompts[0]
```

- [ ] **Step 4: Run the targeted tests and confirm they fail first**

Run:

```bash
uv run pytest tests/lib/style_linter/test_cli.py::test_cli_owns_repo_specific_rules_prompt_and_default_paths \
  tests/lib/style_linter/test_detector.py::test_build_detector_prompt_includes_docstring_exemption \
  tests/lib/style_linter/test_verifier.py::test_verify_findings_includes_docstring_exemption_in_prompt -q
```

Expected: FAIL with an assertion diff showing the current `dedented-multiline-strings` text is missing the docstring exemption.

- [ ] **Step 5: Commit the red tests**

Commit point:

```bash
git add tests/lib/style_linter/test_cli.py tests/lib/style_linter/test_detector.py tests/lib/style_linter/test_verifier.py
git commit -m "test: pin docstring exemption in style lint rules"
```

---

### Task 2: Update The Shipped Rule Description

**Files:**
- Modify: `bin/linters/style_lint.py:54`

- [ ] **Step 1: Change only the `dedented-multiline-strings` description**

Replace the current rule text with:

```python
style_lint.Rule(
    id="dedented-multiline-strings",
    description=(
        "Use textwrap.dedent for indented multiline string values. "
        "Docstrings are exempt — indented multiline docstrings are correct."
    ),
),
```

- [ ] **Step 2: Re-run the focused tests**

Run:

```bash
uv run pytest tests/lib/style_linter/test_cli.py::test_cli_owns_repo_specific_rules_prompt_and_default_paths \
  tests/lib/style_linter/test_detector.py::test_build_detector_prompt_includes_docstring_exemption \
  tests/lib/style_linter/test_verifier.py::test_verify_findings_includes_docstring_exemption_in_prompt -q
```

Expected: PASS. The CLI rule registry and detector prompt both carry the updated wording.

- [ ] **Step 3: Commit the code change**

Commit point:

```bash
git add bin/linters/style_lint.py
git commit -m "fix: exempt docstrings from dedented multiline rule"
```

---

### Task 3: Sync The Rules Spec And Verify End To End

**Files:**
- Modify: `docs/specs/2026-07-05-style-lint-rules.md:91`
- Modify: `docs/specs/2026-07-09-style-lint-docstring-exemption.md`

- [ ] **Step 1: Update the quoted spec literal**

Change the quoted rule block to match the shipped code exactly:

```python
Rule(
    id="dedented-multiline-strings",
    description=(
        "Use textwrap.dedent for indented multiline string values. "
        "Docstrings are exempt — indented multiline docstrings are correct."
    ),
),
```

- [ ] **Step 2: Run the repository-required verification commands**

Run:

```bash
make test
make lint
```

Expected: both pass.

- [ ] **Step 3: Verify the style-lint behavior against the known false-positive shape**

If `GEMINI_API_KEY` is available, run:

```bash
uv run python bin/linters/style_lint.py --verify-findings src/evalspec/agents/claude.py
```

Expected: no surviving `dedented-multiline-strings` finding for the docstring-only case.

- [ ] **Step 4: Commit the docs sync**

Commit point:

```bash
git add docs/specs/2026-07-05-style-lint-rules.md docs/specs/2026-07-09-style-lint-docstring-exemption.md
git commit -m "docs: sync style lint rule text with docstring exemption"
```

---

### Notes

- No framework changes are needed in `lib/style_lint/`; the existing detector/verifier pipeline already serializes `Rule.description` verbatim.
- No CLI surface changes are expected.
- The plan intentionally keeps the fix to one rule string plus matching tests and docs.
