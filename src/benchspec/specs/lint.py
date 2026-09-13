"""Static lint for eval suites: find unjudgeable assertions at authoring time.

The judge grades ONLY from supplied evidence (workdir facts, SHAs, the agent's final
message, process facts). An assertion the evidence can't decide gets graded by vibes —
pass rates move without the skill changing. Rules are heuristics: a `warning` means the
assertion is unjudgeable as written.

Two rules read the scope clause instead: a clause naming no variable is a constant, and
a skill-trigger line with no clause is graded in a baseline where it can never pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from benchspec.specs import discovery, scope

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
_TRIGGER = re.compile(r"^Skill `[^`]+` (not )?invoked$")
_TRIGGER_CLAUSE = "- if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}"


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


def lint_clause(text: str, clause: dict | None) -> list[tuple[str, str]]:
    """(rule, message) pairs for one assertion's scope clause (or its absence).

    Raises:
        scope.ScopeError: the clause's expression does not parse.
    """
    findings = []
    if clause is not None and not scope.names(scope.parse(clause["expr"])):
        findings.append(
            (
                "constant-clause",
                f"`{scope.clause_text(clause)}` reads no `{{VAR}}`, so it holds or fails "
                "identically in every arm — a no-op or a disabled line",
            )
        )

    if clause is None and _TRIGGER.match(text):
        findings.append(
            (
                "untagged-trigger",
                "a skill-trigger line with no clause is graded in the baseline too, where "
                f"it can never pass, so it inflates every delta; add `{_TRIGGER_CLAUSE}`",
            )
        )

    return findings


def lint_repo(repo_root: Path) -> list[Finding]:
    """Lint discovered eval assertions for unjudgeable wording and unscoped triggers."""
    findings: list[Finding] = []
    for case in discovery.discover_eval_cases(repo_root):
        for text, clause in zip(case.assertions, case.clauses, strict=True):
            for rule, message in lint_assertion(text) + lint_clause(text, clause):
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
