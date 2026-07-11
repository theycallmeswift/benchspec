"""Command dispatch for the `evalspec` CLI entry point.

Each test drives `main` with real argv and asserts which subcommand handler ran and the
root it resolved, with the handler stubbed so no eval discovery happens.
"""

from __future__ import annotations

from pathlib import Path

from evalspec import __main__


def test_analyze_command_dispatches_to_analyze_run(monkeypatch: object) -> None:
    """Verify `evalspec analyze <dir>` routes to analyze.run with the resolved root."""
    seen: list[Path] = []

    def fake_run(root: Path) -> int:
        """Record the resolved root and report a success exit code."""
        seen.append(root)
        return 0

    monkeypatch.setattr(__main__.analyze, "run", fake_run)

    exit_code = __main__.main(["analyze", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]


def test_lint_command_dispatches_to_lint_run(monkeypatch: object) -> None:
    """Verify `evalspec lint <dir>` still routes to lint.run with the resolved root."""
    seen: list[Path] = []

    def fake_run(root: Path) -> int:
        """Record the resolved root and report a success exit code."""
        seen.append(root)
        return 0

    monkeypatch.setattr(__main__.lint, "run", fake_run)

    exit_code = __main__.main(["lint", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]


def test_run_command_splits_passthrough_at_double_dash(monkeypatch: object) -> None:
    """Verify args after `--` are split into passthrough while flags before it still parse."""
    seen: list[object] = []

    def fake_run(args: object) -> int:
        """Record the parsed namespace and report a success exit code."""
        seen.append(args)
        return 0

    monkeypatch.setattr(__main__.run, "run", fake_run)

    __main__.main(["run", "--set", "x", "--", "-k", "foo"])

    assert seen[0].passthrough == ["-k", "foo"]
    assert seen[0].set == "x"


def test_run_command_dispatches_to_run_run(monkeypatch: object) -> None:
    """Verify `evalspec run` routes to run.run and returns its exit code."""
    monkeypatch.setattr(__main__.run, "run", lambda args: 7)

    exit_code = __main__.main(["run"])

    assert exit_code == 7
