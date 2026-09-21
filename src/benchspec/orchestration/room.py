"""Clean-room construction and fact-gathering for honest skill-eval runs.

`seed_room` stages an eval's `workspace/` into a workdir outside the project, substitutes
`{TODAY}` in file contents and names, and SHA-snapshots the result; `gather_facts`
snapshots the workdir for the judge. The honesty contract these enforce: the workdir
holds only the eval's own workspace — no docs, no peers, no project state. The skill
install itself is a per-cell concern, handled by the eval's own `setup.sh` inside the
sandbox, so the arms differ there, not here.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
from pathlib import Path

from benchspec.orchestration.results import process_substitutions, substitute_prompt
from benchspec.specs.schema import SchemaError


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha(text: str) -> str:
    """Return the SHA-256 digest for text content."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def seed_room(workspace_dir: Path | None, workdir: Path, today: str | None = None) -> dict:
    """Create the clean-room workdir from the eval's workspace and record baseline facts."""
    workdir.mkdir(parents=True, exist_ok=True)
    if workspace_dir is None:
        return {}
    shutil.copytree(workspace_dir, workdir, dirs_exist_ok=True)
    if today is not None:
        # 1. Substitute {TODAY} in file contents first (before any path renames).
        for path in sorted(workdir.rglob("*")):
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                    if "{TODAY}" in text:
                        path.write_text(text.replace("{TODAY}", today), encoding="utf-8")
                except UnicodeDecodeError:
                    pass  # binary files are left untouched
        # 2. Rename paths (bottom-up so parent dirs are renamed after children).
        paths_to_rename = sorted(
            (path for path in workdir.rglob("*") if "{TODAY}" in path.name),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in paths_to_rename:
            new_name = path.parent / path.name.replace("{TODAY}", today)
            path.rename(new_name)

    shas: dict[str, str] = {}
    for path in sorted(workdir.rglob("*")):
        if path.is_file():
            shas[str(path.relative_to(workdir))] = _sha256(path)
    return shas


def render_history(history: list[dict | str] | None, today: str | None = None) -> str:
    """Render history turns or verbatim JSONL lines into an agent prompt prefix."""
    if not history:
        return ""
    lines = ["<transcript>"]
    verbatim = isinstance(history[0], str)
    for turn_index, turn in enumerate(history):
        if verbatim:
            if not isinstance(turn, str):
                raise SchemaError(
                    f"history[{turn_index}]: all entries must use the same representation"
                )
            lines.append(process_substitutions(turn, today))
            continue
        if not isinstance(turn, dict):
            if isinstance(turn, str):
                raise SchemaError(
                    f"history[{turn_index}]: all entries must use the same representation"
                )
            raise SchemaError(f"history[{turn_index}]: turn must be a mapping with role/content")
        for key in ("role", "content"):
            val = turn.get(key)
            if not isinstance(val, str) or not val.strip():
                raise SchemaError(f"history[{turn_index}]: missing or non-string `{key}`")
        content = substitute_prompt(turn["content"], today)
        lines.append(f"{turn['role']}: {content}")
    lines.append("</transcript>")
    return "\n".join(lines) + "\n\n"


# The judge's view of a workdir: the rendered tree, per-file contents, per-file SHA-256s.
Facts = tuple[str, dict[str, str], dict[str, str]]


def gather_facts(workdir: Path, max_bytes: int = 20000) -> Facts:
    """Collect clean-room file facts for grading evidence."""
    tree_lines: list[str] = []
    contents: dict[str, str] = {}
    shas: dict[str, str] = {}
    for path in sorted(workdir.rglob("*")):
        relative_path = str(path.relative_to(workdir))
        if path.is_dir():
            tree_lines.append(relative_path + "/")
            continue
        tree_lines.append(relative_path)
        shas[relative_path] = _sha256(path)
        try:
            data = path.read_text(encoding="utf-8")
            contents[relative_path] = (
                data if len(data) <= max_bytes else data[:max_bytes] + "\n<TRUNCATED>"
            )
        except UnicodeDecodeError:
            contents[relative_path] = "<binary>"
    return "\n".join(tree_lines), contents, shas


# Agents may write skill artifacts to fixed guest dirs rather than the workdir mount. Those
# dirs are internal to the guest, so artifact capture snapshots them in-guest and merges
# authored files into the workdir facts.

# Two-phase capture keeps snapshot cost off the hot path: hash every file cheaply each turn
# (a SHA line is ~80 bytes), diff against the staged baseline, stream the bytes of ONLY the
# changed/new files. When the agent authored nothing in its skills dir (the common case)
# that's zero content transfer — one `sha256sum` pass — so the snapshot doesn't add 296KB×N
# of stdout per turn and starve the concurrent agent sandboxes into timeouts.


def sha_snapshot_script(dirs: list[str]) -> str:
    """Build a shell script that prints SHA-256 facts for artifact dirs."""
    quoted = " ".join(shlex.quote(directory) for directory in dirs)
    return (
        f'for d in {quoted}; do [ -d "$d" ] && '
        'find -L "$d" -type f -exec sha256sum {} + 2>/dev/null; done; true'
    )


def parse_sha_stream(stdout: str) -> dict[str, str]:
    """Parse `sha_snapshot_script` stdout into {abs-path: sha}."""
    paths_by_sha: dict[str, str] = {}
    for line in stdout.splitlines():
        sha, sep, path = line.partition("  ")
        if sep and path:
            paths_by_sha[path] = sha
    return paths_by_sha


def changed_paths(baseline_shas: dict[str, str], current_shas: dict[str, str]) -> list[str]:
    """Return artifact paths whose SHA differs from the baseline."""
    return [path for path, sha in current_shas.items() if baseline_shas.get(path) != sha]


# Record-separator-framed header so file contents pass through verbatim. RS (0x1e) is
# control-only — it never appears in a skill's text — so a header can't collide with content.
_ARTIFACT_RE = re.compile("\x1e\x1eARTIFACT\x1e\x1e(.*?)\x1e\x1e\n")


def read_files_script(paths: list[str]) -> str:
    r"""Return POSIX shell that prints each path as an RS-framed record."""
    body = "".join(
        f"printf '\\036\\036ARTIFACT\\036\\036%s\\036\\036\\n' {shlex.quote(path)}; "
        f"cat {shlex.quote(path)} 2>/dev/null; "
        for path in paths
    )
    return body or "true"


def parse_artifact_stream(stdout: str) -> dict[str, str]:
    """Parse `read_files_script` stdout into {abs-path: content}."""
    parts = _ARTIFACT_RE.split(stdout)
    # parts = [pre, path1, content1, path2, content2, ...]; the pre-segment before the
    # first header is discarded.
    artifacts: dict[str, str] = {}
    it = iter(parts[1:])

    for path, content in zip(it, it, strict=False):
        artifacts[path] = content
    return artifacts


def to_display_paths(mapping: dict[str, str], guest_home: str) -> dict[str, str]:
    """Rewrite guest-home paths into display paths for judge evidence."""
    home = guest_home.rstrip("/")
    display_paths: dict[str, str] = {}
    for path, content in mapping.items():
        display_paths["~" + path[len(home) :] if path.startswith(home + "/") else path] = content
    return display_paths


def merge_facts(
    tree: str,
    contents: dict[str, str],
    shas: dict[str, str],
    extra: dict[str, str],
    max_bytes: int = 20000,
) -> Facts:
    """Append captured artifact facts to a `gather_facts` triple."""
    tree_lines = [ln for ln in tree.split("\n") if ln] if tree else []
    contents, shas = dict(contents), dict(shas)
    for path in sorted(extra):
        artifact_content = extra[path]
        tree_lines.append(path)
        shas[path] = _text_sha(artifact_content)
        contents[path] = (
            artifact_content
            if len(artifact_content) <= max_bytes
            else artifact_content[:max_bytes] + "\n<TRUNCATED>"
        )
    return "\n".join(tree_lines), contents, shas
