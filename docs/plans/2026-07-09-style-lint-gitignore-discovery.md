# Style Lint Gitignore Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make advisory style-lint directory discovery exclude Python files ignored by Git while preserving deterministic fallback behavior outside Git repositories.

**Architecture:** Keep ignore handling inside `lib/style_lint/source.py`, the shared collection boundary used by dry-run and real lint. Use `git check-ignore --stdin` for Git semantics, and fall back to the existing sorted filesystem scan when Git ignore evaluation is unavailable.

**Tech Stack:** Python `pathlib`, `subprocess`, pytest, existing `lib.style_lint` runner and CLI.

---

## File Structure

- Modify `lib/style_lint/source.py`: collect Python files, call Git ignore evaluation once per collection, and keep sorted absolute paths.
- Modify `tests/lib/style_linter/test_source.py`: cover ignored directory files, tracked or unignored files, explicit file behavior, and Git fallback behavior.
- Modify `tests/lib/style_linter/test_runner.py`: prove real lint planning receives the filtered file set.
- Modify `tests/lib/style_linter/test_cli.py`: prove dry-run CLI output omits ignored Python files.
- Modify `docs/specs/2026-07-05-style-lint-rules.md`: document that default and explicit directory scans respect `.gitignore`.

## Task 1: Source Collection Tests

**Files:**
- Modify: `tests/lib/style_linter/test_source.py`

- [ ] **Step 1: Add a failing test for ignored directory discovery**

Add this test after `test_collect_python_files_defaults_to_configured_roots`:

```python
def test_collect_python_files_skips_gitignored_directory_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Skip ignored Python files discovered through directory scans."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path(".")],
        default_paths=(Path("src"),),
    )

    assert included_file.resolve() in targets
    assert ignored_file.resolve() not in targets
```

- [ ] **Step 2: Add a failing test for explicit ignored files**

Add this test in the same file:

```python
def test_collect_python_files_skips_explicit_gitignored_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Apply Git ignore filtering to explicit Python file paths."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored.py\n")
    ignored_file = tmp_path / "ignored.py"
    ignored_file.write_text("value = 1\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path("ignored.py")],
        default_paths=(Path("src"),),
    )

    assert targets == []
```

- [ ] **Step 3: Add a fallback test for Git failures**

Add this test in the same file:

```python
def test_collect_python_files_keeps_existing_scan_when_git_ignore_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Keep advisory collection usable when Git ignore checks are unavailable."""
    source = tmp_path / "src/example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("value = 1\n")

    def raise_file_not_found(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("git is unavailable")

    source_module = importlib.import_module("lib.style_lint.source")
    monkeypatch.setattr(source_module.subprocess, "run", raise_file_not_found)
    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path("src")],
        default_paths=(Path("src"),),
    )

    assert targets == [source.resolve()]
```

- [ ] **Step 4: Add required imports**

At the top of `tests/lib/style_linter/test_source.py`, add:

```python
import importlib
import subprocess
```

- [ ] **Step 5: Run tests to verify failure**

Run:

```bash
uv run pytest tests/lib/style_linter/test_source.py -q
```

Expected: the two Git ignore tests fail because ignored files are still returned; the fallback test may pass only after imports are present.

- [ ] **Step 6: Commit the failing tests**

Run:

```bash
git add tests/lib/style_linter/test_source.py
git commit -m "test: cover style lint gitignore discovery"
```

## Task 2: Git Ignore Filtering

**Files:**
- Modify: `lib/style_lint/source.py`
- Test: `tests/lib/style_linter/test_source.py`

- [ ] **Step 1: Add subprocess import**

Update imports in `lib/style_lint/source.py`:

```python
import subprocess
from pathlib import Path
```

- [ ] **Step 2: Filter collected targets through Git**

Add a private helper below `collect_python_files()`:

```python
def _exclude_gitignored_paths(paths: set[Path]) -> set[Path]:
    """Return paths that are not ignored by Git, falling back on Git failure."""
    if not paths:
        return paths

    sorted_paths = sorted(paths)
    check = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(str(path) for path in sorted_paths),
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode not in (0, 1):
        return paths

    ignored_paths = {Path(path).resolve() for path in check.stdout.splitlines()}
    return {path for path in paths if path.resolve() not in ignored_paths}
```

- [ ] **Step 3: Use the helper at the collection boundary**

Change the return in `collect_python_files()`:

```python
    return sorted(_exclude_gitignored_paths(targets))
```

- [ ] **Step 4: Preserve fallback for missing Git executable**

Wrap the `subprocess.run()` call in `_exclude_gitignored_paths()`:

```python
    try:
        check = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(str(path) for path in sorted_paths),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return paths
```

- [ ] **Step 5: Run source tests**

Run:

```bash
uv run pytest tests/lib/style_linter/test_source.py -q
```

Expected: all source collection tests pass.

- [ ] **Step 6: Run style-linter tests**

Run:

```bash
uv run pytest tests/lib/style_linter -q
```

Expected: all style-linter tests pass.

- [ ] **Step 7: Commit implementation**

Run:

```bash
git add lib/style_lint/source.py tests/lib/style_linter/test_source.py
git commit -m "fix: respect gitignore in style lint discovery"
```

## Task 3: Runner and CLI Parity Tests

**Files:**
- Modify: `tests/lib/style_linter/test_runner.py`
- Modify: `tests/lib/style_linter/test_cli.py`

- [ ] **Step 1: Add a runner dry-run parity test**

Add this test near the existing dry-run tests in `tests/lib/style_linter/test_runner.py`:

```python
def test_run_advisory_lint_dry_run_skips_gitignored_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Plan dry-run work from the same Git-filtered collection as real lint."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)

    result = framework.run_advisory_lint(
        framework.StyleLintConfig(
            paths=[Path(".")],
            default_paths=(Path("src"),),
            rules=[],
            policy_instructions="Use the repository style guide.",
            api_key="unused-in-dry-run",
            model="gemini-test",
            dry_run=True,
        )
    )

    assert result.plan is not None
    assert included_file.resolve() in result.plan.files
    assert ignored_file.resolve() not in result.plan.files
```

- [ ] **Step 2: Add required runner import**

If not already present, add this import to `tests/lib/style_linter/test_runner.py`:

```python
import subprocess
```

- [ ] **Step 3: Add a CLI dry-run output test**

Add this test near the existing dry-run tests in `tests/lib/style_linter/test_cli.py`:

```python
def test_cli_dry_run_omits_gitignored_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    style_lint_cli: ModuleType,
) -> None:
    """Keep CLI dry-run output aligned with Git-filtered source collection."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)

    exit_code = style_lint_cli.main(["--dry-run", "."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(included_file.resolve()) in captured.out
    assert str(ignored_file.resolve()) not in captured.out
```

- [ ] **Step 4: Run runner and CLI tests**

Run:

```bash
uv run pytest tests/lib/style_linter/test_runner.py tests/lib/style_linter/test_cli.py -q
```

Expected: runner and CLI parity tests pass.

- [ ] **Step 5: Commit parity tests**

Run:

```bash
git add tests/lib/style_linter/test_runner.py tests/lib/style_linter/test_cli.py
git commit -m "test: cover style lint gitignore parity"
```

## Task 4: Documentation and Behavior Check

**Files:**
- Modify: `docs/specs/2026-07-05-style-lint-rules.md`

- [ ] **Step 1: Update style-lint rules spec**

In `docs/specs/2026-07-05-style-lint-rules.md`, add this bullet after the dry-run output bullet:

```markdown
  - Default-path and explicit directory scans respect Git ignore rules, so generated local files under ignored directories such as `.venv/`, `.worktrees/`, `tmp/`, `dist/`, and `build/` are not lint targets.
```

- [ ] **Step 2: Verify full-project dry-run excludes ignored files**

Run:

```bash
mkdir -p tmp/style-lint-ignored
printf 'value = 1\n' > tmp/style-lint-ignored/generated.py
uv run python bin/linters/style_lint.py --dry-run --verbose . > /tmp/evalspec-style-lint-dry-run.out
if rg 'tmp/style-lint-ignored/generated.py' /tmp/evalspec-style-lint-dry-run.out; then
  echo "ignored file was linted"
  exit 1
fi
rm -rf tmp/style-lint-ignored
```

Expected: command exits zero and `rg` finds no ignored generated file.

- [ ] **Step 3: Run issue verification**

Run:

```bash
uv run pytest tests/lib/style_linter -q
make test
make lint
uv run python bin/linters/style_lint.py --dry-run --verbose .
```

Expected: all commands exit zero; dry-run output lists non-ignored Python files only.

- [ ] **Step 4: Commit documentation**

Run:

```bash
git add docs/specs/2026-07-05-style-lint-rules.md
git commit -m "docs: document style lint gitignore discovery"
```

## Self-Review

- Spec coverage: ignored directory files, explicit ignored files, fallback behavior, deterministic sorting, and dry-run parity are covered.
- Placeholder scan: no TBD or underspecified implementation steps remain.
- Type consistency: helper accepts and returns `set[Path]`, and `collect_python_files()` continues returning `list[Path]`.
