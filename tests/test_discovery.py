"""Tests and helpers for evalspec."""

from pathlib import Path

import pytest

from evalspec import discovery, schema
from evalspec.discovery import (
    discover_eval_cases,
    discover_trigger_cases,
    resolve_environment_config,
    resolve_repo_root,
)
from evalspec.mdformat import MdFormatError


def _trigger_md(skill_name: str, queries: list[str]) -> str:
    """Build a minimal trigger-evals.md with all queries in the Trigger section."""
    lines = [f"---\nskill_name: {skill_name}\n---\n## Trigger\n"]
    lines.extend(queries)
    return "".join(lines)


def _write_trigger_md(skill_dir: Path, queries: list[str], *, skill_name: object = None) -> Path:
    """Handle _write_trigger_md."""
    evals_dir = skill_dir / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    f = evals_dir / "trigger-evals.md"
    f.write_text(_trigger_md(skill_name or skill_dir.name, queries), encoding="utf-8")
    return f


class _FakeConfig:
    """Represent _FakeConfig."""

    def __init__(
        self: object,
        repo_root: object = None,
        rootpath: object = Path("/fallback/root"),
    ) -> None:
        """Initialize the instance."""
        self._repo_root = repo_root
        self.rootpath = rootpath

    def getoption(self: object, name: object) -> object:
        """Handle getoption."""
        return self._repo_root if name == "evalspec_repo_root" else None


def _seed_slug_suite(
    tmp_path: object,
    skill: object = "demo",
    evals: object = None,
    root: object = "skills",
) -> object:
    """Test the expected behavior."""
    base = tmp_path / root / skill / "evals"
    for slug, (prompt, assertions) in (
        evals
        or {
            "alpha": ("do the thing", ["it did the thing"]),
        }
    ).items():
        d = base / slug
        d.mkdir(parents=True)
        lines = [f"- [ ] {a}" for a in assertions]
        (d / "prompt.md").write_text(
            f"---\n{{}}\n---\n\n## Prompt\n\n{prompt}\n\n"
            "## Assertions\n\n" + "\n".join(lines) + "\n"
        )
    return base


def _write_triggers(skill_dir: Path, queries: list[str], *, skill_name: object = None) -> Path:
    """Write a trigger-evals.md.

    `queries` is a list of markdown bullet lines.
    """
    return _write_trigger_md(skill_dir, queries, skill_name=skill_name)


# ---------------------------------------------------------------------------
# resolve_repo_root
# ---------------------------------------------------------------------------


def test_resolve_repo_root_prefers_option(tmp_path: object) -> None:
    """Test the expected behavior."""
    cfg = _FakeConfig(repo_root=str(tmp_path))
    assert resolve_repo_root(cfg) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_project_root_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Test the expected behavior."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    assert resolve_repo_root(_FakeConfig()) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_rootpath(monkeypatch: object) -> None:
    """Test the expected behavior."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    cfg = _FakeConfig(rootpath=Path("/some/rootdir"))
    assert resolve_repo_root(cfg) == Path("/some/rootdir")


# ---------------------------------------------------------------------------
# discover_eval_cases
# ---------------------------------------------------------------------------


def _skill(
    tmp_path: object, name: object, slug: object, body: object, root: object = "skills"
) -> object:
    """Write one `<root>/<name>/evals/<slug>/prompt.md` with the given body."""
    d = tmp_path / root / name / "evals" / slug
    d.mkdir(parents=True)
    (d / "prompt.md").write_text(body)
    return tmp_path


def test_discover_eval_cases_self_contained(tmp_path: object) -> None:
    """Test the expected behavior."""
    _skill(
        tmp_path,
        "ingest",
        "single-article",
        "---\n{}\n---\n\n"
        "## Prompt\n\nUse `ingest`.\n\n"
        "## Assertions\n\n- [ ] Skill `ingest` invoked\n",
    )

    cases = discover_eval_cases(tmp_path)

    assert len(cases) == 1
    c = cases[0]
    assert c.skill == "ingest"
    assert c.slug == "single-article"
    assert c.eval_id == "single-article"
    assert c.param_id == "ingest-single-article"
    assert c.prompt == "Use `ingest`."
    assert c.assertions == ["Skill `ingest` invoked"]
    assert c.seed == []
    assert c.fixtures_dir is None


def test_discover_eval_cases_seed_and_fixtures(tmp_path: object) -> None:
    """Test the expected behavior."""
    base = _skill(
        tmp_path,
        "archive",
        "clobber",
        "---\nseed:\n  - role: user\n    text: hi\n---\n\n"
        "## Prompt\n\nArchive ./x.\n\n## Assertions\n\n- [ ] it refused\n",
    )
    fx = base / "skills" / "archive" / "evals" / "clobber" / "fixtures"
    fx.mkdir()
    (fx / "x.md").write_text("body")

    [c] = discover_eval_cases(tmp_path)

    assert c.seed == [{"role": "user", "text": "hi"}]
    assert c.fixtures_dir == fx


def test_discover_rejects_unknown_frontmatter_end_to_end(tmp_path: object) -> None:
    """Test the expected behavior."""
    # The mdformat → schema._validate → discovery seam: an unknown frontmatter key
    # must surface as an error through the whole chain, not pass silently. The body
    # is well-formed except for the unknown `id` key, so this isolates that guard.
    _skill(
        tmp_path,
        "ingest",
        "bad",
        "---\nid: bad\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] x\n",
    )

    with pytest.raises(MdFormatError):
        discover_eval_cases(tmp_path)


def test_skill_with_only_triggers_still_discovers_no_eval_cases(
    tmp_path: object,
) -> None:
    """Test the expected behavior."""
    _write_trigger_md(
        tmp_path / "skills" / "demo",
        ["- my-query: q\n"],
    )

    assert discover_eval_cases(tmp_path) == []
    assert len(discover_trigger_cases(tmp_path)) == 1


def test_discover_rejects_legacy_flat_eval_file(tmp_path: object) -> None:
    """Test the expected behavior."""
    # A suite with no slug dirs but a stray flat `*.md` (the retired legacy shape)
    # is half-migrated, not trigger-only: discovery must raise instead of silently
    # demoting it to "trigger-only" and dropping its coverage. trigger-evals.md is
    # the only reserved non-eval, so it must NOT trip this guard.
    evals = tmp_path / "skills" / "stale" / "evals"
    evals.mkdir(parents=True)
    (evals / "old-eval.md").write_text("---\nid: old-eval\n---\n\n## Prompt\n\np\n")

    with pytest.raises(schema.SchemaError, match="old-eval.md"):
        discover_eval_cases(tmp_path)


def test_discover_rejects_legacy_flat_eval_file_beside_slug_dirs(
    tmp_path: object,
) -> None:
    """Test the expected behavior."""
    # The dangerous case: a half-migrated suite where one slug dir IS converted but a
    # flat `*.md` lingers. load_suite_dir iterates only subdirs, so the flat file's
    # coverage drops silently unless the stray check runs even when a slug dir exists.
    base = _skill(
        tmp_path,
        "archive",
        "converted",
        "---\n{}\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] x\n",
    )
    evals = base / "skills" / "archive" / "evals"
    (evals / "old-eval.md").write_text("---\nid: old-eval\n---\n\n## Prompt\n\np\n")

    with pytest.raises(schema.SchemaError, match="old-eval.md"):
        discover_eval_cases(tmp_path)


def test_discover_eval_cases_one_per_slug(tmp_path: object) -> None:
    """Test the expected behavior."""
    _seed_slug_suite(
        tmp_path,
        skill="myskill",
        evals={
            "alpha": ("p", ["a"]),
            "beta": ("t1", ["x"]),
        },
    )

    cases = discover_eval_cases(tmp_path)

    assert [c.param_id for c in cases] == ["myskill-alpha", "myskill-beta"]
    assert all(c.skill_dir == tmp_path / "skills" / "myskill" for c in cases)
    assert all(c.skill == "myskill" for c in cases)


def test_discover_eval_cases_sorted_by_skill(tmp_path: object) -> None:
    """Test the expected behavior."""
    _seed_slug_suite(tmp_path, skill="zebra", evals={"z": ("p", ["a"])})
    _seed_slug_suite(tmp_path, skill="alpha", evals={"a": ("p", ["a"])})
    cases = discover_eval_cases(tmp_path)
    assert [c.skill for c in cases] == ["alpha", "zebra"]


def test_discover_eval_cases_no_skills_root(tmp_path: object) -> None:
    """Test the expected behavior."""
    assert discover_eval_cases(tmp_path) == []


def test_discover_eval_cases_skips_skill_without_evals(tmp_path: object) -> None:
    """Test the expected behavior."""
    (tmp_path / "skills" / "bare").mkdir(parents=True)
    _seed_slug_suite(tmp_path, skill="real", evals={"r": ("p", ["a"])})
    cases = discover_eval_cases(tmp_path)
    assert [c.skill for c in cases] == ["real"]


def test_discover_eval_cases_raises_on_bad_schema(tmp_path: object) -> None:
    """Test the expected behavior."""
    # Malformed here = a bare `## Prompt` with no `## Assertions` section.
    bad = tmp_path / "skills" / "bad" / "evals" / "a" / "prompt.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("---\n{}\n---\n\n## Prompt\n\np\n")

    with pytest.raises((MdFormatError, schema.SchemaError)) as e:
        discover_eval_cases(tmp_path)

    assert str(bad) in str(e.value)  # the offending file is named


# ---------------------------------------------------------------------------
# discover_trigger_cases
# ---------------------------------------------------------------------------


def test_discover_trigger_cases_one_per_query(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_triggers(
        tmp_path / "skills" / "myskill",
        [
            "- do-it: do it\n",
            "- not-this: not this\n",
        ],
    )
    cases = discover_trigger_cases(tmp_path)
    assert [c.param_id for c in cases] == ["myskill-do-it", "myskill-not-this"]
    assert all(c.skill_name == "myskill" for c in cases)
    assert all(c.repo_root == tmp_path for c in cases)


def test_discover_trigger_reads_markdown(tmp_path: object) -> None:
    """Test the expected behavior."""
    evals = tmp_path / "skills" / "ingest" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: ingest\n---\n## Trigger\n"
        "- ingest-article: ingest this article into my kb\n",
        encoding="utf-8",
    )
    cases = discovery.discover_trigger_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].query["slug"] == "ingest-article"
    assert cases[0].param_id == "ingest-ingest-article"


# ---------------------------------------------------------------------------
# dual-root discovery (.claude/skills)
# ---------------------------------------------------------------------------


def _seed_md_under(root: object, skill: object, eval_id: object = "e") -> object:
    """Handle _seed_md_under."""
    d = root / skill / "evals" / eval_id
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n")
    return d


def test_discover_eval_cases_finds_both_roots(tmp_path: object) -> None:
    """Test the expected behavior."""
    _seed_md_under(tmp_path / "skills", "plug", "p")
    _seed_md_under(tmp_path / ".claude" / "skills", "loc", "l")
    cases = {c.skill: c for c in discover_eval_cases(tmp_path)}
    assert set(cases) == {"plug", "loc"}
    assert cases["loc"].skill_dir == tmp_path / ".claude" / "skills" / "loc"


def test_discover_eval_cases_local_only(tmp_path: object) -> None:
    """Test the expected behavior."""
    _seed_md_under(tmp_path / ".claude" / "skills", "loc", "l")
    assert [c.skill for c in discover_eval_cases(tmp_path)] == ["loc"]


def test_discover_eval_cases_skips_local_skill_without_evals(tmp_path: object) -> None:
    """Test the expected behavior."""
    (tmp_path / ".claude" / "skills" / "stub").mkdir(parents=True)
    _seed_md_under(tmp_path / ".claude" / "skills", "real", "r")
    assert [c.skill for c in discover_eval_cases(tmp_path)] == ["real"]


def test_discover_trigger_cases_finds_both_roots(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_triggers(
        tmp_path / "skills" / "plug",
        ["- some-query: q\n"],
    )
    _write_triggers(
        tmp_path / ".claude" / "skills" / "loc",
        ["- some-query: q\n"],
    )
    cases = {c.skill: c for c in discover_trigger_cases(tmp_path)}
    assert set(cases) == {"plug", "loc"}
    assert all(c.repo_root == tmp_path for c in cases.values())


def test_discover_raises_on_duplicate_name_across_roots(tmp_path: object) -> None:
    """Test the expected behavior."""
    _seed_md_under(tmp_path / "skills", "dup", "e")
    _seed_md_under(tmp_path / ".claude" / "skills", "dup", "e")
    with pytest.raises(schema.SchemaError, match="duplicate skill name 'dup'"):
        discover_eval_cases(tmp_path)


# ---------------------------------------------------------------------------
# pyproject_table
# ---------------------------------------------------------------------------


def test_pyproject_table_reads_tool_evalspec(tmp_path: object) -> None:
    """Test the expected behavior."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\nagent = "opencode"\neval_roots = ["evals/skills"]\n'
    )
    table = discovery.pyproject_table(tmp_path)
    assert table == {"agent": "opencode", "eval_roots": ["evals/skills"]}


def test_pyproject_table_missing_file_is_empty(tmp_path: object) -> None:
    """Test the expected behavior."""
    assert discovery.pyproject_table(tmp_path) == {}


def test_output_and_trigger_evals_coexist(tmp_path: object) -> None:
    """Test the expected behavior."""
    # A skill dir with BOTH a valid output eval .md AND trigger-evals.md:
    # discover_eval_cases must not raise, must return the output eval case,
    # and must not include anything derived from trigger-evals.md.
    # discover_trigger_cases must return the trigger case.
    skill_dir = tmp_path / "skills" / "myskill"
    _seed_slug_suite(tmp_path, skill="myskill", evals={"alpha": ("do the thing", ["it did"])})
    _write_triggers(skill_dir, ["- my-query: do this thing\n"])

    output_cases = discover_eval_cases(tmp_path)
    trigger_cases = discover_trigger_cases(tmp_path)

    # Output cases: exactly one, from the output eval file — no bleed from trigger-evals.md
    assert len(output_cases) == 1
    assert output_cases[0].eval_id == "alpha"
    assert output_cases[0].skill == "myskill"
    assert not any("trigger" in c.eval_id for c in output_cases)

    # Trigger cases: exactly one, from trigger-evals.md
    assert len(trigger_cases) == 1
    assert trigger_cases[0].param_id == "myskill-my-query"


# ---------------------------------------------------------------------------
# resolve_environment_config / EnvConfig
# ---------------------------------------------------------------------------


def _write_pyproject(tmp_path: object, body: str) -> None:
    """Handle _write_pyproject."""
    (tmp_path / "pyproject.toml").write_text(f"[tool.evalspec]\n{body}", encoding="utf-8")


def test_env_config_absent_is_falsy_and_empty_digest(tmp_path: object) -> None:
    """Test the expected behavior."""
    cfg = resolve_environment_config(tmp_path)  # no pyproject.toml at all
    assert bool(cfg) is False
    assert cfg.base_image is None
    assert cfg.script == b""
    assert cfg.digest() == ""


def test_env_config_base_image_only(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_pyproject(tmp_path, 'base_image = "python:3.12-slim"\n')
    cfg = resolve_environment_config(tmp_path)
    assert bool(cfg) is True
    assert cfg.base_image == "python:3.12-slim"
    assert cfg.script == b""
    assert len(cfg.digest()) == 8


def test_env_config_script_resolved_to_bytes(tmp_path: object) -> None:
    """Test the expected behavior."""
    (tmp_path / "setup.sh").write_text("apt-get install -y jq\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    cfg = resolve_environment_config(tmp_path)
    assert bool(cfg) is True
    assert cfg.script == b"apt-get install -y jq\n"
    assert cfg.script_path == "setup.sh"
    assert len(cfg.digest()) == 8


def test_env_config_digest_changes_with_script_bytes(tmp_path: object) -> None:
    """Test the expected behavior."""
    (tmp_path / "setup.sh").write_text("echo one\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    first = resolve_environment_config(tmp_path).digest()
    (tmp_path / "setup.sh").write_text("echo two\n", encoding="utf-8")  # in-place edit
    second = resolve_environment_config(tmp_path).digest()
    assert first != second


def test_env_config_digest_ignores_script_path(tmp_path: object) -> None:
    """Test the expected behavior."""
    # Same bytes under two different paths ⇒ same digest (path is not hashed).
    (tmp_path / "a.sh").write_text("echo same\n", encoding="utf-8")
    (tmp_path / "b.sh").write_text("echo same\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "a.sh"\n')
    da = resolve_environment_config(tmp_path).digest()
    _write_pyproject(tmp_path, 'environment_script = "b.sh"\n')
    db = resolve_environment_config(tmp_path).digest()
    assert da == db


def test_env_config_base_image_wrong_type_raises(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_pyproject(tmp_path, "base_image = 7\n")
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_base_image_empty_string_raises(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_pyproject(tmp_path, 'base_image = ""\n')
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_environment_script_wrong_type_raises(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_pyproject(tmp_path, "environment_script = true\n")
    with pytest.raises(schema.SchemaError, match="environment_script"):
        resolve_environment_config(tmp_path)


def test_env_config_environment_script_missing_file_raises(tmp_path: object) -> None:
    """Test the expected behavior."""
    _write_pyproject(tmp_path, 'environment_script = "nope.sh"\n')
    with pytest.raises(schema.SchemaError, match="file not found: nope.sh"):
        resolve_environment_config(tmp_path)
