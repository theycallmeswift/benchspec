# Phase 9: End-to-End Evals and [tool.evalspec] Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author evalspec's first real `[tool.evalspec]` config and a `hello` end-to-end
eval suite (`evals/e2e/`) that turns the prose walkthrough in `docs/quickstart.md` into a
runnable in-repo suite, and wire a `make e2e` target that exercises the whole product
path (config → arms → sandbox → binder → judge → matrix + artifacts) end-to-end.

**Architecture:** A three-arm `e2e` eval set (`baseline` / `trial` / `trial-overrides`)
plus a cross-family Codex judge live in `pyproject.toml`. The skill under test, its two
evals, and an arm-branching `setup.sh` live under `evals/e2e/hello/`, discovered via the
`evals` search path. `make e2e` runs `evalspec run --set e2e`; `make evals` keeps running
the binder corpus unchanged, and the redundant `evals:binder` alias is removed.

**Tech Stack:** Python 3.11+, pytest, TOML (`[tool.evalspec]` pyproject config), Markdown
eval format (`.eval.md`), bash (`setup.sh`), `uv`, `make`.

## Global Constraints

- Python 3.11+ (`pyproject.toml` `requires-python = ">=3.11"`).
- `[tool.evalspec]` key casing: top-level `default-set` is kebab-case; every other key
  (`harness`, `model`, `effort`, `env`, `harness_args`, `baseline`, `arms`, `name`,
  `eval_paths`) is snake_case.
- No new checkers — the registry (`file_exists`, `not_file_exists`, `glob_count`,
  `sha256_match`, `frontmatter_has`, `regex`, `skill_invoked`, `not_skill_invoked`) is
  sufficient; authors write only plain-prose `- [ ]` assertions, never checker syntax.
- No CI microVM job and no no-boot collect/lint CI tier — `make e2e` stays a manual/local
  target; CI stays `make test` + `make lint`.
- No base-image digest pinning — the suite uses floating `ubuntu:latest` / `version =
  "latest"`.
- Every task arm uses `harness = "claude-code"`; only the judge uses `codex` — no
  multi-harness task arms (that's a richer benchmark, not this smoke test).
- Conventional commit messages (`feat:`, `test:`, `docs:`, `chore:`).
- Test style (`docs/style/development.md`): DAMP not DRY — each test reads as its own
  story, some duplication is fine; four-phase layout (setup / exercise / verify /
  teardown) with phases separated by blank lines; Google-style docstrings
  (`Args:`/`Returns:`/`Raises:`) on every function including trivial helpers;
  `from __future__ import annotations` is always the first import.
- Ruff (`pyproject.toml` `[tool.ruff]`): line-length 100, `pydocstyle` convention
  `google`, `ANN`/`D`/`PT`/`B` rules enforced — new Python code needs full type
  annotations and docstrings. `make lint` also runs `houserules` on changed/untracked
  Python files (needs `GEMINI_API_KEY`).
- microsandbox is **not installed** in this dev venv (only host binaries exist) — the
  real `make e2e` VM run cannot execute in this environment. This is a known,
  pre-existing local gap (not introduced by this plan); every task below is verified
  without booting a VM, and `make e2e` itself is documented as a manual/paid gate, not a
  task-completion requirement.

---

## Task 1: `[tool.evalspec]` e2e config + config-resolution tests

**Goal:** Declare the `e2e` eval set (three arms, a shared env, a named baseline) and its
Codex judge in `pyproject.toml`, and prove — without booting a VM — that it resolves to
the right arms, that env inherits and overrides correctly on `trial-overrides`, and that
the judge is a cross-family Codex judge distinct from the task harness.

A recon check against this repo's real `[tool.evalspec]`-shaped table showed that the
*default* `eval_paths` (`skills`, `tests`, `evals`, `benchmarks`) picks up an unrelated
fixture, `tests/fixtures/activation/evals/activation-demo/eval.md`, when discovery is run
against the real repo root — that file exists solely as a fixture for
`tests/test_analyze.py` (it points `discover_eval_cases` at that fixture subtree
directly, never at the real repo root) but would still be picked up by real discovery
once a real `[tool.evalspec]` set exists to run it against, breaking the "six cells"
expectation in this plan's own verification. Declaring `eval_paths = ["evals"]`
explicitly scopes discovery (and `evalspec lint`, which shares the same resolution path)
to the `evals/` tree, where the new `hello` suite and the (eval-file-free) binder corpus
both live — confirmed empirically:

```
$ uv run python -c "
from pathlib import Path
from evalspec.discovery import discover_eval_cases
cases = discover_eval_cases(Path('.').resolve())
for c in cases: print(c.group, c.eval_id, c.eval_file)
"
activation-demo activation-demo /…/tests/fixtures/activation/evals/activation-demo/eval.md
```

**Files:**
- Modify: `pyproject.toml` (append a new `[tool.evalspec]` block after
  `[tool.pytest.ini_options]`, currently the file's last section, ending at line 97)
- Create: `tests/test_e2e_suite.py`

**Interfaces:**
- Consumes:
  - `evalspec.arms.parse_sets(table: dict) -> tuple[dict[str, RawSet], str]`
    (`src/evalspec/arms.py:91`)
  - `evalspec.arms.resolve_set(rawsets: dict, default_set: str, *, set_name: str | None
    = None, model=None, harness=None, effort=None, env=None, models=None,
    environ=None) -> Set` (`src/evalspec/arms.py:232`) — `Set.arms: list[Arm]`,
    `Set.baseline: str | None`; `Arm.name: str`, `Arm.harness: str`, `Arm.model: str`,
    `Arm.effort: str`, `Arm.env: dict`
  - `evalspec.discovery.pyproject_table(repo_root: Path) -> dict`
    (`src/evalspec/discovery.py:83`) — returns the raw `[tool.evalspec]` table (`{}` if
    absent)
  - `evalspec.judges.config.resolve_judge_config(*, pyproject_table: dict | None = None,
    scratch_table=None, cli_table=None) -> JudgeConfig`
    (`src/evalspec/judges/config.py:134`) — `JudgeConfig.harness: str`,
    `JudgeConfig.model: str`. Callers pass the `judge` sub-table
    (`pyproject_table(repo_root).get("judge")`), never the whole `[tool.evalspec]` table
    — this is exactly how `src/evalspec/plugin.py:371` calls it.
- Produces: `tests/test_e2e_suite.py`'s `REPO_ROOT` constant
  (`Path(__file__).resolve().parents[1]`) — Task 2 appends to this same file and reuses
  `REPO_ROOT`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_e2e_suite.py`:

```python
"""Config-resolution and discovery/parse tests for evalspec's own end-to-end suite.

Exercises the real `[tool.evalspec]` config in this repo's `pyproject.toml` (the `e2e`
set and its Codex judge) and the real `evals/e2e/hello/` suite against the live parsers
— the same "authored example must parse for real" pattern as
`tests/test_readme_examples.py`, extended to config resolution and eval discovery.
"""

from __future__ import annotations

from pathlib import Path

from evalspec.arms import parse_sets, resolve_set
from evalspec.discovery import pyproject_table
from evalspec.judges.config import resolve_judge_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_e2e_set_resolves_to_three_arms_with_declared_config() -> None:
    """Verify the e2e set resolves to three arms with the declared harness/model/effort."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    assert default_set == "e2e"
    assert resolved.baseline == "baseline"
    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert set(arms_by_name) == {"baseline", "trial", "trial-overrides"}
    assert arms_by_name["baseline"].harness == "claude-code"
    assert arms_by_name["baseline"].model == "sonnet"
    assert arms_by_name["baseline"].effort == "medium"
    assert arms_by_name["trial"].harness == "claude-code"
    assert arms_by_name["trial"].model == "sonnet"
    assert arms_by_name["trial"].effort == "medium"
    assert arms_by_name["trial-overrides"].harness == "claude-code"
    assert arms_by_name["trial-overrides"].model == "opus"
    assert arms_by_name["trial-overrides"].effort == "high"


def test_e2e_trial_overrides_env_inherits_style_and_overrides_locale() -> None:
    """Verify trial-overrides inherits GREETING_STYLE and overrides GREETING_LOCALE."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)

    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    arms_by_name = {arm.name: arm for arm in resolved.arms}
    assert arms_by_name["baseline"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-US",
    }
    assert arms_by_name["trial-overrides"].env == {
        "GREETING_STYLE": "formal",
        "GREETING_LOCALE": "en-GB",
    }


def test_e2e_judge_is_codex_and_distinct_from_the_task_harness() -> None:
    """Verify the e2e judge is a cross-family Codex judge, distinct from the task harness."""
    table = pyproject_table(REPO_ROOT)
    rawsets, default_set = parse_sets(table)
    resolved = resolve_set(rawsets, default_set, set_name="e2e")

    judge = resolve_judge_config(pyproject_table=table.get("judge"))

    assert judge.harness == "codex"
    assert judge.model == "gpt-5.5"
    assert judge.harness != resolved.arms[0].harness
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_e2e_suite.py -v`
Expected: FAIL (error, not assertion failure) — `pyproject_table(REPO_ROOT)` returns `{}`
(no `[tool.evalspec]` block exists yet), so `parse_sets({})` raises:
```
evalspec.schema.SchemaError: [tool.evalspec] needs at least one eval set
([tool.evalspec.sets.<name>] with `arms`, optional set-level harness/model/effort/env
defaults, and a `baseline`). The flat [[tool.evalspec.arms]] + `reference` shape is no
longer supported — wrap your arms in a named set and add `default-set`.
```

- [ ] **Step 3: Write minimal implementation**

Append to `pyproject.toml`, after `[tool.pytest.ini_options]` (the file's last section):

```toml

[tool.evalspec]
default-set = "e2e"
# Scope discovery to evals/ — the default (skills, tests, evals, benchmarks) would also
# pick up tests/fixtures/activation/evals/activation-demo/eval.md, an unrelated
# test_analyze.py fixture, breaking the "six cells" collection count below.
eval_paths = ["evals"]

[tool.evalspec.sets.e2e]
harness  = "claude-code"
model    = "sonnet"
effort   = "medium"
baseline = "baseline"
env = { GREETING_STYLE = "formal", GREETING_LOCALE = "en-US" }
arms = [
  { name = "baseline" },                                                   # installs no skill
  { name = "trial" },                                                      # installs the skill, set defaults
  { name = "trial-overrides", model = "opus", effort = "high", env = { GREETING_LOCALE = "en-GB" } },
]

[tool.evalspec.judge]
harness = "codex"
model   = "gpt-5.5"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_e2e_suite.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_e2e_suite.py
git commit -m "feat: declare the e2e eval set and Codex judge in [tool.evalspec]"
```

---

## Task 2: The `hello` e2e suite (skill, two evals, setup.sh) + discovery/parse tests

**Goal:** Author the skill under test and its two evals under `evals/e2e/hello/`, and
prove — without booting a VM — that both are discovered under the `evals` search path
with the exact `(group, eval_id)` identities the six-cell matrix depends on, that each
parses to a non-empty `## Prompt` and `## Assertions`, and that `setup.sh` itself has
valid syntax, no-ops cleanly on the `baseline` arm, and resolves its `../../SKILL.md`
reference to the real skill file — all without booting a VM.

Design: the skill writes an exact, deterministic first line (`Hello, <name>!`) plus a
warm second line, regardless of `GREETING_LOCALE` — so both the file-content assertion
and the tone assertion stay true across all three arms even though `trial-overrides`
runs with a different `GREETING_LOCALE`. The second line's wording branches only on
`GREETING_STYLE` (`formal` vs. anything else), never on locale, and both branches read
as warm — a first pass that had the skill emit `Style:`/`Locale:` metadata lines instead
of prose was caught during recon: a judge would plausibly grade raw metadata lines as
robotic, which would fail the trial arm's own tone assertion and contradict "WITH the
skill passes everything." `greets-by-name.eval.md` carries one
`skill_invoked`-bindable line, one deterministic file-content line, and one subjective
tone line the binder must punt to the judge, so a capable agent *without* the skill still
passes the tone line (partial baseline) but fails the other two; `writes-greeting-file.eval.md`
adds a `regex`-bindable content check. Verified empirically against the real parsers
(`discover_eval_cases`, `evalspec.lint.run`) before locking this content in: both files
parse to the expected `(group, eval_id)` pairs with non-empty prompt/assertions, and
`evalspec lint` reports `0 warning(s)` / exit code `0` against them.

**Files:**
- Create: `evals/e2e/hello/SKILL.md`
- Create: `evals/e2e/hello/evals/hello/greets-by-name.eval.md`
- Create: `evals/e2e/hello/evals/hello/writes-greeting-file.eval.md`
- Create: `evals/e2e/hello/evals/hello/setup.sh` (executable)
- Modify: `tests/test_e2e_suite.py`

**Interfaces:**
- Consumes: `evalspec.discovery.discover_eval_cases(repo_root: Path, eval_paths:
  list[str] | None = None) -> list[EvalCase]` (`src/evalspec/discovery.py:231`) —
  `EvalCase.group: str`, `EvalCase.eval_id: str` (property), `EvalCase.prompt: str`
  (property), `EvalCase.assertions: list[str]` (property). `REPO_ROOT` from Task 1. Also
  consumes the on-disk `evals/e2e/hello/evals/hello/setup.sh` this task creates, invoked
  the same way `run_setup_sh` invokes it host-side (`src/evalspec/sandbox.py:184`: `cd
  {eval_dir}; bash ./setup.sh`) — via the stdlib `subprocess` module, not a real sandbox.
- Produces: the on-disk suite at `evals/e2e/hello/` that Task 3's `make e2e` target runs
  and Task 4's docs cross-reference.

- [ ] **Step 1: Write the failing test**

Add two stdlib imports, the `discover_eval_cases` import, and four new tests to
`tests/test_e2e_suite.py`. The stdlib import block (after `from __future__ import
annotations`) becomes:

```python
import os
import subprocess
from pathlib import Path
```

and the `evalspec.discovery` import line becomes `from evalspec.discovery import
discover_eval_cases, pyproject_table`. The new tests are appended after
`test_e2e_judge_is_codex_and_distinct_from_the_task_harness`:

```python
def test_hello_evals_are_discovered_with_expected_identities() -> None:
    """Verify both hello evals are discovered with a non-empty prompt and assertions.

    Calls `discover_eval_cases(REPO_ROOT)` with no `eval_paths` override — the same call
    shape production uses (`analyze.py:66`, `lint.py:72`) — so this test exercises the
    real `eval_paths = ["evals"]` in `pyproject.toml` (Task 1), not a hardcoded stand-in.
    That also proves the scoping does its job: `tests/fixtures/activation/evals/
    activation-demo/eval.md`, which the *default* `eval_paths` (`skills`, `tests`,
    `evals`, `benchmarks`) would pick up, must NOT appear in the discovered set.
    """
    cases = discover_eval_cases(REPO_ROOT)
    groups = {case.group for case in cases}
    hello_cases = {(case.group, case.eval_id): case for case in cases if case.group == "hello"}

    assert set(hello_cases) == {
        ("hello", "greets-by-name"),
        ("hello", "writes-greeting-file"),
    }
    for case in hello_cases.values():
        assert case.prompt
        assert case.assertions
    assert "activation-demo" not in groups


def test_setup_sh_has_valid_bash_syntax() -> None:
    """Verify setup.sh parses as valid bash without executing any of it."""
    setup_sh = REPO_ROOT / "evals/e2e/hello/evals/hello/setup.sh"

    result = subprocess.run(
        ["bash", "-n", str(setup_sh)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_setup_sh_baseline_arm_is_a_no_op() -> None:
    """Verify the baseline branch exits 0 before any filesystem write.

    Runs the real script exactly as `run_setup_sh` invokes it host-side
    (`src/evalspec/sandbox.py:184`: `cd <eval_dir>; bash ./setup.sh`), with
    `EVALSPEC_ARM=baseline`. This does NOT exercise the trial/trial-overrides branches —
    those `mkdir`/`cp` into the guest-only path `/home/evalspec/skills`, which only
    exists inside a booted microVM, so they stay real-run-only (`make e2e`).
    """
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"

    result = subprocess.run(
        ["bash", "./setup.sh"],
        cwd=eval_dir,
        env={**os.environ, "EVALSPEC_ARM": "baseline"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_setup_sh_relative_skill_path_resolves_to_the_real_skill_md() -> None:
    """Verify setup.sh's `../../SKILL.md` reference resolves to the real skill file."""
    eval_dir = REPO_ROOT / "evals/e2e/hello/evals/hello"

    skill_md = (eval_dir / "../../SKILL.md").resolve()

    assert skill_md == REPO_ROOT / "evals/e2e/hello/SKILL.md"
    assert skill_md.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_e2e_suite.py -k "hello_evals_are_discovered or setup_sh" -v`
Expected: FAIL, all four (no `evals/e2e/` tree exists yet):
- `test_hello_evals_are_discovered_with_expected_identities` — `assert set() ==
  {('hello', 'greets-by-name'), ('hello', 'writes-greeting-file')}` (the
  `activation-demo` assertion never runs because the `set(hello_cases)` assertion above
  it fails first).
- `test_setup_sh_has_valid_bash_syntax` — `assert 127 == 0` (`bash -n` on a missing path
  prints `No such file or directory` and exits `127`).
- `test_setup_sh_baseline_arm_is_a_no_op` — ERROR, `FileNotFoundError: [Errno 2] No such
  file or directory: '.../evals/e2e/hello/evals/hello'` (`subprocess.run`'s `cwd` doesn't
  exist yet).
- `test_setup_sh_relative_skill_path_resolves_to_the_real_skill_md` — `assert False`
  on `skill_md.is_file()` (path arithmetic already resolves correctly via
  `Path.resolve()`'s non-strict lexical normalization, but `SKILL.md` doesn't exist on
  disk yet).

- [ ] **Step 3: Write minimal implementation**

Create `evals/e2e/hello/SKILL.md`:

```markdown
---
name: hello
description: Greet a named person with a warm, deterministic greeting file, honoring GREETING_STYLE and GREETING_LOCALE from the environment.
---

# Hello

When asked to greet someone by name, write their greeting to `./Greetings/<name>.md`
(using the person's name exactly as given, unchanged case) with this exact
deterministic body:

```
Hello, <name>!
It's a genuine pleasure to meet you — welcome!
```

Read `GREETING_STYLE` and `GREETING_LOCALE` from the environment (default to `casual`
and `en-US` if either is unset): when `GREETING_STYLE` is `formal`, write the second
line exactly as shown; when it is anything else, write it as `Hey, so glad you're
here!` instead. `GREETING_LOCALE` does not change the wording — it exists for callers
that need locale-aware formatting later. Do not add anything else to the file — no
extra prose, no markdown headers, no signature.
```

Create `evals/e2e/hello/evals/hello/greets-by-name.eval.md`:

```markdown
---
---

## Prompt

You are working in a workspace rooted at your current working directory. Greet Alice by name.

## Assertions

- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'
- [ ] Skill `hello` invoked
- [ ] The greeting feels warm and personable, not curt or robotic
```

Create `evals/e2e/hello/evals/hello/writes-greeting-file.eval.md`:

```markdown
---
---

## Prompt

You are working in a workspace rooted at your current working directory. Greet Bob by name.

## Assertions

- [ ] ./Greetings/Bob.md matches the regex 'Hello, Bob!'
```

Create `evals/e2e/hello/evals/hello/setup.sh`, then `chmod +x` it:

```bash
#!/usr/bin/env bash
set -e
if [ "$EVALSPEC_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/evalspec/skills/hello
cp ../../SKILL.md /home/evalspec/skills/hello/SKILL.md
```

```bash
chmod +x evals/e2e/hello/evals/hello/setup.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_e2e_suite.py -v`
Expected: PASS — 7 passed (the 3 tests from Task 1 plus this task's 4).

Also confirm the suite lints clean (static, no credentials/sandbox needed):

Run: `uv run evalspec lint`
Expected: `0 warning(s)`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add evals/e2e/hello/SKILL.md \
  evals/e2e/hello/evals/hello/greets-by-name.eval.md \
  evals/e2e/hello/evals/hello/writes-greeting-file.eval.md \
  evals/e2e/hello/evals/hello/setup.sh \
  tests/test_e2e_suite.py
git commit -m "feat: author the hello e2e suite (skill, two evals, setup.sh)"
```

---

## Task 3: `make e2e` target + `evals:binder` removal

**Goal:** Add a `make e2e` target that runs the new suite end-to-end, and remove the
redundant `evals:binder` alias so `make e2e` (does the framework work) and `make evals`
(binder quality) read as two distinct jobs, per the Makefile's `## ` trailing-comment
help convention.

**Files:**
- Modify: `Makefile`

**Interfaces:**
- Consumes: the `evalspec run --set e2e` CLI invocation (the `run` subcommand is
  registered at `src/evalspec/__main__.py:124`; `--set` resolves the eval set exactly as
  `evalspec run --set default` does in `docs/quickstart.md` Step 5).
- Produces: `make e2e` — Task 4's README/quickstart cross-references name this target.

Makefile changes have no pytest cycle (`Makefile` isn't Python), so this task substitutes
a grep/dry-run check for the usual test-first cycle.

- [ ] **Step 1: Confirm the target doesn't exist yet**

Run: `grep -n 'e2e' Makefile`
Expected: no output (no match) — the target doesn't exist.

- [ ] **Step 2: Confirm the alias currently exists**

Run: `grep -cF 'evals\:binder' Makefile`
Expected: `2` (the `.PHONY` entry and the target line). `-F` (fixed-string) matters here:
the Makefile's literal text is `evals\:binder` (a real backslash before the colon,
`Makefile`'s own escaping convention for a `:` inside a target name), and a bare BRE
pattern silently drops the backslash and still matches `evals:binder` (0 hits either
way) — verified empirically against the current Makefile before writing this step.

- [ ] **Step 3: Write the implementation**

Replace `Makefile` in full:

```makefile
.PHONY: help install test e2e evals lint lint\:ruff lint\:houserules clean
.DEFAULT_GOAL := help

help:  ## Show this help
	@awk '/^[a-zA-Z0-9_:\\-]+:.*## / {t=$$0; sub(/:[ \t]*##.*/,"",t); gsub(/\\/,"",t); d=$$0; sub(/^.*## /,"",d); printf "  \033[36m%-11s\033[0m %s\n", t, d}' $(MAKEFILE_LIST)

install:  ## Create the venv and install dev dependencies
	uv sync

test:  ## Run the unit test suite
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester

e2e:  ## Run evalspec's own end-to-end suite (real microVMs; needs claude+codex CLIs and provider credentials)
	uv run evalspec run --set e2e

# Keep modest: high fan-out trips the Gemini call's ~60s timeout (12-way -> throttling).
BINDER_WORKERS ?= 6
evals:  ## Run the binder corpus (binder quality, not framework function). Pass EVAL_ARGS="--collect-only -q" to dry-run collection.
	uv run pytest -m binder_corpus -n $(BINDER_WORKERS) evals/binder $(EVAL_ARGS)

lint:  ## Lint with Ruff and houserules
	$(MAKE) lint:ruff
	$(MAKE) lint:houserules

lint\:ruff:  ## Lint with Ruff
	uv run ruff check .

LINT_BASE ?= origin/dev
lint\:houserules:  ## Lint changed and new Python files with houserules (needs GEMINI_API_KEY)
	uv run houserules --base "$$(git merge-base $(LINT_BASE) HEAD)" --verbose .
	@untracked_python_files="$$(git ls-files --others --exclude-standard -- '*.py')"; \
	if [ -n "$$untracked_python_files" ]; then \
		uv run houserules --verbose $$untracked_python_files; \
	fi

clean:  ## Remove the venv and Python caches
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

- [ ] **Step 4: Verify**

Run: `grep -n 'e2e' Makefile`
Expected: two matches — the `.PHONY` line and the `e2e:  ##` target line.

Run: `grep -cF 'evals\:binder' Makefile`
Expected: `0`.

Run: `make -n e2e` (dry-run — prints the recipe without executing it, no VM/credentials needed)
Expected:
```
uv run evalspec run --set e2e
```

Run: `make help`
Expected: `e2e` and `evals` both listed with distinct one-line descriptions, no
`evals:binder` line.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "chore: add make e2e, remove the redundant evals:binder alias"
```

---

## Task 4: Docs cross-references

**Goal:** Point `README.md` and `docs/quickstart.md` at the real, runnable `evals/e2e/`
suite so a reader lands on working code instead of only prose.

**Files:**
- Modify: `README.md`
- Modify: `docs/quickstart.md`

**Interfaces:**
- Consumes: `evals/e2e/hello/` (Task 2) and the `make e2e` target (Task 3) — this task
  only adds prose links to both, no code changes.

Docs changes have no pytest cycle; this task substitutes a grep check for the usual
test-first cycle.

- [ ] **Step 1: Confirm the cross-references don't exist yet**

Run: `grep -n 'evals/e2e' README.md docs/quickstart.md`
Expected: no output (no match).

- [ ] **Step 2: Edit README.md**

In `README.md`, immediately after the paragraph ending `Full walkthrough:
[\`docs/quickstart.md\`](docs/quickstart.md).` (the last line of the `## Quickstart`
section, before the `## Host requirements` heading), insert:

```markdown

evalspec dogfoods itself: [`evals/e2e/`](evals/e2e/) is the project's own end-to-end
suite — a real `[tool.evalspec]` config, a `hello` skill, and its evals, run with `make
e2e`. It's the framework's own smoke test that the whole path (config → arms → sandbox →
binder → judge → matrix) still works, and a reference `[tool.evalspec]` + `.eval.md` +
`setup.sh` shape to copy from.
```

- [ ] **Step 3: Edit docs/quickstart.md**

In `docs/quickstart.md`, immediately after the paragraph ending `A malformed suite fails
here with the offending path quoted.` (the last line of `## Step 3 — Write the eval`,
before the `## Step 4 — Build the snapshot` heading), insert:

```markdown

This walkthrough's `hello` skill and its two evals also live in-repo as a runnable
suite, not just prose: [`evals/e2e/hello/`](../evals/e2e/hello/) follows exactly this
shape — `SKILL.md`, `evals/hello/{greets-by-name,writes-greeting-file}.eval.md`, and
`evals/hello/setup.sh` — under `evals/` instead of a user project's `skills/`, wired to
the repo's own `e2e` eval set and run with `make e2e`.
```

- [ ] **Step 4: Verify**

Run: `grep -n 'evals/e2e' README.md docs/quickstart.md`
Expected: one match in each file.

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_readme_examples.py -v`
Expected: PASS — the README edit didn't touch the embedded ` ```markdown ` eval example
block that test parses, so both tests still pass.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/quickstart.md
git commit -m "docs: cross-reference the evals/e2e/ suite and make e2e"
```

---

## Verification

Run after all four tasks land:

- `make test` — Expected: full unit suite green, including the 7 tests in
  `tests/test_e2e_suite.py` (3 from Task 1, 4 from Task 2).
- `make lint` — Expected: Ruff + houserules pass on `tests/test_e2e_suite.py` (the only
  new/modified Python file).
- `uv run evalspec lint` — Expected: `0 warning(s)`, exit code `0` (assertions in both
  `hello` evals are fairly gradable; verified in Task 2).
- `uv run evalspec run --set e2e -- --collect-only` — Expected: 6 collected items —
  `test_eval[hello-greets-by-name-baseline]`, `test_eval[hello-greets-by-name-trial]`,
  `test_eval[hello-greets-by-name-trial-overrides]`, `test_eval[hello-writes-greeting-file-baseline]`,
  `test_eval[hello-writes-greeting-file-trial]`, `test_eval[hello-writes-greeting-file-trial-overrides]`
  — no `activation-demo` cell, because `eval_paths = ["evals"]` (Task 1) scopes discovery
  away from `tests/fixtures/`.
- `grep -n 'e2e' Makefile` — Expected: the `.PHONY` entry and the `e2e:  ##` target.
- `grep -cF 'evals\:binder' Makefile` — Expected: `0` (`-F` fixed-string matches the
  literal backslash-colon `Makefile` uses to escape a `:` inside a target name).
- `make e2e` — the full real-VM run: manual/paid, **not part of this plan's completion
  criteria**. Needs `claude` + `codex` CLIs on `PATH`,
  `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` + `GEMINI_API_KEY` exported or in
  `.env`, and a Python 3.11–3.13 venv with the `microsandbox` Python client installed
  (this dev venv does not have it — see Global Constraints). When it can run: exits `0`,
  prints one Δ line per contrast arm vs. `baseline` with `baseline` at a partial rate
  below `trial`, and writes `meta.json` (`format_version` 2), `benchmark.json`
  (`format_version` 3), `benchmark.md` (matrix + `rate (+Npp)` cells + `All evals` +
  Provenance), `index.jsonl` (harness/model/effort rows), and a per-sample
  `provenance.json`. If it cannot run in a given environment, note it as a known,
  pre-existing local gap rather than a task failure.

---

## Self-Review

**1. Spec coverage** — every section of issue #40 maps to a task:
- Solution's `[tool.evalspec]` TOML → Task 1 (plus the `eval_paths` addition, justified
  by the empirical `activation-demo` collision found during recon and documented inline).
- "The skill under test, its two evals, and the arm-branching `setup.sh`" → Task 2.
- "`make e2e` runs `evalspec run --set e2e` end-to-end" → Task 3.
- Implementation Decisions bullets (three consolidated arms; two evals under one group;
  partial→higher baseline; `setup.sh` branching on `$EVALSPEC_ARM`; Codex judge; `make
  e2e`/`make evals` as two targets; frozen v2/v3 artifact contract) → Tasks 1–3 implement
  the config/files/target side; the frozen-artifact-contract assertions are explicitly
  real-run-only (Behavior/Interface testing tiers) and called out as such in Verification
  rather than faked as no-boot unit tests.
- Testing Plan → Logic tier is Tasks 1–2's seven unit tests (config resolution incl.
  baseline naming and judge distinctness, env inherit/override, discovery/parse of both
  evals against the real `eval_paths` config with an explicit check that it excludes the
  `activation-demo` fixture, plus three no-VM `setup.sh` contract tests — syntax check,
  a real `bash ./setup.sh` run of the `baseline` no-op branch, and pinning the
  `../../SKILL.md` relative path); Behavior/Interface tiers are explicitly real-VM-only
  (the `trial`/`trial-overrides` branches that write to the guest-only
  `/home/evalspec/skills` path are not exercised host-side) and covered by the
  Verification section's `make e2e` entry, not faked as unit tests.
- Documentation Plan (`Makefile`, `README.md`, `docs/quickstart.md`) → Tasks 3–4.
- Out of Scope items (new checkers, CI microVM job, binder corpus changes, image
  pinning, multi-harness task arms) are restated as Global Constraints so no task
  drifts into them.

**2. Placeholder scan** — no "TBD"/"similar to Task N"/unshown code found; every step
that changes a file shows its complete content (full `pyproject.toml` block, full
`Makefile` rewrite, full `SKILL.md`/`.eval.md`/`setup.sh` bodies, full test file
contents, full doc paragraphs).

**3. Type/name consistency** — checked across tasks: `Arm.name/.harness/.model/.effort/.env`
(Task 1) match the fields asserted on in Task 1's tests; `Set.arms/.baseline` (Task 1)
match `resolved.arms`/`resolved.baseline` usage in Task 2's test; `EvalCase.group/.eval_id/
.prompt/.assertions` (Task 2) match `discover_eval_cases`'s real return type
(`src/evalspec/discovery.py:22-64`); `JudgeConfig.harness/.model` (Task 1) match
`resolve_judge_config`'s real return type (`src/evalspec/judges/config.py:68-81`);
`REPO_ROOT` is defined once in Task 1 and reused verbatim in Task 2, never redefined.
All config values (`e2e`, `baseline`/`trial`/`trial-overrides`, `claude-code`/`sonnet`/
`medium`, `opus`/`high`, `GREETING_STYLE`/`GREETING_LOCALE`, `formal`/`en-US`/`en-GB`,
`codex`/`gpt-5.5`) are identical between the `pyproject.toml` block (Task 1) and the
assertions that check them (Task 1's tests).

Every recon claim used to write this plan (config parsing signatures, discovery
defaults, the `.eval.md` format, the checker registry, `setup.sh` auto-discovery, the
judge resolution path, the Makefile's help convention, `pyproject.toml`'s current last
section) was spot-verified against the live worktree at plan time — including running
the real parsers/`evalspec lint` against the exact `SKILL.md`/`.eval.md`/`setup.sh`
content this plan specifies, and running `discover_eval_cases` against the real repo
root to confirm the `eval_paths` scoping requirement in Task 1.

Two substantive defects were caught and fixed during drafting, not deferred as open
questions:
1. **`eval_paths = ["evals"]` is an addition beyond the issue's illustrative
   `[tool.evalspec]` snippet** — needed because default discovery picks up an unrelated
   `tests/fixtures/` fixture and would break the "six cells" collection count (empirical
   proof in Task 1). This is a deviation from "exact TOML from the issue body" and should
   be called out to whoever ratifies this plan, not silently assumed.
2. **The first `SKILL.md` draft emitted raw `Style: … / Locale: …` metadata lines**,
   which a judge would plausibly grade as robotic — failing the trial arm's own tone
   assertion and contradicting the locked design decision "WITH the skill passes
   everything." Task 2's `SKILL.md` was rewritten so both style branches read as warm
   prose while keeping the first line exact and deterministic; re-verified against the
   real parser and `evalspec lint` after the fix.
