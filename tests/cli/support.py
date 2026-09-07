"""Plumbing shared by the CLI end-to-end tests.

Every test in this package spawns the real `python -m benchspec` entry point against a
throwaway repo under `tmp_path`, so argparse, dispatch, and each subcommand's body all
execute for real. The helpers here only build that repo and launch the process; each
test keeps its own inputs and assertions inline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sandbox"


def run_benchspec(
    *argv: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    drop: Iterable[str] = (),
) -> subprocess.CompletedProcess:
    """Run the real `benchspec` console entry point and capture its output.

    The child inherits this process's environment so the venv and any provider
    credentials resolve the same way a developer's shell would. `cwd` is always a
    `tmp_path` so the repo's own `.env` is never picked up by the dotenv walk.

    Args:
        *argv: The subcommand and its arguments, exactly as typed after `benchspec`.
        cwd: The working directory for the child process.
        env: Variables to set (or override) in the child's environment.
        drop: Variable names to remove from the child's environment before `env` applies.

    Returns:
        The completed process with `returncode`, `stdout`, and `stderr` as text.
    """
    dropped = set(drop)
    child_env = {name: value for name, value in os.environ.items() if name not in dropped}
    child_env.update(env or {})

    return subprocess.run(
        [sys.executable, "-m", "benchspec", *argv],
        cwd=cwd,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


def write_eval(repo_root: Path, assertions: list[str], *, slug: str = "greets") -> Path:
    """Write a minimal eval under `skills/demo/evals/<slug>/eval.md` and return its path.

    A non-kebab `slug` makes the eval malformed, which is how the usage-error tests
    trigger a `SchemaError` at discovery.
    """
    eval_file = repo_root / "skills" / "demo" / "evals" / slug / "eval.md"
    eval_file.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {assertion}\n" for assertion in assertions)
    header = dedent("""\
        ---
        ---

        ## Prompt

        p

        ## Assertions

    """)
    eval_file.write_text(header + body)
    return eval_file
