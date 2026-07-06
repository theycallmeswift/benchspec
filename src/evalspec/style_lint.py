"""Repository-specific advisory style lint checks."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import tokenize
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PATHS = (Path("src"), Path("tests"), Path("evals"))


@dataclass(frozen=True)
class Rule:
    """One advisory style rule."""

    id: str
    description: str


@dataclass(frozen=True)
class Candidate:
    """A deterministic candidate snippet for model review."""

    path: Path
    line: int
    column: int
    rule_id: str
    text: str


@dataclass(frozen=True)
class Finding:
    """An advisory finding emitted for a candidate."""

    path: Path
    line: int
    column: int
    rule_id: str
    message: str


RULES = [
    Rule(
        id="no-suppression-comments",
        description="Do not use lint or type-check suppression comments.",
    ),
    Rule(
        id="section-header-comments",
        description="Do not use region-style comments instead of named structure.",
    ),
    Rule(
        id="provenance-comments",
        description=(
            "Do not reference PRs, issues, commits, callers, or planning docs "
            "in source comments."
        ),
    ),
    Rule(
        id="descriptive-names",
        description=(
            "Do not use single-letter bindings except `_` for intentionally "
            "unused values."
        ),
    ),
    Rule(
        id="dedented-multiline-strings",
        description="Use textwrap.dedent for indented multiline strings.",
    ),
]

_RULE_IDS = {rule.id for rule in RULES}
_SUPPRESSION_PATTERNS = (
    "noqa",
    "type: ignore",
    "pyright: ignore",
    "pylint: disable",
    "ruff: noqa",
    "mypy:",
)
_SECTION_DIVIDER = re.compile(r"^[-=*_~#\s]+$")
_SECTION_WRAPPED_LABEL = re.compile(r"^[-=*_~#\s]+[A-Za-z][A-Za-z0-9 /_-]*[-=*_~#\s]+$")
_SECTION_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9 /_-]{0,40}$")
_PROVENANCE_PATTERN = re.compile(
    r"\b(?:pr|issue|fixes)\s*\#\d+\b|"
    r"\bcommit\s+[0-9a-f]{6,40}\b|"
    r"\badded\s+for\b|"
    r"\bcalled\s+from\b|"
    r"\bcallers?\b|"
    r"\bplanning\s+docs?\b|"
    r"\bsee\s+docs/|"
    r"\bdocs/",
    re.IGNORECASE,
)
_STRING_PREFIX = re.compile(r"(?i)^[rubf]*")


def collect_targets(paths: list[Path] | None = None) -> list[Path]:
    """Collect Python source files for style linting."""
    roots = DEFAULT_PATHS if paths is None else tuple(paths)
    targets: set[Path] = set()

    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.exists():
            continue
        if resolved_root.is_file():
            if resolved_root.suffix == ".py":
                targets.add(resolved_root)
            continue

        for path in resolved_root.rglob("*.py"):
            if path.is_file():
                targets.add(path.resolve())

    return sorted(targets)


def find_candidates(path: Path) -> list[Candidate]:
    """Find deterministic candidate findings in one Python file."""
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    parents = _build_parent_map(tree)
    line_text = source.splitlines()
    candidates: list[Candidate] = []
    candidates.extend(_find_name_candidates(path, tree, line_text))
    candidates.extend(_find_comment_candidates(path, source, line_text))
    candidates.extend(
        _find_multiline_string_candidates(
            path,
            source,
            tree,
            parents,
            line_text,
        )
    )
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.line,
            candidate.column,
            candidate.rule_id,
        ),
    )


def build_detector_prompt(
    candidates: list[Candidate],
    rules: list[Rule] = RULES,
) -> str:
    """Build the Gemini detector prompt for advisory style findings."""
    payload = {
        "rules": [
            {"id": rule.id, "description": rule.description}
            for rule in rules
        ],
        "candidates": [
            {
                "index": index,
                "path": str(candidate.path),
                "line": candidate.line,
                "column": candidate.column,
                "rule_id": candidate.rule_id,
                "text": candidate.text,
            }
            for index, candidate in enumerate(candidates)
        ],
        "instructions": (
            "Return strict JSON with shape "
            '{"findings":[{"candidate_index":0,"rule_id":"...","message":"..."}]}. '
            "Only emit findings supported by the provided candidates. "
            "Do not invent rule IDs."
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def call_gemini(prompt: str, *, model: str) -> str:
    """Call the Gemini REST API and return the model's JSON text response."""
    api_key = os.environ["GEMINI_API_KEY"]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    request_body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object")

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response did not include candidates")

    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise ValueError("Gemini response candidate must be an object")

    content = first_candidate.get("content")
    if not isinstance(content, dict):
        raise ValueError("Gemini response candidate did not include content")

    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Gemini response content did not include parts")

    texts = [part["text"] for part in parts if "text" in part]
    if not texts:
        raise ValueError("Gemini response did not include text content")
    return "".join(texts)


def parse_findings(response: str, candidates: list[Candidate]) -> list[Finding]:
    """Parse strict JSON findings returned by Gemini."""
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("Gemini response must be a JSON object")

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("Gemini response must include a findings list")

    findings: list[Finding] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            raise ValueError("Each finding must be a JSON object")

        rule_id = raw_finding.get("rule_id")
        if rule_id not in _RULE_IDS:
            raise ValueError(f"Unknown rule ID: {rule_id}")

        candidate_index = raw_finding.get("candidate_index")
        if not isinstance(candidate_index, int):
            raise ValueError(f"Malformed finding reference: {candidate_index!r}")
        if candidate_index < 0 or candidate_index >= len(candidates):
            raise ValueError(f"Malformed finding reference: {candidate_index!r}")

        message = raw_finding.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Finding message must be a non-empty string")

        candidate = candidates[candidate_index]
        if rule_id != candidate.rule_id:
            raise ValueError(
                "Finding rule ID does not match candidate rule ID: "
                f"{rule_id} != {candidate.rule_id}"
            )
        findings.append(
            Finding(
                path=candidate.path,
                line=candidate.line,
                column=candidate.column,
                rule_id=rule_id,
                message=message.strip(),
            )
        )

    return findings


def detect_findings(
    candidates: list[Candidate],
    *,
    model: str = DEFAULT_MODEL,
) -> list[Finding]:
    """Call Gemini for advisory findings on the collected candidates."""
    if not candidates:
        return []
    prompt = build_detector_prompt(candidates)
    response = call_gemini(prompt, model=model)
    return parse_findings(response, candidates)


def verify_findings(
    findings: list[Finding],
    candidates: list[Candidate],
    *,
    verify_model: str | None,
) -> list[Finding]:
    """Optionally ask a second model to keep or drop detector findings."""
    if verify_model is None or not findings:
        return findings

    payload = {
        "instructions": (
            "Return strict JSON with shape {'keep_indexes':[0]}. "
            "Indexes refer to the proposed_findings array. Keep only findings "
            "you support."
        ),
        "candidates": [
            {
                "index": index,
                "path": str(candidate.path),
                "line": candidate.line,
                "column": candidate.column,
                "rule_id": candidate.rule_id,
                "text": candidate.text,
            }
            for index, candidate in enumerate(candidates)
        ],
        "proposed_findings": [
            {
                "index": index,
                "path": str(finding.path),
                "line": finding.line,
                "column": finding.column,
                "rule_id": finding.rule_id,
                "message": finding.message,
            }
            for index, finding in enumerate(findings)
        ],
    }
    response = call_gemini(
        json.dumps(payload, indent=2, sort_keys=True),
        model=verify_model,
    )
    parsed = json.loads(response)
    if not isinstance(parsed, dict):
        raise ValueError("Gemini verification response must be a JSON object")

    keep_indexes = parsed.get("keep_indexes")
    if not isinstance(keep_indexes, list):
        raise ValueError("Gemini verification response must include keep_indexes")

    verified_findings: list[Finding] = []
    for keep_index in keep_indexes:
        if (
            not isinstance(keep_index, int)
            or keep_index < 0
            or keep_index >= len(findings)
        ):
            raise ValueError(f"Malformed finding reference: {keep_index!r}")
        verified_findings.append(findings[keep_index])
    return verified_findings


def run(
    paths: list[Path] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    verify_model: str | None = None,
) -> int:
    """Run the advisory style linter and print findings."""
    if not os.environ.get("GEMINI_API_KEY"):
        print("skip: GEMINI_API_KEY is not set; advisory style lint is disabled")
        return 0

    targets = collect_targets(paths)
    candidates: list[Candidate] = []
    for target in targets:
        candidates.extend(find_candidates(target))

    try:
        findings = detect_findings(candidates, model=model)
        if verify_model is not None:
            findings = verify_findings(
                findings,
                candidates,
                verify_model=verify_model,
            )
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        print(f"warning: advisory style lint skipped due to model error: {error}")
        return 0

    for finding in findings:
        print(
            f"{finding.path}:{finding.line}:{finding.column}: "
            f"{finding.rule_id} {finding.message}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the style lint CLI."""
    parser = argparse.ArgumentParser(prog="style_lint")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verify-model")
    args = parser.parse_args(argv)
    return run(
        list(args.paths) or None,
        model=args.model,
        verify_model=args.verify_model,
    )


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Build parent pointers for AST nodes."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _find_name_candidates(
    path: Path,
    tree: ast.AST,
    line_text: list[str],
) -> list[Candidate]:
    """Find single-letter binding names."""
    candidates: list[Candidate] = []

    def add_name(name: str, *, line: int, column: int) -> None:
        if len(name) != 1 or name == "_":
            return
        candidates.append(
            Candidate(
                path=path,
                line=line,
                column=column,
                rule_id="descriptive-names",
                text=line_text[line - 1],
            )
        )

    def visit_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            add_name(target.id, line=target.lineno, column=target.col_offset + 1)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                visit_target(element)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for argument in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            ):
                add_name(
                    argument.arg,
                    line=argument.lineno,
                    column=argument.col_offset + 1,
                )
            if node.args.vararg is not None:
                add_name(
                    node.args.vararg.arg,
                    line=node.args.vararg.lineno,
                    column=node.args.vararg.col_offset + 1,
                )
            if node.args.kwarg is not None:
                add_name(
                    node.args.kwarg.arg,
                    line=node.args.kwarg.lineno,
                    column=node.args.kwarg.col_offset + 1,
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                visit_target(target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            visit_target(node.target)
        elif isinstance(node, ast.comprehension):
            visit_target(node.target)
    return candidates


def _find_comment_candidates(
    path: Path,
    source: str,
    line_text: list[str],
) -> list[Candidate]:
    """Find comment-based candidates."""
    candidates: list[Candidate] = []
    stream = io.StringIO(source)
    for token in tokenize.generate_tokens(stream.readline):
        if token.type != tokenize.COMMENT:
            continue

        comment_text = token.string[1:].strip()
        lowered = comment_text.lower()
        line, column = token.start[0], token.start[1] + 1
        text = line_text[line - 1]

        if any(pattern in lowered for pattern in _SUPPRESSION_PATTERNS):
            candidates.append(
                Candidate(
                    path=path,
                    line=line,
                    column=column,
                    rule_id="no-suppression-comments",
                    text=text,
                )
            )

        if _looks_like_section_header(comment_text):
            candidates.append(
                Candidate(
                    path=path,
                    line=line,
                    column=column,
                    rule_id="section-header-comments",
                    text=text,
                )
            )

        if _PROVENANCE_PATTERN.search(comment_text):
            candidates.append(
                Candidate(
                    path=path,
                    line=line,
                    column=column,
                    rule_id="provenance-comments",
                    text=text,
                )
            )

    return candidates


def _find_multiline_string_candidates(
    path: Path,
    source: str,
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
    line_text: list[str],
) -> list[Candidate]:
    """Find indented triple-quoted strings that skip textwrap.dedent."""
    constants = {
        (node.lineno, node.col_offset): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    candidates: list[Candidate] = []
    stream = io.StringIO(source)

    for token in tokenize.generate_tokens(stream.readline):
        if token.type != tokenize.STRING or not _is_triple_quoted(token.string):
            continue

        line, column = token.start[0], token.start[1]
        if column == 0:
            continue

        constant = constants.get((line, column))
        if (
            constant is None
            or _is_docstring(constant, parents)
            or _is_dedent_wrapped(constant, parents)
        ):
            continue

        candidates.append(
            Candidate(
                path=path,
                line=line,
                column=column + 1,
                rule_id="dedented-multiline-strings",
                text=line_text[line - 1],
            )
        )

    return candidates


def _looks_like_section_header(comment_text: str) -> bool:
    """Return whether a comment looks like a region label or divider."""
    if not comment_text:
        return False
    if ":" in comment_text:
        return False

    normalized = comment_text.strip()
    if _SECTION_DIVIDER.fullmatch(normalized):
        return True
    if _SECTION_WRAPPED_LABEL.fullmatch(normalized):
        return True

    stripped = normalized.strip("-=*_~# ").strip()
    if not stripped:
        return True
    if not _SECTION_LABEL.fullmatch(stripped):
        return False
    words = stripped.split()
    if len(words) > 4:
        return False
    return all(_is_section_label_word(word) for word in words)


def _is_section_label_word(word: str) -> bool:
    """Return whether a word looks like a section label token."""
    token = word.strip("/_-")
    if not token:
        return False
    if token.isupper():
        return True
    return token[0].isupper() and token[1:] == token[1:].lower()


def _is_triple_quoted(token_string: str) -> bool:
    """Return whether a token string is triple-quoted."""
    prefix = _STRING_PREFIX.match(token_string)
    literal = token_string[prefix.end():] if prefix else token_string
    return literal.startswith('"""') or literal.startswith("'''")


def _is_docstring(node: ast.Constant, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether a string constant is used as a docstring."""
    parent = parents.get(node)
    if not isinstance(parent, ast.Expr):
        return False
    container = parents.get(parent)
    if not isinstance(
        container,
        (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
    ):
        return False
    return bool(container.body) and container.body[0] is parent


def _is_dedent_wrapped(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether a node is inside a textwrap.dedent call."""
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Call) and _is_dedent_call(current.func):
            return True
    return False


def _is_dedent_call(func: ast.AST) -> bool:
    """Return whether a call target resolves to textwrap.dedent."""
    if isinstance(func, ast.Name):
        return func.id == "dedent"
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "textwrap"
        and func.attr == "dedent"
    )
