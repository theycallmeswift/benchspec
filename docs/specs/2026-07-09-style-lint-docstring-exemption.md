**TL;DR** — Exempt docstrings in the `dedented-multiline-strings` rule description so the advisory style linter stops confirming false positives on ordinary indented docstrings.

## Problem

- **Symptom:** The advisory linter flags indented multiline *docstrings* under `dedented-multiline-strings` (e.g. a method docstring in `src/evalspec/agents/claude.py` on the PR #6 branch, flagged persistently across three verified runs), even though the rule targets raw string *values*.
- **Why it stayed hidden:** The finding survives `--verify-findings` — the verifier receives the same one-line rule description as the detector, and nothing in it says docstrings are exempt, so verification confirms rather than kills the false positive.
- **Exposed by:** Repeated `--base`-scoped runs during PR #6 review: the same docstring finding recurred every run while every triple-quoted string in the flagged file was a docstring.
- **Scope:** One rule's description text in `RULES` (`bin/linters/style_lint.py:29`); no detector/verifier machinery changes.
- **Constraint:** The style guide itself mandates indented multiline docstrings on everything (`docs/style/development.md:38`) while requiring `textwrap.dedent` only for raw string values (`docs/style/development.md:215`) — the rule text must encode that distinction, not fight it.

## Solution

```python
style_lint.Rule(
    id="dedented-multiline-strings",
    description=(
        "Use textwrap.dedent for indented multiline string values. "
        "Docstrings are exempt — indented multiline docstrings are correct."
    ),
),
```

The rule description flows verbatim into both the detector and verifier prompts, so one text change fixes both stages.

## User Stories

1. As a developer running `make lint:custom` on a branch, I want **docstrings never flagged under `dedented-multiline-strings`**, so recurring false positives stop burying real findings.
2. As a reviewer trusting `--verify-findings`, I want **the verifier to kill docstring findings**, so a "verified" finding reliably means actionable.

## Implementation Decisions

```
RULES (bin/linters/style_lint.py:29)
  ├──► build_detector_prompt (lib/style_lint/detector.py:26)  ──► detector findings
  └──► build_verifier_prompt (lib/style_lint/verifier.py:30)  ──► verified findings (docstrings killed)
```

- **Fix the rule text, not the framework.** `Rule` is `{id, description}` (`lib/style_lint/types.py:28`) serialized verbatim into both prompts; the exemption belongs in the description, so the detector under-reports and the verifier refutes any that slip through.
  - No new rule id — same `dedented-multiline-strings`, sharpened wording.
- **Say "string values" and name the exemption explicitly.** LLM rule prompts follow the literal text; "indented multiline strings" reads as including docstrings, so the description must say both what's in scope (values) and what's out (docstrings).
- **Keep the rules doc in sync.** The same `Rule` literal is quoted in `docs/specs/2026-07-05-style-lint-rules.md:91`; update it to match so the doc doesn't drift from the code.

## Testing Plan

### Logic
- **The shipped rule description carries the docstring exemption** — the `dedented-multiline-strings` entry in `RULES` names string values as in scope and docstrings as exempt, and that text reaches both the detector and verifier prompts unchanged.

### Behavior
- **A file whose only triple-quoted strings are docstrings yields no verified `dedented-multiline-strings` finding** — the previously-flagged shape no longer survives a `--verify-findings` run.
- **A genuine violation still fires** — an indented multiline string assigned as a value (not a docstring) is still reported, proving the exemption narrows rather than disables the rule.

### Interface
- N/A — no change to the CLI surface (`--base`, `--verify-findings`, paths, exit codes) or output format; this is prompt-text only.

## Documentation Plan

- **`docs/specs/2026-07-05-style-lint-rules.md`**: update the quoted `dedented-multiline-strings` `Rule` literal to the amended description.

## Out of Scope

- False-positive tuning for other rules (e.g. `descriptive-names` flagging repo-conventional short names like `sb` before its rename) — separate judgment calls per rule.
- A deterministic AST pre-filter that strips docstrings before chunking — heavier machinery than a one-line prompt fix warrants; reconsider only if the exemption text proves insufficient.
- Any change to `DEFAULT_SYSTEM_PROMPT` or the detector/verifier plumbing.

## References

- `bin/linters/style_lint.py:29` — `RULES`, home of the `dedented-multiline-strings` description being amended.
- `lib/style_lint/detector.py:26` / `lib/style_lint/verifier.py:30` — both prompts serialize `{id, description}` verbatim; why one text change fixes both stages.
- `docs/style/development.md:215` and `:38` — the guide's `textwrap.dedent` rule targets string values while mandating indented docstrings everywhere; the ground truth the rule text must encode.
- `docs/specs/2026-07-05-style-lint-rules.md:91` — quotes the same `Rule` literal; kept in sync.
- PR #6 (`feature/2-judge-abstraction`), `src/evalspec/agents/claude.py:229` — the recurring false positive (a `judge` method docstring) that surfaced this.

## Verification

- `make test` — the style-linter unit tests (including prompt-construction coverage under `tests/lib/style_linter/`) pass with the amended description.
- `make lint` — ruff passes on the edited files.
- `uv run python bin/linters/style_lint.py --verify-findings src/evalspec/agents/claude.py` — a docstring-rich file reports no `dedented-multiline-strings` finding.
