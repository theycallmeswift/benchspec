"""Markdown eval format: one self-contained `evals/<slug>/prompt.md` per output eval.

Parses into the exact dict shape `schema._validate` checks — schema stays the
single validation truth; this module owns only Markdown structure. Strict and
loud: unknown headings, plain `-` bullets, prose outside known sections, and
grandchild/ragged nesting are hard errors. A `- [ ]` item with indented `- [ ]`
children is a display-only header whose children flatten to standalone
assertions in document order (one nesting level only); a childless item is one
assertion.

Eval file `evals/<slug>/prompt.md`: YAML frontmatter (`seed:` only — an optional
list of `{role, text}` turns) + `## Prompt` prose (required) + `## Assertions`
checklist (required, all prose; H3 subheadings are display-only groups, flattened
in document order). The slug is the parent directory name. Single-turn only:
`seed:` carries any prior context; there is no `## Turn` syntax.

Trigger evals: `trigger-evals.md` — frontmatter `skill_name` + optional
`## Description` + `## Trigger`/`## No Trigger` sections of `- <slug>: <query>`
lines, each with an optional indented `fails-on [tiers]: reason`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from evalspec import schema

_FENCE = re.compile(r"^(```|~~~)")
_HEADER = re.compile(r"^(#{2,3}) +(.+?)\s*$")
_CHECKBOX = re.compile(r"^- \[[ xX]\] +(.*\S)\s*$")

_TRIG_QUERY = re.compile(r"^- (\S.*)$")
_TRIG_XFAIL = re.compile(r"^  - fails-on \[([^\]]+)\]: (\S.*)$")
_TRIG_FM = {"skill_name"}
_TRIGGER_TITLES = {"Trigger": True, "No Trigger": False}

_EVAL_FM = {"seed"}

# *.md filenames inside an evals/ dir that are NOT per-slug output evals. Output
# evals are `evals/<slug>/prompt.md` dirs; the slug-dir glob ignores stray files,
# so this only documents the reserved trigger-evals.md name.
NON_EVAL_MD = frozenset({"trigger-evals.md"})


class MdFormatError(schema.SchemaError):
    pass


def _split_frontmatter(text: str, path: Path) -> tuple[dict, list[str]]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise MdFormatError(f"{path}: must start with `---` frontmatter")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                fm = yaml.safe_load("\n".join(lines[1:i])) or {}
            except yaml.YAMLError as e:
                raise MdFormatError(f"{path}: invalid YAML frontmatter: {e}") from e
            if not isinstance(fm, dict):
                raise MdFormatError(f"{path}: frontmatter must be a YAML mapping")
            return fm, lines[i + 1:]
    raise MdFormatError(f"{path}: unterminated frontmatter (no closing `---`)")


def _check_fm_keys(fm: dict, allowed: set[str], path: Path) -> None:
    extra = set(fm) - allowed
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
        m = None if in_fence else _HEADER.match(line)
        if m:
            current = []
            sections.append((len(m.group(1)), m.group(2), current))
        else:
            current.append(line)
    if in_fence:
        raise MdFormatError(f"{path}: unclosed code fence")
    if any(ln.strip() for ln in preamble):
        raise MdFormatError(f"{path}: prose before the first `##` section")
    return sections


def _prose(content_lines: list[str]) -> str:
    return "\n".join(content_lines).strip()


def _checklist(content_lines: list[str], where: str, path: Path) -> list[str]:
    """Flatten one checklist body to plain-prose assertions, expanding a single
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
        if parent is not None and not parent_has_children:
            items.append(parent)

    for line in content_lines:
        if not line.strip():
            continue
        if not line[0].isspace():
            flush()
            m = _CHECKBOX.match(line)
            if not m:
                raise MdFormatError(
                    f"{path}: {where}: expected `- [ ] ...` items, got: {line!r}"
                )
            parent = m.group(1)
            parent_has_children = False
            child_indent = None
            continue
        if parent is None:
            raise MdFormatError(
                f"{path}: {where}: indented `- [ ]` child with no parent item "
                f"above it: {line!r}"
            )
        indent = len(line) - len(line.lstrip())
        m = _CHECKBOX.match(line.lstrip())
        if not m:
            raise MdFormatError(
                f"{path}: {where}: indented lines must be `- [ ]` children — "
                f"one assertion per line, no continuations: {line!r}"
            )
        if child_indent is None:
            child_indent = indent
        elif indent > child_indent:
            raise MdFormatError(
                f"{path}: {where}: only one nesting level — unexpected "
                f"grandchild: {line!r}"
            )
        elif indent < child_indent:
            raise MdFormatError(
                f"{path}: {where}: ragged child indent — siblings must align: "
                f"{line!r}"
            )
        parent_has_children = True
        items.append(m.group(1))

    flush()
    return items


def _collect_assertions(
    sections: list[tuple[int, str, list[str]]], start: int, path: Path
) -> list[str]:
    """The Assertions H2 at `start` plus its H3 groups, flattened in order.
    Groups are display-only — no semantics ride on the H3 title."""
    items = _checklist(sections[start][2], "Assertions", path)
    for level, title, content in sections[start + 1:]:
        if level == 2:
            break
        items.extend(_checklist(content, f"Assertions / {title}", path))
    if not items:
        raise MdFormatError(f"{path}: Assertions section has no checklist items")
    return items


def parse_eval_md(path: Path) -> dict:
    """Parse one self-contained eval `evals/<slug>/prompt.md`; the slug is the parent directory name."""
    fm, body_lines = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    _check_fm_keys(fm, _EVAL_FM, path)
    result: dict = {"slug": path.parent.name}
    if "seed" in fm:
        # Validate here so the single-file path is as strict as load_suite_dir:
        # a bare parse_eval_md call (linter, per-file tooling) must still reject a
        # malformed seed, not defer that to schema._validate inside the suite path.
        schema._validate_seed(fm["seed"], f"{path}: seed")
        result["seed"] = fm["seed"]

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
                raise MdFormatError(
                    f"{path}: `### {title}` outside an `## Assertions` section"
                )
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


def _parse_trigger_section(content_lines: list[str], should_trigger: bool, path: Path) -> list[dict]:
    """One polarity section (`## Trigger` / `## No Trigger`) → query dicts.

    A query is `- <slug>: <query>`; the section it sits under decides should_trigger.
    An optional 2-space-indented `- fails-on [tiers]: reason` rides under its query;
    deeper indented lines continue the reason. Slug uniqueness + kebab are schema's job.
    """
    label = "Trigger" if should_trigger else "No Trigger"
    queries: list[dict] = []
    current: dict | None = None
    reason_open = False
    for line in content_lines:
        if not line.strip():
            reason_open = False
            continue
        mq = _TRIG_QUERY.match(line)
        if mq:
            body = mq.group(1)
            if ": " not in body:
                raise MdFormatError(
                    f"{path}: {label}: expected `<slug>: <query>`, got: {body!r}"
                )
            slug, query = body.split(": ", 1)
            current = {"slug": slug.strip(), "query": query.rstrip(),
                       "should_trigger": should_trigger}
            queries.append(current)
            reason_open = False
            continue
        mx = _TRIG_XFAIL.match(line)
        if mx:
            if current is None:
                raise MdFormatError(f"{path}: {label}: fails-on before any query: {line!r}")
            if "xfail" in current:
                raise MdFormatError(f"{path}: {label}: duplicate fails-on for `{current['slug']}`")
            models = [t.strip() for t in mx.group(1).split(",")]
            current["xfail"] = {"models": models, "reason": mx.group(2).rstrip()}
            reason_open = True
            continue
        if line[0].isspace() and reason_open:
            current["xfail"]["reason"] += " " + line.strip()
            continue
        raise MdFormatError(f"{path}: {label}: unexpected line: {line!r}")
    return queries


def parse_trigger(path: Path) -> dict:
    """Parse `trigger-evals.md` into a validated `evalspec-trigger/v1` doc."""
    fm, body_lines = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    _check_fm_keys(fm, _TRIG_FM, path)
    if "skill_name" not in fm:
        raise MdFormatError(f"{path}: frontmatter missing `skill_name`")
    doc: dict = {"$schema": "evalspec-trigger/v1", "skill_name": fm["skill_name"], "queries": []}
    for level, title, content in _sections(body_lines, path):
        if level == 2 and title == "Description":
            doc["description"] = _prose(content)
        elif level == 2 and title in _TRIGGER_TITLES:
            doc["queries"].extend(_parse_trigger_section(content, _TRIGGER_TITLES[title], path))
        else:
            raise MdFormatError(f"{path}: unexpected `{'#' * level} {title}`")
    try:
        schema._validate(doc)
    except schema.SchemaError as e:
        raise MdFormatError(str(e)) from e
    return doc


def load_suite_dir(evals_dir: Path) -> dict:
    """Assemble one skill's `evals/<slug>/prompt.md` dirs into a validated
    evalspec/v1 doc. No suite file."""
    evals = []
    for slug_dir in sorted(p for p in evals_dir.iterdir() if p.is_dir()):
        if slug_dir.name.startswith(".") or slug_dir.name.startswith("__"):
            continue  # hidden/tooling dirs (.git, .pytest_cache, __pycache__) are not evals
        prompt_md = slug_dir / "prompt.md"
        if not prompt_md.is_file():
            # A slug dir with no prompt.md is a half-authored or misnamed eval — fail loud rather than collect zero cases.
            raise schema.SchemaError(f"{slug_dir} has no prompt.md")
        evals.append(parse_eval_md(prompt_md))
    doc = {"$schema": "evalspec/v1", "evals": evals}
    schema._validate(doc)
    return doc
