"""Happy-path eval coverage for the reusable style-lint framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lib.style_lint as style_lint

RULE_EVAL_CASES = [
    pytest.param(
        "no-suppression-comments",
        "value = 1  # noqa: F401\n",
        "Remove lint suppression comments.",
        id="no-suppression-comments",
    ),
    pytest.param(
        "section-header-comments",
        "# ---- parsing ----\nvalue = 1\n",
        "Extract section-header comments into named structure.",
        id="section-header-comments",
    ),
    pytest.param(
        "provenance-comments",
        "value = 1  # added for PR #8\n",
        "Remove external provenance from source comments.",
        id="provenance-comments",
    ),
    pytest.param(
        "descriptive-names",
        "x = 1\n",
        "Use a descriptive binding name.",
        id="descriptive-names",
    ),
    pytest.param(
        "dedented-multiline-strings",
        'prompt = """\n    Review this source.\n"""\n',
        "Use textwrap.dedent for indented multiline strings.",
        id="dedented-multiline-strings",
    ),
    pytest.param(
        "semantic-block-newlines",
        "if ready:\n    prepare()\nprocess()\n",
        "Use a blank line between semantic blocks.",
        id="semantic-block-newlines",
    ),
]


@pytest.mark.parametrize(
    ("rule_id", "source_text", "message"),
    RULE_EVAL_CASES,
)
def test_style_linter_reports_each_rule_finding(
    rule_id: str,
    source_text: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one advisory lint pass per configured rule with a stubbed model."""
    source = tmp_path / "sample.py"
    source.write_text(source_text)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": rule_id,
                    "message": message,
                }
            ]
        }
    )

    monkeypatch.setattr(style_lint, "call_gemini", lambda **_kwargs: response)

    result = style_lint.run_advisory_lint(
        style_lint.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[style_lint.Rule(id=rule_id, description=f"Eval for {rule_id}.")],
            policy_instructions="Apply the local style guide.",
            api_key="test-key",
            model="gemini-test",
        )
    )

    assert result.warning is None
    assert result.diagnostics == [f"{source.resolve()}:1:1: {rule_id} {message}"]
