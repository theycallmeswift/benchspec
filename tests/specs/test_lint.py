"""Lint rules over assertion text.

Rules are heuristics: each test pins one true-positive and one near-miss that must not
fire.
"""

from __future__ import annotations

from evalspec.specs import lint


def _rules(text: object) -> object:
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
    tmp_path: object, assertions: object, *, skill: object = "demo", slug: object = "a"
) -> object:
    """Write eval."""
    group_dir = tmp_path / "skills" / skill / "evals" / slug
    group_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {a}\n" for a in assertions)
    (group_dir / "eval.md").write_text(
        f"---\n{{}}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n{body}"
    )
    return group_dir


def test_lint_repo_walks_suites(tmp_path: object) -> None:
    """Verify lint repo walks suites."""
    group_dir = _write_eval(tmp_path, ["the agent properly handles the edge case"])

    findings = lint.lint_repo(tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "vague-adverb"
    assert findings[0].eval_id == "a"
    assert findings[0].file == group_dir / "eval.md"


def test_main_exit_codes(tmp_path: object, capsys: object) -> None:
    """Verify main exit codes."""
    _write_eval(tmp_path, ["the output names the source file"])
    assert lint.run(tmp_path) == 0

    _write_eval(tmp_path, ["gracefully handles everything"])
    assert lint.run(tmp_path) == 1
    assert "vague-adverb" in capsys.readouterr().out
