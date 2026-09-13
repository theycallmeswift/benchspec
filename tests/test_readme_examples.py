"""The README's and quickstart's embedded eval examples must parse under the live parser."""

from __future__ import annotations

import re
from pathlib import Path

from benchspec.specs.mdformat import parse_eval_md

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
QUICKSTART = REPO_ROOT / "docs" / "quickstart.md"

TRIGGER_LINE = "Skill `hello` invoked"
TRIGGER_CLAUSE = {"key": "if", "expr": "{BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}"}


def _embedded_eval_block(markdown: str) -> str:
    """Return a document's one fenced eval example (the ```markdown block with assertions)."""
    # The eval example is the fenced ```markdown block containing `## Assertions`.
    blocks = re.findall(r"```markdown\n(.*?)```", markdown, re.DOTALL)
    matches = [block for block in blocks if "## Assertions" in block]
    assert len(matches) == 1, f"expected exactly one eval example block, got {len(matches)}"
    return matches[0]


def _parse_embedded_eval(markdown: str, tmp_path: Path) -> dict:
    """Write a document's embedded eval example to `tmp_path/demo/eval.md` and parse it."""
    eval_dir = tmp_path / "demo"
    eval_dir.mkdir()
    (eval_dir / "eval.md").write_text(_embedded_eval_block(markdown), encoding="utf-8")
    return parse_eval_md(eval_dir / "eval.md")


def test_readme_eval_example_parses(tmp_path: Path) -> None:
    """Verify readme eval example parses."""
    block = _embedded_eval_block(README.read_text(encoding="utf-8"))

    eval_dir = tmp_path / "demo"
    eval_dir.mkdir()
    (eval_dir / "eval.md").write_text(block, encoding="utf-8")

    parse_eval_md(eval_dir / "eval.md")  # must not raise


def test_readme_eval_example_scopes_the_trigger_line(tmp_path: Path) -> None:
    """Verify the README's eval example scopes `Skill hello invoked` off the baseline arm."""
    parsed = _parse_embedded_eval(README.read_text(encoding="utf-8"), tmp_path)

    clauses_by_line = dict(zip(parsed["assertions"], parsed["clauses"], strict=True))

    assert TRIGGER_LINE in clauses_by_line
    assert clauses_by_line[TRIGGER_LINE] == TRIGGER_CLAUSE


def test_quickstart_eval_example_parses_and_scopes_the_trigger_line(tmp_path: Path) -> None:
    """Verify the quickstart's Step 3 eval parses and scopes the trigger line off the baseline."""
    parsed = _parse_embedded_eval(QUICKSTART.read_text(encoding="utf-8"), tmp_path)

    clauses_by_line = dict(zip(parsed["assertions"], parsed["clauses"], strict=True))

    assert TRIGGER_LINE in clauses_by_line
    assert clauses_by_line[TRIGGER_LINE] == TRIGGER_CLAUSE


def test_readme_filters_output_evals_by_group_or_eval_id() -> None:
    """Verify README filtering follows output-eval identity."""
    markdown = README.read_text(encoding="utf-8")

    assert "pytest -k greets-by-name" in markdown
    assert "pytest -k <skill-name>" not in markdown
