"""Command dispatch for the `evalspec` CLI entry point.

Each test drives `main` with real argv and asserts which subcommand handler ran and the
root it resolved, with the handler stubbed so no eval discovery happens.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from evalspec import __main__


def _write_eval(tmp_path: Path, assertions: list[str], *, slug: str = "a") -> Path:
    """Write an eval under `skills/demo/evals/<slug>/`; a non-kebab slug is malformed."""
    group_dir = tmp_path / "skills" / "demo" / "evals" / slug
    group_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"- [ ] {assertion}\n" for assertion in assertions)
    header = dedent("""\
        ---
        {}
        ---

        ## Prompt

        p

        ## Assertions

    """)
    (group_dir / "eval.md").write_text(header + body)
    return group_dir


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


def test_sandbox_build_calls_preflight_then_cli_build(monkeypatch: object) -> None:
    """Verify `sandbox:build <dir>` runs preflight, then builds from the resolved root, exit 0."""
    ran_preflight: list[bool] = []
    built_roots: list[Path] = []
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: ran_preflight.append(True))
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root: built_roots.append(repo_root)
    )

    exit_code = __main__.main(["sandbox:build", "some/dir"])

    assert exit_code == 0
    assert ran_preflight == [True]
    assert built_roots == [Path("some/dir").resolve()]


def test_sandbox_build_reuses_present_snapshot(monkeypatch: object) -> None:
    """Verify a no-op cli_build (snapshot already present) yields exit 0."""
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)
    monkeypatch.setattr(__main__.sandbox, "cli_build", lambda repo_root: None)

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 0


def test_sandbox_build_preflight_failure_exits_two(monkeypatch: object) -> None:
    """Verify a preflight RuntimeError exits 2 before cli_build is ever called."""
    called_cli_build: list[bool] = []

    def failing_preflight() -> None:
        """Reject the host the way an unsupported platform does."""
        raise RuntimeError("microsandbox host unsupported")

    monkeypatch.setattr(__main__.sandbox, "preflight", failing_preflight)
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root: called_cli_build.append(True)
    )

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 2
    assert called_cli_build == []


def test_sandbox_build_build_error_exits_one(monkeypatch: object, capsys: object) -> None:
    """Verify a build-time MicrosandboxError (not a RuntimeError) exits 1 with a clean error."""
    from microsandbox.errors import MicrosandboxError

    def failing_build(repo_root: Path) -> None:
        """Fail provisioning the way a real snapshot build does."""
        raise MicrosandboxError("snapshot build failed")

    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)
    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_build)

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 1
    assert "error: snapshot build failed" in capsys.readouterr().err


def test_analyze_command_maps_schema_error_to_usage(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Verify a malformed eval under `analyze` surfaces as the usage exit code (2)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    exit_code = __main__.main(["analyze", str(tmp_path)])

    assert exit_code == 2


def test_lint_command_maps_schema_error_to_usage(tmp_path: Path) -> None:
    """Verify a malformed eval under `lint` surfaces as the usage exit code (2)."""
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    exit_code = __main__.main(["lint", str(tmp_path)])

    assert exit_code == 2


def test_all_four_subcommands_registered(monkeypatch: object) -> None:
    """Verify all four subcommands are registered, each accepting a root positional."""
    monkeypatch.setattr(__main__.lint, "run", lambda root: 0)
    monkeypatch.setattr(__main__.analyze, "run", lambda root: 0)
    monkeypatch.setattr(__main__.run, "run", lambda args: 0)
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)
    monkeypatch.setattr(__main__.sandbox, "cli_build", lambda repo_root: None)

    for command in ("lint", "analyze", "sandbox:build", "run"):
        assert __main__.main([command, "some/dir"]) == 0
