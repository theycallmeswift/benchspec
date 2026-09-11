**TL;DR** — Accept a bound `regex` checker only when its `pattern` appears verbatim in the assertion text, and punt otherwise, so a binder draw that mangles the quoting can never turn a correct workspace into a failed deterministic check.

## Problem

- **Symptom:** the binder returned a wrong pattern for a regex assertion and the deterministic checker failed a correct file. In the `MLH/skills` `core-data-model` suite, `` ./answer.sql matches the regex "(?i)title\s+ILIKE\s+'%gemini%'" `` bound to `pattern: (?i)title\s+ILIKE\s+'%gemini%''` (a trailing quote appended) on the Gemini Flash cell of `gemini-hack-days`. The agent's SQL contained `es.title ILIKE '%gemini%'`, which the assertion's own pattern matches; `grading.json` recorded `CHECK regex: no match for /(?i)title\s+ILIKE\s+'%gemini%''/`.
- **Symptom:** the same line bound correctly minutes earlier. In the `harnesses` set the evidence reads `CHECK regex: matched: "title ILIKE '%gemini%'"` on all three skill arms. The binder is one `temperature: 0` call per assertion (`src/benchspec/grading/binder.py:192`, `:319`), but its output is not byte-stable across calls, and nothing downstream checks the returned pattern against the assertion it came from.
- **Symptom:** validation stops at "is it a regex". `_parse_binding` (`binder.py:532`) hands the reply to `_validate_checker_obj` (`src/benchspec/specs/schema.py:113`), which for `regex` only requires that `pattern` compiles (`schema.py:134-138`). A pattern with an extra quote compiles, so the spec is accepted and runs as a deterministic check.
- **Symptom:** the binder prompt tells the model to copy paths exactly but says nothing about patterns. `_BINDING_PROMPT` (`binder.py:107`) has "Copy paths exactly as written"; the `regex` primitive line (`binder.py:80`) and the examples (`binder.py:125-160`) carry no regex example at all, let alone one whose pattern contains quotes.
- **Why it stayed hidden:** the binder corpus (`evals/binder/corpus.yaml`) pins `expect` fields for `glob_count` and `file_exists` entries but its `regex` entries (`corpus.yaml:79-85`) are plain text with no `expect: pattern`, so a drifted pattern passes `test_binder_corpus_preserves_expected_checker_fields` (`evals/binder/test_corpus.py:143`). And the in-repo `hello` suite's regex lines contain no quotes.
- **Scope:** the `regex` checker's binding path: the reply guard in `_parse_binding`, the prompt's regex guidance, and the corpus. Other checkers copy paths, which the same guard can cover later.
- **Constraint:** the safe error is a punt. A rejected binding goes to the judge, which was going to see the line anyway; a false negative on a deterministic check is a wrong measurement that no judge revisits.
- **Constraint:** a nondeterministic binder draw cannot be reproduced by an e2e cell on demand. The reproduction surface is the binder corpus, which already runs every entry against the live binder under `make evals`, and unit tests of the guard with a canned reply.

## Solution

```python
# src/benchspec/grading/binder.py — inside _parse_binding, after _validate_checker_obj
if spec["checker"] == "regex" and spec["pattern"] not in assertion:
    return None  # the pattern is not the assertion's own text → punt, never a false negative
```

```yaml
# evals/binder/corpus.yaml — under binds: regex:
- text: ./answer.sql matches the regex "(?i)title\s+ILIKE\s+'%gemini%'"
  expect:
    pattern: (?i)title\s+ILIKE\s+'%gemini%'
```

`_parse_binding` takes the assertion text it is binding, and a `regex` spec whose `pattern` is not a verbatim substring of that text punts. The corpus pins the pattern for quoted regexes so drift fails `make evals`.

## User Stories

1. As an eval author, I want **a regex assertion to grade against the pattern I wrote**, so a deterministic line never fails on a pattern I did not write.
2. As a benchmark reader, I want **a mangled binder draw to fall through to the judge**, so the report never shows a false negative from the binder layer.
3. As a maintainer, I want **the corpus to pin regex patterns**, so a prompt or model change that drifts the pattern turns `make evals` red.

## Implementation Decisions

```
assertion ──► bind() ──► _call_gemini / _call_openrouter ──► reply text
                                                              │
                                                              ▼
                     _parse_binding(text, assertion) ──► _validate_checker_obj (compiles?)
                                                              │
                                                              ├─ regex and pattern ∉ assertion ──► None (punt → judge)
                                                              └─ else ──► spec ──► checkers._regex
evals/binder/corpus.yaml [binds.regex.*.expect.pattern] ──► test_binder_corpus_preserves_expected_checker_fields
```

- **The guard lives in `_parse_binding`, not in the checker.** `_parse_binding` (`binder.py:532`) already converts malformed args into a punt (`:547-548`); the verbatim check is the same class of decision. `checkers._regex` (`src/benchspec/grading/checkers.py:199`) keeps grading whatever spec it is handed.
  - `_parse_binding` gains the assertion text as a second argument; `bind()` (`binder.py:473`) passes the text it already has.
  - Substring, not equality: assertions wrap the pattern in quotes or say "matches the regex", so the pattern is a strict substring of the line.
- **Punt reason is recorded.** The returned `None` today carries no reason; the run's `grading.json` already labels a judged line `judge-backed`, so no new field is needed. The debug log line names the rejected pattern for the reader who wonders why a regex line went to the judge.
- **The prompt gains one regex example with quotes.** After the `sha256_match` examples (`binder.py:145-151`): an assertion of the form `` ./x matches the regex "(?i)foo\s+'bar'" `` bound to `{"checker":"regex","path":"./x","pattern":"(?i)foo\\s+'bar'"}`, plus one sentence beside "Copy paths exactly as written" (`binder.py:107`): patterns too, character for character, without the surrounding quotes.
- **The corpus pins patterns.** Each `binds.regex` entry whose pattern contains a quote gets `expect: {pattern: ...}` (`corpus.yaml:79-85` grows by the `MLH/skills` lines: the `ILIKE` one above, `` "(?i)type\s*=\s*'Email'" ``, `` '(?i)"primary"\s*=\s*true' ``). `test_corpus_integrity.py` already requires `expect` keys to be fields of the expected checker.
- **Paths later, not now.** The same guard would catch a mangled `path`; it is left for a follow-up because paths are rewritten on purpose today (`report.md` for "the file report.md"), so equality is the wrong test there.

## Testing Plan

### Logic
- **A verbatim pattern binds** — a reply whose `pattern` is a substring of the assertion is accepted unchanged.
- **A drifted pattern punts** — a reply whose `pattern` differs from the assertion by one character (an appended quote, a dropped backslash) returns a punt, never a spec.
- **Other checkers are unaffected** — replies for `file_exists`, `glob_count`, `sha256_match`, and the activation checkers pass through exactly as today.
- **The prompt carries the regex example** — the rendered binding prompt contains a regex example whose pattern includes a quote.

### Behavior
- **The corpus pins quoted patterns** — every `binds.regex` entry with a quote in its pattern has an `expect.pattern`, and the live binder returns it byte for byte; a drift fails the fields test.
- **A run with a mangled draw grades by judge** — with the binder forced to return a drifted pattern, the affected line is `judge-backed` in `grading.json` and the cell is never marked failed by the regex checker.

### Interface
- N/A — no CLI, config, or eval-format change; the guard is internal to binding and the corpus is repo-internal.

## Open Questions

- Should the guard log at info or debug? A punted regex is invisible in the matrix; a one-line info log naming the pattern makes the next investigation a grep instead of a diff of `grading.json`. Default: info.

## Documentation Plan

- **`docs/writing-evals.md:201`** (the checker table): note that a bound regex is the assertion's own pattern, verbatim, and that a draw that returns anything else punts.
- **`docs/concepts.md`** (binder entry): one sentence that binding validates the reply against the assertion, not only against the schema.

## Out of Scope

- Retrying the binder on a rejected pattern; a punt is free and the judge grades the line.
- Applying the verbatim guard to `path`, `glob`, or `original` fields; those are rewritten today and need their own rule.
- Making the binder deterministic across calls; `temperature: 0` is already set and the drift is upstream.

## References

- `src/benchspec/grading/binder.py:80,107,125-160,192,319,473,532,547` — the regex primitive line, the copy-paths rule, the examples, both transports, `bind`, and `_parse_binding` with its malformed-args punt.
- `src/benchspec/specs/schema.py:113,134-138` — `_validate_checker_obj` and its regex-compiles check.
- `src/benchspec/grading/checkers.py:199` — the regex checker that ran the drifted pattern.
- `evals/binder/corpus.yaml:79-85`, `evals/binder/test_corpus.py:143`, `evals/binder/test_corpus_integrity.py` — the regex entries, the fields test, and the `expect` integrity check.
- `MLH/skills` run artifacts: `gemini-hack-days` on `gemini-flash` (`iteration_07`) with `no match for /(?i)title\s+ILIKE\s+'%gemini%''/` beside SQL containing `es.title ILIKE '%gemini%'`; the same line matched on every arm in `iteration_06`.

## Verification

- `make test` — proves the guard accepts verbatim patterns, punts drifted ones, leaves other checkers unchanged, and the prompt carries the example.
- `make lint` — proves style and types.
- `make evals` — proves the live binder returns the pinned patterns for every quoted regex entry in the corpus.
- `OPENROUTER_API_KEY=... uv run benchspec analyze /path/to/mlh-skills` — every `matches the regex` line in `evals/core-data-model/` reports `deterministic`, showing the guard does not over-punt on real assertions.
