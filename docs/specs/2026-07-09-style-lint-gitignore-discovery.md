**TL;DR** - Make style-lint file discovery honor `.gitignore` so full-project runs do not lint ignored virtualenvs, worktrees, caches, builds, or other generated Python files.

## Problem

- **Symptom:** `uv run python bin/linters/style_lint.py --dry-run --verbose .` scans every `*.py` under the repository root with `Path.rglob("*.py")`, including files that Git explicitly ignores.
- **Exposed by:** Full-project dry-run usage after PR #12 made the resolved file list visible before model calls.
- **Scope:** This covers default-path and explicit-path discovery in `lib/style_lint/source.py`; changed-file runs through `--base` already come from `git diff`.
- **Constraint:** Discovery must continue to work for existing callers, must not require model credentials, and must preserve stable sorted output for dry-run reports and tests.

## Solution

```sh
uv run python bin/linters/style_lint.py --dry-run --verbose .
```

The command should report Python files that are inside the requested roots and are not ignored by Git. The shared collection path should filter ignored files before chunking so dry-run and real lint runs resolve the same target set.

## User Stories

1. As a contributor, I want **full-project style lint to skip ignored files**, so generated or local-only Python code does not consume model calls.
2. As a contributor, I want **dry-run and real lint to use the same ignored-file filter**, so preflight output accurately predicts the files that would be linted.
3. As a maintainer, I want **the ignore behavior centralized in source collection**, so CLI and library behavior do not drift.

## Implementation Decisions

```
bin/linters/style_lint.py paths
  -> lib.style_lint.source.collect_python_files()
  -> git ignore filtering for discovered Python files
  -> lib.style_lint.runner.run_advisory_lint()
  -> dry-run plan or model-backed lint
```

- **Filter at the collection boundary.** `lib/style_lint/source.py` currently owns `collect_python_files()` and is the shared source of truth for dry-run and real lint target resolution.
- **Use Git's ignore engine instead of reimplementing patterns.** `.gitignore` includes repository-specific ignores such as `.venv/`, `.worktrees/`, cache directories, `tmp/`, `dist/`, `build/`, and `*.egg-info/`; shell or Python pattern matching would drift from Git semantics.
- **Keep non-Git callers predictable.** If the repository is unavailable or Git cannot answer ignore checks, collection should fall back to the existing filesystem scan rather than fail the advisory linter.
- **Do not change changeset scope.** `bin/linters/style_lint.py` builds `--base` scope from `git diff --unified=0 BASE_REF -- "*.py"`, so ignored untracked files are already outside that path.
- **Preserve deterministic output.** The final collected paths should remain sorted absolute paths so dry-run reports and existing tests stay stable.

## Testing Plan

### Logic
- **Ignored files are excluded** - Python files under ignored directories or matching ignored patterns are absent from collected targets.
- **Tracked and unignored files remain included** - requested Python files that are not ignored still appear in sorted target output.
- **Fallback behavior is advisory** - if Git ignore evaluation is unavailable, collection still returns filesystem-discovered Python files rather than aborting.

### Behavior
- **Full-project dry-run respects `.gitignore`** - running a dry-run against `.` does not list ignored virtualenv, worktree, cache, build, or temporary Python files.
- **Real lint uses the same scope as dry-run** - model-backed lint chunks the same non-ignored file set reported by dry-run.

### Interface
- **CLI arguments are unchanged** - users continue to pass positional paths, `--base`, `--dry-run`, `--max-lines`, and `--verbose` the same way, with only ignored files removed from explicit/default directory scans.

## Documentation Plan

- **`docs/specs/2026-07-05-style-lint-rules.md`**: Document that style-lint discovery respects `.gitignore` for default and explicit directory scans.

## Out of Scope

- Changing advisory style rules, prompts, verifier behavior, or model selection.
- Adding include or exclude CLI flags beyond existing positional paths and `--base`.
- Applying `.gitignore` filtering to files explicitly returned by `git diff` for `--base`.
- Supporting non-Python files.

## References

- `bin/linters/style_lint.py` - Owns positional paths, default paths, `--base`, `--dry-run`, and verbose CLI behavior.
- `lib/style_lint/source.py` - Owns `collect_python_files()` and currently scans directories with `Path.rglob("*.py")`.
- `.gitignore` - Defines ignored local and generated paths that full-project lint should skip.
- `docs/specs/2026-07-08-style-lint-dry-run.md` - Defines dry-run as a faithful preview of real target resolution.
- PR #12 - Added dry-run output that exposed the current ignored-file discovery problem.

## Verification

- `uv run pytest tests/lib/style_linter -q` - Confirms style-linter collection, planning, and CLI behavior.
- `make test` - Confirms the full project test suite.
- `make lint` - Confirms Ruff formatting and lint policy.
- `uv run python bin/linters/style_lint.py --dry-run --verbose .` - Confirms full-project dry-run output excludes ignored Python files.
