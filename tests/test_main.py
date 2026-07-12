"""Command dispatch for the `evalspec` CLI entry point.

Each test drives `main` with real argv and asserts which subcommand handler ran and the
root it resolved, with the handler stubbed so no eval discovery happens.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

from evalspec import __main__
from evalspec.schema import SchemaError


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
        __main__.sandbox,
        "cli_build",
        lambda repo_root, *, set_name=None, config=None: built_roots.append(repo_root),
    )

    exit_code = __main__.main(["sandbox:build", "some/dir"])

    assert exit_code == 0
    assert ran_preflight == [True]
    assert built_roots == [Path("some/dir").resolve()]


def test_sandbox_build_threads_set_and_config(monkeypatch: object) -> None:
    """`--set` / `--config` reach cli_build so the resolved set drives the build."""
    seen: dict = {}
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)

    def fake_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Record what the CLI threaded into the build."""
        seen["root"] = repo_root
        seen["set_name"] = set_name
        seen["config"] = config

    monkeypatch.setattr(__main__.sandbox, "cli_build", fake_cli_build)

    exit_code = __main__.main(
        ["sandbox:build", "some/dir", "--set", "micro", "--config", "cfg.toml"]
    )

    assert exit_code == 0
    assert seen["set_name"] == "micro"
    assert seen["config"] == "cfg.toml"


def test_sandbox_build_bare_passes_no_set_or_config(monkeypatch: object) -> None:
    """Bare `sandbox:build` threads set_name=None, config=None (Phase-5 path preserved)."""
    seen: dict = {}
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)

    def fake_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Record the bare invocation's threaded values."""
        seen["set_name"] = set_name
        seen["config"] = config

    monkeypatch.setattr(__main__.sandbox, "cli_build", fake_cli_build)

    assert __main__.main(["sandbox:build", "some/dir"]) == 0
    assert seen == {"set_name": None, "config": None}


def test_sandbox_build_docker_set_exits_two(monkeypatch: object, capsys: object) -> None:
    """A set whose sandbox is `docker` fails fast at exit 2 before any build."""
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)

    def failing_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Raise the SchemaError a docker set produces at resolution."""
        raise SchemaError(
            "[tool.evalspec.sets.dock]: unsupported sandbox `docker` "
            "(supported: ['microsandbox']). Docker is not implemented."
        )

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build", "some/dir", "--set", "dock"])

    assert exit_code == 2
    assert "docker" in capsys.readouterr().err


def test_sandbox_build_reuses_present_snapshot(monkeypatch: object) -> None:
    """Verify a no-op cli_build (snapshot already present) yields exit 0."""
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root, *, set_name=None, config=None: None
    )

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
        __main__.sandbox,
        "cli_build",
        lambda repo_root, *, set_name=None, config=None: called_cli_build.append(True),
    )

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 2
    assert called_cli_build == []


def test_sandbox_build_build_error_exits_one(monkeypatch: object, capsys: object) -> None:
    """Verify a build-time MicrosandboxError (not a RuntimeError) exits 1 with a clean error."""
    from microsandbox.errors import MicrosandboxError

    def failing_build(repo_root: Path, *, set_name: object = None, config: object = None) -> None:
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


@pytest.mark.parametrize("command", ["lint", "analyze", "sandbox:build", "run"])
def test_subcommand_registered(monkeypatch: object, command: str) -> None:
    """Verify each subcommand is registered and accepts a root positional."""
    monkeypatch.setattr(__main__.lint, "run", lambda root: 0)
    monkeypatch.setattr(__main__.analyze, "run", lambda root: 0)
    monkeypatch.setattr(__main__.run, "run", lambda args: 0)
    monkeypatch.setattr(__main__.sandbox, "preflight", lambda: None)
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root, *, set_name=None, config=None: None
    )

    assert __main__.main([command, "some/dir"]) == 0


def test_sandbox_build_missing_package_exits_two_before_importing_errors(
    monkeypatch: object,
) -> None:
    """Verify a host without microsandbox hits preflight's exit 2, never the errors import.

    Poisoning `microsandbox.errors` makes `from microsandbox.errors import MicrosandboxError`
    raise `ImportError`. Because that import now follows `preflight`, a missing-package
    preflight `RuntimeError` returns USAGE (2) first — proving the import no longer precedes
    preflight. Were the import still first, the poisoned module would raise an uncaught
    `ImportError` instead of yielding 2.
    """
    monkeypatch.setitem(sys.modules, "microsandbox.errors", None)

    def missing_package_preflight() -> None:
        """Reject the host the way an absent microsandbox package does."""
        raise RuntimeError("microsandbox runtime not installed")

    monkeypatch.setattr(__main__.sandbox, "preflight", missing_package_preflight)

    assert __main__.main(["sandbox:build", "some/dir"]) == 2
