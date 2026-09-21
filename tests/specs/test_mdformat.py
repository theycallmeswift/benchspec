"""Markdown eval format: parse into the dict shape schema._validate checks."""

from __future__ import annotations

import os
import signal
import textwrap
from pathlib import Path
from types import FrameType
from typing import NoReturn

import pytest

from benchspec.orchestration.room import render_history
from benchspec.specs import mdformat, schema


def _write_eval_file(tmp_path: Path, group: str, filename: str, body: str) -> Path:
    """Write one evals/<group>/<filename>."""
    group_dir = tmp_path / group
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / filename).write_text(textwrap.dedent(body), encoding="utf-8")
    return group_dir / filename


def _write_slug(tmp_path: Path, slug: str, body: str) -> Path:
    """Write one eval.md whose parent folder (and derived id) is `slug`."""
    return _write_eval_file(tmp_path, slug, "eval.md", body)


def test_parse_eval_md_id_from_folder_for_eval_md(tmp_path: Path) -> None:
    """Verify parse eval md id from folder for eval.md."""
    path = _write_eval_file(
        tmp_path,
        "summarize-transcript",
        "eval.md",
        "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n",
    )

    ev = mdformat.parse_eval_md(path)

    assert ev["id"] == "summarize-transcript"


def test_parse_eval_md_id_from_stem_for_dot_eval_md(tmp_path: Path) -> None:
    """Verify parse eval md id from stem for *.eval.md."""
    path = _write_eval_file(
        tmp_path,
        "to-spec-activation",
        "write-spec.eval.md",
        "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n",
    )

    ev = mdformat.parse_eval_md(path)

    assert ev["id"] == "write-spec"


def test_parse_eval_md_rejects_non_eval_filename(tmp_path: Path) -> None:
    """Verify parse eval md rejects non-eval filename."""
    path = _write_eval_file(
        tmp_path, "g", "prompt.md", "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n"
    )

    with pytest.raises(mdformat.MdFormatError, match="eval.md"):
        mdformat.parse_eval_md(path)


def test_parse_eval_md_minimal(tmp_path: Path) -> None:
    """Verify parse eval md minimal."""
    eval_path = _write_slug(
        tmp_path,
        "single-article",
        """\
        ---
        {}
        ---

        ## Prompt

        Use the `ingest` skill to add ./x.md.

        Second paragraph survives verbatim.

        ## Assertions

        - [ ] Skill `ingest` invoked
        - [ ] a Resource page was created

        ### Log

        - [ ] the activity log names the move
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)

    assert ev["id"] == "single-article"
    assert ev["prompt"].startswith("Use the `ingest` skill")
    assert "Second paragraph" in ev["prompt"]
    assert ev["assertions"] == [
        "Skill `ingest` invoked",
        "a Resource page was created",
        "the activity log names the move",  # H3 group flattened in order
    ]
    assert "history" not in ev


def test_parse_eval_md_with_history(tmp_path: Path) -> None:
    """Verify parse eval md with history."""
    eval_path = _write_slug(
        tmp_path,
        "catch-all-pose",
        """\
        ---
        history:
          - role: user
            content: scope my plan
          - role: assistant
            content: which part?
        ---

        ## Prompt

        The deeper question.

        ## Assertions

        - [ ] Skill `interview-me` invoked
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)

    assert ev["history"] == [
        {"role": "user", "content": "scope my plan"},
        {"role": "assistant", "content": "which part?"},
    ]


def test_parse_eval_md_with_history_path_reads_raw_jsonl_lines(tmp_path: Path) -> None:
    """Verify a history path stores non-blank JSONL lines without normalization."""
    eval_path = _write_slug(
        tmp_path,
        "captured-session",
        """\
        ---
        history: ./session.jsonl
        ---

        ## Prompt

        Continue the session.

        ## Assertions

        - [ ] the session continued
    """,
    )
    transcript_lines = [
        '  {"type":"assistant","message":{"content":"first"}}  ',
        '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}',
    ]
    (eval_path.parent / "session.jsonl").write_text(
        f"{transcript_lines[0]}\n\n   \n{transcript_lines[1]}\n", encoding="utf-8"
    )

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["history"] == transcript_lines


def test_parse_eval_md_history_path_splits_records_only_on_lf(tmp_path: Path) -> None:
    """Verify a raw Unicode line separator stays inside its JSONL record."""
    eval_path = _write_slug(
        tmp_path,
        "unicode-line-separator",
        """\
        ---
        history: session.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    transcript_line = '{"content":"a\u2028b"}'
    (eval_path.parent / "session.jsonl").write_text(transcript_line + "\n", encoding="utf-8")

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["history"] == [transcript_line]
    assert render_history(ev["history"]) == (
        f"<transcript>\n{transcript_line}\n</transcript>\n\n"
    )


@pytest.mark.skipif(
    not hasattr(os, "mkfifo") or not hasattr(signal, "SIGALRM"),
    reason="requires POSIX FIFOs and alarm signals",
)
def test_parse_eval_md_rejects_fifo_history_target_without_blocking(tmp_path: Path) -> None:
    """Verify a FIFO history target fails promptly as a non-regular file."""
    eval_path = _write_slug(
        tmp_path,
        "fifo-transcript",
        """\
        ---
        history: session.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    history_path = eval_path.parent / "session.jsonl"
    os.mkfifo(history_path)

    def fail_on_timeout(signal_number: int, frame: FrameType | None) -> NoReturn:
        """Fail instead of letting a FIFO read hang the test process."""
        raise AssertionError("history FIFO validation blocked")

    previous_handler = signal.signal(signal.SIGALRM, fail_on_timeout)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        with pytest.raises(schema.SchemaError) as exc_info:
            mdformat.parse_eval_md(eval_path)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

    assert str(eval_path) in str(exc_info.value)
    assert "session.jsonl" in str(exc_info.value)


def test_parse_eval_md_wraps_nul_history_path_error(tmp_path: Path) -> None:
    """Verify a NUL path becomes a location-rich collection error."""
    eval_path = _write_slug(
        tmp_path,
        "nul-transcript-path",
        """\
        ---
        history: "foo\\0.jsonl"
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    history_path = "foo\0.jsonl"

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert repr(history_path) in str(exc_info.value)


def test_parse_eval_md_rejects_non_jsonl_history_path(tmp_path: Path) -> None:
    """Verify history path files require the documented JSONL suffix."""
    eval_path = _write_slug(
        tmp_path,
        "wrong-transcript-suffix",
        """\
        ---
        history: session.json
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    (eval_path.parent / "session.json").write_text('{}\n', encoding="utf-8")

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert "session.json" in str(exc_info.value)


@pytest.mark.parametrize("history", ["7", "{format: jsonl}"])
def test_parse_eval_md_rejects_unsupported_history_types(
    tmp_path: Path, history: str
) -> None:
    """Verify history type dispatch rejects every form other than list or path."""
    eval_path = _write_slug(
        tmp_path,
        "bad-history-type",
        f"""\
        ---
        history: {history}
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert "expected list or string path" in str(exc_info.value)


@pytest.mark.parametrize("history_path", ["../outside.jsonl", "nested/../../outside.jsonl"])
def test_parse_eval_md_rejects_history_path_escape(
    tmp_path: Path, history_path: str
) -> None:
    """Verify resolved parent traversals cannot escape the eval folder."""
    eval_path = _write_slug(
        tmp_path,
        "path-escape",
        f"""\
        ---
        history: {history_path}
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    (eval_path.parent.parent / "outside.jsonl").write_text('{}\n', encoding="utf-8")

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert history_path in str(exc_info.value)
    assert "outside the eval folder" in str(exc_info.value)


def test_parse_eval_md_rejects_history_symlink_escape(tmp_path: Path) -> None:
    """Verify a transcript symlink cannot resolve outside the eval folder."""
    eval_path = _write_slug(
        tmp_path,
        "symlink-escape",
        """\
        ---
        history: session.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")
    (eval_path.parent / "session.jsonl").symlink_to(outside)

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert "session.jsonl" in str(exc_info.value)
    assert "outside the eval folder" in str(exc_info.value)


def test_parse_eval_md_allows_history_symlink_inside_eval_folder(tmp_path: Path) -> None:
    """Verify an in-folder transcript symlink resolves and renders."""
    eval_path = _write_slug(
        tmp_path,
        "symlink-inside",
        """\
        ---
        history: session.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    transcript_line = '{"role":"user","content":"captured"}'
    (eval_path.parent / "capture.jsonl").write_text(transcript_line + "\n", encoding="utf-8")
    (eval_path.parent / "session.jsonl").symlink_to("capture.jsonl")

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["history"] == [transcript_line]
    assert render_history(ev["history"]) == (
        f"<transcript>\n{transcript_line}\n</transcript>\n\n"
    )


def test_parse_eval_md_rejects_first_invalid_jsonl_line(tmp_path: Path) -> None:
    """Verify transcript validation names its path and first invalid source line."""
    eval_path = _write_slug(
        tmp_path,
        "malformed-transcript",
        """\
        ---
        history: transcript.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    (eval_path.parent / "transcript.jsonl").write_text(
        '{"valid":1}\n\n{"broken":}\nnot-json\n', encoding="utf-8"
    )

    with pytest.raises(schema.SchemaError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    message = str(exc_info.value)
    assert str(eval_path) in message
    assert "transcript.jsonl" in message
    assert "line 3" in message
    assert "line 4" not in message


def test_empty_history_file_matches_omitted_history_rendering(tmp_path: Path) -> None:
    """Verify an empty transcript produces no prefix, exactly like no history."""
    eval_path = _write_slug(
        tmp_path,
        "empty-transcript",
        """\
        ---
        history: transcript.jsonl
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )
    (eval_path.parent / "transcript.jsonl").write_text("", encoding="utf-8")

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["history"] == []
    assert render_history(ev["history"]) == render_history(None)


def test_parse_eval_md_malformed_history_rejected(tmp_path: Path) -> None:
    """Verify parse eval md malformed history rejected."""
    # A bare parse_eval_md call must validate history itself, not defer to discovery.
    eval_path = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        history:
          - role: user
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] x
    """,
    )
    with pytest.raises(schema.SchemaError, match="history"):
        mdformat.parse_eval_md(eval_path)


def test_parse_eval_md_rejects_bare_string_inline_history_turn(tmp_path: Path) -> None:
    """Verify list dispatch keeps rejecting malformed inline turns."""
    eval_path = _write_slug(
        tmp_path,
        "bare-history-turn",
        """\
        ---
        history:
          - set up my vault
        ---

        ## Prompt

        Continue.

        ## Assertions

        - [ ] continued
    """,
    )

    with pytest.raises(schema.SchemaError, match=r"history\[0\].*expected object"):
        mdformat.parse_eval_md(eval_path)


def test_parse_eval_md_unknown_frontmatter_key_rejected(tmp_path: Path) -> None:
    """Verify parse eval md unknown frontmatter key rejected."""
    eval_path = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        id: a
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] x
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="id"):
        mdformat.parse_eval_md(eval_path)


def test_parse_eval_md_missing_prompt_rejected(tmp_path: Path) -> None:
    """Verify parse eval md missing prompt rejected."""
    eval_path = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        {}
        ---

        ## Assertions

        - [ ] x
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="Prompt"):
        mdformat.parse_eval_md(eval_path)


def test_parse_eval_md_missing_assertions_rejected(tmp_path: Path) -> None:
    """Verify parse eval md missing assertions rejected."""
    eval_path = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="Assertions"):
        mdformat.parse_eval_md(eval_path)


def test_continuation_lines_are_hard_errors(tmp_path: Path) -> None:
    """Verify continuation lines are hard errors."""
    eval_path = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] an assertion that
          wraps onto a second line
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="one assertion per line"):
        mdformat.parse_eval_md(eval_path)


def test_unknown_heading_is_a_hard_error(tmp_path: Path) -> None:
    """Verify unknown heading is a hard error."""
    eval_path = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertion

        - [ ] typo'd heading must not silently drop
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="Assertion"):
        mdformat.parse_eval_md(eval_path)


def test_plain_bullet_is_a_hard_error(tmp_path: Path) -> None:
    """Verify plain bullet is a hard error."""
    eval_path = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        - missing the checkbox
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match=r"- \[ \]"):
        mdformat.parse_eval_md(eval_path)


def test_bad_yaml_frontmatter_is_loud(tmp_path: Path) -> None:
    """Verify bad yaml frontmatter is loud."""
    eval_path = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        seed: unquoted: colon value
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] a
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="frontmatter"):
        mdformat.parse_eval_md(eval_path)


def test_mdformat_error_is_a_schema_error(tmp_path: Path) -> None:
    """Verify mdformat error is a schema error."""
    # discovery's error prefixing catches schema.SchemaError; Markdown structure
    # errors must ride the same channel.
    assert issubclass(mdformat.MdFormatError, schema.SchemaError)


def test_empty_assertions_section_is_an_error(tmp_path: Path) -> None:
    """Verify empty assertions section is an error."""
    eval_path = _write_slug(
        tmp_path,
        "empty-assertions",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions
    """,
    )
    with pytest.raises(schema.SchemaError, match="no checklist items"):
        mdformat.parse_eval_md(eval_path)


def test_stray_h3_outside_assertions_is_an_error(tmp_path: Path) -> None:
    """Verify stray h3 outside assertions is an error."""
    eval_path = _write_slug(
        tmp_path,
        "stray-h3",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ### Notes

        - [ ] this content would silently vanish

        ## Assertions

        - [ ] the real assertion
    """,
    )
    with pytest.raises(schema.SchemaError, match="outside an `## Assertions`"):
        mdformat.parse_eval_md(eval_path)


def test_h3_before_any_h2_is_an_error(tmp_path: Path) -> None:
    """Verify h3 before any h2 is an error."""
    # An H3 that appears before the first H2 (last_h2 is None) must still raise
    # the same "outside an ## Assertions" error — not silently pass or crash.
    eval_path = _write_slug(
        tmp_path,
        "early-h3",
        """\
        ---
        {}
        ---

        ### Too Early

        - [ ] this appears before any H2 section

        ## Prompt

        Do it.

        ## Assertions

        - [ ] the real assertion
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="outside an `## Assertions`"):
        mdformat.parse_eval_md(eval_path)


def test_h3_groups_under_assertions_still_parse(tmp_path: Path) -> None:
    """Verify h3 groups under assertions still parse."""
    eval_path = _write_slug(
        tmp_path,
        "grouped",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] top-level assertion

        ### A group

        - [ ] grouped assertion
    """,
    )
    doc = mdformat.parse_eval_md(eval_path)
    assert doc["assertions"] == ["top-level assertion", "grouped assertion"]


def test_parent_child_flattens_to_children(tmp_path: Path) -> None:
    """Verify parent child flattens to children."""
    eval_path = _write_slug(
        tmp_path,
        "scaffolded",
        """\
        ---
        {}
        ---

        ## Prompt

        Scaffold the vault.

        ## Assertions

        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
          - [ ] ./.meta/index.md opens with '# Index'
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)
    assert ev["assertions"] == [
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
        "./.meta/index.md opens with '# Index'",
    ]


def test_mixed_childless_and_parent_items_keep_document_order(tmp_path: Path) -> None:
    """Verify mixed childless and parent items keep document order."""
    eval_path = _write_slug(
        tmp_path,
        "mixed",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] Skill `bootstrap` invoked
        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
        - [ ] a whoami profile stub exists
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)
    assert ev["assertions"] == [
        "Skill `bootstrap` invoked",
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
        "a whoami profile stub exists",
    ]


def test_flat_list_parses_unchanged(tmp_path: Path) -> None:
    """Verify flat list parses unchanged."""
    # Back-compat: a flat (un-nested) list parses exactly as before.
    eval_path = _write_slug(
        tmp_path,
        "flat",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] the archived file exists
        - [ ] the source no longer exists
        - [ ] Skill `archive` invoked
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)
    assert ev["assertions"] == [
        "the archived file exists",
        "the source no longer exists",
        "Skill `archive` invoked",
    ]


def test_grandchild_is_a_hard_error(tmp_path: Path) -> None:
    """Verify grandchild is a hard error."""
    eval_path = _write_slug(
        tmp_path,
        "deep",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
            - [ ] and is empty
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="grandchild"):
        mdformat.parse_eval_md(eval_path)


def test_ragged_child_indent_is_a_hard_error(tmp_path: Path) -> None:
    """Verify ragged child indent is a hard error."""
    eval_path = _write_slug(
        tmp_path,
        "ragged",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] the vault was scaffolded:
            - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="ragged"):
        mdformat.parse_eval_md(eval_path)


def test_child_without_parent_is_a_hard_error(tmp_path: Path) -> None:
    """Verify child without parent is a hard error."""
    eval_path = _write_slug(
        tmp_path,
        "orphan",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

          - [ ] the 0. Inbox/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="no parent"):
        mdformat.parse_eval_md(eval_path)


def test_children_cannot_cross_h3_boundary(tmp_path: Path) -> None:
    """Verify children cannot cross h3 boundary."""
    # The H2-body parent stays childless; the H3's indented line is then an orphan
    # (hence the "no parent" error, not silent cross-section adoption).
    eval_path = _write_slug(
        tmp_path,
        "cross-section",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        - [ ] the vault was scaffolded:

        ### Detail

          - [ ] the 0. Inbox/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="no parent"):
        mdformat.parse_eval_md(eval_path)


def test_parent_child_decomposes_within_h3_group(tmp_path: Path) -> None:
    """Verify parent child decomposes within h3 group."""
    # The only claimed-but-otherwise-untested capability: decomposition inside a
    # ### group, not just the H2 body.
    eval_path = _write_slug(
        tmp_path,
        "h3-decomp",
        """\
        ---
        {}
        ---

        ## Prompt

        eval_path
        ## Assertions

        ### Detail

        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
    """,
    )
    ev = mdformat.parse_eval_md(eval_path)

    assert ev["assertions"] == [
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
    ]


def test_if_clause_parses_into_clauses(tmp_path: Path) -> None:
    """Verify an `- if:` sub-bullet attaches to its item and leaves the prose untouched."""
    eval_path = _write_slug(
        tmp_path,
        "scoped",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] Skill `hello` invoked
          - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
        - [ ] ./out.md exists
    """,
    )

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["assertions"] == ["Skill `hello` invoked", "./out.md exists"]
    assert ev["clauses"] == [
        {"key": "if", "expr": "{BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}"},
        None,
    ]


def test_unless_clause_parses_into_clauses(tmp_path: Path) -> None:
    """Verify an `- unless:` sub-bullet parses with its own key and raw expression."""
    eval_path = _write_slug(
        tmp_path,
        "scoped",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] Opens the note with Read, not Bash
          - unless: {BENCHSPEC_HARNESS} == "codex"
    """,
    )

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["assertions"] == ["Opens the note with Read, not Bash"]
    assert ev["clauses"] == [{"key": "unless", "expr": '{BENCHSPEC_HARNESS} == "codex"'}]


def test_clauses_is_all_none_for_an_unclaused_eval(tmp_path: Path) -> None:
    """Verify `clauses` is always present, aligned with `assertions`, all None without clauses."""
    eval_path = _write_slug(
        tmp_path,
        "flat",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] the archived file exists
        - [ ] the source no longer exists
    """,
    )

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["clauses"] == [None, None]


def test_second_clause_on_one_item_is_a_hard_error(tmp_path: Path) -> None:
    """Verify two clauses on one item raise, quoting the path and the second clause line."""
    eval_path = _write_slug(
        tmp_path,
        "twice",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] Skill `hello` invoked
          - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
          - if: {BENCHSPEC_ARM} == "trial"
    """,
    )

    with pytest.raises(mdformat.MdFormatError, match="already carries a clause") as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert 'if: {BENCHSPEC_ARM} == "trial"' in str(exc_info.value)


def test_clause_with_no_parent_is_a_hard_error(tmp_path: Path) -> None:
    """Verify a clause with no `- [ ]` item above it in its section raises, quoting that line."""
    # The H2-body clause is legal; the H3 group's clause has no item above it in its own
    # section, so it is the one the error must name.
    eval_path = _write_slug(
        tmp_path,
        "orphan-clause",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] ./out.md exists
          - if: {BENCHSPEC_ARM} == "trial"

        ### Detail

          - if: {BENCHSPEC_HARNESS} == "codex"
    """,
    )

    with pytest.raises(mdformat.MdFormatError) as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert 'if: {BENCHSPEC_HARNESS} == "codex"' in str(exc_info.value)


def test_clause_before_children_under_a_display_only_parent_is_a_hard_error(
    tmp_path: Path,
) -> None:
    """Verify a clause under a parent, ahead of its children, raises quoting the clause line."""
    eval_path = _write_slug(
        tmp_path,
        "scoped-parent",
        """\
        ---
        {}
        ---

        ## Prompt

        Scaffold the project.

        ## Assertions

        - [ ] the project was scaffolded:
          - if: {BENCHSPEC_ARM} == "trial"
          - [ ] the ./src/ directory exists
          - [ ] the ./tests/ directory exists
    """,
    )

    with pytest.raises(mdformat.MdFormatError, match="display-only parent") as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert 'if: {BENCHSPEC_ARM} == "trial"' in str(exc_info.value)


def test_clause_after_children_under_a_display_only_parent_is_a_hard_error(
    tmp_path: Path,
) -> None:
    """Verify a clause at the children's indent, after them, raises quoting that line."""
    eval_path = _write_slug(
        tmp_path,
        "late-parent-clause",
        """\
        ---
        {}
        ---

        ## Prompt

        Scaffold the project.

        ## Assertions

        - [ ] the project was scaffolded:
          - [ ] the ./src/ directory exists
          - [ ] the ./tests/ directory exists
          - if: {BENCHSPEC_ARM} == "trial"
    """,
    )

    with pytest.raises(mdformat.MdFormatError, match="display-only parent") as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert str(eval_path) in str(exc_info.value)
    assert 'if: {BENCHSPEC_ARM} == "trial"' in str(exc_info.value)


def test_clause_without_a_space_after_the_colon_is_a_hard_error(tmp_path: Path) -> None:
    """Verify a near-miss clause line names the exact `- if: <expr>` shape it needs."""
    eval_path = _write_slug(
        tmp_path,
        "near-miss-clause",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] ./out.md exists
          - if:{BENCHSPEC_ARM} == "trial"
    """,
    )

    with pytest.raises(mdformat.MdFormatError, match="reads `- if: <expr>`") as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert 'if:{BENCHSPEC_ARM} == "trial"' in str(exc_info.value)


def test_child_may_carry_its_own_clause(tmp_path: Path) -> None:
    """Verify a child's clause, one indent deeper than the child, attaches to that child only."""
    eval_path = _write_slug(
        tmp_path,
        "scoped-child",
        """\
        ---
        {}
        ---

        ## Prompt

        Scaffold the project.

        ## Assertions

        - [ ] the project was scaffolded:
          - [ ] the ./src/ directory exists
            - if: {BENCHSPEC_ARM} == "trial"
          - [ ] the ./tests/ directory exists
    """,
    )

    ev = mdformat.parse_eval_md(eval_path)

    assert ev["assertions"] == [
        "the ./src/ directory exists",
        "the ./tests/ directory exists",
    ]
    assert ev["clauses"] == [{"key": "if", "expr": '{BENCHSPEC_ARM} == "trial"'}, None]


def test_indented_plain_bullet_that_is_not_a_clause_stays_a_hard_error(tmp_path: Path) -> None:
    """Verify a clause-shaped parser still rejects any other indented plain bullet, naming it."""
    eval_path = _write_slug(
        tmp_path,
        "stray-bullet",
        """\
        ---
        {}
        ---

        ## Prompt

        Do it.

        ## Assertions

        - [ ] ./out.md exists
          - if: {BENCHSPEC_ARM} == "trial"
        - [ ] the greeting is warm
          - just a note, not a clause
    """,
    )

    with pytest.raises(mdformat.MdFormatError, match="one assertion per line") as exc_info:
        mdformat.parse_eval_md(eval_path)

    assert "just a note, not a clause" in str(exc_info.value)
