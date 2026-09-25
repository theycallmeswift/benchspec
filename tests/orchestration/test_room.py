"""Tests for room."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from benchspec.orchestration.room import (
    changed_paths,
    gather_facts,
    merge_facts,
    parse_artifact_stream,
    parse_sha_stream,
    read_files_script,
    seed_room,
    sha_snapshot_script,
    to_display_paths,
)


def test_seed_room_copies_fixture_and_records_shas(tmp_path: Path) -> None:
    """Verify seed room copies fixture and records shas."""
    fixture = tmp_path / "fixture"
    (fixture / "0. Inbox").mkdir(parents=True)
    (fixture / "0. Inbox" / "a.md").write_text("hello")
    vault = tmp_path / "room" / "vault"

    shas = seed_room(fixture, vault)

    assert (vault / "0. Inbox" / "a.md").read_text() == "hello"
    assert "0. Inbox/a.md" in shas
    assert len(shas["0. Inbox/a.md"]) == 64  # sha256 hex


def test_seed_room_no_fixture_creates_empty_vault(tmp_path: Path) -> None:
    """Verify seed room no fixture creates empty vault."""
    vault = tmp_path / "room" / "vault"
    shas = seed_room(None, vault)
    assert vault.is_dir()
    assert shas == {}


def test_seed_room_substitutes_today_in_path_names_and_content(
    tmp_path: Path,
) -> None:
    """Verify seed room substitutes today in path names and content."""
    fixture = tmp_path / "fixture"
    dated_dir = fixture / "Sources" / "{TODAY}"
    dated_dir.mkdir(parents=True)
    (dated_dir / "article.md").write_text("fetched: {TODAY}T09:00:00")
    log_dir = fixture / ".meta" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "{TODAY}.md").write_text("## [{TODAY}T10:00:00] archive | title")

    vault = tmp_path / "vault"

    shas = seed_room(fixture, vault, today="2099-07-04")

    assert (vault / "Sources" / "2099-07-04" / "article.md").is_file()
    assert (vault / ".meta" / "logs" / "2099-07-04.md").is_file()
    assert not any("{TODAY}" in str(p) for p in vault.rglob("*"))
    assert "2099-07-04" in (vault / "Sources" / "2099-07-04" / "article.md").read_text()
    assert "2099-07-04" in (vault / ".meta" / "logs" / "2099-07-04.md").read_text()
    # SHAs are computed after substitution — they must match the seeded bytes
    article_rel = "Sources/2099-07-04/article.md"
    assert article_rel in shas
    expected_sha = hashlib.sha256(
        (vault / "Sources" / "2099-07-04" / "article.md").read_bytes()
    ).hexdigest()
    assert shas[article_rel] == expected_sha




def test_gather_facts_returns_tree_contents_and_shas(tmp_path: Path) -> None:
    """Verify gather facts returns tree contents and shas."""
    (tmp_path / "0. Inbox").mkdir()
    (tmp_path / "0. Inbox" / "a.md").write_text("hello")

    tree, contents, shas = gather_facts(tmp_path, max_bytes=1000)

    assert "0. Inbox/a.md" in tree
    assert contents["0. Inbox/a.md"] == "hello"
    assert len(shas["0. Inbox/a.md"]) == 64




def _stream(*pairs: tuple[str, str]) -> str:
    """Build a read_files-script stdout from (path, content) pairs."""
    return "".join(
        f"\x1e\x1eARTIFACT\x1e\x1e{path}\x1e\x1e\n{content}" for path, content in pairs
    )


def _sha_lines(*pairs: tuple[str, str]) -> str:
    """Build sha256sum stdout (`<hex>  <path>`) from (path, sha) pairs."""
    return "".join(f"{sha}  {path}\n" for path, sha in pairs)


def test_sha_snapshot_script_lists_each_dir_and_skips_missing() -> None:
    """Verify sha snapshot script lists each dir and skips missing."""
    script = sha_snapshot_script(["/root/.claude/skills", "/root/.config/opencode/skills"])
    assert "/root/.claude/skills" in script
    assert "/root/.config/opencode/skills" in script
    assert '[ -d "$d" ]' in script  # guards missing dirs
    assert "sha256sum" in script  # hashes, doesn't cat


def test_sha_snapshot_script_exits_zero_when_last_dir_missing(tmp_path: Path) -> None:
    """Verify sha snapshot script exits zero when last dir missing."""
    # A `for` loop exits with its last iteration's status, so a missing LAST dir makes
    # `[ -d "$d" ] &&` short-circuit to exit 1 — which the caller would read as a failed
    # snapshot and dump the staged tree as "authored". The `; done; true` terminator pins
    # exit 0 regardless of the order in which dirs are present/absent.
    present = tmp_path
    missing = tmp_path / "nope"
    script = sha_snapshot_script([str(present), str(missing)])  # missing dir last
    proc = subprocess.run(["sh", "-c", script], capture_output=True)
    assert proc.returncode == 0


def test_sha_snapshot_script_descends_through_a_symlinked_load_dir(
    tmp_path: Path,
) -> None:
    """Verify sha snapshot script descends through a symlinked load dir."""
    # The skills-home bridge replaces the agent's load dir with a SYMLINK to the fixed
    # skills home (bridge_skills_home_script). A bare `find <symlink> -type f` start point
    # does not descend through the link, so an agent-authored skill would be invisible to
    # the judge. `find -L` must walk the link's target and report the symlink-prefixed path
    # so `to_display_paths` still relabels it `~/...`. Real /bin/sh — a mock can't catch
    # the find-on-symlink interaction.
    real_home = tmp_path / "home" / "benchspec" / "skills"
    (real_home / "authored" / "evals").mkdir(parents=True)
    (real_home / "authored" / "SKILL.md").write_text("agent wrote this\n")
    (real_home / "authored" / "evals" / "case.json").write_text("{}\n")

    load_dir = tmp_path / "root" / ".claude" / "skills"
    load_dir.parent.mkdir(parents=True)
    load_dir.symlink_to(real_home)  # mirrors the provision-time bridge

    script = sha_snapshot_script([str(load_dir)])
    proc = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0

    shas = parse_sha_stream(proc.stdout)
    # Files surface under the SYMLINK-prefixed path (so to_display_paths can relabel),
    # including the nested evals/ tree — proof the walk descended through the link.
    assert str(load_dir / "authored" / "SKILL.md") in shas
    assert str(load_dir / "authored" / "evals" / "case.json") in shas


def test_parse_sha_stream_splits_digest_from_path() -> None:
    """Verify parse sha stream splits digest from path."""
    shas = parse_sha_stream(
        _sha_lines(
            ("/root/.claude/skills/commit/SKILL.md", "a" * 64),
            ("/root/.claude/skills/commit/evals/evals.json", "b" * 64),
        )
    )
    assert shas["/root/.claude/skills/commit/SKILL.md"] == "a" * 64
    assert shas["/root/.claude/skills/commit/evals/evals.json"] == "b" * 64


def test_parse_sha_stream_ignores_blank_lines() -> None:
    """Verify parse sha stream ignores blank lines."""
    assert parse_sha_stream("\n\n") == {}


def test_changed_paths_returns_new_and_modified_drops_unchanged() -> None:
    """Verify changed paths returns new and modified drops unchanged."""
    baseline = {"/skills/staged/SKILL.md": "aaa", "/skills/edited/SKILL.md": "bbb"}
    current = {
        "/skills/staged/SKILL.md": "aaa",  # unchanged staged input → drop
        "/skills/edited/SKILL.md": "ZZZ",  # sha changed → keep
        "/skills/new/SKILL.md": "ccc",  # new → keep
    }
    assert set(changed_paths(baseline, current)) == {
        "/skills/edited/SKILL.md",
        "/skills/new/SKILL.md",
    }


def test_read_files_script_quotes_each_path_and_is_noop_when_empty() -> None:
    """Verify read files script quotes each path and is noop when empty."""
    script = read_files_script(["/root/.claude/skills/commit/SKILL.md"])
    assert "/root/.claude/skills/commit/SKILL.md" in script
    assert "cat" in script
    assert read_files_script([]) == "true"  # no paths → harmless no-op, no shell work


def test_parse_artifact_stream_roundtrips_paths_and_contents() -> None:
    """Verify parse artifact stream roundtrips paths and contents."""
    out = parse_artifact_stream(
        _stream(
            ("/root/.claude/skills/commit/SKILL.md", "---\nname: commit\n---\nbody\n"),
            ("/root/.claude/skills/commit/evals/evals.json", '{"id": "x"}'),
        )
    )
    assert out["/root/.claude/skills/commit/SKILL.md"] == "---\nname: commit\n---\nbody\n"
    assert out["/root/.claude/skills/commit/evals/evals.json"] == '{"id": "x"}'


def test_parse_artifact_stream_preserves_content_with_newlines_and_no_trailing_newline() -> None:
    """Verify parse artifact stream preserves content with newlines and no trailing."""
    # A file whose content ends WITHOUT a newline must not bleed into the next record,
    # and embedded newlines are kept verbatim.
    out = parse_artifact_stream(
        _stream(
            ("/a/SKILL.md", "line1\nline2"),  # no trailing newline
            ("/b/SKILL.md", "second"),
        )
    )
    assert out["/a/SKILL.md"] == "line1\nline2"
    assert out["/b/SKILL.md"] == "second"


def test_parse_artifact_stream_empty_is_empty() -> None:
    """Verify parse artifact stream empty is empty."""
    assert parse_artifact_stream("") == {}


def test_to_display_paths_rewrites_home_prefix() -> None:
    """Verify to display paths rewrites home prefix."""
    disp = to_display_paths(
        {"/root/.claude/skills/commit/SKILL.md": "x", "/other/abs.md": "y"},
        guest_home="/root",
    )
    assert disp["~/.claude/skills/commit/SKILL.md"] == "x"
    assert disp["/other/abs.md"] == "y"  # outside home → left absolute


def test_merge_facts_appends_artifacts_to_tree_and_contents() -> None:
    """Verify merge facts appends artifacts to tree and contents."""
    tree, contents, shas = "out.md", {"out.md": "workdir file"}, {"out.md": "deadbeef"}
    mtree, mcontents, mshas = merge_facts(
        tree,
        contents,
        shas,
        {"~/.claude/skills/commit/SKILL.md": "the skill"},
    )
    assert "out.md" in mtree
    assert "~/.claude/skills/commit/SKILL.md" in mtree
    assert mcontents["~/.claude/skills/commit/SKILL.md"] == "the skill"
    assert len(mshas["~/.claude/skills/commit/SKILL.md"]) == 64
    # original workdir facts are preserved
    assert mcontents["out.md"] == "workdir file"


def test_merge_facts_truncates_oversized_artifact() -> None:
    """Verify merge facts truncates oversized artifact."""
    big = "x" * 50
    _, contents, _ = merge_facts("", {}, {}, {"~/skills/big/SKILL.md": big}, max_bytes=10)
    assert contents["~/skills/big/SKILL.md"].startswith("x" * 10)
    assert contents["~/skills/big/SKILL.md"].endswith("<TRUNCATED>")
