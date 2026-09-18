"""Score one TypeSafe routing prompt against the binder corpus.

Backs [`../2026-09-17-typesafe-binder-routing.md`](../2026-09-17-typesafe-binder-routing.md).
Asks one Choice question per assertion — the eight deterministic checkers plus `punt` —
and scores the answer against the corpus gold labels, using the metric definitions in
`evals/binder/conftest.py` so the numbers line up with what `make evals` prints.

Needs TYPESAFE_API_KEY. One pass over the 139-entry corpus costs about half a cent.

    uv run docs/research/typesafe-binder-routing/route.py --prompt v7 --split all
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from corpus import CORPUS, CorpusEntry

HERE = Path(__file__).resolve().parent

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 3
AUTH_HTTP_CODES = {401, 403}

PROMPTS = json.loads((HERE / "prompts.json").read_text())
SPLIT = json.loads((HERE / "split.json").read_text())


class RoutingAuthError(Exception):
    """The TypeSafe API rejected the configured credential."""


def ask(text: str, instructions: object, criteria: dict) -> dict:
    """Send one routing Choice for `text` and return the parsed answer plus usage.

    Raises:
        RoutingAuthError: the API key was rejected.
        urllib.error.HTTPError / OSError / ValueError: every other transport failure,
            left for the caller's retry loop to classify.
    """
    body = json.dumps({
        "state": text,
        "model": TYPESAFE_MODEL,
        "questions": {
            "route": {"type": "choice", "instructions": instructions, "criteria": criteria}
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        TYPESAFE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    answer = payload["answers"]["route"]
    usage = payload.get("usage", {})
    return {
        "choice": answer["choice"],
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
        "input_tokens": usage.get("input_tokens", 0),
    }


def route_entry(entry: CorpusEntry, instructions: object, criteria: dict) -> dict:
    """Route one corpus entry, retrying transient failures with backoff.

    Returns a record carrying the gold label alongside the answer, or an `error` string
    when every attempt failed. An auth rejection stops the whole run instead.
    """
    record = {
        "text": entry["text"],
        "gold": entry["gold"],
        "cohort": entry["cohort"],
        "expect_checker": entry.get("expect_checker"),
    }
    failure = "not attempted"
    for attempt in range(MAX_ATTEMPTS):
        try:
            return {**record, **ask(entry["text"], instructions, criteria), "error": None}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:200]
            if error.code in AUTH_HTTP_CODES:
                raise RoutingAuthError(f"TypeSafe rejected the credential: {detail}") from error
            failure = f"HTTP {error.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            failure = f"transport: {error}"
        time.sleep(2**attempt)
    return {**record, "choice": None, "error": failure}


def rate(rows: list[dict], hit: Callable[[dict], bool]) -> str:
    """Format the share of rows matching a predicate, with its raw counts."""
    if not rows:
        return "0/0"
    return f"{sum(1 for row in rows if hit(row))}/{len(rows)}"


def report(name: str, split: str, rows: list[dict]) -> None:
    """Print the gate result and the reporting rates for one scored run.

    Every mismatch counts as dangerous alongside every leak: a bind-labeled assertion
    bound to the wrong checker can pass on failing output just as a leak can, and the
    production gate does not currently catch it.
    """
    answered = [row for row in rows if not row["error"]]
    binds = [row for row in answered if row["gold"] == "bind"]
    punts = [row for row in answered if row["gold"] == "punt"]
    leaks = [row for row in punts if row["choice"] != "punt"]
    mismatches = [
        row for row in binds if row["choice"] not in ("punt", row["expect_checker"])
    ]
    kept = [row for row in binds if row["choice"] == row["expect_checker"]]

    errors = len(rows) - len(answered)
    retention = f"{len(kept)}/{len(binds)} = {len(kept) / len(binds):.3f}" if binds else "n/a"
    tokens = sum(row.get("input_tokens", 0) for row in answered)

    print(f"PROMPT {name}  SPLIT {split}  ({len(answered)} scored, {errors} errors)")
    print(f"  DANGEROUS (leaks + mismatches) : {len(leaks) + len(mismatches)}     <-- must be 0")
    print(f"    leaks    (punt -> bound)     : {rate(punts, lambda row: row['choice'] != 'punt')}")
    print(f"    mismatch (bind -> wrong)     : {len(mismatches)}/{len(binds)}")
    print(f"  retention  (bind -> right)     : {retention}")
    print(f"  over-punt  (bind -> punt)      : {rate(binds, lambda row: row['choice'] == 'punt')}")
    print(f"  input tokens                   : {tokens}")

    for label, group in (("LEAK", leaks), ("MISMATCH", mismatches)):
        for row in group:
            top = dict(sorted(row["probabilities"].items(), key=lambda kv: -kv[1])[:3])
            want = row["expect_checker"] or "punt"
            print(f"  {label}: -> {row['choice']} (want {want}, conf {row['confidence']}) {top}")
            print(f"        {row['text'][:104]}")


def main() -> None:
    """Score one prompt over one corpus split."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="v7", choices=sorted(PROMPTS), help="prompt variant")
    parser.add_argument("--split", default="all", choices=["train", "test", "all"])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", help="write per-entry records to this JSONL path")
    args = parser.parse_args()

    prompt = PROMPTS[args.prompt]
    indexes = range(len(CORPUS)) if args.split == "all" else SPLIT[args.split]
    entries = [CORPUS[index] for index in indexes]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(
            lambda entry: route_entry(entry, prompt["instructions"], prompt["criteria"]),
            entries,
        ))

    if args.out:
        Path(args.out).write_text("".join(json.dumps(row) + "\n" for row in rows))
    report(args.prompt, args.split, rows)


if __name__ == "__main__":
    main()
