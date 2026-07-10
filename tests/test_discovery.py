"""Tests for discovery."""

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
    """Write trigger md."""
    evals_dir = skill_dir / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    trigger_path = evals_dir / "trigger-evals.md"
    trigger_path.write_text(_trigger_md(skill_name or skill_dir.name, queries), encoding="utf-8")
    return trigger_path


class _FakeConfig:
    """Provide a fake config for tests."""

    def __init__(
        self: object,
        repo_root: object = None,
        rootpath: object = Path("/fallback/root"),
    ) -> None:
        """Initialize the instance."""
        self._repo_root = repo_root
        self.rootpath = rootpath

    def getoption(self: object, option_name: object) -> object:
        """Getoption."""
        return self._repo_root if option_name == "evalspec_repo_root" else None


def _write_triggers(skill_dir: Path, queries: list[str], *, skill_name: object = None) -> Path:
    """Write a trigger-evals.md.

    `queries` is a list of markdown bullet lines.
    """
    return _write_trigger_md(skill_dir, queries, skill_name=skill_name)


# ---------------------------------------------------------------------------
# resolve_repo_root
# ---------------------------------------------------------------------------


def test_resolve_repo_root_prefers_option(tmp_path: object) -> None:
    """Verify resolve repo root prefers option."""
    fake_config = _FakeConfig(repo_root=str(tmp_path))
    assert resolve_repo_root(fake_config) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_project_root_env(
    tmp_path: object, monkeypatch: object
) -> None:
    """Verify resolve repo root falls back to project root env."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    assert resolve_repo_root(_FakeConfig()) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_rootpath(monkeypatch: object) -> None:
    """Verify resolve repo root falls back to rootpath."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    fake_config = _FakeConfig(rootpath=Path("/some/rootdir"))
    assert resolve_repo_root(fake_config) == Path("/some/rootdir")


# ---------------------------------------------------------------------------
# discover_eval_cases
# ---------------------------------------------------------------------------


def _write_eval(
    tmp_path: object, evals_parent: object, group: object, filename: object = "eval.md"
) -> object:
    """Write one <evals_parent>/evals/<group>/<filename>."""
    group_dir = tmp_path / evals_parent / "evals" / group
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / filename).write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n", encoding="utf-8"
    )
    return group_dir


def test_discover_finds_eval_md_and_dot_eval_md(tmp_path: object) -> None:
    """Verify discover finds eval.md and *.eval.md at any depth."""
    _write_eval(tmp_path, "skills/ingest", "summarize-transcript", "eval.md")
    _write_eval(tmp_path, "docs/probes", "to-spec-activation", "write-spec.eval.md")

    cases = discover_eval_cases(tmp_path)

    by_id = {case.param_id: case for case in cases}
    assert set(by_id) == {
        "summarize-transcript-summarize-transcript",
        "to-spec-activation-write-spec",
    }
    assert by_id["to-spec-activation-write-spec"].eval_id == "write-spec"
    assert by_id["summarize-transcript-summarize-transcript"].skill == "summarize-transcript"


def test_discover_prunes_tmp_git_pycache_and_dot_dirs(tmp_path: object) -> None:
    """Verify discover prunes tmp/.git/__pycache__/dot dirs."""
    _write_eval(tmp_path, "tmp/evals-mirror", "hidden-a")
    _write_eval(tmp_path, ".git/x", "hidden-b")
    _write_eval(tmp_path, "src/__pycache__", "hidden-c")
    _write_eval(tmp_path, ".claude/skills/loc", "hidden-d")
    _write_eval(tmp_path, "skills/real", "kept")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_shared_workspace_for_sibling_evals(tmp_path: object) -> None:
    """Verify sibling *.eval.md files share one workspace/."""
    group_dir = _write_eval(tmp_path, "s", "suite", "one.eval.md")
    (group_dir / "two.eval.md").write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n"
    )
    (group_dir / "workspace").mkdir()
    (group_dir / "workspace" / "seed.md").write_text("body")

    cases = {case.eval_id: case for case in discover_eval_cases(tmp_path)}

    assert cases["one"].workspace_dir == group_dir / "workspace"
    assert cases["two"].workspace_dir == group_dir / "workspace"


def test_discover_history_and_workspace(tmp_path: object) -> None:
    """Verify discover reads history and locates workspace/."""
    group_dir = tmp_path / "skills" / "archive" / "evals" / "clobber"
    group_dir.mkdir(parents=True)
    (group_dir / "eval.md").write_text(
        "---\nhistory:\n  - role: user\n    content: hi\n---\n\n"
        "## Prompt\n\nArchive ./x.\n\n## Assertions\n\n- [ ] it refused\n"
    )
    (group_dir / "workspace").mkdir()
    (group_dir / "workspace" / "x.md").write_text("body")

    [case] = discover_eval_cases(tmp_path)

    assert case.history == [{"role": "user", "content": "hi"}]
    assert case.workspace_dir == group_dir / "workspace"


def test_discover_does_not_descend_into_nested_evals_dir(tmp_path: object) -> None:
    """Verify a nested evals/ dir under a workspace/ fixture is not itself collected."""
    _write_eval(tmp_path, "skills/foo", "bar")
    nested_dir = (
        tmp_path
        / "skills"
        / "foo"
        / "evals"
        / "bar"
        / "workspace"
        / "project"
        / "evals"
        / "baz"
    )
    nested_dir.mkdir(parents=True)
    (nested_dir / "eval.md").write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n", encoding="utf-8"
    )

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["bar-bar"]


def test_discover_raises_on_duplicate_group_eval_pair(tmp_path: object) -> None:
    """Verify discover raises on duplicate (group, eval_id)."""
    _write_eval(tmp_path, "a", "happy", "eval.md")
    _write_eval(tmp_path, "b", "happy", "eval.md")

    with pytest.raises(schema.SchemaError, match="happy"):
        discover_eval_cases(tmp_path)


def test_discover_non_kebab_dunder_group_fails_loudly(tmp_path: object) -> None:
    """Verify a non-kebab `__`-prefixed group raises instead of being silently skipped."""
    _write_eval(tmp_path, "skills/foo", "__bad__")

    with pytest.raises(schema.SchemaError, match="kebab"):
        discover_eval_cases(tmp_path)


def test_discover_no_evals_dir_is_empty(tmp_path: object) -> None:
    """Verify discover with no evals dir is empty."""
    assert discover_eval_cases(tmp_path) == []


def test_discover_group_dir_without_eval_files_is_skipped(tmp_path: object) -> None:
    """Verify a group dir with no eval file is skipped, not raised."""
    (tmp_path / "evals" / "binder").mkdir(parents=True)
    (tmp_path / "evals" / "binder" / "corpus.yaml").write_text("x: 1")
    _write_eval(tmp_path, "skills/real", "kept")

    assert [case.param_id for case in discover_eval_cases(tmp_path)] == ["kept-kept"]


def test_discover_rejects_unknown_frontmatter_end_to_end(tmp_path: object) -> None:
    """Verify discover rejects unknown frontmatter end to end."""
    # The mdformat → discovery seam: an unknown frontmatter key must surface as an
    # error through the whole chain, not pass silently. The body is well-formed
    # except for the unknown `id` key, so this isolates that guard.
    group_dir = tmp_path / "skills" / "ingest" / "evals" / "bad-prompt"
    group_dir.mkdir(parents=True)
    (group_dir / "eval.md").write_text(
        "---\nid: bad-prompt\n---\n\n## Prompt\n\np\n\n## Assertions\n\n- [ ] x\n"
    )

    with pytest.raises(MdFormatError):
        discover_eval_cases(tmp_path)


def test_skill_with_only_triggers_still_discovers_no_eval_cases(
    tmp_path: object,
) -> None:
    """Verify skill with only triggers still discovers no eval cases."""
    _write_trigger_md(
        tmp_path / "skills" / "demo",
        ["- my-query: q\n"],
    )

    assert discover_eval_cases(tmp_path) == []
    assert len(discover_trigger_cases(tmp_path)) == 1


def test_discover_eval_cases_raises_on_bad_schema(tmp_path: object) -> None:
    """Verify discover eval cases raises on bad schema."""
    # Malformed here = a bare `## Prompt` with no `## Assertions` section.
    group_dir = tmp_path / "skills" / "bad-prompt" / "evals" / "a"
    group_dir.mkdir(parents=True)
    bad = group_dir / "eval.md"
    bad.write_text("---\n{}\n---\n\n## Prompt\n\np\n")

    with pytest.raises((MdFormatError, schema.SchemaError)) as exc_info:
        discover_eval_cases(tmp_path)

    assert str(bad) in str(exc_info.value)  # the offending file is named


# ---------------------------------------------------------------------------
# discover_trigger_cases
# ---------------------------------------------------------------------------


def test_discover_trigger_cases_one_per_query(tmp_path: object) -> None:
    """Verify discover trigger cases one per query."""
    _write_triggers(
        tmp_path / "skills" / "myskill",
        [
            "- do-it: do it\n",
            "- not-this: not this\n",
        ],
    )
    cases = discover_trigger_cases(tmp_path)
    assert [case.param_id for case in cases] == ["myskill-do-it", "myskill-not-this"]
    assert all(case.skill_name == "myskill" for case in cases)
    assert all(case.repo_root == tmp_path for case in cases)


def test_discover_trigger_reads_markdown(tmp_path: object) -> None:
    """Verify discover trigger reads markdown."""
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


def test_discover_trigger_cases_finds_both_roots(tmp_path: object) -> None:
    """Verify discover trigger cases finds both roots."""
    _write_triggers(
        tmp_path / "skills" / "plug",
        ["- some-query: q\n"],
    )
    _write_triggers(
        tmp_path / ".claude" / "skills" / "loc",
        ["- some-query: q\n"],
    )
    cases = {case.skill: case for case in discover_trigger_cases(tmp_path)}
    assert set(cases) == {"plug", "loc"}
    assert all(case.repo_root == tmp_path for case in cases.values())


# ---------------------------------------------------------------------------
# pyproject_table
# ---------------------------------------------------------------------------


def test_pyproject_table_reads_tool_evalspec(tmp_path: object) -> None:
    """Verify pyproject table reads tool evalspec."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.evalspec]\nagent = "opencode"\neval_roots = ["evals/skills"]\n'
    )
    table = discovery.pyproject_table(tmp_path)
    assert table == {"agent": "opencode", "eval_roots": ["evals/skills"]}


def test_pyproject_table_missing_file_is_empty(tmp_path: object) -> None:
    """Verify pyproject table missing file is empty."""
    assert discovery.pyproject_table(tmp_path) == {}


def test_output_and_trigger_evals_coexist(tmp_path: object) -> None:
    """Verify output and trigger evals coexist."""
    # A skill dir with BOTH a valid output eval.md AND trigger-evals.md under evals/:
    # discover_eval_cases must not raise, must return the output eval case, and must
    # not include anything derived from trigger-evals.md (a bare file, never a group
    # dir). discover_trigger_cases must return the trigger case.
    skill_dir = tmp_path / "skills" / "myskill"
    _write_eval(tmp_path, "skills/myskill", "myskill", "eval.md")
    _write_triggers(skill_dir, ["- my-query: do this thing\n"])

    output_cases = discover_eval_cases(tmp_path)
    trigger_cases = discover_trigger_cases(tmp_path)

    # Output cases: exactly one, from the output eval file — no bleed from trigger-evals.md
    assert len(output_cases) == 1
    assert output_cases[0].eval_id == "myskill"
    assert output_cases[0].skill == "myskill"
    assert not any("trigger" in case.eval_id for case in output_cases)

    # Trigger cases: exactly one, from trigger-evals.md
    assert len(trigger_cases) == 1
    assert trigger_cases[0].param_id == "myskill-my-query"


# ---------------------------------------------------------------------------
# resolve_environment_config / EnvConfig
# ---------------------------------------------------------------------------


def _write_pyproject(tmp_path: object, body: str) -> None:
    """Write pyproject."""
    (tmp_path / "pyproject.toml").write_text(f"[tool.evalspec]\n{body}", encoding="utf-8")


def test_env_config_absent_is_falsy_and_empty_digest(tmp_path: object) -> None:
    """Verify env config absent is falsy and empty digest."""
    fake_config = resolve_environment_config(tmp_path)  # no pyproject.toml at all
    assert bool(fake_config) is False
    assert fake_config.base_image is None
    assert fake_config.script == b""
    assert fake_config.digest() == ""


def test_env_config_base_image_only(tmp_path: object) -> None:
    """Verify env config base image only."""
    _write_pyproject(tmp_path, 'base_image = "python:3.12-slim"\n')
    fake_config = resolve_environment_config(tmp_path)
    assert bool(fake_config) is True
    assert fake_config.base_image == "python:3.12-slim"
    assert fake_config.script == b""
    assert len(fake_config.digest()) == 8


def test_env_config_script_resolved_to_bytes(tmp_path: object) -> None:
    """Verify env config script resolved to bytes."""
    (tmp_path / "setup.sh").write_text("apt-get install -y jq\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    fake_config = resolve_environment_config(tmp_path)
    assert bool(fake_config) is True
    assert fake_config.script == b"apt-get install -y jq\n"
    assert fake_config.script_path == "setup.sh"
    assert len(fake_config.digest()) == 8


def test_env_config_digest_changes_with_script_bytes(tmp_path: object) -> None:
    """Verify env config digest changes with script bytes."""
    (tmp_path / "setup.sh").write_text("echo one\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    first = resolve_environment_config(tmp_path).digest()
    (tmp_path / "setup.sh").write_text("echo two\n", encoding="utf-8")  # in-place edit
    second = resolve_environment_config(tmp_path).digest()
    assert first != second


def test_env_config_digest_ignores_script_path(tmp_path: object) -> None:
    """Verify env config digest ignores script path."""
    # Same bytes under two different paths ⇒ same digest (path is not hashed).
    (tmp_path / "a.sh").write_text("echo same\n", encoding="utf-8")
    (tmp_path / "b.sh").write_text("echo same\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "a.sh"\n')
    digest_a = resolve_environment_config(tmp_path).digest()
    _write_pyproject(tmp_path, 'environment_script = "b.sh"\n')
    digest_b = resolve_environment_config(tmp_path).digest()
    assert digest_a == digest_b


def test_env_config_base_image_wrong_type_raises(tmp_path: object) -> None:
    """Verify env config base image wrong type raises."""
    _write_pyproject(tmp_path, "base_image = 7\n")
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_base_image_empty_string_raises(tmp_path: object) -> None:
    """Verify env config base image empty string raises."""
    _write_pyproject(tmp_path, 'base_image = ""\n')
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_environment_script_wrong_type_raises(tmp_path: object) -> None:
    """Verify env config environment script wrong type raises."""
    _write_pyproject(tmp_path, "environment_script = true\n")
    with pytest.raises(schema.SchemaError, match="environment_script"):
        resolve_environment_config(tmp_path)


def test_env_config_environment_script_missing_file_raises(tmp_path: object) -> None:
    """Verify env config environment script missing file raises."""
    _write_pyproject(tmp_path, 'environment_script = "nope.sh"\n')
    with pytest.raises(schema.SchemaError, match="file not found: nope.sh"):
        resolve_environment_config(tmp_path)
