"""Load the binder corpus for the research scripts, without importing the eval suite.

`evals/binder/test_corpus_integrity.py` builds the same list, but importing it from here
means putting its directory on `sys.path`, which neither the type checker nor a reader
can follow. The corpus file is the source of truth either way, so read it directly.

Entry order must match `test_corpus_integrity.CORPUS` — binds first, grouped by checker,
then punts grouped by cohort — because `results.jsonl` stores entry indexes into it.
"""

from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

import yaml

CORPUS_PATH = Path(__file__).resolve().parents[3] / "evals" / "binder" / "corpus.yaml"


class CorpusEntry(TypedDict):
    """One gold-labeled assertion: a bind with its expected checker, or a punt."""

    text: str
    gold: str
    cohort: str
    expect_checker: NotRequired[str]
    expect: NotRequired[dict]


def _bind_entry(checker: str, item: object) -> CorpusEntry:
    """Build one gold-bind entry from a `binds` line: bare text, or a mapping with `expect`."""
    if isinstance(item, str):
        return {"text": item, "gold": "bind", "cohort": checker, "expect_checker": checker}
    if isinstance(item, dict):
        entry: CorpusEntry = {
            "text": item["text"],
            "gold": "bind",
            "cohort": checker,
            "expect_checker": checker,
        }
        if "expect" in item:
            entry["expect"] = item["expect"]
        return entry
    raise TypeError(f"corpus bind entry under {checker!r} must be text or a mapping")


def load_corpus(path: Path = CORPUS_PATH) -> list[CorpusEntry]:
    """Flatten corpus.yaml's binds-by-checker and punts-by-cohort tables into entries."""
    raw = yaml.safe_load(path.read_text())
    corpus: list[CorpusEntry] = []
    for checker, items in raw["binds"].items():
        corpus += [_bind_entry(checker, item) for item in items]
    for cohort, texts in raw["punts"].items():
        corpus += [{"text": text, "gold": "punt", "cohort": cohort} for text in texts]
    return corpus


CORPUS = load_corpus()
