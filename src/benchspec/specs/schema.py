"""Strict schema validator for skill eval files.

Dispatches on the top-level `$schema` field and raises `SchemaError` on any
deviation. No unknown fields permitted. Used at pytest collection (see
`discovery._validate`, which names the offending file).

The only supported schema is `benchspec/v1` for output evals.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeVar

_EVALS_V1 = "benchspec/v1"

FieldT = TypeVar("FieldT")

_ID_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# checker -> (required keys, optional keys). Key types live in _CHECKER_KEY_TYPES.
# Negation is a distinct checker name (`not_file_exists`, `not_skill_invoked`) sharing its
# positive counterpart's fields, not a polarity flag — so a spec reads unambiguously and
# the binder picks an explicit primitive. Only the two currently-useful negatives exist;
# more (`regex_absent`, `frontmatter_missing`, …) get added the same way as evals need them.
_CHECKER_FIELDS = {
    "file_exists": ({"path"}, set()),
    "not_file_exists": ({"path"}, set()),
    "glob_count": ({"glob"}, {"count", "min"}),
    "sha256_match": ({"path"}, {"original", "sha256"}),
    "frontmatter_has": ({"path", "key"}, {"value"}),
    "regex": ({"path", "pattern"}, set()),
    "skill_invoked": ({"skill"}, set()),
    "not_skill_invoked": ({"skill"}, set()),
}

_CHECKER_KEY_TYPES = {
    "path": str,
    "glob": str,
    "original": str,
    "sha256": str,
    "key": str,
    "value": str,
    "pattern": str,
    "skill": str,
    "count": int,
    "min": int,
}

_KEBAB_HINT = "lowercase alphanumerics separated by hyphens, e.g. `happy-path`"


def is_kebab(value: str) -> bool:
    """Return whether a string is a kebab-case identifier."""
    return bool(_ID_KEBAB.match(value))


class SchemaError(ValueError):
    """Signal schema failures."""

    pass


def _require(
    obj: dict,
    key: str,
    expected_type: type[FieldT],
    path: str,
    example: str | None = None,
) -> FieldT:
    """Return a required key after validating its type."""
    if key not in obj:
        hint = f" — e.g. {example}" if example else ""
        raise SchemaError(f"{path}: missing required field `{key}`{hint}")
    return _checked(obj[key], key, expected_type, path)


def _optional(obj: dict, key: str, expected_type: type[FieldT], path: str) -> FieldT | None:
    """Return an optional key after validating its type when present."""
    if key not in obj:
        return None
    return _checked(obj[key], key, expected_type, path)


def _checked(val: object, key: str, expected_type: type[FieldT], path: str) -> FieldT:
    """Return `val` once it is an `expected_type`, else raise naming the field."""
    if not isinstance(val, expected_type):
        raise SchemaError(
            f"{path}.{key}: expected {expected_type.__name__}, got {type(val).__name__}"
        )
    return val


def _reject_extra_keys(obj: dict, allowed: set[str], path: str) -> None:
    """Reject schema objects containing undeclared keys."""
    extra = set(obj.keys()) - allowed
    if extra:
        raise SchemaError(f"{path}: unknown field(s) {sorted(extra)} (allowed: {sorted(allowed)})")


def _check_key_type(item: dict, key: str, path: str) -> None:
    """Validate one checker field against its expected type."""
    val = item[key]
    expected = _CHECKER_KEY_TYPES[key]
    # bool subclasses int: True as a count must not validate.
    if (expected is int and isinstance(val, bool)) or not isinstance(val, expected):
        raise SchemaError(f"{path}.{key}: expected {expected.__name__}, got {type(val).__name__}")


def _validate_checker_obj(item: dict, path: str) -> None:
    """Validate one explicit checker assertion object."""
    checker = _require(item, "checker", str, path)
    if checker not in _CHECKER_FIELDS:
        raise SchemaError(
            f"{path}.checker: `{checker}` is not a known checker (valid: {sorted(_CHECKER_FIELDS)})"
        )
    required, optional = _CHECKER_FIELDS[checker]
    _reject_extra_keys(item, {"type", "checker", "text"} | required | optional, path)
    for key in required:
        if key not in item:
            raise SchemaError(f"{path}: checker `{checker}` requires `{key}`")
    for key in (required | optional) & set(item):
        _check_key_type(item, key, path)
    if checker == "glob_count" and ("count" in item) == ("min" in item):
        raise SchemaError(f"{path}: glob_count takes exactly one of `count` / `min`")
    if checker == "sha256_match":
        if ("original" in item) == ("sha256" in item):
            raise SchemaError(f"{path}: sha256_match takes exactly one of `original` / `sha256`")
        if "sha256" in item and not _SHA256_HEX.match(item["sha256"]):
            raise SchemaError(f"{path}.sha256: must be 64 hex chars (lowercase)")
    if checker == "regex":
        try:
            re.compile(item["pattern"])
        except re.error as error:
            raise SchemaError(f"{path}.pattern: invalid regex: {error}") from error
    text = _optional(item, "text", str, path)
    if text is not None and not text.strip():
        raise SchemaError(f"{path}.text: must be non-empty")


def _validate_history(history: object, path: str) -> None:
    """Validate history conversation turns."""
    if not isinstance(history, list):
        raise SchemaError(f"{path}: expected list, got {type(history).__name__}")
    for index, turn in enumerate(history):
        turn_path = f"{path}[{index}]"
        if not isinstance(turn, dict):
            raise SchemaError(f"{turn_path}: expected object, got {type(turn).__name__}")
        _reject_extra_keys(turn, {"role", "content"}, turn_path)
        for key in ("role", "content"):
            value = _require(turn, key, str, turn_path)
            if not value.strip():
                raise SchemaError(f"{turn_path}.{key}: must be non-empty")


def _validate_history_path(history_path: str, eval_path: Path) -> list[str]:
    """Read and validate a JSONL history file contained by its eval folder."""
    if Path(history_path).suffix != ".jsonl":
        raise SchemaError(f"{eval_path}: history path {history_path!r} must name a `.jsonl` file")

    try:
        eval_folder = eval_path.parent.resolve()
        resolved_path = (eval_path.parent / history_path).resolve()
    except (OSError, RuntimeError) as error:
        raise SchemaError(
            f"{eval_path}: could not resolve history path {history_path!r}: {error}"
        ) from error
    if not resolved_path.is_relative_to(eval_folder):
        raise SchemaError(
            f"{eval_path}: history path {history_path!r} resolves outside the eval folder"
        )

    try:
        transcript = resolved_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SchemaError(
            f"{eval_path}: could not read history path {history_path!r}: {error}"
        ) from error

    lines: list[str] = []
    for line_number, line in enumerate(transcript.splitlines(), start=1):
        # Blank lines are skipped wherever they occur; non-blank line bytes stay untouched.
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as error:
            raise SchemaError(
                f"{eval_path}: history path {history_path!r}, line {line_number}: "
                f"invalid JSON: {error.msg}"
            ) from error
        lines.append(line)
    return lines


def _validate_evals_v1(data: dict) -> None:
    """Validate an benchspec/v1 output-eval document."""
    _reject_extra_keys(data, {"$schema", "evals"}, "root")
    evals = _require(
        data,
        "evals",
        list,
        "root",
        example='"evals": [{"id": "happy-path", "prompt": "…", "assertions": ["…"]}]',
    )
    if not evals:
        raise SchemaError("root.evals: must be non-empty")

    seen: set[str] = set()
    allowed_eval = {"id", "history", "prompt", "assertions"}
    for eval_index, item in enumerate(evals):
        path = f"root.evals[{eval_index}]"
        if not isinstance(item, dict):
            raise SchemaError(f"{path}: expected object, got {type(item).__name__}")
        _reject_extra_keys(item, allowed_eval, path)

        eval_id = _require(item, "id", str, path, example='"id": "happy-path"')
        if not _ID_KEBAB.match(eval_id):
            raise SchemaError(f"{path}.id: `{eval_id}` is not kebab-case ({_KEBAB_HINT})")
        if eval_id in seen:
            raise SchemaError(f"{path}.id: duplicate id `{eval_id}`")
        seen.add(eval_id)

        if "history" in item:
            _validate_history(item["history"], f"{path}.history")

        prompt = _require(
            item,
            "prompt",
            str,
            path,
            example='"prompt": "Use the `ingest` skill to add ./x.md."',
        )
        if not prompt.strip():
            raise SchemaError(f"{path}.prompt: must be non-empty")

        assertions = _require(
            item,
            "assertions",
            list,
            path,
            example='"assertions": ["a Resource page was created"]',
        )
        if not assertions:
            raise SchemaError(f"{path}.assertions: must be non-empty")
        for assertion_index, assertion in enumerate(assertions):
            if not isinstance(assertion, str):
                raise SchemaError(
                    f"{path}.assertions[{assertion_index}]: expected string (assertions are prose; "
                    "the binder derives deterministic checks)"
                )
            if not assertion.strip():
                raise SchemaError(f"{path}.assertions[{assertion_index}]: must be non-empty")


def _validate(data: dict) -> None:
    """Provide the validate helper."""
    schema = data.get("$schema")
    if schema == _EVALS_V1:
        _validate_evals_v1(data)
    else:
        raise SchemaError(
            f"root.$schema: expected `{_EVALS_V1}` (output evals), got {schema!r} — add "
            '`"$schema"` as the first key'
        )


def validate_path(path: Path) -> dict:
    """Load and validate an eval file, returning its parsed data.

    Raises SchemaError on any schema deviation; lets JSONDecodeError propagate. Used at
    pytest collection so a malformed eval file fails loudly and early.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: top-level must be an object")
    _validate(data)
    return data
