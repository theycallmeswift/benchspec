"""The README's embedded eval example must parse under the live mdformat parser."""

from __future__ import annotations

import re
from pathlib import Path

from benchspec.specs.mdformat import parse_eval_md

README = Path(__file__).resolve().parents[1] / "README.md"


def _embedded_eval_block(md: str) -> str:
    """Build the embedded eval block test fixture."""
    # The eval example is the fenced ```markdown block containing `## Assertions`.
    blocks = re.findall(r"```markdown\n(.*?)```", md, re.DOTALL)
    matches = [b for b in blocks if "## Assertions" in b]
    assert len(matches) == 1, f"expected exactly one eval example block, got {len(matches)}"
    return matches[0]


def test_readme_eval_example_parses(tmp_path: object) -> None:
    """Verify readme eval example parses."""
    block = _embedded_eval_block(README.read_text(encoding="utf-8"))

    eval_dir = tmp_path / "demo"
    eval_dir.mkdir()
    (eval_dir / "eval.md").write_text(block, encoding="utf-8")

    parse_eval_md(eval_dir / "eval.md")  # must not raise


def test_readme_filters_output_evals_by_group_or_eval_id() -> None:
    """Verify README filtering follows output-eval identity."""
    markdown = README.read_text(encoding="utf-8")

    assert "pytest -k greets-by-name" in markdown
    assert "pytest -k <skill-name>" not in markdown
