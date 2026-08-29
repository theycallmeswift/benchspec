"""Static lint for eval suites: find unjudgeable assertions at authoring time.

The judge grades ONLY from supplied evidence (workdir facts, SHAs, the agent's final
message, process facts). An assertion the evidence can't decide gets graded by vibes —
pass rates move without the skill changing. Rules are heuristics: a `warning` means the
assertion is unjudgeable as written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harnessbench.specs import discovery

_VAGUE = re.compile(
    r"\b(explicitly|appropriately|properly|gracefully|suitably|reasonably|adequately)\b",
    re.IGNORECASE,
)
_COMPARATIVE = re.compile(r"\b(better|worse|cleaner|clearer|stronger|improved)\b", re.IGNORECASE)
_PATHISH = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.\w{1,8}")
# A workdir anchor: a `./`-prefixed path (after start, whitespace, or a quote/bracket —
# not mid-word and not a bare `..`). Its presence marks the path-ish text as a workdir
# fact the judge is shown, so the unseen-file rule stands down.
_ANCHORED = re.compile(r"(?<![\w.])\./")


@dataclass(frozen=True)
class Finding:
    """Store one static eval-lint finding."""

    file: Path
    eval_id: str
    assertion: str
    rule: str
    message: str


def lint_assertion(text: str) -> list[tuple[str, str]]:
    """(rule, message) pairs for one assertion string."""
    findings = []
    if vague_match := _VAGUE.search(text):
        findings.append(
            (
                "vague-adverb",
                f"`{vague_match.group(0)}` names no observable criterion — state the behavior "
                "the evidence can show",
            )
        )
    if not _ANCHORED.search(text) and (path_match := _PATHISH.search(text)):
        findings.append(
            (
                "unseen-file",
                f"the judge never sees `{path_match.group(0)}` — it grades only workdir facts "
                "and the final message; anchor it to the workdir with a leading `./`",
            )
        )
    if _COMPARATIVE.search(text) and " than " not in text.lower():
        findings.append(
            (
                "relative-claim",
                "relative claim with no comparand — compare against something the judge is shown",
            )
        )
    return findings


def lint_repo(repo_root: Path) -> list[Finding]:
    """Lint discovered eval assertions for unjudgeable wording."""
    findings: list[Finding] = []
    for case in discovery.discover_eval_cases(repo_root):
        for text in case.assertions:
            for rule, message in lint_assertion(text):
                findings.append(Finding(case.eval_file, case.eval_id, text, rule, message))
    return findings


def run(repo_root: Path) -> int:
    """Print findings grouped by file; exit 1 on any finding (all are warnings)."""
    findings = lint_repo(repo_root)
    current = None
    for finding in findings:
        if finding.file != current:
            print(f"\n{finding.file}")
            current = finding.file
        print(f"  [warning] {finding.eval_id}: {finding.rule}: {finding.message}")
        print(f"      `{finding.assertion[:100]}`")
    print(f"\n{len(findings)} warning(s)")
    return 1 if findings else 0
