"""End-to-end tests for `benchspec sandbox:clean`.

Each test runs the real console entry point in a subprocess against a throwaway `HOME`
seeded with leaked sandboxes and snapshots, so the CLI, dispatch, and `cli_clean`'s
pruning all execute for real. The one substitution is the `msb` runtime: `MSB_PATH`
points at a shell shim that records every invocation and removes the sandbox or
snapshot directory the way the real binary does, so the suite never needs a booted
microVM runtime and never touches the developer's own `~/.microsandbox`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def _write_msb_shim(shim_dir: Path) -> Path:
    """Write an `msb` stand-in that logs its argv and deletes what `rm` would delete."""
    shim = shim_dir / "msb"
    shim.write_text(dedent("""\
        #!/bin/sh
        printf "%s\\n" "$*" >> "$BENCHSPEC_COMMAND_LOG"
        case "$1 $2" in
          "rm -f") rm -rf "$HOME/.microsandbox/sandboxes/$3" ;;
          "snapshot rm") rm -rf "$HOME/.microsandbox/snapshots/$4" ;;
        esac
    """))
    shim.chmod(0o755)
    return shim


def _run_sandbox_clean(tmp_path: Path, home: Path, repo_root: Path) -> subprocess.CompletedProcess:
    """Run the real `benchspec sandbox:clean <repo_root>` with `HOME` and `msb` redirected."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = _write_msb_shim(shim_dir)

    return subprocess.run(
        [sys.executable, "-m", "benchspec", "sandbox:clean", str(repo_root)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(home),
            "MSB_PATH": str(shim),
            "BENCHSPEC_COMMAND_LOG": str(tmp_path / "commands.log"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_sandbox_clean_removes_leaked_sandboxes_snapshots_and_locks(tmp_path: Path) -> None:
    """Verify the real command prunes matching entries from disk and leaves the rest alone."""
    home = tmp_path / "home"
    sandboxes = home / ".microsandbox" / "sandboxes"
    snapshots = home / ".microsandbox" / "snapshots"
    for leaked_sandbox in ("eval-hello-trial-gw0", "benchspec-build-abc", "trigger-hello-gw1"):
        (sandboxes / leaked_sandbox).mkdir(parents=True)
    (sandboxes / "unrelated-sandbox").mkdir()
    (snapshots / "benchspec-microsandbox-claude-code-latest-c30e39d4").mkdir(parents=True)
    (snapshots / "unrelated-snapshot").mkdir()
    repo_root = tmp_path / "repo"
    lock_file = repo_root / "tmp" / ".benchspec-snapshot-claude-code.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.touch()

    result = _run_sandbox_clean(tmp_path, home, repo_root)

    assert result.returncode == 0, result.stderr
    assert sorted(entry.name for entry in sandboxes.iterdir()) == ["unrelated-sandbox"]
    assert sorted(entry.name for entry in snapshots.iterdir()) == ["unrelated-snapshot"]
    assert not lock_file.exists()
    commands = sorted((tmp_path / "commands.log").read_text().splitlines())
    assert commands == [
        "rm -f benchspec-build-abc",
        "rm -f eval-hello-trial-gw0",
        "rm -f trigger-hello-gw1",
        "snapshot rm --force benchspec-microsandbox-claude-code-latest-c30e39d4",
        "stop benchspec-build-abc",
        "stop eval-hello-trial-gw0",
        "stop trigger-hello-gw1",
    ]


def test_sandbox_clean_on_a_clean_host_is_a_no_op_success(tmp_path: Path) -> None:
    """Verify a host with nothing matching the prefixes exits 0 and never invokes `msb`."""
    home = tmp_path / "home"
    (home / ".microsandbox" / "sandboxes" / "unrelated-sandbox").mkdir(parents=True)
    (home / ".microsandbox" / "snapshots").mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = _run_sandbox_clean(tmp_path, home, repo_root)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "commands.log").exists()
    assert (home / ".microsandbox" / "sandboxes" / "unrelated-sandbox").is_dir()
