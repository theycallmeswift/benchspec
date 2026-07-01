"""Clean-room construction and fact-gathering for honest skill-eval runs.

`seed_room` stages an eval's fixture into a workdir outside the project, substitutes
`{TODAY}` in file contents and names, and SHA-snapshots the result; `gather_facts`
snapshots the workdir for the judge. The honesty contract these enforce: the workdir
holds only the eval's own fixture — no docs, no peers, no project state. The skill
install itself is a per-cell concern, handled by `setup.sh` inside the sandbox, so the
arms differ there, not here.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
from pathlib import Path

from evalspec.runner import substitute_prompt
from evalspec.schema import SchemaError


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def seed_room(fixture_dir: Path | None, workdir: Path, today: str | None = None) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    if fixture_dir is None:
        return {}
    shutil.copytree(fixture_dir, workdir, dirs_exist_ok=True)
    if today is not None:
        # 1. Substitute {TODAY} in file contents first (before any path renames).
        for p in sorted(workdir.rglob("*")):
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8")
                    if "{TODAY}" in text:
                        p.write_text(text.replace("{TODAY}", today), encoding="utf-8")
                except UnicodeDecodeError:
                    pass  # binary files are left untouched
        # 2. Rename paths (bottom-up so parent dirs are renamed after children).
        paths_to_rename = sorted(
            (p for p in workdir.rglob("*") if "{TODAY}" in p.name),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for p in paths_to_rename:
            new_name = p.parent / p.name.replace("{TODAY}", today)
            p.rename(new_name)
    shas: dict[str, str] = {}
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            shas[str(p.relative_to(workdir))] = _sha256(p)
    return shas


def render_seed(seed: list[dict] | None, today: str | None = None) -> str:
    """Render a `seed:` turn list into a transcript block prepended to the graded prompt, so it works on any agent without session injection."""
    if not seed:
        return ""
    lines = ["<transcript>"]
    for i, turn in enumerate(seed):
        if not isinstance(turn, dict):
            raise SchemaError(f"seed[{i}]: turn must be a mapping with role/text")
        for key in ("role", "text"):
            val = turn.get(key)
            if not isinstance(val, str) or not val.strip():
                raise SchemaError(f"seed[{i}]: missing or non-string `{key}`")
        text = substitute_prompt(turn["text"], today)
        lines.append(f"{turn['role']}: {text}")
    lines.append("</transcript>")
    return "\n".join(lines) + "\n\n"


def gather_facts(workdir: Path, max_bytes: int = 20000):
    tree_lines, contents, shas = [], {}, {}
    for p in sorted(workdir.rglob("*")):
        rel = str(p.relative_to(workdir))
        if p.is_dir():
            tree_lines.append(rel + "/")
            continue
        tree_lines.append(rel)
        shas[rel] = _sha256(p)
        try:
            data = p.read_text(encoding="utf-8")
            contents[rel] = data if len(data) <= max_bytes else data[:max_bytes] + "\n<TRUNCATED>"
        except UnicodeDecodeError:
            contents[rel] = "<binary>"
    return "\n".join(tree_lines), contents, shas


# --- Artifact capture outside the workdir mount ---------------------------------
#
# Some agents write skill artifacts to a fixed guest dir (e.g. claude → ~/.claude/skills,
# opencode → ~/.config/opencode/skills) rather than the workdir mount. That dir is internal
# to the microVM — no host bind-mount — so `gather_facts` can't see it, and any skill the
# agent authors there is invisible to the judge. The agent declares the dirs
# (`CodingAgent.artifact_dirs`); the session snapshots them in-guest and merges the
# AGENT-AUTHORED files (diffed against the staged baseline) into the workdir facts.

# Two-phase capture keeps snapshot cost off the hot path: hash every file cheaply each turn
# (a sha line is ~80 bytes), diff against the staged baseline, stream the bytes of ONLY the
# changed/new files. When the agent authored nothing in its skills dir (the common case)
# that's zero content transfer — one `sha256sum` pass — so the snapshot doesn't add 296KB×N
# of stdout per turn and starve the concurrent agent VMs into timeouts.


def sha_snapshot_script(dirs: list[str]) -> str:
    """POSIX-sh that prints `sha256sum` lines (`<hex>  <abs-path>`) for every file under
    `dirs`. Missing dirs are skipped. `parse_sha_stream` reverses this.

    `find -L` follows symlinks: the agents bridge their load dir (e.g. ~/.claude/skills)
    to a symlink at provision (bridge_skills_home_script), and a bare `find <symlink>`
    start point does NOT descend through the link — it would yield zero files, silently
    hiding every agent-authored skill from the judge. `-L` walks the link's target while
    still reporting the symlink-prefixed path, so `parse_sha_stream`/`to_display_paths`
    keep relabeling it `~/...`. The `[ -d "$d" ]` guard is `-L`-symmetric (it follows
    links too), so a symlinked-but-present dir still passes.

    Terminates with `; done; true` so the script's exit status can't ride on the loop's
    last iteration: a `for` loop exits with its final iteration's status, so a missing
    *last* dir would make `[ -d "$d" ] &&` short-circuit to exit 1 — which the caller reads
    as a failed snapshot, not an empty one. `true` pins exit 0 regardless of dir presence."""
    quoted = " ".join(shlex.quote(d) for d in dirs)
    return f'for d in {quoted}; do [ -d "$d" ] && find -L "$d" -type f -exec sha256sum {{}} + 2>/dev/null; done; true'


def parse_sha_stream(stdout: str) -> dict[str, str]:
    """Parse `sha_snapshot_script` stdout into {abs-path: sha}. `sha256sum` separates the
    digest from the path with exactly two spaces."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        sha, sep, path = line.partition("  ")
        if sep and path:
            out[path] = sha
    return out


def changed_paths(baseline_shas: dict[str, str], current_shas: dict[str, str]) -> list[str]:
    """Paths the agent created or changed — sha new or differs from the staged baseline."""
    return [p for p, sha in current_shas.items() if baseline_shas.get(p) != sha]


# Record-separator-framed header so file contents pass through verbatim. RS (0x1e) is
# control-only — it never appears in a skill's text — so a header can't collide with content.
_ARTIFACT_RE = re.compile("\x1e\x1eARTIFACT\x1e\x1e(.*?)\x1e\x1e\n")


def read_files_script(paths: list[str]) -> str:
    """POSIX-sh that prints each path in `paths` as an RS-framed record:
    `\\x1e\\x1eARTIFACT\\x1e\\x1e<abs-path>\\x1e\\x1e\\n` then the file's exact bytes.
    `parse_artifact_stream` reverses this. Pass only the changed paths `changed_paths` found."""
    body = "".join(
        f"printf '\\036\\036ARTIFACT\\036\\036%s\\036\\036\\n' {shlex.quote(p)}; "
        f"cat {shlex.quote(p)} 2>/dev/null; "
        for p in paths
    )
    return body or "true"


def parse_artifact_stream(stdout: str) -> dict[str, str]:
    """Parse `read_files_script` stdout into {abs-path: content}."""
    parts = _ARTIFACT_RE.split(stdout)
    # parts = [pre, path1, content1, path2, content2, ...]; the pre-segment before the
    # first header is discarded.
    out: dict[str, str] = {}
    it = iter(parts[1:])
    for path, content in zip(it, it):
        out[path] = content
    return out


def to_display_paths(mapping: dict[str, str], guest_home: str) -> dict[str, str]:
    """Rewrite absolute guest paths to `~/...` so the judge can tell skills-dir artifacts
    from workdir files at a glance."""
    home = guest_home.rstrip("/")
    out: dict[str, str] = {}
    for path, content in mapping.items():
        out["~" + path[len(home):] if path.startswith(home + "/") else path] = content
    return out


def merge_facts(tree: str, contents: dict, shas: dict, extra: dict[str, str],
                max_bytes: int = 20000):
    """Append `extra` ({display-path: content}) to a `gather_facts` triple, so captured
    artifacts grade alongside the workdir tree."""
    tree_lines = [ln for ln in tree.split("\n") if ln] if tree else []
    contents, shas = dict(contents), dict(shas)
    for path in sorted(extra):
        data = extra[path]
        tree_lines.append(path)
        shas[path] = _text_sha(data)
        contents[path] = data if len(data) <= max_bytes else data[:max_bytes] + "\n<TRUNCATED>"
    return "\n".join(tree_lines), contents, shas
