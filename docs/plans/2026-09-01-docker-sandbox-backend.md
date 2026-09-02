# Docker Sandbox Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `DockerBackend` as the second concrete `SandboxBackend` so `sandbox = "docker"` runs real eval cells in containers on hosts without Apple Silicon or KVM, plus the two seam repairs it forces (backend-neutral agent credentials, a backend-neutral infra error).

**Architecture:** A new stdlib-only `src/harnessbench/sandbox/docker.py` drives the `docker` CLI through `asyncio.create_subprocess_exec`/`subprocess.run` and exposes `DockerBackend`, `DockerSandbox` (the guest contract: `.shell`, `.exec`, `.exec_stream`, `.stop`), and `DockerVolume`. The backend-agnostic pieces both backends need — mount paths, VM sizing, the cache fingerprint, and the two shared provisioning steps — move into `src/harnessbench/sandbox/primitives.py` so `backend.py` can import `docker.py` without an import cycle. Two seams are repaired above the backend line: agents return a backend-neutral `GuestCredential` instead of constructing `microsandbox.Secret`, and agents catch a backend-neutral `SandboxRuntimeError` instead of importing `MicrosandboxError`.

**Tech Stack:** Python 3.11+, stdlib only for the new backend (`asyncio`, `subprocess`, `shutil`, `re`, `dataclasses`, `contextlib`), pytest 8, the `docker` CLI (Docker Engine / Docker Desktop). No new third-party dependencies — no docker-py, no aiodocker.

---

## Global Constraints

- **No new Python dependency.** Every Docker interaction is a `docker` CLI subprocess. `pyproject.toml` gains no entry under `[project.optional-dependencies]` or `[dependency-groups]`.
- **Lazy-import invariant, strengthened.** `backend.py`'s docstring already forbids importing `microsandbox` at module scope. `harnessbench/sandbox/docker.py` goes further: it imports **only** the standard library and `harnessbench.*`. Task 5 lands an AST-based test that enforces this permanently, so `lint`/`analyze` keep working on a host with nothing but Python.
- **Faking strategy for unit tests (read this before writing any test).** Every `docker` invocation in `sandbox/docker.py` funnels through exactly three module-level functions — `_docker_sync` (blocking), `_docker` (async, capture), `_docker_stream` (async, incremental). Unit tests fake Docker by `monkeypatch.setattr(docker_mod, "_docker", fake)` etc., exactly the way `tests/sandbox/test_sandbox.py:104` fakes microsandbox by `monkeypatch.setattr(backend_mod.MicrosandboxBackend, "create_trigger_sandbox", fake_create_trigger)` and `tests/agents/test_codex.py:52` fakes `monkeypatch.setattr(microsandbox, "Secret", DummySecret)`. **No unit test may require a Docker daemon.** A new call site that shells out to `docker` directly instead of through those three functions is a bug — the module docstring says so.
- **Daemon-gated behavior tests are separated and skipped, never failed.** Tests that genuinely need a live daemon live in exactly one file, `tests/sandbox/test_docker_daemon.py`, whose module-level `pytestmark = pytest.mark.skipif(...)` carries the actual preflight error text as the skip reason. `make test` on a Docker-less host reports them as skipped with a readable reason, never as failures or errors.
- **The backend-agnostic callers do not change.** `snapshot_name`, `preflight`, `ensure_snapshot`, `SandboxSession`, `arm_session`, `_route_in_sandbox_async`, `run_setup_sh`, and `_agent_extra_volumes` in `src/harnessbench/sandbox/sandbox.py` are untouched by this plan. If a task wants to edit one of them, the design is wrong — fix the backend instead.
- **The guest contract is four methods, not three.** The spec lists `.shell` / `.exec_stream` / `.stop`. The code also requires **`.exec`**: `GuestSandbox.exec` (`src/harnessbench/orchestration/environments.py:114`) calls `self._sandbox.exec(command[0], command[1:], cwd=cwd, env=env, timeout=timeout, stdin=stdin)` and every agent's `invoke` goes through it. `DockerSandbox` implements all four. Result objects expose `exit_code` / `stdout_text` / `stderr_text`.
- **Fingerprint bytes for microsandbox must not change.** Extracting `build_fingerprint_inputs` is a pure refactor: the payload stays `b"\0".join((backend_id, base_image_ref, install_fingerprint, env.script))` and the digest stays `sha256(...)[:8]`. Task 4 proves it with a test that recomputes the digest by hand. A changed microsandbox digest silently invalidates every cached snapshot on every developer machine.
- **`SandboxRuntimeError` subclasses `RuntimeError`**, matching the codebase's existing infra-failure convention (`ProcResult.require_success` at `orchestration/environments.py:44` raises `RuntimeError`). **Landmine:** `__main__._run_sandbox_build` catches `RuntimeError` → exit `2`. A Docker *build* failure must be exit `1`, so Task 13 adds an `except SandboxRuntimeError` branch **before** the `except RuntimeError` branch.
- **Ordering rule.** Every task leaves `make test` green. Tasks 1–4 are seam repairs with no Docker in them; Tasks 5–11 build `docker.py` while `"docker"` is still an unregistered name (so no config path can reach it); Task 12 flips the registration and every fail-fast test in one commit.
- **Style.** `docs/style/development.md` applies to every snippet: `from __future__ import annotations` first, absolute imports at the top, Google-style docstrings on *every* module/class/function including private helpers, fully descriptive names (no single letters), no `# noqa` / `# type: ignore`, comments explain *why* not *what*, and no comment references an issue or plan. Tests are DAMP with four-phase blank-line separation.
- **Commit hygiene.** Conventional commits. Every `git add` names specific files — never `git add .`.
- **Out of scope** (from the spec, do not drift): replicating `allow_hosts` scoping under Docker, remote `DOCKER_HOST`, Podman/rootless guarantees, Windows support claims, any change to microsandbox behavior or VM sizing, and a third backend.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/harnessbench/sandbox/errors.py` | **New.** `SandboxRuntimeError` (the backend-neutral infra failure) and `sandbox_error_types()` (the `except` tuple that also covers microsandbox's native error). Stdlib-only at import time. |
| `src/harnessbench/sandbox/primitives.py` | **New.** The backend-agnostic primitives both backends build on: `GUEST_WORKDIR`, `PROJECT_MOUNT`, `BASE_IMAGE`, `VM_CPUS`, `VM_MEMORY_MIB`, `host_mount_path`, `FingerprintInputs`, `build_fingerprint_inputs`, `bridge_skills_home`, `run_environment_script`. Extracted from `backend.py` so `backend.py` can import `docker.py` without a cycle. |
| `src/harnessbench/sandbox/docker.py` | **New.** `DockerBackend`, `DockerSandbox`, `DockerExecOutput`, `DockerExecStream`, `DockerStreamEvent`, `DockerVolume`, and the three `docker`-CLI seam functions `_docker_sync` / `_docker` / `_docker_stream`. Imports only stdlib + `harnessbench.*`. |
| `src/harnessbench/sandbox/backend.py` | Modified: shared pieces move to `primitives.py` and are re-exported via `__all__`; `MicrosandboxBackend` gains `_runtime_secrets` (neutral credential → `Secret.env`) and delegates `fingerprint_inputs`/provision steps to `primitives`; `_REGISTRY` gains `"docker"`; `_NOT_IMPLEMENTED` empties. |
| `src/harnessbench/agents/base.py` | Modified: adds the `GuestCredential` dataclass; `CodingAgent.secrets()` is retyped to `list[GuestCredential]`. |
| `src/harnessbench/agents/claude.py`, `codex.py`, `opencode.py` | Modified: `secrets()` returns `GuestCredential`s; `invoke` catches `sandbox_error_types()` instead of importing `MicrosandboxError`. Both `microsandbox` imports disappear from all three. |
| `src/harnessbench/__main__.py` | Modified: `_run_sandbox_build` maps `SandboxRuntimeError` to exit `1` before the `RuntimeError` → exit `2` branch. |
| `tests/sandbox/test_errors.py` | **New.** The neutral error type and the `except`-tuple builder. |
| `tests/sandbox/test_docker.py` | **New.** Every `DockerBackend`/`DockerSandbox`/`DockerVolume` unit test, with the `docker` CLI faked at the three seam functions. |
| `tests/sandbox/test_docker_daemon.py` | **New.** The daemon-gated behavior suite; whole module skips with an explicit reason when `docker info` fails. |
| `tests/sandbox/test_backend.py` | Modified: docker fail-fast test flips to a resolution test; new credential-mapping test; new fingerprint-refactor test; the microsandbox-import allowlist shrinks. |
| `tests/sandbox/test_sandbox.py` | Modified: `test_cli_build_docker_set_raises_schema_error` flips to proving `cli_build` drives the Docker backend. |
| `tests/config/test_arms.py` | Modified: the docker fail-fast tests (lines 551, 611) flip; `qemu` takes over as the unknown-name case. |
| `tests/test_main.py` | Modified: `test_sandbox_build_docker_set_exits_two` (line 174) flips; new exit-`1` test for a Docker build failure. |
| `tests/agents/test_codex.py`, `test_opencode.py` | Modified: `secrets()` assertions move from faked `microsandbox.Secret` to real `GuestCredential`s; new infra-error `invoke` tests. |
| `tests/agents/test_claude.py` | Modified: new `secrets()` and infra-error `invoke` tests. |
| `tests/fixtures/sandbox/two-backends.toml` | Modified: stops being a fail-fast fixture; becomes the accept-path fixture for a file holding one microsandbox set and one docker set. |
| `README.md`, `docs/sandbox.md`, `docs/configuration.md`, `docs/harnesses.md` | Modified: two supported backends, Docker host requirements, the shared-kernel and credential-visibility tradeoffs, image cache location. |

---

### Task 1: The backend-neutral infra error

**Files:**
- Create: `src/harnessbench/sandbox/errors.py`
- Test: `tests/sandbox/test_errors.py`

**Interfaces:**
- Produces: `SandboxRuntimeError` (subclass of `RuntimeError`), `sandbox_error_types() -> tuple[type[BaseException], ...]`.
- Consumes: nothing at import time. `microsandbox.errors.MicrosandboxError` is imported lazily inside the function body.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_errors.py
"""Tests for the backend-neutral sandbox-runtime error seam."""

from __future__ import annotations

import sys

from harnessbench.sandbox.errors import SandboxRuntimeError, sandbox_error_types


def test_sandbox_runtime_error_is_a_runtime_error() -> None:
    """The neutral infra error is a RuntimeError, matching the harness convention."""
    assert issubclass(SandboxRuntimeError, RuntimeError)


def test_sandbox_error_types_always_includes_the_neutral_error() -> None:
    """Whatever the host has installed, the neutral type is always catchable."""
    assert SandboxRuntimeError in sandbox_error_types()


def test_sandbox_error_types_includes_microsandbox_error_when_importable() -> None:
    """With the microsandbox package present, its native error joins the tuple.

    MicrosandboxError subclasses Exception directly, so no builtin covers it — an agent
    that dropped it from the tuple would crash the run instead of recording an errored arm.
    """
    from microsandbox.errors import MicrosandboxError

    assert MicrosandboxError in sandbox_error_types()


def test_sandbox_error_types_degrades_when_microsandbox_is_absent(monkeypatch: object) -> None:
    """On a host without microsandbox the tuple is just the neutral error, never a raise."""
    monkeypatch.setitem(sys.modules, "microsandbox.errors", None)

    assert sandbox_error_types() == (SandboxRuntimeError,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harnessbench.sandbox.errors'` at collection.

- [ ] **Step 3: Write minimal implementation**

```python
# src/harnessbench/sandbox/errors.py
"""The backend-neutral sandbox-runtime failure type.

An agent adapter must be able to classify "the sandbox itself broke" — the daemon went
away, the container was OOM-killed, an exec was torn down — as a recorded infra error
without naming a concrete runtime. `SandboxRuntimeError` is what harnessbench's own
backends raise for that, and `sandbox_error_types()` folds in each runtime's native
exception type so one `except` tuple covers every backend.

IMPORTANT: importing this module must NOT import the `microsandbox` package. The
`import microsandbox` stays inside the function body so `lint`/`analyze` keep working on
a host where the package is absent.
"""

from __future__ import annotations


class SandboxRuntimeError(RuntimeError):
    """A sandbox-runtime failure at the guest boundary, independent of the backend.

    Subclasses RuntimeError to match the harness's existing infra-failure convention
    (`ProcResult.require_success`): grading records the arm as errored and excludes it
    rather than laundering the failure into fake assertion misses.
    """


def sandbox_error_types() -> tuple[type[BaseException], ...]:
    """Return every exception type that means "the sandbox broke".

    Always includes `SandboxRuntimeError`. When the `microsandbox` package is importable
    it also includes that runtime's native `MicrosandboxError`, which subclasses
    `Exception` directly and is therefore covered by no builtin.

    Returns:
        A tuple of exception classes, ready for
        `except (TimeoutError, OSError, *sandbox_error_types())`.
    """
    try:
        from microsandbox.errors import MicrosandboxError
    except ImportError:
        return (SandboxRuntimeError,)
    return (SandboxRuntimeError, MicrosandboxError)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_errors.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/errors.py tests/sandbox/test_errors.py
git commit -m "feat(sandbox): add a backend-neutral sandbox runtime error"
```

---

### Task 2: Agents catch the neutral error instead of `MicrosandboxError`

**Files:**
- Modify: `src/harnessbench/agents/claude.py` (line 302 `from microsandbox.errors import MicrosandboxError`, line 323 the `except` tuple)
- Modify: `src/harnessbench/agents/codex.py` (line 330 the import, line 351 the `except` tuple)
- Modify: `src/harnessbench/agents/opencode.py` (line 399 the import, line 423 the `except` tuple)
- Test: `tests/agents/test_claude.py`, `tests/agents/test_codex.py`, `tests/agents/test_opencode.py` (append)

**Interfaces:**
- Consumes: `harnessbench.sandbox.errors.sandbox_error_types`, `SandboxRuntimeError`.
- Produces: no new symbols. Three `invoke` methods now classify any backend's infra failure as `RunResult(..., is_error=True)` with a `<sandbox-error> ` prefix.
- Import-cycle check: `harnessbench.sandbox.errors` imports nothing from `harnessbench`, and `harnessbench/sandbox/__init__.py` has no imports, so a top-level import from an agent module is safe.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_claude.py  (append at end of file)


def test_invoke_records_a_neutral_sandbox_error_as_an_errored_arm() -> None:
    """A backend-neutral runtime failure is recorded as an errored arm, never raised."""

    class ExplodingSandbox:
        """A guest whose exec fails the way a broken sandbox runtime does."""

        async def exec(self: object, *args: object, **kwargs: object) -> object:
            """Fail like a torn-down sandbox."""
            raise SandboxRuntimeError("container 1234 is not running")

    agent = ClaudeCodeAgent(auth_value="test-token")

    result = asyncio.run(
        agent.invoke(
            ExplodingSandbox(),
            "do the thing",
            eval_id="e1",
            config="alpha",
            workdir="/workspace",
            plugin_dir=None,
            model="sonnet",
            effort="medium",
            resume_session_id=None,
            detect_skill=None,
        )
    )

    assert result.is_error is True
    assert "container 1234 is not running" in result.result_text
    assert result.result_text.startswith("<sandbox-error> ")
```

Add to that file's imports (top of file, alphabetized into the existing groups):

```python
import asyncio

from harnessbench.sandbox.errors import SandboxRuntimeError
```

Write the same test, adjusted for each adapter, in `tests/agents/test_codex.py` (`test_invoke_records_a_neutral_sandbox_error_as_an_errored_arm`, `CodexAgent(auth_value="ck", auth_env="CODEX_API_KEY")`, and `invoke` additionally takes `workdir="/workspace"` exactly as above) and `tests/agents/test_opencode.py` (`OpenCodeAgent(auth_value="ok", auth_env="OPENROUTER_API_KEY")`, `model="anthropic/claude-sonnet-4-6"`).

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/agents/test_claude.py tests/agents/test_codex.py tests/agents/test_opencode.py -k neutral_sandbox_error -v`
Expected: FAIL — the `SandboxRuntimeError` propagates out of `invoke` (`E harnessbench.sandbox.errors.SandboxRuntimeError: container 1234 is not running`) for claude and opencode. Codex may already pass because its tuple happens to include `RuntimeError`; that is fine — implement anyway so the neutral type is explicit and `MicrosandboxError` is no longer imported there.

- [ ] **Step 3: Write minimal implementation**

In `src/harnessbench/agents/claude.py`, add to the top-of-file imports:

```python
from harnessbench.sandbox.errors import sandbox_error_types
```

Delete the line `from microsandbox.errors import MicrosandboxError` from `invoke` (line 302) and replace the try/except with:

```python
        # One tuple, every backend: the neutral error plus whatever native type the
        # resolved runtime raises.
        infra_errors = (TimeoutError, OSError, *sandbox_error_types())
        try:
            res = await GuestSandbox(sandbox).exec(
                cmd,
                cwd=workdir,
                # Per-arm extra_env (e.g. a leaky OpenRouter base URL) merges over
                # guest_env(), arm env winning.
                env={**self.guest_env(), **(extra_env or {})},
                timeout=timeout,
                stdin=b"",
            )
        except infra_errors as error:
            # A sandbox-boundary failure (runtime/exec/timeout) is an infra error for this
            # arm, not a graded miss — record it so the benchmark excludes it. A
            # programming error is not caught here: let it surface.
            message = f"<sandbox-error> {error}"[-2000:]
            return RunResult(eval_id, config, message, 0, 0, is_error=True)
```

In `src/harnessbench/agents/codex.py`: add the same top-level import, delete the `from microsandbox.errors import MicrosandboxError` line inside `invoke`, and change the tuple to

```python
        infra_errors = (TimeoutError, OSError, RuntimeError, *sandbox_error_types())
```

keeping `except infra_errors as error:` and the existing `RunResult(...)` body unchanged. (`RuntimeError` stays because `_write_auth_json` raises a bare one.)

In `src/harnessbench/agents/opencode.py`: add the same top-level import, delete the lazy `MicrosandboxError` import, and use

```python
        infra_errors = (TimeoutError, OSError, *sandbox_error_types())
```

with `except infra_errors as error:`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green (the three new tests included).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/agents/claude.py src/harnessbench/agents/codex.py src/harnessbench/agents/opencode.py tests/agents/test_claude.py tests/agents/test_codex.py tests/agents/test_opencode.py
git commit -m "refactor(agents): catch a backend-neutral sandbox error at the invoke boundary"
```

---

### Task 3: Backend-neutral agent credentials (`GuestCredential`)

**Files:**
- Modify: `src/harnessbench/agents/base.py` (add `GuestCredential`; retype `CodingAgent.secrets` at line 223)
- Modify: `src/harnessbench/agents/claude.py` (lines 167-177), `codex.py` (lines 185-197), `opencode.py` (lines 318-326)
- Modify: `src/harnessbench/sandbox/backend.py` (add `MicrosandboxBackend._runtime_secrets`; use it at lines 327 and 350)
- Test: `tests/agents/test_codex.py`, `tests/agents/test_opencode.py` (rewrite the `Secret`-faking tests), `tests/agents/test_claude.py` (new), `tests/sandbox/test_backend.py` (new mapping test; tighten the allowlist test at line 253)

**Interfaces:**
- Produces: `harnessbench.agents.base.GuestCredential(env_name: str, value: str, allow_hosts: tuple[str, ...])`; `MicrosandboxBackend._runtime_secrets(agent) -> list`.
- Consumes: `agent.secrets() -> list[GuestCredential]`.
- After this task, no file under `src/harnessbench/agents/` imports `microsandbox` at all.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_backend.py  (append)


def test_microsandbox_maps_guest_credentials_to_host_scoped_secrets() -> None:
    """One neutral credential renders as a microsandbox Secret with the same value and hosts."""
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="sk-test-value", auth_env="ANTHROPIC_API_KEY")

    secrets = microsandbox_backend._runtime_secrets(agent)

    assert len(secrets) == 1
    assert secrets[0].name == "ANTHROPIC_API_KEY"
    assert secrets[0].value == "sk-test-value"
    assert secrets[0].allow_hosts == ["api.anthropic.com"]
```

```python
# tests/agents/test_claude.py  (append)


def test_secrets_declares_a_host_scoped_guest_credential() -> None:
    """The adapter declares WHAT the credential is; the backend decides HOW it is injected."""
    agent = ClaudeCodeAgent(auth_value="sk-test-value", auth_env="ANTHROPIC_API_KEY")

    credentials = agent.secrets()

    assert credentials == [
        GuestCredential(
            env_name="ANTHROPIC_API_KEY",
            value="sk-test-value",
            allow_hosts=("api.anthropic.com",),
        )
    ]
```

(import `from harnessbench.agents.base import GuestCredential` at the top of that file).

Rewrite the existing `Secret`-faking tests in `tests/agents/test_opencode.py` (lines 467, 493, 517, 545) and `tests/agents/test_codex.py` (the `DummySecret` fixture at line 41 and its users at lines 208, 222, 245) to assert on `GuestCredential` directly. For example, the OpenCode Gemini remap test becomes:

```python
def test_secrets_gemini_remaps_to_sdk_env_name_and_scopes_to_google_host() -> None:
    """A host GEMINI_API_KEY is declared under the SDK's own guest variable name."""
    agent = OpenCodeAgent(auth_value="gk", auth_env="GEMINI_API_KEY")

    credentials = agent.secrets()

    assert credentials == [
        GuestCredential(
            env_name="GOOGLE_GENERATIVE_AI_API_KEY",
            value="gk",
            allow_hosts=("generativelanguage.googleapis.com",),
        )
    ]
```

and the Codex auth-json case keeps asserting `agent.secrets() == []` with the `DummySecret` fixture and its `monkeypatch.setattr(microsandbox, "Secret", ...)` deleted entirely (including the now-unused `import microsandbox`).

Finally, tighten the allowlist test in `tests/sandbox/test_backend.py:253`:

```python
def test_microsandbox_imported_only_under_allowlist() -> None:
    """No source file outside the allowlist imports the microsandbox package.

    The backend is the primary home for the concrete runtime; the CLI wrapper keeps its
    exit-code-split import and `sandbox/errors.py` keeps the one that builds the agents'
    `except` tuple. Everything else — notably every agent adapter, sandbox.py, and
    execution.py — must go through the resolved SandboxBackend or the neutral seams. This
    guards that boundary so a future edit cannot reintroduce a scattered
    `import microsandbox`.
    """
    import re
    from pathlib import Path

    allowlist = {
        "backend.py",
        "__main__.py",
        "errors.py",
    }
    src = Path("src/harnessbench")
    pattern = re.compile(r"^\s*(import microsandbox|from microsandbox)", re.MULTILINE)
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"microsandbox imported outside the allowlist: {offenders}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/agents tests/sandbox/test_backend.py -v`
Expected: FAIL — `ImportError: cannot import name 'GuestCredential' from 'harnessbench.agents.base'` at collection, and `AttributeError: 'MicrosandboxBackend' object has no attribute '_runtime_secrets'`.

- [ ] **Step 3: Write minimal implementation**

In `src/harnessbench/agents/base.py`, add after the `AgentCapabilities` dataclass:

```python
@dataclass(frozen=True)
class GuestCredential:
    """One provider credential an agent needs inside the guest, declared backend-neutrally.

    The adapter declares WHAT the credential is; the backend decides HOW it reaches the
    guest. `MicrosandboxBackend` renders it as a network-scoped `Secret.env`, so the value
    is substituted at the network boundary and never becomes a readable guest variable.
    `DockerBackend` renders it as a plain container environment variable, because Docker
    has no equivalent scoping primitive — the value IS readable in the guest there, which
    is part of what selecting `sandbox = "docker"` opts into.
    """

    env_name: str  # the variable name the agent's CLI reads INSIDE the guest
    value: str
    allow_hosts: tuple[str, ...]  # provider hosts the value may be used toward
```

Retype the protocol method (`src/harnessbench/agents/base.py:223`):

```python
    def secrets(self: object) -> list[GuestCredential]:
        """Return the provider credentials this agent needs inside the guest."""
        ...
```

`src/harnessbench/agents/claude.py` — replace the whole `secrets` method (lines 167-177):

```python
    def secrets(self: object) -> list[GuestCredential]:
        """Return the Anthropic credential, scoped to the Anthropic API host."""
        return [
            GuestCredential(
                env_name=self._auth_env,
                value=self._auth_value,
                allow_hosts=("api.anthropic.com",),
            )
        ]
```

with `GuestCredential` added to the existing `from harnessbench.agents.base import ...` line.

`src/harnessbench/agents/codex.py` — replace `secrets` (lines 185-197):

```python
    def secrets(self: object) -> list[GuestCredential]:
        """Return the Codex credential, or none when auth arrives as a mounted auth.json."""
        if self._auth_json_path:
            return []
        return [
            GuestCredential(
                env_name=self._auth_env,
                value=self._auth_value,
                allow_hosts=tuple(_PROVIDER_HOSTS[self._auth_env]),
            )
        ]
```

`src/harnessbench/agents/opencode.py` — replace `secrets` (lines 318-326):

```python
    def secrets(self: object) -> list[GuestCredential]:
        """Return the provider credential under the guest variable name the SDK reads."""
        allow_host = _PROVIDER_HOSTS[self._auth_env]
        guest_env_name = _GUEST_ENV_NAMES.get(self._auth_env, self._auth_env)
        return [
            GuestCredential(
                env_name=guest_env_name,
                value=self._auth_value,
                allow_hosts=(allow_host,),
            )
        ]
```

`src/harnessbench/sandbox/backend.py` — add to `MicrosandboxBackend` (just above `create_sandbox`):

```python
    def _runtime_secrets(self: object, agent: object) -> list:
        """Map the agent's backend-neutral credentials onto microsandbox scoped secrets."""
        from microsandbox import Secret

        return [
            Secret.env(
                credential.env_name,
                value=credential.value,
                allow_hosts=list(credential.allow_hosts),
            )
            for credential in agent.secrets()
        ]
```

and change both `secrets=agent.secrets(),` call sites (lines 327 and 350) to `secrets=self._runtime_secrets(agent),`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/agents/base.py src/harnessbench/agents/claude.py src/harnessbench/agents/codex.py src/harnessbench/agents/opencode.py src/harnessbench/sandbox/backend.py tests/agents/test_claude.py tests/agents/test_codex.py tests/agents/test_opencode.py tests/sandbox/test_backend.py
git commit -m "refactor(agents): declare credentials backend-neutrally as GuestCredential"
```

---

### Task 4: Extract the backend-agnostic primitives

**Files:**
- Create: `src/harnessbench/sandbox/primitives.py`
- Modify: `src/harnessbench/sandbox/backend.py` (remove lines 33-34, 37-54, 73-87, and the bodies of `fingerprint_inputs`/`_bridge_skills_home`/`_run_environment_script`; add re-exports)
- Test: `tests/sandbox/test_backend.py` (append the digest-stability proof)

**Interfaces:**
- Produces: `harnessbench.sandbox.primitives` exporting `GUEST_WORKDIR`, `PROJECT_MOUNT`, `BASE_IMAGE`, `VM_CPUS`, `VM_MEMORY_MIB`, `host_mount_path`, `FingerprintInputs`, `build_fingerprint_inputs(*, backend_id, agent, env)`, `bridge_skills_home(sandbox, agent)`, `run_environment_script(sandbox, agent, env)`.
- Consumes: `harnessbench.agents.CodingAgent`, `harnessbench.specs.discovery.EnvConfig`.
- **Why this module exists:** `backend.py` must import `docker.py` (to put `DockerBackend` in `_REGISTRY`) and `docker.py` needs these constants and helpers. A direct mutual import is a hard `ImportError`, and moving the `import` below the constants trips ruff `E402`, which the house rules forbid suppressing. Extracting the shared half breaks the cycle cleanly.
- Backward compatibility: `backend.py` re-exports every moved name via `__all__`, so `from harnessbench.sandbox.backend import BASE_IMAGE, host_mount_path` (used by `sandbox.py:25-32` and the tests) keeps working unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_backend.py  (append)


def test_microsandbox_fingerprint_payload_is_unchanged_by_the_extraction() -> None:
    """The microsandbox digest is recomputed by hand: a drift here invalidates every cache.

    The payload is NUL-joined backend id, declared base-image reference, agent install
    fingerprint, and the raw environment-script bytes, truncated to 8 hex characters.
    """
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    agent = _agent()
    env = EnvConfig(base_image="ubuntu:22.04", script=b"echo hi\n", script_path="s.sh")
    payload = b"\0".join(
        (
            b"microsandbox",
            b"ubuntu:22.04",
            agent.install_fingerprint().encode(),
            b"echo hi\n",
        )
    )

    digest = microsandbox_backend.cache_fingerprint(agent, env)

    assert digest == hashlib.sha256(payload).hexdigest()[:8]


def test_primitives_build_fingerprint_inputs_varies_only_with_the_backend_id() -> None:
    """Two backend ids over one agent+env produce different digests, same other ingredients."""
    agent = _agent()
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")

    micro = primitives.build_fingerprint_inputs(backend_id="microsandbox", agent=agent, env=env)
    docker = primitives.build_fingerprint_inputs(backend_id="docker", agent=agent, env=env)

    assert micro.digest != docker.digest
    assert micro.base_image_ref == docker.base_image_ref
    assert micro.install_fingerprint == docker.install_fingerprint
    assert micro.env_script_sha256 == docker.env_script_sha256
```

Add `from harnessbench.sandbox import primitives` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_backend.py -v`
Expected: FAIL — `ImportError: cannot import name 'primitives' from 'harnessbench.sandbox'` at collection.

- [ ] **Step 3: Write minimal implementation**

```python
# src/harnessbench/sandbox/primitives.py
"""The backend-agnostic primitives every `SandboxBackend` builds on.

Mount paths and guest layout, the shared VM/container sizing, the snapshot cache
fingerprint, and the two provisioning steps that run identically under any runtime. These
live below both concrete backends so `backend.py` can import a concrete backend module
without that module importing `backend.py` back.

Nothing here touches a concrete runtime: this module imports only the standard library
and harnessbench's own agent/spec types.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from harnessbench.agents import CodingAgent
from harnessbench.specs.discovery import EnvConfig

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"

BASE_IMAGE = "ubuntu:latest"
# Agent CLI installers and real eval work need this memory budget.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


def host_mount_path(path: object) -> str:
    """Return the host path to hand the runtime for a bind mount.

    The runtime binds the literal path it is given. On macOS the temp root — where the
    clean room and the staged project live — sits behind the `/var` → `/private/var`
    symlink, and a mount through that link fails with "Not a directory". Resolving
    first makes every bind site immune to that.
    """
    return str(Path(path).resolve())


@dataclass(frozen=True)
class FingerprintInputs:
    """The raw ingredients behind a snapshot cache fingerprint, plus the digest itself.

    `snapshot_name()` and provenance capture both read fingerprint data from one
    `fingerprint_inputs()` call rather than re-hashing or parsing the snapshot-name
    suffix — this is the single source both consumers share.
    """

    backend_id: str
    base_image_ref: str
    install_fingerprint: str
    env_script_sha256: str
    digest: str


def build_fingerprint_inputs(
    *, backend_id: str, agent: CodingAgent, env: EnvConfig
) -> FingerprintInputs:
    """Fold one backend's identity and an agent+environment into a cache fingerprint.

    Folds in: the backend id, the DECLARED base-image reference (the tag/ref as
    configured — harnessbench does not resolve it to a digest; a floating tag is therefore
    not reproducible across time/machines, and the backend records the actual pulled
    digest in run artifacts), the agent install fingerprint (installer inputs beyond
    `version()`), and the raw environment script bytes.

    Args:
        backend_id: The resolved backend's `id`, so two backends over one agent+env
            never collide on a snapshot name.
        agent: The agent whose installer inputs are folded in.
        env: The host environment config selecting the base image and script.

    Returns:
        The structured ingredients alongside the 8-character digest built from them.
    """
    base_image_ref = env.base_image or BASE_IMAGE
    install_fingerprint = agent.install_fingerprint()
    env_script_sha256 = hashlib.sha256(env.script).hexdigest()
    payload = b"\0".join(
        (
            backend_id.encode(),
            base_image_ref.encode(),
            install_fingerprint.encode(),
            env.script,
        )
    )
    digest = hashlib.sha256(payload).hexdigest()[:8]
    return FingerprintInputs(
        backend_id=backend_id,
        base_image_ref=base_image_ref,
        install_fingerprint=install_fingerprint,
        env_script_sha256=env_script_sha256,
        digest=digest,
    )


async def bridge_skills_home(sandbox: object, agent: object) -> None:
    """Link the agent's skill directory to the fixed, harness-neutral skills home.

    Args:
        sandbox: A live guest exposing `.shell(script, env=...)`.
        agent: The agent whose `bridge_skills_home_script()` is run.

    Raises:
        RuntimeError: If the bridge script exits nonzero, with the stderr tail.
    """
    result = await sandbox.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"skills-home bridge failed (exit {result.exit_code}): "
            f"{result.stderr_text[-2000:]}"
        )


async def run_environment_script(sandbox: object, agent: object, env: EnvConfig) -> None:
    """Run the host's environment script after provisioning, before sealing.

    Args:
        sandbox: A live guest exposing `.shell(script, env=...)`.
        agent: The agent whose `guest_env()` the script runs under.
        env: The host environment config; an empty script is a no-op.

    Raises:
        RuntimeError: If the script exits nonzero, with the stderr tail.
    """
    if not env.script:
        return
    script = b"set -e\n" + env.script
    result = await sandbox.shell(script.decode(), env=agent.guest_env())
    if result.exit_code != 0:
        raise RuntimeError(
            f"environment_script failed (exit {result.exit_code}): "
            f"{result.stderr_text[-2000:]}"
        )
```

In `src/harnessbench/sandbox/backend.py`: delete `GUEST_WORKDIR`/`PROJECT_MOUNT` (lines 33-34), `host_mount_path` (37-45), `BASE_IMAGE`/`VM_CPUS`/`VM_MEMORY_MIB` (48, 51-53), the `FingerprintInputs` dataclass (73-87), and the `_bridge_skills_home`/`_run_environment_script` methods (281-302). Delete the now-unused `import hashlib` and `from dataclasses import dataclass`. Add to the import block:

```python
from harnessbench.sandbox.primitives import (
    BASE_IMAGE,
    GUEST_WORKDIR,
    PROJECT_MOUNT,
    VM_CPUS,
    VM_MEMORY_MIB,
    FingerprintInputs,
    bridge_skills_home,
    build_fingerprint_inputs,
    host_mount_path,
    run_environment_script,
)
```

Add an `__all__` right below the constants so ruff counts the re-exports as used:

```python
__all__ = [
    "BASE_IMAGE",
    "DEFAULT_SANDBOX",
    "GUEST_WORKDIR",
    "PROJECT_MOUNT",
    "VM_CPUS",
    "VM_MEMORY_MIB",
    "FingerprintInputs",
    "MicrosandboxBackend",
    "SandboxBackend",
    "host_mount_path",
    "msb_binary",
    "resolve_sandbox",
]
```

Replace `MicrosandboxBackend.fingerprint_inputs`'s body with the delegation:

```python
    def fingerprint_inputs(self: object, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return build_fingerprint_inputs(backend_id=self.id, agent=agent, env=env)
```

and in `_build_snapshot_async` change `await self._bridge_skills_home(sandbox, agent)` to `await bridge_skills_home(sandbox, agent)` and `await self._run_environment_script(sandbox, agent, env)` to `await run_environment_script(sandbox, agent, env)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green — in particular `test_microsandbox_fingerprint_payload_is_unchanged_by_the_extraction`, `test_fingerprint_inputs_carries_structured_fields`, and `test_host_mount_path_resolves_symlinked_roots` (which still reaches `backend.host_mount_path` through the re-export).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/primitives.py src/harnessbench/sandbox/backend.py tests/sandbox/test_backend.py
git commit -m "refactor(sandbox): extract backend-agnostic primitives from the microsandbox backend"
```

---

### Task 5: The `docker` CLI seam, `DockerVolume`, and the stdlib-only invariant

**Files:**
- Create: `src/harnessbench/sandbox/docker.py`
- Test: `tests/sandbox/test_docker.py`

**Interfaces:**
- Produces: `DOCKER_BINARY`, `DockerResult`, `_docker_sync`, `_docker`, `_docker_stream`, `DockerVolume`, `container_name`, `image_ref`, `_env_flags`, `_volume_flags`, `_resource_flags`.
- Consumes: `harnessbench.sandbox.errors.SandboxRuntimeError`, `harnessbench.sandbox.primitives.{VM_CPUS, VM_MEMORY_MIB}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py
"""Unit tests for the Docker sandbox backend.

Every test here fakes the `docker` CLI at the three seam functions
(`_docker_sync`, `_docker`, `_docker_stream`). Nothing in this file needs a daemon;
the daemon-gated behavior suite lives in `test_docker_daemon.py`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from harnessbench.sandbox import docker as docker_mod
from harnessbench.sandbox.docker import DockerResult, DockerVolume


def test_docker_module_imports_only_stdlib_and_harnessbench() -> None:
    """Importing the Docker backend must not need any third-party package.

    The lazy-import invariant: `lint` and `analyze` run on a host with nothing but
    Python, so a docker client library must never appear here — not even lazily.
    """
    source = Path("src/harnessbench/sandbox/docker.py").read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    foreign = {root for root in roots if root != "harnessbench"} - sys.stdlib_module_names

    assert foreign == set(), f"non-stdlib imports in sandbox/docker.py: {sorted(foreign)}"


def test_volume_bind_renders_a_read_write_mount() -> None:
    """A read-write bind renders as `host:guest` with no suffix."""
    volume = DockerVolume.bind("/host/room")

    assert volume.flag_value("/workspace") == "/host/room:/workspace"


def test_volume_bind_renders_a_read_only_mount() -> None:
    """A read-only bind renders with Docker's `:ro` suffix."""
    volume = DockerVolume.bind("/host/stage", readonly=True)

    assert volume.flag_value("/project") == "/host/stage:/project:ro"


def test_volume_flags_render_every_mount_in_order() -> None:
    """Each guest path becomes its own `-v` pair, preserving insertion order."""
    volumes = {
        "/workspace": DockerVolume.bind("/host/room"),
        "/project": DockerVolume.bind("/host/stage", readonly=True),
    }

    assert docker_mod._volume_flags(volumes) == [
        "-v",
        "/host/room:/workspace",
        "-v",
        "/host/stage:/project:ro",
    ]


def test_env_flags_render_each_variable_as_a_docker_e_pair() -> None:
    """Guest environment variables become repeated `-e NAME=VALUE` arguments."""
    assert docker_mod._env_flags({"HOME": "/root", "TZ": "UTC"}) == [
        "-e",
        "HOME=/root",
        "-e",
        "TZ=UTC",
    ]


def test_env_flags_of_none_is_empty() -> None:
    """No environment means no `-e` arguments at all."""
    assert docker_mod._env_flags(None) == []


def test_resource_flags_come_from_the_shared_sizing_constants() -> None:
    """Docker resource limits reuse the same VM sizing every backend shares."""
    assert docker_mod._resource_flags() == ["--cpus", "2", "--memory", "2048m"]


def test_container_name_replaces_characters_docker_rejects() -> None:
    """Run names built from eval ids may hold characters Docker's name grammar rejects."""
    assert docker_mod.container_name("eval-greets/by name-alpha:1") == (
        "eval-greets-by-name-alpha-1"
    )


def test_container_name_prefixes_a_leading_non_alphanumeric() -> None:
    """Docker requires an alphanumeric first character; a prefix supplies one."""
    assert docker_mod.container_name("-trigger-main") == "hb--trigger-main"


def test_image_ref_lowercases_the_snapshot_name() -> None:
    """Docker repository names must be lowercase; the mapping is deterministic both ways."""
    assert docker_mod.image_ref("harnessbench-docker-claude-code-V1.2-ab12cd34") == (
        "harnessbench-docker-claude-code-v1.2-ab12cd34"
    )


def test_docker_sync_raises_a_neutral_error_when_the_binary_is_missing(
    monkeypatch: object,
) -> None:
    """A missing `docker` binary is a sandbox-runtime failure, not a bare OSError."""

    def missing_binary(*args: object, **kwargs: object) -> object:
        """Fail the way subprocess does when the binary is not on PATH."""
        raise FileNotFoundError("docker")

    monkeypatch.setattr(docker_mod.subprocess, "run", missing_binary)

    with pytest.raises(docker_mod.SandboxRuntimeError, match="docker CLI not found"):
        docker_mod._docker_sync("info")


def test_docker_sync_captures_both_streams_and_the_exit_code(monkeypatch: object) -> None:
    """A completed `docker` call is captured as a DockerResult carrying both streams."""

    class Completed:
        """Stand in for the CompletedProcess subprocess.run returns."""

        returncode = 7
        stdout = "out"
        stderr = "err"

    monkeypatch.setattr(docker_mod.subprocess, "run", lambda *a, **k: Completed())

    result = docker_mod._docker_sync("image", "inspect", "missing")

    assert result == DockerResult(("image", "inspect", "missing"), 7, "out", "err")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harnessbench.sandbox.docker'` at collection.

- [ ] **Step 3: Write minimal implementation**

```python
# src/harnessbench/sandbox/docker.py
"""The Docker implementation of the `SandboxBackend` seam.

Every Docker interaction is a `docker` CLI subprocess — no client library — so this module
imports nothing outside the standard library and `harnessbench` itself, and a host with a
Docker daemon needs no extra Python package. A snapshot is a `docker commit`-ed local
image; a cell is a container run from it.

A container shares the host kernel: it is a weaker boundary than a microVM, and Docker has
no equivalent of microsandbox's network-scoped secrets, so an agent's credential is a plain
readable environment variable inside the guest. Selecting `sandbox = "docker"` is the
user's explicit opt-in to both.

IMPORTANT: every `docker` invocation goes through `_docker_sync`, `_docker`, or
`_docker_stream`. Unit tests fake the CLI by monkeypatching exactly those three module
attributes, so a call site that shells out directly would silently require a live daemon.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass

from harnessbench.sandbox.errors import SandboxRuntimeError
from harnessbench.sandbox.primitives import VM_CPUS, VM_MEMORY_MIB

DOCKER_BINARY = "docker"
# Docker container names match [a-zA-Z0-9][a-zA-Z0-9_.-]*; everything else is replaced.
_ILLEGAL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
# A blocking `docker` call (preflight, image inspect) must not hang a collection-time check.
SYNC_CALL_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DockerResult:
    """The captured outcome of one completed `docker` CLI invocation."""

    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


def _docker_sync(*args: str, timeout: float = SYNC_CALL_TIMEOUT_SECONDS) -> DockerResult:
    """Run one blocking `docker` subcommand, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        timeout: Seconds to wait before treating the call as wedged.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the `docker` binary is absent or the call times out.
    """
    try:
        completed = subprocess.run(
            [DOCKER_BINARY, *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as error:
        raise SandboxRuntimeError("docker CLI not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise SandboxRuntimeError(
            f"`docker {' '.join(args)}` timed out after {timeout}s"
        ) from error
    return DockerResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


async def _docker(
    *args: str, stdin: bytes | None = None, timeout: float | None = None
) -> DockerResult:
    """Run one `docker` subcommand on the event loop, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin; None writes nothing and
            still closes it, so a guest command can never block on an open pipe.
        timeout: Seconds to wait for completion, or None to wait indefinitely.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned or exceeds `timeout`.
    """
    process = await _spawn(*args)
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin or b""), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise SandboxRuntimeError(
            f"`docker {' '.join(args)}` timed out after {timeout}s"
        ) from None
    return DockerResult(
        tuple(args),
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _docker_stream(*args: str, stdin: bytes | None = None) -> object:
    """Spawn one `docker` subcommand for incremental stdout reads.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin.

    Returns:
        The live `asyncio.subprocess.Process`; the caller drains `.stdout` and awaits
        `.wait()` for the exit code.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned.
    """
    process = await _spawn(*args)
    if process.stdin is not None:
        process.stdin.write(stdin or b"")
        process.stdin.close()
    return process


async def _spawn(*args: str) -> object:
    """Start a `docker` subprocess with all three streams piped.

    Raises:
        SandboxRuntimeError: If the binary is missing or the OS refuses the spawn.
    """
    try:
        return await asyncio.create_subprocess_exec(
            DOCKER_BINARY,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise SandboxRuntimeError(f"could not run `docker {' '.join(args)}`: {error}") from error


@dataclass(frozen=True)
class DockerVolume:
    """One host→guest bind mount, rendered as a `docker run -v` argument."""

    host_path: str
    readonly: bool

    @classmethod
    def bind(cls: type[DockerVolume], path: str, readonly: bool = False) -> DockerVolume:
        """Bind `path` from the host, read-only when asked.

        Mirrors `microsandbox.Volume.bind` so `sandbox._agent_extra_volumes` hands either
        class the identical call.
        """
        return cls(host_path=str(path), readonly=readonly)

    def flag_value(self: DockerVolume, guest_path: str) -> str:
        """Render the `-v` value that mounts this volume at `guest_path`."""
        suffix = ":ro" if self.readonly else ""
        return f"{self.host_path}:{guest_path}{suffix}"


def container_name(name: str) -> str:
    """Sanitize a harnessbench run name into a legal Docker container name.

    Docker accepts `[a-zA-Z0-9][a-zA-Z0-9_.-]*`, while harnessbench run names are built
    from eval ids and arm names that may carry other characters. Anything illegal becomes
    `-`, and a name that would start with an illegal character gains an `hb-` prefix.
    """
    sanitized = _ILLEGAL_NAME_CHARS.sub("-", name)
    return sanitized if sanitized[:1].isalnum() else f"hb-{sanitized}"


def image_ref(snapshot: str) -> str:
    """Return the local image reference a snapshot name maps to.

    Docker repository names must be lowercase, and a snapshot name folds in the harness's
    version string, so the reference is the lowercased snapshot name — deterministic in
    both directions, so `build_snapshot` and `snapshot_exists` always agree.
    """
    return snapshot.lower()


def _env_flags(env: dict | None) -> list[str]:
    """Render a guest environment mapping as repeated `docker exec -e` arguments."""
    return [flag for name, value in (env or {}).items() for flag in ("-e", f"{name}={value}")]


def _volume_flags(volumes: dict) -> list[str]:
    """Render a guest-path → volume mapping as repeated `docker run -v` arguments."""
    return [
        flag
        for guest_path, volume in volumes.items()
        for flag in ("-v", volume.flag_value(guest_path))
    ]


def _resource_flags() -> list[str]:
    """Render the shared sizing constants as `docker run` resource limits."""
    return ["--cpus", str(VM_CPUS), "--memory", f"{VM_MEMORY_MIB}m"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): add the docker CLI seam and bind-mount rendering"
```

---

### Task 6: `DockerBackend` preflight, `snapshot_exists`, `image_identity`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (append the `DockerBackend` class)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerBackend` with `id = "docker"`, `preflight()`, `snapshot_exists(name)`, `image_identity(snapshot)`.
- Consumes: `_docker_sync`, `image_ref`, `harnessbench.sandbox.provenance.ImageIdentity`, `shutil.which`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


def _fake_sync(monkeypatch: object, result: object, calls: list | None = None) -> None:
    """Point the blocking docker seam at a canned result, optionally recording arguments."""

    def fake(*args: str, timeout: float = 30.0) -> object:
        """Return the canned result for any blocking docker call."""
        if calls is not None:
            calls.append(args)
        return result

    monkeypatch.setattr(docker_mod, "_docker_sync", fake)


def test_preflight_reports_a_missing_cli_with_an_install_remedy(monkeypatch: object) -> None:
    """No `docker` on PATH yields one actionable error naming what to install."""
    monkeypatch.setattr(docker_mod.shutil, "which", lambda binary: None)
    backend = docker_mod.DockerBackend()

    errors = backend.preflight()

    assert len(errors) == 1
    assert "docker CLI not found on PATH" in errors[0]
    assert "Docker Engine" in errors[0]


def test_preflight_reports_an_unreachable_daemon(monkeypatch: object) -> None:
    """A `docker info` that exits nonzero is reported as an unreachable daemon."""
    monkeypatch.setattr(docker_mod.shutil, "which", lambda binary: "/usr/local/bin/docker")
    _fake_sync(
        monkeypatch,
        DockerResult(("info",), 1, "", "Cannot connect to the Docker daemon at unix:///..."),
    )
    backend = docker_mod.DockerBackend()

    errors = backend.preflight()

    assert len(errors) == 1
    assert "docker daemon unreachable" in errors[0]
    assert "Cannot connect to the Docker daemon" in errors[0]


def test_preflight_is_clean_when_the_daemon_answers(monkeypatch: object) -> None:
    """A CLI on PATH plus a zero-exit `docker info` means the host is ready."""
    monkeypatch.setattr(docker_mod.shutil, "which", lambda binary: "/usr/local/bin/docker")
    _fake_sync(monkeypatch, DockerResult(("info",), 0, "Server Version: 27.0.3", ""))
    backend = docker_mod.DockerBackend()

    assert backend.preflight() == []


def test_snapshot_exists_inspects_the_lowercased_image(monkeypatch: object) -> None:
    """An existing local image means the snapshot is present; the ref is lowercased."""
    calls: list = []
    _fake_sync(monkeypatch, DockerResult(("image", "inspect"), 0, "[]", ""), calls)
    backend = docker_mod.DockerBackend()

    present = backend.snapshot_exists("harnessbench-docker-claude-code-V1-ab12cd34")

    assert present is True
    assert calls == [
        ("image", "inspect", "harnessbench-docker-claude-code-v1-ab12cd34")
    ]


def test_snapshot_exists_false_when_the_image_is_absent(monkeypatch: object) -> None:
    """A nonzero `docker image inspect` means the snapshot has not been built."""
    _fake_sync(monkeypatch, DockerResult(("image", "inspect"), 1, "", "No such image"))
    backend = docker_mod.DockerBackend()

    assert backend.snapshot_exists("harnessbench-docker-claude-code-latest-ab12cd34") is False


def test_image_identity_available_from_the_local_image_id(monkeypatch: object) -> None:
    """A committed image has no registry digest, so its local Id is the recorded identity."""
    _fake_sync(monkeypatch, DockerResult(("image", "inspect"), 0, "sha256:abc123\n", ""))
    backend = docker_mod.DockerBackend()

    identity = backend.image_identity("harnessbench-docker-claude-code-latest-ab12cd34")

    assert identity.image_digest == "sha256:abc123"
    assert identity.image_digest_status == "available"
    assert identity.image_digest_error is None


def test_image_identity_unavailable_with_an_explanation_on_a_failed_inspect(
    monkeypatch: object,
) -> None:
    """A failed lookup degrades to an explained unavailable, never an exception."""
    _fake_sync(monkeypatch, DockerResult(("image", "inspect"), 1, "", "No such image: nope"))
    backend = docker_mod.DockerBackend()

    identity = backend.image_identity("nope")

    assert identity.image_digest is None
    assert identity.image_digest_status == "unavailable"
    assert "No such image" in identity.image_digest_error


def test_image_identity_unavailable_when_the_docker_cli_is_missing(monkeypatch: object) -> None:
    """A missing binary is an explained unavailable, so provenance capture never aborts."""

    def missing(*args: str, timeout: float = 30.0) -> object:
        """Fail the way the seam does with no docker binary present."""
        raise docker_mod.SandboxRuntimeError("docker CLI not found on PATH")

    monkeypatch.setattr(docker_mod, "_docker_sync", missing)
    backend = docker_mod.DockerBackend()

    identity = backend.image_identity("any-snapshot")

    assert identity.image_digest_status == "unavailable"
    assert "docker CLI not found" in identity.image_digest_error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: FAIL — `AttributeError: module 'harnessbench.sandbox.docker' has no attribute 'DockerBackend'` (and no attribute `shutil`).

- [ ] **Step 3: Write minimal implementation**

Add `import shutil` to the stdlib import block and `from harnessbench.sandbox.provenance import ImageIdentity` to the harnessbench block of `src/harnessbench/sandbox/docker.py`, then append:

```python
class DockerBackend:
    """The Docker implementation of `SandboxBackend`."""

    id = "docker"

    # A `docker commit`-ed image has no registry digest, so RepoDigests is empty and the
    # local image Id is the only stable identity there is; a pulled base image keeps its
    # registry digest, which is the stronger record when one exists.
    _IMAGE_DIGEST_FORMAT = "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}"

    def preflight(self: object) -> list[str]:
        """Return host-readiness errors: the CLI on PATH and a reachable daemon.

        The Docker-specific remedy lives here, not in the shared preflight, exactly as
        the microsandbox backend owns its Apple-Silicon/KVM remedy.
        """
        if shutil.which(DOCKER_BINARY) is None:
            return [
                "docker CLI not found on PATH — install Docker Engine or Docker Desktop"
            ]
        try:
            info = _docker_sync("info")
        except SandboxRuntimeError as error:
            return [f"docker daemon unreachable: {error}"]
        if info.exit_code != 0:
            return [
                f"docker daemon unreachable (`docker info` exited {info.exit_code}) — "
                f"start Docker and retry: {info.stderr.strip()[-500:]}"
            ]
        return []

    def snapshot_exists(self: object, name: str) -> bool:
        """Return whether the committed snapshot image is present in the local image store."""
        return _docker_sync("image", "inspect", image_ref(name)).exit_code == 0

    def image_identity(self: object, snapshot: str) -> ImageIdentity:
        """Return the snapshot image's registry digest when it has one, else its image Id.

        Any failure — the image missing, the daemon gone, no docker binary at all —
        becomes an explained `unavailable` result rather than propagating: a backend
        lookup failure must never abort provenance capture or artifact aggregation.
        """
        try:
            result = _docker_sync(
                "image", "inspect", image_ref(snapshot), "--format", self._IMAGE_DIGEST_FORMAT
            )
        except SandboxRuntimeError as error:
            return ImageIdentity.unavailable(str(error) or "docker image digest read failed")
        if result.exit_code != 0:
            detail = result.stderr.strip()[-500:] or "(no output)"
            return ImageIdentity.unavailable(
                f"docker image inspect exited {result.exit_code}: {detail}"
            )
        digest = result.stdout.strip()
        if not digest:
            return ImageIdentity.unavailable(
                f"docker reported no image digest for `{snapshot}`"
            )
        return ImageIdentity.available(digest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (20 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): add DockerBackend preflight, snapshot lookup, and image identity"
```

---

### Task 7: `DockerBackend` cache fingerprint

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `fingerprint_inputs`, `cache_fingerprint` to `DockerBackend`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerBackend.fingerprint_inputs(agent, env) -> FingerprintInputs`, `DockerBackend.cache_fingerprint(agent, env) -> str`.
- Consumes: `harnessbench.sandbox.primitives.build_fingerprint_inputs`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


def test_fingerprint_inputs_carry_the_docker_backend_id() -> None:
    """The Docker fingerprint folds the same four ingredients with backend_id="docker"."""
    backend = docker_mod.DockerBackend()
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    env = EnvConfig(base_image="ubuntu:22.04", script=b"echo hi\n", script_path="s.sh")

    inputs = backend.fingerprint_inputs(agent, env)

    assert inputs.backend_id == "docker"
    assert inputs.base_image_ref == "ubuntu:22.04"
    assert inputs.install_fingerprint == agent.install_fingerprint()
    assert inputs.digest == backend.cache_fingerprint(agent, env)


def test_docker_and_microsandbox_snapshots_of_one_agent_never_collide() -> None:
    """A Docker and a microsandbox snapshot of the same agent+env get different names."""
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")

    docker_name = snapshot_name(agent, env, backend=docker_mod.DockerBackend())
    micro_name = snapshot_name(agent, env, backend=backend_mod.MicrosandboxBackend())

    assert docker_name.startswith("harnessbench-docker-claude-code-1.2.3-")
    assert micro_name.startswith("harnessbench-microsandbox-claude-code-1.2.3-")
    assert docker_name != micro_name


def test_docker_fingerprint_changes_with_every_ingredient() -> None:
    """Base image, installer, and environment script each independently force a rebuild."""
    backend = docker_mod.DockerBackend()
    agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    other_agent = ClaudeCodeAgent(auth_value="test-token", version="1.2.3")
    other_agent.provision_script = lambda: "install a different revision"
    baseline = backend.cache_fingerprint(agent, EnvConfig(script=b"one\n", script_path="s.sh"))

    assert baseline != backend.cache_fingerprint(
        agent, EnvConfig(base_image="ubuntu:24.04", script=b"one\n", script_path="s.sh")
    )
    assert baseline != backend.cache_fingerprint(
        other_agent, EnvConfig(script=b"one\n", script_path="s.sh")
    )
    assert baseline != backend.cache_fingerprint(
        agent, EnvConfig(script=b"two\n", script_path="s.sh")
    )
```

Add to that file's imports:

```python
from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.sandbox import backend as backend_mod
from harnessbench.sandbox.sandbox import snapshot_name
from harnessbench.specs.discovery import EnvConfig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k fingerprint -v`
Expected: FAIL — `AttributeError: 'DockerBackend' object has no attribute 'fingerprint_inputs'`.

- [ ] **Step 3: Write minimal implementation**

Add `FingerprintInputs` and `build_fingerprint_inputs` to the `from harnessbench.sandbox.primitives import ...` line, and add to `DockerBackend` (right after `snapshot_exists`):

```python
    def fingerprint_inputs(
        self: object, agent: CodingAgent, env: EnvConfig
    ) -> FingerprintInputs:
        """Return the structured inputs and digest behind the snapshot cache fingerprint."""
        return build_fingerprint_inputs(backend_id=self.id, agent=agent, env=env)

    def cache_fingerprint(self: object, agent: CodingAgent, env: EnvConfig) -> str:
        """Return the snapshot cache fingerprint for this backend, agent, and env."""
        return self.fingerprint_inputs(agent, env).digest
```

Add the two type imports to the harnessbench import block:

```python
from harnessbench.agents import CodingAgent
from harnessbench.specs.discovery import EnvConfig
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (23 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): fold the docker backend id into the snapshot cache fingerprint"
```

---

### Task 8: `DockerSandbox.shell` and `.exec`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `DockerExecOutput`, `DockerSandbox`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerExecOutput(exit_code, stdout_text, stderr_text)`; `DockerSandbox(container)` with `.container`, `.shell(script, *, env=None, cwd=None)`, `.exec(command, args=None, *, cwd=None, env=None, timeout=None, stdin=None)`.
- Consumes: `_docker`, `_env_flags`.
- **Contract source:** `sandbox.run_setup_sh` calls `sandbox.shell(script, env=env, cwd=PROJECT_MOUNT)` (`sandbox/sandbox.py:197`); `GuestSandbox.exec` calls `self._sandbox.exec(command[0], command[1:], cwd=..., env=..., timeout=..., stdin=...)` (`orchestration/environments.py:114`). Both read `exit_code` / `stdout_text` / `stderr_text` off the result.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


def _record_docker(monkeypatch: object, result: object) -> list:
    """Point the async docker seam at a canned result and record every invocation."""
    calls: list = []

    async def fake(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record one async docker call and return the canned result."""
        calls.append({"args": args, "stdin": stdin, "timeout": timeout})
        return result

    monkeypatch.setattr(docker_mod, "_docker", fake)
    return calls


def test_shell_runs_the_script_under_sh_inside_the_container(monkeypatch: object) -> None:
    """`shell` becomes `docker exec -i -e ... -w ... <container> /bin/sh -c <script>`."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "done\n", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    result = asyncio.run(sandbox.shell("echo done", env={"HOME": "/root"}, cwd="/project"))

    assert calls[0]["args"] == (
        "exec", "-i", "-e", "HOME=/root", "-w", "/project",
        "eval-hello-alpha-main", "/bin/sh", "-c", "echo done",
    )
    assert (result.exit_code, result.stdout_text, result.stderr_text) == (0, "done\n", "")


def test_shell_omits_the_workdir_flag_when_no_cwd_is_given(monkeypatch: object) -> None:
    """No cwd means no `-w`, so the image's own working directory stands."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("build-claude-code")

    asyncio.run(sandbox.shell("apt-get update"))

    assert calls[0]["args"] == (
        "exec", "-i", "build-claude-code", "/bin/sh", "-c", "apt-get update",
    )


def test_exec_runs_the_command_without_a_shell(monkeypatch: object) -> None:
    """`exec` passes argv straight through, so no quoting rule can mangle a prompt."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "{}", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(
        sandbox.exec(
            "/root/.local/bin/claude",
            ["-p", "write a haiku"],
            cwd="/workspace",
            env={"TZ": "UTC"},
            timeout=600,
            stdin=b"",
        )
    )

    assert calls[0]["args"] == (
        "exec", "-i", "-e", "TZ=UTC", "-w", "/workspace",
        "eval-hello-alpha-main", "/root/.local/bin/claude", "-p", "write a haiku",
    )
    assert calls[0]["stdin"] == b""
    assert calls[0]["timeout"] == 600


def test_exec_result_fields_match_the_guest_contract(monkeypatch: object) -> None:
    """The result exposes exit_code/stdout_text/stderr_text, which GuestSandbox reads."""
    _record_docker(monkeypatch, DockerResult(("exec",), 3, "out", "err"))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    result = asyncio.run(sandbox.exec("false"))

    assert (result.exit_code, result.stdout_text, result.stderr_text) == (3, "out", "err")


def test_guest_sandbox_drives_a_docker_sandbox_unchanged(monkeypatch: object) -> None:
    """The agents' transport wrapper works against DockerSandbox with no adaptation."""
    _record_docker(monkeypatch, DockerResult(("exec",), 0, "hi", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    proc = asyncio.run(
        GuestSandbox(sandbox).exec(["echo", "hi"], env={}, timeout=5, cwd="/workspace")
    )

    assert (proc.exit_code, proc.stdout, proc.stderr) == (0, "hi", "")
```

Add `import asyncio` and `from harnessbench.orchestration.environments import GuestSandbox` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "shell or exec or guest_sandbox" -v`
Expected: FAIL — `AttributeError: module 'harnessbench.sandbox.docker' has no attribute 'DockerSandbox'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/harnessbench/sandbox/docker.py` (above `DockerBackend`):

```python
@dataclass(frozen=True)
class DockerExecOutput:
    """One completed guest command's captured outcome.

    Field names match the microsandbox exec result that `sandbox.py`, the agents, and
    `GuestSandbox` already read, so the guest contract is identical across backends.
    """

    exit_code: int
    stdout_text: str
    stderr_text: str


class DockerSandbox:
    """A live container satisfying the guest contract `sandbox.py` and the agents drive."""

    def __init__(self: object, container: str) -> None:
        """Wrap an already-running container by name."""
        self._container = container

    @property
    def container(self: object) -> str:
        """The name of the container this sandbox drives."""
        return self._container

    def _exec_args(self: object, *, cwd: str | None, env: dict | None) -> list[str]:
        """Build the leading `docker exec` arguments shared by every guest call.

        `-i` keeps stdin attached so the caller can force EOF on it; without that a
        guest CLI can block forever on an open pipe.
        """
        args = ["exec", "-i", *_env_flags(env)]
        if cwd:
            args += ["-w", cwd]
        return [*args, self._container]

    async def shell(
        self: object, script: str, *, env: dict | None = None, cwd: str | None = None
    ) -> DockerExecOutput:
        """Run `script` under `/bin/sh -c` inside the container."""
        result = await _docker(*self._exec_args(cwd=cwd, env=env), "/bin/sh", "-c", script)
        return DockerExecOutput(result.exit_code, result.stdout, result.stderr)

    async def exec(
        self: object,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecOutput:
        """Run `command` with `args` directly — no shell, so nothing re-quotes a prompt."""
        result = await _docker(
            *self._exec_args(cwd=cwd, env=env),
            command,
            *(args or []),
            stdin=stdin,
            timeout=timeout,
        )
        return DockerExecOutput(result.exit_code, result.stdout, result.stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (28 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): run guest shell and exec commands through docker exec"
```

---

### Task 9: `DockerSandbox.exec_stream`, `.kill()`, and `.stop()`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `DockerStreamEvent`, `DockerExecStream`; add `exec_stream`/`stop` to `DockerSandbox`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerStreamEvent(event_type, data, code)`, `DockerExecStream(process)` (async-iterable, `.kill()`), `DockerSandbox.exec_stream(...)`, `DockerSandbox.stop(timeout=None)`.
- Consumes: `_docker_stream`, `_docker`.
- **Contract source:** `sandbox._route_in_sandbox_async` (`sandbox/sandbox.py:417-448`) calls `sandbox.exec_stream(cmd[0], cmd[1:], cwd=..., env=..., stdin=b"")`, then `async for event in handle` reading `event.event_type` (`"stdout"` / `"exited"` / `"failed"`), `event.data` (bytes), `event.code`, and calls `await handle.kill()`. `SandboxSession.__aexit__` (`sandbox/sandbox.py:321`) calls `await self._sandbox.stop()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


class FakeStdout:
    """A stdout pipe that hands back queued chunks, then EOF."""

    def __init__(self: object, chunks: list) -> None:
        """Queue the chunks this pipe will yield before EOF."""
        self._chunks = list(chunks)

    async def read(self: object, limit: int) -> bytes:
        """Return the next queued chunk, or b"" once they are exhausted."""
        return self._chunks.pop(0) if self._chunks else b""


class FakeProcess:
    """A `docker exec` process double recording kills and returning a canned exit code."""

    def __init__(self: object, chunks: list, code: int = 0) -> None:
        """Wire the stdout chunks and the exit code this process reports."""
        self.stdout = FakeStdout(chunks)
        self.returncode = None
        self.killed = False
        self._code = code

    async def wait(self: object) -> int:
        """Report the process's exit code and mark it reaped."""
        self.returncode = self._code
        return self._code

    def kill(self: object) -> None:
        """Record that the client process was killed."""
        self.killed = True


def test_exec_stream_yields_stdout_chunks_then_a_terminal_exit_event() -> None:
    """The stream shape matches what trigger routing drains: stdout chunks, then `exited`."""
    stream = docker_mod.DockerExecStream(FakeProcess([b'{"a":1}\n', b'{"b":2}\n'], code=0))

    async def drain() -> list:
        """Collect every event the stream yields."""
        return [event async for event in stream]

    events = asyncio.run(drain())

    assert [event.event_type for event in events] == ["stdout", "stdout", "exited"]
    assert [event.data for event in events[:2]] == [b'{"a":1}\n', b'{"b":2}\n']
    assert events[-1].code == 0


def test_exec_stream_reports_a_nonzero_exit_code() -> None:
    """A crashed guest command surfaces its code, which routing turns into a RoutingError."""
    stream = docker_mod.DockerExecStream(FakeProcess([], code=127))

    async def drain() -> list:
        """Collect every event the stream yields."""
        return [event async for event in stream]

    events = asyncio.run(drain())

    assert [event.event_type for event in events] == ["exited"]
    assert events[0].code == 127


def test_exec_stream_kill_terminates_the_process_once() -> None:
    """Killing a stream is idempotent: a second kill on a reaped process is a no-op."""
    process = FakeProcess([b"chunk"], code=0)
    stream = docker_mod.DockerExecStream(process)

    asyncio.run(stream.kill())
    asyncio.run(stream.kill())

    assert process.killed is True


def test_exec_stream_spawns_docker_exec_with_stdin_closed(monkeypatch: object) -> None:
    """The streaming spawn carries the same exec arguments and forces EOF on stdin."""
    calls: list = []

    async def fake_stream(*args: str, stdin: object = None) -> object:
        """Record the streaming spawn and return a process double."""
        calls.append({"args": args, "stdin": stdin})
        return FakeProcess([], code=0)

    monkeypatch.setattr(docker_mod, "_docker_stream", fake_stream)
    sandbox = docker_mod.DockerSandbox("trigger-main")

    asyncio.run(
        sandbox.exec_stream(
            "/root/.local/bin/claude", ["-p", "route"], cwd="/root", env={"HOME": "/root"},
            stdin=b"",
        )
    )

    assert calls[0]["args"] == (
        "exec", "-i", "-e", "HOME=/root", "-w", "/root",
        "trigger-main", "/root/.local/bin/claude", "-p", "route",
    )
    assert calls[0]["stdin"] == b""


def test_stop_removes_the_container(monkeypatch: object) -> None:
    """Stopping a Docker session removes the container — the analogue of a VM stop."""
    calls = _record_docker(monkeypatch, DockerResult(("rm",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(sandbox.stop())

    assert calls[0]["args"] == ("rm", "-f", "eval-hello-alpha-main")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "stream or stop" -v`
Expected: FAIL — `AttributeError: module 'harnessbench.sandbox.docker' has no attribute 'DockerExecStream'`.

- [ ] **Step 3: Write minimal implementation**

Add above `DockerSandbox`:

```python
@dataclass(frozen=True)
class DockerStreamEvent:
    """One `exec_stream` event: a `stdout` chunk, or the terminal `exited` code.

    Mirrors the microsandbox event shape `sandbox._route_in_sandbox_async` drains, so
    trigger routing reads either backend's stream with the same code. Docker reports a
    failed launch through a nonzero exit code rather than a distinct `failed` event, and
    routing's own `exit_code not in (None, 0)` check already covers that.
    """

    event_type: str
    data: bytes | None = None
    code: int | None = None


class DockerExecStream:
    """An async iterator over a running `docker exec`'s stdout, ending with its exit code."""

    _CHUNK_BYTES = 65536

    def __init__(self: object, process: object) -> None:
        """Wrap the live `docker exec` process whose stdout is drained."""
        self._process = process
        self._finished = False

    def __aiter__(self: object) -> DockerExecStream:
        """Iterate this stream's own events."""
        return self

    async def __anext__(self: object) -> DockerStreamEvent:
        """Yield the next stdout chunk, then exactly one terminal `exited` event."""
        if self._finished:
            raise StopAsyncIteration
        chunk = b""
        if self._process.stdout is not None:
            chunk = await self._process.stdout.read(self._CHUNK_BYTES)
        if chunk:
            return DockerStreamEvent("stdout", data=chunk)
        self._finished = True
        return DockerStreamEvent("exited", code=await self._process.wait())

    async def kill(self: object) -> None:
        """Terminate the `docker exec` client process.

        WARNING: this kills the local client, not the process inside the container. The
        caller (`_route_in_sandbox_async`) removes the container immediately afterwards
        via `stop_quietly`, and that removal is what actually reaps the guest process.
        """
        if self._process.returncode is not None:
            return
        self._process.kill()
        await self._process.wait()
```

Append to `DockerSandbox`:

```python
    async def exec_stream(
        self: object,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        stdin: bytes | None = None,
    ) -> DockerExecStream:
        """Start `command` in the container and stream its stdout incrementally."""
        process = await _docker_stream(
            *self._exec_args(cwd=cwd, env=env), command, *(args or []), stdin=stdin
        )
        return DockerExecStream(process)

    async def stop(self: object, timeout: float | None = None) -> None:
        """Remove the container, ending the session.

        A container is disposable, so removal — not a stop — is the Docker analogue of a
        microVM stop. `timeout` is accepted for parity with the microsandbox signature
        and has no meaning here; `docker rm -f` returns as soon as the daemon reaps it.
        """
        await _docker("rm", "-f", self._container)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (33 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): stream docker exec output and tear containers down"
```

---

### Task 10: `DockerBackend.build_snapshot`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `build_snapshot`, `_build_snapshot_async` to `DockerBackend`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerBackend.build_snapshot(agent, name, env) -> None`.
- Consumes: `_docker`, `DockerSandbox`, `container_name`, `image_ref`, `_resource_flags`, `primitives.{BASE_IMAGE, bridge_skills_home, run_environment_script}`, `agent.provision`.
- Mirrors `MicrosandboxBackend._build_snapshot_async` (`sandbox/backend.py:260-279`) step for step: run base → provision → bridge → environment script → seal → always remove the build container.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


class RecordingAgent:
    """A CodingAgent stand-in that records the provisioning calls a build makes."""

    id = "claude-code"
    guest_home = "/root"
    skill_load_dir = "/root/.claude/skills"

    def __init__(self: object) -> None:
        """Start with an empty provisioning log."""
        self.provisioned: list = []

    def guest_env(self: object) -> dict:
        """Return the guest environment the build steps run under."""
        return {"HOME": "/root"}

    def bridge_skills_home_script(self: object) -> str:
        """Return the skills-home bridge script."""
        return "ln -s /home/harnessbench/skills /root/.claude/skills"

    async def provision(self: object, sandbox: object) -> None:
        """Record that the agent installed its CLI into the build container."""
        self.provisioned.append(sandbox.container)


def test_build_snapshot_runs_provision_bridge_script_then_commits(monkeypatch: object) -> None:
    """The Docker build mirrors the microsandbox build and seals with `docker commit`."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "sha256:committed\n", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    agent = RecordingAgent()
    env = EnvConfig(script=b"apt-get install -y jq\n", script_path="s.sh")

    docker_mod.DockerBackend().build_snapshot(
        agent, "harnessbench-docker-claude-code-latest-ab12cd34", env
    )

    assert calls[0] == ("rm", "-f", "harnessbench-build-claude-code")
    assert calls[1] == (
        "run", "--detach", "--name", "harnessbench-build-claude-code",
        "--cpus", "2", "--memory", "2048m",
        "--entrypoint", "sleep", "ubuntu:latest", "infinity",
    )
    assert agent.provisioned == ["harnessbench-build-claude-code"]
    assert calls[-2] == (
        "commit",
        "harnessbench-build-claude-code",
        "harnessbench-docker-claude-code-latest-ab12cd34",
    )
    assert calls[-1] == ("rm", "-f", "harnessbench-build-claude-code")


def test_build_snapshot_uses_the_declared_base_image(monkeypatch: object) -> None:
    """A declared base_image replaces the default in the build container's run."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)

    docker_mod.DockerBackend().build_snapshot(
        RecordingAgent(), "snap", EnvConfig(base_image="python:3.12-slim")
    )

    assert "python:3.12-slim" in calls[1]


def test_build_snapshot_raises_a_neutral_error_when_the_base_run_fails(
    monkeypatch: object,
) -> None:
    """A base image that cannot start is a loud sandbox-runtime failure, not a bad commit."""

    async def failing_run(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Succeed on cleanup, fail on the run."""
        if args[0] == "run":
            return DockerResult(args, 125, "", "Unable to find image 'nope:latest' locally")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", failing_run)

    with pytest.raises(docker_mod.SandboxRuntimeError, match="Unable to find image"):
        docker_mod.DockerBackend().build_snapshot(
            RecordingAgent(), "snap", EnvConfig(base_image="nope:latest")
        )


def test_build_snapshot_removes_the_build_container_when_a_step_raises(
    monkeypatch: object,
) -> None:
    """A failed provision still tears the build container down — no leaked containers."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)

    class ExplodingAgent(RecordingAgent):
        """An agent whose CLI install fails mid-build."""

        async def provision(self: object, sandbox: object) -> None:
            """Fail the way a broken installer does."""
            raise RuntimeError("claude-code provision failed (exit 1)")

    with pytest.raises(RuntimeError, match="provision failed"):
        docker_mod.DockerBackend().build_snapshot(ExplodingAgent(), "snap", EnvConfig())

    assert calls[-1] == ("rm", "-f", "harnessbench-build-claude-code")
    assert ("commit", "harnessbench-build-claude-code", "snap") not in calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k build_snapshot -v`
Expected: FAIL — `AttributeError: 'DockerBackend' object has no attribute 'build_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Add `BASE_IMAGE`, `bridge_skills_home`, and `run_environment_script` to the `from harnessbench.sandbox.primitives import ...` line, then append to `DockerBackend`:

```python
    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision a container from the base image and commit it as the snapshot image."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(
        self: object, agent: object, name: str, env: EnvConfig
    ) -> None:
        """Provision and seal the reusable snapshot image asynchronously.

        Raises:
            SandboxRuntimeError: If the base image cannot start or the commit fails.
        """
        base_image = env.base_image or BASE_IMAGE
        build_container = container_name(f"harnessbench-build-{agent.id}")
        # `replace=True` semantics: a build container left behind by an aborted run must
        # not collide with this one, so remove before run rather than after.
        await _docker("rm", "-f", build_container)
        started = await _docker(
            "run",
            "--detach",
            "--name",
            build_container,
            *_resource_flags(),
            # An explicit entrypoint keeps the container alive regardless of what the
            # base image declares, so provisioning has something to exec into.
            "--entrypoint",
            "sleep",
            base_image,
            "infinity",
        )
        if started.exit_code != 0:
            raise SandboxRuntimeError(
                f"docker run failed for base image `{base_image}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        sandbox = DockerSandbox(build_container)
        try:
            await agent.provision(sandbox)
            await bridge_skills_home(sandbox, agent)
            await run_environment_script(sandbox, agent, env)
            committed = await _docker("commit", build_container, image_ref(name))
            if committed.exit_code != 0:
                raise SandboxRuntimeError(
                    f"docker commit failed for `{name}` (exit {committed.exit_code}): "
                    f"{committed.stderr.strip()[-2000:]}"
                )
        finally:
            await _docker("rm", "-f", build_container)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (37 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): build docker snapshots by provisioning and committing a container"
```

---

### Task 11: `DockerBackend` session creation and the quiet helpers

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `create_sandbox`, `create_trigger_sandbox`, `_run_container`, `_credential_flags`, `guest_shell`, `stop_quietly`, `kill_quietly`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: the remaining `SandboxBackend` protocol methods on `DockerBackend`.
- Consumes: `sandbox._agent_extra_volumes` (passed in as `extra_volumes`), `agent.secrets()` → `GuestCredential`s, `primitives.{GUEST_WORKDIR, PROJECT_MOUNT, host_mount_path}`.
- After this task `isinstance(DockerBackend(), SandboxBackend)` is True — the protocol is `runtime_checkable`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


class CredentialAgent(RecordingAgent):
    """A RecordingAgent that also declares one backend-neutral credential."""

    def secrets(self: object) -> list:
        """Declare the Anthropic credential this agent needs in the guest."""
        return [
            GuestCredential(
                env_name="ANTHROPIC_API_KEY",
                value="sk-test-value",
                allow_hosts=("api.anthropic.com",),
            )
        ]

    async def stage_project_assets(self: object, sandbox: object, project_mount: str) -> None:
        """Record that project assets were staged into the trigger container."""
        self.provisioned.append(f"staged:{project_mount}")


def test_docker_backend_satisfies_the_sandbox_backend_protocol() -> None:
    """The whole protocol is implemented, so `resolve_sandbox` can hand it to any caller."""
    assert isinstance(docker_mod.DockerBackend(), backend_mod.SandboxBackend)


def test_create_sandbox_mounts_workspace_and_project_and_injects_credentials(
    monkeypatch: object, tmp_path: object,
) -> None:
    """An arm container gets a writable workspace, a read-only project, and its credential."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    workdir = tmp_path / "room" / "workdir"
    workdir.mkdir(parents=True)
    stage = tmp_path / "stage"
    stage.mkdir()

    sandbox = asyncio.run(
        docker_mod.DockerBackend().create_sandbox(
            agent=CredentialAgent(),
            snapshot="harnessbench-docker-claude-code-latest-ab12cd34",
            name="eval-hello-alpha-main",
            host_workdir=workdir,
            host_repo_root=stage,
            extra_volumes=lambda agent, volume_cls: {},
        )
    )

    run_args = calls[1]
    assert calls[0] == ("rm", "-f", "eval-hello-alpha-main")
    assert "-v" in run_args
    assert f"{workdir.resolve()}:/workspace" in run_args
    assert f"{stage.resolve()}:/project:ro" in run_args
    assert "ANTHROPIC_API_KEY=sk-test-value" in run_args
    assert run_args[-4:] == (
        "--entrypoint",
        "sleep",
        "harnessbench-docker-claude-code-latest-ab12cd34",
        "infinity",
    )
    assert sandbox.container == "eval-hello-alpha-main"


def test_create_sandbox_omits_the_project_mount_when_there_is_no_stage(
    monkeypatch: object, tmp_path: object,
) -> None:
    """With no staged project there is no `/project` mount at all."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)

    asyncio.run(
        docker_mod.DockerBackend().create_sandbox(
            agent=CredentialAgent(),
            snapshot="snap",
            name="eval-hello-alpha-main",
            host_workdir=tmp_path,
            host_repo_root=None,
            extra_volumes=lambda agent, volume_cls: {},
        )
    )

    assert not any(argument.endswith(":/project:ro") for argument in calls[1])


def test_create_sandbox_renders_agent_extra_volumes_through_docker_volume(
    monkeypatch: object, tmp_path: object,
) -> None:
    """The real `_agent_extra_volumes` hands DockerVolume the same call it hands microsandbox."""
    calls: list = []

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Record every docker call and report success."""
        calls.append(args)
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    auth_json = tmp_path / "auth.json"
    auth_json.write_text("{}", encoding="utf-8")
    agent = CredentialAgent()
    agent.auth_json_path = lambda: str(auth_json)

    asyncio.run(
        docker_mod.DockerBackend().create_sandbox(
            agent=agent,
            snapshot="snap",
            name="eval-hello-alpha-main",
            host_workdir=tmp_path,
            host_repo_root=None,
            extra_volumes=sandbox_mod._agent_extra_volumes,
        )
    )

    assert f"{auth_json.resolve()}:/harnessbench-codex-auth/auth.json:ro" in calls[1]


def test_create_trigger_sandbox_stages_project_assets(
    monkeypatch: object, tmp_path: object,
) -> None:
    """The routing container mounts the stage read-only and stages the agent's assets."""

    async def fake_docker(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Report success for every docker call."""
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    agent = CredentialAgent()

    asyncio.run(
        docker_mod.DockerBackend().create_trigger_sandbox(
            agent=agent,
            snapshot="snap",
            name="trigger-main",
            host_repo_root=tmp_path,
            extra_volumes=lambda agent_arg, volume_cls: {},
        )
    )

    assert agent.provisioned == ["staged:/project"]


def test_create_sandbox_raises_a_neutral_error_when_the_container_will_not_start(
    monkeypatch: object, tmp_path: object,
) -> None:
    """A container that cannot boot is a sandbox-runtime failure the arm records."""

    async def failing_run(*args: str, stdin: object = None, timeout: object = None) -> object:
        """Fail the run, succeed on cleanup."""
        if args[0] == "run":
            return DockerResult(args, 125, "", "No such image: snap")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", failing_run)

    with pytest.raises(docker_mod.SandboxRuntimeError, match="No such image"):
        asyncio.run(
            docker_mod.DockerBackend().create_sandbox(
                agent=CredentialAgent(),
                snapshot="snap",
                name="eval-hello-alpha-main",
                host_workdir=tmp_path,
                host_repo_root=None,
                extra_volumes=lambda agent, volume_cls: {},
            )
        )


def test_guest_shell_returns_stdout_on_success_and_none_on_failure(monkeypatch: object) -> None:
    """A zero exit yields stdout; a nonzero exit or a runtime failure yields None."""
    agent = CredentialAgent()
    backend = docker_mod.DockerBackend()

    _record_docker(monkeypatch, DockerResult(("exec",), 0, "1.2.3\n", ""))
    ok = asyncio.run(backend.guest_shell(docker_mod.DockerSandbox("c"), agent, "claude --version"))

    _record_docker(monkeypatch, DockerResult(("exec",), 1, "", "not found"))
    failed = asyncio.run(
        backend.guest_shell(docker_mod.DockerSandbox("c"), agent, "claude --version")
    )

    assert ok == "1.2.3\n"
    assert failed is None


def test_stop_quietly_swallows_a_runtime_failure() -> None:
    """Teardown never masks the real flow, even when the daemon has already gone."""

    class BrokenSandbox:
        """A sandbox whose teardown fails."""

        async def stop(self: object, timeout: object = None) -> None:
            """Fail the way a vanished daemon does."""
            raise docker_mod.SandboxRuntimeError("daemon gone")

    asyncio.run(docker_mod.DockerBackend().stop_quietly(BrokenSandbox()))


def test_kill_quietly_swallows_a_runtime_failure() -> None:
    """The routing timeout path must not raise out of its own cleanup."""

    class BrokenHandle:
        """A stream handle whose kill fails."""

        async def kill(self: object) -> None:
            """Fail the way an already-reaped process does."""
            raise OSError("no such process")

    asyncio.run(docker_mod.DockerBackend().kill_quietly(BrokenHandle()))
```

Add to the test file's imports:

```python
from harnessbench.agents.base import GuestCredential
from harnessbench.sandbox import sandbox as sandbox_mod
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "create_ or guest_shell or quietly or protocol" -v`
Expected: FAIL — `AttributeError: 'DockerBackend' object has no attribute 'create_sandbox'`.

- [ ] **Step 3: Write minimal implementation**

Add `import contextlib` to the stdlib imports and `GUEST_WORKDIR`, `PROJECT_MOUNT`, `host_mount_path` to the `primitives` import line. Add the module-level helper next to `_volume_flags`:

```python
def _credential_flags(agent: object) -> list[str]:
    """Render the agent's declared credentials as plain container environment variables.

    Docker has no equivalent of microsandbox's network-scoped secret substitution, so
    `allow_hosts` cannot be enforced and the value IS readable inside the guest. That
    tradeoff is the documented cost of selecting `sandbox = "docker"`.
    """
    return [
        flag
        for credential in agent.secrets()
        for flag in ("-e", f"{credential.env_name}={credential.value}")
    ]
```

Append to `DockerBackend`:

```python
    async def create_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_workdir: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> DockerSandbox:
        """Boot a container from the snapshot image for one arm session."""
        volumes = {
            GUEST_WORKDIR: DockerVolume.bind(host_mount_path(host_workdir), readonly=False)
        }
        if host_repo_root is not None:
            # The project mounts read-only so a per-eval setup.sh can install the
            # suite-specific skill without risking a write back into the host checkout.
            volumes[PROJECT_MOUNT] = DockerVolume.bind(
                host_mount_path(host_repo_root), readonly=True
            )
        volumes.update(extra_volumes(agent, DockerVolume))
        return await self._run_container(
            snapshot=snapshot, name=name, agent=agent, volumes=volumes
        )

    async def create_trigger_sandbox(
        self: object,
        *,
        agent: object,
        snapshot: object,
        name: object,
        host_repo_root: object,
        extra_volumes: object,
    ) -> DockerSandbox:
        """Boot the container used for trigger-routing probes."""
        volumes = {PROJECT_MOUNT: DockerVolume.bind(host_mount_path(host_repo_root), readonly=True)}
        volumes.update(extra_volumes(agent, DockerVolume))
        sandbox = await self._run_container(
            snapshot=snapshot, name=name, agent=agent, volumes=volumes
        )
        try:
            await agent.stage_project_assets(sandbox, PROJECT_MOUNT)
        except BaseException:
            await self.stop_quietly(sandbox)
            raise
        return sandbox

    async def _run_container(
        self: object, *, snapshot: object, name: object, agent: object, volumes: dict
    ) -> DockerSandbox:
        """Remove any same-named container, then run a fresh one from the snapshot image.

        Raises:
            SandboxRuntimeError: If the container fails to start.
        """
        container = container_name(str(name))
        # `replace=True` semantics: a name collision left by an earlier aborted cell must
        # not fail this one.
        await _docker("rm", "-f", container)
        started = await _docker(
            "run",
            "--detach",
            "--name",
            container,
            *_resource_flags(),
            *_volume_flags(volumes),
            *_credential_flags(agent),
            "--entrypoint",
            "sleep",
            image_ref(str(snapshot)),
            "infinity",
        )
        if started.exit_code != 0:
            raise SandboxRuntimeError(
                f"docker run failed for snapshot `{snapshot}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        return DockerSandbox(container)

    async def guest_shell(
        self: object, sandbox: object, agent: object, script: str
    ) -> str | None:
        """Run `script` in the guest, returning stdout on success or None on any failure."""
        try:
            result = await sandbox.shell(script, env=agent.guest_env())
        except (TimeoutError, SandboxRuntimeError, OSError):
            return None
        return result.stdout_text if result.exit_code == 0 else None

    async def stop_quietly(self: object, sandbox: object) -> None:
        """Best-effort container teardown that never masks the real flow."""
        with contextlib.suppress(SandboxRuntimeError, TimeoutError, OSError):
            await sandbox.stop()

    async def kill_quietly(self: object, handle: object) -> None:
        """Best-effort kill of a streaming exec handle (routing timeout path)."""
        with contextlib.suppress(SandboxRuntimeError, OSError):
            await handle.kill()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): create docker arm and trigger sessions with mounts and credentials"
```

---

### Task 12: Register `"docker"` and flip every fail-fast test

**Files:**
- Modify: `src/harnessbench/sandbox/backend.py` (module docstring lines 8-10, `_REGISTRY` line 364, `_NOT_IMPLEMENTED` line 367, `resolve_sandbox` docstring)
- Modify: `tests/sandbox/test_backend.py` (line 27), `tests/config/test_arms.py` (lines 550, 611), `tests/sandbox/test_sandbox.py` (line 516), `tests/test_main.py` (line 174)
- Modify: `tests/fixtures/sandbox/two-backends.toml`

**Interfaces:**
- Produces: `resolve_sandbox("docker")` returns a `DockerBackend`; `parse_sets` accepts `sandbox = "docker"`; `cli_build --set <docker-set>` drives the Docker backend.
- Consumes: `harnessbench.sandbox.docker.DockerBackend`.
- Import direction: `backend.py` → `docker.py` → `primitives.py`. `docker.py` never imports `backend.py`, so there is no cycle.

- [ ] **Step 1: Write the failing test**

Replace `tests/sandbox/test_backend.py:27-30` with:

```python
def test_resolve_sandbox_returns_docker_backend() -> None:
    """`docker` resolves to a backend whose id is `docker`."""
    resolved = backend.resolve_sandbox("docker")

    assert resolved.id == "docker"
    assert isinstance(resolved, docker_mod.DockerBackend)


def test_resolve_sandbox_unknown_lists_both_supported_backends() -> None:
    """An unknown name fails fast listing every implemented backend."""
    with pytest.raises(SchemaError, match=r"\['docker', 'microsandbox'\]"):
        backend.resolve_sandbox("qemu")
```

(delete the old `test_resolve_sandbox_unknown_fails` it supersedes, and add `from harnessbench.sandbox import docker as docker_mod` to the imports).

Replace `tests/config/test_arms.py:550-566` with:

```python
def test_parse_sets_accepts_the_docker_sandbox() -> None:
    """`sandbox = "docker"` is a valid set-level value now that the backend exists."""
    rawsets, default = parse_sets(
        _sets_table(
            {
                "default": {
                    "model": "sonnet",
                    "sandbox": "docker",
                    "arms": [{"name": "alpha", "harness": "claude-code"}],
                }
            }
        )
    )

    assert resolve_set(rawsets, default).sandbox == "docker"


def test_parse_sets_unknown_sandbox_fails_naming_set() -> None:
    """An unknown sandbox fails fast naming the offending set and the supported values."""
    with pytest.raises(
        SchemaError, match=r"tool\.harnessbench\.sets\.default.*unsupported sandbox `qemu`"
    ):
        parse_sets(
            _sets_table(
                {
                    "default": {
                        "model": "sonnet",
                        "sandbox": "qemu",
                        "arms": [{"name": "alpha", "harness": "claude-code"}],
                    }
                }
            )
        )
```

Replace `tests/config/test_arms.py:609-617` with:

```python
def test_two_backends_fixture_parses_both_sets() -> None:
    """The two-backend fixture parses: one microsandbox set and one docker set, side by side."""
    raw = tomllib.loads(
        Path("tests/fixtures/sandbox/two-backends.toml").read_text(encoding="utf-8")
    )

    rawsets, default = parse_sets(raw["tool"]["harnessbench"])

    assert resolve_set(rawsets, default, set_name="micro").sandbox == "microsandbox"
    assert resolve_set(rawsets, default, set_name="dock").sandbox == "docker"
```

Replace `tests/sandbox/test_sandbox.py:516-538` with:

```python
def test_cli_build_docker_set_drives_the_docker_backend(
    monkeypatch: object, tmp_path: object, capsys: object
) -> None:
    """A docker set resolves, preflights the Docker backend, and names a docker snapshot."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.harnessbench]\n"
        'default-set = "dock"\n'
        "[tool.harnessbench.sets.dock]\n"
        'model = "sonnet"\n'
        'sandbox = "docker"\n'
        'baseline = "baseline"\n'
        'arms = [{ name = "baseline", harness = "claude-code" }]\n',
        encoding="utf-8",
    )
    preflighted: list = []
    monkeypatch.setattr(sandbox, "preflight", lambda backend=None: preflighted.append(backend.id))
    monkeypatch.setattr(sandbox, "make_agent", _claude_agent)
    monkeypatch.setattr(docker_mod.DockerBackend, "snapshot_exists", lambda self, name: True)
    monkeypatch.setattr(
        docker_mod.DockerBackend,
        "image_identity",
        lambda self, name: ImageIdentity.available("sha256:abc123"),
    )

    sandbox.cli_build(repo_root=tmp_path, set_name="dock")

    assert preflighted == ["docker"]
    assert "snapshot harnessbench-docker-claude-code-v-" in capsys.readouterr().out
```

(add `from harnessbench.sandbox import docker as docker_mod` and `from harnessbench.sandbox.provenance import ImageIdentity` to that file's imports).

Replace `tests/test_main.py:174-191` with:

```python
def test_sandbox_build_unknown_sandbox_set_exits_two(
    monkeypatch: object, capsys: object
) -> None:
    """A set whose sandbox is unknown fails fast at exit 2 before any build."""

    def failing_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Raise the SchemaError an unknown sandbox produces at resolution."""
        raise SchemaError(
            "[tool.harnessbench.sets.weird]: unsupported sandbox `qemu` "
            "(supported: ['docker', 'microsandbox'])"
        )

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build", "some/dir", "--set", "weird"])

    assert exit_code == 2
    assert "qemu" in capsys.readouterr().err


def test_sandbox_build_docker_preflight_failure_exits_two(
    monkeypatch: object, capsys: object
) -> None:
    """An unreachable Docker daemon is a host-preflight problem: exit 2, readable message."""

    def failing_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Raise the RuntimeError the shared preflight raises for a dead daemon."""
        raise RuntimeError(
            "harnessbench sandbox preflight failed:\n  - docker daemon unreachable "
            "(`docker info` exited 1)"
        )

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build", "some/dir", "--set", "dock"])

    assert exit_code == 2
    assert "docker daemon unreachable" in capsys.readouterr().err
```

Rewrite `tests/fixtures/sandbox/two-backends.toml`:

```toml
# tests/fixtures/sandbox/two-backends.toml
# Accept-path fixture: one file, two implemented backends. Both sets parse and resolve,
# and each drives its own backend:
#   harnessbench sandbox:build --set micro --config tests/fixtures/sandbox/two-backends.toml
#   harnessbench sandbox:build --set dock  --config tests/fixtures/sandbox/two-backends.toml
# The `dock` set needs a reachable Docker daemon; without one, preflight exits 2 with a
# readable remedy rather than failing the file at parse time.
[tool.harnessbench]
default-set = "micro"

[tool.harnessbench.sets.micro]
model = "sonnet"
sandbox = "microsandbox"
baseline = "baseline"
arms = [{ name = "baseline", harness = "claude-code" }]

[tool.harnessbench.sets.dock]
model = "sonnet"
sandbox = "docker"
baseline = "baseline"
arms = [{ name = "baseline", harness = "claude-code" }]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_backend.py tests/config/test_arms.py tests/sandbox/test_sandbox.py tests/test_main.py -v`
Expected: FAIL — `SchemaError: unsupported sandbox 'docker' (supported: ['microsandbox']). Docker is not implemented.` from `test_resolve_sandbox_returns_docker_backend`, `test_parse_sets_accepts_the_docker_sandbox`, `test_two_backends_fixture_parses_both_sets`, and `test_cli_build_docker_set_drives_the_docker_backend`.

- [ ] **Step 3: Write minimal implementation**

In `src/harnessbench/sandbox/backend.py`, replace the second docstring paragraph (lines 8-10) with:

```
microsandbox and Docker are the implemented backends. microsandbox boots hardware-isolated
microVMs and needs Apple Silicon or Linux+KVM; Docker runs containers on any host with a
reachable daemon, trading a shared kernel and guest-readable credentials for that reach.
Adding a third backend is additive: implement the protocol, register it.
```

Add to the harnessbench import block:

```python
from harnessbench.sandbox.docker import DockerBackend
```

Add `"DockerBackend"` to `__all__`, and replace lines 364-367:

```python
_REGISTRY: dict[str, type] = {
    DEFAULT_SANDBOX: MicrosandboxBackend,
    DockerBackend.id: DockerBackend,
}

# Known-but-unimplemented backends: named so the fail-fast message can be specific. Empty
# today — every registered name is implemented — but kept as the seam a future named-but-
# unbuilt backend plugs into.
_NOT_IMPLEMENTED: dict[str, str] = {}
```

Update `resolve_sandbox`'s docstring so it stops naming `docker` as unimplemented:

```python
def resolve_sandbox(name: str) -> SandboxBackend:
    """Return the backend for a set's `sandbox` value, or fail fast.

    Returns a FRESH backend instance each call. An implemented name returns its backend.
    A known-but-unimplemented name raises naming it as not implemented. Any other name
    raises listing the supported values. The raised `SchemaError` maps to exit 2 through
    the CLI/plugin.
    """
```

Leave the body unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green (the flipped tests included).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/backend.py tests/sandbox/test_backend.py tests/config/test_arms.py tests/sandbox/test_sandbox.py tests/test_main.py tests/fixtures/sandbox/two-backends.toml
git commit -m "feat(sandbox): register docker as an implemented sandbox backend"
```

---

### Task 13: Map a Docker build failure to exit `1`

**Files:**
- Modify: `src/harnessbench/__main__.py` (`_run_sandbox_build`, the error-mapping docstring and the `except` chain)
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: `harnessbench.sandbox.errors.SandboxRuntimeError`.
- **Landmine:** `SandboxRuntimeError` IS a `RuntimeError`, so its `except` branch must come FIRST. Reversed, every Docker build failure would report as a config/usage error (exit `2`) instead of a build failure (exit `1`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py  (append)


def test_sandbox_build_docker_runtime_failure_exits_one(
    monkeypatch: object, capsys: object
) -> None:
    """A genuine Docker build failure is a finding (exit 1), not a usage error (exit 2).

    SandboxRuntimeError subclasses RuntimeError, so this pins the branch ORDER: the
    neutral-error branch must be checked before the preflight RuntimeError branch.
    """

    def failing_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Raise the neutral error a failed `docker commit` produces."""
        raise SandboxRuntimeError("docker commit failed for `snap` (exit 1): no space left")

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)

    exit_code = __main__.main(["sandbox:build", "some/dir", "--set", "dock"])

    assert exit_code == 1
    assert "docker commit failed" in capsys.readouterr().err
```

Add `from harnessbench.sandbox.errors import SandboxRuntimeError` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_main.py -k docker_runtime_failure -v`
Expected: FAIL — `assert 2 == 1`; the `except RuntimeError` branch swallowed it as USAGE.

- [ ] **Step 3: Write minimal implementation**

In `src/harnessbench/__main__.py`, add to the top-level imports:

```python
from harnessbench.sandbox.errors import SandboxRuntimeError
```

Replace the error-mapping bullet list in `_run_sandbox_build`'s docstring with:

```
    - `SchemaError` (bad config, or an unsupported set): propagates to the `main` boundary
      → USAGE (2). Config error, not a build failure.
    - `SandboxRuntimeError` (a backend's own build/provision failure): FINDING (1).
      WARNING: this branch must precede the `RuntimeError` branch below — it IS a
      RuntimeError, and reversing them would report every Docker build failure as a
      usage error.
    - `RuntimeError` (host preflight, including microsandbox-not-installed): USAGE (2),
      surfaced before any provisioning. This branch imports no microsandbox, so a host
      without the package still exits 2 cleanly rather than raising `ModuleNotFoundError`.
    - `MicrosandboxError` (a genuine build/provision failure): FINDING (1). Only reachable
      after preflight passed, which guarantees `import microsandbox` works, so importing
      the error type here is safe.
```

and replace the `try` block's `except` chain with:

```python
    try:
        sandbox.cli_build(root, set_name=args.set, config=args.config)
    except SandboxRuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.FINDING
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return ExitCode.USAGE
    except SchemaError:
        raise  # a bad config / unsupported backend is a usage error; let `main` map it to 2
    except Exception as error:
        # Reached only after preflight passed, so microsandbox is importable here.
        from microsandbox.errors import MicrosandboxError

        if isinstance(error, MicrosandboxError):
            print(f"error: {error}", file=sys.stderr)
            return ExitCode.FINDING
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/__main__.py tests/test_main.py
git commit -m "fix(cli): report a docker build failure as a finding, not a usage error"
```

---

### Task 14: The daemon-gated behavior suite

**Files:**
- Create: `tests/sandbox/test_docker_daemon.py`

**Interfaces:**
- Consumes: `DockerBackend`, `DockerSandbox`, `_docker`, `image_ref`, `primitives.BASE_IMAGE`. Nothing is faked here — this is the only file that talks to a real daemon.
- Skip contract: the whole module skips with the actual preflight error text when the daemon is unreachable, so `make test` on a Docker-less host reports skips with a readable reason, never failures.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker_daemon.py
"""Behavior tests for the Docker backend against a REAL daemon.

Every test here shells out to `docker`, so the whole module skips — with the actual
preflight error as the reason — when no daemon answers. `make test` therefore stays green
on a host without Docker while still proving the backend end to end where one exists.
Unit coverage with the CLI faked lives in `test_docker.py`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from harnessbench.sandbox.docker import (
    DockerBackend,
    DockerSandbox,
    _docker,
    image_ref,
)
from harnessbench.sandbox.primitives import BASE_IMAGE

_PREFLIGHT_ERRORS = DockerBackend().preflight()

pytestmark = pytest.mark.skipif(
    bool(_PREFLIGHT_ERRORS),
    reason=f"docker daemon not reachable: {'; '.join(_PREFLIGHT_ERRORS)}",
)


def _unique(prefix: str) -> str:
    """Return a collision-proof container or image name for one test."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _start_container(name: str) -> DockerSandbox:
    """Run a detached base-image container and return a sandbox wrapping it."""
    started = await _docker(
        "run", "--detach", "--name", name, "--entrypoint", "sleep", BASE_IMAGE, "infinity"
    )
    assert started.exit_code == 0, started.stderr
    return DockerSandbox(name)


def test_shell_and_exec_run_inside_a_real_container() -> None:
    """A real container answers both guest primitives with the contract's field names."""
    container = _unique("harnessbench-test")

    async def exercise() -> tuple:
        """Boot, run one shell and one exec, then tear down."""
        sandbox = await _start_container(container)
        try:
            shelled = await sandbox.shell("echo shelled", env={"TZ": "UTC"}, cwd="/tmp")
            execed = await sandbox.exec("printenv", ["TZ"], env={"TZ": "UTC"}, stdin=b"")
            failed = await sandbox.shell("exit 3")
            return shelled, execed, failed
        finally:
            await sandbox.stop()

    shelled, execed, failed = asyncio.run(exercise())

    assert (shelled.exit_code, shelled.stdout_text.strip()) == (0, "shelled")
    assert (execed.exit_code, execed.stdout_text.strip()) == (0, "UTC")
    assert failed.exit_code == 3


def test_exec_stream_yields_output_then_the_real_exit_code() -> None:
    """Streaming reassembles stdout chunks and ends with the guest process's exit code."""
    container = _unique("harnessbench-test")

    async def exercise() -> list:
        """Boot, stream a multi-line command, then tear down."""
        sandbox = await _start_container(container)
        try:
            handle = await sandbox.exec_stream(
                "/bin/sh", ["-c", "echo one; echo two; exit 5"], stdin=b""
            )
            return [event async for event in handle]
        finally:
            await sandbox.stop()

    events = asyncio.run(exercise())

    streamed = b"".join(event.data for event in events if event.event_type == "stdout")
    assert streamed.decode().splitlines() == ["one", "two"]
    assert events[-1].event_type == "exited"
    assert events[-1].code == 5


def test_stop_removes_the_container_from_the_daemon() -> None:
    """After stop, the container is gone — no leaked containers between cells."""
    container = _unique("harnessbench-test")

    async def exercise() -> int:
        """Boot, stop, then ask the daemon whether the container still exists."""
        sandbox = await _start_container(container)
        await sandbox.stop()
        return (await _docker("container", "inspect", container)).exit_code

    assert asyncio.run(exercise()) != 0


def test_commit_makes_the_snapshot_exist_with_an_available_image_identity() -> None:
    """A committed container is a snapshot: it inspects clean and yields a real digest."""
    container = _unique("harnessbench-test")
    snapshot = _unique("harnessbench-docker-test")
    backend = DockerBackend()

    async def exercise() -> None:
        """Boot, commit as the snapshot image, then remove the container."""
        sandbox = await _start_container(container)
        try:
            committed = await _docker("commit", container, image_ref(snapshot))
            assert committed.exit_code == 0, committed.stderr
        finally:
            await sandbox.stop()

    asyncio.run(exercise())
    try:
        identity = backend.image_identity(snapshot)

        assert backend.snapshot_exists(snapshot) is True
        assert identity.image_digest_status == "available"
        assert identity.image_digest.startswith("sha256:")
    finally:
        asyncio.run(_docker("image", "rm", "-f", image_ref(snapshot)))


def test_snapshot_exists_is_false_for_an_image_that_was_never_built() -> None:
    """A name the daemon has never seen reports absent, not an error."""
    assert DockerBackend().snapshot_exists(_unique("harnessbench-docker-absent")) is False
```

- [ ] **Step 2: Run test to verify it fails**

With no daemon: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker_daemon.py -v`
Expected: `5 skipped` with the reason `docker daemon not reachable: docker daemon unreachable (\`docker info\` exited 1) — start Docker and retry: ...`.

With a daemon running (`docker info` exits 0), the same command runs the tests for real. If `ubuntu:latest` is not present locally, pull it first: `docker pull ubuntu:latest`.

- [ ] **Step 3: Write minimal implementation**

No production code changes. If a test fails against a real daemon, fix the backend — not the test — and note the fix in the commit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS overall; the five daemon tests either pass (daemon up) or report as skipped with the preflight reason (daemon down).

- [ ] **Step 5: Commit**

```bash
git add tests/sandbox/test_docker_daemon.py
git commit -m "test(sandbox): add a daemon-gated behavior suite for the docker backend"
```

---

### Task 15: Documentation

**Files:**
- Modify: `README.md` (lines 37-39, the pre-1.0 callout)
- Modify: `docs/sandbox.md` (lines 17-24 the backend paragraph; the "Host requirements and preflight" section; the snapshot cache-location line; "Inside a running cell"; the "Credentials" section)
- Modify: `docs/configuration.md` (line 47, the `sandbox` row)
- Modify: `docs/harnesses.md` (the "Credentials ride as scoped secrets" bullet around line 59)

**Interfaces:**
- Produces: docs that match the code. `docs/style/development.md`: "If a doc drifts from the code, fix it or delete it."
- **Note:** the spec's Documentation Plan names three files; `README.md` also claims Docker is unimplemented (line 38-39), so it is a fourth required edit.

- [ ] **Step 1: Write the failing test**

There is no automated doc test for these claims (`tests/test_readme_examples.py` only parses the README's embedded eval block). Verify by grep instead — this is the check that must go from "hits" to "no hits":

Run: `grep -rn -i "not implemented\|only sandbox backend\|only backend\|only implementation" README.md docs/sandbox.md docs/configuration.md docs/harnesses.md`
Expected before the edit: hits at `README.md:38-39`, `docs/sandbox.md:20-22`, `docs/configuration.md:47`.

- [ ] **Step 2: Run test to verify it fails**

Run the grep above. Expected: three files still describe `docker` as recognized-but-unimplemented, which is now false.

- [ ] **Step 3: Write minimal implementation**

`README.md` — replace the pre-1.0 callout body:

```markdown
> **Pre-1.0.** The eval format and the artifact schemas are the surfaces most
> likely to change. Two sandbox backends ship: `microsandbox` (microVMs; needs
> Apple Silicon or Linux+KVM) and `docker` (containers; needs a reachable Docker
> daemon, weaker isolation — see [docs/sandbox.md](docs/sandbox.md)).
```

`docs/sandbox.md` — replace the backend paragraph (lines 17-24):

```markdown
Isolation sits behind a `SandboxBackend` seam: preflight, the snapshot cache,
and the per-cell mounts belong to the backend, and everything else in
harnessbench is backend-agnostic. A set selects its backend with the `sandbox`
key, and two are implemented:

| `sandbox` | Runtime | Host requirement | Isolation |
|---|---|---|---|
| `microsandbox` (default) | [microsandbox](https://github.com/superradcompany/microsandbox) microVMs | Apple Silicon Mac, or Linux with `/dev/kvm` | Own kernel; credentials injected at the network boundary and never readable in the guest |
| `docker` | Docker containers | Any platform with a reachable Docker daemon (Docker Engine or Docker Desktop) | Shared host kernel; credentials are plain environment variables the guest can read |

The contract on this page — the mounts, the staging rules, the cache identity —
holds for both. Where they differ (cache location, sizing, credential handling)
the difference is labeled.

> **Read this before choosing `docker`.** A container shares the host kernel, and
> the agent runs as root with `bypassPermissions` on the promise that the sandbox
> is the containment boundary. Docker also has no equivalent of microsandbox's
> host-scoped secrets, so the arm's provider credential is a readable environment
> variable inside the guest — an agent under test can print it. Choose `docker`
> when microsandbox cannot run on your host, and treat the credential as exposed
> to whatever the agent does.
```

`docs/sandbox.md` — replace the "Host requirements and preflight" section:

```markdown
## Host requirements and preflight

**microsandbox** runs hardware-virtualized guests, so the host must be one of:

- an **Apple Silicon Mac**, or
- **Linux with `/dev/kvm`**.

**Docker** needs the `docker` CLI on `PATH` and a daemon that answers
`docker info` — any platform, no virtualization extensions required.

Before any cell runs, harnessbench preflights the *selected* backend — platform
or daemon, plus a usable agent credential — and fails with every problem listed,
as exit `2`, before a single cell boots or a paid call is made.
```

`docs/sandbox.md` — in "Snapshots: build once, boot many", replace the cache-location sentence:

```markdown
Snapshots are cached — for microsandbox under `~/.microsandbox/snapshots/`, for
Docker as local images in the daemon's image store — and named
`harnessbench-<backend>-<harness>-<harness-version>-<fingerprint>`. The backend id
is the first ingredient of the 8-character fingerprint, so a Docker and a
microsandbox snapshot of the same agent and environment never collide. List the
Docker ones with `docker images "harnessbench-docker-*"` and reclaim the space
with `docker image rm`; they rebuild on demand.
```

`docs/sandbox.md` — in "Inside a running cell", replace the opening sentence:

```markdown
Each `(eval × arm × sample)` boots its own guest from the snapshot — a microVM
under microsandbox, a container under Docker, both sized 2 vCPUs and 2 GiB — and
tears it down after the turn. A cell, in order:
```

`docs/sandbox.md` — in "Credentials", replace the first bullet and add a second:

```markdown
- **Under `microsandbox`**, each harness adapter declares its credential as a
  scoped **secret** — usable only toward the provider's hosts (an
  `ANTHROPIC_API_KEY` works only toward `api.anthropic.com`). The value is
  injected at the network boundary and is not readable by the agent or by
  `setup.sh`.
- **Under `docker`**, the same declaration becomes a plain container environment
  variable. Docker has no host-scoping primitive, so the value IS readable in the
  guest and `allow_hosts` is not enforced. Prefer a narrowly-scoped API key over a
  subscription token for Docker runs.
```

`docs/configuration.md` — replace line 47:

```markdown
| `sandbox` | string | The sandbox backend: `microsandbox` (the default; microVMs, needs Apple Silicon or Linux+KVM) or `docker` (containers, needs a reachable Docker daemon; shared kernel and guest-readable credentials — see [`sandbox.md`](sandbox.md)). Any other value fails fast with exit `2`. |
```

`docs/harnesses.md` — replace the "Credentials ride as scoped secrets" bullet:

```markdown
- **Credentials are declared once, injected per backend.** Each adapter declares
  its credential and the provider hosts it may be used toward. Under
  `microsandbox` that becomes a host-scoped secret substituted at the network
  boundary — never a readable guest variable. Under `docker` it becomes a plain
  container environment variable, readable in the guest, because Docker has no
  scoping equivalent. OpenCode's quirk holds either way: a host `GEMINI_API_KEY`
  is injected under the SDK's expected `GOOGLE_GENERATIVE_AI_API_KEY` name.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `grep -rn -i "not implemented\|only sandbox backend\|only backend\|only implementation" README.md docs/sandbox.md docs/configuration.md docs/harnesses.md`
Expected: no output.

Run: `make test`
Expected: PASS, whole suite green (`tests/test_readme_examples.py` included).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/sandbox.md docs/configuration.md docs/harnesses.md
git commit -m "docs: document the docker sandbox backend and its isolation tradeoffs"
```

---

## Verification

Run in order. The first four are the issue's done-criteria; the rest are the supporting proofs.

- **`make test`** — proves registry resolution, the cross-backend fingerprint split, Docker preflight strings, credential mapping per backend, the whole `DockerSandbox` guest contract with the CLI faked, and the unchanged microsandbox path (including the byte-identical fingerprint payload). On a host without a daemon the five `tests/sandbox/test_docker_daemon.py` tests report as **skipped** with the preflight reason — never as failures.
- **`make lint`** — proves the new modules and doc updates pass Ruff and houserules, with no suppressions. `make lint:ruff` alone runs on a host with nothing installed but Python, which is the lazy-import invariant in practice; `tests/sandbox/test_docker.py::test_docker_module_imports_only_stdlib_and_harnessbench` enforces it permanently.
- **`uv run harnessbench sandbox:build --set dock --config tests/fixtures/sandbox/two-backends.toml`**
  - With the daemon running: exits **`0`**, printing `building snapshot harnessbench-docker-claude-code-<version>-<fingerprint> from ubuntu:latest ...` then `built ...` and `image identity: sha256:...`. Confirm with `docker images "harnessbench-docker-*"`.
  - With the daemon stopped: exits **`2`** with `error: harnessbench sandbox preflight failed:` and the `docker daemon unreachable (\`docker info\` exited 1) — start Docker and retry: ...` line. Re-running the same command with `--set micro` still exits `0`/`2` on microsandbox's own terms — the file no longer fails as a whole, which is the accept-path flip Task 12 landed.
  - Forcing a build failure (e.g. a set whose `base_image` does not exist) exits **`1`**, not `2` — the branch order from Task 13.
- **`uv run harnessbench run --set dock --config tests/fixtures/sandbox/two-backends.toml`** on a sample eval, with a reachable daemon and a real agent credential — proves a cell stages the project, boots a container, runs `setup.sh`, invokes the agent, gathers facts, grades, tears the container down, and lands in the matrix. Check the artifacts: `provenance.json` records `sandbox.backend_id == "docker"` and an `image_digest` of `sha256:...`, and `docker ps -a` shows no leaked `eval-*` containers afterwards.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`** — the faked-CLI unit suite: preflight remedies, snapshot lookup, image identity degradation, fingerprint distinctness, the four guest-contract methods, mount and credential rendering, build ordering, and teardown-on-failure.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker_daemon.py -v`** with Docker running — the behavior proofs against a real daemon: shell/exec/exec_stream/stop, container removal, and the commit → `snapshot_exists` → `image_identity` round trip.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_backend.py tests/config/test_arms.py tests/test_main.py -v`** — proves the interface flip: `sandbox = "docker"` is a valid config value everywhere, the two-backend fixture parses, unknown names still fail listing both supported values, and the exit-code contract (`0` built/reused, `2` config/preflight, `1` build failure) holds.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/agents -v`** — proves the two seam repairs: every adapter declares `GuestCredential`s and records any backend's infra failure as an errored arm, and `test_microsandbox_imported_only_under_allowlist` proves no agent adapter imports `microsandbox` any more.
- **`grep -rn -i "not implemented" README.md docs/`** returns nothing about the sandbox backend — the docs no longer lie about Docker.

---

## Self-Review

**Spec coverage** — every spec section maps to tasks:

| Spec section / requirement | Task(s) |
|---|---|
| Problem: `docker` is a fail-fast stub | 12 |
| Problem: microsandbox's host gate excludes common machines | 5-11 (the whole backend) |
| Seam leak: agents construct `microsandbox.Secret` | 3 |
| Seam leak: agents catch `MicrosandboxError` | 1, 2 |
| Constraint: lazy-import invariant | 5 (enforced by an AST test) |
| Constraint: the implicit guest-runtime contract | 8, 9 (plus `.exec`, which the spec omits) |
| Constraint: isolation semantics stated, not hidden | 15 |
| Solution: one set-level key, same cache model and exit codes | 12, 13 |
| Decision: CLI, not a client library | 5 |
| Decision: snapshot = committed image; `docker commit` build | 6, 10 |
| Decision: `fingerprint_inputs` with `backend_id="docker"` | 4, 7 |
| Decision: `DockerSandbox` + `DockerVolume` satisfy the guest contract | 5, 8, 9 |
| Decision: resource and lifecycle parity, `replace=True`, teardown | 10, 11 |
| Testing — Logic (registry, cache identity, preflight, credential mapping) | 3, 6, 7, 12 |
| Testing — Behavior (full cell, runtime failure records, microsandbox unchanged) | 2, 4, 14, plus the `harnessbench run` verification |
| Testing — Interface (valid config value, exit codes, no client package at import) | 5, 12, 13 |
| Documentation Plan (`sandbox.md`, `configuration.md`, `harnesses.md`) | 15 (plus `README.md`, which the spec missed) |
| Out of Scope | No task touches `allow_hosts` under Docker, `DOCKER_HOST`, Podman/rootless, Windows claims, microsandbox behavior, VM sizing, or a third backend |

**Discrepancies between the spec and the code** (code wins; each is handled):

1. **The guest contract has four methods, not three.** The spec names `.shell`, `.exec_stream`, `.stop`. `GuestSandbox.exec` (`orchestration/environments.py:114`) also requires **`.exec(command, args, cwd=, env=, timeout=, stdin=)`**, and every agent's `invoke` goes through it. Task 8 implements it.
2. **`docker.py` alone cannot be the only new module.** `backend.py` must import `docker.py` to register it, and `docker.py` needs `backend.py`'s constants and helpers — a hard cycle, and moving the import below the constants trips ruff `E402`, which the house rules forbid suppressing. Task 4 adds `sandbox/primitives.py` to break it.
3. **A `docker commit`-ed image has no RepoDigest.** The spec says `image_identity` reads "Id/RepoDigest". `RepoDigests` is empty for locally committed images, so the local `Id` is what actually lands in artifacts; the format string prefers a registry digest when one exists and falls back to the Id.
4. **The exit-code mapping needs a new branch.** `__main__._run_sandbox_build` maps `RuntimeError` → exit `2`. Since `SandboxRuntimeError` subclasses `RuntimeError`, a Docker build failure would report as a usage error without Task 13's earlier branch.
5. **`README.md` is a fourth doc surface.** The spec's Documentation Plan lists three files; `README.md:38-39` also claims `docker` fails fast as not implemented. Task 15 fixes it.
6. **`tests/fixtures/sandbox/two-backends.toml` is a fail-fast fixture whose entire premise inverts.** The spec's References list only mentions the tests at `tests/config/test_arms.py:551-616`; the fixture file itself and its header comment must be rewritten too (Task 12).
7. **`test_microsandbox_imported_only_under_allowlist` allowlists the three agent adapters.** After Tasks 2-3 they no longer import microsandbox, so the allowlist shrinks to `backend.py`, `__main__.py`, `errors.py` — tightening the guard rather than leaving it stale (Task 3).

**Decisions the spec left open** (all made here, all reversible):

- **Container naming.** Run names come from `_sandbox_run_name` (`eval-<eval_id>-<config>-<worker>`) and eval ids may contain characters Docker's name grammar rejects. `container_name()` maps illegal characters to `-` and prefixes `hb-` when the first character is not alphanumeric.
- **Image naming.** Snapshot names fold in a harness version string. Docker repository names must be lowercase, so `image_ref()` lowercases — deterministic in both directions, so build and lookup always agree.
- **Keeping the container alive.** `docker run --detach --entrypoint sleep <image> infinity` — an explicit entrypoint override, so a base image that declares its own `ENTRYPOINT` still yields something to exec into.
- **`--memory` units.** `VM_MEMORY_MIB` renders as `--memory 2048m`.
- **`.kill()` semantics.** It kills the local `docker exec` client, not the guest process; the caller removes the container immediately afterwards, and that removal is what reaps the guest. Documented as a WARNING in the docstring.
- **No `failed` stream event.** Docker reports a failed launch through a nonzero exit code, so `DockerExecStream` emits `stdout` chunks then exactly one `exited` event. Routing's own `exit_code not in (None, 0)` check already covers the failure path.
- **`_NOT_IMPLEMENTED` stays, empty.** It is the seam a future named-but-unbuilt backend plugs into, and `resolve_sandbox` still reads it; deleting it would mean re-adding the mechanism for the next backend.
- **`SandboxRuntimeError` subclasses `RuntimeError`**, matching `ProcResult.require_success`'s existing infra-failure convention — which is exactly why Task 13's branch ordering is load-bearing.
- **`sandbox_error_types()` rather than a wrapper object.** Keeping microsandbox's native error reachable through one helper leaves the microsandbox path byte-for-byte unchanged (the spec's "no change to microsandbox behavior") while giving agents one neutral `except` tuple.

**Open Questions from the spec, deliberately not resolved here:** rootless Docker / Podman compatibility, whether a subscription-shaped credential should warn or refuse under Docker, and Windows support. Task 15's docs claim Docker Engine/Desktop only; the behavior suite in Task 14 is the instrument for answering the first and third once someone runs it on those targets.
