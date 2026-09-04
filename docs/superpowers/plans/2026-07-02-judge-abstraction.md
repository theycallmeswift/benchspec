# Judge Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a run-level, harness-independent judge abstraction (`JudgeConfig` + host-side judge runners for `claude-code`/`codex`/`opencode`) so grading no longer routes through the task agent's `.judge()` method, is fully configurable (harness/model/effort/timeout/harness_args/env) via `pyproject.toml`, scratch config, and CLI flags, and is recorded structurally in `meta.json`.

**Architecture:** A new `src/evalspec/judges/` package owns judge configuration (`JudgeConfig`, precedence resolution, local preflight, model/harness family detection) and judge transport (one runner per harness, each returning the exact `{"result": "<judge-json-string>"}` envelope `judge.py` already parses). `judge.py` keeps prompt-building, JSON parsing, and retry semantics but calls `judges.run_judge(prompt, config=judge_config)` instead of `agent.judge(...)`. `execution.py` threads a resolved `JudgeConfig` through grading instead of a bare `judge_model` string. `plugin.py` resolves the `JudgeConfig` at collection time (CLI > scratch `--evalspec-config` > pyproject `[tool.evalspec.judge]` > built-in defaults), preflights structurally before any task arm runs, and records the resolved judge + same-family warnings in `meta.json["judge"]`. `binder.py`'s existing `run_host_judge` transport is left completely untouched.

**Tech Stack:** Python 3.10+, pytest 8 (plugin hooks: `pytest_addoption`, `pytest_generate_tests`, `pytest_sessionfinish`), `tomllib`/`tomli`, `subprocess`, dataclasses. No new third-party dependencies.

## Global Constraints

- Every judge runner returns stdout-shaped text that is exactly one JSON object `{"result": "<judge-json-string>", ...}` — `judge.py` keeps doing `json.loads(raw)` then parsing `outer.get("result", "")`. Codex/OpenCode runners extract their harness-specific final text and wrap it: `json.dumps({"result": final_text})`.
- `binder.py`'s `bind(...)` call to `evalspec.agents.judge_cli.run_host_judge` must not change behavior, model, or provider policy in Phase 1.
- `meta.json` drops the flat `judge_model` key and gains a nested `judge` object: `{harness, model, effort, timeout, env (redacted), harness_args, warnings}`. Every reader (writer in `plugin.py`, `tests/test_plugin.py`, `docs/concepts.md`, `docs/quickstart.md`, `docs/configuration.md`) is updated in the same task as the writer change.
- `[tool.evalspec.judge]` lives under top-level `[tool.evalspec]`, never under a set or an arm.
- Explicit precedence, per field: CLI override > scratch `--evalspec-config` > project `pyproject.toml` > built-in defaults.
- CLI overrides: `--evalspec-judge-harness`, `--evalspec-judge-model` (existing flag name kept, now maps into `JudgeConfig.model`), `--evalspec-judge-effort`, `--evalspec-judge-timeout`, repeatable `--evalspec-judge-harness-arg`, repeatable `--evalspec-judge-env KEY=VAL`.
- Local judge preflight (harness known, harness_args reserved-flag rejection, opencode provider-qualified model, conservative known-incompatible model/harness rejection) runs at pytest collection time (`pytest_generate_tests`), before any paid task arm executes. Binary-on-`PATH` preflight runs in a session-scoped autouse fixture (mirrors `_sandbox_preflight`), so it never fires under `--collect-only` and never breaks the many existing `_collect`-based plugin tests that don't have judge CLIs installed.
- Same-family detection is model-family-first (Anthropic: `claude`/`sonnet`/`opus`/`haiku`/`anthropic/...`; OpenAI: `gpt-*`/`o<digit>...`/`openai/...`; Gemini: `gemini*`/`google/...`); harness family is only a fallback when the model family can't be determined. One warning per run, not per arm.
- Judge `env` values are strings; `$VAR`/`${VAR}` expand from the host environment at judge *execution* time (never at collection); unset referenced vars raise. Reuse `evalspec.arms.expand_env` directly for this so judge env expansion is provably identical to arm env expansion — do not reimplement it.
- `CodingAgent.judge` is removed from the protocol and from `ClaudeCodeAgent`/`CodexAgent`/`OpenCodeAgent`; `agents/judge_cli.py` (`run_host_judge`, `raise_for_judge_cli_failure`) stays, used only by `binder.py`.
- Out of scope: binder config/Gemini binder changes, running the judge in the task sandbox, eval Markdown/discovery/report changes, Docker/microsandbox backend selection, Cursor/Copilot/Antigravity judge runners, broad reorg, back-compat shims for superseded internal module paths, remote availability preflight beyond local binary/model-family checks.
- No literal `evalspec run` subcommand exists in this codebase (`evalspec.__main__` only implements `evalspec lint`). Every `evalspec run --evalspec-config <fixture>` verification line in the source issue maps to `pytest -p evalspec.plugin --evalspec-config <fixture>` (equivalently `make evals EVAL_ARGS="--evalspec-config <fixture>"`) — see Task 15 and the Verification section.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/evalspec/judges/__init__.py` | Package re-exports: `JudgeConfig`, `resolve_judge_config`, `run_judge`, `known_judge_harnesses`. |
| `src/evalspec/judges/config.py` | `JudgeConfig` dataclass, `[tool.evalspec.judge]`-table validation, precedence resolution, structural (non-binary) local preflight. |
| `src/evalspec/judges/compat.py` | Model/harness family detection, conservative known-incompatible rejection, same-family warning text. |
| `src/evalspec/judges/registry.py` | Harness → runner/binary-name/version-probe lookup, binary-on-`PATH` preflight, `run_judge` dispatcher (env expansion + dispatch). |
| `src/evalspec/judges/claude_code.py` | Host `claude -p` judge runner — native `{"result": ...}` envelope; reuses `agents/judge_cli.raise_for_judge_cli_failure` for infra-error detection. |
| `src/evalspec/judges/codex.py` | Host `codex exec --json` judge runner — extracts final `agent_message` text, wraps the envelope, detects Codex-specific infra failures. |
| `src/evalspec/judges/opencode.py` | Host `opencode run --format json` judge runner — extracts final `text` events, wraps the envelope, detects OpenCode-specific infra failures. |
| `src/evalspec/judge.py` | Modified: `grade_run` takes `judge_config: JudgeConfig` and calls `judges.run_judge` instead of `agent.judge`. Prompt build / JSON parse / retry unchanged. |
| `src/evalspec/execution.py` | Modified: `run_eval_arm`/`_grade_via_judge`/`_grade_mixed` thread `judge_config` instead of `agent` + `judge_model`; `JUDGE_MODEL` constant removed. |
| `src/evalspec/agents/base.py` | Modified: `judge(...)` removed from the `CodingAgent` Protocol. |
| `src/evalspec/agents/claude.py`, `codex.py`, `opencode.py` | Modified: `.judge()` method and `run_host_judge` import removed from each concrete agent. |
| `src/evalspec/agents/judge_cli.py` | **Unchanged** — kept solely for `binder.py`. |
| `src/evalspec/binder.py` | **Unchanged.** |
| `src/evalspec/plugin.py` | Modified: new `--evalspec-judge-*` CLI options, `resolved_judge_config`, judge preflight wired into `pytest_generate_tests`, `meta.json["judge"]` nested object + same-family warnings in `_write_manifest`/`pytest_sessionfinish`; scratch-config TOML reading factored into a shared helper. |
| `src/evalspec/cases.py` | Modified: `judge_model` fixture replaced by `judge_config`; new session-scoped autouse `_judge_preflight` fixture (binary-on-PATH). |
| `tests/judges/test_config.py`, `test_compat.py`, `test_registry.py`, `test_claude_code.py`, `test_codex.py`, `test_opencode.py` | New unit tests for the `judges` package. |
| `tests/test_judge.py`, `tests/test_execution.py`, `tests/test_plugin.py`, `tests/test_binder.py`, `tests/agents/test_claude.py`, `tests/agents/test_codex.py`, `tests/agents/test_opencode.py` | Modified to match the new signatures/contracts. |
| `tests/fixtures/judge/*.toml` | New scratch-config fixtures for the five verification scenarios. |
| `docs/configuration.md`, `docs/agents.md`, `docs/quickstart.md`, `docs/concepts.md`, `docs/research/evalspec-readme-vision.md` | Documentation updated to match. |

---

### Task 1: `JudgeConfig` dataclass + field validation

**Files:**
- Create: `src/evalspec/judges/__init__.py`
- Create: `src/evalspec/judges/config.py`
- Test: `tests/judges/test_config.py`

**Interfaces:**
- Produces: `JudgeConfig` (frozen dataclass: `harness: str = "claude-code"`, `model: str = "sonnet"`, `effort: str = "medium"`, `timeout: int = 300`, `harness_args: list[str] = []`, `env: dict = {}`), `DEFAULT_HARNESS`, `DEFAULT_MODEL`, `DEFAULT_EFFORT`, `DEFAULT_TIMEOUT` constants, `_validate_judge_table(where: str, table: dict) -> dict` (module-private, reused by Task 3).
- Consumes: `evalspec.schema.SchemaError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_config.py
import pytest

from evalspec.judges.config import JudgeConfig, _validate_judge_table
from evalspec.schema import SchemaError


def test_judge_config_defaults():
    config = JudgeConfig()
    assert config.harness == "claude-code"
    assert config.model == "sonnet"
    assert config.effort == "medium"
    assert config.timeout == 300
    assert config.harness_args == []
    assert config.env == {}


def test_validate_judge_table_accepts_known_keys():
    validated = _validate_judge_table("[tool.evalspec.judge]", {
        "harness": "codex", "model": "gpt-5.5", "effort": "medium",
        "timeout": 300, "harness_args": ["--sandbox", "read-only"],
        "env": {"CODEX_HOME": "$CODEX_HOME"},
    })
    assert validated == {
        "harness": "codex", "model": "gpt-5.5", "effort": "medium",
        "timeout": 300, "harness_args": ["--sandbox", "read-only"],
        "env": {"CODEX_HOME": "$CODEX_HOME"},
    }


def test_validate_judge_table_rejects_unknown_key():
    with pytest.raises(SchemaError, match="unknown judge key"):
        _validate_judge_table("[tool.evalspec.judge]", {"harness": "codex", "bogus": 1})


@pytest.mark.parametrize("bad_table,match", [
    ({"harness": 5}, "harness"),
    ({"model": ""}, "model"),
    ({"effort": None}, "effort"),
    ({"timeout": "300"}, "timeout"),
    ({"timeout": 0}, "timeout"),
    ({"timeout": True}, "timeout"),
    ({"harness_args": "not-a-list"}, "harness_args"),
    ({"harness_args": [1, 2]}, "harness_args"),
    ({"env": ["not", "a", "table"]}, "env"),
    ({"env": {"KEY": 1}}, "env"),
])
def test_validate_judge_table_rejects_bad_types(bad_table, match):
    with pytest.raises(SchemaError, match=match):
        _validate_judge_table("[tool.evalspec.judge]", bad_table)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalspec.judges'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/__init__.py
"""Run-level judge abstraction: config resolution, family detection, and host-side
judge runners for claude-code/codex/opencode — independent from task arms.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
everything about WHICH harness grades and HOW it's invoked. `binder.py`'s host-Claude
call (`agents.judge_cli.run_host_judge`) is a separate, untouched transport.

NOTE: this module grows incrementally. Task 1 exports only `JudgeConfig`. Task 3
appends `resolve_judge_config` once it exists. Task 4 appends `run_judge` and
`known_judge_harnesses` once the registry module provides them. Do not import
symbols here before the task that creates them lands — `evalspec.judges` must stay
importable at every task boundary."""

from __future__ import annotations

from evalspec.judges.config import JudgeConfig

__all__ = [
    "JudgeConfig",
]
```

```python
# src/evalspec/judges/config.py
"""JudgeConfig: the run-level, harness-independent judge configuration.

Resolved once per run from four layers (CLI > scratch --evalspec-config >
pyproject.toml [tool.evalspec.judge] > built-in defaults — see resolve_judge_config
in Task 3). Structural validation (field types, known harness, reserved harness_args,
model/harness compatibility) happens at resolve time, safe to run under
`--collect-only` — see Task 3. Binary-on-PATH is a separate, later check
(judges.registry.preflight_judge_binary) that only runs when tests actually execute."""

from __future__ import annotations

from dataclasses import dataclass, field

from evalspec.schema import SchemaError

DEFAULT_HARNESS = "claude-code"
DEFAULT_MODEL = "sonnet"
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 300

_JUDGE_KEYS = ("harness", "model", "effort", "timeout", "harness_args", "env")


@dataclass(frozen=True)
class JudgeConfig:
    """The resolved, run-level judge: which harness grades, with what model/effort/
    timeout/pass-through args/env. Independent from every task arm's own harness/model/
    effort/env — resolving this must never mutate or read arm config."""
    harness: str = DEFAULT_HARNESS
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    timeout: int = DEFAULT_TIMEOUT
    harness_args: list[str] = field(default_factory=list, hash=False)
    env: dict = field(default_factory=dict, hash=False)


def _validate_judge_table(where: str, table: dict) -> dict:
    """Validate one layer's [tool.evalspec.judge]-shaped dict; return only the keys
    it declared (partial — callers merge over prior layers). Raises SchemaError
    naming the defect, never a silent no-op."""
    out: dict = {}
    if "harness" in table:
        value = table["harness"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `harness` must be a non-empty string")
        out["harness"] = value
    if "model" in table:
        value = table["model"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `model` must be a non-empty string")
        out["model"] = value
    if "effort" in table:
        value = table["effort"]
        if not isinstance(value, str) or not value:
            raise SchemaError(f"{where}: `effort` must be a non-empty string")
        out["effort"] = value
    if "timeout" in table:
        value = table["timeout"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SchemaError(f"{where}: `timeout` must be a positive integer")
        out["timeout"] = value
    if "harness_args" in table:
        value = table["harness_args"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SchemaError(f"{where}: `harness_args` must be a list of strings")
        out["harness_args"] = value
    if "env" in table:
        value = table["env"]
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise SchemaError(f"{where}: `env` must be a table of strings")
        out["env"] = value
    unknown = sorted(set(table) - set(_JUDGE_KEYS))
    if unknown:
        raise SchemaError(f"{where}: unknown judge key(s) {unknown} (known: {list(_JUDGE_KEYS)})")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/test_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/__init__.py src/evalspec/judges/config.py tests/judges/test_config.py
git commit -m "feat(judges): add JudgeConfig dataclass and judge-table validation"
```

---

### Task 2: Model/harness family detection, compatibility rejection, same-family warning

**Files:**
- Create: `src/evalspec/judges/compat.py`
- Test: `tests/judges/test_compat.py`

**Interfaces:**
- Consumes: `evalspec.schema.SchemaError`, `evalspec.arms.Arm` (for `same_family_warning`'s `arms` param — `.name`/`.harness`/`.model` attributes only).
- Produces: `model_family(model: str) -> str | None`, `harness_family(harness: str) -> str | None`, `effective_family(*, harness: str, model: str) -> str | None`, `validate_model_harness_compatibility(harness: str, model: str) -> None` (raises `SchemaError`), `same_family_warning(*, judge_harness: str, judge_model: str, arms) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_compat.py
import pytest

from evalspec.arms import Arm
from evalspec.judges.compat import (
    effective_family,
    harness_family,
    model_family,
    same_family_warning,
    validate_model_harness_compatibility,
)
from evalspec.schema import SchemaError


@pytest.mark.parametrize("model,family", [
    ("sonnet", "anthropic"), ("opus", "anthropic"), ("haiku", "anthropic"),
    ("claude-sonnet-4-6", "anthropic"), ("anthropic/claude-sonnet-4-6", "anthropic"),
    ("gpt-5.5", "openai"), ("gpt-5-mini", "openai"), ("openai/gpt-5.5", "openai"),
    ("o1", "openai"), ("o3-mini", "openai"),
    ("gemini-3.5-flash", "gemini"), ("google/gemini-3.5-flash", "gemini"),
    ("mystery-model-9", None),
])
def test_model_family(model, family):
    assert model_family(model) == family


def test_model_family_does_not_confuse_opus_with_openai_o_prefix():
    # "opus" starts with 'o' but must NOT match the OpenAI o<digit> pattern.
    assert model_family("opus") == "anthropic"


def test_harness_family():
    assert harness_family("claude-code") == "anthropic"
    assert harness_family("codex") == "openai"
    assert harness_family("opencode") is None  # multi-provider — no fixed family


def test_effective_family_prefers_model_then_falls_back_to_harness():
    assert effective_family(harness="codex", model="sonnet") == "anthropic"  # model wins
    assert effective_family(harness="codex", model="some-unmapped-name") == "openai"  # fallback


def test_validate_model_harness_compatibility_rejects_codex_plus_sonnet():
    with pytest.raises(SchemaError, match="codex"):
        validate_model_harness_compatibility("codex", "sonnet")


def test_validate_model_harness_compatibility_rejects_claude_code_plus_gpt():
    with pytest.raises(SchemaError, match="claude-code"):
        validate_model_harness_compatibility("claude-code", "gpt-5.5")


def test_validate_model_harness_compatibility_accepts_matching_pair():
    validate_model_harness_compatibility("codex", "gpt-5.5")  # no raise
    validate_model_harness_compatibility("claude-code", "sonnet")  # no raise


def test_validate_model_harness_compatibility_trusts_unknown_family():
    # A model evalspec can't classify is not locally rejectable — trust + record.
    validate_model_harness_compatibility("codex", "some-unmapped-name")  # no raise


def test_same_family_warning_fires_once_for_matching_arm():
    arms = [Arm("baseline", "claude-code", "sonnet"), Arm("trial", "claude-code", "opus")]
    warning = same_family_warning(judge_harness="claude-code", judge_model="sonnet", arms=arms)
    assert warning is not None
    assert "baseline" in warning and "trial" in warning


def test_same_family_warning_quiet_for_non_matching_arms():
    arms = [Arm("codex-arm", "codex", "gpt-5.5")]
    assert same_family_warning(judge_harness="claude-code", judge_model="sonnet", arms=arms) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_compat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalspec.judges.compat'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/compat.py
"""Model/harness family detection for same-family bias warnings and conservative
known-incompatible rejection.

Family is determined MODEL-FIRST: a model name's shape (e.g. "sonnet", "gpt-5.5")
usually says more than the harness it's paired with (a harness like `opencode` is
multi-provider by design). Harness family is only a fallback for a model evalspec
can't classify."""

from __future__ import annotations

import re

from evalspec.schema import SchemaError

_ANTHROPIC_BARE = {"sonnet", "opus", "haiku"}
# `^o\d` (not a bare "o" or "op*" prefix) so "opus" never matches the OpenAI
# reasoning-model shape (o1, o3, o4-mini, ...).
_OPENAI_REASONING_RE = re.compile(r"^o\d")

_HARNESS_FAMILY = {
    "claude-code": "anthropic",
    "codex": "openai",
    "opencode": None,  # multi-provider — no fixed family
}

# Conservative, locally-knowable rejection: reject only shapes that are unambiguous
# (e.g. codex+sonnet). Remote existence/quota/account access is never checked here.
_INCOMPATIBLE_FAMILIES = {
    "codex": {"anthropic", "gemini"},
    "claude-code": {"openai", "gemini"},
}


def model_family(model: str) -> str | None:
    m = model.strip().lower()
    if not m:
        return None
    if m in _ANTHROPIC_BARE or m.startswith("claude") or m.startswith("anthropic/"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("openai/") or _OPENAI_REASONING_RE.match(m):
        return "openai"
    if m.startswith("gemini") or m.startswith("google/"):
        return "gemini"
    return None


def harness_family(harness: str) -> str | None:
    return _HARNESS_FAMILY.get(harness)


def effective_family(*, harness: str, model: str) -> str | None:
    """Model family first; harness family only as a fallback when the model family
    can't be determined (e.g. a Codex-specific model name evalspec has no mapping for)."""
    return model_family(model) or harness_family(harness)


def validate_model_harness_compatibility(harness: str, model: str) -> None:
    """Raise SchemaError for a KNOWN-incompatible (harness, model) pair (e.g.
    harness="codex" with a Claude-family model). An unrecognized model family is not
    locally rejectable — trust + record, matching the rest of evalspec's config
    validation philosophy."""
    family = model_family(model)
    if family is None:
        return
    if family in _INCOMPATIBLE_FAMILIES.get(harness, set()):
        raise SchemaError(
            f"judge harness `{harness}` rejects model `{model}` (family={family}) — "
            "known-incompatible pair (e.g. codex+sonnet, claude-code+gpt-5.5); "
            "remote availability is not checked, only this local family mismatch"
        )


def same_family_warning(*, judge_harness: str, judge_model: str, arms) -> str | None:
    """One warning (or None) for the WHOLE run: non-None if any arm's effective
    family matches the judge's effective family — self-preference bias risk, not a
    block. `arms` is an iterable of objects with `.name`/`.harness`/`.model`
    (evalspec.arms.Arm in production)."""
    judge_fam = effective_family(harness=judge_harness, model=judge_model)
    if judge_fam is None:
        return None
    matches = [
        a.name for a in arms
        if effective_family(harness=a.harness, model=a.model) == judge_fam
    ]
    if not matches:
        return None
    return (
        f"same-family judge risk: judge harness={judge_harness} model={judge_model} "
        f"(family={judge_fam}) matches arm(s) {matches} — grading may be biased "
        "toward its own family's outputs"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/test_compat.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/compat.py tests/judges/test_compat.py
git commit -m "feat(judges): add model/harness family detection and same-family warning"
```

---

### Task 3: `resolve_judge_config` precedence resolution + structural preflight

**Files:**
- Create: `src/evalspec/judges/registry.py` (minimal stub — provides only `known_judge_harnesses`; extended by Task 4)
- Modify: `src/evalspec/judges/config.py`
- Modify: `src/evalspec/judges/__init__.py` (append `resolve_judge_config` to the public surface)
- Test: `tests/judges/test_config.py`

**Interfaces:**
- Consumes: `evalspec.judges.registry.known_judge_harnesses` (created as a stub by this task's Step 3, below; Task 4 extends the same file with `judge_binary`/`preflight_judge_binary`/`run_judge`), `evalspec.judges.compat.validate_model_harness_compatibility`, `evalspec.agents.claude._validate_harness_args`, `evalspec.agents.codex._validate_harness_args`, `evalspec.agents.opencode._validate_harness_args` (private, cross-module reuse — this codebase already does this for `judge._balanced_objects` between `judge.py`/`binder.py`).
- Produces: `resolve_judge_config(*, pyproject_table: dict | None = None, scratch_table: dict | None = None, cli_table: dict | None = None) -> JudgeConfig` (raises `SchemaError`). Also produces the `judges/registry.py` stub (`known_judge_harnesses` only) that Task 4 later extends in place.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_config.py (append)
from evalspec.judges.config import resolve_judge_config


def test_resolve_judge_config_defaults_with_no_layers():
    config = resolve_judge_config()
    assert config == JudgeConfig()


def test_resolve_judge_config_pyproject_layer():
    config = resolve_judge_config(pyproject_table={"harness": "codex", "model": "gpt-5.5"})
    assert config.harness == "codex"
    assert config.model == "gpt-5.5"
    assert config.effort == "medium"  # untouched fields keep the default


def test_resolve_judge_config_scratch_beats_pyproject_per_field():
    config = resolve_judge_config(
        pyproject_table={"harness": "codex", "model": "gpt-5.5", "effort": "high"},
        scratch_table={"model": "gpt-5-mini"},
    )
    assert config.harness == "codex"      # from pyproject, scratch didn't touch it
    assert config.model == "gpt-5-mini"   # scratch wins
    assert config.effort == "high"        # from pyproject


def test_resolve_judge_config_cli_beats_everything():
    config = resolve_judge_config(
        pyproject_table={"harness": "codex", "model": "gpt-5.5"},
        scratch_table={"model": "gpt-5-mini"},
        cli_table={"model": "gpt-5-nano"},
    )
    assert config.model == "gpt-5-nano"


def test_resolve_judge_config_env_shallow_merges_cli_over_config():
    config = resolve_judge_config(
        pyproject_table={"env": {"A": "1", "B": "2"}},
        cli_table={"env": {"B": "override", "C": "3"}},
    )
    assert config.env == {"A": "1", "B": "override", "C": "3"}


def test_resolve_judge_config_harness_args_cli_fully_replaces_not_merges():
    config = resolve_judge_config(
        pyproject_table={"harness_args": ["--from-pyproject"]},
        cli_table={"harness_args": ["--from-cli"]},
    )
    assert config.harness_args == ["--from-cli"]


def test_resolve_judge_config_rejects_unsupported_harness():
    with pytest.raises(SchemaError, match="not a supported judge harness"):
        resolve_judge_config(pyproject_table={"harness": "cursor"})


def test_resolve_judge_config_rejects_known_incompatible_pair():
    with pytest.raises(SchemaError, match="known-incompatible"):
        resolve_judge_config(pyproject_table={"harness": "codex", "model": "sonnet"})


def test_resolve_judge_config_rejects_reserved_harness_arg():
    with pytest.raises(SchemaError, match="reserved"):
        resolve_judge_config(pyproject_table={"harness": "claude-code", "harness_args": ["--model", "opus"]})


def test_resolve_judge_config_opencode_requires_provider_qualified_model():
    with pytest.raises(SchemaError, match="provider-qualified"):
        resolve_judge_config(pyproject_table={"harness": "opencode", "model": "sonnet"})


def test_resolve_judge_config_opencode_accepts_provider_qualified_model():
    config = resolve_judge_config(
        pyproject_table={"harness": "opencode", "model": "anthropic/claude-sonnet-4-6"},
    )
    assert config.harness == "opencode"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_judge_config'`

- [ ] **Step 3: Create the `judges/registry.py` stub**

`config.py`'s structural preflight needs `known_judge_harnesses()` to validate the
`harness` field, but the full runner registry (binary lookup, preflight, `run_judge`
dispatcher) doesn't exist until Task 4. Create a minimal stub now — Task 4 extends
this exact file in place rather than recreating it, mirroring the `TYPE_CHECKING`
stub-then-extend discipline used elsewhere in this plan (e.g. `registry.run_judge`'s
`NotImplementedError` stub, filled in by Task 8).

```python
# src/evalspec/judges/registry.py
"""Registered judge-runner harnesses. This file starts as a minimal stub (this task
provides only `known_judge_harnesses`, so `judges/config.py`'s structural preflight
can validate the `harness` field without depending on Task 4's runner registry).
Task 4 EXTENDS this file in place — adds `judge_binary`, `preflight_judge_binary`,
`run_judge`, and `probe_judge_version` — it does not recreate it."""

from __future__ import annotations

_BINARIES = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}


def known_judge_harnesses() -> frozenset[str]:
    """Registered judge-runner harnesses — deliberately its own registry, decoupled
    from evalspec.agents.known_harnesses() (task harnesses), even though the two
    currently name the same three values."""
    return frozenset(_BINARIES)
```

- [ ] **Step 4: Write minimal implementation**

```python
# src/evalspec/judges/config.py (append)
from evalspec.agents.claude import _validate_harness_args as _validate_claude_code_harness_args
from evalspec.agents.codex import _validate_harness_args as _validate_codex_harness_args
from evalspec.agents.opencode import _validate_harness_args as _validate_opencode_harness_args
from evalspec.judges.compat import validate_model_harness_compatibility
from evalspec.judges.registry import known_judge_harnesses

_HARNESS_ARG_VALIDATORS = {
    "claude-code": _validate_claude_code_harness_args,
    "codex": _validate_codex_harness_args,
    "opencode": _validate_opencode_harness_args,
}


def _preflight_judge_config(config: JudgeConfig) -> None:
    """Structural, environment-free checks — safe to run under `--collect-only` and
    on a host with no judge CLI installed. Binary-on-PATH is a separate, later check
    (judges.registry.preflight_judge_binary) run only when tests actually execute."""
    if config.harness not in known_judge_harnesses():
        raise SchemaError(
            f"[tool.evalspec.judge] harness `{config.harness}` is not a supported "
            f"judge harness (known: {sorted(known_judge_harnesses())})"
        )
    try:
        _HARNESS_ARG_VALIDATORS[config.harness](config.harness_args)
    except ValueError as e:
        raise SchemaError(
            f"[tool.evalspec.judge] harness_args invalid for `{config.harness}`: {e}"
        ) from e
    if config.harness == "opencode" and "/" not in config.model:
        raise SchemaError(
            "judge harness `opencode` needs a provider-qualified model "
            f"(e.g. 'anthropic/claude-sonnet-4-6'), got `{config.model}`"
        )
    validate_model_harness_compatibility(config.harness, config.model)


def resolve_judge_config(
    *, pyproject_table: dict | None = None, scratch_table: dict | None = None,
    cli_table: dict | None = None,
) -> JudgeConfig:
    """Resolve the run's one JudgeConfig from four layers, per field:
    CLI override > scratch --evalspec-config > pyproject [tool.evalspec.judge] >
    built-in defaults. `env` shallow-merges across layers (later layer's keys win);
    every other field fully replaces. Raises SchemaError on any structural defect —
    callers (plugin.py) turn that into a pytest.UsageError at collection time, before
    any paid task arm runs."""
    resolved: dict = {
        "harness": DEFAULT_HARNESS, "model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT,
        "timeout": DEFAULT_TIMEOUT, "harness_args": [], "env": {},
    }
    for label, table in (
        ("[tool.evalspec.judge]", pyproject_table),
        ("--evalspec-config [tool.evalspec.judge]", scratch_table),
        ("--evalspec-judge-* CLI overrides", cli_table),
    ):
        if not table:
            continue
        validated = _validate_judge_table(label, table)
        if "env" in validated:
            validated["env"] = {**resolved["env"], **validated["env"]}
        resolved.update(validated)

    config = JudgeConfig(**resolved)
    _preflight_judge_config(config)
    return config
```

- [ ] **Step 5: Export `resolve_judge_config` from `judges/__init__.py`**

`resolve_judge_config` now exists; append it to the package's public surface so
`evalspec.judges.resolve_judge_config` works. `run_judge`/`known_judge_harnesses`
still aren't re-exported here — that's Task 4, once `registry.py` provides real
implementations instead of stubs/`NotImplementedError`.

```python
# src/evalspec/judges/__init__.py (full file)
"""Run-level judge abstraction: config resolution, family detection, and host-side
judge runners for claude-code/codex/opencode — independent from task arms.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
everything about WHICH harness grades and HOW it's invoked. `binder.py`'s host-Claude
call (`agents.judge_cli.run_host_judge`) is a separate, untouched transport.

NOTE: this module grows incrementally. Task 4 appends `run_judge` and
`known_judge_harnesses` once the registry module provides real implementations."""

from __future__ import annotations

from evalspec.judges.config import JudgeConfig, resolve_judge_config

__all__ = [
    "JudgeConfig",
    "resolve_judge_config",
]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/judges/test_config.py -v`
Expected: PASS (18 tests total in the file). Also re-run `pytest tests/judges/ -v` — `known_judge_harnesses` now resolves against the Step 3 stub, with no forward dependency on Task 4. Confirm `python -c "import evalspec.judges"` still succeeds.

- [ ] **Step 7: Commit**

```bash
git add src/evalspec/judges/registry.py src/evalspec/judges/config.py src/evalspec/judges/__init__.py tests/judges/test_config.py
git commit -m "feat(judges): add resolve_judge_config precedence resolution, structural preflight, and registry stub"
```

---

### Task 4: Judge runner registry + binary-on-`PATH` preflight

**Files:**
- Modify: `src/evalspec/judges/registry.py` (extends the Task 3 stub — adds runner lookup, binary preflight, `run_judge` dispatcher)
- Modify: `src/evalspec/judges/__init__.py` (append `run_judge` and `known_judge_harnesses` to the public surface)
- Test: `tests/judges/test_registry.py`

**Interfaces:**
- Consumes: (`config: JudgeConfig` only via `TYPE_CHECKING` to avoid a runtime import cycle with `judges/config.py`, which imports `known_judge_harnesses` from this module). `known_judge_harnesses` and `_BINARIES` already exist from the Task 3 stub.
- Produces: `judge_binary(harness: str) -> str`, `preflight_judge_binary(config) -> None` (raises `RuntimeError`), `run_judge(prompt: str, *, config) -> str` (dispatcher — body added in Task 8 once runners exist; this task adds the registry plumbing and a `NotImplementedError` stub so imports resolve), `probe_judge_version(harness: str) -> str | None` (stub returning `None`, filled in Task 8). `known_judge_harnesses() -> frozenset[str]` is unchanged from Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_registry.py
import pytest

from evalspec.judges.registry import judge_binary, known_judge_harnesses, preflight_judge_binary


def test_known_judge_harnesses():
    assert known_judge_harnesses() == frozenset({"claude-code", "codex", "opencode"})


def test_judge_binary_names():
    assert judge_binary("claude-code") == "claude"
    assert judge_binary("codex") == "codex"
    assert judge_binary("opencode") == "opencode"


class _Config:
    def __init__(self, harness):
        self.harness = harness


def test_preflight_judge_binary_raises_when_missing(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        preflight_judge_binary(_Config("claude-code"))


def test_preflight_judge_binary_passes_when_present(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    preflight_judge_binary(_Config("codex"))  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_registry.py -v`
Expected: FAIL with `ImportError: cannot import name 'judge_binary' from 'evalspec.judges.registry'` — the module exists (Task 3's stub) but `judge_binary`/`preflight_judge_binary` don't yet.

- [ ] **Step 3: Extend the registry.py stub**

This replaces the whole file with its Task 4 shape: the Task 3 stub's `_BINARIES` and
`known_judge_harnesses` are unchanged, and this task adds `judge_binary`,
`preflight_judge_binary`, `run_judge`, and `probe_judge_version` alongside them.

```python
# src/evalspec/judges/registry.py (full file — extends the Task 3 stub in place)
"""Harness -> judge runner lookup, binary-on-PATH preflight, and the run_judge
dispatcher. `run_judge`/`probe_judge_version` are completed in Task 8 once the
per-harness runner modules exist (Tasks 5-7). `known_judge_harnesses`/`_BINARIES`
below are unchanged from the Task 3 stub that `judges/config.py`'s structural
preflight already depends on; this task adds everything else (JudgeConfig is only
imported here under TYPE_CHECKING, never at runtime, to avoid a circular import with
`judges/config.py`)."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evalspec.judges.config import JudgeConfig

_BINARIES = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}


def known_judge_harnesses() -> frozenset[str]:
    """Registered judge-runner harnesses — deliberately its own registry, decoupled
    from evalspec.agents.known_harnesses() (task harnesses), even though the two
    currently name the same three values."""
    return frozenset(_BINARIES)


def judge_binary(harness: str) -> str:
    return _BINARIES[harness]


def preflight_judge_binary(config: JudgeConfig) -> None:
    """RuntimeError if the selected judge harness's binary is missing from PATH.
    Environment-dependent — call only when tests actually execute (a session-scoped
    autouse fixture, see cases.py), never from collection-time code that also runs
    under --collect-only."""
    binary = judge_binary(config.harness)
    if shutil.which(binary) is None:
        raise RuntimeError(
            f"judge harness `{config.harness}` binary `{binary}` not found on PATH"
        )


def run_judge(prompt: str, *, config: JudgeConfig) -> str:
    raise NotImplementedError("wired up in Task 8 once the per-harness runners exist")


def probe_judge_version(harness: str) -> str | None:
    return None  # filled in Task 8
```

- [ ] **Step 4: Export `run_judge` and `known_judge_harnesses` from `judges/__init__.py`**

Both names now exist in `registry.py` (`run_judge` still raises `NotImplementedError`
until Task 8 fills in the dispatcher body — that's fine, the export is about the name
resolving, not the implementation being complete). This is the last `__init__.py`
edit in the plan; every symbol in `__all__` now exists for the remainder of the tasks.

```python
# src/evalspec/judges/__init__.py (full file)
"""Run-level judge abstraction: config resolution, family detection, and host-side
judge runners for claude-code/codex/opencode — independent from task arms.

`judge.py` keeps prompt-building, JSON parsing, and retry semantics; this package owns
everything about WHICH harness grades and HOW it's invoked. `binder.py`'s host-Claude
call (`agents.judge_cli.run_host_judge`) is a separate, untouched transport."""

from __future__ import annotations

from evalspec.judges.config import JudgeConfig, resolve_judge_config
from evalspec.judges.registry import known_judge_harnesses, run_judge

__all__ = [
    "JudgeConfig",
    "resolve_judge_config",
    "run_judge",
    "known_judge_harnesses",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/judges/test_registry.py -v`
Expected: PASS (4 tests). Also re-run `pytest tests/judges/ -v` — Task 3's tests still pass (this task only added functions, it didn't touch `known_judge_harnesses`). Confirm `python -c "import evalspec.judges"` still succeeds.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/judges/registry.py src/evalspec/judges/__init__.py tests/judges/test_registry.py
git commit -m "feat(judges): extend judge runner registry with binary preflight and run_judge dispatcher"
```

---

### Task 5: Claude Code judge runner

**Files:**
- Create: `src/evalspec/judges/claude_code.py`
- Test: `tests/judges/test_claude_code.py`

**Interfaces:**
- Consumes: `evalspec.agents.judge_cli.raise_for_judge_cli_failure` (shared infra-error check — **not** `run_host_judge` itself, which stays exclusively wired to `binder.py`, per the Global Constraints).
- Produces: `run(prompt: str, *, model: str, effort: str, timeout: int, harness_args: list[str], env: dict) -> str`, `probe_version() -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_claude_code.py
import json
import subprocess

import pytest

from evalspec.judges import claude_code


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_returns_native_result_envelope(monkeypatch):
    payload = json.dumps({"result": '{"assertions": []}', "is_error": False})
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return _fake_proc(stdout=payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = claude_code.run(
        "grade this", model="sonnet", effort="medium", timeout=300,
        harness_args=["--plugin-dir", "/x"], env={"FOO": "bar"},
    )

    assert out == payload
    assert json.loads(out)["result"] == '{"assertions": []}'
    assert captured["cmd"][:2] == ["claude", "-p"]
    assert "--model" in captured["cmd"] and "sonnet" in captured["cmd"]
    assert "--effort" in captured["cmd"] and "medium" in captured["cmd"]
    assert captured["cmd"][-2:] == ["--plugin-dir", "/x"]
    assert captured["env"]["FOO"] == "bar"


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="connection reset"),
    )
    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_is_error_envelope(monkeypatch):
    payload = json.dumps({"result": "Not logged in", "is_error": True})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(stdout=payload))
    with pytest.raises(RuntimeError, match="is_error=true.*Not logged in"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        claude_code.run("p", model="sonnet", effort="medium", timeout=300, harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    assert claude_code.probe_version() is None


def test_probe_version_returns_stripped_stdout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(stdout="2.1.0\n"))
    assert claude_code.probe_version() == "2.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_claude_code.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalspec.judges.claude_code'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/claude_code.py
"""Host-side Claude Code judge runner.

Reuses agents/judge_cli.raise_for_judge_cli_failure for the same infra-error contract
binder.py relies on (auth/quota/rate-limit/nonzero-exit) so the two call sites can
never disagree on what counts as an infra failure — but builds its own command so
effort/harness_args/env are configurable per JudgeConfig. binder.py's run_host_judge
stays untouched with its own narrower model+timeout call shape; nothing here imports
or calls it."""

from __future__ import annotations

import os
import subprocess

from evalspec.agents.judge_cli import raise_for_judge_cli_failure

BIN = "claude"


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    """`claude -p --output-format json` already emits {"result": ..., "is_error": ...}
    — the native envelope judge.py expects, no wrapping needed."""
    cmd = [BIN, "-p", prompt, "--output-format", "json", "--model", model,
           "--effort", effort, *harness_args]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=run_env)
    except FileNotFoundError as e:
        raise RuntimeError("host claude CLI not found on PATH") from e
    raise_for_judge_cli_failure(proc)
    return proc.stdout


def probe_version() -> str | None:
    """Best-effort version probe — never raises, never fails the run."""
    try:
        proc = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/test_claude_code.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/claude_code.py tests/judges/test_claude_code.py
git commit -m "feat(judges): add Claude Code host-side judge runner"
```

---

### Task 6: Codex judge runner

**Files:**
- Create: `src/evalspec/judges/codex.py`
- Test: `tests/judges/test_codex.py`

**Interfaces:**
- Consumes: `evalspec.trajectory.iter_events` (shared JSONL parsing helper already used by `agents/codex.py` and `agents/opencode.py`).
- Produces: `run(prompt: str, *, model: str, effort: str, timeout: int, harness_args: list[str], env: dict) -> str`, `probe_version() -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_codex.py
import json
import subprocess

import pytest

from evalspec.judges import codex as codex_judge


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _jsonl(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_run_wraps_final_agent_message_in_result_envelope(monkeypatch):
    stdout = _jsonl(
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "agent_message",
                                             "text": '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    )
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = codex_judge.run(
        "grade this", model="gpt-5.5", effort="medium", timeout=300,
        harness_args=["--sandbox", "read-only"], env={},
    )

    envelope = json.loads(out)
    assert envelope["result"] == '{"assertions": [{"text": "a", "passed": true, "evidence": "ok"}]}'
    assert captured["cmd"][:3] == ["codex", "exec", "--json"]
    assert "-m" in captured["cmd"] and "gpt-5.5" in captured["cmd"]
    assert captured["cmd"][-1] == "grade this"  # trailing positional prompt


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="network error"),
    )
    with pytest.raises(RuntimeError, match="exited 1.*network error"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_on_error_event(monkeypatch):
    stdout = _jsonl({"type": "error", "message": "Unauthorized: invalid API key"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(stdout=stdout))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        codex_judge.run("p", model="gpt-5.5", effort="medium", timeout=300, harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert codex_judge.probe_version() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_codex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalspec.judges.codex'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/codex.py
"""Host-side Codex judge runner.

Codex `exec --json` has no native {"result": ...} envelope the way `claude -p
--output-format json` does — extract the final agent_message text from the JSONL
stream and wrap it manually. Infra-error detection is Codex-specific: a
`turn.failed`/`error` event, or a nonzero exit."""

from __future__ import annotations

import json
import os
import subprocess

from evalspec.trajectory import iter_events

BIN = "codex"


def _event_item(event: dict) -> dict:
    item = event.get("item") if isinstance(event, dict) else None
    return item if isinstance(item, dict) else {}


def _extract_final_text(stdout: str) -> str:
    parts: list[str] = []
    for event in iter_events(stdout):
        if event.get("type") != "item.completed":
            continue
        item = _event_item(event)
        if item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _raise_for_codex_infra_failure(proc: subprocess.CompletedProcess) -> None:
    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"host codex CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}")
    for event in iter_events(proc.stdout):
        if event.get("type") in {"error", "turn.failed"}:
            msg = str(event.get("message") or event.get("error") or "").strip()
            raise RuntimeError(f"host codex CLI reported an error: {msg[:1000] or '(no message)'}")


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    # `effort` has no stable Codex exec flag (mirrors agents/codex.py's build_command,
    # which accepts effort for protocol parity but doesn't wire it).
    del effort
    cmd = [BIN, "exec", "--json", "-m", model, *harness_args, prompt]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=run_env)
    except FileNotFoundError as e:
        raise RuntimeError("host codex CLI not found on PATH") from e
    _raise_for_codex_infra_failure(proc)
    return json.dumps({"result": _extract_final_text(proc.stdout)})


def probe_version() -> str | None:
    try:
        proc = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/test_codex.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/codex.py tests/judges/test_codex.py
git commit -m "feat(judges): add Codex host-side judge runner"
```

---

### Task 7: OpenCode judge runner

**Files:**
- Create: `src/evalspec/judges/opencode.py`
- Test: `tests/judges/test_opencode.py`

**Interfaces:**
- Consumes: `evalspec.trajectory.iter_events`.
- Produces: `run(prompt: str, *, model: str, effort: str, timeout: int, harness_args: list[str], env: dict) -> str`, `probe_version() -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_opencode.py
import json
import subprocess

import pytest

from evalspec.judges import opencode as opencode_judge


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _jsonl(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_run_wraps_final_text_events_in_result_envelope(monkeypatch):
    stdout = _jsonl(
        {"type": "step_start"},
        {"type": "text", "part": {"text": '{"assertions": [{"text": "a", "passed": false, "evidence": "no"}]}'}},
        {"type": "step_finish", "part": {"tokens": {"total": 42}}},
    )
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_proc(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = opencode_judge.run(
        "grade this", model="anthropic/claude-sonnet-4-6", effort="high", timeout=300,
        harness_args=[], env={},
    )

    envelope = json.loads(out)
    assert envelope["result"] == '{"assertions": [{"text": "a", "passed": false, "evidence": "no"}]}'
    assert captured["cmd"][:2] == ["opencode", "run"]
    assert "--variant" in captured["cmd"] and "thorough" in captured["cmd"]  # high -> thorough
    assert captured["cmd"][-1] == "grade this"


def test_run_raises_runtimeerror_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_proc(returncode=1, stderr="connection reset"),
    )
    with pytest.raises(RuntimeError, match="exited 1.*connection reset"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_on_infra_looking_stderr(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(stdout="", stderr="401 Unauthorized: invalid API key", returncode=0),
    )
    with pytest.raises(RuntimeError, match="Unauthorized"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_run_raises_runtimeerror_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="not found on PATH"):
        opencode_judge.run("p", model="anthropic/claude-sonnet-4-6", effort="medium", timeout=300,
                            harness_args=[], env={})


def test_probe_version_best_effort_none_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert opencode_judge.probe_version() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_opencode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalspec.judges.opencode'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/opencode.py
"""Host-side OpenCode judge runner.

OpenCode's JSONL stream has no terminal result event and no documented explicit
error-event type (see agents/opencode.py's comments) — a nonzero exit or an
auth/quota/rate-limit-shaped stderr message are the locally-detectable infra-failure
signals. Final text is every non-empty `text` event's `part.text`, matching
agents/opencode.py's own text-collection rule."""

from __future__ import annotations

import json
import os
import subprocess

from evalspec.trajectory import iter_events

BIN = "opencode"

_EFFORT_TO_VARIANT = {"low": "fast", "medium": "default", "high": "thorough"}
_INFRA_KEYWORDS = (
    "unauthorized", "auth", "quota", "rate limit", "rate-limit", "overloaded",
    "429", "401", "403",
)


def _looks_like_infra_failure(text: str) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in _INFRA_KEYWORDS)


def _extract_final_text(stdout: str) -> str:
    parts: list[str] = []
    for event in iter_events(stdout):
        if event.get("type") != "text":
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _raise_for_opencode_infra_failure(proc: subprocess.CompletedProcess) -> None:
    if proc.returncode != 0:
        body = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"host opencode CLI exited {proc.returncode}: {body[-1000:] or '(no output)'}")
    stderr = (proc.stderr or "").strip()
    if stderr and _looks_like_infra_failure(stderr):
        raise RuntimeError(f"host opencode CLI reported an error: {stderr[-1000:]}")


def run(
    prompt: str, *, model: str, effort: str, timeout: int,
    harness_args: list[str], env: dict,
) -> str:
    variant = _EFFORT_TO_VARIANT.get(effort, "default")
    cmd = [BIN, "run", "--format", "json", "--variant", variant, "-m", model,
           *harness_args, prompt]
    run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=run_env,
            stdin=subprocess.DEVNULL,  # opencode blocks reading stdin forever without this
        )
    except FileNotFoundError as e:
        raise RuntimeError("host opencode CLI not found on PATH") from e
    _raise_for_opencode_infra_failure(proc)
    return json.dumps({"result": _extract_final_text(proc.stdout)})


def probe_version() -> str | None:
    try:
        proc = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/test_opencode.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/opencode.py tests/judges/test_opencode.py
git commit -m "feat(judges): add OpenCode host-side judge runner"
```

---

### Task 8: `run_judge` dispatcher — env expansion + version probing

**Files:**
- Modify: `src/evalspec/judges/registry.py`
- Modify: `src/evalspec/judges/__init__.py`
- Test: `tests/judges/test_registry.py`

**Interfaces:**
- Consumes: `evalspec.arms.expand_env` (reused directly — the constraint that judge env expansion is provably identical to arm env expansion), `judges.claude_code.run`/`probe_version`, `judges.codex.run`/`probe_version`, `judges.opencode.run`/`probe_version`.
- Produces: `run_judge(prompt: str, *, config: JudgeConfig) -> str` (full dispatch), `probe_judge_version(harness: str) -> str | None` (full implementation, never raises).

- [ ] **Step 1: Write the failing test**

```python
# tests/judges/test_registry.py (append)
import os

import pytest

from evalspec.judges.config import JudgeConfig
from evalspec.judges.registry import probe_judge_version, run_judge
from evalspec.schema import SchemaError


def test_run_judge_dispatches_by_harness(monkeypatch):
    calls = []

    def fake_claude_run(prompt, **kw):
        calls.append(("claude-code", prompt, kw))
        return '{"result": "ok"}'

    monkeypatch.setattr("evalspec.judges.claude_code.run", fake_claude_run)

    out = run_judge("grade", config=JudgeConfig(harness="claude-code", model="sonnet"))

    assert out == '{"result": "ok"}'
    assert calls[0][0] == "claude-code"
    assert calls[0][2]["model"] == "sonnet"


def test_run_judge_expands_env_using_arms_expand_env(monkeypatch):
    monkeypatch.setenv("MY_JUDGE_VAR", "expanded-value")
    captured = {}

    def fake_codex_run(prompt, **kw):
        captured["env"] = kw["env"]
        return '{"result": "ok"}'

    monkeypatch.setattr("evalspec.judges.codex.run", fake_codex_run)

    run_judge("grade", config=JudgeConfig(harness="codex", model="gpt-5.5",
                                          env={"CODEX_HOME": "$MY_JUDGE_VAR", "LITERAL": "x"}))

    assert captured["env"] == {"CODEX_HOME": "expanded-value", "LITERAL": "x"}


def test_run_judge_env_unset_var_raises_schemaerror(monkeypatch):
    monkeypatch.delenv("DEFINITELY_UNSET_JUDGE_VAR", raising=False)

    with pytest.raises(SchemaError, match="DEFINITELY_UNSET_JUDGE_VAR"):
        run_judge("grade", config=JudgeConfig(env={"KEY": "$DEFINITELY_UNSET_JUDGE_VAR"}))


def test_probe_judge_version_best_effort_none_on_exception(monkeypatch):
    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr("evalspec.judges.claude_code.probe_version", boom)
    assert probe_judge_version("claude-code") is None


def test_probe_judge_version_returns_probe_result(monkeypatch):
    monkeypatch.setattr("evalspec.judges.codex.probe_version", lambda: "1.2.3")
    assert probe_judge_version("codex") == "1.2.3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/judges/test_registry.py -v`
Expected: FAIL — `run_judge`/`probe_judge_version` raise `NotImplementedError`/return the stub `None` unconditionally.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judges/registry.py — replace the stub run_judge/probe_judge_version
# and add the runner/probe maps + os/expand_env imports at the top.
import os

from evalspec.arms import expand_env

from evalspec.judges import claude_code, codex, opencode

_RUNNERS = {
    "claude-code": claude_code.run,
    "codex": codex.run,
    "opencode": opencode.run,
}
_VERSION_PROBES = {
    "claude-code": claude_code.probe_version,
    "codex": codex.probe_version,
    "opencode": opencode.probe_version,
}


def run_judge(prompt: str, *, config: JudgeConfig) -> str:
    """Expand config.env exactly like an arm's env (evalspec.arms.expand_env — same
    function, so judge env expansion is provably identical to arm env expansion),
    then dispatch to the selected harness's runner. Raises SchemaError for an unset
    referenced $VAR (never a silent empty string); RuntimeError propagates unchanged
    from the runner for infra failures."""
    env = expand_env(config.env, os.environ)
    runner = _RUNNERS[config.harness]  # KeyError impossible after config.py's preflight
    return runner(
        prompt, model=config.model, effort=config.effort, timeout=config.timeout,
        harness_args=config.harness_args, env=env,
    )


def probe_judge_version(harness: str) -> str | None:
    """Best-effort version probe for meta.json. Never raises — any exception from the
    per-harness probe degrades to None, matching the existing agent-version pattern
    in plugin._write_manifest."""
    probe = _VERSION_PROBES.get(harness)
    if probe is None:
        return None
    try:
        return probe()
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/judges/ -v`
Expected: PASS (all tests in `tests/judges/`, ~39 total)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judges/registry.py tests/judges/test_registry.py
git commit -m "feat(judges): wire run_judge dispatcher with arm-parity env expansion"
```

---

### Task 9: Decouple `judge.py` transport — `grade_run` takes `JudgeConfig`

**Files:**
- Modify: `src/evalspec/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `evalspec.judges.JudgeConfig`, `evalspec.judges.run_judge`.
- Produces: `grade_run(assertions, tree, file_contents, shas, final_message, eval_id, config, *, judge_config: JudgeConfig | None = None, original_shas=None, process_facts="") -> dict` — **drops** `agent`, `model`, `timeout` params. `build_judge_prompt` and `parse_judge_json` are unchanged.

- [ ] **Step 1: Write the failing test**

Read `tests/test_judge.py` first to find its current `grade_run` tests (they pass a fake `agent` with `.judge(...)`) and replace them:

```python
# tests/test_judge.py — replace every grade_run test's fake `agent=` fixture with
# `judge_config=` + a monkeypatched evalspec.judge.run_judge. Representative tests:

import pytest

from evalspec.judge import grade_run
from evalspec.judges import JudgeConfig


def test_grade_run_calls_run_judge_with_the_resolved_config(monkeypatch):
    captured = {}

    def fake_run_judge(prompt, *, config):
        captured["config"] = config
        return '{"result": "{\\"assertions\\": [{\\"text\\": \\"a1\\", \\"passed\\": true, \\"evidence\\": \\"ok\\"}]}"}'

    monkeypatch.setattr("evalspec.judge.run_judge", fake_run_judge)

    judge_config = JudgeConfig(harness="codex", model="gpt-5.5")
    result = grade_run(
        ["a1"], "tree", {}, {}, "final message", "eval1", "trial",
        judge_config=judge_config,
    )

    assert captured["config"] == judge_config
    assert result["assertions"][0]["passed"] is True


def test_grade_run_defaults_to_a_default_judge_config_when_none_given(monkeypatch):
    captured = {}

    def fake_run_judge(prompt, *, config):
        captured["config"] = config
        return '{"result": "{\\"assertions\\": [{\\"text\\": \\"a1\\", \\"passed\\": true, \\"evidence\\": \\"ok\\"}]}"}'

    monkeypatch.setattr("evalspec.judge.run_judge", fake_run_judge)

    grade_run(["a1"], "tree", {}, {}, "final message", "eval1", "trial")

    assert captured["config"] == JudgeConfig()  # harness=claude-code, model=sonnet, ...


def test_grade_run_does_not_catch_runtimeerror(monkeypatch):
    def boom(prompt, *, config):
        raise RuntimeError("host claude CLI returned is_error=true: Not logged in")

    monkeypatch.setattr("evalspec.judge.run_judge", boom)

    with pytest.raises(RuntimeError, match="Not logged in"):
        grade_run(["a1"], "tree", {}, {}, "final", "eval1", "trial")
```

Apply the same `judge_config=`/`monkeypatch("evalspec.judge.run_judge", ...)` substitution to every other pre-existing `grade_run` test in `tests/test_judge.py` (retry-on-malformed-JSON, assertion-count-mismatch masking, etc.) — keep their assertions unchanged, only swap the fake `agent.judge` for a monkeypatched `evalspec.judge.run_judge`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_judge.py -v`
Expected: FAIL — `grade_run()` still calls `agent.judge(...)`; `TypeError: grade_run() got an unexpected keyword argument 'judge_config'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/judge.py — add the import and replace grade_run's signature/body.
from evalspec.judges import JudgeConfig, run_judge


def grade_run(
    assertions,
    tree,
    file_contents,
    shas,
    final_message,
    eval_id,
    config,
    *,
    judge_config: JudgeConfig | None = None,
    original_shas=None,
    process_facts="",
) -> dict:
    judge_config = judge_config or JudgeConfig()
    prompt = build_judge_prompt(
        assertions, tree, file_contents, shas, final_message, original_shas,
        process_facts=process_facts,
    )
    for attempt in (1, 2):
        try:
            raw = run_judge(prompt, config=judge_config)
            outer = json.loads(raw)
            graded = parse_judge_json(outer.get("result", ""), eval_id, config)
            if len(graded["assertions"]) != len(assertions):
                raise ValueError("judge returned a different assertion count")
            return graded
        except (
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ):
            if attempt == 1:
                continue
            return {
                "eval_id": eval_id,
                "arm": config,
                "assertions": [
                    {
                        "text": a,
                        "passed": False,
                        "evidence": "JUDGE ERROR: unparseable output",
                    }
                    for a in assertions
                ],
            }
    raise AssertionError("unreachable: loop exits via return in both branches")
```

Also update the module docstring's second sentence (`"Spawning the judge subprocess is CodingAgent.judge's job"`) to read: `"Spawning the judge subprocess is evalspec.judges.run_judge's job"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_judge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/judge.py tests/test_judge.py
git commit -m "refactor(judge): grade_run takes JudgeConfig, calls judges.run_judge"
```

---

### Task 10: Deprecate `CodingAgent.judge` + prove `binder.py` is unchanged

**Files:**
- Modify: `src/evalspec/agents/base.py`
- Modify: `src/evalspec/agents/claude.py`
- Modify: `src/evalspec/agents/codex.py`
- Modify: `src/evalspec/agents/opencode.py`
- Modify: `tests/agents/test_claude.py`, `tests/agents/test_codex.py`, `tests/agents/test_opencode.py`
- Modify: `tests/test_binder.py`
- Test (new, in the same file): `tests/test_binder.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `CodingAgent` Protocol with no `judge` member; `ClaudeCodeAgent`/`CodexAgent`/`OpenCodeAgent` with no `.judge()` method. `evalspec.agents.judge_cli.run_host_judge`/`raise_for_judge_cli_failure` **unchanged** — still importable exactly as before.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_binder.py (append) — proves binder.py's transport is untouched by this
# refactor: same function object, same file, imported the same way.
from evalspec import binder
from evalspec.agents.judge_cli import run_host_judge


def test_binder_still_imports_the_original_run_host_judge():
    assert binder.run_host_judge is run_host_judge
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binder.py -v`
Expected: PASS already (no code changed yet) — this step is a baseline confirmation, not a red step. Run it now to record the pre-refactor baseline; it must **stay** green through every step below.

- [ ] **Step 3: Remove `.judge()` from the protocol and concrete agents**

```python
# src/evalspec/agents/base.py — delete this one line from the CodingAgent Protocol:
#     def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str: ...
```

```python
# src/evalspec/agents/claude.py
# 1. Delete the import: `from evalspec.agents.judge_cli import run_host_judge`
# 2. Delete the method:
#     def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str:
#         """Grade via the host's `claude -p` ..."""
#         return run_host_judge(prompt, model=model, timeout=timeout)
```

```python
# src/evalspec/agents/codex.py
# 1. Delete the import: `from evalspec.agents.judge_cli import run_host_judge`
# 2. Delete the method:
#     def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str:
#         return run_host_judge(prompt, model=model, timeout=timeout)
```

```python
# src/evalspec/agents/opencode.py
# 1. Delete the import: `from evalspec.agents.judge_cli import run_host_judge`
# 2. Delete the method:
#     def judge(self, prompt: str, *, model: str, timeout: int = 300) -> str:
#         """Delegate judging to the host's Claude CLI ..."""
#         return run_host_judge(prompt, model=model, timeout=timeout)
```

- [ ] **Step 4: Remove the now-obsolete `.judge()` tests and their now-unused `subprocess` import**

In `tests/agents/test_claude.py`: delete `test_judge_returns_stdout_on_healthy_run`, `test_judge_raises_runtimeerror_on_nonzero_exit`, `test_judge_raises_runtimeerror_on_is_error_envelope`, and the `_fake_proc` helper (used only by these three). Then remove `import subprocess` from the top of the file (grep the file for `subprocess` first — it must have zero remaining uses; `ruff` fails the build on an unused import).

In `tests/agents/test_codex.py`: delete `test_judge_delegates_to_host_claude` and its `_fake_proc` helper; remove `import subprocess` if now unused.

In `tests/agents/test_opencode.py`: delete `test_judge_raises_runtimeerror_on_nonzero_exit`, `test_judge_raises_runtimeerror_on_is_error_envelope`, and their `_fake_proc` helper; remove `import subprocess` if now unused.

Run: `grep -n "subprocess" tests/agents/test_claude.py tests/agents/test_codex.py tests/agents/test_opencode.py` after deleting — confirm each file has either zero matches (drop the import) or a remaining non-judge use (keep it).

- [ ] **Step 5: Run tests to verify everything passes**

Run: `pytest tests/agents/ tests/test_binder.py -v`
Expected: PASS — no `.judge()` references remain in `tests/agents/`; `tests/test_binder.py::test_binder_still_imports_the_original_run_host_judge` still passes, proving `agents/judge_cli.py` was never touched.

Run: `ruff check src/evalspec/agents/ tests/agents/`
Expected: clean — no unused imports.

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/agents/base.py src/evalspec/agents/claude.py \
        src/evalspec/agents/codex.py src/evalspec/agents/opencode.py \
        tests/agents/test_claude.py tests/agents/test_codex.py \
        tests/agents/test_opencode.py tests/test_binder.py
git commit -m "refactor(agents): remove CodingAgent.judge; binder.py stays untouched"
```

---

### Task 11: `execution.py` — thread `JudgeConfig` through grading

**Files:**
- Modify: `src/evalspec/execution.py:34-36,87-144,191-259`
- Modify: `tests/test_execution.py`

**Interfaces:**
- Consumes: `evalspec.judges.JudgeConfig`.
- Produces: `run_eval_arm(..., judge_config: JudgeConfig | None = None, ...)` — **drops** `judge_model: str = JUDGE_MODEL` and the module-level `JUDGE_MODEL` constant. `grade` callables (real `grade_run` and test fakes) are now called as `grade(assertions, tree, contents, shas, result_text, eval_id, arm_name, judge_config=judge_config, original_shas=..., process_facts=...)` — **no more `agent=`/`model=`**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution.py — update the module-level fake and two judge-model tests.

# Replace the existing `_grade_all_pass` fake's signature (it currently accepts
# `agent=None, model="sonnet"`):
def _grade_all_pass(assertions, tree, contents, shas, final, eval_id, config,
                    *, judge_config=None, original_shas=None, process_facts=""):
    return {
        "eval_id": eval_id,
        "arm": config,
        "assertions": [{"text": a, "passed": True, "evidence": "ok"} for a in assertions],
    }


# Replace test_judge_model_used_by_default (uses JUDGE_MODEL today) with:
def test_default_judge_config_used_by_default(tmp_path):
    from evalspec.judges import JudgeConfig
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "mu", "prompt": "p", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions, *a, judge_config, **k):
        seen_configs.append(judge_config)
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    run_eval_arm(
        ec, Arm("trial", "opencode", "google/gemini-3.5-flash"), workdir, {}, tmp_path,
        today="2099-01-01", repo_root=tmp_path, sample=0,
        session_factory=fake_session_factory(
            RunResult("mu", "trial", "did it", 1, 1, False, session_id="s", fired=True)),
        grade=grade, bind=_punt_all,
    )

    assert seen_configs == [JudgeConfig()]


# Replace test_judge_model_param_overrides_default with:
def test_judge_config_param_overrides_default(tmp_path):
    from evalspec.judges import JudgeConfig
    workspace.set_current_iteration("iteration_01")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    ec = _case(tmp_path, {"slug": "nu", "prompt": "p", "assertions": ["a"]})
    seen_configs = []

    def grade(assertions, *a, judge_config, **k):
        seen_configs.append(judge_config)
        return {"assertions": [{"text": x, "passed": True, "evidence": "ok"} for x in assertions]}

    custom = JudgeConfig(harness="codex", model="gpt-5.5")
    run_eval_arm(
        ec, TRIAL, workdir, {}, tmp_path, today="2099-01-01", repo_root=tmp_path, sample=0,
        judge_config=custom,
        session_factory=fake_session_factory(
            RunResult("nu", "trial", "done", 1, 1, False, session_id="s", fired=True)),
        grade=grade, bind=_punt_all,
    )

    assert seen_configs == [custom]
```

Also update every other `grade=` fake in `tests/test_execution.py` whose signature currently declares `model="sonnet"`/`agent=None` (search for `def grade` and `agent=` in the file) to accept `judge_config=None` instead — their bodies are unaffected since none of them read `agent`/`model`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_execution.py -v`
Expected: FAIL — `run_eval_arm()` still has `judge_model` param, still calls `grade(..., agent=agent, model=judge_model, ...)`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/execution.py
# 1. Replace the import block's judge-model comment + constant (lines ~31-36):
from evalspec.judges import JudgeConfig
from evalspec.judges.config import DEFAULT_HARNESS, DEFAULT_MODEL  # noqa: F401 (documentation reference)
# JUDGE_MODEL constant removed — the default judge is now JudgeConfig() (harness=
# claude-code, model=sonnet), resolved via evalspec.judges.config's defaults.

# 2. _grade_via_judge: drop `agent`, rename `judge_model` -> `judge_config`.
def _grade_via_judge(*, assertions, tree, contents, shas, result_text, grade,
                     judge_config, eval_id, arm_name, pre_run_shas, process_facts=""):
    """Judge-grade assertions; return (graded, judge_ms, judge_errored)."""
    t0 = time.perf_counter()
    judge_errored = False
    try:
        graded = grade(
            assertions, tree, contents, shas, result_text, eval_id, arm_name,
            judge_config=judge_config, original_shas=pre_run_shas,
            process_facts=process_facts,
        )
    except RuntimeError as e:
        judge_errored = True
        graded = {"assertions": [
            {"text": a, "passed": False, "evidence": f"JUDGE INFRA ERROR: {e}"}
            for a in assertions
        ]}
    return graded, int((time.perf_counter() - t0) * 1000), judge_errored


# 3. _grade_mixed: drop `agent`, rename `judge_model` -> `judge_config`.
def _grade_mixed(*, assertions, tree, contents, shas, result_text, bind, grade, workdir,
                 grade_context, judge_config, eval_id, arm_name, pre_run_shas,
                 process_facts=""):
    """Grade each assertion on the host where a checker binds, else punt to the judge."""
    results: list = [None] * len(assertions)
    judge_idx: list[int] = []
    bind_cache: dict[str, dict | None] = {}
    for i, text in enumerate(assertions):
        if text not in bind_cache:
            try:
                bind_cache[text] = bind(text)
            except (RuntimeError, subprocess.TimeoutExpired):
                bind_cache[text] = None
        spec = bind_cache[text]
        if spec is not None:
            entry = checkers.run_assertion(spec, workdir, pre_run_shas, context=grade_context)
            entry["text"] = text
            results[i] = entry
        else:
            judge_idx.append(i)
    if not judge_idx:
        return results, 0, False

    texts = [assertions[i] for i in judge_idx]
    graded, judge_ms, judge_errored = _grade_via_judge(
        assertions=texts, tree=tree, contents=contents, shas=shas, result_text=result_text,
        grade=grade, judge_config=judge_config, eval_id=eval_id,
        arm_name=arm_name, pre_run_shas=pre_run_shas, process_facts=process_facts,
    )
    for i, entry in zip(judge_idx, graded["assertions"]):
        entry["type"] = "semantic"
        results[i] = entry
    return results, judge_ms, judge_errored


# 4. run_eval_arm's signature (drop `judge_model: str = JUDGE_MODEL`):
def run_eval_arm(
    eval_case: EvalCase,
    arm: Arm,
    workdir: Path,
    pre_run_shas: dict,
    project: Path | None,
    *,
    today: str,
    repo_root: Path,
    sample: int,
    eval_set: str = "",
    project_marker: str = DEFAULT_PROJECT_MARKER,
    judge_config: JudgeConfig | None = None,
    session_factory=arm_session,
    grade=grade_run,
    bind=binder.bind,
) -> ArmOutcome:
    judge_config = judge_config or JudgeConfig()
    eval_id = eval_case.eval_id
    arm_name = arm.name
    detect_skill = eval_case.skill

    agent = make_agent(arm.harness)
    snapshot = ensure_snapshot(agent, repo_root=repo_root)

    prompt = render_seed(eval_case.seed, today) + substitute_prompt(eval_case.prompt, today)
    graded_assertions = substitute_assertions(eval_case.assertions, today)

    arm_run = asyncio.run(
        _run_arm_turns(
            session_factory, agent=agent, snapshot=snapshot, eval_id=eval_id,
            arm_name=arm_name, workdir=workdir, project=project, model=arm.model,
            effort=arm.effort, prompt=prompt, project_marker=project_marker,
            skill=eval_case.skill, detect_skill=detect_skill,
            arm_env=expand_env(arm.env, os.environ), eval_set=eval_set,
            harness_args=arm.harness_args,
        )
    )

    fired_skills = tuple(skills_dispatched(arm_run.trajectory, eval_case.skill))
    grade_context = checkers.GradeContext(fired_skills=fired_skills)

    merged, judge_ms, judge_errored = _grade_mixed(
        assertions=graded_assertions,
        tree=arm_run.tree, contents=arm_run.contents, shas=arm_run.shas,
        result_text=arm_run.result_text,
        bind=bind, grade=grade, workdir=workdir, grade_context=grade_context,
        judge_config=judge_config, eval_id=eval_id, arm_name=arm_name,
        pre_run_shas=pre_run_shas,
        process_facts=render_process_facts([arm_run.trajectory]),
    )

    errored = arm_run.errored or judge_errored
    grading = {
        "eval_id": eval_id,
        "skill": eval_case.skill,
        "arm": arm_name,
        "sample": sample,
        "errored": errored,
        "assertions": merged,
    }
    run_dir = workspace.arm_dir(repo_root, eval_case.skill, eval_id, arm_name, sample=sample)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "timing.json").write_text(
        json.dumps({
            "duration_ms": arm_run.total_duration_ms,
            "judge_ms": judge_ms,
            "total_tokens": arm_run.total_tokens,
            "cache_read_tokens": arm_run.total_cache_read,
            "cache_creation_tokens": arm_run.total_cache_creation,
            "input_tokens": arm_run.total_input_tokens,
            "output_tokens": arm_run.total_output_tokens,
        }) + "\n"
    )
    (run_dir / "grading.json").write_text(json.dumps(grading, indent=2) + "\n")
    (run_dir / "transcript.json").write_text(json.dumps(arm_run.transcript, indent=2) + "\n")
    if arm_run.raw:
        block = arm_run.raw if arm_run.raw.endswith("\n") else arm_run.raw + "\n"
        (run_dir / "session.jsonl").write_text(
            json.dumps({TURN_DELIM: 1}) + "\n" + block
        )
    return ArmOutcome(
        grading, errored, arm_run.total_duration_ms, arm_run.total_tokens,
        fired=arm_run.transcript[0]["fired"] if arm_run.transcript else False,
    )
```

Drop the unused `DEFAULT_HARNESS`/`DEFAULT_MODEL` import from step 1 if `ruff` flags it as unused (it's referenced only as a documentation note) — prefer just deleting that `# noqa` import line entirely; it isn't required by any code below it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_execution.py -v`
Expected: PASS (all tests, including the two rewritten in Step 1)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/execution.py tests/test_execution.py
git commit -m "refactor(execution): thread JudgeConfig through grading instead of judge_model"
```

---

### Task 12: `plugin.py` — CLI flags, scratch-config refactor, `resolved_judge_config`, collection-time preflight

**Files:**
- Modify: `src/evalspec/plugin.py:42-158,182-242,450-467`
- Modify: `tests/test_plugin.py`

**Interfaces:**
- Consumes: `evalspec.judges.JudgeConfig`, `evalspec.judges.resolve_judge_config`.
- Produces: new CLI options `--evalspec-judge-harness`, `--evalspec-judge-effort`, `--evalspec-judge-timeout`, `--evalspec-judge-harness-arg` (repeatable), `--evalspec-judge-env` (repeatable); `--evalspec-judge-model` default changed from `"sonnet"` to `None`. `resolved_judge_config(config) -> JudgeConfig` (raises `pytest.UsageError`). `_read_scratch_evalspec_table(config_path: str | None) -> dict` (extracted, reused by `_layer_config_sets`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin.py — add near the existing test_judge_model_flag_is_accepted (it
# stays; the flag name is unchanged).

def test_judge_harness_flag_is_accepted(pytester):
    _make_project(pytester)
    result = _collect(pytester, "--evalspec-judge-harness", "codex",
                       "--evalspec-judge-model", "gpt-5.5")
    assert result.ret == 0


def test_judge_effort_and_timeout_flags_are_accepted(pytester):
    _make_project(pytester)
    result = _collect(pytester, "--evalspec-judge-effort", "high",
                       "--evalspec-judge-timeout", "120")
    assert result.ret == 0


def test_judge_harness_arg_flag_is_repeatable(pytester):
    _make_project(pytester)
    result = _collect(pytester, "--evalspec-judge-harness-arg", "--plugin-dir",
                       "--evalspec-judge-harness-arg", "/project")
    assert result.ret == 0


def test_judge_env_flag_is_repeatable(pytester):
    _make_project(pytester)
    result = _collect(pytester, "--evalspec-judge-env", "A=1",
                       "--evalspec-judge-env", "B=2")
    assert result.ret == 0


def test_judge_model_flag_no_longer_shadows_pyproject_default_when_unset(pytester):
    # Regression guard for the precedence bug: --evalspec-judge-model must default to
    # None so an unset flag never overrides [tool.evalspec.judge] model.
    project_toml = ARMS_TOML + """
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
"""
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)  # no --evalspec-judge-model passed
    assert result.ret == 0


def test_unsupported_judge_harness_fails_at_collection(pytester):
    project_toml = ARMS_TOML + """
[tool.evalspec.judge]
harness = "cursor"
"""
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "not a supported judge harness" in out


def test_incompatible_judge_model_fails_at_collection(pytester):
    project_toml = ARMS_TOML + """
[tool.evalspec.judge]
harness = "codex"
model = "sonnet"
"""
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "known-incompatible" in out


def test_non_dict_pyproject_judge_table_fails_loudly(pytester):
    # Regression guard: a present-but-non-dict [tool.evalspec.judge] (e.g. `judge =
    # "codex"` from a fat-fingered TOML edit) must raise, not silently coerce to
    # None and fall through to defaults.
    project_toml = ARMS_TOML + """
[tool.evalspec]
judge = "codex"
"""
    _make_project(pytester, arms_toml=project_toml)
    result = _collect(pytester)
    assert result.ret != 0
    out = result.stderr.str() + result.stdout.str()
    assert "[tool.evalspec.judge] must be a table" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plugin.py -k judge -v`
Expected: FAIL — new flags unrecognized (`pytest: error: unrecognized arguments`); collection succeeds for the two invalid-config tests instead of failing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/plugin.py
# 1. Add the import near the top:
from evalspec.judges import JudgeConfig, resolve_judge_config

# 2. Replace the existing --evalspec-judge-model addoption block (default="sonnet")
#    and add five siblings, inside pytest_addoption:
    group.addoption(
        "--evalspec-judge-harness",
        default=None,
        help="scalar override of the judge harness (default: claude-code, or "
             "[tool.evalspec.judge] harness). One of claude-code, codex, opencode.",
    )
    group.addoption(
        "--evalspec-judge-model",
        default=None,
        help="model for the LLM judge (default: sonnet, or [tool.evalspec.judge] "
             "model). Precedence: this flag > --evalspec-config > "
             "[tool.evalspec.judge] > built-in default. Recorded in meta.json under "
             "`judge.model`. Must default to None (not a hardcoded model) so an "
             "unset flag never shadows a project's [tool.evalspec.judge] model.",
    )
    group.addoption(
        "--evalspec-judge-effort",
        default=None,
        help="scalar override of the judge reasoning effort (default: medium, or "
             "[tool.evalspec.judge] effort).",
    )
    group.addoption(
        "--evalspec-judge-timeout",
        type=int,
        default=None,
        help="scalar override of the judge subprocess timeout in seconds (default: "
             "300, or [tool.evalspec.judge] timeout).",
    )
    group.addoption(
        "--evalspec-judge-harness-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="raw CLI token appended to the judge harness invocation (repeatable). "
             "When given at all, fully REPLACES [tool.evalspec.judge] harness_args "
             "(not merged/appended) — unlike set/arm harness_args, which append.",
    )
    group.addoption(
        "--evalspec-judge-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="add/override a judge env entry (repeatable); shallow-merges over "
             "[tool.evalspec.judge] env, CLI keys winning. A $VAR value expands "
             "from the host environment at judge EXECUTION time (never at "
             "collection); an unset referenced var raises.",
    )

# 3. Extract the scratch-file TOML read (used today only inside _layer_config_sets)
#    into a shared helper, and rewrite _layer_config_sets to use it:
def _read_scratch_evalspec_table(config_path: str | None) -> dict:
    """The raw `[tool.evalspec]` table from an untracked --evalspec-config scratch
    file, or {} if none was passed. Shared by `_layer_config_sets` (sets/default-set)
    and `resolved_judge_config` ([tool.evalspec.judge]) so there's one TOML read and
    one error-reporting path for a malformed scratch file."""
    if not config_path:
        return {}
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    try:
        with Path(config_path).open("rb") as f:
            raw = tomllib.load(f)
    except OSError as err:
        raise pytest.UsageError(f"--evalspec-config {config_path}: {err}") from None
    except UnicodeDecodeError as err:
        raise pytest.UsageError(f"--evalspec-config {config_path}: {err}") from None
    except tomllib.TOMLDecodeError as err:
        raise pytest.UsageError(f"--evalspec-config {config_path}: {err}") from None
    scratch = raw.get("tool", {}).get("evalspec")
    if not isinstance(scratch, dict):
        raise pytest.UsageError(
            f"--evalspec-config {config_path}: expected a [tool.evalspec] table "
            "with [tool.evalspec.sets.<name>] and/or [tool.evalspec.judge]"
        )
    return scratch


def _layer_config_sets(table: dict, config_path: str | None) -> dict:
    """Merge a scratch --evalspec-config file's [tool.evalspec.sets.*] over pyproject's."""
    scratch = _read_scratch_evalspec_table(config_path)
    if not scratch:
        return table
    merged = dict(table)
    merged["sets"] = {**table.get("sets", {}), **scratch.get("sets", {})}
    if scratch.get("default-set"):
        merged["default-set"] = scratch["default-set"]
    return merged


# 4. New: resolved_judge_config, mirroring resolved_run_set.
def _parse_judge_cli_table(config) -> dict:
    cli_table: dict = {}
    harness = config.getoption("evalspec_judge_harness")
    if harness is not None:
        cli_table["harness"] = harness
    model = config.getoption("evalspec_judge_model")
    if model is not None:
        cli_table["model"] = model
    effort = config.getoption("evalspec_judge_effort")
    if effort is not None:
        cli_table["effort"] = effort
    timeout = config.getoption("evalspec_judge_timeout")
    if timeout is not None:
        cli_table["timeout"] = timeout
    harness_args = config.getoption("evalspec_judge_harness_arg")
    if harness_args:
        cli_table["harness_args"] = harness_args
    env_pairs = config.getoption("evalspec_judge_env")
    if env_pairs:
        cli_table["env"] = _parse_env_pairs(env_pairs)
    return cli_table


def resolved_judge_config(config) -> JudgeConfig:
    """The single JudgeConfig this run uses. Layers `--evalspec-config`'s
    [tool.evalspec.judge] over pyproject's, applies CLI overrides, and preflights
    structurally (known harness, reserved harness_args, model/harness compatibility).
    Binary-on-PATH is NOT checked here — see cases.py's `_judge_preflight` fixture —
    so this stays safe to call under `--collect-only` and from every existing
    pytester-based collection test."""
    repo_root = resolve_repo_root(config)
    pyproject_judge = pyproject_table(repo_root).get("judge")
    scratch = _read_scratch_evalspec_table(config.getoption("evalspec_config"))
    scratch_judge = scratch.get("judge")
    # A present-but-non-dict `judge` key is a malformed config, not "no judge table" —
    # coercing it to None here would silently skip _validate_judge_table entirely and
    # fall through to defaults. Raise loudly instead, same as any other structural
    # judge-config defect (caught below and turned into pytest.UsageError).
    if pyproject_judge is not None and not isinstance(pyproject_judge, dict):
        raise pytest.UsageError(
            "[tool.evalspec.judge] must be a table, got "
            f"{type(pyproject_judge).__name__}"
        )
    if scratch_judge is not None and not isinstance(scratch_judge, dict):
        raise pytest.UsageError(
            f"--evalspec-config {config.getoption('evalspec_config')}: "
            f"[tool.evalspec.judge] must be a table, got {type(scratch_judge).__name__}"
        )
    try:
        return resolve_judge_config(
            pyproject_table=pyproject_judge,
            scratch_table=scratch_judge,
            cli_table=_parse_judge_cli_table(config),
        )
    except SchemaError as e:
        raise pytest.UsageError(str(e)) from None


# 5. Wire the preflight into pytest_generate_tests, right after arms are resolved:
def pytest_generate_tests(metafunc):
    fixtures = metafunc.fixturenames
    if "eval_arm" in fixtures:
        repo_root = resolve_repo_root(metafunc.config)
        eval_roots = resolve_eval_roots(metafunc.config)
        cases = discover_eval_cases(repo_root, eval_roots)
        arms = resolved_run_set(metafunc.config).arms if cases else []
        if cases:
            # Structural judge preflight — before ANY paid task arm runs. Raises
            # pytest.UsageError at collection on a bad config; binary-on-PATH is
            # checked separately, later, only when tests actually execute.
            resolved_judge_config(metafunc.config)
        pairs = []
        ids = []
        for case in cases:
            for arm in arms:
                pairs.append((case, arm))
                ids.append(f"{case.param_id}-{arm.name}")
        metafunc.parametrize("eval_arm", pairs, ids=ids)
    if "trigger_query" in fixtures:
        # ... unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plugin.py -k judge -v`
Expected: PASS (all new tests + the pre-existing `test_judge_model_flag_is_accepted`)

Run: `pytest tests/test_plugin.py -v` (full file)
Expected: PASS — confirms the `_layer_config_sets` refactor didn't regress existing scratch-config tests.

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/plugin.py tests/test_plugin.py
git commit -m "feat(plugin): add --evalspec-judge-* CLI flags and collection-time preflight"
```

---

### Task 13: `cases.py` — `judge_config` fixture + session-scoped binary preflight

**Files:**
- Modify: `src/evalspec/cases.py:47-49,95-121`

**Interfaces:**
- Consumes: `evalspec.plugin.resolved_judge_config`, `evalspec.judges.registry.preflight_judge_binary`.
- Produces: `judge_config` fixture (replaces `judge_model`); `_judge_preflight` session-scoped autouse fixture, gated to no-op unless the session collected at least one `judge_config`-dependent item (so trigger-only sessions never require a judge binary).

- [ ] **Step 1: Write the failing test**

`cases.py` is exercised indirectly through `tests/test_plugin.py`'s pytester runs (it's never imported directly by unit tests, per its own docstring: `make evals` loads it, `make test` does not). Add one pytester assertion to prove the fixture rename didn't break collection, and one to prove the session preflight raises without a judge binary on `PATH`:

```python
# tests/test_plugin.py (append near the judge-flag tests from Task 12)

def test_eval_test_body_resolves_judge_config_fixture_without_error(pytester):
    # A real (non --collect-only) run still needs a microVM to get past sandbox
    # preflight, so this only proves collection succeeds with the fixture renamed —
    # full execution is covered by tests/test_execution.py's run_eval_arm tests.
    _make_project(pytester)
    result = _collect(pytester)
    assert result.ret == 0


def test_judge_preflight_fixture_raises_when_binary_missing(pytester, monkeypatch):
    import shutil

    from evalspec import sandbox

    _make_project(pytester)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # No-op the sandbox preflight so it can't fail first for unrelated reasons (no
    # microVM/credentials) and mask the judge-binary assertion below. pytester.runpytest
    # runs in-process (not a subprocess), so this monkeypatch on evalspec.sandbox
    # reaches the collected test_cases.py's `_sandbox_preflight` fixture directly —
    # same pattern as test_plugin_self_registers_cases_without_positional's
    # `monkeypatch.setattr(plugin, "_CASES", ...)`.
    monkeypatch.setattr(sandbox, "preflight", lambda: None)
    result = pytester.runpytest(
        "-p", "evalspec.plugin",
        "--evalspec-repo-root", str(pytester.path),
        "test_cases.py::test_eval",
    )
    assert result.ret != 0
    out = result.stdout.str() + result.stderr.str()
    assert "not found on PATH" in out


def test_judge_preflight_does_not_fire_on_trigger_only_session(pytester, monkeypatch):
    # Regression guard: a trigger-only session (test_trigger, no eval_arm/judge_config
    # item collected) must not be forced to have a judge binary on PATH — trigger runs
    # never grade. No judge binary is "missing" here on purpose, so a failure can only
    # mean _judge_preflight fired when it shouldn't have.
    import shutil

    from evalspec import sandbox

    evals = pytester.path / "skills" / "myskill" / "evals"
    evals.mkdir(parents=True)
    (evals / "trigger-evals.md").write_text(
        "---\nskill_name: myskill\n---\n## Trigger\n\n- q1: do it\n\n## No Trigger\n\n- q2: nope\n"
    )
    pytester.makepyfile(test_cases=DUMMY_CASES)
    monkeypatch.setattr(shutil, "which", lambda name: None)  # no judge binary anywhere
    monkeypatch.setattr(sandbox, "preflight", lambda: None)
    result = pytester.runpytest(
        "-p", "evalspec.plugin",
        "--evalspec-repo-root", str(pytester.path),
        "test_cases.py::test_trigger",
    )
    out = result.stdout.str() + result.stderr.str()
    assert "not found on PATH" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plugin.py -k "judge_config_fixture or judge_preflight" -v`
Expected: `test_eval_test_body_resolves_judge_config_fixture_without_error` already passes (collection-only, unaffected). `test_judge_preflight_fixture_raises_when_binary_missing` FAILS with `ModuleNotFoundError`/`ImportError` (no `judges.registry.preflight_judge_binary`/`_judge_preflight` fixture yet) rather than the `"not found on PATH"` message. With the sandbox preflight neutralized, the assertion stays meaningful once Step 3 lands — do not relax it to `assert result.ret != 0`; that would stop distinguishing a judge-binary failure from any other infra failure. `test_judge_preflight_does_not_fire_on_trigger_only_session` also FAILS the same way (import error) — both pass together once Step 3's gated fixture lands.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/cases.py
# 1. Add the import:
from evalspec.judges import JudgeConfig
from evalspec.judges.registry import preflight_judge_binary
from evalspec.plugin import resolved_judge_config

# 2. Replace the judge_model fixture:
@pytest.fixture
def judge_config(request) -> JudgeConfig:
    return resolved_judge_config(request.config)


# 3. Add a session-scoped autouse preflight, alongside _sandbox_preflight:
@pytest.fixture(scope="session", autouse=True)
def _judge_preflight(request):
    # Binary-on-PATH is environment-dependent, unlike resolved_judge_config's
    # structural checks (already run at collection in pytest_generate_tests) — mirror
    # _sandbox_preflight's pattern so this never fires under --collect-only. Also gate
    # on whether the session actually collected any judge_config-dependent item
    # (test_eval, parametrized over eval_arm): a trigger-only session (test_trigger
    # only, no eval_set) collects zero such items and must not be forced to have a
    # judge binary installed — trigger runs never grade. By the time a session-scoped
    # fixture first executes, collection has already populated request.session.items,
    # so this is safe to check here (not at collection time).
    needs_judge = any(
        "judge_config" in item.fixturenames for item in request.session.items
    )
    if not needs_judge:
        return
    preflight_judge_binary(resolved_judge_config(request.config))


# 4. Update test_eval's signature and call site:
@pytest.mark.evalspec
def test_eval(eval_arm, seeded_workdir, repo_root, today,
              eval_set_name, project_marker, judge_config, sample_index):
    eval_case, arm = eval_arm
    workdir, pre_run_shas = seeded_workdir

    outcome = run_eval_arm(
        eval_case, arm, workdir, pre_run_shas, project=repo_root,
        today=today, repo_root=repo_root, sample=sample_index,
        eval_set=eval_set_name, project_marker=project_marker, judge_config=judge_config,
    )

    assert not outcome.errored, f"{arm.name} run errored — see transcript.json"
```

Note: `cases.py` importing `resolved_judge_config` from `evalspec.plugin` mirrors its existing imports of `make_agent`/`discover_eval_cases`/etc. from sibling modules — no new circularity (`plugin.py` already imports `cases.py`'s path as a string constant, `_CASES`, never as a Python import, so there is no cycle).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plugin.py -k "judge_config_fixture or judge_preflight" -v`
Expected: PASS (all three: the fixture-rename test, the missing-binary test, and the trigger-only-doesn't-need-a-judge test)

- [ ] **Step 5: Commit**

```bash
git add src/evalspec/cases.py tests/test_plugin.py
git commit -m "feat(cases): resolve judge_config fixture and preflight the judge binary"
```

---

### Task 14: `meta.json` — nested `judge` object + same-family warnings

**Files:**
- Modify: `src/evalspec/plugin.py:284-397`
- Modify: `tests/test_plugin.py`
- Modify: `docs/concepts.md`, `docs/quickstart.md` (glossary text only — full doc pass is Task 16, but these two `meta.json`-shape lines must not lie in the interim; update them here alongside the writer, per the Global Constraints' "writer AND all readers together" rule)

**Interfaces:**
- Consumes: `evalspec.judges.compat.same_family_warning`, `evalspec.report.redact_env` (existing). Deliberately does NOT consume `evalspec.judges.registry.probe_judge_version` — the pinned `judge{...}` meta shape below has no `version` field, and an unused import would fail `make lint` (ruff F401).
- Produces: `meta.json` gains nested `judge: {harness, model, effort, timeout, env, harness_args, warnings}`; the flat `judge_model` key is removed. `build_manifest`'s signature is unchanged (it already spreads `**cfg`); only the `cfg` dict `_write_manifest` assembles changes shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin.py — update the three tests that reference judge_model, and add
# one same-family-warning test.

# In _FakeConfig.getoption's dict, replace "evalspec_judge_model": "sonnet" with:
            "evalspec_judge_harness": None,
            "evalspec_judge_model": None,
            "evalspec_judge_effort": None,
            "evalspec_judge_timeout": None,
            "evalspec_judge_harness_arg": [],
            "evalspec_judge_env": [],


def test_build_manifest_assembles_shape_by_value():
    cfg = {
        "agent": "claude-code", "agent_version": "9.9.9", "model": "sonnet",
        "judge": {"harness": "claude-code", "model": "sonnet", "effort": "medium",
                  "timeout": 300, "env": {}, "harness_args": [], "warnings": []},
        "eval_effort": "medium", "trigger_effort": "low", "trigger_mode": "asymmetric",
    }

    manifest = plugin.build_manifest(
        run_id="r" * 32, started_at="2026-06-13T00:00:00+00:00", commit="abc123",
        iteration="iteration_07", cfg=cfg, token_split=True,
    )

    assert manifest["judge"]["harness"] == "claude-code"
    assert manifest["judge"]["model"] == "sonnet"
    assert manifest["judge"]["warnings"] == []
    assert len(manifest["config_hash"]) == 12


def test_build_manifest_config_hash_is_order_independent():
    cfg = {
        "agent": "claude-code", "agent_version": None, "model": "sonnet",
        "judge": {"harness": "claude-code", "model": "sonnet", "effort": "medium",
                  "timeout": 300, "env": {}, "harness_args": [], "warnings": []},
        "eval_effort": "medium", "trigger_effort": "low", "trigger_mode": "asymmetric",
    }
    reordered = dict(reversed(list(cfg.items())))

    a = plugin.build_manifest(run_id="a" * 32, started_at="t1", commit="c1",
                              iteration="iteration_01", cfg=cfg, token_split=True)
    b = plugin.build_manifest(run_id="b" * 32, started_at="t2", commit="c2",
                              iteration="iteration_99", cfg=reordered, token_split=False)

    assert a["config_hash"] == b["config_hash"]


def test_sessionfinish_writes_nested_judge_object(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text(ARMS_TOML)  # harness=claude-code, model=sonnet/opus arms
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)
    seed_arm(it, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    assert "judge_model" not in meta
    assert meta["judge"]["harness"] == "claude-code"
    assert meta["judge"]["model"] == "sonnet"
    assert meta["judge"]["effort"] == "medium"
    assert meta["judge"]["timeout"] == 300
    assert meta["judge"]["env"] == {}
    assert meta["judge"]["harness_args"] == []
    # ARMS_TOML's arms (baseline=sonnet-inherited, trial=opus) are BOTH anthropic
    # family, matching the default claude-code/sonnet judge — one warning expected.
    assert len(meta["judge"]["warnings"]) == 1
    assert "same-family" in meta["judge"]["warnings"][0]


def test_sessionfinish_no_same_family_warning_for_cross_family_arms(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    (tmp_path / "pyproject.toml").write_text("""\
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "codex"
model = "gpt-5.5"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial"}]
""")
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)
    seed_arm(it, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    # Default judge is claude-code/sonnet (anthropic); arms are codex/gpt-5.5 (openai).
    assert meta["judge"]["warnings"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plugin.py -k "manifest or sessionfinish_writes_nested or same_family" -v`
Expected: FAIL — `meta.json` still has flat `judge_model`; `_FakeConfig` missing new option keys raises `TypeError`/`KeyError` inside `resolved_judge_config`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evalspec/plugin.py
# 1. Add the import:
from evalspec.judges.compat import same_family_warning

# NOTE: do NOT import probe_judge_version here. This task's judge_meta shape (below)
# has no `version` field — see the Global Constraints' pinned `judge{...}` shape — so
# nothing in plugin.py calls it. An unused import would fail `make lint` (ruff F401).
# probe_judge_version stays available on evalspec.judges.registry for a future
# metadata revision; Task 8's tests remain its only current caller.

# 2. New helper, near build_manifest:
def _judge_meta(judge_config: JudgeConfig, run_set) -> dict:
    """The resolved judge, structurally shaped for meta.json. `warnings` carries at
    most one same-family entry (one per run, not per arm) — non-fatal, visible."""
    warnings: list[str] = []
    if run_set is not None:
        warning = same_family_warning(
            judge_harness=judge_config.harness, judge_model=judge_config.model,
            arms=run_set.arms,
        )
        if warning:
            warnings.append(warning)
    return {
        "harness": judge_config.harness,
        "model": judge_config.model,
        "effort": judge_config.effort,
        "timeout": judge_config.timeout,
        "env": report.redact_env(judge_config.env),
        "harness_args": judge_config.harness_args,
        "warnings": warnings,
    }


# 3. _write_manifest: accept judge_config, drop the flat judge_model cfg key.
def _write_manifest(
    config, iteration_root: Path, iteration: str, repo_root: Path, run_set: Set | None,
    judge_config: JudgeConfig,
) -> None:
    agent_version = token_split = None
    try:
        agent = make_agent()
        agent_version = agent.version()
        token_split = agent.capabilities.token_split
    except Exception:
        pass
    cfg = {
        "agent": os.environ.get("EVALSPEC_AGENT", "claude-code"),
        "agent_version": agent_version,
        "set": run_set.name if run_set else None,
        "arms": [{"name": a.name, "harness": a.harness, "model": a.model,
                  "effort": a.effort, "env": report.redact_env(a.env),
                  "harness_args": a.harness_args}
                 for a in run_set.arms] if run_set else [],
        "judge": _judge_meta(judge_config, run_set),
        "trigger_effort": config.getoption("evalspec_trigger_effort"),
        "trigger_mode": config.getoption("evalspec_trigger_mode"),
    }
    manifest = build_manifest(
        run_id=uuid.uuid4().hex,
        started_at=config.stash.get(_STARTED_AT, None),
        commit=_git_commit(repo_root),
        iteration=iteration,
        cfg=cfg,
        token_split=token_split,
    )
    (iteration_root / "meta.json").write_text(json.dumps(manifest, indent=2) + "\n")


# 4. pytest_sessionfinish: resolve judge_config, pass it through, surface warnings
#    in the terminal summary (visible but non-fatal — never touches exitstatus).
def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if hasattr(config, "workerinput"):
        return
    iteration = workspace.current_iteration_or_none()
    if iteration is None:
        return
    repo_root = resolve_repo_root(config)
    skills_root = workspace.skills_root(repo_root)
    if not skills_root.is_dir():
        return
    needs_set = any(
        d.is_dir() and d.name.startswith("eval-")
        for sd in skills_root.iterdir() if sd.is_dir()
        for d in sd.iterdir()
    )
    run_set = resolved_run_set(config) if needs_set else None
    judge_config = resolved_judge_config(config) if needs_set else JudgeConfig()
    _write_manifest(config, skills_root.parent, iteration, repo_root, run_set, judge_config)
    lines = []
    index_lines = []
    fail_under = config.getoption("evalspec_fail_under")
    baseline = run_set.baseline if run_set else None
    arm_meta = {a.name: {"harness": a.harness, "model": a.model, "effort": a.effort,
                         "env": report.redact_env(a.env),
                         "harness_args": a.harness_args}
                for a in run_set.arms} if run_set else None
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if not any(
            d.is_dir() and d.name.startswith(("eval-", "trigger-"))
            for d in skill_dir.iterdir()
        ):
            continue
        skill = skill_dir.name
        benchmark = report.write_benchmark(
            skill_dir, label=f"{iteration} · {skill}",
            baseline=baseline, arm_meta=arm_meta,
        )
        lines.append(report.delta_line(skill, benchmark, skill_dir / "benchmark.md"))
        if fail_under is not None and benchmark["baseline"] is not None:
            for arm_name, stats in benchmark["arms"].items():
                if arm_name == benchmark["baseline"]:
                    continue
                delta_pp = stats.get("delta_pp")
                if delta_pp is not None and delta_pp < fail_under:
                    lines.append(
                        f"FAIL fail-under: {skill}/{arm_name} delta {delta_pp:+.0f}pp "
                        f"< {fail_under:+.0f}pp"
                    )
                    if session.exitstatus == 0:
                        session.exitstatus = 1
        index_lines += [json.dumps(r) for r in report.index_rows(skill_dir, skill)]
    for warning in judge_config and _judge_meta(judge_config, run_set)["warnings"] or []:
        lines.append(f"WARN {warning}")
    if index_lines:
        (skills_root.parent / "index.jsonl").write_text("\n".join(index_lines) + "\n")
    config.stash[_SUMMARY_LINES] = lines
```

Simplify the duplicate `_judge_meta(...)` call in step 4 (it's computed once inside `_write_manifest` and recomputed for the terminal-summary warnings) — refactor to compute `judge_meta = _judge_meta(judge_config, run_set)` once, pass it into `_write_manifest` (change its signature to take `judge_meta: dict` instead of `judge_config: JudgeConfig`), and reuse `judge_meta["warnings"]` for the terminal lines. Apply this simplification before running the tests in Step 4 below — it removes redundant same-family computation without changing any assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plugin.py -v` (full file)
Expected: PASS — including every pre-existing manifest/sessionfinish test, now asserting the nested shape.

- [ ] **Step 5: Update the two `meta.json` glossary lines so they don't lie**

```markdown
<!-- docs/concepts.md line 19 — replace "the judge model" with: -->
… the eval `set` name + per-arm `arms` roster (each arm's harness/model/effort/env/harness_args), the resolved `judge` object (harness/model/effort/timeout/env/harness_args/warnings), `trigger_effort`, trigger mode, start time, `format_version`. …
```

```markdown
<!-- docs/concepts.md line 92 — same substitution -->
`meta.json` is the run manifest at the iteration root (one per run, above `skills/`): `run_id`/`commit`/`config_hash` identity plus agent + versions, the eval `set` name + per-arm `arms` roster (including resolved `harness_args`), the resolved `judge` object (harness/model/effort/timeout/env/harness_args/warnings — including any same-family bias warning), `trigger_effort`, trigger mode, start time, and `format_version`. …
```

```markdown
<!-- docs/quickstart.md line 141 — replace "task & judge models" with: -->
├── meta.json                       # run manifest — run_id/commit/config_hash identity, agent + versions, the resolved judge object (harness/model/effort/timeout/env/harness_args/warnings), efforts, trigger mode, start time, format_version; the join key for cross-run aggregation
```

- [ ] **Step 6: Commit**

```bash
git add src/evalspec/plugin.py tests/test_plugin.py docs/concepts.md docs/quickstart.md
git commit -m "feat(plugin): write nested judge object + same-family warnings to meta.json"
```

---

### Task 15: Verification fixtures + pytester tests

**Files:**
- Create: `tests/fixtures/judge/unsupported-judge-harness.toml`
- Create: `tests/fixtures/judge/incompatible-judge-model.toml`
- Create: `tests/fixtures/judge/unset-judge-env.toml`
- Create: `tests/fixtures/judge/same-family-judge.toml`
- Create: `tests/fixtures/judge/valid-cross-family-judge.toml`
- Modify: `tests/test_plugin.py`

**Interfaces:**
- Consumes: `evalspec.arms.expand_env` (already covered by `tests/test_arms.py` — reused here only as a citation, no new import).
- Produces: five reference `--evalspec-config` scratch fixtures matching the issue's verification scenarios, each exercised by a pytester test.

- [ ] **Step 1: Write the fixture files**

```toml
# tests/fixtures/judge/unsupported-judge-harness.toml
# `evalspec run --evalspec-config tests/fixtures/judge/unsupported-judge-harness.toml`
# (== `pytest -p evalspec.plugin --evalspec-config <this file>`) must exit nonzero:
# `cursor` is not a registered judge harness — fails at collection, before any paid
# task arm executes.
[tool.evalspec]

[tool.evalspec.judge]
harness = "cursor"
model = "gpt-5.5"
```

```toml
# tests/fixtures/judge/incompatible-judge-model.toml
# Must exit nonzero: codex+sonnet is a known-incompatible (harness, model-family)
# pair — fails at collection, before any paid task arm executes.
[tool.evalspec]

[tool.evalspec.judge]
harness = "codex"
model = "sonnet"
```

```toml
# tests/fixtures/judge/unset-judge-env.toml
# Must exit nonzero: $EVALSPEC_JUDGE_FIXTURE_UNSET_VAR is (by construction) never set
# in the environment this fixture is intended to be run in. Structurally valid at
# collection (env values are strings) — the SchemaError fires lazily, at judge
# EXECUTION time, via evalspec.arms.expand_env (the same function arm env uses),
# proving judge env expansion never silently degrades to an empty string.
[tool.evalspec]

[tool.evalspec.judge]
harness = "claude-code"
model = "sonnet"

[tool.evalspec.judge.env]
SOME_JUDGE_KEY = "$EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"
```

```toml
# tests/fixtures/judge/same-family-judge.toml
# Must emit exactly one same-family warning (non-fatal): the default eval set's arms
# are both claude-code/anthropic-family, matching the claude-code/sonnet judge.
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "claude-code"
model = "sonnet"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial", model="opus"}]

[tool.evalspec.judge]
harness = "claude-code"
model = "sonnet"
```

```toml
# tests/fixtures/judge/valid-cross-family-judge.toml
# A supported, cross-family judge configuration — proves a valid judge harness can
# grade a real run independently from the task arms, with zero same-family warnings.
[tool.evalspec]
default-set = "default"

[tool.evalspec.sets.default]
harness = "opencode"
model = "google/gemini-3.5-flash"
baseline = "baseline"
arms = [{name="baseline"}, {name="trial"}]

[tool.evalspec.judge]
harness = "claude-code"
model = "sonnet"
effort = "medium"
timeout = 300
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_plugin.py (append)
import shutil

_FIXTURES = Path(__file__).parent / "fixtures" / "judge"


def test_unsupported_judge_harness_fixture_exits_nonzero(pytester):
    _make_project(pytester)
    result = pytester.runpytest(
        "-p", "evalspec.plugin", "--collect-only", "-q",
        "--evalspec-repo-root", str(pytester.path),
        "--evalspec-config", str(_FIXTURES / "unsupported-judge-harness.toml"),
        "test_cases.py::test_eval",
    )
    assert result.ret != 0
    assert "not a supported judge harness" in (result.stdout.str() + result.stderr.str())


def test_incompatible_judge_model_fixture_exits_nonzero(pytester):
    _make_project(pytester)
    result = pytester.runpytest(
        "-p", "evalspec.plugin", "--collect-only", "-q",
        "--evalspec-repo-root", str(pytester.path),
        "--evalspec-config", str(_FIXTURES / "incompatible-judge-model.toml"),
        "test_cases.py::test_eval",
    )
    assert result.ret != 0
    assert "known-incompatible" in (result.stdout.str() + result.stderr.str())


def test_unset_judge_env_fixture_passes_collection_but_fails_at_judge_exec_time():
    # Collection-time only: proves the fixture's env value is structurally valid
    # (a string) and does NOT raise until evalspec.judges.run_judge actually expands
    # it. Full end-to-end (a real arm run reaching the judge) needs live credentials
    # and a microVM — out of scope for `make test`; see tests/judges/test_registry.py
    # ::test_run_judge_env_unset_var_raises_schemaerror for the unit-level proof, and
    # tests/test_arms.py for expand_env's own unset-var coverage (the same function).
    import tomllib
    from evalspec.judges import resolve_judge_config

    with (_FIXTURES / "unset-judge-env.toml").open("rb") as f:
        raw = tomllib.load(f)
    judge_table = raw["tool"]["evalspec"]["judge"]
    config = resolve_judge_config(pyproject_table=judge_table)  # no raise — structural only
    assert config.env == {"SOME_JUDGE_KEY": "$EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"}

    import os
    from evalspec.judges.registry import run_judge
    from evalspec.schema import SchemaError

    os.environ.pop("EVALSPEC_JUDGE_FIXTURE_UNSET_VAR", None)
    with pytest.raises(SchemaError, match="EVALSPEC_JUDGE_FIXTURE_UNSET_VAR"):
        run_judge("prompt", config=config)


def test_same_family_judge_fixture_emits_one_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    shutil.copy(_FIXTURES / "same-family-judge.toml", tmp_path / "pyproject.toml")
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)
    seed_arm(it, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    assert len(meta["judge"]["warnings"]) == 1


def test_valid_cross_family_judge_fixture_emits_no_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "make_agent", lambda: _StubAgent())
    shutil.copy(_FIXTURES / "valid-cross-family-judge.toml", tmp_path / "pyproject.toml")
    workspace.set_current_iteration("iteration_01")
    it = tmp_path / "tmp" / "evals" / "iteration_01" / "skills" / "archive"
    seed_arm(it, "alpha", "trial", passes=1, total=1)
    seed_arm(it, "alpha", "baseline", passes=0, total=1)

    _finish_and_summarize(tmp_path)

    meta = json.loads((it.parent.parent / "meta.json").read_text())
    assert meta["judge"]["harness"] == "claude-code"
    assert meta["judge"]["model"] == "sonnet"
    assert meta["judge"]["warnings"] == []
```

Add `from pathlib import Path` to `tests/test_plugin.py`'s imports if not already present (check the top of the file first).

- [ ] **Step 3: Run tests to verify they fail, then pass**

Run: `pytest tests/test_plugin.py -k "fixture" -v`
Expected: first FAIL (fixtures/tests don't exist yet), then PASS once both the fixture files (Step 1) and Tasks 12/14's implementation are in place. This task is verification-only (asserting on Task 12/14's existing behavior + new fixtures) — no `src/` changes are needed here; if any of these tests fail against the current `src/`, that is a signal a prior task's implementation step was incomplete, not that this task needs new production code.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/judge/ tests/test_plugin.py
git commit -m "test(judge): add verification fixtures for the five judge preflight/warning scenarios"
```

---

### Task 16: Documentation

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/agents.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/concepts.md`
- Modify: `docs/research/evalspec-readme-vision.md` (check only — see Step 5)

- [ ] **Step 1: `docs/configuration.md`** — add a `[tool.evalspec.judge]` section (mirroring the existing "Eval sets" section's table format) right after "Eval sets", update the CLI flags table's judge-model row, update the precedence section, and update the example block.

```markdown
<!-- Insert after the "Eval sets — [tool.evalspec.sets.<name>]" section, before "## CLI flags" -->

## The judge — `[tool.evalspec.judge]`

One run resolves exactly one **judge**: the host-side harness + model that grades every arm's assertions, independent from the task arms under test (grading a Codex or OpenCode run no longer requires installing Claude Code). Declare it under top-level `[tool.evalspec]`, never under a set or an arm — a fixed grader is what makes arm deltas comparable.

| Key | Type | Default | Notes |
|---|---|---|---|
| `harness` | string | `claude-code` | One of `claude-code`, `codex`, `opencode`. Unsupported values fail at collection, before any paid task arm runs. |
| `model` | string | `sonnet` | Harness-specific, like an arm's `model`. A known-incompatible pairing (e.g. `harness = "codex"` with a Claude-family model) fails at collection; remote availability/quota/account access is never preflighted. |
| `effort` | string | `medium` | Passed through to the judge harness's reasoning-effort flag where one exists (Codex has none — accepted for parity, unused). |
| `timeout` | integer | `300` | Judge subprocess timeout in seconds. Must be a positive integer. |
| `harness_args` | string[] | `[]` | Raw CLI tokens appended to the judge invocation. Reserved (evalspec-owned) flags are rejected per harness, same rule as an arm's `harness_args`. |
| `env` | table | `{}` | Judge-only env, injected at judge **execution** time (never at collection). `$VAR`/`${VAR}` values expand from the host environment via the same rule as arm `env` (`evalspec.arms.expand_env`) — an unset referenced var raises, literals pass through. |

```toml
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
effort = "medium"
timeout = 300
harness_args = ["--sandbox", "read-only"]
env = { CODEX_HOME = "$CODEX_HOME" }
```

A judge whose model family matches any arm's model family (Anthropic: `claude`/`sonnet`/`opus`/`haiku`/`anthropic/...`; OpenAI: `gpt-*`/`o<digit>...`/`openai/...`; Gemini: `gemini*`/`google/...`; harness family is only a fallback when the model family can't be determined) emits one non-fatal "same-family judge risk" warning in `meta.json["judge"]["warnings"]` and the terminal summary — visible, never blocking.
```

```markdown
<!-- Replace the existing --evalspec-judge-model row in the CLI flags table with: -->
| `--evalspec-judge-harness` | (none) | Scalar override of the judge `harness`. Precedence: this flag > `--evalspec-config` `[tool.evalspec.judge]` > project `[tool.evalspec.judge]` > built-in default (`claude-code`). |
| `--evalspec-judge-model` | (none, resolves to `sonnet`) | Scalar override of the judge `model`. **Must default to `None`, not `sonnet`** — a hardcoded flag default would always beat `[tool.evalspec.judge]`, breaking precedence. Recorded in `meta.json["judge"]["model"]`. |
| `--evalspec-judge-effort` | (none, resolves to `medium`) | Scalar override of the judge `effort`. |
| `--evalspec-judge-timeout` | (none, resolves to `300`) | Scalar override of the judge subprocess timeout in seconds. |
| `--evalspec-judge-harness-arg` | (none) | Repeatable. When given at all, **fully replaces** `[tool.evalspec.judge] harness_args` — unlike a set/arm's `harness_args`, which append, the judge's CLI override is a full swap per precedence layer. |
| `--evalspec-judge-env` | (none) | Repeatable `KEY=VAL`. Shallow-merges over `[tool.evalspec.judge] env` (CLI keys win), same merge rule across every layer (pyproject → scratch → CLI). |
```

```markdown
<!-- Update the "Precedence rules" section's flag-only callout — --evalspec-judge-model
     is no longer flag-only (it now has a `[tool.evalspec.judge]` channel too): -->
Not every knob has every channel. `--evalspec-fail-under` is **flag-only** — no env var, no `[tool.evalspec]` key; it's a CI decision, set by the CI command. The judge knobs (`--evalspec-judge-harness`/`-model`/`-effort`/`-timeout`/`-harness-arg`/`-env`) follow the full `CLI > scratch --evalspec-config > pyproject [tool.evalspec.judge] > built-in default` chain, unlike the flag-only knobs above.
```

- [ ] **Step 2: `docs/agents.md`** — replace the `judge(prompt, *, model, timeout)` protocol row and the two "Judge | Delegates to Claude" worked-example rows.

```markdown
<!-- Remove this row from the CodingAgent protocol member table entirely: -->
<!-- | `judge(prompt, *, model, timeout) -> str` | Host-side judge call ... | -->

<!-- Replace the paragraph after the member table ("Plus the class-level conveniences...") — add, right before "## What `invoke` returns": -->

## Grading is not an agent-protocol member

Judging is independent from the task agent — `CodingAgent` has no `judge` member. A run resolves one `JudgeConfig` (`evalspec.judges`, configured via `[tool.evalspec.judge]`; see [`configuration.md`](configuration.md)) naming a judge **harness** (`claude-code`, `codex`, or `opencode`) and model, launched as a fresh host process independent from every task arm's harness. `judge.py`'s `grade_run` calls `evalspec.judges.run_judge(prompt, config=judge_config)`, which dispatches to the selected harness's runner in `evalspec/judges/{claude_code,codex,opencode}.py`. Each runner returns the exact envelope `judge.py` parses — `{"result": "<judge-json-string>"}` — normalizing its own harness's output shape (Claude's `--output-format json` already emits it natively; Codex/OpenCode extract their final agent-message/text events and wrap it) and raises `RuntimeError` for its own infra-failure shapes (missing binary, nonzero exit, auth/quota/rate-limit/overload), exactly like the removed `CodingAgent.judge` contract did.

`binder.py`'s prose→checker classifier is a separate, untouched host-Claude call (`agents.judge_cli.run_host_judge`) — it always uses Claude regardless of the configured judge harness, and Phase 1 does not change that.
```

```markdown
<!-- ClaudeCodeAgent worked-example table: replace the last line
     ("Why the judge stays on the host: ...") — that rationale now lives in judges/
     transport, not the agent. Delete it from this table's surrounding prose;
     it's covered by the new "Grading is not an agent-protocol member" section. -->

<!-- OpenCodeAgent worked-example table: remove the row
     `| Judge | Delegates to Claude (host-side) — task arms differ; grading should not |` -->

<!-- CodexAgent worked-example table: remove the identical Judge row. -->
```

- [ ] **Step 3: `docs/quickstart.md`** — add a minimal cross-family judge example and the CLI override form, near the existing agent-switching section.

```markdown
<!-- Add a new subsection, after the existing agents.md pointer near line 177 -->

## A cross-family judge

By default the judge is `claude-code`/`sonnet` — same-family to a Claude task arm, which is fine for iterating but worth knowing about (a run emits a non-fatal same-family warning when judge and arm share a model family). To grade a Codex or OpenCode matrix with an independent judge:

```toml
[tool.evalspec.judge]
harness = "codex"
model = "gpt-5.5"
```

Or override per run without editing `pyproject.toml`:

```bash
pytest --evalspec-judge-harness codex --evalspec-judge-model gpt-5.5
```

See [`configuration.md`](configuration.md#the-judge--toolevalspecjudge) for the full precedence chain and every `--evalspec-judge-*` flag.
```

- [ ] **Step 4: `docs/concepts.md`** — the "Why the judge is on the host" section (lines 110-117) still says `agent.judge(prompt, model)` / `CodingAgent.judge`; replace it.

```markdown
<!-- Replace lines 110-117 -->
## Why the judge is on the host

The judge spawns a fresh host process for the configured judge harness (`evalspec.judges.run_judge`), not a sandboxed call. Two reasons:

1. **Grading needs the host's reasoning budget.** The judge does real reading and evidence-checking; a fresh microVM per arm doubles wall-clock and provider spend for no signal benefit.
2. **One consistent grader across harnesses.** Running the task under a different harness (`claude-code` vs `opencode`) leaves the rubric stable — only the *task* differs.

The judge harness is resolved once per run (`[tool.evalspec.judge]`, `--evalspec-judge-*` — see [`configuration.md`](configuration.md)), independent from every task arm's own harness — grading a Codex or OpenCode matrix no longer requires Claude Code installed. `binder.py`'s prose→checker classifier is a separate, unrelated host-Claude call, unaffected by the configured judge harness.
```

- [ ] **Step 5: `docs/research/evalspec-readme-vision.md`** — read the three `judge` mentions found during grounding (lines 69, 126, 129, 276) and confirm none of them assert the now-removed `CodingAgent.judge` protocol member, the flat `judge_model` field, or "OpenCode/Codex delegate to Claude" framing. Grep first:

```bash
grep -n "CodingAgent.judge\|judge_model\|delegates to Claude\|agent\.judge(" docs/research/evalspec-readme-vision.md
```

If the grep is empty (expected — this file only speaks in terms of "the judge model" generically, not the removed protocol member), no edit is needed; record that in the commit message. If it matches, update the matched line(s) to match the language used in Steps 1-4 above.

- [ ] **Step 6: Commit**

```bash
git add docs/configuration.md docs/agents.md docs/quickstart.md docs/concepts.md \
        docs/research/evalspec-readme-vision.md
git commit -m "docs: document the judge abstraction (config, CLI flags, protocol change)"
```

---

## Verification

Run in order:

- `make lint` — proves the implementation and docs satisfy project lint rules (in particular: no unused `import subprocess`/`shutil` left behind by Task 10's test deletions).
- `make test` — proves config resolution, judge runners, grading behavior, warnings, metadata, and plugin wiring pass the full suite (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester`, includes every `tests/judges/*` and `tests/test_plugin.py` fixture test from Task 15).
- `pytest tests/test_plugin.py -k judge -v` — proves config parsing, CLI precedence (including the `None`-default regression guard from Task 12), scratch config layering, warning output, and metadata behavior.
- `pytest tests/test_execution.py -k judge -v` — proves grading receives the resolved `JudgeConfig` and never calls the task arm as the judge (Task 11).
- `pytest tests/test_judge.py tests/agents/test_judge_cli.py tests/test_binder.py -v` — proves judge prompt/parsing behavior is unchanged (Task 9), the host-Claude transport `agents/judge_cli.py` still behaves exactly as before (untouched), and `binder.py`'s `run_host_judge` import identity is unchanged (Task 10's explicit proof).
- `pytest tests/judges/ -v` — proves every judge runner wraps successful judge text as `{"result": "<judge-json>"}` and raises `RuntimeError` for representative auth/rate-limit/missing-binary failures (Tasks 5-7), env expansion parity with `evalspec.arms.expand_env` (Task 8), and same-family/compatibility detection (Task 2).
- `pytest -p evalspec.plugin --evalspec-config tests/fixtures/judge/unsupported-judge-harness.toml --collect-only -q` (or the pytester-wrapped equivalent, `pytest tests/test_plugin.py -k unsupported_judge_harness_fixture -v`) exits nonzero — proves unsupported judge harnesses fail before paid task execution.
- `pytest -p evalspec.plugin --evalspec-config tests/fixtures/judge/incompatible-judge-model.toml --collect-only -q` exits nonzero — proves known-incompatible judge model/harness pairs fail before paid task execution.
- `pytest tests/test_plugin.py -k unset_judge_env_fixture -v` and `pytest tests/judges/test_registry.py -k env_unset -v` — prove judge env expansion is explicit and unset references raise rather than silently becoming empty strings (a full live `pytest -p evalspec.plugin --evalspec-config tests/fixtures/judge/unset-judge-env.toml` run additionally needs a working microsandbox + task-arm credentials to reach judge execution — run manually/in CI-with-secrets once a live judge is available; the automated suite covers the same `expand_env` codepath at the unit level).
- `pytest tests/test_plugin.py -k same_family_judge_fixture -v` emits one warning — proves same-family judge risk is visible but non-fatal.
- `pytest tests/test_plugin.py -k valid_cross_family_judge_fixture -v` emits zero warnings — proves a genuinely cross-family judge configuration is silent, and (combined with `tests/test_execution.py`'s fake-session-factory tests) that a supported host-side judge harness can grade independently from the task arms.

---

## Self-Review

**1. Spec coverage** — every issue section maps to a task:

| Spec section / requirement | Task(s) |
|---|---|
| Problem (agents delegate to `run_host_judge`; hardcoded `claude -p`; no harness/effort/args/env config; flat `judge_model`) | 1-16 (the whole plan) |
| Solution — `[tool.evalspec.judge]` table shape | 1, 3, 12, 16 |
| User story 1 (harness-independent judging) | 4-9 |
| User story 2 (one fixed judge config per run) | 1, 3, 12 |
| User story 3 (judge captured in metadata) | 14 |
| User story 4 (same-family warnings, non-blocking) | 2, 14, 15 |
| Run-level, not per-set/per-arm | 1, 3, 12 (declared only under top-level `[tool.evalspec]`) |
| Explicit precedence (CLI > scratch > pyproject > default) | 3, 12 |
| Full CLI overrides (6 flags, 2 repeatable) | 12 |
| Keep `--evalspec-judge-model` flag name | 12 |
| Separate prompt/parsing from transport | 9 |
| Exact judge output envelope contract | 5, 6, 7 |
| Host-side runners for claude-code/codex/opencode | 5, 6, 7 |
| Local judge preflight before paid task execution | 3 (structural), 4 + 13 (binary-on-PATH) |
| Fresh host processes, no task-agent/session reuse | 5, 6, 7 (each `run()` spawns its own `subprocess.run`) |
| Per-runner infra-error detection → `RuntimeError` | 5, 6, 7 |
| Conservative model/harness compatibility rejection | 2, 3 |
| Judge env expansion parity with arm env | 8 (direct reuse of `evalspec.arms.expand_env`) |
| Same-family detection, model-family-first | 2 |
| Structured `meta.json` metadata, flat `judge_model` removed | 14 |
| Best-effort version probing | 8 (implemented; deliberately not wired into the pinned meta shape — documented in Task 14 Step 3) |
| Cross-cutting protocol change (`CodingAgent.judge` removed) | 10 |
| Binder stays behaviorally unchanged | 10 (explicit identity-proof test), verified again in every later task's test run |
| Testing Plan — Logic (deterministic resolution, arm independence, same-family, env parity, early failure) | 1, 2, 3, 8, 12 |
| Testing Plan — Behavior (evidence-only grading, one envelope contract, infra vs. assertion failures, preserved retry/parse semantics, accurate metadata) | 5-9, 11, 14 |
| Testing Plan — Interface (full config surface, full CLI coverage, clean exits) | 3, 12, 15 |
| Documentation Plan (5 files) | 16 (plus Task 14 Step 5 for the two `meta.json` glossary lines, updated alongside the writer) |
| Out of Scope | Explicitly excluded from every task's Files/Interfaces — no task touches binder config, task-sandbox judging, Markdown/discovery/report format, Docker/microsandbox selection, Cursor/Copilot/Antigravity, or back-compat shims |
| Verification commands | Mapped to concrete `pytest`/`make` invocations in the Verification section, with the `evalspec run` → `pytest -p evalspec.plugin --evalspec-config` translation stated once in Global Constraints |

**2. Placeholder scan** — every code block in every task is complete, runnable Python/TOML/bash; no `TBD`, `...`, or "add error handling" prose. The one intentionally deferred item (`probe_judge_version` not wired into `meta.json`) is explicitly justified against the pinned Global-Constraints shape, not left dangling — searched for stray "TODO"/"TBD"/"similar to Task N" strings across the plan: none found.

**3. Type consistency** — traced across tasks: `JudgeConfig` (Task 1: `harness/model/effort/timeout/harness_args/env`) is the same shape used unchanged through `resolve_judge_config` (Task 3), `run_judge` (Task 8), `grade_run`'s `judge_config` param (Task 9), `run_eval_arm`'s `judge_config` param (Task 11), `resolved_judge_config`/`_judge_meta` (Tasks 12, 14), and the `judge_config` pytest fixture (Task 13) — no task renames a field or drops one along the way. Runner function signature `run(prompt, *, model, effort, timeout, harness_args, env)` is identical across `claude_code.py`/`codex.py`/`opencode.py` (Tasks 5-7) and matches exactly how `registry.run_judge` calls it (Task 8). `same_family_warning`'s `arms` parameter (Task 2) is satisfied by `evalspec.arms.Arm` objects with no adapter needed (`.name`/`.harness`/`.model` already match). `SchemaError` (from `evalspec.schema`) is the one exception type used for every structural/config-content failure across Tasks 1-3, 12; `RuntimeError` is the one type used for every environment/infra failure across Tasks 4-8, 10, consistent with the pre-existing `run_host_judge`/`grade_run` convention this plan extends rather than replaces.
