"""Markdown eval format: parse into the dict shape schema._validate checks."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

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
