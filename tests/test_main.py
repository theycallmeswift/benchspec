"""Command dispatch for the `evalspec` CLI entry point.

Each test drives `main` with real argv and asserts which subcommand handler ran and the
root it resolved, with the handler stubbed so no eval discovery happens.
"""

from __future__ import annotations

from pathlib import Path

from evalspec import __main__


def test_analyze_command_dispatches_to_analyze_run(monkeypatch: object) -> None:
    """Verify `evalspec analyze <dir>` routes to analyze.run with the resolved root."""
    seen = []
    monkeypatch.setattr(__main__.analyze, "run", lambda root: seen.append(root) or 0)

    exit_code = __main__.main(["analyze", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]


def test_lint_command_dispatches_to_lint_run(monkeypatch: object) -> None:
    """Verify `evalspec lint <dir>` still routes to lint.run with the resolved root."""
    seen = []
    monkeypatch.setattr(__main__.lint, "run", lambda root: seen.append(root) or 0)

    exit_code = __main__.main(["lint", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]
