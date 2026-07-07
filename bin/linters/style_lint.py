"""Run repository-specific advisory style lint checks."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import subprocess
import sys
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

    Returns:
        Always returns zero because this linter is advisory.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
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
        )
    )
    if result.warning is not None:
        print(f"warning: {result.warning}")
        return 0

    if not result.diagnostics:
        print("All checks passed!")
        return 0

    for line in result.diagnostics:
        print(line)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the style lint CLI."""
    parser = argparse.ArgumentParser(prog="style_lint")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--changed-from")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verify-findings", action="store_true")
    parser.add_argument("--verify-model")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_CHUNK_LINES)
    args = parser.parse_args(argv)
    changed_lines = None
    paths = list(args.paths) or None
    if args.changed_from is not None:
        changed_lines = changed_lines_from_ref(args.changed_from)
        paths = sorted(changed_lines)

    return run(
        paths,
        model=args.model,
        changed_lines=changed_lines,
        verify_findings=args.verify_findings,
        verify_model=args.verify_model,
        max_lines=args.max_lines,
    )


def changed_lines_from_ref(ref: str) -> dict[Path, set[int]]:
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
