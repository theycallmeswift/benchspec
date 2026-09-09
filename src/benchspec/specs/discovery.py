"""Discover skill eval files under a repo root and resolve the root itself.

Output evals are found by walking a configured list of search paths (`eval_paths`,
default `skills`, `tests`, `evals`, `benchmarks`) under the repo root. Any `eval.md` /
`*.eval.md` file under a search path is an eval, keyed on the folder-derived
`(group, eval_id)` pair — one `EvalCase` per eval file. Each file is validated at
discovery so a malformed eval fails pytest collection loudly.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from benchspec.config.options import RunOptions, option_str
from benchspec.specs import mdformat, schema


@dataclass
class EvalCase:
    """One discovered Markdown output eval, keyed on (group, eval_id)."""

    group: str  # the evals/<group>/ folder name; also the artifact-path slot
    eval_dir: Path  # evals/<group>/ — holds the eval file(s), workspace/, setup.sh
    eval_file: Path  # eval.md or <stem>.eval.md
    eval: dict  # {id, prompt, assertions, history?} from parse_eval_md

    @property
    def skill(self) -> str:
        """The group name — kept in the `<skill>` artifact-path slot this phase."""
        return self.group

    @property
    def eval_id(self) -> str:
        """Return the stable eval identifier used in reports and artifact paths."""
        return self.eval["id"]

    @property
    def param_id(self) -> str:
        """Return the pytest parameter id for this case."""
        return f"{self.group}-{self.eval_id}"

    @property
    def prompt(self) -> str:
        """Return the prompt text for this discovered case."""
        return self.eval["prompt"]

    @property
    def assertions(self) -> list[str]:
        """Return assertion text for this discovered case."""
        return self.eval["assertions"]

    @property
    def history(self) -> list[dict]:
        """Return prior-context turns for this discovered case."""
        return self.eval.get("history", [])

    @property
    def workspace_dir(self) -> Path | None:
        """Return the per-eval workspace directory when present."""
        workspace_dir = self.eval_dir / "workspace"
        return workspace_dir if workspace_dir.is_dir() else None


def resolve_repo_root(config: RunOptions) -> Path:
    """The repo root that `eval_paths` discovery and project-relative paths resolve against.

    Order: --benchspec-repo-root, then $PROJECT_ROOT, then the pytest rootdir.
    """
    raw = option_str(config, "benchspec_repo_root") or os.environ.get("PROJECT_ROOT")
    if raw:
        return Path(raw).resolve()
    return Path(config.rootpath)


_DEFAULT_EVAL_PATHS = ["skills", "tests", "evals", "benchmarks"]

_PRUNE_DIRS = frozenset({"tmp", ".git", "__pycache__"})


def pyproject_table(repo_root: Path) -> dict:
    """The raw `[tool.benchspec]` table from the repo's pyproject.toml ({} if absent)."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    with pyproject.open("rb") as pyproject_file:
        data = tomllib.load(pyproject_file)
    return data.get("tool", {}).get("benchspec", {})


def _pyproject_eval_paths(repo_root: Path) -> list[str] | None:
    """Read output-eval search paths from pyproject configuration."""
    paths = pyproject_table(repo_root).get("eval_paths")
    if paths is None:
        return None
    if not (isinstance(paths, list) and all(isinstance(path, str) for path in paths)):
        raise schema.SchemaError("[tool.benchspec] eval_paths must be a list of strings")
    return paths


def resolve_eval_paths(config: RunOptions) -> list[str]:
    """Where to search for output evals, in precedence order.

    --benchspec-eval-paths (comma-separated CLI flag) > [tool.benchspec] eval_paths in
    pyproject.toml > built-in default (skills, tests, evals, benchmarks). Each path is
    a repo-root-relative directory whose tree is walked for `eval.md` / `*.eval.md`.
    """
    raw = option_str(config, "benchspec_eval_paths")
    if raw:
        return [path.strip() for path in raw.split(",") if path.strip()]
    repo_root = resolve_repo_root(config)
    pyproject_paths = _pyproject_eval_paths(repo_root)
    if pyproject_paths is not None:
        return pyproject_paths
    return list(_DEFAULT_EVAL_PATHS)


@dataclass(frozen=True)
class EnvConfig:
    """Host-declared sandbox environment from `[tool.benchspec]`.

    `script_path` is retained for error/display only and is NOT part of the cache
    identity — the digest hashes the script BYTES, so an in-place edit (same path, new
    contents) changes the snapshot name and forces a rebuild.
    """

    base_image: str | None = None
    script: bytes = b""
    script_path: str | None = None

    def __bool__(self) -> bool:
        """Return whether the preflight result contains an error."""
        return self.base_image is not None or bool(self.script)

    def digest(self) -> str:
        """Return a stable digest for environment snapshot caching."""
        if self.base_image is None and not self.script:
            return ""
        payload = (self.base_image or "").encode() + b"\0" + self.script
        return hashlib.sha256(payload).hexdigest()[:8]


def resolve_environment_config(repo_root: Path) -> EnvConfig:
    """Read + validate the optional `base_image` / `environment_script` keys.

    `BENCHSPEC_BASE_IMAGE` in the host environment wins over `[tool.benchspec]
    base_image`, the same precedence the harness version pins use, so a host that needs
    its own base (a proxy CA baked in, say) can select it without editing the config.

    Fails fast with `schema.SchemaError` at config-read time on a bad type, an empty
    string, or an unreadable `environment_script`. Absent keys ⇒ an empty config (falsy,
    empty digest), reproducing the default snapshot name and behavior.
    """
    table = pyproject_table(repo_root)

    base_image = os.environ.get("BENCHSPEC_BASE_IMAGE") or table.get("base_image")
    if base_image is not None and not (isinstance(base_image, str) and base_image):
        raise schema.SchemaError("[tool.benchspec] base_image must be a non-empty string")

    rel = table.get("environment_script")
    script = b""
    script_path = None
    if rel is not None:
        if not (isinstance(rel, str) and rel):
            raise schema.SchemaError(
                "[tool.benchspec] environment_script must be a non-empty string"
            )
        try:
            script = (repo_root / rel).read_bytes()
        except OSError as error:
            raise schema.SchemaError(f"environment_script: file not found: {rel}") from error
        script_path = rel

    return EnvConfig(base_image=base_image, script=script, script_path=script_path)


def _is_pruned_dir(name: str) -> bool:
    """Return whether discovery must ignore a directory basename."""
    return name in _PRUNE_DIRS or name.startswith(".")


def _is_eval_file(name: str) -> bool:
    """Return whether a filename marks an output eval."""
    return name == "eval.md" or name.endswith(".eval.md")


def _walk_eval_root(root: Path) -> list[Path]:
    """Collect eval files under one search path, not following symlinks or fixtures.

    The search path itself is walked verbatim (a dot-prefixed configured root like
    `.claude/skills` is honored); only its descendants are subject to the prune set. A
    `workspace/` beside an eval file is that eval's sandbox seed, never a nested eval
    root, so it is not descended into. An embedded Git repo is a discovery boundary.
    """
    eval_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        if current_dir != root and (current_dir / ".git").exists():
            dirnames[:] = []
            continue
        here = sorted(
            current_dir / name
            for name in filenames
            if _is_eval_file(name) and not (current_dir / name).is_symlink()
        )
        eval_files.extend(here)
        has_eval = bool(here)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not _is_pruned_dir(name)
            and not (current_dir / name).is_symlink()
            and not (has_eval and name == "workspace")
        ]
    return eval_files


def _eval_files(repo_root: Path, eval_paths: list[str]) -> list[Path]:
    """Return output-eval files under each configured search path, deduplicated."""
    seen: set[Path] = set()
    for relative_path in eval_paths:
        root = repo_root / relative_path
        if root.is_symlink() or not root.is_dir():
            continue
        seen.update(_walk_eval_root(root))
    return sorted(seen)


def discover_eval_cases(
    repo_root: Path, eval_paths: list[str] | None = None
) -> list[EvalCase]:
    """Discover Markdown output evals under the configured search paths.

    `eval_paths` are repo-root-relative directories to walk for `eval.md` / `*.eval.md`
    (default: skills, tests, evals, benchmarks; falls back to `[tool.benchspec]
    eval_paths` when not given). Raises `schema.SchemaError` on a non-kebab
    `group`/`eval_id` or a duplicate `(group, eval_id)` pair, naming both source files.
    """
    if eval_paths is None:
        pyproject_paths = _pyproject_eval_paths(repo_root)
        eval_paths = pyproject_paths if pyproject_paths is not None else _DEFAULT_EVAL_PATHS
    seen: dict[tuple[str, str], Path] = {}
    cases: list[EvalCase] = []
    for eval_file in _eval_files(repo_root, eval_paths):
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
