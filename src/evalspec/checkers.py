"""Deterministic assertion checkers: zero-variance grading for mechanically.

checkable facts, run on the host against the workdir before the judge.

Each checker takes a schema-validated spec (see `schema._CHECKER_FIELDS`), the host
workdir, and the pre-run SHA map, and returns a grading entry shaped exactly like a
judge-graded one plus `"type": "deterministic"`. Checkers see the FINAL workspace state;
{TODAY} is substituted upstream and a leading `./` anchor is stripped here (checker
paths are workdir-relative on the host).
"""

from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GradeContext:
    """Process facts a deterministic checker needs beyond the workdir.

    Inert until a runner.     wires fired_skills; absent context grades skill_invoked
    False, never crashes.
    """

    fired_skills: tuple[str, ...] = ()


def assertion_text(assertion: object) -> str:
    """Display text for any assertion shape: plain string, typed object with text,.

    or a checker spec whose text derives from its fields.
    """
    if isinstance(assertion, str):
        return assertion
    return assertion.get("text") or derive_text(assertion)


def derive_text(spec: dict) -> str:
    """Return checker text from an assertion or explicit checker field."""
    checker = spec["checker"]
    if checker == "file_exists":
        verb = "exists" if spec.get("should_exist", True) else "does not exist"
        return f"the file {spec['path']} {verb}"
    if checker == "glob_count":
        if "count" in spec:
            return f"exactly {spec['count']} file(s) match {spec['glob']}"
        return f"at least {spec['min']} file(s) match {spec['glob']}"
    if checker == "sha256_match":
        if "original" in spec:
            return f"{spec['path']} is byte-identical to the pre-run {spec['original']}"
        return f"{spec['path']} has SHA-256 {spec['sha256'][:12]}…"
    if checker == "frontmatter_has":
        tail = f" = {spec['value']}" if "value" in spec else ""
        return f"{spec['path']} frontmatter has {spec['key']}{tail}"
    if checker == "skill_invoked":
        return f"Skill `{spec['skill']}` invoked"
    return f"{spec['path']} content matches /{spec['pattern']}/"


def _strip_anchor(raw: str) -> str:
    """Strip a leading `./` workdir anchor (and any bare leading `/`) so a path.

    written `./9. Archive/x` resolves workdir-relative to `9. Archive/x`.
    """
    return raw.removeprefix("./").lstrip("/")


def _resolve(raw: str, workdir: Path) -> Path:
    """Resolve a checker path relative to the clean-room root."""
    path = (workdir / _strip_anchor(raw)).resolve()
    if not path.is_relative_to(workdir.resolve()):
        raise ValueError(f"checker path escapes the workdir: {raw!r}")
    return path


def _read_text(path: Path) -> str:
    """Read an agent-written output file leniently: a stray BOM or non-UTF-8 byte.

    must produce a graded failure, never crash the run. Decode replacing bad bytes and
    drop a leading BOM.
    """
    return path.read_bytes().decode("utf-8", "replace").lstrip("\ufeff")


def _frontmatter(path: Path) -> dict | None:
    """Parse YAML frontmatter from Markdown content."""
    lines = _read_text(path).split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            data = yaml.safe_load("\n".join(lines[1:i]))
            return data if isinstance(data, dict) else None
    return None


def _file_exists(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate a file-exists checker against the clean-room root."""
    # exists(), not is_file(): file_exists verifies a path is present or absent
    # regardless of type, so "the folder X was created / no longer exists" is checkable.
    exists = _resolve(spec["path"], workdir).exists()
    want = spec.get("should_exist", True)
    return exists == want, f"{spec['path']} {'exists' if exists else 'absent'}"


def _glob_count(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate a glob-count checker against the clean-room root."""
    # Same workdir boundary the path checkers enforce via _resolve: reject a `../`-bearing
    # or absolute glob up front with a clear error rather than silently returning zero matches.
    raw = spec["glob"]
    pat = _strip_anchor(raw)
    # absolute check uses the raw glob — _strip_anchor lstrips the leading "/"
    if raw.startswith("/") or ".." in Path(pat).parts:
        raise ValueError(f"checker glob escapes the workdir: {raw!r}")
    root = workdir.resolve()
    n = sum(1 for m in workdir.glob(pat) if m.is_file() and m.resolve().is_relative_to(root))
    if "count" in spec:
        return n == spec["count"], f"{n} match(es), expected exactly {spec['count']}"
    return n >= spec["min"], f"{n} match(es), expected at least {spec['min']}"


def _sha256_match(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate a SHA-256 checker against the clean-room root."""
    path = _resolve(spec["path"], workdir)
    if not path.is_file():
        return False, f"{spec['path']} absent"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if "original" in spec:
        expected = original_shas.get(_strip_anchor(spec["original"]))
        if expected is None:
            return False, f"no pre-run SHA recorded for {spec['original']!r}"
    else:
        expected = spec["sha256"]
    return actual == expected, f"sha256 {actual[:12]}… vs expected {expected[:12]}…"


def _yaml_scalar(text: str) -> object:
    """Coerce an author's string expectation to the Python type YAML would produce.

    for the same literal, so a `value:` matches a typed frontmatter value
    (bool/int/float/date). The frontmatter side is yaml.safe_loaded too, so both sides
    normalize the same way and compare by value, not by repr. Non-scalar or empty
    results (None, list, dict) fall back to the raw string.
    """
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    scalars = (bool, int, float, str, datetime.date)  # date covers datetime subclass
    return value if isinstance(value, scalars) else text


def _frontmatter_has(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate a frontmatter key/value checker."""
    path = _resolve(spec["path"], workdir)
    if not path.is_file():
        return False, f"{spec['path']} absent"
    try:
        fm = _frontmatter(path)
    except yaml.YAMLError as e:
        return False, f"frontmatter is not valid YAML: {e}"
    if fm is None:
        return False, "no frontmatter block"
    key = spec["key"]
    if key not in fm:
        return False, f"key `{key}` missing (has: {sorted(map(str, fm))})"
    if "value" in spec and fm[key] != _yaml_scalar(spec["value"]):
        return False, f"`{key}` = {fm[key]!r}, expected {spec['value']!r}"
    return True, f"`{key}` present" + (f" = {spec['value']!r}" if "value" in spec else "")


def _regex(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate a regex checker against file content."""
    path = _resolve(spec["path"], workdir)
    if not path.is_file():
        return False, f"{spec['path']} absent"
    match = re.search(spec["pattern"], _read_text(path), re.MULTILINE)
    if match:
        return True, f"matched: {match.group(0)[:80]!r}"
    return False, f"no match for /{spec['pattern']}/"


def _skill_invoked(
    spec: dict, workdir: Path, original_shas: dict, context: object = None
) -> tuple[bool, str]:
    """Evaluate whether trajectory facts show a skill invocation."""
    # Exact-or-namespaced match: a skill may fire as `ingest` or `plugin:ingest`.
    target = spec["skill"]
    fired = context.fired_skills if context else ()
    hit = any(s == target or s.endswith(f":{target}") for s in fired)
    return hit, (f"`{target}` invoked" if hit else f"`{target}` not among fired {list(fired)}")


_CHECKERS = {
    "file_exists": _file_exists,
    "glob_count": _glob_count,
    "sha256_match": _sha256_match,
    "frontmatter_has": _frontmatter_has,
    "regex": _regex,
    "skill_invoked": _skill_invoked,
}


def run_assertion(
    spec: dict,
    workdir: Path,
    original_shas: dict,
    *,
    context: GradeContext | None = None,
) -> dict:
    """Grade one deterministic assertion.

    Returns a grading entry interchangeable.     with a judge-graded one, evidence
    prefixed so artifacts show it never saw a judge.
    """
    passed, evidence = _CHECKERS[spec["checker"]](spec, workdir, original_shas, context)
    return {
        "text": assertion_text(spec),
        "passed": passed,
        "evidence": f"CHECK {spec['checker']}: {evidence}",
        "type": "deterministic",
    }
