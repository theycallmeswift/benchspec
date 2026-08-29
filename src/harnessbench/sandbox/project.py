"""Staging the host project for the guest's read-only `/project` mount.

The guest needs to see the repository so a per-eval `setup.sh` can install the skill
under test and an agent can load project-local skills. It must not see the checkout
itself: a bind mount of the repo root exposes `.env` (every host credential — none of
which the guest needs, since its own credential arrives as a host-scoped secret), the
`.git` history, and `tmp/` (prior runs' transcripts and grades). `stage_project` copies
what a clone would contain — tracked files plus untracked files git does not ignore —
into a fresh directory, and the session mounts that copy instead.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# harnessbench's own artifact root under the repo; never part of the project under test.
_ARTIFACT_DIR = "tmp"

# Excluded whether or not git lists them.
_ALWAYS_EXCLUDED_DIRS = frozenset({".git", _ARTIFACT_DIR})

# The no-git fallback cannot honor .gitignore, so it drops the usual heavyweight and
# machine-local directories by name instead.
_FALLBACK_EXCLUDED_DIRS = _ALWAYS_EXCLUDED_DIRS | frozenset(
    {".venv", "node_modules", "__pycache__", ".worktrees"}
)


def is_env_file(name: str) -> bool:
    """Return whether a file name is a dotenv file (`.env`, `.env.local`, `.env.example`, …)."""
    return name == ".env" or name.startswith(".env.")


def _git_listed_files(repo_root: Path) -> list[Path] | None:
    """Return the repo-relative files a clone would contain, or None when git can't say.

    `--cached --others --exclude-standard` is tracked files plus untracked files that
    `.gitignore` (and the user's global excludes) do not ignore — the working tree as a
    fresh contributor would see it. Paths come back relative to `repo_root` even when it
    is a subdirectory of the git checkout.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    entries = completed.stdout.decode("utf-8", "replace").split("\0")
    return [Path(entry) for entry in entries if entry]


def _walked_files(repo_root: Path) -> list[Path]:
    """Return repo-relative files by walking the tree, for a project outside git."""
    files: list[Path] = []
    for path in sorted(repo_root.rglob("*")):
        relative = path.relative_to(repo_root)
        if any(part in _FALLBACK_EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_file() or path.is_symlink():
            files.append(relative)
    return files


def _stageable(relative: Path) -> bool:
    """Return whether a repo-relative path may be copied into the guest's view."""
    if is_env_file(relative.name):
        return False
    return not any(part in _ALWAYS_EXCLUDED_DIRS for part in relative.parts)


def _env_files_under(root: Path) -> list[str]:
    """Return every dotenv file path below `root`, relative, for the post-stage check."""
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if is_env_file(path.name)
    )


def stage_project(repo_root: Path, staging_parent: Path | None = None) -> Path:
    """Copy the project's clone-visible files into a fresh directory for the guest mount.

    The copy preserves the repo-relative layout, so `setup.sh` locations, project-local
    skill directories, and `--plugin-dir /project` resolve exactly as they would against
    the checkout. Symlinks are copied as links, not followed — a link that points outside
    the repo dangles in the guest rather than pulling the target in.

    Args:
        repo_root: The host repository root to stage.
        staging_parent: Directory to create the stage under (default: the OS temp root).

    Returns:
        The staged directory. The caller removes it once the sandbox is gone.

    Raises:
        RuntimeError: if a dotenv file would still be visible in the stage.
    """
    repo_root = Path(repo_root)
    staged = Path(tempfile.mkdtemp(prefix="harnessbench-project-", dir=staging_parent))

    listed = _git_listed_files(repo_root)
    files = listed if listed is not None else _walked_files(repo_root)
    for relative in files:
        source = repo_root / relative
        # A tracked-but-deleted file is still listed by `--cached`; a submodule gitlink
        # is a directory. Neither is a file to copy.
        if not _stageable(relative) or not (source.is_file() or source.is_symlink()):
            continue
        destination = staged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)

    leaked = _env_files_under(staged)
    if leaked:
        shutil.rmtree(staged, ignore_errors=True)
        raise RuntimeError(
            "refusing to mount the project: dotenv file(s) would be visible in the guest: "
            + ", ".join(leaked)
        )
    return staged


def discard_stage(staged: Path | None) -> None:
    """Remove a staged project directory; a missing or partly removed stage is fine."""
    if staged is not None:
        shutil.rmtree(staged, ignore_errors=True)
