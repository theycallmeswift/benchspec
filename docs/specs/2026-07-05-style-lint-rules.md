**TL;DR** — Add Ruff configuration plus two small `bin/` advisory lint scripts so mechanical style preferences are enforced by tools and subjective line-break guidance stays isolated and tunable.

## Problem

- **Symptom:** `make lint` currently runs `uv run ruff check .`, but `pyproject.toml` has no Ruff rule configuration and no local policy checks.
- **Exposed by:** Review of `docs/style/development.md` found preferences that split into three enforcement levels: Ruff-native rules, simple custom checks, and semantic logical-line-break review.
- **Scope:** Add executable lint coverage for Python style preferences where automation is practical, while keeping model-backed checks advisory.
- **Constraint:** Ruff rule codes must have inline comments because terse rule families are not self-descriptive.
- **Constraint:** Custom scripts live in `bin/`.
- **Constraint:** Logical line-break review must be its own script because it uses different mechanics, latency, cost, and tuning from simpler checks.

## Solution

```text
pyproject.toml        # commented Ruff rule configuration
bin/style_lint.py     # simple extensible advisory checks
bin/line_break_lint.py # hybrid v1 logical-line-break checker
make lint             # canonical entry point for deterministic checks
```

Ruff owns deterministic Python rules, `bin/style_lint.py` owns simple repo-specific advisory checks, and `bin/line_break_lint.py` owns the semantic logical-line-break experiment path.

## User Stories

1. As a maintainer, I want **Ruff to encode the mechanical style rules**, so review does not spend time on preferences a tool can catch.
2. As a contributor, I want **lint rule codes explained inline**, so I can understand project policy without cross-referencing every Ruff code.
3. As a maintainer, I want **custom checks represented by readable rule IDs**, so diagnostics describe the preference instead of exposing opaque `SL00X` names.
4. As a maintainer, I want **logical line-break review isolated from other checks**, so we can tune cost, recall, and false positives without destabilizing normal linting.

## Implementation Decisions

```text
docs/style/development.md
  ├─► pyproject.toml [tool.ruff.*] ──► make lint
  ├─► bin/style_lint.py ─────────────► advisory diagnostics
  └─► bin/line_break_lint.py ────────► hybrid v1 logical-line-break findings
```

- **Ruff configuration is the deterministic base.**
  - Add `[tool.ruff]` with the project target version.
  - Add `[tool.ruff.lint] select = [...]` with inline comments beside every rule family.
  - Include only rule families that align with `docs/style/development.md`.
  - Avoid arbitrary size and complexity limits because the style guide rejects line-count-based structure rules.

- **Readable custom rule IDs replace `SL00X` codes.**
  - Use names such as `section-header-comments`, `provenance-comments`, and `logical-line-breaks`.
  - Keep rule definitions in a list so future checks can be added without redesigning the script.
  - Emit diagnostics in a lint-like format with file, line, column, rule ID, and message.

- **`bin/style_lint.py` stays simple and extensible.**
  - Provide a small rule list and one pass over Python source files.
  - Cover simple checks such as section-header comments, external provenance comments, lint/type suppression comments, and other low-ambiguity local policies.
  - Treat findings as advisory unless a later implementation decision promotes a specific rule to blocking CI.

- **`bin/line_break_lint.py` uses the latest v1 hybrid approach.**
  - Generate candidate adjacent-statement boundaries deterministically from AST structure.
  - Filter candidates using the tuned v1 boundary filter from the experiments.
  - Ask the model to judge only candidate boundaries, not whole files.
  - Report boundary-oriented findings using `after_line` and `before_line` rather than pretending the model can reliably choose a single exact line.
  - Keep confirmation/refinement out of the first implementation because the experiment showed it added cost and strictness without enough value.

- **`make lint` remains the canonical local interface.**
  - Keep Ruff in the lint target.
  - Add the simple deterministic custom script when its baseline is ready.
  - Keep the line-break script callable from `bin/` but separate from blocking `make lint` until its advisory behavior is tuned.

## Testing Plan

### Logic
- **Ruff configuration reflects the style guide** — selected rule families map to documented preferences and exclude rules that contradict the guide.
- **Custom rule matching is stable** — each simple rule reports deterministic locations for matching source and accepts documented non-matches.
- **Boundary generation is deterministic** — the line-break script produces stable candidate boundaries for the same Python input.

### Behavior
- **The canonical lint path runs deterministic enforcement** — the normal lint command reports Ruff findings and deterministic custom findings together.
- **The line-break checker produces advisory results** — semantic findings are emitted separately with boundary locations and readable rule IDs.

### Interface
- **Scripts behave like lint CLIs** — each script accepts repository paths or defaults, prints diagnostics in a predictable format, and exits nonzero only for the mode that is meant to block.

## Documentation Plan

- **docs/style/development.md**: Update only if implementation introduces explicit exceptions or rollout stages that change the documented preference.
- **README.md**: Update only if contributor workflow gains a command beyond existing `make lint`.

## Out of Scope

- Adding pre-commit hooks.
- Building a Ruff plugin.
- Making model-backed line-break findings blocking in the first rollout.
- Rewriting the current codebase to satisfy every new lint finding.
- Enforcing subjective design guidance such as DRY boundaries, logging volume, package cohesion, or function extraction.

## References

- `docs/style/development.md:24` — prefers vertical whitespace between logical beats.
- `docs/style/development.md:29` — bans lint and type-check suppression comments.
- `docs/style/development.md:32` — identifies section-header comments as a smell.
- `docs/style/development.md:33` — bans comments that reference external context.
- `docs/style/development.md:70` — rejects single-letter names except `_`.
- `docs/style/development.md:88` — requires absolute, grouped, top-of-file imports.
- `docs/style/development.md:170` — requires blank-line test phase separation.
- `docs/style/development.md:191` — prefers linter and formatter driven enforcement.
- `docs/style/development.md:212` — requires `from __future__ import annotations` after the module docstring.
- `docs/style/development.md:215` — requires `textwrap.dedent` for indented multiline strings.
- `pyproject.toml:44` — Ruff is already a dev dependency.
- `Makefile:21` — `make lint` is already the lint entry point.

## Verification

- `python3 scripts/validate_spec.py docs/specs/2026-07-05-style-lint-rules.md` — validates spec structure if the validator is added to this repo.
- `make lint` — proves deterministic lint configuration and scripts are wired into the canonical lint target.
- `make test` — proves checker behavior and the existing suite pass together.
