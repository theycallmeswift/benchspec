# Style Lint Gitignore Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make advisory style-lint directory discovery exclude Python files ignored by Git while preserving deterministic fallback behavior outside Git repositories.

**Architecture:** Keep ignore handling inside `lib/style_lint/source.py`, the shared collection boundary used by dry-run and real lint. Prefer Git-backed discovery with `git ls-files --cached --others --exclude-standard -- '*.py'` so ignored directories are not traversed by Python, and fall back to the existing sorted filesystem scan when Git discovery is unavailable.

**Tech Stack:** Python `pathlib`, `subprocess`, pytest, existing `lib.style_lint` runner and CLI.

---

## File Structure

- Modify `lib/style_lint/source.py`: collect Python files through Git-backed discovery when available, intersect Git's Python file list with requested roots, and keep sorted absolute paths.
- Modify `tests/lib/style_linter/test_source.py`: cover ignored directory files, tracked or unignored files, explicit file behavior, ignored-tree traversal avoidance, and Git fallback behavior.
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

- [ ] **Step 2: Add a test documenting explicit ignored-file behavior**

Add this test in the same file:

```python
def test_collect_python_files_keeps_explicit_gitignored_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Keep explicitly named Python files even when Git would ignore them."""
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

    assert targets == [ignored_file.resolve()]
```

This keeps the issue focused on discovery: ignored files found through default or explicit directory scans are excluded, while a user who names a Python file directly gets that file.

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

- [ ] **Step 4: Add a failing test for ignored-tree traversal avoidance**

Add this test in the same file:

```python
def test_collect_python_files_uses_git_discovery_without_rglob_ignored_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: ModuleType,
) -> None:
    """Avoid Python recursion through ignored trees when Git discovery works."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("ignored/\n")
    included_file = tmp_path / "src/included.py"
    included_file.parent.mkdir(parents=True, exist_ok=True)
    included_file.write_text("value = 1\n")
    ignored_file = tmp_path / "ignored/generated.py"
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("value = 2\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    source_module = importlib.import_module("lib.style_lint.source")
    original_rglob = Path.rglob

    def fail_on_ignored_rglob(path: Path, pattern: str) -> object:
        if path.resolve() == (tmp_path / "ignored").resolve():
            raise AssertionError("ignored directory should not be traversed")
        return original_rglob(path, pattern)

    monkeypatch.setattr(source_module.Path, "rglob", fail_on_ignored_rglob)
    monkeypatch.chdir(tmp_path)

    targets = framework.collect_python_files(
        [Path(".")],
        default_paths=(Path("src"),),
    )

    assert included_file.resolve() in targets
    assert ignored_file.resolve() not in targets
```

- [ ] **Step 5: Add required imports**

At the top of `tests/lib/style_linter/test_source.py`, add:

```python
import importlib
import subprocess
```

- [ ] **Step 6: Run tests to verify failure**

Run:

```bash
uv run pytest tests/lib/style_linter/test_source.py -q
```

Expected: the ignored directory and traversal tests fail because ignored files are still discovered through `rglob`; the explicit file test documents existing behavior.

- [ ] **Step 7: Commit the failing tests**

Run:

```bash
git add tests/lib/style_linter/test_source.py
git commit -m "test: cover style lint gitignore discovery"
```

## Task 2: Git-Backed Discovery

**Files:**
- Modify: `lib/style_lint/source.py`
- Test: `tests/lib/style_linter/test_source.py`

- [ ] **Step 1: Add subprocess import**

Update imports in `lib/style_lint/source.py`:

```python
import subprocess
from pathlib import Path
```

- [ ] **Step 2: Split filesystem fallback from Git-backed discovery**

Replace `collect_python_files()` with this version and add helpers below it:

```python
def collect_python_files(
    paths: list[Path] | None,
    *,
    default_paths: tuple[Path, ...],
) -> list[Path]:
    """Collect Python source files from explicit or default paths.

    Args:
        paths: Explicit file or directory paths. `None` uses `default_paths`.
        default_paths: Paths to scan when `paths` is absent.

    Returns:
        Sorted absolute paths for existing Python files.
    """
    roots = default_paths if paths is None else tuple(paths)
    git_targets = _collect_python_files_with_git(roots)
    if git_targets is not None:
        return sorted(git_targets)

    return sorted(_collect_python_files_from_filesystem(roots))


def _collect_python_files_from_filesystem(roots: tuple[Path, ...]) -> set[Path]:
    """Collect Python files by walking the filesystem."""
    targets: set[Path] = set()
    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.exists():
            continue
        if resolved_root.is_file():
            if resolved_root.suffix == ".py":
                targets.add(resolved_root)
            continue

        for path in resolved_root.rglob("*.py"):
            if path.is_file():
                targets.add(path.resolve())

    return targets


def _collect_python_files_with_git(roots: tuple[Path, ...]) -> set[Path] | None:
    """Collect Python files with Git ignore semantics when Git is available."""
    repo_root = _git_repo_root()
    if repo_root is None:
        return None

    try:
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if listing.returncode != 0:
        return None

    discovered_paths = {
        (repo_root / line).resolve()
        for line in listing.stdout.splitlines()
        if line
    }
    explicit_files = {
        root.resolve()
        for root in roots
        if root.resolve().is_file() and root.resolve().suffix == ".py"
    }
    requested_roots = [root.resolve() for root in roots if root.resolve().is_dir()]
    if not requested_roots:
        return explicit_files

    return explicit_files | {
        path
        for path in discovered_paths
        if _is_relative_to_any(path, requested_roots)
    }


def _git_repo_root() -> Path | None:
    """Return the current Git repository root, or None outside Git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _is_relative_to_any(path: Path, roots: list[Path]) -> bool:
    """Return whether path is inside one of the requested roots."""
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False
```

- [ ] **Step 3: Run source tests**

Run:

```bash
uv run pytest tests/lib/style_linter/test_source.py -q
```

Expected: all source collection tests pass.

- [ ] **Step 4: Run style-linter tests**

Run:

```bash
uv run pytest tests/lib/style_linter -q
```

Expected: all style-linter tests pass.

- [ ] **Step 5: Commit implementation**

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
  - Explicitly named Python files are still linted even when ignored, matching the CLI convention that direct file operands are intentional.
```

- [ ] **Step 2: Verify full-project dry-run excludes ignored files**

Run:

```bash
trap 'rm -rf tmp/style-lint-ignored' EXIT
mkdir -p tmp/style-lint-ignored
printf 'value = 1\n' > tmp/style-lint-ignored/generated.py
uv run python bin/linters/style_lint.py --dry-run --verbose . > /tmp/evalspec-style-lint-dry-run.out
if rg 'tmp/style-lint-ignored/generated.py' /tmp/evalspec-style-lint-dry-run.out; then
  echo "ignored file was linted"
  exit 1
fi
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

- Spec coverage: ignored directory files, explicit file behavior, fallback behavior, deterministic sorting, ignored-tree traversal avoidance, and dry-run parity are covered.
- Placeholder scan: no TBD or underspecified implementation steps remain.
- Type consistency: Git-backed discovery and filesystem fallback both return `set[Path]`, and `collect_python_files()` continues returning `list[Path]`.
