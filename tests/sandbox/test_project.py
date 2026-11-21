"""Tests for benchspec.sandbox.project — staging the guest's view of the host repo."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tarfile
from pathlib import Path
from typing import NoReturn

import pytest

from benchspec.agents import DEFAULT_CELL_TIMEOUT
from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.events.cell import CellEvents
from benchspec.events.sink import NULL_SINK
from benchspec.orchestration.deadline import CellDeadline
from benchspec.sandbox import project, registry, sandbox
from benchspec.testing import FakeSandbox


def _git(repo_root: Path, *args: str) -> None:
    """Run a git command in `repo_root`, with identity set so commits work in CI."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "-C", str(repo_root), *args], check=True, capture_output=True, env=env)


def _write(path: Path, text: str = "x\n") -> Path:
    """Write a small file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _relative_files(root: Path) -> set[str]:
    """Return every file (and symlink) below `root` as a repo-relative string."""
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def _git_repo_with_secrets(repo_root: Path) -> None:
    """Build a repo shaped like a real project: skills, an ignored .env, prior-run artifacts."""
    repo_root.mkdir(parents=True)
    _git(repo_root, "init", "-q")
    _write(repo_root / ".gitignore", ".env\ntmp/\n.venv/\n")
    _write(repo_root / "pyproject.toml", "[tool.benchspec]\n")
    _write(repo_root / "skills" / "hello" / "SKILL.md", "# hello\n")
    _write(repo_root / "skills" / "hello" / "evals" / "hello" / "setup.sh", "exit 0\n")
    _write(repo_root / ".claude" / "skills" / "local" / "SKILL.md", "# local\n")
    _write(repo_root / ".env.example", "GEMINI_API_KEY=\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")
    _write(repo_root / ".env", "GEMINI_API_KEY=real-secret\n")
    _write(repo_root / "tmp" / "evals" / "iteration_01" / "benchmark.md", "prior run\n")
    _write(repo_root / ".venv" / "lib" / "site.py", "venv\n")
    _write(repo_root / "notes-in-progress.md", "untracked but not ignored\n")


def test_stage_project_copies_a_clone_and_drops_secrets_git_and_artifacts(
    tmp_path: Path,
) -> None:
    """Verify the stage holds what a clone would, minus dotenv files, .git, and tmp/."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)

    staged = project.stage_project(repo_root, staging_parent=tmp_path)

    assert _relative_files(staged) == {
        ".gitignore",
        "pyproject.toml",
        "skills/hello/SKILL.md",
        "skills/hello/evals/hello/setup.sh",
        ".claude/skills/local/SKILL.md",
        "notes-in-progress.md",
    }
    assert not (staged / ".git").exists()


def test_stage_project_preserves_relative_layout(tmp_path: Path) -> None:
    """Verify a path relative to the host root resolves identically under the stage."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)
    eval_dir = repo_root / "skills" / "hello" / "evals" / "hello"
    setup_reldir = eval_dir.relative_to(repo_root)

    staged = project.stage_project(repo_root, staging_parent=tmp_path)

    assert (staged / setup_reldir / "setup.sh").read_text() == "exit 0\n"


def test_stage_project_copies_symlinks_as_links(tmp_path: Path) -> None:
    """Verify a symlink is staged as a link, so an out-of-repo target is not pulled in."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)
    (repo_root / "CLAUDE.md").symlink_to("pyproject.toml")
    outside_secret = _write(tmp_path / "outside" / "secret.txt", "outside\n")
    (repo_root / "leak.txt").symlink_to(outside_secret)
    _git(repo_root, "add", "-A")

    staged = project.stage_project(repo_root, staging_parent=tmp_path)

    assert (staged / "CLAUDE.md").is_symlink()
    assert os.readlink(staged / "CLAUDE.md") == "pyproject.toml"
    assert (staged / "leak.txt").is_symlink()
    assert os.readlink(staged / "leak.txt") == str(outside_secret)


def test_stage_project_drops_tracked_dotenv_files(tmp_path: Path) -> None:
    """Verify a dotenv file is excluded even when someone committed it."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    _write(repo_root / ".env", "GEMINI_API_KEY=committed-by-mistake\n")
    _write(repo_root / "config" / ".env.production", "TOKEN=x\n")
    _write(repo_root / "keep.md")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "oops")

    staged = project.stage_project(repo_root, staging_parent=tmp_path)

    assert _relative_files(staged) == {"keep.md"}


def test_stage_project_falls_back_to_a_walk_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a project outside git is staged by walking, skipping machine-local dirs."""
    monkeypatch.setattr(project, "_git_listed_files", lambda repo_root: None)
    repo_root = tmp_path / "plain"
    _write(repo_root / "skills" / "hello" / "SKILL.md")
    _write(repo_root / ".env", "SECRET=1\n")
    _write(repo_root / ".venv" / "lib" / "site.py")
    _write(repo_root / "node_modules" / "left-pad" / "index.js")
    _write(repo_root / "skills" / "__pycache__" / "x.pyc")
    _write(repo_root / "tmp" / "evals" / "iteration_01" / "benchmark.md")
    _write(repo_root / ".git" / "HEAD", "ref: refs/heads/main\n")

    staged = project.stage_project(repo_root, staging_parent=tmp_path)

    assert _relative_files(staged) == {"skills/hello/SKILL.md"}


def test_stage_project_refuses_a_stage_that_still_holds_a_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the post-stage check fails closed if the filter ever lets a dotenv file through."""
    monkeypatch.setattr(project, "_stageable", lambda relative: True)
    repo_root = tmp_path / "plain"
    _write(repo_root / ".env", "SECRET=1\n")
    _write(repo_root / "keep.md")

    with pytest.raises(RuntimeError, match=r"dotenv file\(s\) would be visible.*\.env"):
        project.stage_project(repo_root, staging_parent=tmp_path)

    assert list(tmp_path.glob("benchspec-project-*")) == []


def test_discard_stage_tolerates_none_and_missing_dirs(tmp_path: Path) -> None:
    """Verify discard is a no-op for None and for a stage already gone."""
    project.discard_stage(None)
    project.discard_stage(tmp_path / "never-created")

    staged = tmp_path / "stage"
    staged.mkdir()
    project.discard_stage(staged)

    assert not staged.exists()


def _stage_with_a_link(tmp_path: Path) -> Path:
    """A stage holding a nested file and a symlink."""
    staged = tmp_path / "stage"
    _write(staged / "skills" / "hello" / "SKILL.md", "# hello\n")
    (staged / "CLAUDE.md").symlink_to("skills/hello/SKILL.md")

    return staged


def test_stage_archive_packs_the_stage_next_to_it(tmp_path: Path) -> None:
    """The archive sits beside the stage and holds its layout, links kept as links."""
    staged = _stage_with_a_link(tmp_path)

    archive = project.stage_archive(staged)

    assert archive.parent == staged.parent
    with tarfile.open(archive, "r:gz") as tar:
        members = {member.name: member for member in tar.getmembers()}
    assert members["skills/hello/SKILL.md"].isfile()
    assert members["CLAUDE.md"].issym()
    assert members["CLAUDE.md"].linkname == "skills/hello/SKILL.md"


def test_stage_archive_is_built_once(tmp_path: Path) -> None:
    """Later calls return the first archive without packing again."""
    staged = _stage_with_a_link(tmp_path)
    project.stage_archive(staged).write_bytes(b"first build")

    assert project.stage_archive(staged).read_bytes() == b"first build"


def test_discard_stage_removes_the_archive(tmp_path: Path) -> None:
    """Discarding a packed stage leaves neither the stage nor its archive."""
    staged = _stage_with_a_link(tmp_path)
    archive = project.stage_archive(staged)

    project.discard_stage(staged)

    assert not staged.exists()
    assert not archive.exists()


def _open_session(create_sandbox: object, monkeypatch: pytest.MonkeyPatch, stage: Path) -> None:
    """Open and close an arm session over `stage` with the backend's create replaced."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", create_sandbox)

    async def drive() -> None:
        """Enter and leave the session."""
        async with sandbox.arm_session(
            deadline=CellDeadline(DEFAULT_CELL_TIMEOUT),
            events=CellEvents(NULL_SINK, "cell"),
            agent=ClaudeCodeAgent(auth_value="test-token", version="v"),
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=stage.parent / "wd",
            host_repo_root=stage,
            model="sonnet",
            effort="medium",
            backend=microsandbox_backend,
        ):
            pass

    asyncio.run(drive())


def test_arm_session_mounts_the_stage_it_is_given_and_leaves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The backend mounts the run's stage as is, and the session does not remove it."""
    stage = _stage_with_a_link(tmp_path)
    create_kwargs: dict[str, object] = {}

    async def fake_create(**kwargs: object) -> FakeSandbox:
        """Record the mount the backend was asked for."""
        create_kwargs.update(kwargs)

        return FakeSandbox()

    _open_session(fake_create, monkeypatch, stage)

    assert create_kwargs["host_repo_root"] == stage
    assert stage.is_dir()


def test_arm_session_leaves_the_stage_when_boot_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A create failure leaves the shared stage for the run's other cells."""
    stage = _stage_with_a_link(tmp_path)

    async def boom(**kwargs: object) -> NoReturn:
        """Fail the boot."""
        raise RuntimeError("boot failed")

    with pytest.raises(RuntimeError, match="boot failed"):
        _open_session(boom, monkeypatch, stage)

    assert stage.is_dir()
