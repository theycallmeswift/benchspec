"""Markdown eval format: one self-contained `evals/<group>/eval.md` per output eval.

Parses into the exact dict shape `schema._validate` checks — schema stays the single
validation truth; this module owns only Markdown structure. Strict and loud: unknown
headings, plain `-` bullets, prose outside known sections, and grandchild/ragged nesting
are hard errors. A `- [ ]` item with indented `- [ ]` children is a display-only header
whose children flatten to standalone assertions in document order (one nesting level
only); a childless item is one assertion.

Eval file `evals/<group>/eval.md` (or `evals/<group>/<stem>.eval.md`): YAML frontmatter
(`history:` only — an optional list of `{role, content}` turns) + `## Prompt` prose
(required) + `## Assertions` checklist (required, all prose; H3 subheadings are
display-only groups, flattened in document order). The eval id is the parent folder name
for `eval.md`, or the `<stem>` for `<stem>.eval.md`. Single-turn only: `history:` carries
any prior context; there is no `## Turn` syntax.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from harnessbench.specs import schema

_FENCE = re.compile(r"^(```|~~~)")
_HEADER = re.compile(r"^(#{2,3}) +(.+?)\s*$")
_CHECKBOX = re.compile(r"^- \[[ xX]\] +(.*\S)\s*$")

_EVAL_FM = {"history"}


class MdFormatError(schema.SchemaError):
    """Signal md format failures."""

    pass


def _split_frontmatter(text: str, path: Path) -> tuple[dict, list[str]]:
    """Split Markdown frontmatter from the document body."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise MdFormatError(f"{path}: must start with `---` frontmatter")
    for line_index in range(1, len(lines)):
        if lines[line_index].strip() == "---":
            try:
                frontmatter = yaml.safe_load("\n".join(lines[1:line_index])) or {}
            except yaml.YAMLError as error:
                raise MdFormatError(f"{path}: invalid YAML frontmatter: {error}") from error
            if not isinstance(frontmatter, dict):
                raise MdFormatError(f"{path}: frontmatter must be a YAML mapping")
            return frontmatter, lines[line_index + 1 :]
    raise MdFormatError(f"{path}: unterminated frontmatter (no closing `---`)")


def _check_fm_keys(frontmatter: dict, allowed: set[str], path: Path) -> None:
    """Validate frontmatter keys against the allowed set."""
    extra = set(frontmatter) - allowed
    if extra:
        raise MdFormatError(
            f"{path}: unknown frontmatter key(s) {sorted(extra)} (allowed: {sorted(allowed)})"
        )


def _sections(body_lines: list[str], path: Path) -> list[tuple[int, str, list[str]]]:
    """Split the body into (level, title, content_lines), fence-aware."""
    sections: list[tuple[int, str, list[str]]] = []
    in_fence = False
    preamble: list[str] = []
    current = preamble
    for line in body_lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            current.append(line)
            continue
        header_match = None if in_fence else _HEADER.match(line)
        if header_match:
            current = []
            sections.append((len(header_match.group(1)), header_match.group(2), current))
        else:
            current.append(line)
    if in_fence:
        raise MdFormatError(f"{path}: unclosed code fence")
    if any(ln.strip() for ln in preamble):
        raise MdFormatError(f"{path}: prose before the first `##` section")
    return sections


def _prose(content_lines: list[str]) -> str:
    """Normalize parsed Markdown prose into assertion text."""
    return "\n".join(content_lines).strip()


def _checklist(content_lines: list[str], where: str, path: Path) -> list[str]:
    """Flatten one checklist body to plain-prose assertions, expanding a single.

    level of parent/child nesting (a parent with children is a display-only
    header; each child becomes a standalone assertion).

    Parent and children must share one body: `_collect_assertions` calls this
    per section, so a child indented under a following `###` group surfaces here
    as an orphan with no parent rather than adopting the prior section's parent.
    """
    items: list[str] = []
    parent: str | None = None
    parent_has_children = False
    child_indent: int | None = None

    def flush() -> None:
        """Flush one pending Markdown assertion block into parsed output."""
        if parent is not None and not parent_has_children:
            items.append(parent)

    for line in content_lines:
        if not line.strip():
            continue
        if not line[0].isspace():
            flush()
            checkbox_match = _CHECKBOX.match(line)
            if not checkbox_match:
                raise MdFormatError(f"{path}: {where}: expected `- [ ] ...` items, got: {line!r}")
            parent = checkbox_match.group(1)
            parent_has_children = False
            child_indent = None
            continue
        if parent is None:
            raise MdFormatError(
                f"{path}: {where}: indented `- [ ]` child with no parent item above it: {line!r}"
            )
        indent = len(line) - len(line.lstrip())
        checkbox_match = _CHECKBOX.match(line.lstrip())
        if not checkbox_match:
            raise MdFormatError(
                f"{path}: {where}: indented lines must be `- [ ]` children — "
                f"one assertion per line, no continuations: {line!r}"
            )
        if child_indent is None:
            child_indent = indent
        elif indent > child_indent:
            raise MdFormatError(
                f"{path}: {where}: only one nesting level — unexpected grandchild: {line!r}"
            )
        elif indent < child_indent:
            raise MdFormatError(
                f"{path}: {where}: ragged child indent — siblings must align: {line!r}"
            )
        parent_has_children = True
        items.append(checkbox_match.group(1))

    flush()
    return items


def _collect_assertions(
    sections: list[tuple[int, str, list[str]]], start: int, path: Path
) -> list[str]:
    """The Assertions H2 at `start` plus its H3 groups, flattened in order.

    Groups are display-only — no semantics ride on the H3 title.
    """
    items = _checklist(sections[start][2], "Assertions", path)
    for level, title, content in sections[start + 1 :]:
        if level == 2:
            break
        items.extend(_checklist(content, f"Assertions / {title}", path))
    if not items:
        raise MdFormatError(f"{path}: Assertions section has no checklist items")
    return items


def parse_eval_md(path: Path) -> dict:
    """Parse one `eval.md` / `*.eval.md` file into schema input.

    The eval id is the parent folder name for `eval.md`, or the file stem for
    `<stem>.eval.md`. Any other filename is a hard error.
    """
    if path.name == "eval.md":
        eval_id = path.parent.name
    elif path.name.endswith(".eval.md"):
        eval_id = path.name[: -len(".eval.md")]
    else:
        raise MdFormatError(f"{path}: eval files must be named `eval.md` or `*.eval.md`")
    fm, body_lines = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    _check_fm_keys(fm, _EVAL_FM, path)
    result: dict = {"id": eval_id}
    if "history" in fm:
        # Validate here so the single-file path is as strict as discovery: a bare
        # parse_eval_md call (linter, per-file tooling) must still reject a malformed
        # history block, not defer that to schema inside discovery.
        schema._validate_history(fm["history"], f"{path}: history")
        result["history"] = fm["history"]

    sections = _sections(body_lines, path)
    prompt = None
    assertions = None
    last_h2 = None
    for i, (level, title, content) in enumerate(sections):
        if level == 3:
            # H3s are display-only groups, legal only under `## Assertions` (where
            # _collect_assertions consumes them). Anywhere else they'd be silently
            # dropped, so reject them loudly.
            if last_h2 != "Assertions":
                raise MdFormatError(f"{path}: `### {title}` outside an `## Assertions` section")
            continue
        last_h2 = title
        if title == "Prompt":
            prompt = _prose(content)
        elif title == "Assertions":
            # A present `## Assertions` must be non-empty; _collect_assertions enforces it.
            assertions = _collect_assertions(sections, i, path)
        else:
            raise MdFormatError(f"{path}: unexpected `## {title}`")
    if not prompt:
        raise MdFormatError(f"{path}: missing or empty `## Prompt`")
    if not assertions:
        raise MdFormatError(f"{path}: missing or empty `## Assertions`")
    result["prompt"] = prompt
    result["assertions"] = assertions
    return result
