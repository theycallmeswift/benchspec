"""Discover skill eval files under a repo root and resolve the root itself.

For each `<name>/` under `<repo_root>/skills/` and `<repo_root>/.claude/skills/`, reads
the self-contained output evals (`evals/<slug>/prompt.md`) and `evals/trigger-evals.md`,
validating each at discovery so a malformed eval fails pytest collection loudly. Output
evals become `EvalCase`s (one per slug dir); trigger evals become `TriggerCase`s (one
per query).
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from evalspec import mdformat, schema


@dataclass
class EvalCase:
    """Represent EvalCase."""

    skill_dir: Path
    eval: dict  # one per-slug dict from load_suite_dir: {slug, prompt, assertions, seed?}

    @property
    def skill(self: object) -> str:
        """Handle skill."""
        # The skill-dir name — the suite identity used for artifact paths, test ids,
        # and `[tool.evalspec.skills.<name>]` override matching.
        return self.skill_dir.name

    @property
    def slug(self: object) -> str:
        """Handle slug."""
        return self.eval["slug"]

    @property
    def eval_id(self: object) -> str:
        """Handle eval_id."""
        return self.slug  # artifact paths read eval-<eval_id>/

    @property
    def param_id(self: object) -> str:
        """Handle param_id."""
        return f"{self.skill}-{self.slug}"

    @property
    def prompt(self: object) -> str:
        """Handle prompt."""
        return self.eval["prompt"]

    @property
    def assertions(self: object) -> list[str]:
        """Handle assertions."""
        return self.eval["assertions"]

    @property
    def seed(self: object) -> list[dict]:
        """Handle seed."""
        return self.eval.get("seed", [])

    @property
    def fixtures_dir(self: object) -> Path | None:
        """Handle fixtures_dir."""
        d = self.skill_dir / "evals" / self.slug / "fixtures"
        return d if d.is_dir() else None


@dataclass
class TriggerCase:
    """Represent TriggerCase."""

    skill_dir: Path
    repo_root: Path  # repo root staged for real routing (plugin + local skills)
    skill_name: str  # data["skill_name"] — the name detect_skill_fired matches
    query: dict

    @property
    def skill(self: object) -> str:
        """Handle skill."""
        return self.skill_dir.name  # directory name — for artifact paths and test ids

    @property
    def param_id(self: object) -> str:
        """Handle param_id."""
        return f"{self.skill}-{self.query['slug']}"


def resolve_repo_root(config: object) -> Path:
    """Project whose `skills/` tree is under test.

    Order: --evalspec-repo-root, then $PROJECT_ROOT, then the pytest rootdir.
    """
    raw = config.getoption("evalspec_repo_root") or os.environ.get("PROJECT_ROOT")
    if raw:
        return Path(raw).resolve()
    return Path(config.rootpath)


_DEFAULT_EVAL_ROOTS = ["skills", ".claude/skills"]


def pyproject_table(repo_root: Path) -> dict:
    """The raw `[tool.evalspec]` table from the repo's pyproject.toml ({} if absent)."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("evalspec", {})


def _pyproject_eval_roots(repo_root: Path) -> list[str] | None:
    """Handle _pyproject_eval_roots."""
    roots = pyproject_table(repo_root).get("eval_roots")
    if roots is None:
        return None
    if not (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        raise schema.SchemaError("[tool.evalspec] eval_roots must be a list of strings")
    return roots


def resolve_eval_roots(config: object) -> list[str]:
    """Where to look for eval-bearing skill dirs, in precedence order:.

    --evalspec-eval-roots (comma-separated CLI flag) > [tool.evalspec] eval_roots in
    pyproject.toml > built-in default (skills, .claude/skills). Lets external consumers
    point at a non-Claude layout without disturbing the host plugin's `make evals` flow.
    """
    raw = config.getoption("evalspec_eval_roots")
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    repo_root = resolve_repo_root(config)
    pyproject_roots = _pyproject_eval_roots(repo_root)
    if pyproject_roots is not None:
        return pyproject_roots
    return list(_DEFAULT_EVAL_ROOTS)


@dataclass(frozen=True)
class EnvConfig:
    """Host-declared sandbox environment from `[tool.evalspec]`.

    `script_path` is retained for error/display only and is NOT part of the cache
    identity — the digest hashes the script BYTES, so an in-place edit (same path, new
    contents) changes the snapshot name and forces a rebuild.
    """

    base_image: str | None = None
    script: bytes = b""
    script_path: str | None = None

    def __bool__(self: object) -> bool:
        """Handle __bool__."""
        return self.base_image is not None or bool(self.script)

    def digest(self: object) -> str:
        """Handle digest."""
        if not self:
            return ""
        payload = (self.base_image or "").encode() + b"\0" + self.script
        return hashlib.sha256(payload).hexdigest()[:8]


def resolve_environment_config(repo_root: Path) -> EnvConfig:
    """Read + validate the optional `base_image` / `environment_script` keys.

    Fails fast with `schema.SchemaError` at config-read time on a bad type, an empty
    string, or an unreadable `environment_script`. Absent keys ⇒ an empty config (falsy,
    empty digest), reproducing the default snapshot name and behavior.
    """
    table = pyproject_table(repo_root)

    base_image = table.get("base_image")
    if base_image is not None and not (isinstance(base_image, str) and base_image):
        raise schema.SchemaError("[tool.evalspec] base_image must be a non-empty string")

    rel = table.get("environment_script")
    script = b""
    script_path = None
    if rel is not None:
        if not (isinstance(rel, str) and rel):
            raise schema.SchemaError(
                "[tool.evalspec] environment_script must be a non-empty string"
            )
        try:
            script = (repo_root / rel).read_bytes()
        except OSError as err:
            raise schema.SchemaError(f"environment_script: file not found: {rel}") from err
        script_path = rel

    return EnvConfig(base_image=base_image, script=script, script_path=script_path)


def _skill_dirs(repo_root: Path, eval_roots: list[str] | None = None) -> list[Path]:
    """Skill dirs under each configured root, sorted by name.

    Raises if a name appears.
        under more than one root: test ids and workspace paths key on the bare directory name,
        so a duplicate would silently collide. Explicit roots, not a recursive glob — that
        would crawl skill-shaped scratch under `tmp/` and the `tests/` mirror. Defaults to the
        Claude-shaped roots so callers that pass no config keep working.
    """
    roots = eval_roots if eval_roots is not None else _DEFAULT_EVAL_ROOTS
    seen: dict[str, Path] = {}
    for rel in roots:
        root = repo_root / rel
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if not p.is_dir():
                continue
            if p.name in seen:
                raise schema.SchemaError(f"duplicate skill name {p.name!r}: {seen[p.name]} and {p}")
            seen[p.name] = p
    return sorted(seen.values(), key=lambda p: p.name)


def discover_eval_cases(repo_root: Path, eval_roots: list[str] | None = None) -> list[EvalCase]:
    """Handle discover_eval_cases."""
    cases: list[EvalCase] = []
    for skill_dir in _skill_dirs(repo_root, eval_roots):
        evals_dir = skill_dir / "evals"
        if not evals_dir.is_dir():
            continue
        # A flat `*.md` eval file is the retired legacy shape (only trigger-evals.md is a
        # reserved non-eval). A stray file beside slug dirs is a half-migrated suite whose
        # flat evals load_suite_dir would silently drop, so fail loudly regardless of how
        # many slug dirs the suite has.
        stray = sorted(
            p.name
            for p in evals_dir.iterdir()
            if p.is_file() and p.suffix == ".md" and p.name not in mdformat.NON_EVAL_MD
        )
        if stray:
            raise schema.SchemaError(
                f"{evals_dir}: legacy flat eval file(s) {stray} — explode each into its "
                f"own evals/<slug>/prompt.md dir or delete it."
            )
        has_slug = any(p.is_dir() and (p / "prompt.md").is_file() for p in evals_dir.iterdir())
        if not has_slug:
            continue  # trigger-only suite (just trigger-evals.md) — no output evals
        data = mdformat.load_suite_dir(evals_dir)
        for ev in data["evals"]:
            cases.append(EvalCase(skill_dir=skill_dir, eval=ev))
    return cases


def discover_trigger_cases(
    repo_root: Path, eval_roots: list[str] | None = None
) -> list[TriggerCase]:
    """Handle discover_trigger_cases."""
    cases: list[TriggerCase] = []
    for skill_dir in _skill_dirs(repo_root, eval_roots):
        evals_dir = skill_dir / "evals"
        f = evals_dir / "trigger-evals.md"
        if not f.is_file():
            continue
        data = mdformat.parse_trigger(f)
        for q in data.get("queries", []):
            cases.append(
                TriggerCase(
                    skill_dir=skill_dir,
                    repo_root=repo_root,
                    skill_name=data["skill_name"],
                    query=q,
                )
            )
    return cases
