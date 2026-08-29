"""Tests for harnessbench.sandbox.project — staging the guest's view of the host repo."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.sandbox import backend as backend_mod
from harnessbench.sandbox import project, sandbox
from harnessbench.testing import FakeSandbox


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
    _write(repo_root / "pyproject.toml", "[tool.harnessbench]\n")
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

    assert staged == staged.resolve()
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

    assert list(tmp_path.glob("harnessbench-project-*")) == []


def test_discard_stage_tolerates_none_and_missing_dirs(tmp_path: Path) -> None:
    """Verify discard is a no-op for None and for a stage already gone."""
    project.discard_stage(None)
    project.discard_stage(tmp_path / "never-created")

    staged = tmp_path / "stage"
    staged.mkdir()
    project.discard_stage(staged)

    assert not staged.exists()


def test_arm_session_mounts_a_staged_copy_and_removes_it_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the backend mounts a stage without .env, never the checkout, then removes it."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)
    fake = FakeSandbox()
    create_kwargs: dict = {}

    async def fake_create(**kwargs: object) -> object:
        """Record the mount the backend was asked for."""
        create_kwargs.update(kwargs)
        return fake

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", fake_create)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> Path:
        """Open a session and report what was mounted while it was live."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=repo_root,
            model="sonnet",
            effort="medium",
            backend=microsandbox_backend,
        ):
            mounted = Path(create_kwargs["host_repo_root"])
            assert mounted != repo_root
            assert (mounted / "skills" / "hello" / "SKILL.md").is_file()
            assert not (mounted / ".env").exists()
            assert not (mounted / "tmp").exists()
            return mounted

    mounted = asyncio.run(drive())

    assert not mounted.exists()


def test_arm_session_removes_the_stage_when_boot_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify a create failure does not leave a staged copy behind."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)
    create_kwargs: dict = {}

    async def boom(**kwargs: object) -> object:
        """Fail the boot after recording the mount."""
        create_kwargs.update(kwargs)
        raise RuntimeError("boot failed")

    microsandbox_backend = backend_mod.resolve_sandbox("microsandbox")
    monkeypatch.setattr(microsandbox_backend, "create_sandbox", boom)
    agent = ClaudeCodeAgent(auth_value="test-token", version="v")

    async def drive() -> None:
        """Open a session that fails to boot."""
        async with sandbox.arm_session(
            agent=agent,
            snapshot="snap",
            eval_id="e1",
            config="trial",
            host_workdir=tmp_path / "wd",
            host_repo_root=repo_root,
            model="sonnet",
            effort="medium",
            backend=microsandbox_backend,
        ):
            pass

    with pytest.raises(RuntimeError, match="boot failed"):
        asyncio.run(drive())

    assert not Path(create_kwargs["host_repo_root"]).exists()


def test_route_in_sandbox_mounts_a_staged_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify trigger routing also mounts a stage rather than the checkout."""
    repo_root = tmp_path / "repo"
    _git_repo_with_secrets(repo_root)
    create_kwargs: dict = {}

    class FakeHandle:
        """Yield one dispatch line, then end."""

        def __init__(self: object) -> None:
            """Initialize the instance."""
            self._sent = False

        def __aiter__(self: object) -> object:
            """Return self as the async iterator."""
            return self

        async def __anext__(self: object) -> object:
            """Emit a single exited event."""
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return type("Event", (), {"event_type": "exited", "code": 0, "data": b""})()

        async def kill(self: object) -> None:
            """Kill."""

    class FakeTriggerSandbox:
        """Fake trigger VM."""

        async def shell(self: object, *args: object, **kwargs: object) -> object:
            """Shell."""
            return type("Out", (), {"exit_code": 0, "stdout_text": "", "stderr_text": ""})()

        async def exec_stream(self: object, *args: object, **kwargs: object) -> object:
            """Exec stream."""
            return FakeHandle()

        async def stop(self: object, timeout: object = None) -> None:
            """Stop."""

    async def fake_create_trigger(self: object, **kwargs: object) -> object:
        """Record the mount and return the fake VM."""
        create_kwargs.update(kwargs)
        return FakeTriggerSandbox()

    monkeypatch.setattr(sandbox, "ensure_snapshot", lambda agent, **kwargs: "snap")
    monkeypatch.setattr(
        sandbox, "make_agent", lambda *args: ClaudeCodeAgent(auth_value="t", version="v")
    )
    monkeypatch.setattr(
        backend_mod.MicrosandboxBackend, "create_trigger_sandbox", fake_create_trigger
    )

    with pytest.raises(sandbox.RoutingError):
        sandbox.route_in_sandbox("query", repo_root, "sonnet", 20, skill_name="archive")

    mounted = Path(create_kwargs["host_repo_root"])
    assert mounted != repo_root
    assert not mounted.exists()
