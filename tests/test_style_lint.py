"""Contract tests for the advisory custom style lint CLI."""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _framework() -> ModuleType:
    """Import the reusable style-lint framework."""
    return importlib.import_module("lib.style_lint")


def _cli() -> ModuleType:
    """Import the repository-specific style-lint CLI module."""
    script_path = Path(__file__).resolve().parents[1] / "bin/linters/style_lint.py"
    spec = importlib.util.spec_from_file_location(
        "evalspec_style_lint_cli",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not import style lint CLI")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_makefile_wires_custom_lint_target_and_keeps_lint_ruff_only() -> None:
    """Keep the default lint target Ruff-only and expose the custom make target."""
    makefile_text = (Path(__file__).resolve().parents[1] / "Makefile").read_text()

    assert "lint\\:custom" in makefile_text
    assert "uv run ruff check ." in makefile_text
    assert "uv run python bin/linters/style_lint.py" in makefile_text

    lint_target_text = makefile_text.split("lint:  ## Lint with ruff", maxsplit=1)[1]
    lint_target_text = lint_target_text.split("\n\n", maxsplit=1)[0]

    assert "style_lint.py" not in lint_target_text


def test_plan_file_lives_in_docs_plans() -> None:
    """Keep implementation plans in the repository-level plans directory."""
    repo_root = Path(__file__).resolve().parents[1]

    assert (repo_root / "docs/plans/2026-07-05-style-lint-rules.md").exists()
    assert not (
        repo_root / "docs/superpowers/plans/2026-07-05-style-lint-rules.md"
    ).exists()


def test_evalspec_package_does_not_own_style_lint_framework() -> None:
    """Keep reusable linter framework code out of the evalspec package."""
    old_module = Path(__file__).resolve().parents[1] / "src/evalspec/style_lint.py"

    assert not old_module.exists()

    framework = _framework()

    assert framework.Rule.__module__ == "lib.style_lint.models"


def test_cli_owns_repo_specific_rules_prompt_and_default_paths() -> None:
    """Keep evalspec-specific lint policy in the repository script."""
    cli = _cli()

    assert cli.DEFAULT_PATHS == (
        Path("src"),
        Path("tests"),
        Path("evals"),
        Path("bin"),
        Path("lib"),
    )
    assert {rule.id for rule in cli.RULES} == {
        "no-suppression-comments",
        "section-header-comments",
        "provenance-comments",
        "descriptive-names",
        "dedented-multiline-strings",
    }
    assert "docs/style/development.md" in cli.POLICY_INSTRUCTIONS
    assert "Review the numbered source chunks" not in cli.POLICY_INSTRUCTIONS


def test_framework_init_only_exports_submodule_api() -> None:
    """Keep framework implementation out of the package __init__ module."""
    framework = _framework()
    init_text = (
        Path(__file__).resolve().parents[1] / "lib/style_lint/__init__.py"
    ).read_text()

    assert "def " not in init_text
    assert "class " not in init_text
    assert framework.StyleLintConfig.__module__ == "lib.style_lint.runner"
    assert (
        importlib.import_module("lib.style_lint.prompt").DEFAULT_SYSTEM_PROMPT
        == framework.DEFAULT_SYSTEM_PROMPT
    )


def test_collect_python_files_defaults_to_configured_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect Python files from caller-provided default roots."""
    framework = _framework()
    src_file = tmp_path / "src/evalspec/example.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("value = 1\n")
    tests_file = tmp_path / "tests/test_example.py"
    tests_file.parent.mkdir(parents=True, exist_ok=True)
    tests_file.write_text("value = 2\n")
    ignored_file = tmp_path / "tools/ignored.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 3\n")

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        None,
        default_paths=(Path("src"), Path("tests")),
    )

    assert targets == [src_file.resolve(), tests_file.resolve()]


def test_chunk_source_formats_numbered_source_lines(tmp_path: Path) -> None:
    """Format source chunks with stable source line numbers."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    chunks = framework.chunk_source_files([source], max_lines=2)

    assert [chunk.index for chunk in chunks] == [0, 1]
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert "1 | one = 1" in chunks[0].numbered_source
    assert "2 | two = 2" in chunks[0].numbered_source
    assert chunks[1].line_start == 3
    assert "3 | three = 3" in chunks[1].numbered_source


def test_build_detector_prompt_includes_rules_chunks_and_schema(tmp_path: Path) -> None:
    """Build a strict detector prompt from caller-owned policy."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    rules = [
        framework.Rule(
            id="descriptive-names",
            description="Do not use single-letter bindings except `_`.",
        )
    ]

    prompt = framework.build_detector_prompt(
        chunks=chunks,
        rules=rules,
        instructions="Use the repository style guide.",
    )

    assert "Use the repository style guide." in prompt
    assert "Review the numbered source chunks" in prompt
    assert "descriptive-names" in prompt
    assert "chunk_index" in prompt
    assert "1 | x = 1" in prompt


def test_run_advisory_lint_encapsulates_framework_call_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the reusable lint pipeline from one config object."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )
    calls: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        return response

    monkeypatch.setattr(framework, "call_gemini", _call_gemini)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
            policy_instructions="Use the repository style guide.",
            api_key="test-key",
            model="gemini-test",
        )
    )

    assert calls == ["gemini-test"]
    assert len(result.findings) == 1
    assert result.diagnostics == [
        f"{source.resolve()}:1:1: descriptive-names Use a descriptive binding name."
    ]


def test_parse_findings_rejects_unknown_rule_ids(tmp_path: Path) -> None:
    """Reject model findings that reference unknown rule identifiers."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "unknown-rule",
                    "message": "Bad rule.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="unknown-rule"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[framework.Rule(id="known-rule", description="Known rule.")],
        )


def test_parse_findings_rejects_boolean_indexes_and_lines(tmp_path: Path) -> None:
    """Reject booleans where JSON schema requires integers."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": False,
                    "line": True,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Bad reference.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="Malformed finding reference"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
        )


def test_parse_findings_rejects_lines_outside_referenced_chunk(tmp_path: Path) -> None:
    """Reject model findings that point outside the referenced source chunk."""
    framework = _framework()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n")
    chunks = framework.chunk_source_files([source], max_lines=80)
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 2,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Bad reference.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="outside chunk"):
        framework.parse_findings(
            response,
            chunks=chunks,
            rules=[
                framework.Rule(
                    id="descriptive-names",
                    description="Do not use single-letter bindings.",
                )
            ],
        )


def test_call_gemini_rejects_malformed_api_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject partial Gemini payloads that omit the expected content shape."""
    framework = _framework()

    class _Response:
        """Minimal context manager response for urllib stubs."""

        def __init__(self, payload: object) -> None:
            self._payload = payload

        def read(self) -> bytes:
            """Return encoded payload bytes."""
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self) -> _Response:
            """Enter the response context."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Exit the response context."""
            return None

    malformed_payloads = [
        {"candidates": []},
        {"candidates": [{}]},
        {"candidates": [{"content": {}}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": ["text"]}}]},
        {"candidates": [{"content": {"parts": [{"text": 1}]}}]},
    ]

    for payload in malformed_payloads:
        monkeypatch.setattr(
            framework.urllib.request,
            "urlopen",
            lambda request, *, timeout, payload=payload: _Response(payload),
        )

        with pytest.raises(ValueError, match="Gemini response"):
            framework.call_gemini(
                prompt="{}",
                api_key="test-key",
                model="gemini-test",
                timeout=1.0,
            )


def test_cli_run_skips_cleanly_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip advisory lint cleanly when the Gemini API key is absent."""
    cli = _cli()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = cli.run()

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GEMINI_API_KEY" in captured.out
    assert "skip" in captured.out.lower()


def test_cli_script_runs_from_makefile_entry_path_without_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support direct `python bin/linters/style_lint.py` execution."""
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = subprocess.run(
        [sys.executable, "bin/linters/style_lint.py"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "GEMINI_API_KEY" in result.stdout
    assert result.stderr == ""


def test_cli_run_catches_malformed_model_output_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Soft-fail model parse errors because custom lint is advisory."""
    cli = _cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli.style_lint, "call_gemini", lambda **_kwargs: "not json")

    exit_code = cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "warning:" in captured.out
    assert "model error" in captured.out


def test_cli_run_prints_findings_and_stays_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print findings in path-line-column format without failing the command."""
    cli = _cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli.style_lint, "call_gemini", lambda **_kwargs: response)

    exit_code = cli.run(paths=[source])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"{source.resolve()}:1:1: descriptive-names" in captured.out
    assert "Use a descriptive binding name." in captured.out


def test_cli_verify_model_can_filter_findings_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Allow optional second-pass verification to drop detector findings."""
    cli = _cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    calls: list[str] = []
    detector_response = json.dumps(
        {
            "findings": [
                {
                    "chunk_index": 0,
                    "line": 1,
                    "column": 1,
                    "rule_id": "descriptive-names",
                    "message": "Use a descriptive binding name.",
                }
            ]
        }
    )
    verifier_response = json.dumps({"keep_indexes": []})

    def _call_gemini(**kwargs: object) -> str:
        model = kwargs["model"]
        calls.append(str(model))
        if model == "gemini-verifier":
            return verifier_response
        return detector_response

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli.style_lint, "call_gemini", _call_gemini)

    exit_code = cli.run(paths=[source], verify_model="gemini-verifier")

    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == [cli.DEFAULT_MODEL, "gemini-verifier"]
    assert captured.out == ""


def test_cli_verify_model_is_not_called_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep stronger-model verification opt-in."""
    cli = _cli()
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n")
    calls: list[str] = []

    def _call_gemini(**kwargs: object) -> str:
        calls.append(str(kwargs["model"]))
        return json.dumps({"findings": []})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli.style_lint, "call_gemini", _call_gemini)

    exit_code = cli.run(paths=[source])

    assert exit_code == 0
    assert calls == [cli.DEFAULT_MODEL]
