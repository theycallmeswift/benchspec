# TypeSafe binder routing — scripts and data

Everything needed to reproduce
[`../2026-09-17-typesafe-binder-routing.md`](../2026-09-17-typesafe-binder-routing.md):
can TypeSafe's Jev replace the Gemini model in benchspec's assertion binder, and can
prompting close the gap.

Nothing here is wired into `src/`. These scripts hit paid APIs and are run by hand.

## Files

| File | What it is |
| --- | --- |
| `prompts.json` | All eight prompt variants, verbatim as sent. `v0` is the original; `v4` is the overfit one kept as a negative result; `v7` is the one the note recommends. |
| `route.py` | Scores one prompt over one split against the corpus gold labels. |
| `baseline_gemini.py` | Same scoring for the production Gemini binder, for comparison. |
| `stability.py` | Repeats a prompt over the corpus and reports whether answers drift, and in which direction. |
| `split.py` | Rebuilds the stratified train/test split. Deterministic — reproduces `split.json`. |
| `split.json` | The exact 72/67 split the tuning used (seed 20260918). |
| `results.jsonl` | Every run behind the note's numbers, one row per prompt × split × assertion. |

## Running

```sh
uv run docs/research/typesafe-binder-routing/route.py --prompt v7 --split all
uv run docs/research/typesafe-binder-routing/route.py --prompt v0 --split test
uv run docs/research/typesafe-binder-routing/baseline_gemini.py
uv run docs/research/typesafe-binder-routing/stability.py --prompt v7 --passes 4
```

`route.py` and `stability.py` need `TYPESAFE_API_KEY`; `baseline_gemini.py` needs
`GEMINI_API_KEY`. A pass over the 139-entry corpus costs about half a cent on Jev and
about seven cents on Gemini.

## Reading `results.jsonl`

One row per answer:

```json
{"variant": "v7", "model": "jev-1.13.0", "split": "all", "entry": 42,
 "choice": "punt", "confidence": 0.86, "probabilities": {"punt": 0.9, "regex": 0.1}}
```

`entry` is a 0-based index into `evals/binder/corpus.yaml` in load order — the order
`test_corpus_integrity.CORPUS` builds, binds first by checker, then punts by cohort. Join
against that to recover the assertion text and its gold label:

```python
import json, sys
sys.path.insert(0, "evals/binder")
from test_corpus_integrity import CORPUS

rows = [json.loads(line) for line in open("docs/research/typesafe-binder-routing/results.jsonl")]
v7 = [r for r in rows if r["variant"] == "v7" and r["split"] == "all"]
leaks = [r for r in v7 if CORPUS[r["entry"]]["gold"] == "punt" and r["choice"] != "punt"]
print(len(leaks), "leaks")
```

The corpus is the source of truth for gold labels, so a corpus edit invalidates these
rows rather than silently changing their meaning — re-run rather than reinterpret.

## What is not here

The N=10 repeat runs. Both models answered identically on all ten passes, so the repeats
carry no information the single pass does not; `results.jsonl` keeps one pass per prompt.
`stability.py` is how that claim was checked, and re-checks it.
