"""Score the production Gemini binder over the same corpus, for comparison.

Deliberately bypasses `_bind_bare_exists`, the local regex fast path that binds bare
existence assertions with no API call, so the numbers isolate model routing quality and
line up with `route.py`, which has no such fast path to bypass.

Needs GEMINI_API_KEY. One pass over the corpus costs about seven cents.

    uv run docs/research/typesafe-binder-routing/baseline_gemini.py
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from corpus import CORPUS, CorpusEntry
from route import SPLIT, report

from benchspec.grading.binder import (
    _BINDING_PROMPT,
    _parse_binding,
    call_model_for,
)
from benchspec.grading.binder_config import BinderConfig

MAX_ATTEMPTS = 3
CONFIG = BinderConfig()


def bind_entry(entry: CorpusEntry) -> dict:
    """Bind one corpus entry through the model path only, in `route.py`'s record shape."""
    record = {
        "text": entry["text"],
        "gold": entry["gold"],
        "cohort": entry["cohort"],
        "expect_checker": entry.get("expect_checker"),
        "confidence": None,
        "probabilities": {},
    }
    call_model = call_model_for(CONFIG)
    prompt = _BINDING_PROMPT.format(assertion=entry["text"])
    failure = "not attempted"
    for attempt in range(MAX_ATTEMPTS):
        try:
            reply = call_model(prompt, timeout=60)
            spec = _parse_binding(reply.text, entry["text"])
            return {
                **record,
                "choice": spec["checker"] if spec else "punt",
                "input_tokens": reply.prompt_tokens,
                "error": None,
            }
        except RuntimeError as error:
            failure = str(error)[:200]
        time.sleep(2**attempt)
    return {**record, "choice": None, "error": failure}


def main() -> None:
    """Score the Gemini binder over one corpus split."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="all", choices=["train", "test", "all"])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", help="write per-entry records to this JSONL path")
    args = parser.parse_args()

    indexes = range(len(CORPUS)) if args.split == "all" else SPLIT[args.split]
    entries = [CORPUS[index] for index in indexes]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(bind_entry, entries))

    if args.out:
        Path(args.out).write_text("".join(json.dumps(row) + "\n" for row in rows))
    report(f"{CONFIG.provider}/{CONFIG.model}", args.split, rows)


if __name__ == "__main__":
    main()
