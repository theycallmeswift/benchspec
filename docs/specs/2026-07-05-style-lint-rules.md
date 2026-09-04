**TL;DR** — Add commented Ruff configuration plus verified full-codebase advisory Gemini 3.1 Flash Lite style checks under `make lint`.

## Problem

- **Symptom:** `make lint` currently runs `uv run ruff check .`, but `pyproject.toml` has no Ruff rule configuration and no local policy checks.
- **Exposed by:** Review of `docs/style/development.md` found preferences that split into Ruff-native rules and model-backed custom checks.
- **Scope:** Add executable lint coverage for Python style preferences where automation is practical, while keeping Ruff as the first deterministic gate.
- **Constraint:** Ruff rule codes must have inline comments because terse rule families are not self-descriptive.
- **Constraint:** Local lint scripts live under `bin/linters/`.
- **Constraint:** Custom style checks use Gemini 3.1 Flash Lite as the default detector model.

## Solution

```toml
[tool.ruff]
target-version = "py310"

[tool.ruff.lint]
select = [
    "E",      # pycodestyle errors: syntax-adjacent readability and whitespace.
    "F",      # Pyflakes: undefined names, unused imports, and invalid constructs.
    "I",      # isort: stdlib, third-party, local import grouping and ordering.
    "UP",     # pyupgrade: modern Python syntax for the supported version range.
    "FA",     # flake8-future-annotations: require postponed annotations.
    "D",      # pydocstyle: module, class, and function docstring requirements.
    "ANN",    # flake8-annotations: function argument and return annotations.
    "TID",    # flake8-tidy-imports: absolute imports and banned import shapes.
    "PT",     # flake8-pytest-style: pytest-specific readability rules.
    "B",      # flake8-bugbear: likely bugs and surprising Python behavior.
    "A",      # flake8-builtins: avoid shadowing Python builtins.
    "RUF100", # Ruff: remove stale noqa suppressions while custom lint bans suppressions.
]

[tool.ruff.lint.pydocstyle]
convention = "google"
```

Add the Ruff configuration above, add reusable advisory lint framework code under `lib/style_lint/`, add evalspec-specific policy and CLI orchestration in `bin/linters/style_lint.py`, and run that script with verification after Ruff in the default `make lint` target.

## User Stories

1. As a maintainer, I want **Ruff to encode the mechanical style rules**, so review does not spend time on preferences a tool can catch.
2. As a contributor, I want **lint rule codes explained inline**, so I can understand project policy without cross-referencing every Ruff code.
3. As a maintainer, I want **custom checks represented by readable rule IDs**, so diagnostics describe the preference instead of exposing opaque `SL00X` names.
4. As a contributor, I want **one canonical lint command that runs Ruff first and then slower advisory custom lint**, so I know what must pass before pushing and where to run repo-specific style review.

## Implementation Decisions

```text
docs/style/development.md
  ├─► pyproject.toml [tool.ruff.*] ────────────────┐
  └─► bin/linters/style_lint.py + lib/style_lint/ ─┴─► make lint ─► Ruff gate, then verified advisory style review
```

- **Ruff configuration is the deterministic base.**
  - Use `ruff 0.15.20` semantics, which is the locally installed Ruff version when this spec was written.
  - Configure `target-version = "py310"` because `pyproject.toml` requires Python `>=3.10`.
  - Add the `select` list exactly as shown in the Solution section, keeping inline comments beside every rule family.
  - Configure Google docstrings with `[tool.ruff.lint.pydocstyle] convention = "google"`.
  - Avoid arbitrary size and complexity limits such as `C901`, `PLR0912`, and `PLR0915` because `docs/style/development.md` rejects line-count-based structure rules.
  - Do not rely on Ruff `DOC` preview rules in the first rollout; docstring existence and Google-style shape are enough for the initial deterministic pass.

- **`bin/linters/style_lint.py` owns advisory model-backed rules Ruff cannot express cleanly.**
  - Use `gemini-3.1-flash-lite` as the default detector model.
  - Require `GEMINI_API_KEY`; if it is missing, print a clear skip message and exit zero.
  - Keep reusable framework code under `lib/style_lint/`, not inside `src/evalspec/`, so it can later be extracted.
  - Keep framework implementation in focused submodules; use `lib/style_lint/__init__.py` only for export control.
  - Keep evalspec-specific business logic in `bin/linters/style_lint.py`: default paths, rule definitions, model defaults, and prompt instructions.
  - Keep the generic linter system prompt in `lib/style_lint/detector.py`; keep evalspec policy instructions in `bin/linters/style_lint.py`.
  - Define rules in a list and inject them into one general prompt so future checks are additive:

    ```python
    RULES = [
        Rule(
            id="no-suppression-comments",
            description="Do not use lint or type-check suppression comments.",
        ),
        Rule(
            id="section-header-comments",
            description="Do not use region-style comments instead of named structure.",
        ),
        Rule(
            id="provenance-comments",
            description="Do not reference PRs, issues, commits, callers, or planning docs in source comments.",
        ),
        Rule(
            id="descriptive-names",
            description="Do not use single-letter bindings except `_` for intentionally unused values.",
        ),
        Rule(
            id="dedented-multiline-strings",
            description=(
                "Use textwrap.dedent for indented multiline string values. "
                "Docstrings are exempt — indented multiline docstrings are correct."
            ),
        ),
    ]
    ```

  - Gemini receives numbered source chunks plus the rule list and decides which snippets violate the rules.
  - Deterministic framework code should stay limited to file collection, source chunking/numbered line formatting, Gemini transport, strict JSON/schema validation, optional verification, advisory error handling, and output formatting.
  - Expose a config-based library runner so callers do not have to manually sequence the framework functions.
  - Do not keep hand-rolled AST/token helper logic for subjective rule detection in the first rollout.
  - Diagnostics use `path:line:col: rule-id message`; default paths are `src`, `tests`, `evals`, `bin`, and `lib`, with optional path arguments for scoped runs.
  - `--base <ref>` limits output to findings on lines touched since the given git ref while still letting the model review full source context.
  - Exit zero by default even when findings exist because model-backed findings are advisory in the first rollout.
  - A future explicit strict flag may return nonzero for findings after the baseline and false-positive rate are understood.

- **Stronger-model verification is optional, not required.**
  - `make lint` runs verification with the default detector model after Ruff passes, so full-codebase advisory output is confirmed without a separate manual verifier command.
  - Keep `--verify-findings` available for direct scoped runs and PR-review workflows that want confirmation. `--verify-model` only selects
    the verification model; when omitted, verification uses the detector model.
  - Verification, when enabled, receives the original input plus Gemini 3.1 Flash Lite findings and may drop or amend findings before output.
  - Verification remains advisory and exits zero with findings, but it is part of the local `make lint` output after Ruff succeeds.

- **Readable custom rule IDs replace `SL00X` codes.**
  - Use `section-header-comments` and `provenance-comments` instead of `SL002` and `SL003`.
  - Keep IDs stable because they become search terms in CI logs and review comments.

- **Development commands define the speed boundary.**
  - `make lint` is the canonical local command contributors run before pushing.
  - `make lint` runs Ruff first, then verified full-codebase style lint only if Ruff succeeds:

    ```make
    lint:  ## Lint with ruff and verified style checks
    	uv run ruff check .
    	uv run python bin/linters/style_lint.py --verify-findings --verbose .
    ```

  - `make lint:custom` remains available for direct unverified advisory style checks:

    ```make
    lint\:custom:  ## Run custom style checks
    	uv run python bin/linters/style_lint.py
    ```

  - `bin/linters/style_lint.py` should also be directly runnable for scoped debugging, for example `uv run python bin/linters/style_lint.py src/evalspec/plugin.py`.
  - Changeset-focused runs use `uv run python bin/linters/style_lint.py --base origin/dev`.
  - `bin/linters/style_lint.py` also supports preflight planning with `uv run python bin/linters/style_lint.py --dry-run`.
  - Dry-run composes with explicit paths, `--base origin/dev`, `--max-lines`, `--verify-findings`, `--verify-model`, and `--verbose`.
  - Dry-run prints the resolved file list plus `files`, `chunks`, `detector_api_calls`, `max_verifier_api_calls`, and `max_total_api_calls` without requiring `GEMINI_API_KEY` or calling Gemini.
  - Default-path and explicit directory scans respect Git ignore rules, so generated local files under ignored directories such as `.venv/`, `.worktrees/`, `tmp/`, `dist/`, and `build/` are not lint targets.
  - Explicitly named Python files are still linted even when ignored, matching the CLI convention that direct file operands are intentional.
  - `detector_api_calls` is exact for the resolved file and chunk plan; `max_verifier_api_calls` and `max_total_api_calls` are upper bounds because verifier execution depends on detector findings.
  - With `--base`, dry-run resolves the changed files exactly, but changed-line filtering inside chunks still depends on the real lint pass.
  - Example preflight commands:

    ```sh
    uv run python bin/linters/style_lint.py --dry-run
    uv run python bin/linters/style_lint.py --dry-run --base origin/dev --verbose
    ```

- **CI policy mirrors the canonical lint command.**
  - This repo does not currently contain `.github/` workflow files, so this spec does not require adding CI as part of the lint implementation.
  - If a required CI workflow is added or already exists in the implementation branch, it should run `make lint` and `make test`.
  - Required CI should run `make lint` if CI is added; custom style findings remain advisory because the script exits zero on findings.
  - A later workflow may parse style-lint output into pull-request review comments, but that is a follow-up decision after the output is tuned.

## Testing Plan

### Logic
- **Ruff configuration reflects the style guide** — selected rule families map to documented preferences and exclude rules that contradict the guide.
- **Custom rule prompting is stable** — each rule is injected from the rule list with a stable ID, description, and expected output schema.
- **Verification stays isolated** — confirmation runs through the verifier path,
  remains advisory, and is also available for direct scoped runs through
  `--verify-findings`.

### Behavior
- **The canonical lint path runs Ruff first** — `make lint` does not run
  verified style lint unless Ruff passes.
- **The slower custom style path is included in `make lint`** — verified
  advisory Gemini 3.1 Flash Lite custom rules run after Ruff and remain
  non-failing on findings.
- **Future required CI matches local lint** — any required CI workflow uses `make lint`, not a bespoke lint command.

### Interface
- **Scripts behave like lint CLIs** — each script accepts repository paths or defaults, prints diagnostics in a predictable format, and exits nonzero only for the mode that is meant to block.

## Documentation Plan

- **docs/style/development.md**: Update only if implementation introduces explicit exceptions or rollout stages that change the documented preference.
- **README.md**: No update needed because contributors still use `make lint`.

## Out of Scope

- Adding pre-commit hooks.
- Building a Ruff plugin.
- Adding required CI solely for this lint change.
- Rewriting the current codebase to satisfy every new lint finding.
- Enforcing subjective design guidance such as DRY boundaries, logging volume, package cohesion, or function extraction.
- Making advisory style findings fail `make lint`.

## References

- `docs/style/development.md:29` — bans lint and type-check suppression comments.
- `docs/style/development.md:32` — identifies section-header comments as a smell.
- `docs/style/development.md:33` — bans comments that reference external context.
- `docs/style/development.md:70` — rejects single-letter names except `_`.
- `docs/style/development.md:88` — requires absolute, grouped, top-of-file imports.
- `docs/style/development.md:191` — prefers linter and formatter driven enforcement.
- `docs/style/development.md:212` — requires `from __future__ import annotations` after the module docstring.
- `docs/style/development.md:215` — requires `textwrap.dedent` for indented multiline strings.
- `pyproject.toml:10` — the project requires Python `>=3.10`.
- `pyproject.toml:44` — Ruff is already a dev dependency.
- `Makefile:21` — `make lint` is already the lint entry point.
- `Makefile:1` — phony targets are explicitly listed and should include new lint targets when added.

## Verification

- `python3 scripts/validate_spec.py docs/specs/2026-07-05-style-lint-rules.md` — validates spec structure if the validator is added to this repo.
- `make lint` — proves Ruff runs first and verified full-codebase style lint runs afterward.
- `make lint:custom` — proves the Gemini 3.1 Flash Lite custom checker remains directly runnable.
- `make test` — proves checker behavior and the existing suite pass together.
