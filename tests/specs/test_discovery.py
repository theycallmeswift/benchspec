"""Tests for discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchspec.specs import discovery, schema
from benchspec.specs.discovery import (
    discover_eval_cases,
    resolve_environment_config,
    resolve_repo_root,
)
from benchspec.specs.mdformat import MdFormatError


class _FakeConfig:
    """A `RunOptions` stand-in carrying only the repo-root and eval-paths options."""

    def __init__(
        self,
        repo_root: str | None = None,
        rootpath: Path = Path("/fallback/root"),
        eval_paths: str | None = None,
    ) -> None:
        """Store the option values the discovery resolvers read."""
        self._repo_root = repo_root
        self.rootpath = rootpath
        self._eval_paths = eval_paths

    def getoption(self, name: str) -> object:
        """Return the configured value for `name`, or None for any other option."""
        if name == "benchspec_repo_root":
            return self._repo_root
        if name == "benchspec_eval_paths":
            return self._eval_paths
        return None


# ---------------------------------------------------------------------------
# resolve_repo_root
# ---------------------------------------------------------------------------


def test_resolve_repo_root_prefers_option(tmp_path: Path) -> None:
    """Verify resolve repo root prefers option."""
    fake_config = _FakeConfig(repo_root=str(tmp_path))
    assert resolve_repo_root(fake_config) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_project_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify resolve repo root falls back to project root env."""
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    assert resolve_repo_root(_FakeConfig()) == tmp_path.resolve()


def test_resolve_repo_root_falls_back_to_rootpath(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify resolve repo root falls back to rootpath."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    fake_config = _FakeConfig(rootpath=Path("/some/rootdir"))
    assert resolve_repo_root(fake_config) == Path("/some/rootdir")


# ---------------------------------------------------------------------------
# discover_eval_cases
# ---------------------------------------------------------------------------


def _write_eval(
    tmp_path: Path, evals_parent: str, group: str, filename: str = "eval.md"
) -> Path:
    """Write one <evals_parent>/evals/<group>/<filename>."""
    group_dir = tmp_path / evals_parent / "evals" / group
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / filename).write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n", encoding="utf-8"
    )
    return group_dir


def _write_eval_at(tmp_path: Path, group_reldir: str, filename: str = "eval.md") -> Path:
    """Write one eval file at <group_reldir>/<filename> (no forced `evals/` infix)."""
    group_dir = tmp_path / group_reldir
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / filename).write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n", encoding="utf-8"
    )
    return group_dir


def test_discover_finds_eval_md_and_dot_eval_md(tmp_path: Path) -> None:
    """Verify discover finds eval.md and *.eval.md across default search paths."""
    _write_eval(tmp_path, "skills/ingest", "summarize-transcript", "eval.md")
    _write_eval(tmp_path, "tests", "to-spec-activation", "write-spec.eval.md")

    cases = discover_eval_cases(tmp_path)

    by_id = {case.param_id: case for case in cases}
    assert set(by_id) == {
        "summarize-transcript-summarize-transcript",
        "to-spec-activation-write-spec",
    }
    assert by_id["to-spec-activation-write-spec"].eval_id == "write-spec"
    assert by_id["summarize-transcript-summarize-transcript"].skill == "summarize-transcript"


def test_discover_marker_is_filename_not_evals_segment(tmp_path: Path) -> None:
    """Verify an eval file is found by filename alone, with no `evals/` ancestor."""
    _write_eval_at(tmp_path, "tests/happy-path")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["happy-path-happy-path"]
    assert cases[0].group == "happy-path"


def test_discover_ignores_evals_outside_search_paths(tmp_path: Path) -> None:
    """Verify an eval under a non-configured path (e.g. build/) is not discovered."""
    _write_eval(tmp_path, "build/copied", "my-case")
    _write_eval(tmp_path, "skills/real", "kept")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_defaults_search_skills_not_arbitrary_dirs(tmp_path: Path) -> None:
    """Verify the default search paths find `skills/` evals but not a `probes/` dir."""
    _write_eval(tmp_path, "probes", "custom")
    _write_eval(tmp_path, "skills/real", "kept")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_explicit_eval_paths_argument_overrides_defaults(tmp_path: Path) -> None:
    """Verify an explicit eval_paths list replaces the defaults."""
    _write_eval(tmp_path, "probes", "custom")
    _write_eval(tmp_path, "skills/real", "kept")

    cases = discover_eval_cases(tmp_path, ["probes"])

    assert [case.param_id for case in cases] == ["custom-custom"]


def test_discover_reads_eval_paths_from_pyproject(tmp_path: Path) -> None:
    """Verify discover falls back to [tool.benchspec] eval_paths when none is passed."""
    (tmp_path / "pyproject.toml").write_text('[tool.benchspec]\neval_paths = ["probes"]\n')
    _write_eval(tmp_path, "probes", "custom")
    _write_eval(tmp_path, "skills/real", "not-searched")

    assert [case.param_id for case in discover_eval_cases(tmp_path)] == ["custom-custom"]


def test_discover_prunes_scratch_cache_and_dot_dirs_within_a_root(tmp_path: Path) -> None:
    """Verify scratch/cache/dot dirs are pruned inside a configured search path."""
    _write_eval(tmp_path, "skills/tmp", "hidden-a")
    _write_eval(tmp_path, "skills/__pycache__", "hidden-b")
    _write_eval(tmp_path, "skills/.hidden", "hidden-c")
    _write_eval(tmp_path, "skills/real", "kept")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_honors_dot_prefixed_configured_root(tmp_path: Path) -> None:
    """Verify a dot-prefixed configured root is walked even though dot-dirs prune."""
    _write_eval(tmp_path, ".claude/skills/loc", "kept")

    cases = discover_eval_cases(tmp_path, [".claude/skills"])

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_does_not_cross_embedded_git_repo(tmp_path: Path) -> None:
    """Verify an embedded Git repo under a search path is a discovery boundary."""
    _write_eval(tmp_path, "skills/source", "kept")
    embedded_root = tmp_path / "skills" / "vendored"
    embedded_root.mkdir(parents=True)
    (embedded_root / ".git").mkdir()
    _write_eval(embedded_root, "package", "hidden")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["kept-kept"]


def test_discover_shared_workspace_for_sibling_evals(tmp_path: Path) -> None:
    """Verify sibling *.eval.md files share one workspace/."""
    group_dir = _write_eval(tmp_path, "skills/s", "suite", "one.eval.md")
    (group_dir / "two.eval.md").write_text(
        "---\n{}\n---\n\n## Prompt\n\nx\n\n## Assertions\n\n- [ ] a\n"
    )
    (group_dir / "workspace").mkdir()
    (group_dir / "workspace" / "seed.md").write_text("body")

    cases = {case.eval_id: case for case in discover_eval_cases(tmp_path)}

    assert cases["one"].workspace_dir == group_dir / "workspace"
    assert cases["two"].workspace_dir == group_dir / "workspace"


def test_discover_history_and_workspace(tmp_path: Path) -> None:
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


def test_discover_does_not_descend_into_eval_workspace(tmp_path: Path) -> None:
    """Verify a nested eval file under a workspace/ fixture is not collected."""
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


def test_discover_finds_nested_non_workspace_evals(tmp_path: Path) -> None:
    """Verify a nested evals root outside workspace remains discoverable."""
    _write_eval(tmp_path, "tests/examples/sample-project", "smoke")

    cases = discover_eval_cases(tmp_path)

    assert [case.param_id for case in cases] == ["smoke-smoke"]


def test_discover_skips_symlinked_group(tmp_path: Path) -> None:
    """Verify a symlinked eval group is skipped, not followed."""
    outside_group = _write_eval(tmp_path, "outside", "escaped")
    evals_dir = tmp_path / "skills" / "source" / "evals"
    evals_dir.mkdir(parents=True)
    (evals_dir / "escaped").symlink_to(outside_group, target_is_directory=True)

    assert discover_eval_cases(tmp_path) == []


def test_discover_skips_symlinked_eval_file(tmp_path: Path) -> None:
    """Verify a symlinked eval.md is skipped, not followed."""
    real = _write_eval_at(tmp_path, "outside/real")
    group_dir = tmp_path / "skills" / "demo" / "evals" / "linked"
    group_dir.mkdir(parents=True)
    (group_dir / "eval.md").symlink_to(real / "eval.md")

    assert discover_eval_cases(tmp_path) == []


def test_discover_ignores_symlinked_trigger_file(tmp_path: Path) -> None:
    """Verify output discovery ignores a symlinked trigger file beside groups."""
    evals_dir = tmp_path / "skills" / "demo" / "evals"
    evals_dir.mkdir(parents=True)
    trigger_source = tmp_path / "trigger-source.md"
    trigger_source.write_text("trigger data", encoding="utf-8")
    (evals_dir / "trigger-evals.md").symlink_to(trigger_source)

    assert discover_eval_cases(tmp_path) == []


def test_discover_raises_on_duplicate_group_eval_pair(tmp_path: Path) -> None:
    """Verify discover raises on duplicate (group, eval_id) across search paths."""
    _write_eval(tmp_path, "skills", "happy", "eval.md")
    _write_eval(tmp_path, "tests", "happy", "eval.md")

    with pytest.raises(schema.SchemaError, match="happy"):
        discover_eval_cases(tmp_path)


def test_discover_non_kebab_dunder_group_fails_loudly(tmp_path: Path) -> None:
    """Verify a non-kebab `__`-prefixed group raises instead of being silently skipped."""
    _write_eval(tmp_path, "skills/foo", "__bad__")

    with pytest.raises(schema.SchemaError, match="kebab"):
        discover_eval_cases(tmp_path)


def test_discover_no_evals_dir_is_empty(tmp_path: Path) -> None:
    """Verify discover with no evals dir is empty."""
    assert discover_eval_cases(tmp_path) == []


def test_discover_group_dir_without_eval_files_is_skipped(tmp_path: Path) -> None:
    """Verify a group dir with no eval file is skipped, not raised."""
    (tmp_path / "evals" / "binder").mkdir(parents=True)
    (tmp_path / "evals" / "binder" / "corpus.yaml").write_text("x: 1")
    _write_eval(tmp_path, "skills/real", "kept")

    assert [case.param_id for case in discover_eval_cases(tmp_path)] == ["kept-kept"]


def test_discover_rejects_unknown_frontmatter_end_to_end(tmp_path: Path) -> None:
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


def test_discover_eval_cases_raises_on_bad_schema(tmp_path: Path) -> None:
    """Verify discover eval cases raises on bad schema."""
    # Malformed here = a bare `## Prompt` with no `## Assertions` section.
    group_dir = tmp_path / "skills" / "bad-prompt" / "evals" / "a"
    group_dir.mkdir(parents=True)
    bad = group_dir / "eval.md"
    bad.write_text("---\n{}\n---\n\n## Prompt\n\np\n")

    with pytest.raises((MdFormatError, schema.SchemaError)) as exc_info:
        discover_eval_cases(tmp_path)

    assert str(bad) in str(exc_info.value)  # the offending file is named


def test_resolve_eval_paths_defaults_when_unset(tmp_path: Path) -> None:
    """Verify resolve_eval_paths falls back to the built-in default paths."""
    config = _FakeConfig(repo_root=str(tmp_path))

    assert discovery.resolve_eval_paths(config) == ["skills", "tests", "evals", "benchmarks"]


def test_resolve_eval_paths_reads_pyproject(tmp_path: Path) -> None:
    """Verify resolve_eval_paths reads [tool.benchspec] eval_paths when no flag is set."""
    (tmp_path / "pyproject.toml").write_text('[tool.benchspec]\neval_paths = ["probes"]\n')
    config = _FakeConfig(repo_root=str(tmp_path))

    assert discovery.resolve_eval_paths(config) == ["probes"]


def test_resolve_eval_paths_flag_overrides_pyproject(tmp_path: Path) -> None:
    """Verify the CLI flag wins over pyproject and is split on commas."""
    (tmp_path / "pyproject.toml").write_text('[tool.benchspec]\neval_paths = ["probes"]\n')
    config = _FakeConfig(repo_root=str(tmp_path), eval_paths="a, b ,c")

    assert discovery.resolve_eval_paths(config) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# pyproject_table
# ---------------------------------------------------------------------------


def test_pyproject_table_reads_tool_benchspec(tmp_path: Path) -> None:
    """Verify pyproject table reads tool benchspec."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.benchspec]\nagent = "opencode"\neval_paths = ["evals/skills"]\n'
    )
    table = discovery.pyproject_table(tmp_path)
    assert table == {"agent": "opencode", "eval_paths": ["evals/skills"]}


def test_pyproject_table_missing_file_is_empty(tmp_path: Path) -> None:
    """Verify pyproject table missing file is empty."""
    assert discovery.pyproject_table(tmp_path) == {}


# ---------------------------------------------------------------------------
# resolve_environment_config / EnvConfig
# ---------------------------------------------------------------------------


def _write_pyproject(tmp_path: Path, body: str) -> None:
    """Write pyproject."""
    (tmp_path / "pyproject.toml").write_text(f"[tool.benchspec]\n{body}", encoding="utf-8")


def test_env_config_absent_is_falsy_and_empty_digest(tmp_path: Path) -> None:
    """Verify env config absent is falsy and empty digest."""
    fake_config = resolve_environment_config(tmp_path)  # no pyproject.toml at all
    assert bool(fake_config) is False
    assert fake_config.base_image is None
    assert fake_config.script == b""
    assert fake_config.digest() == ""


def test_env_config_base_image_only(tmp_path: Path) -> None:
    """Verify env config base image only."""
    _write_pyproject(tmp_path, 'base_image = "python:3.12-slim"\n')
    fake_config = resolve_environment_config(tmp_path)
    assert bool(fake_config) is True
    assert fake_config.base_image == "python:3.12-slim"
    assert fake_config.script == b""
    assert len(fake_config.digest()) == 8


def test_env_config_script_resolved_to_bytes(tmp_path: Path) -> None:
    """Verify env config script resolved to bytes."""
    (tmp_path / "setup.sh").write_text("apt-get install -y jq\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    fake_config = resolve_environment_config(tmp_path)
    assert bool(fake_config) is True
    assert fake_config.script == b"apt-get install -y jq\n"
    assert fake_config.script_path == "setup.sh"
    assert len(fake_config.digest()) == 8


def test_env_config_digest_changes_with_script_bytes(tmp_path: Path) -> None:
    """Verify env config digest changes with script bytes."""
    (tmp_path / "setup.sh").write_text("echo one\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "setup.sh"\n')
    first = resolve_environment_config(tmp_path).digest()
    (tmp_path / "setup.sh").write_text("echo two\n", encoding="utf-8")  # in-place edit
    second = resolve_environment_config(tmp_path).digest()
    assert first != second


def test_env_config_digest_ignores_script_path(tmp_path: Path) -> None:
    """Verify env config digest ignores script path."""
    # Same bytes under two different paths ⇒ same digest (path is not hashed).
    (tmp_path / "a.sh").write_text("echo same\n", encoding="utf-8")
    (tmp_path / "b.sh").write_text("echo same\n", encoding="utf-8")
    _write_pyproject(tmp_path, 'environment_script = "a.sh"\n')
    digest_a = resolve_environment_config(tmp_path).digest()
    _write_pyproject(tmp_path, 'environment_script = "b.sh"\n')
    digest_b = resolve_environment_config(tmp_path).digest()
    assert digest_a == digest_b


def test_env_config_base_image_wrong_type_raises(tmp_path: Path) -> None:
    """Verify env config base image wrong type raises."""
    _write_pyproject(tmp_path, "base_image = 7\n")
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_base_image_empty_string_raises(tmp_path: Path) -> None:
    """Verify env config base image empty string raises."""
    _write_pyproject(tmp_path, 'base_image = ""\n')
    with pytest.raises(schema.SchemaError, match="base_image"):
        resolve_environment_config(tmp_path)


def test_env_config_base_image_env_var_wins_over_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify BENCHSPEC_BASE_IMAGE overrides the pyproject base_image."""
    _write_pyproject(tmp_path, 'base_image = "python:3.12-slim"\n')
    monkeypatch.setenv("BENCHSPEC_BASE_IMAGE", "benchspec-base:proxy-ca")

    fake_config = resolve_environment_config(tmp_path)

    assert fake_config.base_image == "benchspec-base:proxy-ca"


def test_env_config_base_image_env_var_without_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify BENCHSPEC_BASE_IMAGE alone selects a base image and keys the digest."""
    monkeypatch.setenv("BENCHSPEC_BASE_IMAGE", "benchspec-base:proxy-ca")

    fake_config = resolve_environment_config(tmp_path)  # no pyproject.toml at all

    assert bool(fake_config) is True
    assert fake_config.base_image == "benchspec-base:proxy-ca"
    assert len(fake_config.digest()) == 8


def test_env_config_base_image_empty_env_var_falls_back_to_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an empty BENCHSPEC_BASE_IMAGE counts as unset."""
    _write_pyproject(tmp_path, 'base_image = "python:3.12-slim"\n')
    monkeypatch.setenv("BENCHSPEC_BASE_IMAGE", "")

    fake_config = resolve_environment_config(tmp_path)

    assert fake_config.base_image == "python:3.12-slim"


def test_env_config_environment_script_wrong_type_raises(tmp_path: Path) -> None:
    """Verify env config environment script wrong type raises."""
    _write_pyproject(tmp_path, "environment_script = true\n")
    with pytest.raises(schema.SchemaError, match="environment_script"):
        resolve_environment_config(tmp_path)


def test_env_config_environment_script_missing_file_raises(tmp_path: Path) -> None:
    """Verify env config environment script missing file raises."""
    _write_pyproject(tmp_path, 'environment_script = "nope.sh"\n')
    with pytest.raises(schema.SchemaError, match="file not found: nope.sh"):
        resolve_environment_config(tmp_path)
