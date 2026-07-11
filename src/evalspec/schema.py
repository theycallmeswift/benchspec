"""Strict schema validator for skill eval files.

Dispatches on the top-level `$schema` field and raises `SchemaError` on any
deviation. No unknown fields permitted. Used at pytest collection (see
`discovery._validate`, which names the offending file).

Supported schemas are `evalspec/v1` for output evals and `evalspec-trigger/v1` for
`trigger-evals.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_EVALS_V1 = "evalspec/v1"
_TRIGGER_V1 = "evalspec-trigger/v1"

_ID_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# checker -> (required keys, optional keys). Key types live in _CHECKER_KEY_TYPES.
_CHECKER_FIELDS = {
    "file_exists": ({"path"}, {"should_exist"}),
    "glob_count": ({"glob"}, {"count", "min"}),
    "sha256_match": ({"path"}, {"original", "sha256"}),
    "frontmatter_has": ({"path", "key"}, {"value"}),
    "regex": ({"path", "pattern"}, set()),
    "skill_invoked": ({"skill"}, {"expected"}),
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
    "should_exist": bool,
    "expected": bool,
}

_KEBAB_HINT = "lowercase alphanumerics separated by hyphens, e.g. `happy-path`"


def is_kebab(value: str) -> bool:
    """Return whether a string is a kebab-case identifier."""
    return bool(_ID_KEBAB.match(value))


class SchemaError(ValueError):
    """Signal schema failures."""

    pass


def _require(
    obj: object,
    key: str,
    expected_type: type | tuple[type, ...],
    path: str,
    example: str | None = None,
) -> object:
    """Return a required key after validating its type."""
    if key not in obj:
        hint = f" — e.g. {example}" if example else ""
        raise SchemaError(f"{path}: missing required field `{key}`{hint}")
    val = obj[key]
    if not isinstance(val, expected_type):
        expected_name = (
            expected_type.__name__
            if isinstance(expected_type, type)
            else " | ".join(candidate_type.__name__ for candidate_type in expected_type)
        )
        raise SchemaError(f"{path}.{key}: expected {expected_name}, got {type(val).__name__}")
    return val


def _optional(obj: object, key: str, expected_type: type | tuple[type, ...], path: str) -> object:
    """Return an optional key after validating its type when present."""
    if key not in obj:
        return None
    val = obj[key]
    if not isinstance(val, expected_type):
        expected_name = (
            expected_type.__name__
            if isinstance(expected_type, type)
            else " | ".join(candidate_type.__name__ for candidate_type in expected_type)
        )
        raise SchemaError(f"{path}.{key}: expected {expected_name}, got {type(val).__name__}")
    return val


def _reject_extra_keys(obj: dict, allowed: set[str], path: str) -> None:
    """Reject schema objects containing undeclared keys."""
    extra = set(obj.keys()) - allowed
    if extra:
        raise SchemaError(f"{path}: unknown field(s) {sorted(extra)} (allowed: {sorted(allowed)})")


def _validate_string_list(val: object, key_path: str) -> None:
    """Validate a non-empty list of non-empty strings."""
    if not isinstance(val, list):
        raise SchemaError(f"{key_path}: expected list, got {type(val).__name__}")
    if not val:
        raise SchemaError(f"{key_path}: must be non-empty")
    for item_index, item in enumerate(val):
        if not isinstance(item, str):
            raise SchemaError(
                f"{key_path}[{item_index}]: expected string, got {type(item).__name__}"
            )
        if not item.strip():
            raise SchemaError(f"{key_path}[{item_index}]: must be non-empty")


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


def _validate_evals_v1(data: dict) -> None:
    """Validate an evalspec/v1 output-eval document."""
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


def _validate_trigger_v1(data: dict) -> None:
    """Validate an evalspec-trigger/v1 routing document."""
    allowed_top = {"$schema", "description", "skill_name", "queries"}
    _reject_extra_keys(data, allowed_top, "root")

    _optional(data, "description", str, "root")
    skill_name = _require(data, "skill_name", str, "root", example='"skill_name": "my-skill"')
    if not _SKILL_NAME.match(skill_name):
        raise SchemaError(f"root.skill_name: `{skill_name}` is not kebab-case ({_KEBAB_HINT})")

    queries = _require(
        data,
        "queries",
        list,
        "root",
        example='"queries": [{"slug": "ingest-article", "query": "…", "should_trigger": true}]',
    )
    if not queries:
        raise SchemaError("root.queries: must be non-empty")

    seen_slugs: set[str] = set()
    allowed_query = {"slug", "description", "query", "should_trigger", "xfail"}
    valid_tiers = {"opus", "sonnet", "haiku"}
    for query_index, item in enumerate(queries):
        path = f"root.queries[{query_index}]"
        if not isinstance(item, dict):
            raise SchemaError(f"{path}: expected object, got {type(item).__name__}")
        _reject_extra_keys(item, allowed_query, path)

        slug = _require(item, "slug", str, path, example='"slug": "ingest-article"')
        if not _ID_KEBAB.match(slug):
            raise SchemaError(f"{path}.slug: `{slug}` is not kebab-case ({_KEBAB_HINT})")
        if slug in seen_slugs:
            raise SchemaError(f"{path}.slug: duplicate slug `{slug}`")
        seen_slugs.add(slug)

        _optional(item, "description", str, path)
        query = _require(item, "query", str, path, example='"query": "archive this meeting note"')
        if not query.strip():
            raise SchemaError(f"{path}.query: must be non-empty")

        _require(item, "should_trigger", bool, path, example='"should_trigger": true')

        # `xfail`, when present, records a known routing miss the suite expects on
        # SPECIFIC model tiers rather than gates on. `models` lists the tiers that
        # miss (e.g. a query that routes on opus but not sonnet); the runner attaches
        # a non-strict pytest xfail only when the run's model is one of them, so any
        # other tier runs strict and a regression there still fails. `should_trigger`
        # stays truthful (the query *should* route) — `xfail` changes the gate per
        # tier, not the truth.
        xfail = _optional(item, "xfail", dict, path)
        if xfail is not None:
            xfail_path = f"{path}.xfail"
            _reject_extra_keys(xfail, {"models", "reason"}, xfail_path)
            models = _require(
                xfail,
                "models",
                list,
                xfail_path,
                example='"models": ["sonnet", "haiku"]',
            )
            _validate_string_list(models, f"{xfail_path}.models")
            for tier in models:
                if tier not in valid_tiers:
                    raise SchemaError(f"{xfail_path}.models: `{tier}` not in {sorted(valid_tiers)}")
            reason = _require(
                xfail,
                "reason",
                str,
                xfail_path,
                example='"reason": "sonnet routing boundary; …"',
            )
            if not reason.strip():
                raise SchemaError(f"{xfail_path}.reason: must be non-empty")


def _validate(data: dict) -> None:
    """Provide the validate helper."""
    schema = data.get("$schema")
    if schema == _EVALS_V1:
        _validate_evals_v1(data)
    elif schema == _TRIGGER_V1:
        _validate_trigger_v1(data)
    else:
        raise SchemaError(
            f"root.$schema: expected `{_EVALS_V1}` (output evals) or "
            f"`{_TRIGGER_V1}` (trigger-evals.md), got {schema!r} — add "
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
