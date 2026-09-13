"""Lint rules over assertion text.

Rules are heuristics: each test pins one true-positive and one near-miss that must not
fire.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchspec.specs import lint


def _rules(text: str) -> list[str]:
    """Build the rules test fixture."""
    return [finding[0] for finding in lint.lint_assertion(text)]


def test_vague_adverb_flagged() -> None:
    """Verify vague adverb flagged."""
    assert "vague-adverb" in _rules("the agent explicitly acknowledges the limit")
    assert "vague-adverb" not in _rules("the file names the limit: 50 items")


def test_unseen_file_flagged() -> None:
    """Verify unseen file flagged."""
    assert "unseen-file" in _rules("respects references/skill-conventions.md")
    # a bare workdir-relative path is not anchored — the judge can't tell it is a
    # workdir fact, so it warns
    assert "unseen-file" in _rules("the capture lives at 9. Archive/Sources/note.md")
    # `./`-anchored paths are workdir facts the judge does see
    assert "unseen-file" not in _rules("the capture lives at ./9. Archive/Sources/{TODAY}/note.md")
    # a quoted `./` anchor (the common authored form) also stands the rule down
    assert "unseen-file" not in _rules(
        "the entity page './2. Entities/anthropic.md' was updated in place"
    )


def test_relative_claim_without_comparand_flagged() -> None:
    """Verify relative claim without comparand flagged."""
    assert "relative-claim" in _rules("the summary is clearer and better organized")
    assert "relative-claim" not in _rules("the summary is shorter than the original capture")


def test_existence_assertion_yields_no_findings() -> None:
    """Verify existence assertion yields no findings."""
    # the leading `./` anchors it as a workdir fact, so `unseen-file` stands
    # down and nothing else matches.
    assert lint.lint_assertion("The file ./Greetings/Alice.md exists") == []


def _write_eval(
    tmp_path: Path, assertions: list[str], *, skill: str = "demo", slug: str = "a"
) -> Path:
    """Write eval."""
    group_dir = tmp_path / "skills" / skill / "evals" / slug
    group_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {assertion}\n" for assertion in assertions)
    (group_dir / "eval.md").write_text(
        f"---\n{{}}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n{body}"
    )
    return group_dir


def test_lint_repo_walks_suites(tmp_path: Path) -> None:
    """Verify lint repo walks suites."""
    group_dir = _write_eval(tmp_path, ["the agent properly handles the edge case"])

    findings = lint.lint_repo(tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "vague-adverb"
    assert findings[0].eval_id == "a"
    assert findings[0].file == group_dir / "eval.md"


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify main exit codes."""
    _write_eval(tmp_path, ["the output names the source file"])
    assert lint.run(tmp_path) == 0

    _write_eval(tmp_path, ["gracefully handles everything"])
    assert lint.run(tmp_path) == 1
    assert "vague-adverb" in capsys.readouterr().out


def _clause_rules(text: str, clause: dict | None) -> list[str]:
    """The rule ids `lint_clause` fires for one assertion and its clause."""
    return [finding[0] for finding in lint.lint_clause(text, clause)]


def test_constant_clause_flagged() -> None:
    """Verify a clause that reads no `{VAR}` warns, and one that does stays quiet."""
    variable_clause = {"key": "if", "expr": '{BENCHSPEC_ARM} == "trial"'}

    assert _clause_rules("./out.md exists", {"key": "if", "expr": "true"}) == ["constant-clause"]
    assert _clause_rules("./out.md exists", variable_clause) == []


def test_constant_clause_message_names_the_clause() -> None:
    """Verify the warning quotes the clause text so the author can find the line."""
    findings = lint.lint_clause("./out.md exists", {"key": "unless", "expr": "1 == 1"})

    assert findings[0][0] == "constant-clause"
    assert "`unless: 1 == 1`" in findings[0][1]


def test_untagged_trigger_flagged() -> None:
    """Verify a bare `Skill ... invoked` line warns and the suggested clause is in the message."""
    findings = lint.lint_clause("Skill `hello` invoked", None)

    assert [rule for rule, _message in findings] == ["untagged-trigger"]
    assert "- if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}" in findings[0][1]


def test_untagged_trigger_not_invoked_form_flagged() -> None:
    """Verify the negated trigger line is untagged too."""
    assert _clause_rules("Skill `hello` not invoked", None) == ["untagged-trigger"]


def test_tagged_trigger_is_clean() -> None:
    """Verify a trigger line carrying a variable clause fires neither rule."""
    clause = {"key": "if", "expr": "{BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}"}

    assert _clause_rules("Skill `hello` invoked", clause) == []


def test_non_trigger_line_without_a_clause_is_clean() -> None:
    """Verify an ordinary unclaused assertion fires no clause rule."""
    assert _clause_rules("Skill `hello` was mentioned in the reply", None) == []


def test_lint_repo_zips_clauses_onto_assertions(tmp_path: Path) -> None:
    """Verify `lint_repo` reads each line's clause from the eval file."""
    group_dir = tmp_path / "skills" / "demo" / "evals" / "scoped"
    group_dir.mkdir(parents=True)
    (group_dir / "eval.md").write_text(
        "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n"
        "- [ ] Skill `hello` invoked\n"
        "  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}\n"
        "- [ ] ./out.md exists\n"
        "  - if: false\n"
    )

    findings = lint.lint_repo(tmp_path)

    assert [(finding.assertion, finding.rule) for finding in findings] == [
        ("./out.md exists", "constant-clause")
    ]
