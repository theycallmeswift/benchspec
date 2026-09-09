"""Command dispatch for the `benchspec` CLI entry point.

Each test drives `main` with real argv and asserts which subcommand handler ran and the
root it resolved, with the handler stubbed so no eval discovery happens.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec import __main__
from benchspec.grading.binder import BinderAuthError
from benchspec.sandbox.errors import SandboxError
from benchspec.specs.schema import SchemaError


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


def test_analyze_command_dispatches_to_analyze_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `benchspec analyze <dir>` routes to analyze.run with the resolved root."""
    seen: list[Path] = []

    def fake_run(root: Path) -> int:
        """Record the resolved root and report a success exit code."""
        seen.append(root)
        return 0

    monkeypatch.setattr(__main__.analyze, "run", fake_run)

    exit_code = __main__.main(["analyze", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]


def test_lint_command_dispatches_to_lint_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `benchspec lint <dir>` still routes to lint.run with the resolved root."""
    seen: list[Path] = []

    def fake_run(root: Path) -> int:
        """Record the resolved root and report a success exit code."""
        seen.append(root)
        return 0

    monkeypatch.setattr(__main__.lint, "run", fake_run)

    exit_code = __main__.main(["lint", "some/dir"])

    assert exit_code == 0
    assert seen == [Path("some/dir").resolve()]


def test_run_command_splits_passthrough_at_double_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify args after `--` are split into passthrough while flags before it still parse."""
    seen: list[argparse.Namespace] = []

    def fake_run(args: argparse.Namespace) -> int:
        """Record the parsed namespace and report a success exit code."""
        seen.append(args)
        return 0

    monkeypatch.setattr(__main__.run, "run", fake_run)

    __main__.main(["run", "--set", "x", "--", "-k", "foo"])

    assert seen[0].passthrough == ["-k", "foo"]
    assert seen[0].set == "x"


def test_run_command_dispatches_to_run_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `benchspec run` routes to run.run and returns its exit code."""
    monkeypatch.setattr(__main__.run, "run", lambda args: 7)

    exit_code = __main__.main(["run"])

    assert exit_code == 7


def test_sandbox_build_resolves_root_into_cli_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `sandbox:build <dir>` builds from the resolved root, exit 0."""
    built_roots: list[Path] = []
    monkeypatch.setattr(
        __main__.sandbox,
        "cli_build",
        lambda repo_root, *, set_name=None, config=None: built_roots.append(repo_root),
    )

    exit_code = __main__.main(["sandbox:build", "some/dir"])

    assert exit_code == 0
    assert built_roots == [Path("some/dir").resolve()]


def test_sandbox_build_threads_set_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--set` / `--config` reach cli_build so the resolved set drives the build."""
    seen: dict[str, Path | str | None] = {}

    def fake_cli_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
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


def test_sandbox_build_does_not_preflight_before_backend_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build handler lets cli_build resolve and preflight the selected backend once."""
    monkeypatch.setattr(
        __main__.sandbox,
        "preflight",
        lambda: pytest.fail("preflight ran before the selected backend was resolved"),
    )
    monkeypatch.setattr(
        __main__.sandbox,
        "cli_build",
        lambda repo_root, *, set_name=None, config=None: None,
    )

    assert __main__.main(["sandbox:build", "--set", "micro"]) == 0


def test_sandbox_build_bare_passes_no_set_or_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `sandbox:build` threads set_name=None, config=None (Phase-5 path preserved)."""
    seen: dict[str, Path | str | None] = {}

    def fake_cli_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Record the bare invocation's threaded values."""
        seen["set_name"] = set_name
        seen["config"] = config

    monkeypatch.setattr(__main__.sandbox, "cli_build", fake_cli_build)

    assert __main__.main(["sandbox:build", "some/dir"]) == 0
    assert seen == {"set_name": None, "config": None}


def test_sandbox_build_unknown_sandbox_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A set naming a sandbox no backend implements fails fast at exit 2 before any build."""

    def failing_cli_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Raise the SchemaError an unknown sandbox name produces at resolution."""
        raise SchemaError(
            "[tool.benchspec.sets.dock]: unsupported sandbox `qemu` "
            "(supported: ['docker', 'microsandbox'])"
        )

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build", "some/dir", "--set", "dock"])

    assert exit_code == 2
    assert "qemu" in capsys.readouterr().err


def test_sandbox_build_reuses_present_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a no-op cli_build (snapshot already present) yields exit 0."""
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root, *, set_name=None, config=None: None
    )

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 0


def test_sandbox_build_preflight_failure_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a host-preflight RuntimeError from cli_build maps to exit 2, not a build failure."""

    def failing_cli_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Reject the host the way the resolved backend's preflight does."""
        raise RuntimeError("microsandbox host unsupported")

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 2
    assert "microsandbox host unsupported" in capsys.readouterr().err


def test_sandbox_build_build_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a build-time SandboxError (not a plain RuntimeError) exits 1 with a clean error."""

    def failing_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Fail provisioning the way a real snapshot build does."""
        raise SandboxError("snapshot build failed")

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_build)

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 1
    assert "error: snapshot build failed" in capsys.readouterr().err


def test_sandbox_build_finding_wins_over_the_usage_superclass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a SandboxError exits 1 even though it is also one of the mapped usage types.

    `sandbox:build` maps preflight `RuntimeError` to USAGE (2) and `SandboxError` to
    FINDING (1), and `SandboxError` subclasses `RuntimeError` — so the boundary must test
    the finding types before the usage types. Were that order reversed, every genuine
    build failure would silently report the preflight exit code instead.
    """
    assert issubclass(SandboxError, RuntimeError)

    def failing_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Fail the build with the backend-neutral error every backend raises."""
        raise SandboxError("docker build failed")

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_build)

    exit_code = __main__.main(["sandbox:build"])

    assert exit_code == 1
    assert capsys.readouterr().err == "error: docker build failed\n"


def test_sandbox_clean_resolves_root_into_cli_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `sandbox:clean <dir>` prunes from the resolved root, exit 0."""
    cleaned_roots: list[Path] = []
    monkeypatch.setattr(
        __main__.sandbox, "cli_clean", lambda repo_root: cleaned_roots.append(repo_root)
    )

    exit_code = __main__.main(["sandbox:clean", "some/dir"])

    assert exit_code == 0
    assert cleaned_roots == [Path("some/dir").resolve()]


def test_sandbox_clean_bare_uses_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `sandbox:clean` prunes from the resolved cwd, exit 0."""
    cleaned_roots: list[Path] = []
    monkeypatch.setattr(
        __main__.sandbox, "cli_clean", lambda repo_root: cleaned_roots.append(repo_root)
    )

    exit_code = __main__.main(["sandbox:clean"])

    assert exit_code == 0
    assert cleaned_roots == [Path(".").resolve()]


def test_sandbox_clean_help_names_the_running_sandbox_hazard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify `sandbox:clean --help` exits 0 and warns that it stops running sandboxes."""
    monkeypatch.setattr(
        __main__.sandbox,
        "cli_clean",
        lambda repo_root: pytest.fail("cli_clean ran during --help"),
    )

    with pytest.raises(SystemExit) as raised:
        __main__.main(["sandbox:clean", "--help"])

    assert raised.value.code == 0
    assert "stops running" in capsys.readouterr().out


def test_sandbox_clean_rejects_unknown_flag_with_usage_exit(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `sandbox:clean --bogus` is an argparse usage error, exit 2."""
    monkeypatch.setattr(
        __main__.sandbox,
        "cli_clean",
        lambda repo_root: pytest.fail("cli_clean ran on a malformed invocation"),
    )

    with pytest.raises(SystemExit) as raised:
        __main__.main(["sandbox:clean", "--bogus"])

    assert raised.value.code == 2


def test_analyze_command_maps_schema_error_to_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a malformed eval under `analyze` surfaces as the usage exit code (2)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    exit_code = __main__.main(["analyze", str(tmp_path)])

    assert exit_code == 2


def test_analyze_command_maps_preflight_error_to_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a binder-preflight RuntimeError from analyze.run maps to a clean exit 2."""

    def failing_run(root: Path) -> int:
        """Reject the environment the way `binder.preflight_verify_gemini_key` does."""
        raise RuntimeError("GEMINI_API_KEY is required")

    monkeypatch.setattr(__main__.analyze, "run", failing_run)

    exit_code = __main__.main(["analyze", "some/dir"])

    assert exit_code == 2
    assert capsys.readouterr().err == "error: GEMINI_API_KEY is required\n"


def test_analyze_command_maps_binder_auth_error_to_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a rejected Gemini credential from analyze.run maps to a clean exit 2."""

    def failing_run(root: Path) -> int:
        """Reject the credential the way the binder does on a 401/403."""
        raise BinderAuthError("Gemini API rejected GEMINI_API_KEY")

    monkeypatch.setattr(__main__.analyze, "run", failing_run)

    exit_code = __main__.main(["analyze", "some/dir"])

    assert exit_code == 2
    assert capsys.readouterr().err == "error: Gemini API rejected GEMINI_API_KEY\n"


def test_lint_command_maps_schema_error_to_usage(tmp_path: Path) -> None:
    """Verify a malformed eval under `lint` surfaces as the usage exit code (2)."""
    _write_eval(tmp_path, ["Skill `ingest` invoked"], slug="NotKebab")

    exit_code = __main__.main(["lint", str(tmp_path)])

    assert exit_code == 2


@pytest.mark.parametrize(
    "command", ["lint", "analyze", "sandbox:build", "sandbox:clean", "run"]
)
def test_subcommand_registered(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """Verify each subcommand is registered and accepts a root positional."""
    monkeypatch.setattr(__main__.lint, "run", lambda root: 0)
    monkeypatch.setattr(__main__.analyze, "run", lambda root: 0)
    monkeypatch.setattr(__main__.run, "run", lambda args: 0)
    monkeypatch.setattr(
        __main__.sandbox, "cli_build", lambda repo_root, *, set_name=None, config=None: None
    )
    monkeypatch.setattr(__main__.sandbox, "cli_clean", lambda repo_root: None)

    assert __main__.main([command, "some/dir"]) == 0


def test_sandbox_build_missing_package_exits_two_before_importing_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a host without microsandbox hits preflight's exit 2, never a microsandbox import.

    `_run_sandbox_build`'s exit-code mapping is backend-neutral now: it catches `SandboxError`
    and `RuntimeError`, never importing anything microsandbox-specific. Poisoning
    `microsandbox.errors` proves the build path never imports it at any point: a
    missing-package preflight `RuntimeError` is still caught as USAGE (2) even with the
    module poisoned to raise `ImportError` on import.
    """
    monkeypatch.setitem(sys.modules, "microsandbox.errors", None)

    def missing_package_cli_build(
        repo_root: Path, *, set_name: str | None = None, config: str | None = None
    ) -> None:
        """Reject the host the way an absent microsandbox package does at preflight."""
        raise RuntimeError("microsandbox runtime not installed")

    monkeypatch.setattr(__main__.sandbox, "cli_build", missing_package_cli_build)

    assert __main__.main(["sandbox:build", "some/dir"]) == 2


def test_main_loads_repo_dotenv_for_every_subcommand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the CLI loads a repo-root `.env` before dispatching any subcommand."""
    _write_eval(tmp_path, ["./out.md exists"])
    (tmp_path / ".env").write_text("BENCHSPEC_DOTENV_SENTINEL=loaded\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BENCHSPEC_DOTENV_SENTINEL", raising=False)

    exit_code = __main__.main(["lint", str(tmp_path)])

    assert exit_code == 0
    assert os.environ.get("BENCHSPEC_DOTENV_SENTINEL") == "loaded"
