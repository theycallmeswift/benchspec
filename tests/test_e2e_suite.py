"""Config-resolution and discovery/parse tests for benchspec's own end-to-end suite.

Exercises the real `[tool.benchspec]` config in this repo's `pyproject.toml` (the `e2e`
set and its Codex judge) and the real `evals/e2e/hello/` suite against the live parsers
— the same "authored example must parse for real" pattern as
`tests/test_readme_examples.py`, extended to config resolution and eval discovery.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from benchspec.config.arms import parse_sets, resolve_set
from benchspec.grading.binder_config import resolve_binder_config
from benchspec.grading.judges.config import resolve_judge_config
from benchspec.specs import scope
from benchspec.specs.discovery import discover_eval_cases, pyproject_table
from tests.support.cli import run_benchspec

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_command_recorder(command_dir: Path, command: str) -> None:
    """Write a PATH command shim that records its invocation without changing files."""
    recorder = command_dir / command
    recorder.write_text(dedent("""\
        #!/bin/sh
        printf "%s" "${0##*/}" >> "$BENCHSPEC_COMMAND_LOG"
        printf " %s" "$@" >> "$BENCHSPEC_COMMAND_LOG"
        printf "\\n" >> "$BENCHSPEC_COMMAND_LOG"
    """))
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
    assert all(arm.provider == "default" for arm in resolved.arms)


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
    assert judge.provider == "default"
    assert judge.model == "gpt-5.5"
    assert judge.harness != resolved.arms[0].harness


def test_hello_evals_are_discovered_with_expected_identities() -> None:
    """Verify every hello eval is discovered with a non-empty prompt and assertions.

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
        ("hello-file", "writes-greeting-file"),
        ("hello-outside", "allows-filesystem-traversal"),
    }
    for case in cases:
        assert case.prompt
        assert case.assertions


def test_hello_evals_exercise_history_and_seeded_workspace() -> None:
    """Verify a live eval depends on authored history and a seeded workspace file."""
    cases = discover_eval_cases(REPO_ROOT)
    cases_by_id = {case.eval_id: case for case in cases}
    context_case = cases_by_id["writes-greeting-file"]
    greeting_case = cases_by_id["greets-by-name"]
    request_file = REPO_ROOT / "evals/e2e/hello/evals/hello-file/workspace/request.md"

    assert context_case.history
    assert any("Bob" in turn["content"] for turn in context_case.history)
    assert "Bob" not in context_case.prompt
    assert context_case.workspace_dir == request_file.parent
    assert request_file.is_file()
    assert request_file.read_text(encoding="utf-8").strip()
    assert "Bob" not in request_file.read_text(encoding="utf-8")
    assert "./request.md" in context_case.prompt
    assert greeting_case.workspace_dir is None


@pytest.mark.parametrize("group", ["hello", "hello-file", "hello-outside"])
def test_setup_sh_has_valid_bash_syntax(group: str) -> None:
    """Verify setup.sh parses as valid bash without executing any of it."""
    setup_sh = REPO_ROOT / "evals/e2e/hello/evals" / group / "setup.sh"

    result = subprocess.run(
        ["bash", "-n", str(setup_sh)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("group", ["hello", "hello-file", "hello-outside"])
def test_setup_sh_baseline_arm_runs_no_install_commands(tmp_path: Path, group: str) -> None:
    """Verify the baseline branch exits without running an install command."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals" / group
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
            "BENCHSPEC_ARM": "baseline",
            "BENCHSPEC_COMMAND_LOG": str(command_log),
            "PATH": str(command_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not command_log.exists()


@pytest.mark.parametrize("group", ["hello", "hello-file", "hello-outside"])
def test_setup_sh_trial_installs_the_real_skill_without_host_writes(
    tmp_path: Path, group: str
) -> None:
    """Verify the trial branch installs the real skill at the fixed guest path."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals" / group
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
            "BENCHSPEC_ARM": "trial",
            "BENCHSPEC_COMMAND_LOG": str(command_log),
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
        "mkdir -p /home/benchspec/skills/hello",
        "cp ../../SKILL.md /home/benchspec/skills/hello/SKILL.md",
    ]
    assert (eval_dir / copied_source).resolve() == REPO_ROOT / "evals/e2e/hello/SKILL.md"


def test_make_e2e_runs_the_e2e_set() -> None:
    """Verify the E2E target runs the repository's live eval set."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "e2e"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "uv run benchspec run --set e2e " in output
    assert "verify_e2e_artifacts" not in output


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

    failure_message = "\n".join(
        ["active references to the removed binder make alias remain:", *references]
    )

    assert references == [], failure_message


def _make_e2e_commands() -> list[list[str]]:
    """The `benchspec run` argv lines `make e2e` executes, split like a shell would."""
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "e2e"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [shlex.split(line) for line in result.stdout.splitlines() if "benchspec run" in line]


def _flag_value(argv: list[str], flag: str) -> str:
    """The value following `flag` in `argv`, whichever of `--flag value` or `--flag=value`."""
    for index, token in enumerate(argv):
        if token == flag:
            return argv[index + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    raise AssertionError(f"{flag} not passed in {argv}")


def test_e2e_openrouter_set_puts_every_harness_on_openrouter() -> None:
    """Verify the OpenRouter set spans all three harnesses, every arm on the gateway."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e-openrouter")

    assert default_set == "e2e"  # the native set stays the plain-run default
    assert resolved.baseline == "baseline"
    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert {arm.harness for arm in resolved.arms} == {"claude-code", "codex", "opencode"}
    assert all(arm.provider == "openrouter" for arm in resolved.arms)
    assert all("/" in arm.model for arm in resolved.arms)
    assert arms_by_name["baseline"].harness == "claude-code"
    assert arms_by_name["trial"].model == arms_by_name["baseline"].model
    assert arms_by_name["trial-codex"].model.startswith("openai/")
    assert arms_by_name["trial-opencode"].model.startswith("openrouter/")
    assert all(
        arm.env == {"GREETING_STYLE": "formal", "GREETING_LOCALE": "en-US"}
        for arm in resolved.arms
    )


def test_make_e2e_runs_the_openrouter_set_with_judge_and_binder_on_openrouter() -> None:
    """Verify the second `make e2e` run resolves to OpenRouter everywhere, via the real parsers.

    Reads the flags off `make -n e2e` itself so the Makefile and this test cannot drift:
    the judge and binder overrides it passes are fed to the same resolvers the CLI uses.
    """
    table = pyproject_table(REPO_ROOT)
    commands = _make_e2e_commands()
    openrouter_run = next(
        argv for argv in commands if _flag_value(argv, "--set") == "e2e-openrouter"
    )

    judge = resolve_judge_config(
        pyproject_table=table.get("judge"),
        cli_table={
            "provider": _flag_value(openrouter_run, "--judge-provider"),
            "model": _flag_value(openrouter_run, "--judge-model"),
        },
    )
    binder = resolve_binder_config(
        cli_table={"provider": _flag_value(openrouter_run, "--binder-provider")}
    )
    rawsets, default_set = parse_sets(table)
    arms = resolve_set(rawsets, default_set, set_name="e2e-openrouter").arms

    assert [_flag_value(argv, "--set") for argv in commands] == ["e2e", "e2e-openrouter"]
    assert judge.harness == "codex"
    assert judge.provider == "openrouter"
    assert judge.model.startswith("google/")  # cross-family from the Anthropic and OpenAI arms
    assert binder.provider == "openrouter"
    assert binder.model == "google/gemini-3.5-flash-lite"
    assert all(arm.provider == "openrouter" for arm in arms)


TRIGGER_LINE = "Skill `hello` invoked"
TRIGGER_CLAUSE = {"key": "if", "expr": "{BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}"}
EN_GB_LINE = "./Greetings/Bob.md contains the text 'an absolute pleasure'"
EN_GB_CLAUSE = {"key": "if", "expr": '{GREETING_LOCALE} == "en-GB"'}


def test_hello_trigger_lines_carry_the_baseline_clause() -> None:
    """Verify every in-repo `Skill hello invoked` line is scoped off the baseline arm."""
    cases = discover_eval_cases(REPO_ROOT)

    trigger_clauses = {
        (case.eval_id, index): clause
        for case in cases
        for index, (assertion, clause) in enumerate(zip(case.assertions, case.clauses, strict=True))
        if assertion == TRIGGER_LINE
    }

    assert {eval_id for eval_id, _ in trigger_clauses} == {
        "greets-by-name",
        "allows-filesystem-traversal",
    }
    assert len(trigger_clauses) == 2
    assert trigger_clauses == dict.fromkeys(trigger_clauses, TRIGGER_CLAUSE)


def test_hello_file_locale_line_carries_the_en_gb_clause() -> None:
    """Verify the en-GB-only phrase is scoped to the locale and the other line is unscoped."""
    cases = discover_eval_cases(REPO_ROOT)
    case = next(case for case in cases if case.eval_id == "writes-greeting-file")

    clauses_by_line = dict(zip(case.assertions, case.clauses, strict=True))

    assert clauses_by_line == {
        "./Greetings/Bob.md matches the regex 'Hello, Bob!'": None,
        EN_GB_LINE: EN_GB_CLAUSE,
    }


@pytest.mark.parametrize("set_name", ["e2e", "e2e-openrouter"])
def test_hello_clauses_resolve_for_every_arm(set_name: str) -> None:
    """Verify every in-repo clause resolves on every arm of both live sets to the expected flag.

    The trigger line is graded everywhere but the baseline; the en-GB phrase only where the
    arm's own `GREETING_LOCALE` is `en-GB` (`trial-overrides` in `e2e`, nowhere in
    `e2e-openrouter`); an unclaused line everywhere.
    """
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)
    resolved = resolve_set(rawsets, default_set, set_name=set_name)
    cases = discover_eval_cases(REPO_ROOT)

    flags_by_cell = {
        (case.eval_id, arm.name): scope.applicable(
            case, arm, baseline=resolved.baseline, eval_set=set_name
        )
        for case in cases
        for arm in resolved.arms
    }

    en_gb_arms = {arm.name for arm in resolved.arms if arm.env["GREETING_LOCALE"] == "en-GB"}
    assert resolved.baseline == "baseline"
    assert en_gb_arms == ({"trial-overrides"} if set_name == "e2e" else set())
    expected_by_cell = {}
    for case in cases:
        for arm in resolved.arms:
            expected = []
            for assertion in case.assertions:
                if assertion == TRIGGER_LINE:
                    expected.append(arm.name != "baseline")
                elif assertion == EN_GB_LINE:
                    expected.append(arm.name in en_gb_arms)
                else:
                    expected.append(True)
            expected_by_cell[(case.eval_id, arm.name)] = expected
    assert flags_by_cell == expected_by_cell


def test_lint_is_clean_on_the_in_repo_suite() -> None:
    """Verify `benchspec lint` on this repository reports no warnings and exits 0."""
    result = run_benchspec("lint", str(REPO_ROOT), cwd=REPO_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 warning(s)" in result.stdout
    assert "[warning]" not in result.stdout
