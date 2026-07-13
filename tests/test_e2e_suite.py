"""Config-resolution and discovery/parse tests for evalspec's own end-to-end suite.

Exercises the real `[tool.evalspec]` config in this repo's `pyproject.toml` (the `e2e`
set and its Codex judge) and the real `evals/e2e/hello/` suite against the live parsers
— the same "authored example must parse for real" pattern as
`tests/test_readme_examples.py`, extended to config resolution and eval discovery.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from evalspec.arms import parse_sets, resolve_set
from evalspec.discovery import discover_eval_cases, pyproject_table
from evalspec.judges.config import resolve_judge_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_command_recorder(command_dir: Path, command: str) -> None:
    """Write a PATH command shim that records its invocation without changing files."""
    recorder = command_dir / command
    recorder.write_text(
        "#!/bin/sh\n"
        'printf "%s" "${0##*/}" >> "$EVALSPEC_COMMAND_LOG"\n'
        'printf " %s" "$@" >> "$EVALSPEC_COMMAND_LOG"\n'
        'printf "\\n" >> "$EVALSPEC_COMMAND_LOG"\n'
    )
    recorder.chmod(0o755)


def test_e2e_set_resolves_to_three_arms_with_declared_config() -> None:
    """Verify the e2e set resolves to three arms with the declared harness/model/effort."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    assert default_set == "e2e"
    assert resolved.baseline == "baseline"
    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert set(arms_by_name) == {"baseline", "trial", "trial-overrides"}
    assert arms_by_name["baseline"].harness == "claude-code"
    assert arms_by_name["baseline"].model == "sonnet"
    assert arms_by_name["baseline"].effort == "medium"
    assert arms_by_name["trial"].harness == "claude-code"
    assert arms_by_name["trial"].model == "sonnet"
    assert arms_by_name["trial"].effort == "medium"
    assert arms_by_name["trial-overrides"].harness == "claude-code"
    assert arms_by_name["trial-overrides"].model == "opus"
    assert arms_by_name["trial-overrides"].effort == "high"


def test_e2e_trial_overrides_env_inherits_style_and_overrides_locale() -> None:
    """Verify trial-overrides inherits GREETING_STYLE and overrides GREETING_LOCALE."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert arms_by_name["baseline"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial-overrides"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-GB",
    }


def test_e2e_judge_is_codex_and_distinct_from_the_task_harness() -> None:
    """Verify the e2e judge is a cross-family Codex judge, distinct from the task harness."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)
    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    judge = resolve_judge_config(pyproject_table=table.get("judge"))

    assert judge.harness == "codex"
    assert judge.model == "gpt-5.5"
    assert judge.harness != resolved.arms[0].harness


def test_hello_evals_are_discovered_with_expected_identities() -> None:
    """Verify both hello evals are discovered with a non-empty prompt and assertions.

    Calls `discover_eval_cases(REPO_ROOT)` with no `eval_paths` override — the same call
    shape production uses — so this test exercises the real `eval_paths = ["evals"]` in
    `pyproject.toml`, not a hardcoded stand-in.
    That also proves the scoping does its job: the `activation-demo` fixture eval,
    which the *default* `eval_paths` (`skills`, `tests`, `evals`, `benchmarks`) would
    pick up from the test-fixture tree, must NOT appear in the discovered set.
    """
    cases = discover_eval_cases(REPO_ROOT)

    identities = {(case.group, case.eval_id) for case in cases}

    assert identities == {
        ("hello", "greets-by-name"),
        ("hello", "writes-greeting-file"),
    }
    for case in cases:
        assert case.prompt
        assert case.assertions


def test_hello_evals_exercise_history_and_seeded_workspace() -> None:
    """Verify a live eval depends on authored history and a seeded workspace file."""
    cases = discover_eval_cases(REPO_ROOT)
    cases_by_id = {case.eval_id: case for case in cases if case.group == "hello"}
    context_case = cases_by_id["writes-greeting-file"]
    request_file = REPO_ROOT / "evals/e2e/hello/evals/hello/workspace/request.md"

    assert context_case.history
    assert any("Bob" in turn["content"] for turn in context_case.history)
    assert "Bob" not in context_case.prompt
    assert context_case.workspace_dir == request_file.parent
    assert request_file.is_file()
    assert request_file.read_text(encoding="utf-8").strip()
    assert "Bob" not in request_file.read_text(encoding="utf-8")
    assert "./request.md" in context_case.prompt


def test_setup_sh_has_valid_bash_syntax() -> None:
    """Verify setup.sh parses as valid bash without executing any of it."""
    setup_sh = REPO_ROOT / "evals/e2e/hello/evals/hello/setup.sh"

    result = subprocess.run(
        ["bash", "-n", str(setup_sh)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_setup_sh_baseline_arm_runs_no_install_commands(tmp_path: Path) -> None:
    """Verify the baseline branch exits without running an install command."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    command_log = tmp_path / "commands.log"
    bash = shutil.which("bash")
    assert bash is not None

    result = subprocess.run(
        [bash, "./setup.sh"],
        cwd=eval_dir,
        env={
            **os.environ,
            "EVALSPEC_ARM": "baseline",
            "EVALSPEC_COMMAND_LOG": str(command_log),
            "PATH": str(command_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not command_log.exists()


def test_setup_sh_trial_installs_the_real_skill_without_host_writes(tmp_path: Path) -> None:
    """Verify the trial branch installs the real skill at the fixed guest path."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    _write_command_recorder(command_dir, "mkdir")
    _write_command_recorder(command_dir, "cp")
    command_log = tmp_path / "commands.log"
    bash = shutil.which("bash")
    assert bash is not None

    result = subprocess.run(
        [bash, "./setup.sh"],
        cwd=eval_dir,
        env={
            **os.environ,
            "EVALSPEC_ARM": "trial",
            "EVALSPEC_COMMAND_LOG": str(command_log),
            "PATH": str(command_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    commands = command_log.read_text(encoding="utf-8").splitlines()
    copied_source = commands[1].split()[1]

    assert result.returncode == 0, result.stderr
    assert commands == [
        "mkdir -p /home/evalspec/skills/hello",
        "cp ../../SKILL.md /home/evalspec/skills/hello/SKILL.md",
    ]
    assert (eval_dir / copied_source).resolve() == REPO_ROOT / "evals/e2e/hello/SKILL.md"


def test_make_e2e_runs_artifact_contract_validation() -> None:
    """Verify the E2E target validates the artifacts produced by the live run."""
    result = subprocess.run(
        ["make", "-n", "e2e"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "uv run evalspec run --set e2e",
        "uv run python scripts/verify_e2e_artifacts.py tmp/evals",
    ]


def test_removed_binder_make_alias_has_no_active_references() -> None:
    """Verify maintained files reference the binder corpus through `make evals`."""
    removed_target = "evals" + ":binder"
    historical_doc_parts = {"plans", "specs", "research"}
    text_suffixes = {".md", ".py", ".sh", ".toml", ".yaml", ".yml"}
    active_files = [
        REPO_ROOT / "Makefile",
        REPO_ROOT / "README.md",
        REPO_ROOT / "pyproject.toml",
    ]
    for root_name in ("src", "tests", "evals", "docs"):
        for path in (REPO_ROOT / root_name).rglob("*"):
            if not path.is_file() or path.suffix not in text_suffixes:
                continue
            if root_name == "docs":
                relative_parts = path.relative_to(REPO_ROOT / "docs").parts[:-1]
                if historical_doc_parts.intersection(relative_parts):
                    continue
            active_files.append(path)

    references = []
    for path in sorted(active_files):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if removed_target in line.replace("\\:", ":"):
                relative_path = path.relative_to(REPO_ROOT)
                references.append(f"{relative_path}:{line_number}: {line.strip()}")

    failure_message = "active references to the removed binder make alias remain:\n"
    failure_message += "\n".join(references)

    assert references == [], failure_message
