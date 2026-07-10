"""Discover skill eval files under a repo root and resolve the root itself.

Output evals are found by crawling every `evals/<group>/` folder under `repo_root`
(scratch/mirror subtrees pruned) for `eval.md` / `*.eval.md`, keyed on the folder-derived
`(group, eval_id)` pair — one `EvalCase` per eval file. Trigger evals still resolve per
skill dir under `skills/` and `.claude/skills/` (`evals/trigger-evals.md`), one
`TriggerCase` per query. Each file is validated at discovery so a malformed eval fails
pytest collection loudly.
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
    """One discovered Markdown output eval, keyed on (group, eval_id)."""

    group: str  # the evals/<group>/ folder name — the artifact-path slot Phase 3 keeps
    eval_dir: Path  # evals/<group>/ — holds the eval file(s), workspace/, setup.sh
    eval_file: Path  # eval.md or <stem>.eval.md
    eval: dict  # {id, prompt, assertions, history?} from parse_eval_md

    @property
    def skill(self: object) -> str:
        """The group name — kept in the `<skill>` artifact-path slot this phase."""
        return self.group

    @property
    def eval_id(self: object) -> str:
        """Return the stable eval identifier used in reports and artifact paths."""
        return self.eval["id"]

    @property
    def param_id(self: object) -> str:
        """Return the pytest parameter id for this case."""
        return f"{self.group}-{self.eval_id}"

    @property
    def prompt(self: object) -> str:
        """Return the prompt text for this discovered case."""
        return self.eval["prompt"]

    @property
    def assertions(self: object) -> list[str]:
        """Return assertion text for this discovered case."""
        return self.eval["assertions"]

    @property
    def history(self: object) -> list[dict]:
        """Return prior-context turns for this discovered case."""
        return self.eval.get("history", [])

    @property
    def workspace_dir(self: object) -> Path | None:
        """Return the per-eval workspace directory when present."""
        workspace_dir = self.eval_dir / "workspace"
        return workspace_dir if workspace_dir.is_dir() else None


@dataclass
class TriggerCase:
    """Store trigger case data."""

    skill_dir: Path
    repo_root: Path  # repo root staged for real routing (plugin + local skills)
    skill_name: str  # data["skill_name"] — the name detect_skill_fired matches
    query: dict

    @property
    def skill(self: object) -> str:
        """Return the skill name for this discovered case."""
        return self.skill_dir.name  # directory name — for artifact paths and test ids

    @property
    def param_id(self: object) -> str:
        """Return the pytest parameter id for this case."""
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

_PRUNE_DIRS = frozenset({"tmp", ".git", "__pycache__"})


def pyproject_table(repo_root: Path) -> dict:
    """The raw `[tool.evalspec]` table from the repo's pyproject.toml ({} if absent)."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    with pyproject.open("rb") as pyproject_file:
        data = tomllib.load(pyproject_file)
    return data.get("tool", {}).get("evalspec", {})


def _pyproject_eval_roots(repo_root: Path) -> list[str] | None:
    """Read eval root paths from pyproject configuration."""
    roots = pyproject_table(repo_root).get("eval_roots")
    if roots is None:
        return None
    if not (isinstance(roots, list) and all(isinstance(root, str) for root in roots)):
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
        return [root.strip() for root in raw.split(",") if root.strip()]
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
        """Return whether the preflight result contains an error."""
        return self.base_image is not None or bool(self.script)

    def digest(self: object) -> str:
        """Return a stable digest for environment snapshot caching."""
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
        except OSError as error:
            raise schema.SchemaError(f"environment_script: file not found: {rel}") from error
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
    for relative_root in roots:
        root = repo_root / relative_root
        if not root.is_dir():
            continue
        for skill_path in root.iterdir():
            if not skill_path.is_dir():
                continue
            if skill_path.name in seen:
                raise schema.SchemaError(
                    f"duplicate skill name {skill_path.name!r}: "
                    f"{seen[skill_path.name]} and {skill_path}"
                )
            seen[skill_path.name] = skill_path
    return sorted(seen.values(), key=lambda skill_path: skill_path.name)


def _evals_dirs(repo_root: Path) -> list[Path]:
    """Every directory named `evals` under `repo_root`, scratch/mirror subtrees pruned.

    Prunes `tmp/` (the artifact root), `.git`, `__pycache__`, and every dot-prefixed
    dir before descending — the fixed deny-list that lets the crawl replace the old
    explicit `eval_roots`.
    """
    found: list[Path] = []
    for dirpath, dirnames, _files in os.walk(repo_root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _PRUNE_DIRS and not name.startswith(".")
        )
        if Path(dirpath).name == "evals":
            found.append(Path(dirpath))
    return sorted(found)


def _eval_files(group_dir: Path) -> list[Path]:
    """The `eval.md` and sorted `*.eval.md` files directly under one group dir."""
    files: list[Path] = []
    canonical = group_dir / "eval.md"
    if canonical.is_file():
        files.append(canonical)
    files.extend(
        sorted(
            path
            for path in group_dir.iterdir()
            if path.is_file() and path.name.endswith(".eval.md")
        )
    )
    return files


def discover_eval_cases(repo_root: Path) -> list[EvalCase]:
    """Discover Markdown output evals by crawling every `evals/<group>/` folder.

    Raises `schema.SchemaError` on a non-kebab `group`/`eval_id` or a duplicate
    `(group, eval_id)` pair, naming both source files.
    """
    seen: dict[tuple[str, str], Path] = {}
    cases: list[EvalCase] = []
    for evals_dir in _evals_dirs(repo_root):
        for group_dir in sorted(path for path in evals_dir.iterdir() if path.is_dir()):
            group = group_dir.name
            if group.startswith(".") or group.startswith("__"):
                continue
            for eval_file in _eval_files(group_dir):
                parsed = mdformat.parse_eval_md(eval_file)
                eval_id = parsed["id"]
                if not schema.is_kebab(group):
                    raise schema.SchemaError(
                        f"{eval_file}: group `{group}` is not kebab-case ({schema._KEBAB_HINT})"
                    )
                if not schema.is_kebab(eval_id):
                    raise schema.SchemaError(
                        f"{eval_file}: eval id `{eval_id}` is not kebab-case "
                        f"({schema._KEBAB_HINT})"
                    )
                key = (group, eval_id)
                if key in seen:
                    raise schema.SchemaError(
                        f"duplicate eval {group}/{eval_id}: {seen[key]} and {eval_file}"
                    )
                seen[key] = eval_file
                cases.append(
                    EvalCase(group=group, eval_dir=group_dir, eval_file=eval_file, eval=parsed)
                )
    return sorted(cases, key=lambda case: (case.group, case.eval_id))


def discover_trigger_cases(
    repo_root: Path, eval_roots: list[str] | None = None
) -> list[TriggerCase]:
    """Discover trigger-routing cases from configured roots."""
    cases: list[TriggerCase] = []
    for skill_dir in _skill_dirs(repo_root, eval_roots):
        evals_dir = skill_dir / "evals"
        trigger_file = evals_dir / "trigger-evals.md"
        if not trigger_file.is_file():
            continue
        data = mdformat.parse_trigger(trigger_file)
        for query in data.get("queries", []):
            cases.append(
                TriggerCase(
                    skill_dir=skill_dir,
                    repo_root=repo_root,
                    skill_name=data["skill_name"],
                    query=query,
                )
            )
    return cases
