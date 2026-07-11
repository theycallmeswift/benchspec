"""Classify discovered assertions deterministic/activation/judge-backed before a run.

The injected `bind` stub stands in for the real binder so no test touches the network;
each test pins one assertion shape to the label it must carry.
"""

from __future__ import annotations

import pytest

from evalspec import analyze
from evalspec.schema import SchemaError


def _bind_like_binder(text: object) -> object:
    """Bind stub mirroring the real binder for the three canonical assertion shapes."""
    if text == "Skill `ingest` invoked":
        return {"type": "deterministic", "checker": "skill_invoked", "skill": "ingest"}
    if text == "a file exists at ./notes.md":
        return {"type": "deterministic", "checker": "file_exists", "path": "./notes.md"}
    return None


def _write_eval(
    tmp_path: object, assertions: object, *, skill: object = "demo", slug: object = "a"
) -> object:
    """Write a minimal discoverable eval under a `skills/<skill>/evals/<slug>/` tree."""
    group_dir = tmp_path / "skills" / skill / "evals" / slug
    group_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {assertion}\n" for assertion in assertions)
    (group_dir / "eval.md").write_text(
        f"---\n{{}}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n{body}"
    )
    return group_dir


def test_classify_assertion_labels_activation() -> None:
    """Verify a bound skill_invoked spec classifies as activation."""
    label = analyze.classify_assertion("Skill `ingest` invoked", bind=_bind_like_binder)

    assert label == "activation"


def test_classify_assertion_labels_deterministic() -> None:
    """Verify a bound non-activation spec classifies as deterministic."""
    label = analyze.classify_assertion("a file exists at ./notes.md", bind=_bind_like_binder)

    assert label == "deterministic"


def test_classify_assertion_labels_judge_backed_on_punt() -> None:
    """Verify a punt (bind returns None) classifies as judge-backed."""
    label = analyze.classify_assertion("the summary is accurate", bind=_bind_like_binder)

    assert label == "judge-backed"


def test_analyze_repo_labels_each_assertion(tmp_path: object) -> None:
    """Verify analyze_repo yields one right-labeled Classification per assertion."""
    _write_eval(
        tmp_path,
        ["Skill `ingest` invoked", "a file exists at ./notes.md", "the summary is accurate"],
    )

    classifications = analyze.analyze_repo(tmp_path, bind=_bind_like_binder)

    assert [classification.label for classification in classifications] == [
        "activation",
        "deterministic",
        "judge-backed",
    ]


def test_analyze_repo_records_file_and_eval_id(tmp_path: object) -> None:
    """Verify each Classification carries its source file and eval id."""
    group_dir = _write_eval(tmp_path, ["the summary is accurate"], slug="alpha")

    classifications = analyze.analyze_repo(tmp_path, bind=_bind_like_binder)

    assert classifications[0].file == group_dir / "eval.md"
    assert classifications[0].eval_id == "alpha"


def test_run_returns_zero_on_clean_suite(tmp_path: object, monkeypatch: object) -> None:
    """Verify run classifies a non-empty suite and returns 0, unlike lint's warning exit.

    The assertions all bind through the binder's offline fast paths, so the real
    `binder.bind` classifies them without a network call.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked", "The file ./notes.md exists"])

    exit_code = analyze.run(tmp_path)

    assert exit_code == 0


def test_run_surfaces_schema_error_on_malformed_eval(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify a malformed eval surfaces a SchemaError rather than a nonzero-clean run."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    with pytest.raises(SchemaError):
        analyze.run(tmp_path)
