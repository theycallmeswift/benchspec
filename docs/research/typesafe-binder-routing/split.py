"""Rebuild the stratified train/test split used for prompt tuning.

Writes `split.json`, which is committed — running this reproduces the committed file
exactly, given the same corpus and seed. Tuning against the whole corpus looked fine on
one intermediate prompt and shipped two leaks on held-out entries, which is why the split
exists at all.

    uv run docs/research/typesafe-binder-routing/split.py
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from corpus import CORPUS

HERE = Path(__file__).resolve().parent

SEED = 20260918


def build_split() -> dict[str, list[int]]:
    """Split corpus indexes in half, stratified by (gold label, cohort).

    An odd stratum gives its extra entry to train, so a cohort with a single entry never
    lands only in test.
    """
    by_stratum: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, entry in enumerate(CORPUS):
        by_stratum[(entry["gold"], entry["cohort"])].append(index)

    rng = random.Random(SEED)
    train: list[int] = []
    test: list[int] = []
    for stratum in sorted(by_stratum):
        indexes = by_stratum[stratum][:]
        rng.shuffle(indexes)
        half = len(indexes) // 2
        test.extend(indexes[:half])
        train.extend(indexes[half:])

    return {"train": sorted(train), "test": sorted(test)}


def main() -> None:
    """Write the split and print its shape."""
    split = build_split()
    (HERE / "split.json").write_text(json.dumps(split))
    for name, indexes in split.items():
        binds = sum(1 for index in indexes if CORPUS[index]["gold"] == "bind")
        print(f"{name}: {len(indexes)} entries ({binds} bind / {len(indexes) - binds} punt)")


if __name__ == "__main__":
    main()
