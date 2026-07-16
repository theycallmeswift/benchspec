# E2E Terminal Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `evalspec run` (and `make e2e`) attribute pytest progress to authored `.eval.md` files and finish with an aligned per-eval benchmark matrix in the terminal, replacing the wrapper-module path and the single prose delta line.

**Architecture:** Two independent presentation fixes on top of unchanged collection/execution/scoring. (1) A new `pytest_collection_modifyitems` hook rewrites each parametrized item's `item.location` — the path pytest groups progress under — from the shared `cases.py` wrapper to the eval's authored Markdown file, without touching `item.nodeid`. (2) A new `report.terminal_matrix()` renders the benchmark's existing roster/arms/baseline as width-aligned plain text; `pytest_sessionfinish` emits it in place of `report.delta_line()`, which is then deleted as dead code. Persisted `benchmark.json`/`benchmark.md` artifacts are untouched.

**Tech Stack:** Python 3.11+, pytest (plugin hooks + pytester), `uv` for running, Ruff + houserules for lint. No new dependencies.

## Global Constraints

- **Byte-identical artifacts.** Do NOT change the output of `build_benchmark`, `_format_markdown`, `_matrix_table`, or `write_benchmark`. `benchmark.json`, `benchmark.md`, `meta.json`, and `index.jsonl` schemas and bytes stay exactly as they are today.
- **No new dependency.** No Rich, tabulate, or any terminal-rendering library. Plain `str.ljust`/`str.rjust`.
- **No `item.nodeid` changes.** Collection identity is preserved: `--collect-only`/`-v` must still show unique `test_eval[<group>-<eval_id>-<arm>]` node ids. Only `item.location` changes.
- **Exit semantics unchanged.** `FAIL fail-under: …` and `WARN binder: …` lines still print (now below the matrix) and still control `session.exitstatus` exactly as today.
- **Style guide** ([`docs/style/development.md`](../style/development.md)): Google-style docstrings on ALL functions incl. private/nested helpers; comments explain why-not-what and carry NO issue references; `from __future__ import annotations` (already present in every file touched); fail fast; four-phase DAMP tests. Reuse `_rate_cell`/`_per_eval_rate` rather than duplicating cell-format semantics.
- **Verification commands** (run per the final task; `make e2e` is human-gated — costs real money/microVMs, run once):
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/runners/test_pytest.py -v`
  - `uv run evalspec run --set e2e -- --collect-only -q`
  - `make test`
  - `make lint`
  - `make e2e`

---

### Task 1: `terminal_matrix()` formatter in `report.py`

Add a public plain-text matrix formatter that mirrors `_matrix_table`'s column order, roster rows, and cell semantics but renders width-aligned text plus a `Report:` pointer. No wiring yet — Task 2 consumes it.

**Alignment contract (decided; the issue's example block is hand-drawn and internally inconsistent, so assert against the golden below, not that block):** label column left-justified; each arm column right-justified; every column sized to `max(len(header), widest cell)`; a two-space gutter joins columns; each line is `rstrip()`-ed to drop trailing pad off the last (left-justified) header name.

**Files:**
- Modify: `src/evalspec/reporting/report.py` — add `terminal_matrix` after `_matrix_table` (currently ends at line 379, before `_provenance_lines` at 382).
- Test: `tests/reporting/test_report.py`

**Interfaces:**
- Consumes (already in `report.py`): `_per_eval_rate(arm_stats: dict, group: str, eval_id: str) -> float | None`; `_rate_cell(rate: float | None, delta_pp: float | None) -> str`. Benchmark dict shape from `build_benchmark`: `{"arms": {name: {"pass_rate", "delta_pp"?, "per_eval": [...]}}, "baseline": str | None, "roster": [{"group", "eval_id"}]}`.
- Produces: `terminal_matrix(benchmark: dict, benchmark_md: Path) -> list[str]` — one string per line (header, one per roster eval, `All evals` footer, `Report: <path>`), or `[]` when the benchmark has no arm columns.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/reporting/test_report.py`. Note the top of the file already has `from pathlib import Path`? Verify — it imports only `pytest`, `report`, and `seed_arm`. Add `from pathlib import Path` to the imports at the top of the file (after `import pytest`):

```python
from pathlib import Path
```

Then append these tests:

```python
def test_terminal_matrix_layout_golden(tmp_path: object) -> None:
    """Verify the aligned plain-text matrix, header, footer, and Report pointer."""
    seed_arm(tmp_path / "hello", "greets-by-name", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "hello", "greets-by-name", "trial", passes=2, total=2)  # 100% → +50pp

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path),
        "iteration_02",
        baseline="baseline",
        arm_meta={"baseline": {"harness": "claude-code"}, "trial": {"harness": "claude-code"}},
    )

    lines = report.terminal_matrix(bench, Path("tmp/evals/iteration_02/benchmark.md"))

    # Label column left-justified to width 20; baseline column right-justified to width 8
    # (header "baseline"); trial column right-justified to width 12 ("100% (+50pp)").
    assert lines == [
        "Eval" + " " * 16 + "  " + "baseline" + "  " + "trial",
        "hello/greets-by-name" + "  " + " " * 5 + "50%" + "  " + "100% (+50pp)",
        "All evals" + " " * 11 + "  " + " " * 5 + "50%" + "  " + "100% (+50pp)",
        "Report: tmp/evals/iteration_02/benchmark.md",
    ]


def test_terminal_matrix_renders_dash_for_all_errored_eval(tmp_path: object) -> None:
    """Verify an all-errored eval keeps a roster row rendered as a — cell."""
    seed_arm(tmp_path / "hello", "ok", "trial", passes=2, total=2)
    seed_arm(tmp_path / "hello", "ghost", "trial", passes=0, total=2, errored=True)

    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), "iteration_01", baseline=None
    )

    lines = report.terminal_matrix(bench, Path("tmp/evals/iteration_01/benchmark.md"))

    # Roster sorts by (group, eval_id): ghost before ok. The all-errored eval has no
    # per-eval row in any arm, so its only cell is —.
    ghost = next(line for line in lines if line.startswith("hello/ghost"))
    assert ghost.split() == ["hello/ghost", "—"]


def test_terminal_matrix_empty_without_arms() -> None:
    """Verify a benchmark with no arm columns yields no lines."""
    bench = {"arms": {}, "baseline": None, "roster": []}

    assert report.terminal_matrix(bench, Path("x/benchmark.md")) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/reporting/test_report.py::test_terminal_matrix_layout_golden tests/reporting/test_report.py::test_terminal_matrix_renders_dash_for_all_errored_eval tests/reporting/test_report.py::test_terminal_matrix_empty_without_arms -v`
Expected: FAIL with `AttributeError: module 'evalspec.reporting.report' has no attribute 'terminal_matrix'`.

- [ ] **Step 3: Write the implementation**

In `src/evalspec/reporting/report.py`, insert this function immediately after `_matrix_table` (after its `return lines` on line 379, before `def _provenance_lines`):

```python
def terminal_matrix(benchmark: dict, benchmark_md: Path) -> list[str]:
    """Render the run-level matrix as width-aligned plain text for the terminal.

    A plain-text sibling of `_matrix_table`: same baseline-first column order, same
    roster rows (all-errored evals included as `—` cells), same `_rate_cell` cell
    semantics, same pooled `All evals` footer. The label column is left-justified; every
    arm column is right-justified and sized to the wider of its header and its widest
    cell, joined by a two-space gutter. The final line points at the persisted report.

    Args:
        benchmark: The benchmark dict from `build_benchmark`/`write_benchmark`.
        benchmark_md: Path to the run's `benchmark.md`, printed as the trailing pointer.

    Returns:
        One string per line (header, one per roster eval, the `All evals` footer, and a
        `Report: <path>` pointer), or `[]` when the benchmark has no arm columns.
    """
    arms = benchmark["arms"]
    baseline = benchmark.get("baseline")
    # Baseline column first, then the rest in declared order — matching `_matrix_table`.
    names = ([baseline] if baseline in arms else []) + [
        arm_name for arm_name in arms if arm_name != baseline
    ]
    if not names:
        return []

    # Build (label, cells) rows from the roster then the pooled footer, reusing the exact
    # per-eval lookup and cell formatting the Markdown matrix uses.
    rows: list[tuple[str, list[str]]] = []
    for entry in benchmark["roster"]:
        group = entry["group"]
        eval_id = entry["eval_id"]
        ref_rate = _per_eval_rate(arms[baseline], group, eval_id) if baseline in arms else None
        cells = []
        for arm_name in names:
            rate = _per_eval_rate(arms[arm_name], group, eval_id)
            measured = arm_name != baseline and ref_rate is not None and rate is not None
            delta_pp = (rate - ref_rate) * 100 if measured else None
            cells.append(_rate_cell(rate, delta_pp))
        rows.append((f"{group}/{eval_id}", cells))
    footer_cells = []
    for arm_name in names:
        delta_pp = None if arm_name == baseline else arms[arm_name].get("delta_pp")
        footer_cells.append(_rate_cell(arms[arm_name]["pass_rate"], delta_pp))
    rows.append(("All evals", footer_cells))

    # Column widths: label column fits its header and every row label; each arm column
    # fits its header and every cell in that column.
    label_width = max(len("Eval"), *(len(label) for label, _ in rows))
    arm_widths = [
        max(len(names[index]), *(len(cells[index]) for _, cells in rows))
        for index in range(len(names))
    ]

    def _data(label: str, cells: list[str]) -> str:
        """Left-justify the label, right-justify each cell, join on the two-space gutter."""
        parts = [label.ljust(label_width)]
        parts += [cells[index].rjust(arm_widths[index]) for index in range(len(names))]
        return "  ".join(parts).rstrip()

    # Header names read as left-justified labels; rstrip drops the trailing pad off the
    # last one so the line has no dangling whitespace.
    header_parts = ["Eval".ljust(label_width)]
    header_parts += [names[index].ljust(arm_widths[index]) for index in range(len(names))]
    lines = ["  ".join(header_parts).rstrip()]
    lines += [_data(label, cells) for label, cells in rows]
    lines.append(f"Report: {benchmark_md}")
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/reporting/test_report.py::test_terminal_matrix_layout_golden tests/reporting/test_report.py::test_terminal_matrix_renders_dash_for_all_errored_eval tests/reporting/test_report.py::test_terminal_matrix_empty_without_arms -v`
Expected: 3 passed.

- [ ] **Step 5: Guard the byte-identical-artifact constraint**

Run the existing markdown-matrix tests to confirm `_matrix_table`/`_format_markdown` are untouched:
Run: `uv run pytest tests/reporting/test_report.py -q`
Expected: all pass (the pre-existing `test_format_markdown_renders_matrix_table`, `test_matrix_row_from_roster_for_all_errored_eval`, etc., plus the 3 new tests).

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/reporting/report.py tests/reporting/test_report.py
git commit -m "feat(report): add terminal_matrix plain-text formatter"
```

---

### Task 2: Wire `pytest_sessionfinish` to the matrix; delete dead `delta_line`

Replace the single prose delta line with the multi-line matrix in the terminal summary. `delta_line` has exactly one src caller (`pytest.py:329`, being replaced here) — grep confirms no other. Delete it and its dedicated tests together (YAGNI: no dead public API).

**Files:**
- Modify: `src/evalspec/runners/pytest.py:329` (swap `delta_line` for `terminal_matrix`).
- Modify: `src/evalspec/reporting/report.py:640-671` (delete `delta_line`).
- Test: `tests/runners/test_pytest.py` (update `test_terminal_summary_prints_delta` at lines 458-463; `test_terminal_summary_multi_skill_single_header` at lines 467-496).
- Test: `tests/reporting/test_report.py` (delete 4 `test_delta_line_*` tests; trim `test_within_noise_label_in_markdown_and_delta_line`).

**Interfaces:**
- Consumes: `report.terminal_matrix(benchmark: dict, benchmark_md: Path) -> list[str]` from Task 1.
- Produces: no new public symbol; `report.delta_line` no longer exists after this task.

- [ ] **Step 1: Update the two terminal-summary tests (make them the failing spec)**

In `tests/runners/test_pytest.py`, replace the body of `test_terminal_summary_prints_delta` from its `_finish_and_summarize` call through the `benchmark.md` assertion (current lines 458-463):

```python
    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert ("separator", "evalspec benchmark") in terminal_reporter.events
    lines = [message for kind, message in terminal_reporter.events if kind == "line"]
    assert any("iteration_01" in message and "+100pp" in message for message in lines)
    assert (skill_results_dir.parent.parent / "benchmark.md").is_file()
```

with:

```python
    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert ("separator", "evalspec benchmark") in terminal_reporter.events
    lines = [message for kind, message in terminal_reporter.events if kind == "line"]
    # The matrix header names the arms; a data row carries the +100pp delta cell; the
    # final line points at the persisted report under this iteration.
    assert lines[0].split() == ["Eval", "baseline", "trial"]
    assert any("+100pp" in message for message in lines)
    assert any(message.startswith("Report: ") and "iteration_01" in message for message in lines)
    assert (skill_results_dir.parent.parent / "benchmark.md").is_file()
```

Then in `test_terminal_summary_multi_skill_single_header`, replace the docstring/comment and the `assert len(lines) == 1` block (current lines 467-488) so the whole body from the docstring through the markdown assertions reads:

```python
    """Verify terminal summary multi skill single table."""
    # Two skills with eval-* children pool into ONE run-level matrix under a single
    # header (rows sorted: archive before ingest).
    monkeypatch.setattr(report, "make_agent", lambda harness=None: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)
    workspace.set_current_iteration("iteration_01")
    skills = tmp_path / "tmp" / "evals" / "iteration_01" / "skills"

    archive_dir = skills / "archive"
    seed_arm(archive_dir, "alpha", "trial", passes=2, total=2)
    seed_arm(archive_dir, "alpha", "baseline", passes=0, total=2)

    ingest_dir = skills / "ingest"
    seed_arm(ingest_dir, "beta", "trial", passes=1, total=2)
    seed_arm(ingest_dir, "beta", "baseline", passes=1, total=2)

    _, terminal_reporter = _finish_and_summarize(tmp_path)

    assert terminal_reporter.events.count(("separator", "evalspec benchmark")) == 1

    lines = [message for kind, message in terminal_reporter.events if kind == "line"]
    # One pooled table: header, a row per eval (archive before ingest), the pooled
    # footer, then the report pointer — no per-skill repetition.
    assert lines[0].split() == ["Eval", "baseline", "trial"]
    assert any(line.startswith("archive/alpha") for line in lines)
    assert any(line.startswith("ingest/beta") for line in lines)
    assert any(line.startswith("All evals") for line in lines)
    assert lines[-1].startswith("Report: ")

    iteration_root = skills.parent
    benchmark = json.loads((iteration_root / "benchmark.json").read_text())
    assert benchmark["label"] == "iteration_01"
    markdown = (iteration_root / "benchmark.md").read_text()
    assert "| archive/alpha |" in markdown
    assert "| ingest/beta |" in markdown
    assert "| All evals |" in markdown
```

- [ ] **Step 2: Run the updated tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_terminal_summary_prints_delta" "tests/runners/test_pytest.py::test_terminal_summary_multi_skill_single_header" -v`
Expected: FAIL — the current `delta_line` output produces a single line whose `lines[0]` is the prose delta, so `lines[0].split() == ["Eval", "baseline", "trial"]` fails.

- [ ] **Step 3: Swap the wiring in `pytest.py`**

In `src/evalspec/runners/pytest.py`, replace line 329:

```python
        lines.append(report.delta_line(iteration, benchmark, skills_root.parent / "benchmark.md"))
```

with:

```python
        lines += report.terminal_matrix(benchmark, skills_root.parent / "benchmark.md")
```

- [ ] **Step 4: Run the updated tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_terminal_summary_prints_delta" "tests/runners/test_pytest.py::test_terminal_summary_multi_skill_single_header" -v`
Expected: 2 passed.

- [ ] **Step 5: Confirm FAIL/WARN and noop ordering still hold**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_terminal_summary_noop_without_artifacts" "tests/runners/test_pytest.py::test_fail_under_sets_exit_status" "tests/runners/test_pytest.py::test_binder_degraded_warns_in_terminal_summary" "tests/runners/test_pytest.py::test_binder_degraded_quiet_when_zero" -v`
Expected: all pass (matrix lines print first; `FAIL fail-under: …`/`WARN binder: …` still appended below and still set exit status).

- [ ] **Step 6: Delete `delta_line` and its dedicated tests**

In `src/evalspec/reporting/report.py`, delete the entire `delta_line` function (currently lines 640-671, from `def delta_line(skill: str, benchmark: dict, benchmark_md: Path) -> str:` through its final `return` statement).

In `tests/reporting/test_report.py`, delete these four functions in full: `test_delta_line_reference_only_shows_its_rate`, `test_delta_line_shows_delta`, `test_delta_line_handles_missing_arm`, `test_delta_line_absolute_when_no_reference`.

Then trim `test_within_noise_label_in_markdown_and_delta_line` (currently lines 633-648) to drop the delta_line assertion, replacing the whole function with:

```python
def test_within_noise_label_in_markdown(tmp_path: object) -> None:
    """Verify within noise label in markdown."""
    # delta +17pp, but arms this scattered have SE > 17pp → labeled.
    archive = tmp_path / "archive"
    for sample_index, passes in enumerate((2, 0, 1)):
        seed_arm(archive, "alpha", "trial", passes=passes, total=2, sample=sample_index)
    for sample_index, passes in enumerate((0, 1, 1)):
        seed_arm(archive, "alpha", "baseline", passes=passes, total=2, sample=sample_index)

    report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="alpha", baseline="baseline"
    )
    md = (tmp_path / "benchmark.md").read_text()

    assert "within noise" in md
```

- [ ] **Step 7: Verify no dangling references and the suite is green**

Run: `grep -rn "delta_line" src tests`
Expected: no output (zero matches).

Run: `uv run pytest tests/reporting/test_report.py -q && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/runners/test_pytest.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/evalspec/runners/pytest.py src/evalspec/reporting/report.py tests/reporting/test_report.py tests/runners/test_pytest.py
git commit -m "feat(runner): print benchmark matrix in terminal summary; drop delta_line"
```

---

### Task 3: Collection hook — attribute item locations to authored eval files

Add `pytest_collection_modifyitems` to the plugin so pytest progress groups each cell under its authored `.eval.md` path instead of the shared `cases.py` wrapper. Rewrite `item.location` only; leave `item.nodeid` alone.

**Files:**
- Modify: `src/evalspec/runners/pytest.py` — add `pytest_collection_modifyitems` after `pytest_configure_node` (ends line 237, before `pytest_sessionfinish` at 239).
- Test: `tests/runners/test_pytest.py` (add one test near the collection tests, after `test_models_flag_sweeps_arms` at line 198).

**Interfaces:**
- Consumes: pytest `Session._node_location_to_relpath(node_path: Path) -> str` (verified present in this env); each parametrized item's `item.callspec.params["eval_arm"]` is the `(EvalCase, Arm)` tuple set by `pytest_generate_tests`; `EvalCase.eval_file: Path` is the absolute authored `.eval.md`.
- Produces: `pytest_collection_modifyitems(config: object, items: list) -> None` — a pytest hook (no return value; mutates `item.location` in place).

- [ ] **Step 1: Write the failing test**

The dummy `def test_eval(eval_arm): pass` in `test_cases.py` requests only the parametrized `eval_arm` value (no sandbox/judge fixtures — those live in `cases.py`, which is not the collection target here), so the four cells run and pass with no `claude -p`. In default (non-verbose) mode pytest groups its progress line by `report.location[0]`, which is copied from `item.location` — the value this hook rewrites. Add to `tests/runners/test_pytest.py` after `test_models_flag_sweeps_arms` (line 198):

```python
def test_progress_groups_under_authored_eval_files(pytester: object) -> None:
    """Verify pytest attributes progress to authored .eval.md paths, node ids intact."""
    _make_project(pytester)

    # Non-verbose progress groups by item.location — the rewritten authored eval file.
    run = pytester.runpytest(
        "-p",
        "evalspec.runners.pytest",
        "--evalspec-repo-root",
        str(pytester.path),
        "test_cases.py::test_eval",
    )

    run.assert_outcomes(passed=4)  # 2 evals × 2 arms; dummy bodies just pass
    progress = run.stdout.str()
    assert "skills/myskill/evals/myskill/alpha.eval.md" in progress
    assert "skills/myskill/evals/myskill/beta.eval.md" in progress

    # Collection identity is unchanged: --collect-only still lists unique (eval × arm) ids.
    collected = _collect(pytester).stdout.str()
    for node_id in (
        "test_eval[myskill-alpha-baseline]",
        "test_eval[myskill-alpha-trial]",
        "test_eval[myskill-beta-baseline]",
        "test_eval[myskill-beta-trial]",
    ):
        assert node_id in collected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_progress_groups_under_authored_eval_files" -v`
Expected: FAIL — with no hook yet, `item.location[0]` is still the `test_cases.py` wrapper, so the authored `.eval.md` paths never appear in the progress output and the two `in progress` assertions fail.

- [ ] **Step 3: Write the hook**

In `src/evalspec/runners/pytest.py`, insert after `pytest_configure_node` (after its closing line 237, before `def pytest_sessionfinish`):

```python
def pytest_collection_modifyitems(config: object, items: list) -> None:
    """Attribute each eval item's displayed location to its authored `.eval.md` file.

    Collection identity (`item.nodeid`) is untouched, so `--collect-only`/`-v` keep the
    unique `(eval × arm)` descriptions; only `item.location` — the path pytest groups
    progress and reports under — is rewritten from the shared `cases.py` wrapper to the
    eval's authored Markdown file, repo-relative. Reading `item.location` first computes
    and caches the tuple; the assignment then overrides that cache. Non-eval items (no
    `eval_arm` callspec param) are left alone.
    """
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None or "eval_arm" not in callspec.params:
            continue
        eval_case, _arm = callspec.params["eval_arm"]
        relpath = item.session._node_location_to_relpath(eval_case.eval_file)
        _, lineno, domain = item.location
        item.location = (relpath, lineno, domain)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_progress_groups_under_authored_eval_files" -v`
Expected: 1 passed.

- [ ] **Step 5: Confirm node ids (collection identity) are untouched**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester "tests/runners/test_pytest.py::test_cross_product_of_evals_and_arms" "tests/runners/test_pytest.py::test_models_flag_sweeps_arms" -v`
Expected: both pass — the pre-existing node-id assertions still hold, proving `item.nodeid` is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/runners/pytest.py tests/runners/test_pytest.py
git commit -m "feat(runner): group pytest progress under authored eval files"
```

---

### Task 4: Documentation — terminal contract in `results.md`, sample output in `quickstart.md`

Document the terminal matrix as the run-time view distinct from `benchmark.md`, and replace the stale prose delta-line sample.

**Files:**
- Modify: `docs/results.md` (add a terminal-summary subsection after the Provenance paragraph — the `<a id="noise"></a>` anchor is line 69, `## Noise, samples, and flakiness` is line 70).
- Modify: `docs/quickstart.md` (replace the sample output block at lines 145-147; adjust the lead-in on line 143).

- [ ] **Step 1: Add the terminal-summary subsection to `results.md`**

In `docs/results.md`, immediately after the Provenance paragraph (ending line 67) and before the `<a id="noise"></a>` anchor (line 69) that precedes `## Noise, samples, and flakiness`, insert the following Markdown (the fenced example inside it uses a ```text block):

````markdown
## The terminal summary

`evalspec run` ends the pytest session with the same matrix rendered as aligned
plain text under an `evalspec benchmark` banner — the run-time view of what
`benchmark.md` persists. Columns are the arms baseline-first; rows are
`group/eval_id` (an all-errored eval still appears, as a row of `—`); the
`All evals` footer is each arm's pooled rate; every non-baseline cell carries its
`(+Npp)` delta. The last line, `Report: <path>`, points at the written
`benchmark.md`:

```text
============================== evalspec benchmark ==============================
Eval                             baseline  trial
hello/greets-by-name                  67%  100% (+33pp)
hello-file/writes-greeting-file       50%   83% (+33pp)
All evals                             58%   92% (+34pp)
Report: tmp/evals/iteration_02/benchmark.md
```

The matrix is display only: `FAIL fail-under: …` and `WARN binder: …` lines, when
present, print below it and are what actually control exit status.
````

- [ ] **Step 2: Replace the stale sample in `quickstart.md`**

In `docs/quickstart.md`, change the lead-in on line 143 from:

```markdown
grade, and the session ends with a benchmark line:
```

to:

```markdown
grade, and the session ends with the benchmark matrix:
```

Then replace the fenced sample block on lines 145-147:

````markdown
```
iteration_01: baseline 0% -> trial 100%  (delta +100pp)  -> tmp/evals/iteration_01/benchmark.md
```
````

with:

````markdown
```
============================== evalspec benchmark ==============================
Eval                  baseline  trial
hello/greets-by-name        0%  100% (+100pp)
All evals                   0%  100% (+100pp)
Report: tmp/evals/iteration_01/benchmark.md
```
````

- [ ] **Step 3: Verify docs lint**

Run: `make lint`
Expected: Ruff passes (no code changed). If the houserules LLM half flags non-reproducing noise, re-run once — Ruff is the hard gate (per project memory).

- [ ] **Step 4: Commit**

```bash
git add docs/results.md docs/quickstart.md
git commit -m "docs: document terminal benchmark matrix; update quickstart output"
```

---

### Task 5: Full verification

Run the issue's Verification commands. `make e2e` is human-gated (real money/microVMs) — run it once, last.

- [ ] **Step 1: Plugin tests in isolation**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/runners/test_pytest.py -v`
Expected: all pass — collection presentation and terminal-table behavior.

- [ ] **Step 2: Collect-only on the real e2e set (no paid tasks)**

Run: `uv run evalspec run --set e2e -- --collect-only -q`
Expected: six cells (two authored eval files × three arms) collect and name the two `.eval.md` files; exit 0. No tasks run.

- [ ] **Step 3: Full unit + integration suite**

Run: `make test`
Expected: green.

- [ ] **Step 4: Lint**

Run: `make lint`
Expected: Ruff clean (hard gate); re-run once if the houserules LLM half emits non-reproducing noise.

- [ ] **Step 5 (human-gated): Real E2E**

Run: `make e2e`
Expected: progress lines name both authored `.eval.md` files; the run ends with the aligned `evalspec benchmark` matrix (two `group/eval_id` rows + `All evals` footer + `Report:` line); `benchmark.json`/`benchmark.md`/`meta.json`/`index.jsonl` are written with unchanged schemas. Run once — it costs real money/microVMs.

---

## Verification (from the issue)

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/runners/test_pytest.py -v` — collection presentation and terminal-table behavior pass in isolation.
- `uv run evalspec run --set e2e -- --collect-only -q` — the six cells name the two authored eval files and three arms without running paid tasks.
- `make test` — all unit and integration behavior remains green.
- `make lint` — Ruff and houserules accept the implementation and documentation changes.
- `make e2e` — real microVM output matches the displayed-file and terminal-matrix contract and writes the expected report artifacts.
