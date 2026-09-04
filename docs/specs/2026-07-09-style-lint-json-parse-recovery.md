**TL;DR** - Make the model-backed style linter recover from malformed JSON responses by extracting valid JSON, retrying transient parse failures, and reporting per-call failures without discarding the whole run.

## Problem

- **Symptom:** A verified style-lint run can end with `warning: advisory style lint skipped due to model error: Extra data: line 4 column 1`, even after earlier chunks completed successfully.
- **Observed command:** `uv run python bin/linters/style_lint.py --verify-findings --verbose <paths...>`.
- **Impact:** One malformed detector or verifier response causes `run_advisory_lint` to return a warning result with no diagnostics, so the user cannot tell whether the reviewed files passed or whether findings were hidden by the parse failure.
- **Current detector parse point:** `lib/style_lint/detector.py` calls `json.loads(response)` directly in `parse_findings`.
- **Current verifier parse point:** `lib/style_lint/verifier.py` calls `json.loads(response.text)` directly in `verify_findings`.
- **Current error policy:** `lib/style_lint/runner.py` catches `ValueError` and `json.JSONDecodeError` around the whole run and returns `advisory style lint skipped due to model error`.
- **Constraint:** The linter remains advisory. Recovery should improve trust in output, not make style findings fail the command.
- **Constraint:** Usage accounting must remain visible even when one request is retried or skipped.

## Solution

```text
Gemini text
  -> parse_json_object()
       -> json.loads(text)
       -> extract one fenced JSON block
       -> extract first balanced JSON object
  -> detector/verifier schema validation
       -> retry parse/schema failures once
       -> keep valid chunks and mark failed chunks
  -> StyleLintResult
       -> diagnostics for valid findings
       -> warning only for partial or exhausted failures
       -> usage includes every attempted request
```

Add a shared JSON response parser for style-lint model output, use it from both detector and verifier paths, and narrow the runner error boundary so one malformed response does not discard findings from unrelated chunks.

## User Stories

1. As a contributor, I want **a transient model JSON formatting error to retry automatically**, so rerunning `make lint` is not the first recovery mechanism.
2. As a maintainer, I want **valid chunk findings preserved when a later chunk fails to parse**, so advisory lint output is useful even on partial model instability.
3. As a maintainer, I want **warnings to identify the failed request scope**, so I can decide whether to rerun a small path instead of the full codebase.
4. As a maintainer, I want **detector and verifier parsing to share one implementation**, so fixes for fenced JSON, extra prose, or duplicate JSON objects apply consistently.

## Implementation Decisions

- **Create a shared parser module under `lib/style_lint/`.**
  - Add a small module such as `lib/style_lint/json_response.py`.
  - Expose `parse_json_object(text: str) -> dict[str, object]`.
  - First try `json.loads(text)` for the ideal `responseMimeType = application/json` path.
  - If direct parsing fails, try a single fenced block whose info string is empty or `json`.
  - If no fenced block parses, scan for the first balanced top-level JSON object and parse that substring.
  - Reject ambiguous output with more than one top-level JSON object instead of guessing, because duplicate objects may represent conflicting model answers.
  - Keep parser errors specific enough to distinguish `empty response`, `multiple JSON objects`, and `no JSON object found`.

- **Use the parser in detector and verifier code.**
  - Replace `json.loads(response)` in `lib/style_lint/detector.py` with `parse_json_object(response)`.
  - Replace `json.loads(response.text)` in `lib/style_lint/verifier.py` with `parse_json_object(response.text)`.
  - Keep existing schema validation after parsing, including detector finding validation and verifier `keep_indexes` validation.
  - Continue to drop malformed individual detector findings when `drop_invalid=True`; JSON recovery is about malformed envelopes, not relaxing finding schema.

- **Retry transient model parse/schema failures at the request boundary.**
  - Add a retry helper around each Gemini detector call in `lib/style_lint/runner.py`.
  - Retry once for JSON parse errors and response-schema `ValueError`s from detector parsing.
  - Retry once for verifier JSON parse errors and verifier response-schema `ValueError`s.
  - Include usage from failed attempts when Gemini returned usage metadata.
  - Do not retry deterministic local errors such as file collection, Unicode decoding, or invalid source chunk construction.

- **Make failures partial instead of whole-run skips where possible.**
  - Move the detector `try` block inside the chunk-batch loop.
  - If a detector chunk batch still fails after retry, append a warning that names the files and line ranges in that batch, then continue to the next batch.
  - If verifier still fails after retry, keep unverified detector findings but mark the result warning as `verification skipped due to model error`.
  - Reserve the existing whole-run warning for setup failures before chunks are available.

- **Extend result shape without breaking callers.**
  - Keep `StyleLintResult.warning: str | None` for CLI compatibility.
  - Add warnings as newline-joined text or introduce `warnings: list[str]` while preserving `warning` as a compatibility property.
  - CLI output should print diagnostics first, then warning lines, then usage.
  - Dry-run output is unchanged.

- **Keep advisory exit semantics unchanged.**
  - The CLI still exits zero for findings and for partial model warnings.
  - A future strict mode can decide whether partial model failures should be blocking; this spec does not add strict mode.

## Testing Plan

### Logic
- **JSON parser accepts common recoverable envelopes** - direct JSON, fenced JSON, and prose-wrapped single JSON objects parse to the same object.
- **JSON parser rejects ambiguous envelopes** - duplicate top-level JSON objects and non-object payloads raise clear errors.
- **Detector and verifier share parser behavior** - both paths handle the same malformed model envelope classes.
- **Retry accounting is deterministic** - retries happen only for model response parse/schema failures and usage includes every attempted request.

### Behavior
- **One bad detector chunk does not erase good chunks** - diagnostics from successful chunks remain visible and the failed chunk is named in a warning.
- **Verifier parse failure does not hide detector findings** - detector findings remain visible with a verification warning.
- **Repeated transient parse failure is bounded** - each model request has a fixed retry limit and cannot loop indefinitely.

### Interface
- **CLI output remains lint-like** - diagnostics keep `path:line:column: rule-id message` format, warnings are explicit, and usage is still printed.
- **Existing advisory contract remains intact** - the command exits zero for findings and partial model warnings.

## Documentation Plan

- **Style-lint spec:** Update `docs/specs/2026-07-05-style-lint-rules.md` only if implementation changes the public CLI contract beyond warning wording.
- **README:** No update needed unless warning output becomes part of documented contributor workflow.
- **Inline docs:** Add docstrings for the shared JSON response parser and retry helper.

## Out of Scope

- Changing the default Gemini model.
- Making model-backed lint findings or model transport failures blocking.
- Replacing Gemini REST transport with an SDK.
- Persisting raw model responses to disk.
- Solving non-JSON semantic false positives from the style-lint rules.

## References

- `lib/style_lint/detector.py:112` - detector currently parses model text with `json.loads(response)`.
- `lib/style_lint/verifier.py:108` - verifier currently parses model text with `json.loads(response.text)`.
- `lib/style_lint/runner.py:169` - runner catches JSON and schema errors around the whole advisory run.
- `lib/style_lint/runner.py:210` - current warning says the advisory style lint was skipped due to model error.
- `lib/style_lint/gemini.py:44` - Gemini requests already set `responseMimeType` to `application/json`.
- `bin/linters/style_lint.py:151` - CLI prints advisory warning results and usage.

## Verification

- `python3 scripts/validate_spec.py docs/specs/2026-07-09-style-lint-json-parse-recovery.md` if the validator exists in the checkout; otherwise self-check the required spec sections.
- `uv run ruff check lib/style_lint bin/linters/style_lint.py tests/lib/style_linter`.
- `uv run pytest tests/lib/style_linter`.
- `uv run python bin/linters/style_lint.py --verify-findings --verbose <fixture paths>` with a stubbed or recorded malformed JSON response.
- `make lint` once the parser and retry behavior are implemented.
