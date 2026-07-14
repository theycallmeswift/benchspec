# Phase 10 PR 1: Package Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `src/evalspec/`'s ~24 flat modules into seven lifecycle packages (`specs/`, `config/`, `runners/`, `orchestration/`, `sandbox/`, `grading/`, `reporting/`), decompose `plugin.py` into thin pytest hooks plus extracted pure logic, and mirror the test tree — with **zero behavior change**. This is PR 1 of issue #42 (Phase 10); the docs rewrite is PR 2 and out of scope here.

**Architecture:** Every module moves basename-intact except two collision renames (`plugin.py` → `runners/pytest.py`, `runner.py` → `orchestration/results.py`). All new package `__init__.py` files are docstring-only — no re-exports — so every internal import names its full module path (`evalspec.sandbox.sandbox`, `evalspec.orchestration.results`). `plugin.py` splits three ways: set/judge-config resolution → `config/sets.py`, run-manifest assembly → `reporting/manifest.py`, `(case × arm)` pairing → `orchestration/cases.py`; the hooks that remain in `runners/pytest.py` call into those homes. Tests mirror the packages; cross-cutting suites stay top-level.

**Tech Stack:** Python 3.11+, pytest (plugin via `pytest11` entrypoint), uv, Ruff + houserules (`make lint`), `git mv` for history-following moves.

**Destination-name decisions (justifications):**

- **`config/sets.py`** (not folded into `config/arms.py`): the extracted resolution functions depend on `pytest.UsageError`, `specs.discovery`, and `grading.judges` — folding them into `arms.py` would drag pytest and discovery into the pure schema/parse module. Judge-config resolution (`resolved_judge_config`, `_parse_judge_cli_table`) rides along in the same module because it shares the scratch-TOML seam (`_read_scratch_evalspec_table` — "one TOML read and one error-reporting path", per its docstring) with set layering; splitting them would force a private cross-module import. `_parse_env_pairs` also moves here: its only two callers are `resolved_run_set` and `_parse_judge_cli_table` (verified by grep — nothing else in the tree uses it).
- **`reporting/manifest.py`**: the session-report glue (`build_manifest`, `_config_hash`, `_judge_meta`, `_write_manifest`, `_aggregate_observed_arms`, `_git_commit`) assembles `meta.json` — a reporting artifact — and already leans on `report.planned_arms` / `report.redact_env`. It gets its own module rather than landing in `report.py` because `report.py` is the benchmark/matrix renderer and the spec names it as a preserved single-source invariant; keep the diff surgical.
- **`(case × arm)` pairing → `orchestration/cases.py`** as `eval_arm_params(config)`: the pairs parametrize the case bodies that live in the same file, and `cases.py` already imports discovery + set resolution. No new module needed.
- **De-underscore only what crosses a module boundary:** `_has_configured_set` → `has_configured_set`, `_judge_meta` → `judge_meta`, `_write_manifest` → `write_manifest`, `_aggregate_observed_arms` → `aggregate_observed_arms` (each now called from `runners/pytest.py`). `_config_hash`, `_git_commit`, `_layer_config_sets`, `_read_scratch_evalspec_table`, `_parse_judge_cli_table`, `_parse_env_pairs`, `_help` stay private — their callers move with them. House style forbids suppressions, so cross-module private-name imports are not an option.
- **`write_manifest` signature:** the `config: object` param (used only for `config.stash.get(_STARTED_AT, None)`) becomes `started_at: str | None` — the stash keys stay with the hooks, keeping pytest types out of `reporting/`. Everything else in the function is byte-identical.
- **Moved test file keeps its module aliases** (`from evalspec.runners import pytest as plugin`) so hook call sites (`plugin.pytest_sessionfinish(...)`) don't churn; only the extracted symbols' prefixes change (`plugin.resolved_run_set` → `sets.resolved_run_set`), which is the import-path-only diff the spec demands.

## Global Constraints

- **Zero behavior diff:** no logic edits anywhere; only module locations, import paths, the two named renames, the three named extractions, and comment/docstring path references that would otherwise lie.
- **No compatibility shims:** no re-exports of old internal paths; new package `__init__.py` files are docstring-only.
- **Frozen entrypoint name:** `[project.entry-points.pytest11]` key stays `evalspec`; only the value changes to `evalspec.runners.pytest`.
- **Frozen public surface:** `evalspec.agents`, `evalspec.testing`, root `evalspec/__init__.py` re-exports (`CodingAgent`, `make_agent`), the `evalspec` CLI, and `ExitCode` 0/1/2/5 in root `exit_codes.py` are untouched.
- **Zero test-logic edits:** only import paths, symbol homes, monkeypatch target strings, and file locations change in tests; every assertion stays byte-identical.
- **Style on every moved/created module:** module docstring, `from __future__ import annotations` as first import, absolute imports sorted stdlib → third-party → local, Google docstrings, no provenance comments, no suppressions.
- **Renames limited to collisions:** `plugin.py` → `runners/pytest.py`, `runner.py` → `orchestration/results.py`; test files rename only to match (`test_plugin.py` → `tests/runners/test_pytest.py`, `test_runner.py` → `tests/orchestration/test_results.py`).
- **Every commit green:** each task ends with `make test` and `make lint` passing before its commit; use `git mv` so history follows.
- **Verification commands:** `make test`; `make lint`; `uv run python -c "from evalspec.agents import CodingAgent, make_agent; from evalspec.testing import FakeSandbox"`; `make evals EVAL_ARGS="--collect-only -q"`; `make e2e` runs once later in the PR flow (needs credentials), not per task.
- **Docs are PR 2's problem:** `docs/*.md` stale module references stay stale (those files get deleted/rewritten in PR 2). README contains no `evalspec` imports (verified by grep), so it needs no PR 1 edit; `tests/test_readme_examples.py` only updates its own `evalspec.mdformat` import.
---

## Target layout (end state)

```
src/evalspec/
├── __init__.py  __main__.py  exit_codes.py  testing.py  py.typed   # root, unchanged content
├── agents/          # untouched
├── specs/           __init__.py  discovery.py  mdformat.py  schema.py  lint.py
├── config/          __init__.py  arms.py  sets.py          # sets.py extracted from plugin.py
├── runners/         __init__.py  pytest.py  run.py         # pytest.py né plugin.py, thin hooks
├── orchestration/   __init__.py  execution.py  cases.py  room.py  workspace.py
│                    environments.py  results.py            # results.py né runner.py
├── sandbox/         __init__.py  sandbox.py  backend.py  provenance.py
├── grading/         __init__.py  binder.py  checkers.py  judge.py  judges/  trajectory.py  trigger.py
└── reporting/       __init__.py  report.py  analyze.py  manifest.py   # manifest.py extracted from plugin.py

tests/
├── conftest.py  support.py  fixtures/  __init__.py         # top-level, unchanged
├── test_main.py  test_exit_codes.py  test_e2e_suite.py  test_readme_examples.py   # cross-cutting, stay top-level
├── agents/          # untouched
├── specs/           __init__.py  test_discovery.py  test_mdformat.py  test_schema.py  test_lint.py
├── config/          __init__.py  test_arms.py
├── runners/         __init__.py  test_pytest.py  test_run.py
├── orchestration/   __init__.py  test_execution.py  test_room.py  test_room_history.py
│                    test_workspace.py  test_results.py
├── sandbox/         __init__.py  test_sandbox.py  test_backend.py  test_provenance.py
├── grading/         __init__.py  test_binder.py  test_checkers.py  test_judge.py
│                    test_trajectory.py  test_trigger.py  judges/   # tests/judges moves whole
└── reporting/       __init__.py  test_report.py  test_analyze.py
```

Every new `__init__.py` (src and tests) is a module docstring plus `from __future__ import annotations`, nothing else. Src example: `"""Eval-spec parsing: discovery, Markdown format, schema validation, lint."""`.

---

## Task 1: Create `specs/` — discovery, mdformat, schema, lint

**Files:**
- Create: `src/evalspec/specs/__init__.py`, `tests/specs/__init__.py`
- Move: `git mv src/evalspec/{discovery,mdformat,schema,lint}.py src/evalspec/specs/`
- Move: `git mv tests/{test_discovery,test_mdformat,test_schema,test_lint}.py tests/specs/`
- Modify (importers): `src/evalspec/__main__.py`, `analyze.py`, `arms.py`, `backend.py`, `binder.py`, `cases.py`, `execution.py`, `judges/config.py`, `plugin.py`, `room.py`, `run.py`, `sandbox.py`; `tests/test_main.py`, `test_readme_examples.py`, `test_e2e_suite.py`, `test_arms.py`, `test_backend.py`, `test_execution.py`, `test_sandbox.py`, `test_plugin.py`, `test_analyze.py`, `tests/judges/test_config.py`, `tests/judges/test_judge_registry.py`, and the four moved test files' own imports.

**Interfaces:** module contents byte-identical; only `import` lines change. New paths: `evalspec.specs.discovery`, `evalspec.specs.mdformat`, `evalspec.specs.schema`, `evalspec.specs.lint`.

**Steps:**

- [ ] Create the two `__init__.py` files (docstring + future import). Src docstring: `"""Eval-spec parsing: discovery, Markdown format, schema validation, lint."""`; tests: `"""Tests for evalspec.specs."""`.
- [ ] `git mv src/evalspec/discovery.py src/evalspec/mdformat.py src/evalspec/schema.py src/evalspec/lint.py src/evalspec/specs/` and `git mv tests/test_discovery.py tests/test_mdformat.py tests/test_schema.py tests/test_lint.py tests/specs/`.
- [ ] Update intra-package imports in the moved modules:
  - `specs/discovery.py:18`: `from evalspec import mdformat, schema` → `from evalspec.specs import mdformat, schema`
  - `specs/mdformat.py:25`: `from evalspec import schema` → `from evalspec.specs import schema`
  - `specs/lint.py:15`: `from evalspec import discovery` → `from evalspec.specs import discovery`
- [ ] Update every src importer (complete list):
  - `__main__.py:9`: `from evalspec import analyze, lint, run, sandbox` → `from evalspec import analyze, run, sandbox` + new line `from evalspec.specs import lint`; `__main__.py:11`: `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `analyze.py:16`: `from evalspec import binder, discovery` → `from evalspec import binder` + `from evalspec.specs import discovery`
  - `arms.py:20`: `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `backend.py:28,30`: `from evalspec.discovery import EnvConfig` → `from evalspec.specs.discovery import EnvConfig`; `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `binder.py:16`: `from evalspec.schema import SchemaError, _validate_checker_obj` → `from evalspec.specs.schema import SchemaError, _validate_checker_obj`
  - `cases.py:24`: `from evalspec.discovery import resolve_repo_root` → `from evalspec.specs.discovery import resolve_repo_root`
  - `execution.py:26`: `from evalspec.discovery import EvalCase, resolve_environment_config` → `from evalspec.specs.discovery import EvalCase, resolve_environment_config`
  - `judges/config.py:19`: `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `plugin.py:28-33`: `from evalspec.discovery import (...)` → `from evalspec.specs.discovery import (...)` (same four names); `plugin.py:37`: `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `room.py:20`, `sandbox.py:26`: `from evalspec.schema import SchemaError` → `from evalspec.specs.schema import SchemaError`
  - `run.py:17`: `from evalspec import discovery` → `from evalspec.specs import discovery`
  - `sandbox.py:17`: `from evalspec.discovery import EnvConfig, pyproject_table, resolve_environment_config` → `from evalspec.specs.discovery import EnvConfig, pyproject_table, resolve_environment_config`
- [ ] Update every test importer (complete list): `tests/specs/test_discovery.py:7,8,13`; `tests/specs/test_mdformat.py:9` (`from evalspec import mdformat, schema` → `from evalspec.specs import mdformat, schema`); `tests/specs/test_schema.py:9,10` (`from evalspec import schema as v` → `from evalspec.specs import schema as v`; also fix its module docstring's `evalspec.schema` mention); `tests/specs/test_lint.py:9` (`from evalspec import lint` → `from evalspec.specs import lint`); `tests/test_main.py:16`; `tests/test_readme_examples.py:8` (`from evalspec.mdformat import parse_eval_md` → `from evalspec.specs.mdformat import parse_eval_md`); `tests/test_e2e_suite.py:20`; `tests/test_arms.py:16`; `tests/test_backend.py:11,12`; `tests/test_execution.py:13,18`; `tests/test_sandbox.py:16,518,543`; `tests/test_plugin.py:1686`; `tests/test_analyze.py:16` (`from evalspec import analyze, discovery, workspace` → `from evalspec import analyze, workspace` + `from evalspec.specs import discovery`) and `:21`; `tests/judges/test_config.py:6`; `tests/judges/test_judge_registry.py:17`.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/specs tests/test_readme_examples.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: move eval-spec parsing modules into specs/`

## Task 2: Create `sandbox/` — sandbox, backend, provenance

**Files:**
- Create: `src/evalspec/sandbox/__init__.py`, `tests/sandbox/__init__.py`
- Move: `git mv src/evalspec/{sandbox,backend,provenance}.py src/evalspec/sandbox/` (mkdir first — sandbox.py becomes `sandbox/sandbox.py`)
- Move: `git mv tests/{test_sandbox,test_backend,test_provenance}.py tests/sandbox/`
- Modify: `src/evalspec/__main__.py`, `agents/base.py`, `arms.py`, `cases.py`, `execution.py`, `plugin.py`; `tests/test_execution.py`, `test_plugin.py`, moved test files.

**Interfaces:** new paths `evalspec.sandbox.sandbox`, `evalspec.sandbox.backend`, `evalspec.sandbox.provenance`. Module-object imports keep the local name `sandbox` (`from evalspec.sandbox import sandbox`), so call sites (`sandbox.preflight`, `sandbox.ensure_snapshot`) are untouched. Note: `sandbox/sandbox.py:13` keeps `from evalspec import workspace` for now (workspace moves in Task 6); docstring-only `__init__.py` files keep the later `orchestration ↔ sandbox` module-level cross-imports acyclic.

**Steps:**

- [ ] `mkdir src/evalspec/sandbox tests/sandbox`; create both `__init__.py` files (src docstring: `"""Sandbox lifecycle: microVM sessions, backend seam, provenance records."""`).
- [ ] `git mv src/evalspec/sandbox.py src/evalspec/backend.py src/evalspec/provenance.py src/evalspec/sandbox/` and `git mv tests/test_sandbox.py tests/test_backend.py tests/test_provenance.py tests/sandbox/`.
- [ ] Update intra-package imports: `sandbox/sandbox.py:16` → `from evalspec.sandbox.backend import BASE_IMAGE, DEFAULT_SANDBOX, SandboxBackend, resolve_sandbox`; `sandbox/backend.py:29` → `from evalspec.sandbox.provenance import ImageIdentity`.
- [ ] Update src importers (complete list):
  - `__main__.py:9`: `from evalspec import analyze, run, sandbox` → `from evalspec import analyze, run` + `from evalspec.sandbox import sandbox`
  - `agents/base.py:27` (TYPE_CHECKING): `from evalspec.backend import SandboxBackend` → `from evalspec.sandbox.backend import SandboxBackend`
  - `arms.py:19`: `from evalspec.backend import DEFAULT_SANDBOX, resolve_sandbox` → `from evalspec.sandbox.backend import DEFAULT_SANDBOX, resolve_sandbox`
  - `cases.py:22`: `from evalspec import binder, runner, sandbox` → `from evalspec import binder, runner` + `from evalspec.sandbox import sandbox`; `cases.py:23` → `from evalspec.sandbox.backend import resolve_sandbox`
  - `execution.py:25` → `from evalspec.sandbox.backend import DEFAULT_SANDBOX, resolve_sandbox`; `:29` → `from evalspec.sandbox.provenance import RuntimeProvenance, SandboxProvenance`; `:32-37` → `from evalspec.sandbox.sandbox import (DEFAULT_PROJECT_MARKER, SandboxSession, arm_session, ensure_snapshot)`
  - `plugin.py:36` → `from evalspec.sandbox.provenance import RuntimeProvenance, aggregate_observed`
- [ ] Update test importers (complete list): `tests/sandbox/test_sandbox.py:10,11` (`from evalspec import backend as backend_mod` → `from evalspec.sandbox import backend as backend_mod`; `from evalspec import sandbox` → `from evalspec.sandbox import sandbox`), `:454,474` provenance; `tests/sandbox/test_backend.py:9` (`from evalspec import backend` → `from evalspec.sandbox import backend`); `tests/sandbox/test_provenance.py:7`; `tests/test_execution.py:12,15,17` (`backend`/`provenance`/`sandbox` paths) and monkeypatch string `:2147`: `"evalspec.sandbox.resolve_environment_config"` → `"evalspec.sandbox.sandbox.resolve_environment_config"`; `tests/test_plugin.py:16` provenance, `:461,1241,1276,1304` (`from evalspec import sandbox` → `from evalspec.sandbox import sandbox`; line 461 keeps `from evalspec import binder, cases` for now).
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/sandbox tests/test_execution.py tests/test_plugin.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: move sandbox modules into sandbox/`

## Task 3: Create `grading/` — binder, checkers, judge, judges/, trajectory, trigger

**Files:**
- Create: `src/evalspec/grading/__init__.py`, `tests/grading/__init__.py`
- Move: `git mv src/evalspec/{binder,checkers,judge,trajectory,trigger}.py src/evalspec/grading/`; `git mv src/evalspec/judges src/evalspec/grading/judges`
- Move: `git mv tests/{test_binder,test_checkers,test_judge,test_trajectory,test_trigger}.py tests/grading/`; `git mv tests/judges tests/grading/judges`
- Modify: `src/evalspec/agents/{base,claude,codex,opencode}.py`, `analyze.py`, `cases.py`, `execution.py`, `plugin.py`, `runner.py`, `sandbox/sandbox.py`; `tests/test_execution.py`, `test_plugin.py`, `test_e2e_suite.py`, `test_analyze.py`, `tests/sandbox/test_sandbox.py`, `tests/agents/test_opencode.py`, moved test files; `evals/binder/{conftest.py,test_corpus.py,test_corpus_integrity.py}`.

**Interfaces:** new paths `evalspec.grading.{binder,checkers,judge,trajectory,trigger}` and package `evalspec.grading.judges` (its internal `config.py`/`registry.py` layout unchanged).

**Steps:**

- [ ] Create both `__init__.py` files (src docstring: `"""Grading: binder classification, deterministic checkers, LLM judge, trajectory and trigger detection."""`). `tests/grading/judges/__init__.py` moves as-is with the directory.
- [ ] Run the `git mv` commands above.
- [ ] Update intra-package imports: `grading/binder.py:15` → `from evalspec.grading.judge import _balanced_objects`; `grading/judge.py:18` → `from evalspec.grading.judges import JudgeConfig, run_judge`; `grading/judges/__init__.py:14,15` → `from evalspec.grading.judges.config import ...` / `from evalspec.grading.judges.registry import ...`; `grading/judges/config.py:18` → `from evalspec.grading.judges.registry import known_judge_harnesses`; `grading/judges/registry.py:24` (TYPE_CHECKING) → `from evalspec.grading.judges.config import JudgeConfig`. (`judges/config.py:15-17` agents imports and `registry.py:20,21` agents/arms imports stay as-is; arms moves in Task 4.)
- [ ] Update src importers (complete list):
  - `agents/base.py:29` (TYPE_CHECKING) → `from evalspec.grading.judges.config import JudgeConfig`
  - `agents/claude.py:19` → `from evalspec.grading.trigger import detect_skill_fired, dispatches_skill, streamed_activity`; `:22` (TYPE_CHECKING) → `from evalspec.grading.judges.config import JudgeConfig`
  - `agents/codex.py:15` → `from evalspec.grading.trajectory import iter_events`; `:18` → grading.judges.config
  - `agents/opencode.py:30` → grading.trajectory; `:33` → grading.judges.config
  - `analyze.py`: `from evalspec import binder` → `from evalspec.grading import binder`
  - `cases.py:22`: `from evalspec import binder, runner` → `from evalspec import runner` + `from evalspec.grading import binder`; `:26,27` → `from evalspec.grading.judges import JudgeConfig` / `from evalspec.grading.judges.registry import preflight_judge_binary`
  - `execution.py:21`: `from evalspec import binder, checkers, workspace` → `from evalspec import workspace` + `from evalspec.grading import binder, checkers`; `:27` → `from evalspec.grading.judge import grade_run`; `:28` → `from evalspec.grading.judges import JudgeConfig`; `:38` → `from evalspec.grading.trajectory import TURN_DELIM, render_process_facts, skills_dispatched`
  - `plugin.py:27` → `from evalspec.grading.binder import binder_identity`; `:34,35` → `from evalspec.grading.judges import JudgeConfig, resolve_judge_config` / `from evalspec.grading.judges.registry import probe_judge_version`
  - `runner.py:16,17` → `from evalspec.grading.trajectory import extract_trajectory` / `from evalspec.grading.trigger import detect_skill_fired`
  - `sandbox/sandbox.py:27` → `from evalspec.grading.trigger import RoutingError`
  - `grading/trajectory.py:223` (lazy) stays `from evalspec.agents.opencode import _opencode_trajectory` — agents didn't move.
- [ ] Update test importers (complete list): moved files' own imports (`tests/grading/test_binder.py:18,19,163`; `test_checkers.py:9,10`; `test_judge.py:9,10` plus the **9 monkeypatch strings** at lines 124,148,162,179,194,210,222,238,276: `"evalspec.judge.run_judge"` → `"evalspec.grading.judge.run_judge"`; `test_trajectory.py:5,194,209,217,247,294`; `test_trigger.py:5` plus its docstring's `evalspec.trigger` mention); `tests/grading/judges/*` (test_config.py:5, test_judge_registry.py:9,10, test_claude_code.py:10, test_codex.py:10, test_opencode.py:10 — all `evalspec.judges.*` → `evalspec.grading.judges.*`); `tests/test_execution.py:1079,1119,1360,1640,2065`; `tests/test_plugin.py:461` (`from evalspec import binder, cases` → `from evalspec import cases` + `from evalspec.grading import binder`), `:1675,1685`; `tests/test_e2e_suite.py:21` → `from evalspec.grading.judges.config import resolve_judge_config`; `tests/test_analyze.py:18` → `from evalspec.grading.binder import _bind_bare_exists`; `tests/sandbox/test_sandbox.py:672` → grading.trigger; `tests/agents/test_opencode.py:972,1001,1023` → grading.trajectory; `tests/test_judge.py` moved already (covered above).
- [ ] Update `evals/` importers: `evals/binder/conftest.py:17` → `from evalspec.grading import binder`; `evals/binder/test_corpus.py:20` → `from evalspec.grading.binder import _bind_bare_exists, bind`; `evals/binder/test_corpus_integrity.py:15,16` → `from evalspec.grading import binder` / `from evalspec.grading.checkers import derive_text`.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/grading tests/agents tests/test_execution.py tests/test_plugin.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean; `make evals EVAL_ARGS="--collect-only -q"` → binder corpus still collects.
- [ ] Commit: `refactor: move grading modules into grading/`

## Task 4: Create `config/` — arms

**Files:**
- Create: `src/evalspec/config/__init__.py`, `tests/config/__init__.py`
- Move: `git mv src/evalspec/arms.py src/evalspec/config/`; `git mv tests/test_arms.py tests/config/`
- Modify: `src/evalspec/execution.py`, `grading/judges/registry.py`, `plugin.py`, `report.py`, `sandbox/sandbox.py`; `tests/test_analyze.py`, `test_e2e_suite.py`, `test_execution.py`, `test_plugin.py`, moved test file.

**Interfaces:** new path `evalspec.config.arms`; contents byte-identical.

**Steps:**

- [ ] Create both `__init__.py` files (src docstring: `"""Run configuration: eval sets, arms, and their resolution."""`); `git mv` as above.
- [ ] Update src importers (complete list):
  - `execution.py:24` → `from evalspec.config.arms import Arm, expand_env`
  - `grading/judges/registry.py:21` → `from evalspec.config.arms import expand_env`
  - `plugin.py:25,26` → `from evalspec.config.arms import Set as EvalSet` / `from evalspec.config.arms import parse_sets, resolve_set`
  - `report.py:29` (TYPE_CHECKING) → `from evalspec.config.arms import Set as EvalSet`
  - `sandbox/sandbox.py:15` → `from evalspec.config.arms import parse_sets, resolve_set`
- [ ] Update test importers: `tests/config/test_arms.py:10` (`from evalspec.arms import (...)` → `from evalspec.config.arms import (...)`); `tests/test_analyze.py:17`; `tests/test_e2e_suite.py:19`; `tests/test_execution.py:11`; `tests/test_plugin.py:423` (`from evalspec.arms import Arm, Set` → `from evalspec.config.arms import Arm, Set`).
- [ ] Also update the comment in `tests/fixtures/judge/unset-judge-env.toml:5` (`evalspec.arms.expand_env` → `evalspec.config.arms.expand_env`) — comment accuracy only.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/config tests/test_e2e_suite.py tests/test_plugin.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: move arm configuration into config/`

## Task 5: Create `reporting/` — report, analyze

**Files:**
- Create: `src/evalspec/reporting/__init__.py`, `tests/reporting/__init__.py`
- Move: `git mv src/evalspec/{report,analyze}.py src/evalspec/reporting/`; `git mv tests/{test_report,test_analyze}.py tests/reporting/`
- Modify: `src/evalspec/__main__.py`, `plugin.py`; moved test files.

**Interfaces:** new paths `evalspec.reporting.report`, `evalspec.reporting.analyze`. `report.planned_arms()` stays the sole planned-arm builder — untouched.

**Steps:**

- [ ] Create both `__init__.py` files (src docstring: `"""Reporting: benchmark matrices, run manifests, artifact analysis."""`); `git mv` as above.
- [ ] Update src importers: `__main__.py:9`: `from evalspec import analyze, run` → `from evalspec import run` + `from evalspec.reporting import analyze`; `plugin.py:23`: `from evalspec import report, workspace` → `from evalspec import workspace` + `from evalspec.reporting import report`.
- [ ] Update test importers: `tests/reporting/test_report.py:5` (`from evalspec import report` → `from evalspec.reporting import report`); `tests/reporting/test_analyze.py:16` (`from evalspec import analyze, workspace` → `from evalspec import workspace` + `from evalspec.reporting import analyze`); `tests/test_plugin.py:14` (`from evalspec import plugin, report, workspace` → `from evalspec import plugin, workspace` + `from evalspec.reporting import report`).
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/reporting tests/test_plugin.py tests/test_main.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: move reporting modules into reporting/`

## Task 6: Create `orchestration/` — execution, cases, room, workspace, environments, results (né runner.py)

**Files:**
- Create: `src/evalspec/orchestration/__init__.py`, `tests/orchestration/__init__.py`
- Move: `git mv src/evalspec/{execution,cases,room,workspace,environments}.py src/evalspec/orchestration/`; `git mv src/evalspec/runner.py src/evalspec/orchestration/results.py`
- Move: `git mv tests/{test_execution,test_room,test_room_history,test_workspace}.py tests/orchestration/`; `git mv tests/test_runner.py tests/orchestration/test_results.py`
- Modify: `src/evalspec/agents/{base,claude,codex,opencode}.py`, `plugin.py`, `sandbox/sandbox.py`, `pyproject.toml` (marker comment only); `tests/test_plugin.py`, `tests/test_analyze.py`? (moved to `tests/reporting/test_analyze.py` — update there), `tests/sandbox/test_sandbox.py`, `tests/agents/test_claude.py`, moved test files.

**Interfaces:** new paths `evalspec.orchestration.{execution,cases,room,workspace,environments,results}`. The `runner.py` → `results.py` rename clears the `runner`/`runners` adjacency; `runner` stays a config-field word. Local module name changes with it: `cases.py`'s `runner.utc_today()` becomes `results.utc_today()` — the only call-site rename in src.

**Steps:**

- [ ] Create both `__init__.py` files (src docstring: `"""Orchestration: run one (eval × arm) — execution, cases, room, workspace, environments, results."""`); run the `git mv` commands above.
- [ ] Update intra-package imports in moved modules:
  - `orchestration/execution.py:21`: `from evalspec import workspace` → `from evalspec.orchestration import workspace`; `:30` → `from evalspec.orchestration.room import gather_facts, merge_facts, render_history`; `:31` → `from evalspec.orchestration.results import substitute_assertions, substitute_prompt`
  - `orchestration/cases.py:22`: `from evalspec import runner` → `from evalspec.orchestration import results`, and call site `cases.py:77`: `return runner.utc_today()` → `return results.utc_today()`; `:25` → `from evalspec.orchestration.execution import run_eval_arm`; `:29` → `from evalspec.orchestration.room import seed_room`
  - `orchestration/room.py:19` → `from evalspec.orchestration.results import substitute_prompt`
  - `orchestration/results.py` docstring/name references to "runner" stay (they describe run results, not the module path).
- [ ] Update src importers (complete list):
  - `agents/base.py:24` → `from evalspec.orchestration.results import RunResult`; `:28` (TYPE_CHECKING) → `from evalspec.orchestration.environments import ExecutionEnv`
  - `agents/claude.py:17` → `from evalspec.orchestration.environments import ExecutionEnv, GuestSandbox, Host`; `:18` → `from evalspec.orchestration.results import RunResult, parse_stream_run`
  - `agents/codex.py:13,14` and `agents/opencode.py:28,29` → same two paths (`results` import is `RunResult` only)
  - `plugin.py:23`: `from evalspec import workspace` → `from evalspec.orchestration import workspace`; `plugin.py:39`: `_CASES = Path(__file__).parent / "cases.py"` → `_CASES = Path(__file__).parent / "orchestration" / "cases.py"` (plugin.py is still at the package root until Task 7)
  - `sandbox/sandbox.py:13`: `from evalspec import workspace` → `from evalspec.orchestration import workspace`; `:18-25` → `from evalspec.orchestration.room import (changed_paths, parse_artifact_stream, parse_sha_stream, read_files_script, sha_snapshot_script, to_display_paths)`
- [ ] Update test importers (complete list):
  - `tests/orchestration/test_execution.py:10,14` (`from evalspec import workspace` → `from evalspec.orchestration import workspace`; execution path), `:16` → `from evalspec.orchestration.results import RunResult`, `:1213` unchanged (agents), and **monkeypatch strings**: every `"evalspec.execution.<name>"` → `"evalspec.orchestration.execution.<name>"` (lines 29,30,664,821,1224,1930,1931,1970,1971,2001,2002,2037,2038,2044,2072,2073,2144,2145,2146,2148) and `:1230` `"evalspec.environments.subprocess.run"` → `"evalspec.orchestration.environments.subprocess.run"`
  - `tests/orchestration/test_results.py:9`: `from evalspec.runner import (...)` → `from evalspec.orchestration.results import (...)`
  - `tests/orchestration/test_room.py:3`, `test_room_history.py:5` → `from evalspec.orchestration.room import ...`; `test_workspace.py:3` → `from evalspec.orchestration import workspace`
  - `tests/reporting/test_analyze.py`: `from evalspec import workspace` → `from evalspec.orchestration import workspace`; `:19` → `from evalspec.orchestration.execution import run_eval_arm`; `:20` → `from evalspec.orchestration.results import RunResult`; monkeypatch strings `:214,215` → `"evalspec.orchestration.execution.<name>"`
  - `tests/test_plugin.py:14`: `from evalspec import plugin, workspace` → `from evalspec import plugin` + `from evalspec.orchestration import workspace`; `:422,442,461`: `from evalspec import cases` → `from evalspec.orchestration import cases`
  - `tests/sandbox/test_sandbox.py:17` → `from evalspec.orchestration.results import RunResult`
  - `tests/agents/test_claude.py:11` → `from evalspec.orchestration.results import parse_run_json`
- [ ] Update `pyproject.toml:95` marker-comment text `evalspec.cases` → `evalspec.orchestration.cases` (comment accuracy only).
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/orchestration tests/agents tests/reporting tests/test_plugin.py -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: move orchestration modules into orchestration/ (runner.py -> results.py)`

## Task 7: Create `runners/` — pytest.py (né plugin.py) and run.py; update the entrypoint value

**Files:**
- Create: `src/evalspec/runners/__init__.py`, `tests/runners/__init__.py`
- Move: `git mv src/evalspec/plugin.py src/evalspec/runners/pytest.py`; `git mv src/evalspec/run.py src/evalspec/runners/`
- Move: `git mv tests/test_plugin.py tests/runners/test_pytest.py`; `git mv tests/test_run.py tests/runners/`
- Modify: `pyproject.toml` (entrypoint value), `src/evalspec/__main__.py`, `orchestration/cases.py`; `tests/fixtures/judge/unsupported-judge-harness.toml` (comment); moved test files.

**Interfaces:** new module `evalspec.runners.pytest` — content still the full plugin (decomposition is Tasks 8–10). Entrypoint NAME stays `evalspec`; only the value changes. Inside `runners/pytest.py`, `import pytest` still resolves to the real pytest (absolute imports).

**Steps:**

- [ ] Create both `__init__.py` files (src docstring: `"""Test-harness adapters: the pytest plugin and the run CLI."""`); run the `git mv` commands.
- [ ] `pyproject.toml:57`: `evalspec = "evalspec.plugin"` → `evalspec = "evalspec.runners.pytest"` under `[project.entry-points.pytest11]` (key unchanged).
- [ ] `runners/pytest.py:39`: `_CASES = Path(__file__).parent / "orchestration" / "cases.py"` → `_CASES = Path(__file__).parent.parent / "orchestration" / "cases.py"` (temporary path arithmetic; Task 10 replaces it with `Path(cases.__file__)`).
- [ ] `orchestration/cases.py:28`: `from evalspec.plugin import resolved_judge_config, resolved_run_set, session_run_set` → `from evalspec.runners.pytest import resolved_judge_config, resolved_run_set, session_run_set` (temporary; Task 8 repoints to `config.sets`).
- [ ] `__main__.py:9`: `from evalspec import run` → `from evalspec.runners import run`.
- [ ] `runners/run.py` comment/docstring path accuracy: line 48 docstring `-p evalspec.plugin` → `-p evalspec.runners.pytest`; line 84 comment likewise. (No code change.)
- [ ] Update moved test files:
  - `tests/runners/test_pytest.py:14`: `from evalspec import plugin` → `from evalspec.runners import pytest as plugin` (alias keeps all ~25 `plugin.` call sites byte-identical)
  - Replace all **10** literal `"evalspec.plugin"` strings (lines 106,118,153,173,480,514,1259,1289,1312,1652 — the `-p` launcher args in pytester runs) with `"evalspec.runners.pytest"`
  - `tests/runners/test_run.py:14,15`: `from evalspec import run` → `from evalspec.runners import run`; `from evalspec.run import translate_run_flags` → `from evalspec.runners.run import translate_run_flags`
- [ ] `tests/fixtures/judge/unsupported-judge-harness.toml:3` comment: `pytest -p evalspec.plugin` → `pytest -p evalspec.runners.pytest`.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/runners -p pytester -q` → all pass (pytester runs exercise the new `-p evalspec.runners.pytest` launcher).
- [ ] Verify: `make test` → green; `make lint` → clean; `ls src/evalspec/*.py` → only `__init__.py __main__.py exit_codes.py testing.py`.
- [ ] Verify entrypoint: `make evals EVAL_ARGS="--collect-only -q"` → collects via the auto-loaded entrypoint (no PYTEST_DISABLE_PLUGIN_AUTOLOAD in that target), exit 0.
- [ ] Commit: `refactor: move pytest plugin and run CLI into runners/ (plugin.py -> pytest.py)`

## Task 8: Extract set/judge-config resolution into `config/sets.py`

**Files:**
- Create: `src/evalspec/config/sets.py`
- Modify: `src/evalspec/runners/pytest.py`, `src/evalspec/orchestration/cases.py`, `tests/runners/test_pytest.py`

**Interfaces:** `config/sets.py` exports (bodies and docstrings byte-identical to today's `plugin.py`, except `_has_configured_set` → `has_configured_set`):

```python
def resolved_run_set(config: object) -> EvalSet: ...
def has_configured_set(config: object) -> bool: ...          # né _has_configured_set
def run_set_when_needed(config: object, *, needs_set: bool) -> EvalSet | None: ...
def session_run_set(config: object) -> EvalSet | None: ...
def resolved_judge_config(config: object) -> JudgeConfig: ...
# module-private, moved intact:
# _read_scratch_evalspec_table, _layer_config_sets, _parse_judge_cli_table, _parse_env_pairs
```

The set-resolution seam (`has_configured_set` / `run_set_when_needed` / `session_run_set`) survives intact — the planned roster stays a config fact, not an output-dir fact.

**Steps:**

- [ ] Create `config/sets.py` with docstring `"""Resolve the run's eval set and judge config from pyproject, scratch config, and CLI overrides."""` and move (cut, byte-identical bodies) `_parse_env_pairs`, `_read_scratch_evalspec_table`, `_layer_config_sets`, `resolved_run_set`, `_has_configured_set` (renamed `has_configured_set`), `run_set_when_needed`, `session_run_set`, `_parse_judge_cli_table`, `resolved_judge_config` out of `runners/pytest.py`. Import block for `sets.py`:

  ```python
  from __future__ import annotations

  import sys
  from pathlib import Path

  import pytest

  from evalspec.config.arms import Set as EvalSet
  from evalspec.config.arms import parse_sets, resolve_set
  from evalspec.grading.judges import JudgeConfig, resolve_judge_config
  from evalspec.specs.discovery import (
      discover_eval_cases,
      pyproject_table,
      resolve_eval_paths,
      resolve_repo_root,
  )
  from evalspec.specs.schema import SchemaError
  ```

  No aliasing needed: the imported `resolve_judge_config` and the defined `resolved_judge_config` are distinct names (verified against `plugin.py:34,361`), exactly as they coexist in `plugin.py` today — bodies stay byte-identical.
- [ ] Thin `runners/pytest.py`: delete the moved functions; drop now-unused imports (`sys`, `parse_sets`, `resolve_set`, `SchemaError`, `resolve_judge_config`); **keep** `discover_eval_cases` and `resolve_eval_paths` — `pytest_generate_tests` still uses them until Task 10. Add `from evalspec.config.sets import has_configured_set, resolved_judge_config, run_set_when_needed`; update `pytest_sessionfinish`'s `_has_configured_set(config)` call to `has_configured_set(config)`. (`pytest_generate_tests` still calls `run_set_when_needed`/`resolved_judge_config` until Task 10.)
- [ ] `orchestration/cases.py:28`: `from evalspec.runners.pytest import resolved_judge_config, resolved_run_set, session_run_set` → `from evalspec.config.sets import resolved_judge_config, resolved_run_set, session_run_set` — this breaks the `cases → plugin` import edge for good.
- [ ] `tests/runners/test_pytest.py`: add `from evalspec.config import sets`; repoint the 12 extracted-symbol call sites (assertions untouched): `plugin.resolved_run_set` → `sets.resolved_run_set` (lines 288,309,323,331,342,352,362,374,386), `plugin.session_run_set` → `sets.session_run_set` (396,410), `plugin.run_set_when_needed` → `sets.run_set_when_needed` (417).
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/runners tests/config -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: extract set and judge-config resolution into config/sets.py`

## Task 9: Extract run-manifest assembly into `reporting/manifest.py`

**Files:**
- Create: `src/evalspec/reporting/manifest.py`
- Modify: `src/evalspec/runners/pytest.py`, `tests/runners/test_pytest.py`

**Interfaces:** `reporting/manifest.py` exports (bodies byte-identical except the two named signature/name changes):

```python
def build_manifest(*, run_id: str, started_at: str | None, commit: str | None,
                   iteration: str, cfg: dict, observed_arms: dict) -> dict: ...   # unchanged
def judge_meta(judge_config: JudgeConfig) -> dict: ...                            # né _judge_meta
def write_manifest(iteration_root: Path, iteration: str, repo_root: Path,
                   run_set: EvalSet | None, judge_meta: dict, observed_arms: dict,
                   *, started_at: str | None) -> None: ...
                   # né _write_manifest; `config` param → explicit started_at (stash stays with hooks)
def aggregate_observed_arms(skills_root: Path, run_set: EvalSet | None) -> dict: ...  # né _aggregate_observed_arms
# module-private, moved intact: _config_hash, _git_commit
```

**Steps:**

- [ ] Create `reporting/manifest.py` with docstring `"""Assemble the run manifest (meta.json): identity, planned config, observed arms."""`, moving `build_manifest`, `_config_hash`, `_judge_meta` (→ `judge_meta`), `_write_manifest` (→ `write_manifest`, `config` param replaced by keyword-only `started_at: str | None`, and its `config.stash.get(_STARTED_AT, None)` line becomes the `started_at` argument), `_aggregate_observed_arms` (→ `aggregate_observed_arms`), `_git_commit`. Import block:

  ```python
  from __future__ import annotations

  import hashlib
  import json
  import subprocess
  import uuid
  from pathlib import Path

  import evalspec
  from evalspec.config.arms import Set as EvalSet
  from evalspec.grading.binder import binder_identity
  from evalspec.grading.judges import JudgeConfig
  from evalspec.grading.judges.registry import probe_judge_version
  from evalspec.reporting import report
  from evalspec.sandbox.provenance import RuntimeProvenance, aggregate_observed
  ```

- [ ] Thin `runners/pytest.py`: delete the moved functions; drop now-unused imports (`hashlib`, `subprocess`, `uuid`, `import evalspec`, `RuntimeProvenance`, `aggregate_observed`, `probe_judge_version`, `EvalSet`); add `from evalspec.reporting import manifest`; update `pytest_sessionfinish` call sites:

  ```python
  judge_meta = manifest.judge_meta(judge_config)
  ...
  observed_arms = manifest.aggregate_observed_arms(skills_root, run_set)
  manifest.write_manifest(
      skills_root.parent,
      iteration,
      repo_root,
      run_set,
      judge_meta,
      observed_arms,
      started_at=config.stash.get(_STARTED_AT, None),
  )
  ```

  (`binder_identity` stays imported in `runners/pytest.py` too — `pytest_sessionfinish` still passes it to `report.write_benchmark`.)
- [ ] `tests/runners/test_pytest.py`: add `manifest` to the reporting import (`from evalspec.reporting import manifest, report`); repoint the 8 extracted-symbol call sites: `plugin.build_manifest` → `manifest.build_manifest` (lines 703,742,750,777,781,811,819) and `plugin._aggregate_observed_arms` → `manifest.aggregate_observed_arms` (line 1118). Assertions untouched.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/runners tests/reporting -p pytester -q` → all pass.
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: extract run-manifest assembly into reporting/manifest.py`

## Task 10: Extract `(case × arm)` pairing into `orchestration/cases.py`; final hook thinning

**Files:**
- Modify: `src/evalspec/orchestration/cases.py`, `src/evalspec/runners/pytest.py`

**Interfaces:** new function in `orchestration/cases.py` (logic byte-identical to today's `pytest_generate_tests` body, minus the `metafunc` shell):

```python
def eval_arm_params(config: object) -> tuple[list[tuple[EvalCase, Arm]], list[str]]:
    """The (case × arm) pairs and ids that parametrize the eval_arm fixture."""
```

It performs, verbatim from the current hook: `resolve_repo_root(config)`, `discover_eval_cases(repo_root, resolve_eval_paths(config))`, `run_set_when_needed(config, needs_set=bool(cases))`, the structural `resolved_judge_config(config)` preflight when cases exist, then the pair/id loops. New imports needed in `cases.py`: `resolve_eval_paths` and `discover_eval_cases` join the existing `evalspec.specs.discovery` import; `run_set_when_needed` joins the existing `evalspec.config.sets` import.

**Steps:**

- [ ] Add `eval_arm_params` to `orchestration/cases.py` (with the hook body's existing comments preserved) and extend its imports as above.
- [ ] Thin `runners/pytest.py`:

  ```python
  def pytest_generate_tests(metafunc: object) -> None:
      """Parametrize pytest items from discovered evalspec cases."""
      if "eval_arm" in metafunc.fixturenames:
          pairs, ids = cases.eval_arm_params(metafunc.config)
          metafunc.parametrize("eval_arm", pairs, ids=ids)
  ```

  Add `from evalspec.orchestration import cases` (safe now — Task 8 broke the `cases → plugin` edge) and replace the path arithmetic with `_CASES = Path(cases.__file__)`. Drop the hook's now-unused imports (`discover_eval_cases`, `resolve_eval_paths`, `run_set_when_needed` if `pytest_sessionfinish` alone still needs it — it does, keep it). Final `runners/pytest.py` import block:

  ```python
  from __future__ import annotations

  import datetime
  import json
  import os
  from pathlib import Path

  import pytest
  from dotenv import load_dotenv

  from evalspec.agents import resolve_agent_name
  from evalspec.config.sets import (
      has_configured_set,
      resolved_judge_config,
      run_set_when_needed,
  )
  from evalspec.grading.binder import binder_identity
  from evalspec.grading.judges import JudgeConfig
  from evalspec.orchestration import cases, workspace
  from evalspec.reporting import manifest, report
  from evalspec.specs.discovery import pyproject_table, resolve_repo_root
  ```

  What remains in the file, in full: `_CASES`, the two stash keys, `_help`, `pytest_addoption`, the `sample_index` fixture, `pytest_configure`, `pytest_configure_node`, `pytest_sessionfinish` (thinned), `pytest_terminal_summary`, `pytest_generate_tests` (thinned). Nothing else.
- [ ] Verify: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/runners tests/orchestration -p pytester -q` → all pass (the pytester collection tests exercise `eval_arm_params` end-to-end through the real plugin).
- [ ] Verify: `make test` → green; `make lint` → clean.
- [ ] Commit: `refactor: extract (case x arm) pairing into orchestration/cases.py`

## Task 11: Full verification (no code changes, no commit)

**Files:** none modified.

**Steps:**

- [ ] `make test` → full unit suite green.
- [ ] `make lint` → Ruff + houserules clean.
- [ ] `uv run python -c "from evalspec.agents import CodingAgent, make_agent; from evalspec.testing import FakeSandbox"` → exits 0 (frozen public surface intact).
- [ ] `make evals EVAL_ARGS="--collect-only -q"` → binder corpus collects through the entrypoint-loaded plugin, exit 0.
- [ ] Entrypoint check: `grep -A1 'entry-points.pytest11' pyproject.toml` → shows `evalspec = "evalspec.runners.pytest"` (name `evalspec` unchanged).
- [ ] Structure check: `ls src/evalspec/*.py` → exactly `__init__.py __main__.py exit_codes.py testing.py`; `git grep -nE "from evalspec import (plugin|runner|arms|discovery|mdformat|schema|lint|execution|room|workspace|environments|sandbox|backend|provenance|binder|checkers|judge|trajectory|trigger|report|analyze)\b|from evalspec\.(plugin|runner|arms|discovery|mdformat|schema|lint|execution|room|environments|backend|provenance|binder|checkers|trajectory|trigger|report|analyze)\b" -- src tests evals` → no hits (no stale flat-path imports; `evalspec.sandbox`/`evalspec.judge*`/`evalspec.run`/`evalspec.workspace`/`evalspec.cases` need eyeball checks since package names overlap — `git grep -n "evalspec\.cases\|evalspec\.workspace\|evalspec\.judges\b"` → no hits).
- [ ] Diff audit for the zero-behavior gate: `git diff dev...HEAD -- tests/` shows only import lines, monkeypatch target strings, moved paths, and the extracted-symbol prefix repointing — no assertion changes.
- [ ] Note for the PR (not run per task): `make e2e` runs once later in the flow on real microVMs to confirm artifacts still match the frozen schemas (`meta.json` v2, `benchmark.json` v3, `index.jsonl`, `provenance.json`).

---

## Self-review checklist (plan-level)

- Every spec requirement maps to a task: seven packages (Tasks 1–7), `plugin.py` decomposition (8–10), test mirror (each move task), frozen entrypoint name (7, 11), no shims (docstring-only `__init__.py`, Task 11 grep), collision-only renames (6, 7), README/`test_readme_examples` handled (Task 1 — import only; README itself has no evalspec imports), `evals/` and fixture-TOML references (3, 4, 7), pyproject comment (6), style rules on every new file, green tree at every commit.
- Out of scope, deliberately: docs/*.md content (PR 2), splitting `tests/runners/test_pytest.py` into per-package test files (would move test logic between files; the extracted functions' tests stay with the plugin suite for a minimal, auditable diff).
