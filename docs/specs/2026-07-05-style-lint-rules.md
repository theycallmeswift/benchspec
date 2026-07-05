**TL;DR** — Add commented Ruff configuration for fast `make lint`, plus one `make lint:custom` target for slower deterministic repo-specific style checks.

## Problem

- **Symptom:** `make lint` currently runs `uv run ruff check .`, but `pyproject.toml` has no Ruff rule configuration and no local policy checks.
- **Exposed by:** Review of `docs/style/development.md` found preferences that split into Ruff-native rules and simple custom checks.
- **Scope:** Add executable lint coverage for Python style preferences where automation is practical, while keeping `make lint` fast and deterministic.
- **Constraint:** Ruff rule codes must have inline comments because terse rule families are not self-descriptive.
- **Constraint:** Local lint scripts live under `bin/linters/`.

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

Add the Ruff configuration above, add `bin/linters/style_lint.py` for slower deterministic local style rules, and run that script under `make lint:custom` outside the default `make lint` fast path.

## User Stories

1. As a maintainer, I want **Ruff to encode the mechanical style rules**, so review does not spend time on preferences a tool can catch.
2. As a contributor, I want **lint rule codes explained inline**, so I can understand project policy without cross-referencing every Ruff code.
3. As a maintainer, I want **custom checks represented by readable rule IDs**, so diagnostics describe the preference instead of exposing opaque `SL00X` names.
4. As a contributor, I want **one fast required lint command and one slower custom lint command**, so I know what must pass before pushing and where to run repo-specific style checks.

## Implementation Decisions

```text
docs/style/development.md
  ├─► pyproject.toml [tool.ruff.*] ─────────────► make lint ───────► future required CI
  └─► bin/linters/style_lint.py ───────────────► make lint:custom ─► slower style review
```

- **Ruff configuration is the deterministic base.**
  - Use `ruff 0.15.20` semantics, which is the locally installed Ruff version when this spec was written.
  - Configure `target-version = "py310"` because `pyproject.toml` requires Python `>=3.10`.
  - Add the `select` list exactly as shown in the Solution section, keeping inline comments beside every rule family.
  - Configure Google docstrings with `[tool.ruff.lint.pydocstyle] convention = "google"`.
  - Avoid arbitrary size and complexity limits such as `C901`, `PLR0912`, and `PLR0915` because `docs/style/development.md` rejects line-count-based structure rules.
  - Do not rely on Ruff `DOC` preview rules in the first rollout; docstring existence and Google-style shape are enough for the initial deterministic pass.

- **`bin/linters/style_lint.py` owns slower deterministic local rules Ruff cannot express cleanly.**
  - Define rules in a list so future checks are additive:

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
            description="Use textwrap.dedent for indented multiline strings.",
        ),
    ]
    ```

  - `no-suppression-comments` flags `# noqa`, `# type: ignore`, `# pyright: ignore`, `# pylint: disable`, `# ruff: noqa`, and `# mypy:` comments.
  - `section-header-comments` flags comments that are only visual dividers or region labels, such as `# --- parsing ---`, `# Validation`, or `# Setup`, while allowing comments that explain rationale or landmines.
  - `provenance-comments` flags comments referencing external history or stale context, including `PR #123`, `issue #123`, `fixes #123`, `commit abc123`, `added for`, `called from`, and `see docs/...`.
  - `descriptive-names` uses AST bindings to flag single-letter argument names, assignment targets, loop targets, lambda parameters, and comprehension targets, while allowing `_`.
  - `dedented-multiline-strings` uses tokens plus parent/ancestor context to flag indented triple-quoted strings that are not passed through `textwrap.dedent`.
  - Diagnostics use `path:line:col: rule-id message`; default paths are `src`, `tests`, and `evals`, with optional path arguments for scoped runs.
  - Exit nonzero when findings exist because direct and `make lint:custom` runs should be actionable, but do not wire this script into `make lint` in the first rollout.

- **Readable custom rule IDs replace `SL00X` codes.**
  - Use `section-header-comments` and `provenance-comments` instead of `SL002` and `SL003`.
  - Keep IDs stable because they become search terms in CI logs and review comments.

- **Development commands define the speed boundary.**
  - `make lint` is the fast blocking local command contributors run before pushing.
  - `make lint` runs Ruff only:

    ```make
    lint:  ## Run fast Ruff lint checks
    	uv run ruff check .
    ```

  - `make lint:custom` runs slower deterministic custom style checks:

    ```make
    lint\:custom:  ## Run custom style checks
    	uv run python bin/linters/style_lint.py
    ```

  - `bin/linters/style_lint.py` should also be directly runnable for scoped debugging, for example `uv run python bin/linters/style_lint.py src/evalspec/plugin.py`.

- **CI policy mirrors the fast blocking command.**
  - This repo does not currently contain `.github/` workflow files, so this spec does not require adding CI as part of the lint implementation.
  - If a required CI workflow is added or already exists in the implementation branch, it should run `make lint` and `make test`.
  - Required CI should not run `make lint:custom` in the first rollout; keep the required gate fast until the baseline and false-positive rate are known.
  - A later non-required workflow may run `make lint:custom` on pull requests and publish review comments, but that is a follow-up decision after the output is tuned.

## Testing Plan

### Logic
- **Ruff configuration reflects the style guide** — selected rule families map to documented preferences and exclude rules that contradict the guide.
- **Custom rule matching is stable** — each simple rule reports deterministic locations for matching source and accepts documented non-matches.

### Behavior
- **The canonical lint path stays fast** — `make lint` runs Ruff only.
- **The slower custom style path stays explicit** — `make lint:custom` runs local deterministic custom rules outside the required fast path.
- **Future required CI matches local fast lint** — any required CI workflow uses `make lint`, not a bespoke lint command.

### Interface
- **Scripts behave like lint CLIs** — each script accepts repository paths or defaults, prints diagnostics in a predictable format, and exits nonzero only for the mode that is meant to block.

## Documentation Plan

- **docs/style/development.md**: Update only if implementation introduces explicit exceptions or rollout stages that change the documented preference.
- **README.md**: Update only if contributor workflow gains a command beyond existing `make lint`.

## Out of Scope

- Adding pre-commit hooks.
- Building a Ruff plugin.
- Adding required CI solely for this lint change.
- Rewriting the current codebase to satisfy every new lint finding.
- Enforcing subjective design guidance such as DRY boundaries, logging volume, package cohesion, or function extraction.

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
- `make lint` — proves Ruff configuration is wired into the canonical fast lint target.
- `make lint:custom` — proves `bin/linters/style_lint.py` is runnable without being part of blocking lint.
- `make test` — proves checker behavior and the existing suite pass together.
