"""Deterministic checkers: pure functions over a workdir + pre-run SHAs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from benchspec.grading import checkers
from benchspec.grading.checkers import GradeContext


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A workdir seeded with two greeting notes, one carrying frontmatter."""
    (tmp_path / "Greetings").mkdir()
    (tmp_path / "Greetings" / "Alice.md").write_text("---\nstatus: archived\n---\nhi\n")
    (tmp_path / "Greetings" / "Bob.md").write_text("hello bob\n")
    return tmp_path


def _run(spec: dict, workdir: Path, original_shas: dict | None = None) -> dict:
    """Build the run test fixture."""
    return checkers.run_assertion(spec, workdir, original_shas or {})


def test_file_exists_pass_and_fail(workdir: Path) -> None:
    """Verify file exists pass and fail."""
    spec = {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "Greetings/Alice.md",
    }
    assert _run(spec, workdir)["passed"] is True
    spec = {"type": "deterministic", "checker": "file_exists", "path": "missing.md"}
    result = _run(spec, workdir)
    assert result["passed"] is False
    assert "absent" in result["evidence"]


def test_not_file_exists_passes_on_missing(workdir: Path) -> None:
    """Verify not_file_exists passes when the path is missing."""
    spec = {"type": "deterministic", "checker": "not_file_exists", "path": "missing.md"}
    assert _run(spec, workdir)["passed"] is True


def test_file_exists_matches_a_directory(workdir: Path) -> None:
    """Verify file exists matches a directory."""
    spec = {"type": "deterministic", "checker": "file_exists", "path": "Greetings"}
    assert _run(spec, workdir)["passed"] is True


def test_not_file_exists_fails_on_present_directory(workdir: Path) -> None:
    """Verify not_file_exists fails when the path is present as a directory."""
    spec = {"type": "deterministic", "checker": "not_file_exists", "path": "Greetings"}
    assert _run(spec, workdir)["passed"] is False


def test_relative_anchor_is_stripped(workdir: Path) -> None:
    """Verify relative anchor is stripped."""
    spec = {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "./Greetings/Alice.md",
    }
    assert _run(spec, workdir)["passed"] is True


def test_relative_anchor_preserves_hidden_path_component(workdir: Path) -> None:
    """Verify relative anchor preserves hidden path component."""
    hidden = workdir / ".meta" / "templates"
    hidden.mkdir(parents=True)
    (hidden / "entity-person.md").write_text("template")
    spec = {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "./.meta/templates/entity-person.md",
    }

    assert _run(spec, workdir)["passed"] is True


def test_path_escape_rejected(workdir: Path) -> None:
    """Verify path escape rejected."""
    spec = {"type": "deterministic", "checker": "file_exists", "path": "../outside.md"}
    with pytest.raises(ValueError, match="escapes"):
        _run(spec, workdir)


def test_glob_count_exact_and_min(workdir: Path) -> None:
    """Verify glob count exact and min."""
    spec = {
        "type": "deterministic",
        "checker": "glob_count",
        "glob": "Greetings/*.md",
        "count": 2,
    }
    assert _run(spec, workdir)["passed"] is True
    spec = {
        "type": "deterministic",
        "checker": "glob_count",
        "glob": "Greetings/*.md",
        "min": 3,
    }
    result = _run(spec, workdir)
    assert result["passed"] is False
    assert "2 match(es)" in result["evidence"]


def test_glob_count_rejects_escaping_pattern(workdir: Path) -> None:
    """Verify glob count rejects escaping pattern."""
    spec = {
        "type": "deterministic",
        "checker": "glob_count",
        "glob": "../*.md",
        "min": 1,
    }
    with pytest.raises(ValueError, match="escapes the workdir"):
        _run(spec, workdir)


def test_glob_count_rejects_absolute_pattern(workdir: Path) -> None:
    """Verify glob count rejects absolute pattern."""
    spec = {
        "type": "deterministic",
        "checker": "glob_count",
        "glob": "/etc/*.conf",
        "min": 1,
    }
    with pytest.raises(ValueError, match="escapes the workdir"):
        _run(spec, workdir)


def test_sha256_match_against_pre_run_original(workdir: Path) -> None:
    """Verify sha256 match against pre run original."""
    content = (workdir / "Greetings" / "Bob.md").read_bytes()
    original_shas = {"0. Inbox/bob.md": hashlib.sha256(content).hexdigest()}
    spec = {
        "type": "deterministic",
        "checker": "sha256_match",
        "path": "Greetings/Bob.md",
        "original": "0. Inbox/bob.md",
    }
    assert _run(spec, workdir, original_shas)["passed"] is True


def test_sha256_match_original_key_is_anchor_stripped(workdir: Path) -> None:
    """Verify sha256 match original key is anchor stripped."""
    # The pre-run SHA map is keyed by clean workdir-relative paths (no ./), but the
    # binder may emit `original` with a leading ./ — the lookup must canonicalize first.
    content = (workdir / "Greetings" / "Bob.md").read_bytes()
    original_shas = {"Greetings/Bob.md": hashlib.sha256(content).hexdigest()}
    spec = {
        "type": "deterministic",
        "checker": "sha256_match",
        "path": "Greetings/Bob.md",
        "original": "./Greetings/Bob.md",
    }
    assert _run(spec, workdir, original_shas)["passed"] is True


def test_sha256_match_unknown_original_fails_loud(workdir: Path) -> None:
    """Verify sha256 match unknown original fails loud."""
    spec = {
        "type": "deterministic",
        "checker": "sha256_match",
        "path": "Greetings/Bob.md",
        "original": "never/seeded.md",
    }
    result = _run(spec, workdir)
    assert result["passed"] is False
    assert "no pre-run SHA" in result["evidence"]


def test_sha256_match_literal(workdir: Path) -> None:
    """Verify sha256 match literal."""
    digest = hashlib.sha256((workdir / "Greetings" / "Bob.md").read_bytes()).hexdigest()
    spec = {
        "type": "deterministic",
        "checker": "sha256_match",
        "path": "Greetings/Bob.md",
        "sha256": digest,
    }
    assert _run(spec, workdir)["passed"] is True


def test_frontmatter_has_key_and_value(workdir: Path) -> None:
    """Verify frontmatter has key and value."""
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "Greetings/Alice.md",
        "key": "status",
    }
    assert _run(spec, workdir)["passed"] is True
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "Greetings/Alice.md",
        "key": "status",
        "value": "draft",
    }
    result = _run(spec, workdir)
    assert result["passed"] is False
    assert "archived" in result["evidence"]


def test_frontmatter_missing_block_fails(workdir: Path) -> None:
    """Verify frontmatter missing block fails."""
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "Greetings/Bob.md",
        "key": "status",
    }
    result = _run(spec, workdir)
    assert result["passed"] is False
    assert "no frontmatter" in result["evidence"]


def test_regex_match_quotes_evidence(workdir: Path) -> None:
    """Verify regex match quotes evidence."""
    (workdir / "log.md").write_text("## [2026-06-10T10:00] archive | My Note\n")
    spec = {
        "type": "deterministic",
        "checker": "regex",
        "path": "log.md",
        "pattern": r"^## \[.+\] archive \| ",
    }
    result = _run(spec, workdir)
    assert result["passed"] is True
    assert "archive |" in result["evidence"]


def test_result_shape_matches_grading_entries(workdir: Path) -> None:
    """Verify result shape matches grading entries."""
    spec = {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "Greetings/Alice.md",
    }
    result = _run(spec, workdir)
    assert set(result) == {"text", "passed", "evidence", "type"}
    assert result["type"] == "deterministic"
    assert result["text"] == "the file Greetings/Alice.md exists"  # derived


def test_explicit_text_overrides_derived(workdir: Path) -> None:
    """Verify explicit text overrides derived."""
    spec = {
        "type": "deterministic",
        "checker": "file_exists",
        "path": "Greetings/Alice.md",
        "text": "Alice got her greeting file",
    }
    assert _run(spec, workdir)["text"] == "Alice got her greeting file"


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("published", "true", True),  # YAML boolean vs author string "true"
        ("count", "3", True),  # YAML int vs author string "3"
        ("status", "archived", True),  # plain string still works
        ("status", "active", False),  # genuine mismatch still fails
    ],
)
def test_frontmatter_has_matches_typed_yaml_values(
    tmp_path: Path, key: str, value: str, expected: bool
) -> None:
    """Verify frontmatter has matches typed yaml values."""
    (tmp_path / "n.md").write_text("---\npublished: true\ncount: 3\nstatus: archived\n---\nbody\n")
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "n.md",
        "key": key,
        "value": value,
    }

    assert _run(spec, tmp_path)["passed"] is expected


def test_frontmatter_has_date_scalar_matches_string_value(tmp_path: Path) -> None:
    """Verify frontmatter has date scalar matches string value."""
    # frontmatter `created: 2026-06-13` (a YAML date), author value "2026-06-13" → passes
    (tmp_path / "dated.md").write_text("---\ncreated: 2026-06-13\n---\nbody\n")
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "dated.md",
        "key": "created",
        "value": "2026-06-13",
    }

    assert _run(spec, tmp_path)["passed"] is True


def test_frontmatter_empty_string_value_does_not_match_null(tmp_path: Path) -> None:
    """Verify frontmatter empty string value does not match null."""
    # When the author writes `value: ""` (bare empty string), the field arrives in
    # Python as "". Before the fix, _yaml_scalar("") returned None and falsely matched
    # a null frontmatter value; it must fall back to the raw string "" and NOT match.
    (tmp_path / "empty.md").write_text('---\nnote: ""\nnull_key:\n---\nbody\n')
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "empty.md",
        "key": "null_key",
        "value": "",
    }

    assert _run(spec, tmp_path)["passed"] is False


def test_frontmatter_empty_string_value_matches_explicit_empty(
    tmp_path: Path,
) -> None:
    """Verify frontmatter empty string value matches explicit empty."""
    # The same author value "" must match a key whose frontmatter value is an explicit
    # empty string.
    (tmp_path / "empty.md").write_text('---\nnote: ""\nnull_key:\n---\nbody\n')
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "empty.md",
        "key": "note",
        "value": "",
    }

    assert _run(spec, tmp_path)["passed"] is True


def test_frontmatter_has_tolerates_utf8_bom(tmp_path: Path) -> None:
    """Verify frontmatter has tolerates utf8 bom."""
    # A leading BOM must not hide the frontmatter block.
    (tmp_path / "bom.md").write_bytes(b"\xef\xbb\xbf---\nstatus: archived\n---\nbody\n")
    spec = {
        "type": "deterministic",
        "checker": "frontmatter_has",
        "path": "bom.md",
        "key": "status",
        "value": "archived",
    }
    assert checkers.run_assertion(spec, tmp_path, {})["passed"] is True


def test_regex_does_not_crash_on_non_utf8_bytes(tmp_path: Path) -> None:
    """Verify regex does not crash on non utf8 bytes."""
    # latin-1 bytes that are invalid UTF-8 must grade, not raise.
    (tmp_path / "bin.md").write_bytes(b"caf\xe9 latin-1\n")
    spec = {
        "type": "deterministic",
        "checker": "regex",
        "path": "bin.md",
        "pattern": "latin-1",
    }
    result = checkers.run_assertion(spec, tmp_path, {})
    assert result["passed"] is True  # the ASCII part still matches after lenient decode


def test_regex_tolerates_utf8_bom(tmp_path: Path) -> None:
    """Verify regex tolerates utf8 bom."""
    # A leading BOM must not prevent a regex match on the first line.
    (tmp_path / "bom.md").write_bytes(b"\xef\xbb\xbf# Title\n")
    spec = {
        "type": "deterministic",
        "checker": "regex",
        "path": "bom.md",
        "pattern": "^# Title",
    }
    assert checkers.run_assertion(spec, tmp_path, {})["passed"] is True


def test_assertion_text_handles_all_shapes() -> None:
    """Verify assertion text handles all shapes."""
    assert checkers.assertion_text("plain") == "plain"
    assert checkers.assertion_text({"type": "process", "text": "t"}) == "t"
    derived = checkers.assertion_text(
        {"type": "deterministic", "checker": "glob_count", "glob": "*.md", "min": 1}
    )
    assert "*.md" in derived


def test_skill_invoked_fired(tmp_path: Path) -> None:
    """Verify skill invoked fired."""
    spec = {"checker": "skill_invoked", "skill": "ingest"}
    out = checkers.run_assertion(
        spec,
        tmp_path,
        {},
        context=GradeContext(fired_skills=("knowledge-base:ingest",)),
    )
    assert out["passed"] is True
    assert out["type"] == "deterministic"


def test_skill_invoked_exact(tmp_path: Path) -> None:
    """Verify skill invoked exact."""
    out = checkers.run_assertion(
        {"checker": "skill_invoked", "skill": "ingest"},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("ingest",)),
    )
    assert out["passed"] is True


def test_skill_invoked_not_fired(tmp_path: Path) -> None:
    """Verify skill invoked not fired."""
    out = checkers.run_assertion(
        {"checker": "skill_invoked", "skill": "ingest"},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("archive",)),
    )
    assert out["passed"] is False


def test_not_skill_invoked_fails_on_firing_arm(tmp_path: Path) -> None:
    """Verify a negative assertion fails when the skill did fire."""
    out = checkers.run_assertion(
        {"checker": "not_skill_invoked", "skill": "ingest"},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("ingest",)),
    )

    assert out["passed"] is False


def test_not_skill_invoked_passes_on_non_firing_arm(tmp_path: Path) -> None:
    """Verify a negative assertion passes when the skill did not fire."""
    out = checkers.run_assertion(
        {"checker": "not_skill_invoked", "skill": "ingest"},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("archive",)),
    )

    assert out["passed"] is True


def test_skill_invoked_expected_true_fails_on_non_firing_arm(tmp_path: Path) -> None:
    """Verify an explicit positive assertion fails when the skill did not fire."""
    out = checkers.run_assertion(
        {"checker": "skill_invoked", "skill": "ingest", "expected": True},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("archive",)),
    )

    assert out["passed"] is False


def test_skill_invoked_namespaced_hit_passes_positive(tmp_path: Path) -> None:
    """Verify a namespaced fire (`plugin:ingest`) counts as a hit for the positive form."""
    out = checkers.run_assertion(
        {"checker": "skill_invoked", "skill": "ingest", "expected": True},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("plugin:ingest",)),
    )

    assert out["passed"] is True


def test_not_skill_invoked_namespaced_hit_fails_negative(tmp_path: Path) -> None:
    """Verify a namespaced fire (`plugin:ingest`) counts as a hit for the negative form."""
    out = checkers.run_assertion(
        {"checker": "not_skill_invoked", "skill": "ingest"},
        tmp_path,
        {},
        context=GradeContext(fired_skills=("plugin:ingest",)),
    )

    assert out["passed"] is False


def test_skill_invoked_no_context_is_negative(tmp_path: Path) -> None:
    """Verify skill invoked no context is negative."""
    # No runner wired fired_skills yet (inert path); absent context grades False, never crashes.
    out = checkers.run_assertion(
        {"checker": "skill_invoked", "skill": "ingest"}, tmp_path, {}, context=None
    )
    assert out["passed"] is False


def test_derive_text_skill_invoked_no_keyerror() -> None:
    """Verify derive text skill invoked no keyerror."""
    assert "ingest" in checkers.derive_text({"checker": "skill_invoked", "skill": "ingest"})


def test_existing_checker_ignores_context(tmp_path: Path) -> None:
    """Verify existing checker ignores context."""
    (tmp_path / "x.md").write_text("hi")
    out = checkers.run_assertion(
        {"checker": "file_exists", "path": "x.md"},
        tmp_path,
        {},
        context=GradeContext(()),
    )
    assert out["passed"] is True
