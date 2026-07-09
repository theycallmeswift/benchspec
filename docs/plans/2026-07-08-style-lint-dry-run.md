# Style Lint Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--dry-run` to the advisory style-lint CLI so contributors can preview the exact Python files, chunk count, and planned Gemini detector and verifier call counts without requiring `GEMINI_API_KEY` or calling Gemini.

**Architecture:** Reuse the real lint planning path in `lib/style_lint` by factoring file collection, chunking, and detector batch counting into a reusable runner-level planning function. Keep `bin/linters/style_lint.py` responsible for CLI parsing, `--base` resolution, verbose logging, and dry-run report formatting so the library stays generic and the repo-specific script owns user-facing output.

**Tech Stack:** Python, pytest, uv, Makefile.

---

## File Map

- `lib/style_lint/runner.py` — add a reusable dry-run planning result and planner function that reuses `collect_python_files()`, `chunk_source_files()`, and `_chunk_batches()`.
- `lib/style_lint/__init__.py` — export the new planner API so the CLI and tests can import it through `lib.style_lint`.
- `tests/lib/style_linter/test_runner.py` — cover planner behavior for chunk batching and verifier-call prediction without any Gemini transport.
- `bin/linters/style_lint.py` — add `--dry-run`, skip `GEMINI_API_KEY` enforcement in dry-run mode, print the dry-run report, and keep existing `--base`, `--max-lines`, `--verify-findings`, `--verify-model`, and `--verbose` behavior intact.
- `tests/lib/style_linter/test_cli.py` — cover dry-run stdout, `--base` composition, verbose stderr, and the guarantee that Gemini is never called in dry-run mode.
- `docs/specs/2026-07-05-style-lint-rules.md` — document the new preflight mode in the CLI behavior bullets and example commands.

### Task 1: Add a Reusable Dry-Run Planner in `lib/style_lint`

**Files:**
- Modify: `lib/style_lint/runner.py`
- Modify: `lib/style_lint/__init__.py`
- Test: `tests/lib/style_linter/test_runner.py`

- [ ] **Step 1: Write the failing runner tests**

Add these tests to `tests/lib/style_linter/test_runner.py`:

```python
def test_build_lint_plan_predicts_detector_and_verifier_calls(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("one = 1\ntwo = 2\nthree = 3\n")

    plan = framework.build_lint_plan(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            verify_findings=True,
            max_lines=1,
            chunk_batch_size=2,
        )
    )

    assert plan.files == [source.resolve()]
    assert plan.files_checked == 1
    assert plan.chunks_checked == 3
    assert plan.detector_api_calls == 2
    assert plan.verifier_api_calls == 1
    assert plan.total_api_calls == 3


def test_build_lint_plan_skips_verifier_when_no_chunks(
    tmp_path: Path,
    framework: ModuleType,
) -> None:
    source = tmp_path / "empty.py"
    source.write_text("")

    plan = framework.build_lint_plan(
        framework.StyleLintConfig(
            paths=[source],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            verify_findings=True,
        )
    )

    assert plan.files == [source.resolve()]
    assert plan.files_checked == 1
    assert plan.chunks_checked == 0
    assert plan.detector_api_calls == 0
    assert plan.verifier_api_calls == 0
    assert plan.total_api_calls == 0
```

- [ ] **Step 2: Run the runner tests to verify they fail**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py -k build_lint_plan -v
```

Expected: FAIL with `AttributeError` because `build_lint_plan` is not defined or exported yet.

- [ ] **Step 3: Add the planner result and implementation**

Update `lib/style_lint/runner.py` and `lib/style_lint/__init__.py` with these additions:

```python
@dataclass(frozen=True)
class StyleLintPlan:
    """Planned work for one advisory lint run without model calls."""

    files: list[Path]
    files_checked: int
    chunks_checked: int
    detector_api_calls: int
    verifier_api_calls: int
    total_api_calls: int


def build_lint_plan(config: StyleLintConfig) -> StyleLintPlan:
    """Plan file, chunk, and API-call counts for an advisory lint run."""
    targets = collect_python_files(
        config.paths,
        default_paths=config.default_paths,
    )
    chunks = chunk_source_files(targets, max_lines=config.max_lines)
    detector_api_calls = len(_chunk_batches(chunks, size=config.chunk_batch_size))
    verifier_api_calls = 1 if config.verify_findings and detector_api_calls > 0 else 0

    return StyleLintPlan(
        files=targets,
        files_checked=len(targets),
        chunks_checked=len(chunks),
        detector_api_calls=detector_api_calls,
        verifier_api_calls=verifier_api_calls,
        total_api_calls=detector_api_calls + verifier_api_calls,
    )
```

```python
from lib.style_lint.runner import (
    StyleLintConfig,
    StyleLintPlan,
    StyleLintResult,
    build_lint_plan,
    run_advisory_lint,
)
```

```python
__all__ = [
    "StyleLintConfig",
    "StyleLintPlan",
    "StyleLintResult",
    "build_lint_plan",
    "run_advisory_lint",
]
```

- [ ] **Step 4: Run the runner tests to verify they pass**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py -k build_lint_plan -v
```

Expected: PASS with both new planner tests green.

- [ ] **Step 5: Commit the library planner**

Run:

```bash
git add lib/style_lint/runner.py lib/style_lint/__init__.py tests/lib/style_linter/test_runner.py
git commit -m "feat: add style lint dry-run planner"
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
        "verifier_api_calls: 0\n"
        "total_api_calls: 1\n"
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
        "verifier_api_calls: 1\n"
        "total_api_calls: 2\n"
    )
    assert "Using paths:" in captured.err
    assert "Using model:" in captured.err
    assert "Using verifier model:" in captured.err
    assert "Dry run:" in captured.err
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
def print_dry_run_report(plan: object) -> None:
    """Print the planned file set and model-call counts."""
    if not isinstance(plan, style_lint.StyleLintPlan):
        raise TypeError("plan must be a StyleLintPlan")

    for path in plan.files:
        print(path)
    print(f"files: {plan.files_checked}")
    print(f"chunks: {plan.chunks_checked}")
    print(f"detector_api_calls: {plan.detector_api_calls}")
    print(f"verifier_api_calls: {plan.verifier_api_calls}")
    print(f"total_api_calls: {plan.total_api_calls}")
```

```python
parser.add_argument("--dry-run", action="store_true")
```

```python
if args.dry_run:
    plan = style_lint.build_lint_plan(
        style_lint.StyleLintConfig(
            paths=paths,
            default_paths=DEFAULT_PATHS,
            rules=RULES,
            policy_instructions=POLICY_INSTRUCTIONS,
            api_key="",
            model=args.model,
            changed_lines=changed_lines,
            verify_findings=args.verify_findings or args.base is not None,
            verify_model=args.verify_model,
            max_lines=args.max_lines,
            progress_callback=None,
        )
    )
    verbose_log(
        args.verbose,
        (
            "Dry run: "
            f"{plan.files_checked} files, "
            f"{plan.chunks_checked} chunks, "
            f"{plan.total_api_calls} planned API calls"
        ),
    )
    print_dry_run_report(plan)
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

### Task 3: Update the Style-Lint Spec and Run Repo Verification

**Files:**
- Modify: `docs/specs/2026-07-05-style-lint-rules.md`
- Modify: `docs/plans/2026-07-08-style-lint-dry-run.md`

- [ ] **Step 1: Update the style-lint rules spec**

Add the dry-run behavior to `docs/specs/2026-07-05-style-lint-rules.md` in the CLI behavior section:

```markdown
- `bin/linters/style_lint.py` also supports preflight planning with
  `uv run python bin/linters/style_lint.py --dry-run`.
- Dry-run composes with explicit paths, `--base origin/dev`, `--max-lines`,
  `--verify-findings`, `--verify-model`, and `--verbose`.
- Dry-run prints the resolved file list plus `files`, `chunks`,
  `detector_api_calls`, `verifier_api_calls`, and `total_api_calls` without
  requiring `GEMINI_API_KEY` or calling Gemini.
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

Expected: both commands exit `0`; stdout lists files and planned call counts; stderr only contains Ruff-style debug logs for the verbose command.

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
git add docs/specs/2026-07-05-style-lint-rules.md docs/plans/2026-07-08-style-lint-dry-run.md
git commit -m "docs: document style lint dry-run"
```
