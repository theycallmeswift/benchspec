**TL;DR** - Add `--dry-run` to the advisory style-lint CLI so users can see which Python files would be linted and how many Gemini API calls would be made before spending time or tokens.

## Problem

- **Symptom:** The advisory style linter can now scan default paths, explicit paths, or `--base` changesets, but users cannot preview the resolved file set or request count without actually running model-backed lint.
- **Why it stayed hidden:** Early usage focused on advisory correctness and output stability; cost visibility arrived later through usage metadata after real API calls had already happened.
- **Exposed by:** Repeated 10-run experiments and verbose progress logging made request count, chunk count, and token cost visible only after execution.
- **Scope:** This solves preflight visibility for `bin/linters/style_lint.py` and the reusable `lib/style_lint` file/chunk planning path.
- **Constraint:** `--dry-run` must not require `GEMINI_API_KEY`, must not call Gemini, must preserve advisory zero-exit behavior, and must compose with existing path arguments, `--base`, `--max-lines`, and `--verbose`.

## Solution

```sh
uv run python bin/linters/style_lint.py --dry-run [--base origin/dev] [paths...]
```

`--dry-run` resolves the same target Python files and chunks as a real run, prints the file list plus predicted detector and verifier API-call counts, and exits `0` without calling Gemini. It gives users the cost-shaping facts before they choose whether to run the advisory model check.

## User Stories

1. As a contributor, I want **a preflight list of files**, so I can confirm the linter will inspect the intended scope before running paid model calls.
2. As a contributor, I want **a predicted API-call count**, so I can estimate cost and runtime before running the advisory linter.
3. As a reviewer, I want **dry-run behavior to match real target resolution**, so changeset runs and explicit path runs are debuggable without model credentials.

## Implementation Decisions

```
bin/linters/style_lint.py --dry-run
  -> changed_lines_from_base() / argparse paths
  -> lib.style_lint.collect_python_files()
  -> lib.style_lint.chunk_source_files()
  -> planned detector batches + optional verifier call
  -> stdout dry-run report, no Gemini call
```

- **Add a reusable planning result instead of duplicating path math in the CLI.**
  - `origin/dev:lib/style_lint/source.py` already owns `collect_python_files()` and `chunk_source_files()`.
  - `origin/dev:lib/style_lint/runner.py` already owns `chunk_batch_size`, `files_checked`, and `chunks_checked`.
  - Add a small library function or result type that returns sorted files, chunk count, detector request count, and verifier request count from the same inputs as `StyleLintConfig`.
- **Make `--dry-run` a first-class CLI mode.**
  - `origin/dev:bin/linters/style_lint.py` currently parses positional `paths`, `--base`, `--model`, `--verify-findings`, `--verify-model`, `--max-lines`, and `--verbose`.
  - Add `parser.add_argument("--dry-run", action="store_true")`.
  - When `--dry-run` is set, skip the `GEMINI_API_KEY` check and skip `run_advisory_lint()`.
  - Preserve the existing `--base` behavior where changeset runs imply verifier filtering; the dry-run report should include that verifier call in the planned call count.
- **Report concrete files and planned calls in stable text.**
  - Print one file per line in the same sorted order the linter would use.
  - Print counts for `files`, `chunks`, `detector_api_calls`, `verifier_api_calls`, and `total_api_calls`.
  - Under `--verbose`, keep the existing stderr debug style and include the same selected paths/model context, but do not emit per-file model progress because no model work is happening.
- **Keep API-call prediction simple and explicit.**
  - Detector calls equal `ceil(chunks / chunk_batch_size)`.
  - Verifier calls are `1` only when verification would run and detector calls are nonzero.
  - Total calls equal detector plus verifier.
  - Token counts remain out of scope for dry-run because they require either Gemini `countTokens` calls or an estimator that this CLI does not currently own.

## Testing Plan

### Logic
- **Target planning matches real lint planning** - the dry-run planner resolves default paths, explicit paths, non-Python files, empty files, `--max-lines`, and chunk batching the same way a real advisory lint run does.
- **Call-count prediction is deterministic** - detector, verifier, and total API-call counts are derived from chunk count and verification settings without calling Gemini.

### Behavior
- **Dry-run does not call Gemini** - running with no `GEMINI_API_KEY` still prints the dry-run report and exits `0`.
- **Changeset dry-run honors touched-file scope** - `--dry-run --base <ref>` reports only changed Python files and includes the verifier call implied by `--base`.
- **Verbose dry-run remains diagnostic only** - verbose messages go to stderr while the dry-run report remains on stdout.

### Interface
- **CLI contract is stable** - `--dry-run` composes with existing positional paths, `--base`, `--max-lines`, `--verify-findings`, `--verify-model`, and `--verbose`, and never changes advisory exit semantics.

## Documentation Plan

- **`docs/specs/2026-07-05-style-lint-rules.md`**: Add `--dry-run` to the CLI behavior bullets and example commands.
- **`README.md` or style-lint docs if introduced later**: Document the preflight command once the custom linter has a user-facing section outside the design spec.

## Out of Scope

- Estimating token counts or dollar cost without making API calls.
- Calling Gemini `countTokens` during dry-run.
- Changing the advisory linter's finding rules, verifier rubric, or default scan paths.
- Changing `make lint:custom`; dry-run is an explicit user-invoked preflight mode.

## References

- `origin/dev:bin/linters/style_lint.py` - Owns the repository-specific CLI, default paths, `--base`, `--verbose`, usage output, and advisory zero-exit behavior.
- `origin/dev:lib/style_lint/source.py` - Owns Python file collection and source chunking that dry-run must reuse.
- `origin/dev:lib/style_lint/runner.py` - Owns chunk batching, verifier behavior, usage metadata aggregation, and checked file/chunk counts.
- `origin/dev:tests/lib/style_linter/test_cli.py` - Existing CLI coverage for no-key behavior, `--base`, verbose logging, usage output, and advisory findings.
- `docs/specs/2026-07-05-style-lint-rules.md` - Prior style-lint design and CLI behavior baseline.
- PR #8 - Merged implementation of the advisory style-lint framework and CLI.

## Verification

- `uv run pytest tests/lib/style_linter evals/lib/style_linter -q` - Confirms style-linter library, CLI, and eval behavior.
- `make test` - Confirms the full project test suite.
- `make lint` - Confirms Ruff formatting and lint policy.
- `env -u GEMINI_API_KEY uv run python bin/linters/style_lint.py --dry-run` - Confirms dry-run does not require credentials.
- `uv run python bin/linters/style_lint.py --dry-run --base origin/dev --verbose` - Confirms changeset planning, API-call counts, and verbose dry-run logging.
- `uv run python bin/linters/style_lint.py --base origin/dev --verbose` - Confirms the implemented changes can be analyzed successfully by the live advisory style linter.
