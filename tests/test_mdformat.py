"""Markdown eval format: parse into the dict shape schema._validate checks."""

from __future__ import annotations

import textwrap

import pytest

from evalspec import mdformat, schema


def _write(tmp_path: object, name: object, body: object) -> object:
    """Handle _write."""
    f = tmp_path / name
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return f


def _write_slug(tmp_path: object, slug: object, body: object) -> object:
    """Handle _write_slug."""
    slug_dir = tmp_path / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "prompt.md").write_text(textwrap.dedent(body), encoding="utf-8")
    return slug_dir / "prompt.md"


def test_parse_eval_md_minimal(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
    ev = mdformat.parse_eval_md(p)

    assert ev["slug"] == "single-article"
    assert ev["prompt"].startswith("Use the `ingest` skill")
    assert "Second paragraph" in ev["prompt"]
    assert ev["assertions"] == [
        "Skill `ingest` invoked",
        "a Resource page was created",
        "the activity log names the move",  # H3 group flattened in order
    ]
    assert "seed" not in ev


def test_parse_eval_md_with_seed(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "catch-all-pose",
        """\
        ---
        seed:
          - role: user
            text: scope my plan
          - role: assistant
            text: which part?
        ---

        ## Prompt

        The deeper question.

        ## Assertions

        - [ ] Skill `interview-me` invoked
    """,
    )
    ev = mdformat.parse_eval_md(p)

    assert ev["seed"] == [
        {"role": "user", "text": "scope my plan"},
        {"role": "assistant", "text": "which part?"},
    ]


def test_parse_eval_md_malformed_seed_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    # A bare parse_eval_md call must validate seed itself, not defer to load_suite_dir.
    p = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        seed:
          - role: user
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] x
    """,
    )
    with pytest.raises(schema.SchemaError, match="seed"):
        mdformat.parse_eval_md(p)


def test_parse_eval_md_unknown_frontmatter_key_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "a",
        """\
        ---
        id: a
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] x
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="id"):
        mdformat.parse_eval_md(p)


def test_parse_eval_md_missing_prompt_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
        mdformat.parse_eval_md(p)


def test_parse_eval_md_missing_assertions_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
        mdformat.parse_eval_md(p)


def test_continuation_lines_are_hard_errors(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] an assertion that
          wraps onto a second line
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="one assertion per line"):
        mdformat.parse_eval_md(p)


def test_unknown_heading_is_a_hard_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertion

        - [ ] typo'd heading must not silently drop
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="Assertion"):
        mdformat.parse_eval_md(p)


def test_plain_bullet_is_a_hard_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        - missing the checkbox
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match=r"- \[ \]"):
        mdformat.parse_eval_md(p)


def test_bad_yaml_frontmatter_is_loud(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "bad",
        """\
        ---
        seed: unquoted: colon value
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] a
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="frontmatter"):
        mdformat.parse_eval_md(p)


def test_load_suite_dir_assembles(tmp_path: object) -> None:
    """Test the expected behavior."""
    evals = tmp_path / "evals"
    _write_slug(evals, "one", "---\n{}\n---\n\n## Prompt\n\np1\n\n## Assertions\n\n- [ ] a1\n")
    _write_slug(evals, "two", "---\n{}\n---\n\n## Prompt\n\np2\n\n## Assertions\n\n- [ ] a2\n")

    doc = mdformat.load_suite_dir(evals)

    assert doc["$schema"] == "evalspec/v1"
    assert [e["slug"] for e in doc["evals"]] == ["one", "two"]
    schema._validate(doc)  # already validated inside, but pin the contract


def test_load_suite_dir_rejects_dir_without_prompt(tmp_path: object) -> None:
    """Test the expected behavior."""
    # A top-level dir under evals/ with no prompt.md is a half-authored or misnamed
    # eval — fail loud rather than silently collect zero cases for it.
    evals = tmp_path / "evals"
    _write_slug(evals, "real", "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n")
    (evals / "bare").mkdir()

    with pytest.raises(schema.SchemaError, match="bare has no prompt.md"):
        mdformat.load_suite_dir(evals)


def test_load_suite_dir_skips_dunder_tooling_dirs(tmp_path: object) -> None:
    """Test the expected behavior."""
    evals = tmp_path / "evals"
    _write_slug(evals, "real", "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n")
    (evals / "__pycache__").mkdir()

    doc = mdformat.load_suite_dir(evals)

    assert [e["slug"] for e in doc["evals"]] == ["real"]


def test_load_suite_dir_ignores_per_eval_fixtures_dir(tmp_path: object) -> None:
    """Test the expected behavior."""
    # A slug's own fixtures/ lives one level down (evals/<slug>/fixtures), so it is
    # never iterated as a suite-level dir and never mistaken for an eval.
    evals = tmp_path / "evals"
    _write_slug(evals, "real", "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] a\n")
    (evals / "real" / "fixtures").mkdir()

    doc = mdformat.load_suite_dir(evals)

    assert [e["slug"] for e in doc["evals"]] == ["real"]


def test_mdformat_error_is_a_schema_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    # discovery's error prefixing catches schema.SchemaError; Markdown structure
    # errors must ride the same channel.
    assert issubclass(mdformat.MdFormatError, schema.SchemaError)


def test_empty_assertions_section_is_an_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
        mdformat.parse_eval_md(p)


def test_stray_h3_outside_assertions_is_an_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
        mdformat.parse_eval_md(p)


def test_h3_before_any_h2_is_an_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    # An H3 that appears before the first H2 (last_h2 is None) must still raise
    # the same "outside an ## Assertions" error — not silently pass or crash.
    p = _write_slug(
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
        mdformat.parse_eval_md(p)


def test_h3_groups_under_assertions_still_parse(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
    doc = mdformat.parse_eval_md(p)
    assert doc["assertions"] == ["top-level assertion", "grouped assertion"]


def test_parse_trigger_basic_grid(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Description
        Positives capture INTO the wiki.

        ## Trigger
        - ingest-article: ingest this article into my knowledge base
        - pasted-text: import this pasted text into the vault

        ## No Trigger
        - archive-near-miss: archive this transcript, I'm done with it
    """,
    )
    doc = mdformat.parse_trigger(p)
    assert doc["$schema"] == "evalspec-trigger/v1"
    assert doc["skill_name"] == "ingest"
    assert doc["description"] == "Positives capture INTO the wiki."
    assert doc["queries"] == [
        {
            "slug": "ingest-article",
            "query": "ingest this article into my knowledge base",
            "should_trigger": True,
        },
        {
            "slug": "pasted-text",
            "query": "import this pasted text into the vault",
            "should_trigger": True,
        },
        {
            "slug": "archive-near-miss",
            "query": "archive this transcript, I'm done with it",
            "should_trigger": False,
        },
    ]


def test_parse_trigger_xfail_subbullet_and_continuation(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Trigger
        - inbox-process: process the source and add it to the wiki
          - fails-on [sonnet, haiku]: routing boundary; routes on opus.
            Cross-verified 2026-05-30.
    """,
    )
    doc = mdformat.parse_trigger(p)
    assert doc["queries"][0]["xfail"] == {
        "models": ["sonnet", "haiku"],
        "reason": "routing boundary; routes on opus. Cross-verified 2026-05-30.",
    }


def test_parse_trigger_query_with_colon_keeps_remainder(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Trigger
        - note: turn this into a note: a real one
    """,
    )
    assert mdformat.parse_trigger(p)["queries"][0]["query"] == "turn this into a note: a real one"


def test_parse_trigger_missing_colon_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Trigger
        - no-colon-here
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="slug.*query"):
        mdformat.parse_trigger(p)


def test_parse_trigger_xfail_before_query_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Trigger
          - fails-on [sonnet]: r
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="fails-on"):
        mdformat.parse_trigger(p)


def test_parse_trigger_unknown_section_rejected(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Bogus
        - a: b
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="Bogus"):
        mdformat.parse_trigger(p)


def test_parse_trigger_duplicate_slug_rejected_via_schema(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write(
        tmp_path,
        "trigger-evals.md",
        """\
        ---
        skill_name: ingest
        ---
        ## Trigger
        - dup: a
        ## No Trigger
        - dup: b
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="duplicate slug"):
        mdformat.parse_trigger(p)


def test_parent_child_flattens_to_children(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
    ev = mdformat.parse_eval_md(p)
    assert ev["assertions"] == [
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
        "./.meta/index.md opens with '# Index'",
    ]


def test_mixed_childless_and_parent_items_keep_document_order(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
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
    ev = mdformat.parse_eval_md(p)
    assert ev["assertions"] == [
        "Skill `bootstrap` invoked",
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
        "a whoami profile stub exists",
    ]


def test_flat_list_parses_unchanged(tmp_path: object) -> None:
    """Test the expected behavior."""
    # Back-compat: a flat (un-nested) list parses exactly as before.
    p = _write_slug(
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
    ev = mdformat.parse_eval_md(p)
    assert ev["assertions"] == [
        "the archived file exists",
        "the source no longer exists",
        "Skill `archive` invoked",
    ]


def test_grandchild_is_a_hard_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "deep",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
            - [ ] and is empty
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="grandchild"):
        mdformat.parse_eval_md(p)


def test_ragged_child_indent_is_a_hard_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "ragged",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] the vault was scaffolded:
            - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="ragged"):
        mdformat.parse_eval_md(p)


def test_child_without_parent_is_a_hard_error(tmp_path: object) -> None:
    """Test the expected behavior."""
    p = _write_slug(
        tmp_path,
        "orphan",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

          - [ ] the 0. Inbox/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="no parent"):
        mdformat.parse_eval_md(p)


def test_children_cannot_cross_h3_boundary(tmp_path: object) -> None:
    """Test the expected behavior."""
    # The H2-body parent stays childless; the H3's indented line is then an orphan
    # (hence the "no parent" error, not silent cross-section adoption).
    p = _write_slug(
        tmp_path,
        "cross-section",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        - [ ] the vault was scaffolded:

        ### Detail

          - [ ] the 0. Inbox/ directory exists
    """,
    )
    with pytest.raises(mdformat.MdFormatError, match="no parent"):
        mdformat.parse_eval_md(p)


def test_parent_child_decomposes_within_h3_group(tmp_path: object) -> None:
    """Test the expected behavior."""
    # The only claimed-but-otherwise-untested capability: decomposition inside a
    # ### group, not just the H2 body.
    p = _write_slug(
        tmp_path,
        "h3-decomp",
        """\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

        ### Detail

        - [ ] the vault was scaffolded:
          - [ ] the 0. Inbox/ directory exists
          - [ ] the 1. Profile/ directory exists
    """,
    )
    ev = mdformat.parse_eval_md(p)

    assert ev["assertions"] == [
        "the 0. Inbox/ directory exists",
        "the 1. Profile/ directory exists",
    ]
