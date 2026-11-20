"""Bind each unique active assertion once per run, before any cell starts.

`binder.bind` depends only on the assertion text and the run's binder config, so the
controller binds every unique text once and hands the table to each cell. A failed bind is
never stored: that cell binds it live, as it would without a table.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor

from benchspec.config.arms import Arm
from benchspec.orchestration import results
from benchspec.specs import scope
from benchspec.specs.discovery import EvalCase

# Below the ~12 concurrent binder calls that trip the Gemini request timeout.
BIND_CONCURRENCY = 8

Binder = Callable[[str], dict | None]
BoundSpecs = dict[str, dict | None]

WORKERINPUT_KEY = "benchspec_bound_specs"


def unique_active_texts(
    pairs: Iterable[tuple[EvalCase, Arm]],
    *,
    baseline: str | None,
    eval_set: str,
    today: str,
) -> list[str]:
    """Every distinct substituted assertion text some arm grades, in first-seen order."""
    texts: dict[str, None] = {}
    for case, arm in pairs:
        substituted = results.substitute_assertions(case.assertions, today)
        applicable = scope.applicable(case, arm, baseline=baseline, eval_set=eval_set)
        for text, holds in zip(substituted, applicable, strict=True):
            if holds:
                texts[text] = None

    return list(texts)


def bind_unique(
    texts: Iterable[str], bind: Binder, *, concurrency: int = BIND_CONCURRENCY
) -> BoundSpecs:
    """Bind each text on a bounded thread pool.

    A RuntimeError (transient binder failure) omits that text from the table; a punt
    (`None`) is stored, being the binder's designed outcome. BinderAuthError propagates.
    """
    ordered = list(texts)

    def attempt(text: str) -> tuple[str, bool, dict | None]:
        try:
            return text, True, bind(text)
        except RuntimeError:
            return text, False, None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = list(pool.map(attempt, ordered))

    return {text: spec for text, bound, spec in outcomes if bound}


def summary_line(texts: list[str], table: Mapping[str, dict | None], seconds: float) -> str:
    """The one-line report of a pre-bind pass."""
    failed = len(texts) - len(table)

    return f"benchspec: bound {len(texts)} unique assertions in {seconds:.1f} s ({failed} failed)"


def prebind(
    pairs: list[tuple[EvalCase, Arm]],
    bind: Binder,
    *,
    baseline: str | None,
    eval_set: str,
    today: str,
) -> tuple[BoundSpecs, str]:
    """Bind every unique active text; return the table and its summary line."""
    texts = unique_active_texts(pairs, baseline=baseline, eval_set=eval_set, today=today)
    started = time.perf_counter()
    table = bind_unique(texts, bind)

    return table, summary_line(texts, table, time.perf_counter() - started)


def table_or_live(table: Mapping[str, dict | None], live: Binder) -> Binder:
    """A binder that answers from the table on a hit and binds live on a miss."""

    def bind(text: str) -> dict | None:
        if text in table:
            return table[text]

        return live(text)

    return bind
