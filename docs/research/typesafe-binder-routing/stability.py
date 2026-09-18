"""Check whether a prompt's answers move between identical calls, and in which direction.

The safety question is not whether the answer is stable but whether instability can go in
the dangerous direction. A bind-labeled assertion drifting to `punt` costs one judge call;
a punt-labeled assertion drifting to a checker is a silent false pass.

Needs TYPESAFE_API_KEY. Each pass costs about half a cent.

    uv run docs/research/typesafe-binder-routing/stability.py --prompt v7 --passes 4
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from corpus import CORPUS
from route import PROMPTS, route_entry


def main() -> None:
    """Run a prompt several times over the whole corpus and report any drift."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="v7", choices=sorted(PROMPTS))
    parser.add_argument("--passes", type=int, default=4)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    prompt = PROMPTS[args.prompt]
    passes: list[dict[str, str | None]] = []
    for number in range(args.passes):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows = list(pool.map(
                lambda entry: route_entry(entry, prompt["instructions"], prompt["criteria"]),
                CORPUS,
            ))
        passes.append({row["text"]: row["choice"] for row in rows})
        print(f"pass {number + 1}/{args.passes} complete", flush=True)

    gold = {entry["text"]: entry for entry in CORPUS}
    leaked: list[tuple[str, Counter]] = []
    drifted: list[tuple[str, str, Counter]] = []
    for text in passes[0]:
        seen = [single[text] for single in passes]
        if gold[text]["gold"] == "punt" and any(choice != "punt" for choice in seen):
            leaked.append((text, Counter(seen)))
        if len(set(seen)) > 1:
            drifted.append((text, gold[text]["gold"], Counter(seen)))

    draws = len(passes) * sum(1 for entry in CORPUS if entry["gold"] == "punt")
    print(f"\npunt-labeled assertions that EVER bound: {len(leaked)}  (over {draws} punt draws)")
    for text, counts in leaked:
        print(f"  LEAK {dict(counts)}  {text[:76]}")

    print(f"\nassertions whose answer moved at all: {len(drifted)}/{len(passes[0])}")
    for text, label, counts in drifted:
        print(f"  gold={label:5s} {dict(counts)}  {text[:70]}")


if __name__ == "__main__":
    main()
