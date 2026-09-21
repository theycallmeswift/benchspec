"""Markdown eval format: one self-contained `evals/<group>/eval.md` per output eval.

Parses into the exact dict shape `schema._validate` checks — schema stays the single
validation truth; this module owns only Markdown structure. Strict and loud: unknown
headings, plain `-` bullets, prose outside known sections, and grandchild/ragged nesting
are hard errors. A `- [ ]` item with indented `- [ ]` children is a display-only header
whose children flatten to standalone assertions in document order (one nesting level
only); a childless item is one assertion.

A graded assertion may carry one indented `- if: <expr>` / `- unless: <expr>` sub-bullet,
its scope clause, kept raw beside it (`clauses`, aligned by index). A clause scopes the
`- [ ]` line directly above it; a display-only parent is never graded, so it cannot carry one.

Eval file `evals/<group>/eval.md` (or `evals/<group>/<stem>.eval.md`): YAML frontmatter
(`history:` only — an optional list of `{role, content}` turns or sibling JSONL path) +
`## Prompt` prose (required) + `## Assertions` checklist (required, all prose; H3
subheadings are display-only groups, flattened in document order). The eval id is the
parent folder name for `eval.md`, or the `<stem>` for `<stem>.eval.md`. Single-turn only:
`history:` carries any prior context; there is no `## Turn` syntax.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from benchspec.specs import schema

_FENCE = re.compile(r"^(```|~~~)")
_HEADER = re.compile(r"^(#{2,3}) +(.+?)\s*$")
_CHECKBOX = re.compile(r"^- \[[ xX]\] +(.*\S)\s*$")
_CLAUSE = re.compile(r"^- (if|unless): +(.*\S)\s*$")
_CLAUSE_LIKE = re.compile(r"^- (?i:if|unless)\b")

_EVAL_FM = {"history"}

_DISPLAY_ONLY_PARENT = (
    "a clause scopes one graded assertion; a display-only parent cannot carry one"
)


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


class _Item:
    """One top-level `- [ ]` item being assembled: its prose, clause, and child indent."""

    def __init__(self, text: str) -> None:
        """Start a childless, unclaused item."""
        self.text = text
        self.clause: dict | None = None
        # Kept so a later child can name the offending line in its error.
        self.clause_line: str | None = None
        self.child_indent: int | None = None


class _Checklist:
    """Accumulate one checklist body into aligned assertion and clause lists."""

    def __init__(self, where: str, path: Path) -> None:
        """Start with no items; `where` and `path` prefix every error."""
        self.items: list[str] = []
        self.clauses: list[dict | None] = []
        self._where = where
        self._path = path
        self._item: _Item | None = None

    def _error(self, message: str, line: str) -> MdFormatError:
        """Build the hard error for `line`, prefixed with the eval path and section."""
        return MdFormatError(f"{self._path}: {self._where}: {message}: {line!r}")

    def flush(self) -> None:
        """Emit the pending item as one assertion unless its children stood in for it."""
        if self._item is not None and self._item.child_indent is None:
            self.items.append(self._item.text)
            self.clauses.append(self._item.clause)

    def top_level(self, line: str) -> None:
        """Start a new item from an unindented line."""
        self.flush()

        checkbox_match = _CHECKBOX.match(line)
        if not checkbox_match:
            raise self._error("expected `- [ ] ...` items, got", line)

        self._item = _Item(checkbox_match.group(1))

    def indented(self, line: str) -> None:
        """Attach an indented line to the pending item as a child or a clause."""
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        clause_match = _CLAUSE.match(stripped)
        if clause_match:
            clause = {"key": clause_match.group(1), "expr": clause_match.group(2)}
            self._clause(clause, indent, line)
            return
        if _CLAUSE_LIKE.match(stripped):
            raise self._error("a clause reads `- if: <expr>` or `- unless: <expr>`", line)
        if self._item is None:
            raise self._error("indented `- [ ]` child with no parent item above it", line)

        checkbox_match = _CHECKBOX.match(stripped)
        if not checkbox_match:
            raise self._error(
                "indented lines must be `- [ ]` children — one assertion per line, "
                "no continuations",
                line,
            )

        self._child(self._item, checkbox_match.group(1), indent, line)

    def _child(self, item: _Item, text: str, indent: int, line: str) -> None:
        """Flatten a `- [ ]` child to its own assertion; `item` becomes display-only."""
        if item.clause_line is not None:
            raise self._error(_DISPLAY_ONLY_PARENT, item.clause_line)

        self._align(item, indent, line)
        self.items.append(text)
        self.clauses.append(None)

    def _align(self, item: _Item, indent: int, line: str) -> None:
        """Pin the child indent on first use; later siblings must sit exactly there."""
        if item.child_indent is None:
            item.child_indent = indent
        elif indent > item.child_indent:
            raise self._error("only one nesting level — unexpected grandchild", line)
        elif indent < item.child_indent:
            raise self._error("ragged child indent — siblings must align", line)

    def _clause(self, clause: dict, indent: int, line: str) -> None:
        """Attach a clause to the `- [ ]` line directly above it: the item or its last child."""
        item = self._item
        if item is None:
            raise self._error("clause with no `- [ ]` item above it", line)

        child_indent = item.child_indent
        if child_indent is None:
            self._item_clause(item, clause, line)
        elif indent > child_indent:
            self._child_clause(clause, line)
        else:
            raise self._error(_DISPLAY_ONLY_PARENT, line)

    def _item_clause(self, item: _Item, clause: dict, line: str) -> None:
        """Attach a clause to a still-childless item."""
        if item.clause is not None:
            raise self._error("item already carries a clause", line)

        item.clause = clause
        item.clause_line = line

    def _child_clause(self, clause: dict, line: str) -> None:
        """Attach a clause to the item's last child."""
        if self.clauses[-1] is not None:
            raise self._error("item already carries a clause", line)

        self.clauses[-1] = clause


def _checklist(
    content_lines: list[str], where: str, path: Path
) -> tuple[list[str], list[dict | None]]:
    """Flatten one checklist body to plain-prose assertions and their aligned clauses.

    A parent with children is a display-only header; each child becomes a standalone
    assertion. Parent and children must share one body: `_collect_assertions` calls this per
    section, so a child indented under a following `###` group surfaces here as an
    orphan with no parent rather than adopting the prior section's parent.
    """
    checklist = _Checklist(where, path)
    for line in content_lines:
        if not line.strip():
            continue

        if line[0].isspace():
            checklist.indented(line)
        else:
            checklist.top_level(line)

    checklist.flush()
    return checklist.items, checklist.clauses


def _collect_assertions(
    sections: list[tuple[int, str, list[str]]], start: int, path: Path
) -> tuple[list[str], list[dict | None]]:
    """The Assertions H2 at `start` plus its H3 groups, flattened in order.

    Groups are display-only — no semantics ride on the H3 title. Returns the assertions
    and their aligned clauses.
    """
    items, clauses = _checklist(sections[start][2], "Assertions", path)
    for level, title, content in sections[start + 1 :]:
        if level == 2:
            break

        group_items, group_clauses = _checklist(content, f"Assertions / {title}", path)
        items.extend(group_items)
        clauses.extend(group_clauses)

    if not items:
        raise MdFormatError(f"{path}: Assertions section has no checklist items")

    return items, clauses


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
        history = fm["history"]
        if isinstance(history, list):
            schema._validate_history(history, f"{path}: history")
            result["history"] = history
        elif isinstance(history, str):
            result["history"] = schema._validate_history_path(history, path)
        else:
            raise MdFormatError(
                f"{path}: history: expected list or string path, "
                f"got {type(history).__name__}"
            )

    sections = _sections(body_lines, path)
    prompt = None
    assertions = None
    clauses: list[dict | None] = []
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
            assertions, clauses = _collect_assertions(sections, i, path)
        else:
            raise MdFormatError(f"{path}: unexpected `## {title}`")
    if not prompt:
        raise MdFormatError(f"{path}: missing or empty `## Prompt`")
    if not assertions:
        raise MdFormatError(f"{path}: missing or empty `## Assertions`")

    result["prompt"] = prompt
    result["assertions"] = assertions
    result["clauses"] = clauses

    return result
