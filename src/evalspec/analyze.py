"""Classify eval assertions by how they will be graded, before a paid run.

`evalspec analyze` binds each assertion the same way a live run does and reports the
evidence domain it grades against: a `deterministic` workdir check, a process-fact
`activation` check, or an LLM `judge-backed` verdict. The local fast paths keep
existence and activation lines free; every other assertion pays one Gemini punt-or-bind
call, so an author sees where each assertion lands before spending on a full run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from evalspec import binder, discovery


@dataclass(frozen=True)
class Classification:
    """Store one assertion's pre-run grading classification."""

    file: Path
    eval_id: str
    assertion: str
    label: str


def classify_assertion(text: str, *, bind: Callable[[str], dict | None] = binder.bind) -> str:
    """Classify how one assertion will be graded at run time.

    Binds the assertion exactly as a live run does: if the binder maps it to a host-side
    checker it grades `deterministic` (zero-variance, no judge cost); otherwise it punts
    and grades `judge-backed` (the nondeterministic LLM path). A binder *infrastructure*
    failure is not a classification and is left to propagate.

    Args:
        text: The assertion prose to classify.
        bind: The binder entry point, injected for tests. Defaults to the real
            `binder.bind` so the label matches how the assertion actually grades.

    Returns:
        `"deterministic"` if the binder binds it, else `"judge-backed"`.

    Raises:
        BinderAuthError: the Gemini credential was rejected — analyze cannot classify
            a suite whose binder is down, so this propagates instead of mislabeling.
        RuntimeError: the binder transport failed, for the same reason.
    """
    return "deterministic" if bind(text) is not None else "judge-backed"


def analyze_repo(
    repo_root: Path, *, bind: Callable[[str], dict | None] = binder.bind
) -> list[Classification]:
    """Classify every discovered assertion under a repo root.

    Args:
        repo_root: The root whose eval cases are discovered and classified.
        bind: The binder entry point, injected for tests.

    Returns:
        One `Classification` per assertion, in discovery order.
    """
    classifications: list[Classification] = []
    for case in discovery.discover_eval_cases(repo_root):
        for text in case.assertions:
            label = classify_assertion(text, bind=bind)
            classifications.append(Classification(case.eval_file, case.eval_id, text, label))
    return classifications


def run(repo_root: Path) -> int:
    """Print each assertion's grading classification grouped by file.

    Preflights the Gemini key (the binder calls Gemini for assertions past the local
    fast paths), classifies every discovered assertion, and prints a per-file table.

    A fully-classified suite always returns 0 — a classification is a report, not a
    warning, so unlike `lint.run` this never exits nonzero on a non-empty suite.
    Discovery/parse/schema errors and binder-infrastructure failures propagate to the
    CLI boundary, which turns them into a nonzero exit.

    Args:
        repo_root: The root to analyze.

    Returns:
        0 once the suite is classified.
    """
    binder.preflight_gemini_key()
    classifications = analyze_repo(repo_root)

    current = None
    for classification in classifications:
        if classification.file != current:
            print(f"\n{classification.file}")
            current = classification.file
        print(f"  {classification.label:<13}  {classification.assertion}")

    print(f"\n{len(classifications)} assertion(s)")
    return 0
