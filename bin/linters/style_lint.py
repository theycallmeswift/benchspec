"""Run repository-specific advisory style lint checks."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

style_lint = importlib.import_module("lib.style_lint")

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PATHS = (Path("src"), Path("tests"), Path("evals"), Path("bin"), Path("lib"))
DEFAULT_CHUNK_LINES = 120
DIFF_HEADER_PATTERN = re.compile(r"^\+\+\+ b/(.+)$")
DIFF_HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

RULES = [
    style_lint.Rule(
        id="no-suppression-comments",
        description="Do not use lint or type-check suppression comments.",
    ),
    style_lint.Rule(
        id="section-header-comments",
        description="Do not use region-style comments instead of named structure.",
    ),
    style_lint.Rule(
        id="provenance-comments",
        description=(
            "Do not reference PRs, issues, commits, callers, or planning docs "
            "in source comments."
        ),
    ),
    style_lint.Rule(
        id="descriptive-names",
        description=(
            "Do not use single-letter bindings except `_` for intentionally "
            "unused values."
        ),
    ),
    style_lint.Rule(
        id="dedented-multiline-strings",
        description="Use textwrap.dedent for indented multiline strings.",
    ),
    style_lint.Rule(
        id="semantic-block-newlines",
        description="Use blank lines to separate semantic blocks for readability.",
    ),
]

POLICY_INSTRUCTIONS = dedent("""\
    Apply evalspec's local Python style guide from docs/style/development.md.
""")


def run(
    paths: list[Path] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    changed_lines: dict[Path, set[int]] | None = None,
    verify_findings: bool = False,
    verify_model: str | None = None,
    max_lines: int = DEFAULT_CHUNK_LINES,
    verbose: bool = False,
) -> int:
    """Run evalspec advisory style lint and print findings.

    Args:
        paths: Optional source paths to lint.
        model: Gemini detector model.
        changed_lines: Optional touched-line filter keyed by source path.
        verify_findings: Whether to run second-pass verification.
        verify_model: Optional Gemini model for second-pass verification. The
            detector model is used when verification is enabled without an
            override.
        max_lines: Maximum source lines per model chunk.
        verbose: Whether to print Ruff-style debug progress to stderr.

    Returns:
        Always returns zero because this linter is advisory.
    """
    started_at = time.perf_counter()
    verbose_log(verbose, f"Using paths: {format_paths(paths)}")
    verbose_log(verbose, f"Using model: {model}")
    if verify_findings:
        verbose_log(verbose, f"Using verifier model: {verify_model or model}")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        verbose_log(verbose, "GEMINI_API_KEY is not set")
        print("skip: GEMINI_API_KEY is not set; advisory style lint is disabled")
        return 0

    result = style_lint.run_advisory_lint(
        style_lint.StyleLintConfig(
            paths=paths,
            default_paths=DEFAULT_PATHS,
            rules=RULES,
            policy_instructions=POLICY_INSTRUCTIONS,
            api_key=api_key,
            model=model,
            changed_lines=changed_lines,
            verify_findings=verify_findings,
            verify_model=verify_model,
            max_lines=max_lines,
            progress_callback=progress_logger(verbose),
        )
    )
    elapsed = time.perf_counter() - started_at
    verbose_log(
        verbose,
        (
            f"Checked {result.files_checked} files across "
            f"{result.chunks_checked} chunks in {elapsed:.3f}s"
        ),
    )

    if result.warning is not None:
        print(f"warning: {result.warning}")
        print_usage(result.usage)
        return 0

    if not result.diagnostics:
        print("All checks passed!")
        print_usage(result.usage)
        return 0

    for line in result.diagnostics:
        print(line)
    print_usage(result.usage)

    return 0


def run_dry_run(
    paths: list[Path] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    verify_findings: bool = False,
    verify_model: str | None = None,
    max_lines: int = DEFAULT_CHUNK_LINES,
    verbose: bool = False,
) -> int:
    """Print advisory style-lint planning data without model calls."""
    verbose_log(verbose, f"Using paths: {format_paths(paths)}")
    verbose_log(verbose, f"Using model: {model}")
    if verify_findings:
        verbose_log(verbose, f"Using verifier model: {verify_model or model}")

    result = style_lint.run_advisory_lint(
        style_lint.StyleLintConfig(
            paths=paths,
            default_paths=DEFAULT_PATHS,
            rules=RULES,
            policy_instructions=POLICY_INSTRUCTIONS,
            api_key="unused-in-dry-run",
            model=model,
            dry_run=True,
            verify_findings=verify_findings,
            verify_model=verify_model,
            max_lines=max_lines,
        )
    )
    if result.warning is not None:
        print(f"warning: {result.warning}")
        return 0

    if result.plan is None:
        raise TypeError("dry-run advisory lint did not return a plan")
    plan = result.plan

    if verbose:
        verbose_log(
            True,
            (
                "Dry run summary: "
                f"{plan.files_checked} files, "
                f"{plan.chunks_checked} chunks, "
                f"{plan.detector_api_calls} detector calls, "
                f"{plan.max_verifier_api_calls} max verifier calls, "
                f"{plan.max_total_api_calls} max total calls"
            ),
        )

    for path in plan.files:
        print(path)
    print(f"files: {plan.files_checked}")
    print(f"chunks: {plan.chunks_checked}")
    print(f"detector_api_calls: {plan.detector_api_calls}")
    print(f"max_verifier_api_calls: {plan.max_verifier_api_calls}")
    print(f"max_total_api_calls: {plan.max_total_api_calls}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the style lint CLI."""
    parser = argparse.ArgumentParser(prog="style_lint")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verify-findings", action="store_true")
    parser.add_argument("--verify-model")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_CHUNK_LINES)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    changed_lines = None
    paths = list(args.paths) or None
    if args.base is not None:
        changed_lines = changed_lines_from_base(args.base)
        paths = sorted(changed_lines)

    if args.dry_run:
        return run_dry_run(
            paths,
            model=args.model,
            verify_findings=args.verify_findings or args.base is not None,
            verify_model=args.verify_model,
            max_lines=args.max_lines,
            verbose=args.verbose,
        )

    return run(
        paths,
        model=args.model,
        changed_lines=changed_lines,
        verify_findings=args.verify_findings or args.base is not None,
        verify_model=args.verify_model,
        max_lines=args.max_lines,
        verbose=args.verbose,
    )


def verbose_log(enabled: bool, message: str) -> None:
    """Print Ruff-style debug logging to stderr when enabled."""
    if not enabled:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d][%H:%M:%S")
    print(f"[{timestamp}][style_lint][DEBUG] {message}", file=sys.stderr)


def progress_logger(enabled: bool) -> Callable[[Path, int, int], None] | None:
    """Return a verbose file progress callback when enabled."""
    if not enabled:
        return None

    def log_file_progress(path: Path, current: int, total: int) -> None:
        percent = 100 if total == 0 else round((current / total) * 100)
        verbose_log(
            True,
            f"Checking file {current}/{total} ({percent}%): {path}",
        )

    return log_file_progress


def format_paths(paths: list[Path] | None) -> str:
    """Format explicit or default CLI paths for debug output."""
    if paths is None:
        return ", ".join(str(path) for path in DEFAULT_PATHS)
    return ", ".join(str(path) for path in paths)


def print_usage(usage: object) -> None:
    """Print model usage metadata when real token counts are available."""
    if not isinstance(usage, style_lint.UsageMetadata):
        return
    if usage.requests == 0:
        return
    print(
        "usage: "
        f"{usage.requests} requests, "
        f"{usage.prompt_tokens} input tokens, "
        f"{usage.output_tokens} output tokens, "
        f"{usage.total_tokens} total tokens"
    )


def changed_lines_from_base(ref: str) -> dict[Path, set[int]]:
    """Return changed Python lines between a git ref and the worktree."""
    diff = subprocess.run(
        ["git", "diff", "--unified=0", ref, "--", "*.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    return changed_lines_from_unified_diff(diff.stdout)


def changed_lines_from_unified_diff(diff_text: str) -> dict[Path, set[int]]:
    """Parse touched Python line numbers from a unified diff."""
    changed_lines: dict[Path, set[int]] = {}
    current_path: Path | None = None
    current_line: int | None = None

    for diff_line in diff_text.splitlines():
        header_match = DIFF_HEADER_PATTERN.match(diff_line)
        if header_match is not None:
            path = Path(header_match.group(1))
            current_path = path if path.suffix == ".py" else None
            current_line = None
            if current_path is not None:
                changed_lines.setdefault(current_path.resolve(), set())
            continue

        hunk_match = DIFF_HUNK_PATTERN.match(diff_line)
        if hunk_match is not None:
            current_line = int(hunk_match.group(1))
            continue

        if current_path is None or current_line is None:
            continue
        if diff_line.startswith("+") and not diff_line.startswith("+++"):
            changed_lines[current_path.resolve()].add(current_line)
            current_line += 1
        elif not diff_line.startswith("-"):
            current_line += 1

    return {path: lines for path, lines in changed_lines.items() if lines}


if __name__ == "__main__":
    raise SystemExit(main())
