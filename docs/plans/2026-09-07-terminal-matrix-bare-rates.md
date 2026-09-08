# Terminal Matrix Bare Rates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the pytest terminal benchmark matrix as bare rates with one `vs baseline` footer line, so every column aligns on a fixed-shape token.

**Architecture:** `_matrix_cells` and the Markdown `_matrix_table` stay untouched and keep emitting `rate (+Npp)` cells. Only `terminal_matrix` changes: it splits each shared cell into `(rate, delta)` tokens, renders rate tokens for every eval row and the `All evals` footer, and appends a `vs baseline` line built from the footer row's delta tokens when any exist. The colorizer's regex gains a delta-only path so the new line colors by sign.

**Tech Stack:** Python 3, stdlib `re`, pytest. No new dependencies.

**Spec:** GitHub issue #74, "Render the terminal benchmark matrix as bare rates with a vs-baseline footer" (`gh issue view 74`). The issue is the binding authority.

## Global Constraints

- `_matrix_cells` (`src/benchspec/reporting/report.py`), `_rate_cell`, and `_matrix_table` are not modified. `benchmark.md` and `benchmark.json` output is byte-identical before and after.
- `terminal_matrix(benchmark, report_path, *, color=False) -> list[str]` keeps its signature and return shape: header line first, `Report: <path>` line last. The call site in `src/benchspec/runners/pytest.py` is not changed.
- Every eval row and the `All evals` row render bare rates only. No cell in those rows contains `pp`.
- Line order: header, eval rows, rule, `All evals`, `vs baseline` (only when a baseline arm exists and the footer row carries at least one delta), `Report:`.
- The `vs baseline` line shows each non-baseline arm's pooled delta as `+Npp` / `-Npp` with no parentheses, blank under the baseline column, right-aligned to the same column edges.
- Rule width formula is unchanged: `label_width + sum(column_widths) + 2 * len(column_widths)`. Every table line (header, rows, rule, footer, `vs baseline`) has that width.
- Missing rates still render `—`.
- Color mode wraps only visible tokens, so stripping ANSI codes from a `color=True` render yields exactly the `color=False` render. Rates color by band (green ≥80, yellow ≥50, red below), deltas by sign (green positive, red negative, yellow zero).
- Code style follows `docs/style/development.md`: Google-style docstrings on every function including private helpers, fully descriptive names (no single letters), no `# noqa` or `# type: ignore`, no comments that reference issues or PRs, tests laid out setup / exercise / verify with blank lines between phases.
- Ruff line length is 100. `make lint:ruff` must pass. `make test` must pass.
- `make lint:houserules` needs `GEMINI_API_KEY`, which is unset in this environment. Run `make lint:ruff` locally and note that houserules is deferred to CI.
- Conventional-commit messages (`feat:` / `docs:`). No attribution trailers in commit messages.

---

### Task 1: Render bare rates and a `vs baseline` line in `terminal_matrix`

**Files:**
- Modify: `src/benchspec/reporting/report.py` (the `_CELL_RE` constant near line 402, `_colorize_cell` near line 427, and `terminal_matrix` near line 444; add one helper `_split_cell` directly above `terminal_matrix`)
- Test: `tests/reporting/test_report.py` (existing terminal tests around lines 384–470 and the two color tests around lines 903–938; add two new tests)

**Interfaces:**
- Consumes: `_matrix_cells(benchmark) -> (names, rows)` unchanged. Each cell is exactly one of three shapes: `—`, `N%`, `N% (+Mpp)`. The footer row is the last element of `rows` and is labeled `All evals`.
- Produces: `terminal_matrix` with the same signature; `_split_cell(cell: str) -> tuple[str, str]`; `_TOKEN_RE` replacing `_CELL_RE`. Nothing downstream consumes the new helper.

- [ ] **Step 1: Update the existing terminal tests to the new contract and add two new tests**

In `tests/reporting/test_report.py`, add `import re` at the top of the file if it is not already imported (keep imports grouped stdlib → third-party → local, as ruff enforces).

Replace `test_terminal_matrix_aligns_columns` with:

```python
def test_terminal_matrix_aligns_columns(tmp_path: object) -> None:
    """Verify the terminal matrix pads every column so header and rows line up."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)
    seed_arm(tmp_path / "ingest", "long-eval-name", "baseline", passes=2, total=2)
    seed_arm(tmp_path / "ingest", "long-eval-name", "trial", passes=2, total=2)
    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    lines = report.terminal_matrix(bench, tmp_path / "benchmark.md")

    header, *rows, pointer = lines
    assert header.split() == ["Eval", "baseline", "trial"]
    assert [row.split("  ")[0].strip() for row in rows] == [
        "archive/alpha",
        "ingest/long-eval-name",
        "-" * len(header),
        "All evals",
        "vs baseline",
    ]
    # Right-aligned arm cells end where the header does, so every table line is the
    # same width and the eval column is padded to its longest label.
    assert {len(line) for line in (header, *rows)} == {len(header)}
    assert rows[0].startswith("archive/alpha".ljust(len("ingest/long-eval-name")) + "  ")
    assert rows[0].endswith("100%")
    assert pointer == f"Report: {tmp_path / 'benchmark.md'}"
```

Replace `test_terminal_matrix_cells_match_markdown_matrix` with:

```python
def test_terminal_matrix_rates_match_markdown_matrix(tmp_path: object) -> None:
    """Verify each terminal rate is the persisted matrix cell's rate token, row for row.

    The Markdown cells keep their `rate (+Npp)` shape; the terminal shows only the rate
    token of each and carries the footer's deltas on its own `vs baseline` line.
    """
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100%
    seed_arm(tmp_path / "archive", "beta", "baseline", passes=2, total=2)  # 100%
    seed_arm(tmp_path / "archive", "beta", "trial", passes=0, total=2)  # 0%
    bench = report.write_benchmark(
        tmp_path, report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    lines = report.terminal_matrix(bench, tmp_path / "benchmark.md")

    markdown = (tmp_path / "benchmark.md").read_text()
    matrix_section = markdown.split("## Matrix", 1)[1].split("## ", 1)[0]
    markdown_rows = {}
    for line in matrix_section.splitlines():
        if line.startswith("| ") and not line.startswith("| Eval"):
            label, *cells = [cell.strip() for cell in line.strip("|").split("|")]
            markdown_rows[label] = cells
    assert markdown_rows == {
        "archive/alpha": ["50%", "100% (+50pp)"],
        "archive/beta": ["100%", "0% (-100pp)"],
        "All evals": ["75%", "50% (-25pp)"],
    }
    terminal_rows = _terminal_rows(lines)
    assert terminal_rows == {
        "archive/alpha": "50% 100%",
        "archive/beta": "100% 0%",
        "All evals": "75% 50%",
        "vs baseline": "-25pp",
    }
    for label, cells in markdown_rows.items():
        assert terminal_rows[label] == " ".join(cell.split(" ")[0] for cell in cells)
    footer_deltas = [cell.split(" ")[1].strip("()") for cell in markdown_rows["All evals"][1:]]
    assert terminal_rows["vs baseline"] == " ".join(footer_deltas)
```

In `test_terminal_matrix_missing_rates_render_dash`, leave the setup and the existing `_terminal_rows` assertion unchanged and append one assertion at the end of the verify phase:

```python
    assert "vs baseline" not in _terminal_rows(lines)
```

Leave `test_terminal_matrix_absolute_without_baseline` unchanged; it already asserts the row dict has no `vs baseline` entry and that no table line contains `pp`.

Add this new test directly after `test_terminal_matrix_absolute_without_baseline`:

```python
def test_terminal_matrix_renders_bare_rates_with_pooled_delta_line(tmp_path: object) -> None:
    """Verify mixed-width deltas leave the terminal: rates share a right edge per column.

    Per-eval deltas of +67pp and +100pp differ in width; the rates must still end on
    the same column, and only the pooled delta appears, on the `vs baseline` line.
    """
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=3)  # 33%
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=3, total=3)  # 100%, +67pp
    seed_arm(tmp_path / "archive", "beta", "baseline", passes=0, total=2)  # 0%
    seed_arm(tmp_path / "archive", "beta", "trial", passes=2, total=2)  # 100%, +100pp
    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    lines = report.terminal_matrix(bench, tmp_path / "benchmark.md")

    assert lines == [
        "Eval           baseline  trial",
        "archive/alpha       33%   100%",
        "archive/beta         0%   100%",
        "------------------------------",
        "All evals           17%   100%",
        "vs baseline              +83pp",
        f"Report: {tmp_path / 'benchmark.md'}",
    ]
    header, alpha, beta, _rule, footer, versus, _pointer = lines
    assert not any("pp" in line for line in (alpha, beta, footer))
    baseline_edge = header.index("baseline") + len("baseline")
    assert all(line[baseline_edge - 1] == "%" for line in (alpha, beta, footer))
    assert all(line[-1] == "%" for line in (alpha, beta, footer))
    assert versus[:baseline_edge].strip() == "vs baseline"
```

Replace `test_terminal_matrix_color_codes_rates_by_band_and_deltas_by_sign` with:

```python
def test_terminal_matrix_color_codes_rates_by_band_and_deltas_by_sign(
    tmp_path: object,
) -> None:
    """Verify color mode wraps rates green/yellow/red by band and footer deltas by sign."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=2)  # 50% yellow
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)  # 100% green
    seed_arm(tmp_path / "archive", "beta", "baseline", passes=2, total=2)  # 100% green
    seed_arm(tmp_path / "archive", "beta", "trial", passes=0, total=2)  # 0% red, pooled -25 red
    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    lines = report.terminal_matrix(bench, tmp_path / "benchmark.md", color=True)

    alpha = next(line for line in lines if line.startswith("archive/alpha"))
    beta = next(line for line in lines if line.startswith("archive/beta"))
    versus = next(line for line in lines if line.startswith("vs baseline"))
    assert "\x1b[33m50%\x1b[0m" in alpha
    assert "\x1b[32m100%\x1b[0m" in alpha
    assert "\x1b[31m0%\x1b[0m" in beta
    assert "\x1b[31m-25pp\x1b[0m" in versus
```

Replace `test_terminal_matrix_zero_delta_is_yellow_and_default_is_plain` with:

```python
def test_terminal_matrix_zero_delta_is_yellow_and_default_is_plain(tmp_path: object) -> None:
    """Verify a zero pooled delta colors yellow and the default render carries no escapes."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=2, total=2)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=2, total=2)
    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    colored = report.terminal_matrix(bench, tmp_path / "benchmark.md", color=True)
    plain = report.terminal_matrix(bench, tmp_path / "benchmark.md")

    versus = next(line for line in colored if line.startswith("vs baseline"))
    assert "\x1b[33m+0pp\x1b[0m" in versus
    assert not any("\x1b" in line for line in plain)
```

Add this new test directly after it:

```python
def test_terminal_matrix_color_adds_no_width(tmp_path: object) -> None:
    """Verify stripping ANSI codes from the color render yields the plain render exactly."""
    seed_arm(tmp_path / "archive", "alpha", "baseline", passes=1, total=3)
    seed_arm(tmp_path / "archive", "alpha", "trial", passes=3, total=3)
    seed_arm(tmp_path / "archive", "beta", "baseline", passes=0, total=2)
    seed_arm(tmp_path / "archive", "beta", "trial", passes=2, total=2)
    bench = report.build_benchmark(
        report.discover_eval_dirs(tmp_path), label="iteration_01", baseline="baseline"
    )

    colored = report.terminal_matrix(bench, tmp_path / "benchmark.md", color=True)
    plain = report.terminal_matrix(bench, tmp_path / "benchmark.md")

    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    assert [ansi_escape.sub("", line) for line in colored] == plain
```

- [ ] **Step 2: Run the terminal tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/reporting/test_report.py -k terminal_matrix -v`

Expected: the aligns-columns, rates-match-markdown, bare-rates, both color tests, and color-adds-no-width tests FAIL because the render still emits `rate (+Npp)` cells and no `vs baseline` line. `missing_rates_render_dash`, `absolute_without_baseline`, and the rule test PASS.

- [ ] **Step 3: Implement the renderer change**

In `src/benchspec/reporting/report.py`, replace the `_CELL_RE` constant:

```python
_TOKEN_RE = re.compile(r"(?:(?P<rate>\d+%)|(?P<pp>[+-]\d+)pp)$")
```

Replace `_colorize_cell`:

```python
def _colorize_cell(padded_cell: str) -> str:
    """Wrap a padded cell's rate or delta token in an ANSI color; a — or blank cell passes through.

    Runs on the already-padded text so alignment is computed on visible characters;
    the escape codes add zero display width.
    """
    match = _TOKEN_RE.search(padded_cell)
    if match is None:
        return padded_cell

    token = match.group(0)
    color = _rate_ansi(match["rate"]) if match["rate"] else _delta_ansi(match["pp"])

    return padded_cell[: match.start()] + color + token + _RESET
```

Add this helper directly above `terminal_matrix`:

```python
def _split_cell(cell: str) -> tuple[str, str]:
    """Split a matrix cell into its rate and delta tokens: `33% (+5pp)` → (`33%`, `+5pp`).

    `_rate_cell` emits exactly three shapes — `—`, `N%`, `N% (+Mpp)` — so the first
    space separates rate from delta, and a cell without a delta yields an empty one.
    """
    rate, _separator, decorated_delta = cell.partition(" ")
    return rate, decorated_delta.strip("()")
```

Replace `terminal_matrix`:

```python
def terminal_matrix(benchmark: dict, report_path: Path, *, color: bool = False) -> list[str]:
    """Render the matrix as width-aligned plain text for the pytest terminal summary.

    Same rows and rates as the Markdown matrix, but every cell is a bare rate so each
    column shares one right edge; the per-eval deltas stay in `benchmark.md`. The eval
    column is left-aligned and every arm column right-aligned; a rule separates the eval
    rows from the `All evals` footer; when a baseline arm exists, a `vs baseline` line
    under the footer carries each other arm's pooled delta as `+Npp`; the last line
    points at the written report.

    Args:
        benchmark: A built benchmark (see `build_benchmark`).
        report_path: The `benchmark.md` path to name on the closing `Report:` line.
        color: Wrap rates and deltas in ANSI colors — rates by band (green ≥80%,
            yellow ≥50%, red below), deltas by sign (green up, red down, yellow zero).
            Off by default so files and pipes get plain text.

    Returns:
        The lines to print, header first, `Report:` pointer last.
    """
    names, rows = _matrix_cells(benchmark)
    if not names:
        return [f"Report: {report_path}"]

    table_rows = [(label, [_split_cell(cell)[0] for cell in cells]) for label, cells in rows]
    _footer_label, footer_cells = rows[-1]
    pooled_deltas = [_split_cell(cell)[1] for cell in footer_cells]
    if any(pooled_deltas):
        table_rows.append(("vs baseline", pooled_deltas))

    label_width = max(len("Eval"), *(len(label) for label, _cells in table_rows))
    column_widths = [
        max(len(name), *(len(cells[column]) for _label, cells in table_rows))
        for column, name in enumerate(names)
    ]

    def aligned(label: str, cells: list[str]) -> str:
        """Join one row's label and cells into a padded line."""
        padded_cells = [
            cell.rjust(width) for cell, width in zip(cells, column_widths, strict=True)
        ]
        if color:
            padded_cells = [_colorize_cell(cell) for cell in padded_cells]
        return "  ".join([label.ljust(label_width), *padded_cells])

    table_width = label_width + sum(column_widths) + 2 * len(column_widths)

    lines = [aligned("Eval", names)]
    for label, cells in table_rows:
        # `_matrix_cells` puts the pooled footer last; rule it off from the eval rows.
        if label == "All evals":
            lines.append("-" * table_width)
        lines.append(aligned(label, cells))
    lines.append(f"Report: {report_path}")

    return lines
```

Confirm with `grep -n "_CELL_RE" src/ tests/` that nothing else references the old constant name.

- [ ] **Step 4: Run the terminal tests, then the full suite and ruff**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/reporting/test_report.py -k terminal_matrix -v`
Expected: all terminal_matrix tests PASS.

Run: `make test` and `make lint:ruff`
Expected: every test passes, ruff reports no issues. If ruff's formatter or line-length rule complains about the `_colorize_cell` docstring's first line, shorten it to `"""Wrap a padded cell's rate or delta token in an ANSI color; — and blank pass through.` and keep the rest.

- [ ] **Step 5: Commit**

```bash
git add src/benchspec/reporting/report.py tests/reporting/test_report.py
git commit -m "feat: render the terminal matrix as bare rates with a vs baseline line"
```

---

### Task 2: Update the terminal samples and contract in the docs

**Files:**
- Modify: `docs/results.md` (the terminal sample fenced block around lines 102–109 and the paragraph that follows it, starting "Rows, columns, and cells follow the Markdown matrix exactly")
- Modify: `docs/quickstart.md` (the terminal sample fenced block around lines 161–170)

**Interfaces:**
- Consumes: the renderer from Task 1. The samples below are exact renders of the new `terminal_matrix` for the rates each doc already shows.
- Produces: nothing downstream.

- [ ] **Step 1: Replace the sample in `docs/results.md`**

Find the fenced block that begins with the `============================ benchspec benchmark ============================` banner and contains `hello/greets-by-name                  17%  67% (+50pp)      83% (+66pp)`. Replace the whole fenced block with exactly:

````
```
============================ benchspec benchmark ============================
Eval                             baseline  trial  trial-overrides
hello/greets-by-name                  17%    67%              83%
hello-file/writes-greeting-file       17%    83%             100%
-----------------------------------------------------------------
All evals                             17%    75%             100%
vs baseline                                +58pp            +83pp
Report: tmp/evals/iteration_02/benchmark.md
```
````

- [ ] **Step 2: Rewrite the contract paragraph in `docs/results.md`**

Replace the paragraph that begins `Rows, columns, and cells follow the Markdown matrix exactly` and ends `the terminal never changes their contents.` with exactly:

```
Rows and rates follow the Markdown matrix: roster rows (including all-errored evals
as `—`), baseline column first, and the pooled `All evals` footer. Every cell is a
bare rate so each column lines up on one right edge; the per-eval `(+Npp)` deltas
live only in `benchmark.md`. When a baseline arm exists, a `vs baseline` line under
the footer carries each other arm's pooled delta as `+Npp` (blank under the baseline
column); without a baseline the table ends at `All evals`. The terminal table carries
no harness labels or noise bands; `Report:` points at the `benchmark.md` that does.
A rule separates the eval rows from the pooled footer, and on a terminal that
supports color, rates are color-coded by band (green from 80%, yellow from 50%,
red below) and the `vs baseline` deltas by sign (green up, red down, yellow zero);
files and pipes always get plain text. The table is presentation only:
`benchmark.json` and `benchmark.md` are the artifacts, and the terminal never
changes their contents.
```

- [ ] **Step 3: Replace the sample in `docs/quickstart.md`**

Find the fenced block that contains `hello/greets-by-name        0%  100% (+100pp)`. Replace the whole fenced block with exactly:

````
```
skills/hello/evals/hello/greets-by-name.eval.md ..                       [100%]

============================ benchspec benchmark ============================
Eval                  baseline   trial
hello/greets-by-name        0%    100%
--------------------------------------
All evals                   0%    100%
vs baseline                     +100pp
Report: tmp/evals/iteration_01/benchmark.md
```
````

Then change the sentence directly below it from

```
On a color terminal, rates are color-coded by band (green from 80%, yellow from
50%, red below) and deltas by sign.
```

to

```
On a color terminal, rates are color-coded by band (green from 80%, yellow from
50%, red below) and the `vs baseline` delta by sign.
```

- [ ] **Step 4: Verify the samples match the renderer**

Run this from the repo root and confirm it prints `samples match`. It locates each
sample by its `Eval … baseline` header line rather than by fence, because a
preceding ```bash fence would confuse a fence-matching regex.

```bash
uv run python - <<'EOF'
from pathlib import Path

def sample_block(doc: Path) -> list[str]:
    """Return the sample's table lines: the `Eval … baseline` header through the line before `Report:`."""
    lines = doc.read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("Eval") and "baseline" in line)
    end = next(index for index, line in enumerate(lines) if index > start and line.startswith("Report:"))
    return lines[start:end]

def render(rows, names):
    """Mirror terminal_matrix's layout for the given label/cell rows."""
    label_width = max(len("Eval"), *(len(label) for label, _cells in rows))
    widths = [max(len(name), *(len(cells[column]) for _label, cells in rows)) for column, name in enumerate(names)]
    def aligned(label, cells):
        return "  ".join([label.ljust(label_width), *[cell.rjust(width) for cell, width in zip(cells, widths)]])
    out = [aligned("Eval", names)]
    for label, cells in rows:
        if label == "All evals":
            out.append("-" * (label_width + sum(widths) + 2 * len(widths)))
        out.append(aligned(label, cells))
    return out

results = render(
    [("hello/greets-by-name", ["17%", "67%", "83%"]),
     ("hello-file/writes-greeting-file", ["17%", "83%", "100%"]),
     ("All evals", ["17%", "75%", "100%"]),
     ("vs baseline", ["", "+58pp", "+83pp"])],
    ["baseline", "trial", "trial-overrides"],
)
quickstart = render(
    [("hello/greets-by-name", ["0%", "100%"]),
     ("All evals", ["0%", "100%"]),
     ("vs baseline", ["", "+100pp"])],
    ["baseline", "trial"],
)
assert sample_block(Path("docs/results.md")) == results, "results.md drifted"
assert sample_block(Path("docs/quickstart.md")) == quickstart, "quickstart.md drifted"
print("samples match")
EOF
```

Also run `grep -n "pp)" docs/results.md docs/quickstart.md` and confirm the only remaining `(+Npp)` mentions are in prose describing the Markdown matrix, not inside a terminal sample block.

- [ ] **Step 5: Commit**

```bash
git add docs/results.md docs/quickstart.md
git commit -m "docs: re-render the terminal matrix samples as bare rates with a vs baseline line"
```
