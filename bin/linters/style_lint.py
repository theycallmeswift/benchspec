"""Run repository-specific advisory style lint checks."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import urllib.error
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

style_lint = importlib.import_module("lib.style_lint")

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PATHS = (Path("src"), Path("tests"), Path("evals"))
DEFAULT_CHUNK_LINES = 120

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
]

DETECTOR_INSTRUCTIONS = dedent("""\
    You are reviewing Python source for evalspec's documented local style guide.
    The relevant policy lives in docs/style/development.md.

    Review the numbered source chunks and return advisory findings for only the
    supplied rules. Prefer precise, high-confidence findings over exhaustive
    guesses. Do not report imports, formatting, docstrings, or annotations that
    Ruff already covers. Do not invent rules.
""")


def run(
    paths: list[Path] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    verify_model: str | None = None,
    max_lines: int = DEFAULT_CHUNK_LINES,
) -> int:
    """Run evalspec advisory style lint and print findings.

    Args:
        paths: Optional source paths to lint.
        model: Gemini detector model.
        verify_model: Optional Gemini model for second-pass verification.
        max_lines: Maximum source lines per model chunk.

    Returns:
        Always returns zero because this linter is advisory.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("skip: GEMINI_API_KEY is not set; advisory style lint is disabled")
        return 0

    try:
        targets = style_lint.collect_python_files(
            paths,
            default_paths=DEFAULT_PATHS,
        )
        chunks = style_lint.chunk_source_files(targets, max_lines=max_lines)
        prompt = style_lint.build_detector_prompt(
            chunks=chunks,
            rules=RULES,
            instructions=DETECTOR_INSTRUCTIONS,
        )
        response = style_lint.call_gemini(
            prompt=prompt,
            api_key=api_key,
            model=model,
        )
        findings = style_lint.parse_findings(response, chunks=chunks, rules=RULES)
        if verify_model is not None:
            findings = style_lint.verify_findings(
                findings=findings,
                chunks=chunks,
                rules=RULES,
                instructions=DETECTOR_INSTRUCTIONS,
                api_key=api_key,
                model=verify_model,
            )
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        urllib.error.URLError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"warning: advisory style lint skipped due to model error: {error}")
        return 0

    for line in style_lint.format_findings(findings):
        print(line)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the style lint CLI."""
    parser = argparse.ArgumentParser(prog="style_lint")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verify-model")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_CHUNK_LINES)
    args = parser.parse_args(argv)
    return run(
        list(args.paths) or None,
        model=args.model,
        verify_model=args.verify_model,
        max_lines=args.max_lines,
    )


if __name__ == "__main__":
    raise SystemExit(main())
