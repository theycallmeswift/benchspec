"""Discover skill eval files under a repo root and resolve the root itself.

Output evals come from Git's tracked and nonignored untracked files when `repo_root` is
inside a worktree, with a pruned filesystem walk as the non-Git fallback. Direct
`evals/<group>/eval.md` / `*.eval.md` files are keyed on the folder-derived
`(group, eval_id)` pair — one `EvalCase` per eval file. Trigger evals still resolve per
skill dir under `skills/` and `.claude/skills/` (`evals/trigger-evals.md`), one
`TriggerCase` per query. Each file is validated at discovery so a malformed eval fails
pytest collection loudly.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
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


def _is_pruned_dir(name: str) -> bool:
    """Return whether discovery must ignore a directory basename."""
    return name in _PRUNE_DIRS or name.startswith(".")


def _git_entries(repo_root: Path) -> list[Path] | None:
    """Return Git-tracked and nonignored untracked entries, or None outside Git."""
    git_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
    }
    try:
        worktree = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_root,
            env=git_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if worktree.returncode != 0 or worktree.stdout.strip() != b"true":
        return None

    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            env=git_env,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise schema.SchemaError(f"{repo_root}: git ls-files failed: {error}") from error
    if listed.returncode != 0:
        detail = os.fsdecode(listed.stderr).strip() or "unknown error"
        raise schema.SchemaError(f"{repo_root}: git ls-files failed: {detail}")

    relative_paths = set()
    for raw_path in listed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = Path(os.fsdecode(raw_path))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise schema.SchemaError(f"git ls-files returned out-of-root path: {relative_path}")
        relative_paths.add(relative_path)
    return [repo_root / relative_path for relative_path in sorted(relative_paths)]


def _filesystem_entries(repo_root: Path) -> list[Path]:
    """Return filesystem entries without following pruned or symlinked directories."""
    entries: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        current_dir = Path(dirpath)
        if current_dir != repo_root and (current_dir / ".git").exists():
            dirnames[:] = []
            continue
        kept_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current_dir / name
            if _is_pruned_dir(name):
                continue
            if name == "workspace" and current_dir.parent.name == "evals":
                continue
            if child.is_symlink():
                entries.append(child)
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        entries.extend(current_dir / name for name in sorted(filenames))
    return entries


def _inside_eval_workspace(relative_path: Path) -> bool:
    """Return whether a path sits below an `evals/<group>/workspace/` tree."""
    parts = relative_path.parts
    return any(
        part == "evals" and index + 2 < len(parts) and parts[index + 2] == "workspace"
        for index, part in enumerate(parts)
    )


def _is_eval_file_path(relative_path: Path) -> bool:
    """Return whether a path has the direct `evals/<group>/<eval file>` shape."""
    return (
        len(relative_path.parts) >= 3
        and relative_path.parts[-3] == "evals"
        and (
            relative_path.name == "eval.md" or relative_path.name.endswith(".eval.md")
        )
    )


def _reject_symlinked_layout(path: Path, relative_path: Path) -> None:
    """Reject symlinks that could hide or escape an eval group layout."""
    if not path.is_symlink():
        return
    if (
        relative_path.name == "evals"
        or (
            relative_path.parent.name == "evals"
            and (path.is_dir() or schema.is_kebab(relative_path.name))
        )
        or _is_eval_file_path(relative_path)
    ):
        raise schema.SchemaError(f"{path}: eval layouts may not use symlinks")


def _validate_candidate_path(repo_root: Path, eval_file: Path) -> None:
    """Require a real eval file under a non-symlinked group inside the repo root."""
    group_dir = eval_file.parent
    evals_dir = group_dir.parent
    if eval_file.is_symlink() or group_dir.is_symlink() or evals_dir.is_symlink():
        raise schema.SchemaError(f"{eval_file}: eval layouts may not use symlinks")
    try:
        group_dir.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except (OSError, ValueError) as error:
        message = f"{eval_file}: eval group resolves outside the repo root"
        raise schema.SchemaError(message) from error


def _eval_files(repo_root: Path) -> list[Path]:
    """Return Git-aware output-eval candidates, with a safe filesystem fallback."""
    entries = _git_entries(repo_root)
    if entries is None:
        entries = _filesystem_entries(repo_root)

    candidates: list[Path] = []
    for path in entries:
        try:
            relative_path = path.relative_to(repo_root)
        except ValueError as error:
            raise schema.SchemaError(f"{path}: repository entry is outside {repo_root}") from error
        if any(_is_pruned_dir(part) for part in relative_path.parts[:-1]):
            continue
        if _inside_eval_workspace(relative_path):
            continue
        if path.is_symlink() and _is_pruned_dir(relative_path.name):
            continue
        _reject_symlinked_layout(path, relative_path)
        if not _is_eval_file_path(relative_path) or not path.is_file():
            continue
        _validate_candidate_path(repo_root, path)
        candidates.append(path)
    return sorted(candidates)


def discover_eval_cases(repo_root: Path) -> list[EvalCase]:
    """Discover Markdown output evals from repository candidate files.

    Raises `schema.SchemaError` on a non-kebab `group`/`eval_id` or a duplicate
    `(group, eval_id)` pair, naming both source files.
    """
    seen: dict[tuple[str, str], Path] = {}
    cases: list[EvalCase] = []
    for eval_file in _eval_files(repo_root):
        group_dir = eval_file.parent
        group = group_dir.name
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
        cases.append(EvalCase(group=group, eval_dir=group_dir, eval_file=eval_file, eval=parsed))
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
