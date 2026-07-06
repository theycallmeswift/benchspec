"""Contract tests for the advisory custom style lint CLI."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _style_lint() -> ModuleType:
    """Import the style-lint module under test."""
    return importlib.import_module("evalspec.style_lint")


def test_run_skips_cleanly_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip advisory lint cleanly when the Gemini API key is absent."""
    style_lint = _style_lint()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = style_lint.run()

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GEMINI_API_KEY" in captured.out
    assert "skip" in captured.out.lower()


def test_collect_targets_defaults_to_src_tests_evals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect Python files from the default src, tests, and evals roots."""
    style_lint = _style_lint()
    src_file = tmp_path / "src/evalspec/example.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("value = 1\n")

    tests_file = tmp_path / "tests/test_example.py"
    tests_file.parent.mkdir(parents=True, exist_ok=True)
    tests_file.write_text("value = 2\n")

    evals_file = tmp_path / "evals/test_example.py"
    evals_file.parent.mkdir(parents=True, exist_ok=True)
    evals_file.write_text("value = 3\n")

    ignored_python = tmp_path / "tools/ignored.py"
    ignored_python.parent.mkdir(parents=True, exist_ok=True)
    ignored_python.write_text("value = 4\n")

    ignored_text = tmp_path / "src/evalspec/not_python.txt"
    ignored_text.parent.mkdir(parents=True, exist_ok=True)
    ignored_text.write_text("value = 5\n")

    monkeypatch.chdir(tmp_path)

    targets = style_lint.collect_targets()

    assert set(targets) == {evals_file, src_file, tests_file}


def test_collect_targets_respects_explicit_path_args(tmp_path: Path) -> None:
    """Scope target collection to explicit file and directory arguments."""
    style_lint = _style_lint()
    explicit_file = tmp_path / "custom/one.py"
    explicit_file.parent.mkdir(parents=True, exist_ok=True)
    explicit_file.write_text("value = 1\n")

    nested_file = tmp_path / "custom/pkg/two.py"
    nested_file.parent.mkdir(parents=True, exist_ok=True)
    nested_file.write_text("value = 2\n")

    default_file = tmp_path / "src/evalspec/default.py"
    default_file.parent.mkdir(parents=True, exist_ok=True)
    default_file.write_text("value = 3\n")

    targets = style_lint.collect_targets([explicit_file, tmp_path / "custom" / "pkg"])

    assert set(targets) == {explicit_file, nested_file}


def test_build_detector_prompt_includes_stable_rule_ids_and_descriptions(
    tmp_path: Path,
) -> None:
    """Include stable rule metadata and candidate details in detector prompts."""
    style_lint = _style_lint()
    candidate_path = tmp_path / "src/evalspec/example.py"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("x = 1\n")
    candidate = style_lint.Candidate(
        path=candidate_path,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )

    prompt = style_lint.build_detector_prompt([candidate])

    assert "descriptive-names" in prompt
    assert "x = 1" in prompt
    assert str(candidate_path) in prompt
    for rule in style_lint.RULES:
        assert rule.id in prompt
        assert rule.description in prompt


def test_parse_findings_rejects_unknown_rule_ids(tmp_path: Path) -> None:
    """Reject model findings that reference unknown rule identifiers."""
    style_lint = _style_lint()
    candidate_path = tmp_path / "src/evalspec/example.py"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=candidate_path,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="value = 1",
    )
    response = json.dumps(
        {
            "findings": [
                {
                    "candidate_index": 0,
                    "rule_id": "totally-unknown-rule",
                    "message": "This rule ID should be rejected.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="totally-unknown-rule"):
        style_lint.parse_findings(response, [candidate])


def test_parse_findings_rejects_rule_id_mismatches_candidate(tmp_path: Path) -> None:
    """Reject model findings that rewrite a candidate's deterministic rule ID."""
    style_lint = _style_lint()
    candidate_path = tmp_path / "src/evalspec/example.py"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=candidate_path,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="value = 1",
    )
    response = json.dumps(
        {
            "findings": [
                {
                    "candidate_index": 0,
                    "rule_id": "section-header-comments",
                    "message": "This should not be allowed.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="section-header-comments"):
        style_lint.parse_findings(response, [candidate])


def test_parse_findings_rejects_boolean_candidate_indexes(tmp_path: Path) -> None:
    """Reject booleans where a candidate index integer is required."""
    style_lint = _style_lint()
    candidate_path = tmp_path / "src/evalspec/example.py"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=candidate_path,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="value = 1",
    )
    response = json.dumps(
        {
            "findings": [
                {
                    "candidate_index": False,
                    "rule_id": "descriptive-names",
                    "message": "This should not be allowed.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="Malformed finding reference"):
        style_lint.parse_findings(response, [candidate])


def test_call_gemini_rejects_malformed_api_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject partial Gemini payloads that omit the expected content shape."""
    style_lint = _style_lint()

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    malformed_payloads = [
        {"candidates": []},
        {"candidates": [{}]},
        {"candidates": [{"content": {}}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": ["text"]}}]},
        {"candidates": [{"content": {"parts": [{"text": 1}]}}]},
    ]

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    for payload in malformed_payloads:
        monkeypatch.setattr(
            style_lint.urllib.request,
            "urlopen",
            lambda request, *, payload=payload: _Response(payload),
        )

        with pytest.raises(ValueError, match="Gemini response"):
            style_lint.call_gemini("{}", model=style_lint.DEFAULT_MODEL)


def test_verify_findings_rejects_boolean_keep_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject booleans where verification keep indexes must be integers."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )
    finding = style_lint.Finding(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        message="Use a descriptive binding name.",
    )

    monkeypatch.setattr(
        style_lint,
        "call_gemini",
        lambda prompt, *, model: json.dumps({"keep_indexes": [False]}),
    )

    with pytest.raises(ValueError, match="Malformed finding reference"):
        style_lint.verify_findings(
            [finding],
            [candidate],
            verify_model="gemini-3.1-pro",
        )


def test_find_candidates_flags_single_letter_bindings_but_allows_unused_underscore(
    tmp_path: Path,
) -> None:
    """Flag single-letter names while allowing `_` as an unused binding."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
items = [1, 2, 3]
x = 1
for _ in items:
    pass
for q in items:
    pass
values = [item for item in items if item > 1]
""",
    )

    candidates = style_lint.find_candidates(source)
    descriptive_names = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "descriptive-names"
    ]

    assert {candidate.line for candidate in descriptive_names} == {2, 5}
    assert all(
        "for _ in items" not in candidate.text
        for candidate in descriptive_names
    )


def test_find_candidates_flags_suppression_comments(tmp_path: Path) -> None:
    """Flag inline suppression comments for lint and type checkers."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
import missing  # noqa: F401
value = maybe_bad()  # type: ignore[arg-type]
""",
    )

    candidates = style_lint.find_candidates(source)
    suppression_comments = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "no-suppression-comments"
    ]

    assert [candidate.line for candidate in suppression_comments] == [1, 2]


def test_find_candidates_flags_section_headers_but_not_why_comments(
    tmp_path: Path,
) -> None:
    """Flag section headers while preserving rationale comments."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
# --- parsing ---
value = 1
# why: upstream API 500s on cold start
other = 2
""",
    )

    candidates = style_lint.find_candidates(source)
    section_headers = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "section-header-comments"
    ]

    assert [candidate.line for candidate in section_headers] == [1]


def test_find_candidates_does_not_flag_normal_short_rationale_comments(
    tmp_path: Path,
) -> None:
    """Allow ordinary short comments that are not labels or dividers."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
# temporary workaround
value = 1
""",
    )

    candidates = style_lint.find_candidates(source)
    section_headers = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "section-header-comments"
    ]

    assert section_headers == []


def test_find_candidates_does_not_flag_all_caps_tag_comments(
    tmp_path: Path,
) -> None:
    """Allow bare all-caps tags that are not actual section headers."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
# TODO
# NOTE
# IMPORTANT
value = 1
""",
    )

    candidates = style_lint.find_candidates(source)
    section_headers = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "section-header-comments"
    ]

    assert section_headers == []


def test_find_candidates_flags_structural_and_title_style_region_labels(
    tmp_path: Path,
) -> None:
    """Flag wrapped or title-style region labels, not bare acronym comments."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
# --- PARSING ---
# Validation
# Setup
# API
# CLI
# JSON
# SQL
# UTC
value = 1
""",
    )

    candidates = style_lint.find_candidates(source)
    section_headers = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "section-header-comments"
    ]

    assert [candidate.line for candidate in section_headers] == [1, 2, 3]


def test_find_candidates_flags_provenance_comments_without_prefix_false_positives(
    tmp_path: Path,
) -> None:
    """Flag issue/PR references without misreading ordinary leading words."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
# problem statement for this branch
# process data in batches
# PR #123 fixes this edge case
# issue #456 tracks this follow-up
value = 1
""",
    )

    candidates = style_lint.find_candidates(source)
    provenance_comments = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "provenance-comments"
    ]

    assert [candidate.line for candidate in provenance_comments] == [3, 4]


def test_find_candidates_flags_indented_triple_quoted_strings_without_dedent(
    tmp_path: Path,
) -> None:
    """Flag indented triple-quoted call arguments that skip `dedent()`."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
def render_prompt() -> str:
    return build_message(
        \"\"\"
        hello
        world
        \"\"\"
    )
""",
    )

    candidates = style_lint.find_candidates(source)
    multiline_strings = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "dedented-multiline-strings"
    ]

    assert [candidate.line for candidate in multiline_strings] == [3]


def test_find_candidates_flags_assigned_indented_triple_quoted_strings(
    tmp_path: Path,
) -> None:
    """Flag assigned indented triple-quoted strings that embed whitespace."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text(
        """\
SYSTEM_PROMPT = \"\"\"
    first line
    second line
\"\"\"
""",
    )

    candidates = style_lint.find_candidates(source)
    multiline_strings = [
        candidate
        for candidate in candidates
        if candidate.rule_id == "dedented-multiline-strings"
    ]

    assert [candidate.line for candidate in multiline_strings] == [1]


def test_run_catches_malformed_model_output_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warn and return zero when the model returns malformed output."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint, "collect_targets", lambda paths=None: [source])
    monkeypatch.setattr(style_lint, "find_candidates", lambda path: [candidate])
    monkeypatch.setattr(style_lint, "call_gemini", lambda prompt, *, model: "{")

    exit_code = style_lint.run([source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "warning" in captured.out.lower()


def test_verify_model_is_not_called_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip verification when no verify model is configured."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )
    finding = style_lint.Finding(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        message="Use a descriptive binding name.",
    )
    verify_called = False

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint, "collect_targets", lambda paths=None: [source])
    monkeypatch.setattr(style_lint, "find_candidates", lambda path: [candidate])
    monkeypatch.setattr(
        style_lint,
        "detect_findings",
        lambda candidates, *, model: [finding],
    )

    def _verify(
        findings: list[Any],
        candidates: list[Any],
        *,
        verify_model: str | None,
    ) -> list[Any]:
        nonlocal verify_called
        verify_called = True
        return findings

    monkeypatch.setattr(style_lint, "verify_findings", _verify)

    exit_code = style_lint.run([source])

    assert exit_code == 0
    assert verify_called is False


def test_verify_model_can_filter_findings_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow the optional verification model to filter detector findings."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )
    first_finding = style_lint.Finding(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        message="Use a descriptive binding name.",
    )
    second_finding = style_lint.Finding(
        path=source,
        line=2,
        column=1,
        rule_id="section-header-comments",
        message="Extract the section into a named helper.",
    )
    seen_verify_model = None

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint, "collect_targets", lambda paths=None: [source])
    monkeypatch.setattr(style_lint, "find_candidates", lambda path: [candidate])
    monkeypatch.setattr(
        style_lint,
        "detect_findings",
        lambda candidates, *, model: [first_finding, second_finding],
    )

    def _verify(
        findings: list[Any],
        candidates: list[Any],
        *,
        verify_model: str | None,
    ) -> list[Any]:
        nonlocal seen_verify_model
        seen_verify_model = verify_model
        return [second_finding]

    monkeypatch.setattr(style_lint, "verify_findings", _verify)

    exit_code = style_lint.run([source], verify_model="gemini-3.1-pro")

    captured = capsys.readouterr()

    assert exit_code == 0
    assert seen_verify_model == "gemini-3.1-pro"
    assert "section-header-comments" in captured.out
    assert "descriptive-names" not in captured.out


def test_run_prints_findings_in_path_line_col_rule_format_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print advisory findings in path:line:col: rule-id message format."""
    style_lint = _style_lint()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    candidate = style_lint.Candidate(
        path=source,
        line=1,
        column=1,
        rule_id="descriptive-names",
        text="x = 1",
    )
    finding = style_lint.Finding(
        path=source,
        line=1,
        column=4,
        rule_id="descriptive-names",
        message="Use a descriptive binding name.",
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(style_lint, "collect_targets", lambda paths=None: [source])
    monkeypatch.setattr(style_lint, "find_candidates", lambda path: [candidate])
    monkeypatch.setattr(
        style_lint,
        "detect_findings",
        lambda candidates, *, model: [finding],
    )

    exit_code = style_lint.run([source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert (
        f"{source}:1:4: descriptive-names Use a descriptive binding name."
        in captured.out
    )
