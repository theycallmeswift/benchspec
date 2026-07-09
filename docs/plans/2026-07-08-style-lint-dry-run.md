# Style Lint Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--dry-run` to the advisory style-lint CLI so contributors can preview the exact Python files, exact chunk count, exact detector batch count, and upper-bound verifier and total Gemini call counts without requiring `GEMINI_API_KEY` or calling Gemini.

**Dry-run contract:** Dry-run matches real file collection, chunk planning, and detector batch counting. With `--base`, the planned paths are the changed files returned by `changed_lines_from_base()`. Dry-run does not promise exact changed-line filtering coverage inside chunks or exact verifier call counts, because those depend on real detector output and `lib/style_lint/verifier.py` returns before calling Gemini when detector findings are empty. Expose `detector_api_calls` as an exact count, and `max_verifier_api_calls` plus `max_total_api_calls` as upper bounds.

**Architecture:** Reuse the real lint planning path in `lib/style_lint` by factoring file collection, chunking, and detector batch counting into a reusable runner-level planning function. Keep `bin/linters/style_lint.py` responsible for CLI parsing, `--base` resolution, verbose logging, and dry-run report formatting so the library stays generic and the repo-specific script owns user-facing output.

**Tech Stack:** Python, pytest, uv, Makefile.

---

## File Map

- `lib/style_lint/runner.py` — add dry-run support to `run_advisory_lint()` through `StyleLintConfig.dry_run`, returning a `StyleLintPlan` after collection/chunking/batching and before model execution.
- `lib/style_lint/__init__.py` — export `StyleLintPlan` without adding a separate planner method.
- `tests/lib/style_linter/test_runner.py` — cover dry-run behavior on the existing runner path, including no Gemini calls and verifier-call upper-bound prediction.
- `bin/linters/style_lint.py` — add `--dry-run`, skip `GEMINI_API_KEY` enforcement in dry-run mode, print the dry-run report, and keep existing `--base`, `--max-lines`, `--verify-findings`, `--verify-model`, and `--verbose` behavior intact.
- `tests/lib/style_linter/test_cli.py` — cover dry-run stdout, `--base` composition, verbose stderr, and the guarantee that Gemini is never called in dry-run mode.
- `docs/specs/2026-07-05-style-lint-rules.md` — document the new preflight mode in the CLI behavior bullets and example commands.
- `docs/specs/2026-07-08-style-lint-dry-run.md` — update the issue spec so its dry-run contract and output fields match the implementation plan.

### Task 1: Add Dry-Run Mode to the Reusable Linter Runner

**Files:**
- Modify: `lib/style_lint/runner.py`
- Modify: `lib/style_lint/__init__.py`
- Test: `tests/lib/style_linter/test_runner.py`

- [ ] **Step 1: Write the failing runner tests**

Add these tests to `tests/lib/style_linter/test_runner.py`:

```python
def test_run_advisory_lint_dry_run_returns_plan_without_model_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")
    monkeypatch.setattr(
        framework,
        "call_gemini",
        lambda **_kwargs: pytest.fail("dry-run must not call Gemini"),
    )

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
            verify_findings=True,
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert result.warning is None
    assert result.diagnostics == []
    assert result.findings == []
    assert result.plan == framework.StyleLintPlan(
        files=[source.resolve()],
        files_checked=1,
        chunks_checked=3,
        detector_api_calls=2,
        max_verifier_api_calls=1,
        max_total_api_calls=3,
    )


def test_run_advisory_lint_dry_run_skips_verifier_when_no_chunks(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    source = tmp_path / "empty.py"
    source.write_text("")

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
            verify_findings=True,
        )
    )

    assert result.plan == framework.StyleLintPlan(
        files=[source.resolve()],
        files_checked=1,
        chunks_checked=0,
        detector_api_calls=0,
        max_verifier_api_calls=0,
        max_total_api_calls=0,
    )
```

- [ ] **Step 2: Run the runner tests to verify they fail**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py -k dry_run -v
```

Expected: FAIL because `StyleLintConfig` does not accept `dry_run` yet.

- [ ] **Step 3: Add the dry-run result and runner branch**

Update `lib/style_lint/runner.py` and `lib/style_lint/__init__.py` with these additions:

```python
@dataclass(frozen=True)
class StyleLintPlan:
    """Planned work for one advisory lint run without model calls."""

    files: list[Path]
    files_checked: int
    chunks_checked: int
    detector_api_calls: int
    max_verifier_api_calls: int
    max_total_api_calls: int
```

```python
@dataclass(frozen=True)
class StyleLintResult:
    """Result from an advisory lint run."""

    findings: list[Finding]
    diagnostics: list[str]
    warning: str | None = None
    usage: UsageMetadata = field(default_factory=UsageMetadata)
    files_checked: int = 0
    chunks_checked: int = 0
    plan: StyleLintPlan | None = None
```

Add `dry_run: bool = False` to `StyleLintConfig`. Inside `run_advisory_lint()`, keep the normal setup path:

```python
    targets = collect_python_files(
        config.paths,
        default_paths=config.default_paths,
    )
    chunks = chunk_source_files(targets, max_lines=config.max_lines)
    chunk_batches = _chunk_batches(chunks, size=config.chunk_batch_size)
```

Then, before detector calls, return a plan when `config.dry_run` is enabled:

```python
if config.dry_run:
    detector_api_calls = len(chunk_batches)
    max_verifier_api_calls = (
        1 if config.verify_findings and detector_api_calls > 0 else 0
    )
    plan = StyleLintPlan(
        files=targets,
        files_checked=len(targets),
        chunks_checked=len(chunks),
        detector_api_calls=detector_api_calls,
        max_verifier_api_calls=max_verifier_api_calls,
        max_total_api_calls=detector_api_calls + max_verifier_api_calls,
    )

    return StyleLintResult(
        findings=[],
        diagnostics=[],
        files_checked=plan.files_checked,
        chunks_checked=plan.chunks_checked,
        plan=plan,
    )
```

Add `StyleLintPlan` to the existing `lib/style_lint/__init__.py` import list and `__all__` list. Do not add a separate planner method.

- [ ] **Step 4: Run the runner tests to verify they pass**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py -k dry_run -v
```

Expected: PASS with both new planner tests green.

- [ ] **Step 5: Commit the library planner**

Run:

```bash
git add lib/style_lint/runner.py lib/style_lint/__init__.py tests/lib/style_linter/test_runner.py
git commit -m "feat: add style lint dry-run runner mode"
```

### Task 2: Wire `--dry-run` Into the CLI Without Gemini Calls

**Files:**
- Modify: `bin/linters/style_lint.py`
- Test: `tests/lib/style_linter/test_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

Add these tests to `tests/lib/style_linter/test_cli.py`:

```python
def test_cli_dry_run_prints_files_and_planned_api_calls_without_gemini_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    exit_code = style_lint_cli.main(["--dry-run", "--max-lines", "1", str(source)])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == (
        f"{source.resolve()}\n"
        "files: 1\n"
        "chunks: 3\n"
        "detector_api_calls: 1\n"
        "max_verifier_api_calls: 0\n"
        "max_total_api_calls: 1\n"
    )
    assert captured.err == ""


def test_cli_dry_run_uses_base_scope_and_never_calls_gemini(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    source = (tmp_path / "changed.py").resolve()
    source.write_text("one = 1\ntwo = 2\n")
    changed_lines = {source: {2}}

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        style_lint_cli,
        "changed_lines_from_base",
        lambda ref: changed_lines,
    )
    monkeypatch.setattr(
        style_lint_cli.style_lint,
        "call_gemini",
        lambda **_kwargs: pytest.fail("dry-run must not call Gemini"),
    )

    exit_code = style_lint_cli.main(["--dry-run", "--base", "origin/dev", "--verbose"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == (
        f"{source}\n"
        "files: 1\n"
        "chunks: 1\n"
        "detector_api_calls: 1\n"
        "max_verifier_api_calls: 1\n"
        "max_total_api_calls: 2\n"
    )
    assert "Using paths:" in captured.err
    assert "Using model:" in captured.err
    assert "Using verifier model:" in captured.err
    assert "Dry run summary:" in captured.err
```

- [ ] **Step 2: Run the CLI tests to verify they fail**

Run:

```bash
uv run pytest tests/lib/style_linter/test_cli.py -k dry_run -v
```

Expected: FAIL because `argparse` does not accept `--dry-run` yet.

- [ ] **Step 3: Implement CLI dry-run mode**

Update `bin/linters/style_lint.py` with these concrete changes:

```python
parser.add_argument("--dry-run", action="store_true")
```

```python
verify_findings = args.verify_findings or args.base is not None
if args.verbose:
    verbose_log(args.verbose, f"Using paths: {format_paths(paths)}")
    verbose_log(args.verbose, f"Using model: {args.model}")
    if verify_findings:
        verbose_log(args.verbose, f"Using verifier model: {args.verify_model or args.model}")

if args.dry_run:
    result = run(
        paths,
        model=args.model,
        changed_lines=changed_lines,
        verify_findings=verify_findings,
        verify_model=args.verify_model,
        max_lines=args.max_lines,
        verbose=args.verbose,
        dry_run=True,
    )
    plan = result.plan
    verbose_log(
        args.verbose,
        (
            "Dry run summary: "
            f"{plan.files_checked} files, "
            f"{plan.chunks_checked} chunks, "
            f"{plan.detector_api_calls} detector calls, "
            f"{plan.max_verifier_api_calls} max verifier calls, "
            f"{plan.max_total_api_calls} max total calls"
        ),
    )
    return 0
```

Keep the existing `run()` path unchanged for non-dry-run execution, including the current `GEMINI_API_KEY` skip behavior.

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run:

```bash
uv run pytest tests/lib/style_linter/test_cli.py -k dry_run -v
```

Expected: PASS with both new dry-run CLI tests green.

- [ ] **Step 5: Run the focused style-linter suite**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py tests/lib/style_linter/test_cli.py -v
```

Expected: PASS with existing runner and CLI tests still green.

- [ ] **Step 6: Commit the CLI dry-run mode**

Run:

```bash
git add bin/linters/style_lint.py tests/lib/style_linter/test_cli.py tests/lib/style_linter/test_runner.py lib/style_lint/runner.py lib/style_lint/__init__.py
git commit -m "feat: add dry-run mode to style lint"
```

### Task 3: Update the Style-Lint Specs and Run Repo Verification

**Files:**
- Modify: `docs/specs/2026-07-05-style-lint-rules.md`
- Modify: `docs/specs/2026-07-08-style-lint-dry-run.md`
- Modify: `docs/plans/2026-07-08-style-lint-dry-run.md`

- [ ] **Step 1: Update the style-lint specs**

Add the dry-run behavior to both `docs/specs/2026-07-05-style-lint-rules.md` and `docs/specs/2026-07-08-style-lint-dry-run.md` so the issue spec and rules spec match the implementation contract:

```markdown
- `bin/linters/style_lint.py` also supports preflight planning with
  `uv run python bin/linters/style_lint.py --dry-run`.
- Dry-run composes with explicit paths, `--base origin/dev`, `--max-lines`,
  `--verify-findings`, `--verify-model`, and `--verbose`.
- Dry-run prints the resolved file list plus `files`, `chunks`,
  `detector_api_calls`, `max_verifier_api_calls`, and `max_total_api_calls`
  without requiring `GEMINI_API_KEY` or calling Gemini.
- `detector_api_calls` is exact for the resolved file/chunk plan.
- `max_verifier_api_calls` and `max_total_api_calls` are upper bounds because
  verifier execution depends on detector findings, and empty findings short-
  circuit before any verifier Gemini call.
- With `--base`, dry-run resolves the changed files exactly, but changed-line
  filtering inside chunks still depends on the real lint pass.
```

Update the existing example command block to include:

```bash
uv run python bin/linters/style_lint.py --dry-run
uv run python bin/linters/style_lint.py --dry-run --base origin/dev --verbose
```

- [ ] **Step 2: Run the documented dry-run commands**

Run:

```bash
env -u GEMINI_API_KEY uv run python bin/linters/style_lint.py --dry-run
env -u GEMINI_API_KEY uv run python bin/linters/style_lint.py --dry-run --base origin/dev --verbose
```

Expected: both commands exit `0`; stdout lists files and planned call counts; stderr for the verbose command includes `Using paths:`, `Using model:`, `Using verifier model:` when verification is enabled, and `Dry run summary:`.

- [ ] **Step 3: Run the repository verification commands**

Run:

```bash
make test
make lint
```

Expected: the full test suite passes and Ruff prints `All checks passed!`.

- [ ] **Step 4: Commit the documentation update**

Run:

```bash
git add docs/specs/2026-07-05-style-lint-rules.md docs/specs/2026-07-08-style-lint-dry-run.md docs/plans/2026-07-08-style-lint-dry-run.md
git commit -m "docs: document style lint dry-run"
```
