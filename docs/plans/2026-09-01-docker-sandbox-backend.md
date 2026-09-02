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
- **Credentials never touch an argv or an exception string.** A container's credentials are rendered with `--env-file` pointing at a mode-`0600` temp file that is deleted as soon as `docker run` returns — never `-e NAME=value`, which any user on the host can read out of `ps` and which lands verbatim in `docker inspect`. The three seam functions and every error they raise describe a call as `docker exec <container>` or `docker run <image>` — never as joined argv — so a secret can never ride a `SandboxRuntimeError` into a report artifact. Task 11's tests assert the token appears in NEITHER the rendered argv NOR the exception text.
- **Every container harnessbench starts is labelled, and it removes only its own.** Every `docker run` carries `--label harnessbench.owner=harnessbench` and `--label harnessbench.run=<per-process nonce>`. The pre-start `rm -f` that gives `replace=True` semantics first inspects the existing container's `harnessbench.owner` label and REFUSES to remove an unlabelled one — a developer's own container that happens to share a name must never be destroyed by a harnessbench run. Every removal goes through one bounded, idempotent `_remove_container` helper that treats "no such container" as success, reports a daemon failure instead of raising, and is therefore safe to call from a `finally`.
- **A docker control-plane failure is not a guest exit code.** `docker exec` exits 125/126/127 both when the daemon is gone or the container was removed AND when the guest command itself exits with those codes. `_docker`, `DockerSandbox.shell`, and `DockerSandbox.exec` tell the two apart — known daemon/container stderr shapes first, a `docker inspect` of the container's state when ambiguous — and raise `SandboxRuntimeError` for the control-plane case so the arm lands as `is_error` rather than as a graded miss. `snapshot_exists` draws the same line between "image absent" (returns False) and "daemon unreachable" (raises).
- **A timed-out guest command kills the container.** Killing the local `docker exec` client does NOT stop the process inside the container. On a timeout, `DockerSandbox` removes the container — which is what reaps the guest process — marks itself dead so every later call fails fast, and raises `SandboxRuntimeError`; the arm records as `is_error` and no orphaned agent keeps burning tokens for the rest of the run.
- **Containers run as root, and a Linux host gets its files back.** Both the build and the runtime `docker run` pass `--user 0:0`: agents install their CLIs under `/root` and run with `bypassPermissions` on the promise that the sandbox is the containment boundary. Under rootful Docker on Linux that leaves root-owned files in the bind-mounted clean room, which breaks host-side fact gathering and `TemporaryDirectory` cleanup — so on Linux hosts `stop()` first runs `chown -R <host uid>:<gid> /workspace` inside the guest. macOS (Docker Desktop) maps ownership in its own file-sharing layer and needs no chown.
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
| `README.md`, `docs/concepts.md`, `docs/sandbox.md`, `docs/configuration.md`, `docs/harnesses.md` | Modified: two supported backends, Docker host requirements, the shared-kernel and credential-visibility tradeoffs, image cache location, and the two blanket "every cell is a microVM" claims (`README.md:14`, `docs/concepts.md:155`). |

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
- Test: `tests/agents/test_codex.py`, `tests/agents/test_opencode.py`, `tests/agents/test_claude.py` (rewrite EVERY `microsandbox`/`_capture_secret`-dependent test — the full list is in Step 1), `tests/sandbox/test_backend.py` (new mapping test; tighten the allowlist test at line 253)

**Interfaces:**
- Produces: `harnessbench.agents.base.GuestCredential(env_name: str, value: str, allow_hosts: tuple[str, ...])`; `MicrosandboxBackend._runtime_secrets(agent) -> list`.
- Consumes: `agent.secrets() -> list[GuestCredential]`.
- After this task, no file under `src/harnessbench/agents/` imports `microsandbox` at all.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_backend.py  (append)


def test_microsandbox_maps_guest_credentials_to_host_scoped_secrets() -> None:
    """One neutral credential renders as a real microsandbox SecretEntry, shape and all.

    This asserts against the runtime's ACTUAL dataclass, not a stand-in: microsandbox's
    `SecretEntry` (`microsandbox/types.py`) names the variable `env_var` — not `name` —
    and `Secret.env` normalizes `allow_hosts` to a TUPLE. A test that invented a friendlier
    shape here would pass while the mapping handed the runtime the wrong keyword.
    """
    microsandbox_backend = backend.resolve_sandbox("microsandbox")
    agent = ClaudeCodeAgent(auth_value="sk-test-value", auth_env="ANTHROPIC_API_KEY")

    secrets = microsandbox_backend._runtime_secrets(agent)

    assert len(secrets) == 1
    assert secrets[0].env_var == "ANTHROPIC_API_KEY"
    assert secrets[0].value == "sk-test-value"
    assert secrets[0].allow_hosts == ("api.anthropic.com",)
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

**Rewrite every `microsandbox`-faking test under `tests/agents/`.** After this task no agent adapter imports `microsandbox`, so every test that monkeypatches `microsandbox.Secret` fakes a collaborator that no longer exists — it would pass while asserting nothing. Confirm the list before editing with `grep -rn "microsandbox\|_capture_secret\|Secret" tests/agents/`; today it is exactly:

| File | Test / fixture | Rewrite to |
|---|---|---|
| `tests/agents/test_claude.py` (~434) | `test_secrets_uses_configured_auth_env` (its inner `FakeSecret` and `monkeypatch.setattr(microsandbox, "Secret", ...)`) | assert the returned `GuestCredential` directly; delete `FakeSecret` and the `import microsandbox` |
| `tests/agents/test_codex.py` (~37) | the `_capture_secret` fixture and its `DummySecret` | **delete the fixture entirely** |
| `tests/agents/test_codex.py` (~199) | `test_from_env_prefers_api_key` | drop the `captured = _capture_secret(monkeypatch)` line; assert `agent.secrets() == [GuestCredential(env_name="CODEX_API_KEY", value="api-key", allow_hosts=("api.openai.com",))]` |
| `tests/agents/test_codex.py` (~214) | `test_from_env_falls_back_to_access_token` | same shape, `env_name="CODEX_ACCESS_TOKEN"`, `value="token"` |
| `tests/agents/test_codex.py` (~289) | `test_secrets_scopes_api_key_to_openai_host` | assert the whole `GuestCredential` in one equality |
| `tests/agents/test_codex.py` (~303) | `test_secrets_scopes_access_token_to_codex_hosts` | assert `"chatgpt.com"` and `"auth.openai.com"` are in `credentials[0].allow_hosts` |
| `tests/agents/test_opencode.py` (~467) | `test_secrets_scopes_to_provider_host` | assert the whole `GuestCredential` |
| `tests/agents/test_opencode.py` (~493) | `test_secrets_anthropic_fallback_scopes_to_anthropic_host` | assert `allow_hosts == ("api.anthropic.com",)` |
| `tests/agents/test_opencode.py` (~519) | `test_secrets_gemini_remaps_to_sdk_env_name_and_scopes_to_google_host` | the snippet below |
| `tests/agents/test_opencode.py` (~545) | `test_secrets_google_generative_ai_passthrough_unchanged` | assert `env_name == "GOOGLE_GENERATIVE_AI_API_KEY"` unchanged |

Each rewrite deletes that test's inner `FakeSecret`/`DummySecret` class, its `import microsandbox`, its `monkeypatch.setattr(microsandbox, "Secret", ...)`, and the now-unneeded `monkeypatch` parameter. Note `allow_hosts` is a **tuple** on `GuestCredential`, so `== ["api.openai.com"]` will not compare equal — write `("api.openai.com",)`. For example, the OpenCode Gemini remap test becomes:

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

`test_secrets_empty_when_auth_json_path_is_used` (`tests/agents/test_codex.py`) keeps asserting `agent.secrets() == []` and simply loses its `_capture_secret` call. Add `from harnessbench.agents.base import GuestCredential` to all three test files' imports, and after the rewrite `grep -rn microsandbox tests/agents/` must return nothing.

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
- Produces: `DOCKER_BINARY`, `OWNER_LABEL`, `OWNER_LABEL_VALUE`, `RUN_LABEL`, `RUN_NONCE`, `DockerResult`, `DockerCallTimeout`, `_call_description`, `_docker_sync`, `_docker`, `_docker_stream`, `DockerVolume`, `container_name`, `image_ref`, `_env_flags`, `_volume_flags`, `_resource_flags`, `_label_flags`.
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


def test_image_ref_lowercases_and_hashes_the_snapshot_name() -> None:
    """Docker repository names must be lowercase, and the suffix keeps the mapping injective."""
    assert docker_mod.image_ref("harnessbench-docker-claude-code-V1.2-ab12cd34") == (
        "harnessbench-docker-claude-code-v1.2-ab12cd34-c0f7b73f"
    )


def test_image_ref_keeps_case_variants_of_one_name_distinct() -> None:
    """Two snapshot names differing only in case must not collapse to one image.

    Lowercasing alone is lossy: `...Latest-AB12CD34` and `...latest-ab12cd34` are
    different cache identities, and collapsing them would serve one snapshot's image for
    the other's fingerprint. The 8-character digest is taken over the ORIGINAL name, so
    the mapping stays injective in practice.
    """
    upper = docker_mod.image_ref("Harnessbench-Docker-Claude-Code-Latest-AB12CD34")
    lower = docker_mod.image_ref("harnessbench-docker-claude-code-latest-ab12cd34")

    assert upper == "harnessbench-docker-claude-code-latest-ab12cd34-9085d48a"
    assert lower == "harnessbench-docker-claude-code-latest-ab12cd34-0d462a07"
    assert upper != lower


def test_image_ref_sanitizes_characters_docker_rejects() -> None:
    """Any snapshot name yields a LEGAL reference — slashes, colons, and spaces included.

    A snapshot name is `harnessbench-<backend>-<harness>-<harness-version>-<fingerprint>`
    and the harness version is whatever the CLI reports, which is not constrained to
    Docker's `[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*` repository grammar. An illegal reference
    would fail the commit AFTER a full provision.
    """
    assert docker_mod.image_ref("harnessbench/docker:claude code+1") == (
        "harnessbench-docker-claude-code-1-22995c9f"
    )
    assert docker_mod.image_ref("...") == "harnessbench-snapshot-ab5df625"


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


def test_call_description_stops_at_the_first_flag() -> None:
    """A described call names the subcommand, never the flags that may carry a secret."""
    described = docker_mod._call_description(
        ("run", "--detach", "--env-file", "/tmp/creds", "ubuntu:latest")
    )

    assert described == "`docker run ...`"
    assert "/tmp/creds" not in described


def test_docker_sync_timeout_message_names_the_call_without_its_argv(
    monkeypatch: object,
) -> None:
    """A wedged call is reported by description; raw argv never reaches the exception.

    Exception text from this seam ends up in `<sandbox-error>` result text, which is
    written to run artifacts. An argv-joined message would publish whatever flag values
    the call carried.
    """

    def wedged(*args: object, **kwargs: object) -> object:
        """Fail the way subprocess does when the call outlives its deadline."""
        raise docker_mod.subprocess.TimeoutExpired(cmd="docker", timeout=30.0)

    monkeypatch.setattr(docker_mod.subprocess, "run", wedged)

    with pytest.raises(docker_mod.SandboxRuntimeError) as raised:
        docker_mod._docker_sync("login", "--password", "sk-super-secret")

    assert "sk-super-secret" not in str(raised.value)
    assert "`docker login ...`" in str(raised.value)
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

IMPORTANT: no error raised from this module may contain a raw argv. Credentials reach a
container through an `--env-file`, and every failure names the call by description
(`docker exec <container>`) rather than by its arguments, because this text ends up in
`<sandbox-error>` result text that is written to run artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import uuid
from dataclasses import dataclass

from harnessbench.sandbox.errors import SandboxRuntimeError
from harnessbench.sandbox.primitives import VM_CPUS, VM_MEMORY_MIB

DOCKER_BINARY = "docker"
# Docker container names match [a-zA-Z0-9][a-zA-Z0-9_.-]*; everything else is replaced.
_ILLEGAL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
# Docker repository names match [a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*; everything else is replaced.
_ILLEGAL_IMAGE_CHARS = re.compile(r"[^a-z0-9._-]")
_REPEATED_SEPARATORS = re.compile(r"[-._]{2,}")
# A blocking `docker` call (preflight, image inspect) must not hang a collection-time check.
SYNC_CALL_TIMEOUT_SECONDS = 30.0

OWNER_LABEL = "harnessbench.owner"
OWNER_LABEL_VALUE = "harnessbench"
RUN_LABEL = "harnessbench.run"
# One nonce per harnessbench process: it makes this run's containers identifiable in
# `docker ps` and lets a cleanup sweep tell a live sibling run's containers from its own.
RUN_NONCE = uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class DockerResult:
    """The captured outcome of one completed `docker` CLI invocation."""

    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


class DockerCallTimeout(SandboxRuntimeError):
    """One `docker` invocation outlived its deadline.

    A distinct type so a caller can react to the deadline specifically — `DockerSandbox`
    reaps its container on this and only this — while every caller that just wants "the
    sandbox broke" still catches it as a `SandboxRuntimeError`.
    """


def _call_description(args: tuple[str, ...]) -> str:
    """Describe a `docker` call for an error message without echoing a single flag value.

    Keeps the leading positional words (the subcommand and, where it precedes the flags,
    its object) and stops at the first `-`. Callers with a safe, more specific description
    — a container or image name that sits AFTER the flags — pass `description=` instead.
    """
    words: list[str] = []
    for argument in args:
        if argument.startswith("-"):
            break
        words.append(argument)
    return f"`docker {' '.join(words)} ...`"


def _docker_sync(
    *args: str, timeout: float = SYNC_CALL_TIMEOUT_SECONDS, description: str | None = None
) -> DockerResult:
    """Run one blocking `docker` subcommand, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        timeout: Seconds to wait before treating the call as wedged.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`. Never interpolate raw argv into an error message here.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the `docker` binary is absent.
        DockerCallTimeout: If the call outlives `timeout`.
    """
    described = description or _call_description(tuple(args))
    try:
        completed = subprocess.run(
            [DOCKER_BINARY, *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as error:
        raise SandboxRuntimeError("docker CLI not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise DockerCallTimeout(f"{described} timed out after {timeout}s") from error
    return DockerResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


async def _docker(
    *args: str,
    stdin: bytes | None = None,
    timeout: float | None = None,
    description: str | None = None,
) -> DockerResult:
    """Run one `docker` subcommand on the event loop, capturing both streams.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin; None writes nothing and
            still closes it, so a guest command can never block on an open pipe.
        timeout: Seconds to wait for completion, or None to wait indefinitely.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`.

    Returns:
        The invocation's exit code and captured streams.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned.
        DockerCallTimeout: If the call outlives `timeout`. Killing this client does NOT
            stop the process inside the container — see `DockerSandbox._reap`.
    """
    described = description or _call_description(tuple(args))
    process = await _spawn(*args, description=described)
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin or b""), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise DockerCallTimeout(f"{described} timed out after {timeout}s") from None
    return DockerResult(
        tuple(args),
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _docker_stream(
    *args: str, stdin: bytes | None = None, description: str | None = None
) -> object:
    """Spawn one `docker` subcommand for incremental stdout reads.

    Args:
        *args: The `docker` subcommand and its arguments.
        stdin: Bytes to write before closing the child's stdin.
        description: How to name this call in an error, defaulting to a redacted rendering
            of `args`.

    Returns:
        The live `asyncio.subprocess.Process`; the caller drains `.stdout`, concurrently
        drains `.stderr`, and awaits `.wait()` for the exit code.

    Raises:
        SandboxRuntimeError: If the process cannot be spawned.
    """
    process = await _spawn(*args, description=description or _call_description(tuple(args)))
    if process.stdin is not None:
        process.stdin.write(stdin or b"")
        process.stdin.close()
    return process


async def _spawn(*args: str, description: str) -> object:
    """Start a `docker` subprocess with all three streams piped.

    Args:
        *args: The `docker` subcommand and its arguments.
        description: How to name this call in an error. Required, and never the argv:
            spawn failures are reported to the user and land in run artifacts.

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
        raise SandboxRuntimeError(f"could not run {description}: {error}") from error


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

    A snapshot name is `harnessbench-<backend>-<harness>-<harness-version>-<fingerprint>`,
    and the harness version is whatever that CLI reports — not constrained to Docker's
    repository grammar, which is lowercase `[a-z0-9]` with `.`, `_`, and `-` separators.
    So the name is lowercased, illegal characters collapse to `-`, and an 8-character
    SHA-256 of the ORIGINAL name is appended.

    That digest is what keeps the mapping injective: lowercasing alone would let two
    distinct snapshot names (differing only in case, or only in a character that sanitizes
    to `-`) collapse onto one image, serving one cache identity's image for another's
    fingerprint. It is deterministic, so `build_snapshot` and `snapshot_exists` always
    agree on the reference.
    """
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:8]
    lowered = _ILLEGAL_IMAGE_CHARS.sub("-", snapshot.lower())
    stem = _REPEATED_SEPARATORS.sub("-", lowered).strip("-._")[:100].rstrip("-._")
    return f"{stem or 'harnessbench-snapshot'}-{digest}"


def _env_flags(env: dict | None) -> list[str]:
    """Render a NON-SECRET guest environment mapping as repeated `docker exec -e` arguments.

    `HOME`, `TZ`, and the `HARNESSBENCH_*` cell variables belong here. Credentials do NOT:
    an `-e` value is visible to every user on the host through `ps` and is echoed back by
    `docker inspect`, so `_credential_env_file` renders those through a `--env-file`.
    """
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


def _label_flags() -> list[str]:
    """Render the ownership labels every harnessbench container carries.

    The owner label is what makes removal safe: the pre-start `rm -f` only fires on a
    container carrying it, so a name collision with something a developer started by hand
    is refused rather than silently destroying their container. The run nonce identifies
    THIS process's containers, so a sweep can leave a concurrent run's alone.
    """
    return [
        "--label",
        f"{OWNER_LABEL}={OWNER_LABEL_VALUE}",
        "--label",
        f"{RUN_LABEL}={RUN_NONCE}",
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): add the docker CLI seam, image refs, and bind-mount rendering"
```

---

### Task 6: `DockerBackend` preflight, `snapshot_exists`, `image_identity`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (append the `DockerBackend` class)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerBackend` with `id = "docker"`, `preflight()`, `snapshot_exists(name)`, `image_identity(snapshot)`; module-level `_IMAGE_ABSENT_PATTERN`.
- Consumes: `_docker_sync`, `image_ref`, `harnessbench.sandbox.provenance.ImageIdentity`, `shutil.which`.
- **Classification rule.** `snapshot_exists` answers a cache question, so it may only return `False` for a nonzero inspect whose stderr has the *image absent* shape. Any other nonzero exit — an unreachable daemon most of all — raises `SandboxRuntimeError`. Returning `False` there would report "not cached", send `ensure_snapshot` into a build, and turn a dead daemon into a confusing build failure instead of a preflight-shaped error.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


def _fake_sync(monkeypatch: object, result: object, calls: list | None = None) -> None:
    """Point the blocking docker seam at a canned result, optionally recording arguments."""

    def fake(*args: str, timeout: float = 30.0, description: object = None) -> object:
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


def test_snapshot_exists_inspects_the_sanitized_image_reference(monkeypatch: object) -> None:
    """An existing local image means the snapshot is present; the ref is the sanitized one."""
    calls: list = []
    _fake_sync(monkeypatch, DockerResult(("image", "inspect"), 0, "[]", ""), calls)
    backend = docker_mod.DockerBackend()

    present = backend.snapshot_exists("harnessbench-docker-claude-code-V1-ab12cd34")

    assert present is True
    assert calls == [
        ("image", "inspect", "harnessbench-docker-claude-code-v1-ab12cd34-da7a2cb7")
    ]


def test_snapshot_exists_false_when_the_image_is_absent(monkeypatch: object) -> None:
    """A nonzero inspect whose stderr has the image-absent shape means "not built yet"."""
    _fake_sync(
        monkeypatch,
        DockerResult(
            ("image", "inspect"),
            1,
            "",
            "Error response from daemon: No such image: harnessbench-docker-x:latest",
        ),
    )
    backend = docker_mod.DockerBackend()

    assert backend.snapshot_exists("harnessbench-docker-claude-code-latest-ab12cd34") is False


def test_snapshot_exists_raises_when_the_daemon_is_unreachable(monkeypatch: object) -> None:
    """A dead daemon is a sandbox-runtime failure, never a "the snapshot isn't cached" False.

    Answering False here would send `ensure_snapshot` into a build against a daemon that
    is not there, reporting a confusing build failure instead of the daemon problem.
    """
    _fake_sync(
        monkeypatch,
        DockerResult(
            ("image", "inspect"),
            1,
            "",
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
        ),
    )
    backend = docker_mod.DockerBackend()

    with pytest.raises(docker_mod.SandboxRuntimeError, match="Cannot connect to the Docker daemon"):
        backend.snapshot_exists("harnessbench-docker-claude-code-latest-ab12cd34")


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

    def missing(*args: str, timeout: float = 30.0, description: object = None) -> object:
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

Add `import shutil` to the stdlib import block and `from harnessbench.sandbox.provenance import ImageIdentity` to the harnessbench block of `src/harnessbench/sandbox/docker.py`. Add the classification pattern next to the other module-level regexes:

```python
# The only nonzero `docker image inspect` that means "not cached yet". Every other
# failure — a dead daemon, a permission problem — must be raised, not read as absence.
_IMAGE_ABSENT_PATTERN = re.compile(r"[Nn]o such image", re.MULTILINE)
```

Then append:

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
        """Return whether the committed snapshot image is present in the local image store.

        Raises:
            SandboxRuntimeError: If the inspect fails for any reason OTHER than the image
                being absent — an unreachable daemon above all. See the classification
                rule in this task's Interfaces.
        """
        reference = image_ref(name)
        result = _docker_sync(
            "image", "inspect", reference, description=f"`docker image inspect {reference}`"
        )
        if result.exit_code == 0:
            return True
        if _IMAGE_ABSENT_PATTERN.search(result.stderr):
            return False
        raise SandboxRuntimeError(
            f"docker image inspect for `{reference}` exited {result.exit_code}: "
            f"{result.stderr.strip()[-500:] or '(no output)'}"
        )

    def image_identity(self: object, snapshot: str) -> ImageIdentity:
        """Return the snapshot image's registry digest when it has one, else its image Id.

        Any failure — the image missing, the daemon gone, no docker binary at all —
        becomes an explained `unavailable` result rather than propagating: a backend
        lookup failure must never abort provenance capture or artifact aggregation.
        """
        try:
            reference = image_ref(snapshot)
            result = _docker_sync(
                "image",
                "inspect",
                reference,
                "--format",
                self._IMAGE_DIGEST_FORMAT,
                description=f"`docker image inspect {reference}`",
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
Expected: PASS (25 tests).

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
Expected: PASS (28 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): fold the docker backend id into the snapshot cache fingerprint"
```

---

### Task 8: `DockerSandbox.shell` and `.exec`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `ContainerRemoval`, `_remove_container`, `DockerExecOutput`, `DockerSandbox`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `ContainerRemoval(name, removed, absent, error)` and `_remove_container(name, *, timeout=...)`; `DockerExecOutput(exit_code, stdout_text, stderr_text)`; `DockerSandbox(container, *, restore_owner=None)` with `.container`, `.alive`, `.shell(script, *, env=None, cwd=None)`, `.exec(command, args=None, *, cwd=None, env=None, timeout=None, stdin=None)`.
- Consumes: `_docker`, `_env_flags`, `DockerCallTimeout`.
- **Contract source:** `sandbox.run_setup_sh` calls `sandbox.shell(script, env=env, cwd=PROJECT_MOUNT)` (`sandbox/sandbox.py:197`); `GuestSandbox.exec` calls `self._sandbox.exec(command[0], command[1:], cwd=..., env=..., timeout=..., stdin=...)` (`orchestration/environments.py:114`). Both read `exit_code` / `stdout_text` / `stderr_text` off the result.
- **Why `_remove_container` lands here** rather than with `stop()` in Task 9: the timeout path needs it first. Tasks 9, 10, and 11 all reuse this one helper — there is exactly one place in the module that removes a container.
- **Two failure modes this task owns:**
  1. **The timeout reap.** `docker exec`'s client dying does not stop the guest process. On `DockerCallTimeout`, `DockerSandbox` removes the container (which reaps the process), marks itself dead, and raises `SandboxRuntimeError` so the arm lands as `is_error`. Without the removal, an agent whose turn timed out keeps running — and keeps spending — for the remainder of the benchmark.
  2. **Control-plane vs guest exit code.** `docker exec` exits 125/126/127 for its own failures AND a guest command may legitimately exit with those codes. Known daemon/container stderr shapes classify outright; an otherwise-ambiguous code is settled by inspecting the container's running state. Control-plane failures raise; guest exit codes are returned.

- [ ] **Step 1: Write the failing test**

```python
# tests/sandbox/test_docker.py  (append)


def _record_docker(monkeypatch: object, result: object) -> list:
    """Point the async docker seam at a canned result and record every invocation."""
    calls: list = []

    async def fake(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
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


def test_exec_timeout_removes_the_container_and_raises(monkeypatch: object) -> None:
    """A timed-out turn reaps the guest process by removing the container.

    Killing the local `docker exec` client leaves the agent running inside the container,
    burning tokens for the rest of the benchmark. Removal is the only thing that stops it,
    and the raise is what lands the arm as `is_error`.
    """
    calls: list = []

    async def timing_out(*args: str, stdin: object = None, timeout: object = None,
                         description: object = None) -> object:
        """Time out the exec, succeed on the removal that follows."""
        calls.append(args)
        if args[0] == "exec":
            raise docker_mod.DockerCallTimeout("`docker exec eval-hello-alpha-main` timed out")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", timing_out)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="timed out"):
        asyncio.run(sandbox.exec("/root/.local/bin/claude", ["-p", "slow"], timeout=1))

    assert calls[-1] == ("rm", "-f", "eval-hello-alpha-main")
    assert sandbox.alive is False


def test_a_reaped_sandbox_fails_every_later_call_fast(monkeypatch: object) -> None:
    """Once the container is gone, later guest calls fail immediately instead of hanging."""

    async def timing_out(*args: str, stdin: object = None, timeout: object = None,
                         description: object = None) -> object:
        """Time out the exec, succeed on the removal that follows."""
        if args[0] == "exec":
            raise docker_mod.DockerCallTimeout("timed out")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", timing_out)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")
    with pytest.raises(docker_mod.SandboxRuntimeError):
        asyncio.run(sandbox.exec("claude", timeout=1))

    with pytest.raises(docker_mod.SandboxRuntimeError, match="no longer usable"):
        asyncio.run(sandbox.shell("echo late"))


def test_exec_classifies_a_removed_container_as_an_infra_failure(monkeypatch: object) -> None:
    """A vanished container is a sandbox failure, not a guest command that exited 126."""
    _record_docker(
        monkeypatch,
        DockerResult(("exec",), 126, "", "Error: No such container: eval-hello-alpha-main"),
    )
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="No such container"):
        asyncio.run(sandbox.exec("claude"))


def test_exec_keeps_an_ordinary_guest_exit_code_when_the_container_is_still_running(
    monkeypatch: object,
) -> None:
    """127 from the guest's own shell stays a graded result; the container is asked.

    `docker exec` reserves 125-127 for its own failures, but `sh -c "nosuchcmd"` also
    exits 127. Treating that as infra would record a real agent miss as an errored arm.
    """

    async def fake(*args: str, stdin: object = None, timeout: object = None,
                   description: object = None) -> object:
        """Report a 127 exec, and a container that is very much still running."""
        if args[0] == "inspect":
            return DockerResult(args, 0, "true\n", "")
        return DockerResult(args, 127, "", "sh: 1: nosuchcmd: not found")

    monkeypatch.setattr(docker_mod, "_docker", fake)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    result = asyncio.run(sandbox.shell("nosuchcmd"))

    assert result.exit_code == 127


def test_remove_container_treats_an_absent_container_as_success(monkeypatch: object) -> None:
    """Removing what is already gone is fine — teardown must be idempotent."""
    _record_docker(
        monkeypatch, DockerResult(("rm",), 1, "", "Error: No such container: gone")
    )

    outcome = asyncio.run(docker_mod._remove_container("gone"))

    assert (outcome.removed, outcome.absent, outcome.error) == (False, True, None)


def test_remove_container_reports_a_daemon_failure_without_raising(monkeypatch: object) -> None:
    """Removal never raises: it is called from `finally` blocks that must not be masked."""
    _record_docker(
        monkeypatch,
        DockerResult(("rm",), 1, "", "Cannot connect to the Docker daemon at unix:///..."),
    )

    outcome = asyncio.run(docker_mod._remove_container("stuck"))

    assert outcome.removed is False
    assert outcome.absent is False
    assert "Cannot connect to the Docker daemon" in outcome.error
```

Add `import asyncio` and `from harnessbench.orchestration.environments import GuestSandbox` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "shell or exec or guest_sandbox" -v`
Expected: FAIL — `AttributeError: module 'harnessbench.sandbox.docker' has no attribute 'DockerSandbox'`.

- [ ] **Step 3: Write minimal implementation**

Add the classification patterns next to the other module-level regexes:

```python
# Stderr shapes that mean docker itself failed, not the guest command.
_CONTROL_PLANE_PATTERN = re.compile(
    r"Cannot connect to the Docker daemon"
    r"|Is the docker daemon running"
    r"|error during connect"
    r"|No such container"
    r"|is not running"
    r"|is restarting"
    r"|is paused"
    r"|removal of container .* is already in progress",
    re.IGNORECASE,
)
# `docker exec` reserves these for its own failures, but a guest command may exit with
# them too (127 is `command not found` from the guest's own shell), so they make a result
# AMBIGUOUS rather than infra — the container's state settles it.
_AMBIGUOUS_EXIT_CODES = frozenset({125, 126, 127})
# The only nonzero `docker rm`/`docker inspect` that means "already gone", which is a
# successful teardown. `rm` says "No such container"; `inspect` says "No such object".
_CONTAINER_ABSENT_PATTERN = re.compile(r"[Nn]o such (container|object)")
# A wedged removal must not hang teardown behind a dead daemon.
REMOVE_TIMEOUT_SECONDS = 30.0
```

Append the removal helper next to the other module-level functions:

```python
@dataclass(frozen=True)
class ContainerRemoval:
    """The outcome of one bounded `docker rm -f`.

    `absent` and `removed` are both successes — teardown is idempotent, and a container
    that is already gone is exactly what the caller wanted. `error` is set only for a
    genuine daemon failure.
    """

    name: str
    removed: bool
    absent: bool
    error: str | None


async def _remove_container(
    name: str, *, timeout: float = REMOVE_TIMEOUT_SECONDS
) -> ContainerRemoval:
    """Remove one container by name, bounded and idempotent.

    This is the ONLY place in the module that removes a container: teardown, the timeout
    reap, the pre-start replace, and cleanup after a failed start all route through it.

    It never raises. Every caller is either a `finally` block or an `except` handler where
    a raise would mask the primary exception, so a daemon failure comes back in `.error`
    for the caller to decide about.

    Args:
        name: The container to remove.
        timeout: Seconds to allow before treating the removal as wedged.

    Returns:
        Whether the container was removed, was already absent, or the daemon failed.
    """
    try:
        result = await _docker(
            "rm", "-f", name, timeout=timeout, description=f"`docker rm -f {name}`"
        )
    except SandboxRuntimeError as error:
        return ContainerRemoval(name, removed=False, absent=False, error=str(error))
    if result.exit_code == 0:
        return ContainerRemoval(name, removed=True, absent=False, error=None)
    if _CONTAINER_ABSENT_PATTERN.search(result.stderr):
        return ContainerRemoval(name, removed=False, absent=True, error=None)
    return ContainerRemoval(
        name,
        removed=False,
        absent=False,
        error=(
            f"docker rm -f `{name}` exited {result.exit_code}: "
            f"{result.stderr.strip()[-500:] or '(no output)'}"
        ),
    )
```

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

    def __init__(self: object, container: str, *, restore_owner: str | None = None) -> None:
        """Wrap an already-running container by name.

        Args:
            container: The running container's name.
            restore_owner: `uid:gid` to chown `/workspace` to before teardown, or None to
                skip. Set only on Linux hosts, where rootful Docker would otherwise leave
                root-owned files the host cannot read or delete.
        """
        self._container = container
        self._restore_owner = restore_owner
        self._alive = True

    @property
    def container(self: object) -> str:
        """The name of the container this sandbox drives."""
        return self._container

    @property
    def alive(self: object) -> bool:
        """Whether the container is still believed to be usable."""
        return self._alive

    def _require_alive(self: object) -> None:
        """Fail fast once the container is known to be gone.

        Raises:
            SandboxRuntimeError: If a previous call reaped or lost the container. Each
                later call would otherwise pay a full `docker exec` round trip to learn
                the same thing, and the arm is already recorded as errored.
        """
        if not self._alive:
            raise SandboxRuntimeError(
                f"container `{self._container}` is no longer usable (it was removed after "
                f"an earlier timeout or runtime failure)"
            )

    def _exec_args(self: object, *, cwd: str | None, env: dict | None) -> list[str]:
        """Build the leading `docker exec` arguments shared by every guest call.

        `-i` keeps stdin attached so the caller can force EOF on it; without that a
        guest CLI can block forever on an open pipe.
        """
        args = ["exec", "-i", *_env_flags(env)]
        if cwd:
            args += ["-w", cwd]
        return [*args, self._container]

    async def _reap(self: object) -> None:
        """Remove the container and mark this sandbox dead.

        Killing the local `docker exec` client does NOT stop the process inside the
        container: without this, an agent whose turn timed out keeps running — and keeps
        spending — for the rest of the benchmark. Removing the container reaps it.
        """
        self._alive = False
        await _remove_container(self._container)

    async def _control_plane_failure(self: object, result: DockerResult) -> str | None:
        """Return an error message when `result` is docker failing, not the guest exiting.

        Known daemon/container stderr shapes classify outright. An otherwise-unexplained
        125/126/127 is ambiguous — `docker exec` reserves those codes, but so does a guest
        shell reporting `command not found` — so the container's running state settles it.
        """
        if result.exit_code == 0:
            return None
        if _CONTROL_PLANE_PATTERN.search(result.stderr):
            return (
                f"docker exec against container `{self._container}` failed: "
                f"{result.stderr.strip()[-500:] or '(no output)'}"
            )
        if result.exit_code not in _AMBIGUOUS_EXIT_CODES:
            return None
        state = await _docker(
            "inspect",
            "--format",
            "{{.State.Running}}",
            self._container,
            description=f"`docker inspect {self._container}`",
        )
        if state.exit_code == 0 and state.stdout.strip() == "true":
            return None
        return (
            f"container `{self._container}` is not running "
            f"(docker exec exited {result.exit_code})"
        )

    async def _guest_call(
        self: object,
        *args: str,
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> DockerExecOutput:
        """Run one guest command, telling a docker failure apart from a guest exit code.

        Raises:
            SandboxRuntimeError: If the call times out (the container is reaped first) or
                docker itself failed rather than the guest command.
        """
        self._require_alive()
        try:
            result = await _docker(
                *args,
                stdin=stdin,
                timeout=timeout,
                description=f"`docker exec {self._container}`",
            )
        except DockerCallTimeout as error:
            await self._reap()
            raise SandboxRuntimeError(
                f"guest command in container `{self._container}` timed out after "
                f"{timeout}s; the container was removed to reap it"
            ) from error
        failure = await self._control_plane_failure(result)
        if failure is not None:
            self._alive = False
            raise SandboxRuntimeError(failure)
        return DockerExecOutput(result.exit_code, result.stdout, result.stderr)

    async def shell(
        self: object, script: str, *, env: dict | None = None, cwd: str | None = None
    ) -> DockerExecOutput:
        """Run `script` under `/bin/sh -c` inside the container."""
        return await self._guest_call(
            *self._exec_args(cwd=cwd, env=env), "/bin/sh", "-c", script
        )

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
        return await self._guest_call(
            *self._exec_args(cwd=cwd, env=env),
            command,
            *(args or []),
            stdin=stdin,
            timeout=timeout,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (39 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): run guest commands through docker exec, reaping on timeout"
```

---

### Task 9: `DockerSandbox.exec_stream`, `.kill()`, and `.stop()`

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `DockerStreamEvent`, `DockerExecStream`; add `exec_stream`/`stop` to `DockerSandbox`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: `DockerStreamEvent(event_type, data, code)`, `DockerExecStream(process)` (async-iterable, `.kill()`, `.stderr_tail`), `DockerSandbox.exec_stream(...)`, `DockerSandbox.stop(timeout=None)`.
- Consumes: `_docker_stream`, `_remove_container` (Task 8), `GUEST_WORKDIR`.
- **Stderr must be drained concurrently.** `_docker_stream` pipes all three streams. A piped stderr that nobody reads stalls the guest the moment it writes past one pipe buffer (64 KiB on Linux): the guest blocks on `write(2)`, stdout stops arriving, and the routing turn deadlocks until its own timeout. `DockerExecStream` therefore drains stderr on a concurrent task from first use, retaining a bounded tail for diagnostics and discarding the rest.
- **`stop()` is the ownership-restoring teardown.** It routes through Task 8's `_remove_container`, honors the caller's `timeout`, checks the returned outcome, and raises `SandboxRuntimeError` on a genuine daemon failure. On a Linux host it first chowns `/workspace` back to the host user, because the container ran as root.
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


class FakeStderr:
    """A stderr pipe that hands back one large canned payload in pipe-sized chunks."""

    def __init__(self: object, payload: bytes) -> None:
        """Queue the bytes this pipe will yield before EOF."""
        self._payload = payload
        self.exhausted = False

    async def read(self: object, limit: int) -> bytes:
        """Return up to `limit` bytes, then b"" and a record that the pipe emptied."""
        chunk, self._payload = self._payload[:limit], self._payload[limit:]
        if not chunk:
            self.exhausted = True
        return chunk


class FakeProcess:
    """A `docker exec` process double recording kills and returning a canned exit code."""

    def __init__(self: object, chunks: list, code: int = 0, stderr: bytes | None = None) -> None:
        """Wire the stdout chunks, optional stderr payload, and the reported exit code."""
        self.stdout = FakeStdout(chunks)
        self.stderr = FakeStderr(stderr) if stderr is not None else None
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


def test_exec_stream_drains_stderr_larger_than_a_pipe_buffer() -> None:
    """A guest that floods stderr must not stall the stream, and the tail stays bounded.

    `_docker_stream` pipes stderr. A piped stderr that nobody reads blocks the guest the
    moment it writes past one pipe buffer (64 KiB on Linux): stdout stops arriving and the
    routing turn deadlocks until its own timeout. The payload here is four buffers' worth,
    so a stream that only drains stdout cannot pass this test.
    """
    process = FakeProcess([b"out\n"], code=0, stderr=b"E" * (256 * 1024))
    stream = docker_mod.DockerExecStream(process)

    async def drain() -> list:
        """Collect every event the stream yields."""
        return [event async for event in stream]

    events = asyncio.run(drain())

    assert [event.event_type for event in events] == ["stdout", "exited"]
    assert process.stderr.exhausted is True
    assert len(stream.stderr_tail) == docker_mod.DockerExecStream._STDERR_TAIL_BYTES


def test_exec_stream_spawns_docker_exec_with_stdin_closed(monkeypatch: object) -> None:
    """The streaming spawn carries the same exec arguments and forces EOF on stdin."""
    calls: list = []

    async def fake_stream(
        *args: str, stdin: object = None, description: object = None
    ) -> object:
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


def test_stop_removes_the_container_within_the_callers_timeout(monkeypatch: object) -> None:
    """Stopping a Docker session removes the container — the analogue of a VM stop."""
    calls = _record_docker(monkeypatch, DockerResult(("rm",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(sandbox.stop(timeout=5))

    assert calls[0]["args"] == ("rm", "-f", "eval-hello-alpha-main")
    assert calls[0]["timeout"] == 5
    assert sandbox.alive is False


def test_stop_raises_when_the_daemon_refuses_the_removal(monkeypatch: object) -> None:
    """A removal the daemon rejected is a runtime failure, not a silent leak.

    `stop_quietly` is the caller that chooses to swallow this; `stop` itself must report
    it, or a container leaked by a dying daemon would go unnoticed until the disk filled.
    """
    _record_docker(
        monkeypatch,
        DockerResult(("rm",), 1, "", "Cannot connect to the Docker daemon at unix:///..."),
    )
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="Cannot connect"):
        asyncio.run(sandbox.stop())


def test_stop_restores_workspace_ownership_before_removing_the_container(
    monkeypatch: object,
) -> None:
    """On a Linux host the guest hands `/workspace` back before the container goes away.

    The container runs as `--user 0:0`, so under rootful Docker every file the agent wrote
    into the bind-mounted clean room is root-owned. The host then cannot read facts out of
    it, and `TemporaryDirectory` cleanup fails. The chown must happen while the container
    still exists, hence "before removing".
    """
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main", restore_owner="501:20")

    asyncio.run(sandbox.stop())

    assert calls[0]["args"][-3:] == ("/bin/sh", "-c", "chown -R 501:20 /workspace")
    assert calls[1]["args"] == ("rm", "-f", "eval-hello-alpha-main")


def test_stop_without_a_restore_owner_skips_the_chown(monkeypatch: object) -> None:
    """macOS maps ownership in Docker Desktop's file-sharing layer, so no chown runs."""
    calls = _record_docker(monkeypatch, DockerResult(("rm",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(sandbox.stop())

    assert [call["args"][0] for call in calls] == ["rm"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "stream or stop" -v`
Expected: FAIL — `AttributeError: module 'harnessbench.sandbox.docker' has no attribute 'DockerExecStream'`.

- [ ] **Step 3: Write minimal implementation**

Add `import contextlib` to the stdlib import block and `GUEST_WORKDIR` to the
`from harnessbench.sandbox.primitives import ...` line, then add above `DockerSandbox`:

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
    """An async iterator over a running `docker exec`'s stdout, ending with its exit code.

    Stderr is drained on a concurrent task rather than left in the pipe. `_docker_stream`
    pipes all three streams, and a piped stderr nobody reads blocks the guest the moment
    it writes past one pipe buffer (64 KiB on Linux): stdout stops arriving and the turn
    deadlocks until its timeout. Only a bounded tail is retained, for diagnostics.
    """

    _CHUNK_BYTES = 65536
    # Enough stderr to explain a failure; the rest is discarded so a chatty guest cannot
    # grow the host's memory for the length of a turn.
    _STDERR_TAIL_BYTES = 8192

    def __init__(self: object, process: object) -> None:
        """Wrap the live `docker exec` process whose stdout is drained."""
        self._process = process
        self._finished = False
        self._stderr_tail = bytearray()
        self._stderr_drain = None

    @property
    def stderr_tail(self: object) -> str:
        """The retained tail of the guest's stderr, for diagnosing a failed stream."""
        return self._stderr_tail.decode("utf-8", errors="replace")

    def _start_stderr_drain(self: object) -> None:
        """Start the stderr drain on first use.

        Deferred to first use rather than done in `__init__` so constructing a stream
        outside a running event loop stays legal.
        """
        if self._stderr_drain is None and getattr(self._process, "stderr", None) is not None:
            self._stderr_drain = asyncio.ensure_future(self._drain_stderr())

    async def _drain_stderr(self: object) -> None:
        """Keep stderr empty, retaining only a bounded tail."""
        while True:
            chunk = await self._process.stderr.read(self._CHUNK_BYTES)
            if not chunk:
                return
            self._stderr_tail.extend(chunk)
            del self._stderr_tail[: -self._STDERR_TAIL_BYTES]

    async def _finish_stderr_drain(self: object) -> None:
        """Wait out the stderr drain, tolerating a cancelled or already-closed pipe."""
        if self._stderr_drain is None:
            return
        drain, self._stderr_drain = self._stderr_drain, None
        with contextlib.suppress(asyncio.CancelledError, OSError, ValueError):
            await drain

    def __aiter__(self: object) -> DockerExecStream:
        """Iterate this stream's own events."""
        return self

    async def __anext__(self: object) -> DockerStreamEvent:
        """Yield the next stdout chunk, then exactly one terminal `exited` event."""
        if self._finished:
            raise StopAsyncIteration
        self._start_stderr_drain()
        chunk = b""
        if self._process.stdout is not None:
            chunk = await self._process.stdout.read(self._CHUNK_BYTES)
        if chunk:
            return DockerStreamEvent("stdout", data=chunk)
        self._finished = True
        code = await self._process.wait()
        await self._finish_stderr_drain()
        return DockerStreamEvent("exited", code=code)

    async def kill(self: object) -> None:
        """Terminate the `docker exec` client process.

        WARNING: this kills the local client, not the process inside the container. The
        caller (`_route_in_sandbox_async`) removes the container immediately afterwards
        via `stop_quietly`, and that removal is what actually reaps the guest process.
        """
        if self._stderr_drain is not None:
            self._stderr_drain.cancel()
        if self._process.returncode is None:
            self._process.kill()
            await self._process.wait()
        await self._finish_stderr_drain()
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
        self._require_alive()
        process = await _docker_stream(
            *self._exec_args(cwd=cwd, env=env),
            command,
            *(args or []),
            stdin=stdin,
            description=f"`docker exec {self._container}`",
        )
        return DockerExecStream(process)

    async def stop(self: object, timeout: float | None = None) -> None:
        """Restore host ownership of the workspace, then remove the container.

        A container is disposable, so removal — not a stop — is the Docker analogue of a
        microVM stop. The chown runs first because it needs the container to still exist:
        the guest ran as root, so under rootful Docker every file it wrote into the
        bind-mounted clean room is root-owned, and the host can then neither gather facts
        from it nor delete the temporary directory holding it.

        Args:
            timeout: Seconds to allow the removal, defaulting to `REMOVE_TIMEOUT_SECONDS`.

        Raises:
            SandboxRuntimeError: If the daemon refused or wedged the removal. Callers that
                must not fail on teardown use `DockerBackend.stop_quietly`.
        """
        if self._restore_owner is not None and self._alive:
            # Best effort: a chown failure must not stop the container from being removed.
            with contextlib.suppress(SandboxRuntimeError, TimeoutError, OSError):
                await self.shell(f"chown -R {self._restore_owner} {GUEST_WORKDIR}")
        self._alive = False
        outcome = await _remove_container(
            self._container, timeout=timeout or REMOVE_TIMEOUT_SECONDS
        )
        if outcome.error is not None:
            raise SandboxRuntimeError(outcome.error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (48 tests).

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
- Produces: `DockerBackend.build_snapshot(agent, name, env) -> None`; module-level `OWNER_LABEL_FORMAT`, `build_container_name(agent_id)`, `_replace_container(name)`.
- Consumes: `_docker`, `_remove_container`, `DockerSandbox`, `container_name`, `image_ref`, `_resource_flags`, `_label_flags`, `primitives.{BASE_IMAGE, bridge_skills_home, run_environment_script}`, `agent.provision`.
- Mirrors `MicrosandboxBackend._build_snapshot_async` (`sandbox/backend.py:260-279`) step for step: replace any stale build container → run base → provision → bridge → environment script → seal → always remove the build container.
- **The build container name is scoped to the repo root.** `build_snapshot` carries no repo-root argument, so the name folds in an 8-character hash of the resolved working directory. Without it, two checkouts building a snapshot for the same harness at the same time share one name and the pre-start `rm -f` in one destroys the other's build mid-provision. (The snapshot file lock in `ensure_snapshot` serializes builds *within* one repo, not across repos.)
- **Removal is label-gated.** The pre-start replace runs through `_replace_container`, which inspects `harnessbench.owner` first and refuses — loudly — to remove a container harnessbench did not start.
- **Provisioning failures are re-raised at the neutral type.** `agent.provision`, `bridge_skills_home`, and `run_environment_script` raise bare `RuntimeError`s, which `__main__._run_sandbox_build` maps to exit `2` (a usage error). A build that genuinely failed is exit `1`, so this boundary re-raises them as `SandboxRuntimeError` with the original chained. Task 13's branch order does the rest.

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


def _fake_build_docker(monkeypatch: object, stdout: str = "") -> list:
    """Fake the async seam for a build: no stale container exists, everything succeeds."""
    calls: list = []

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Record every docker call, reporting no pre-existing container."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: build")
        return DockerResult(args, 0, stdout, "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    return calls


def test_build_container_name_is_scoped_to_the_repo_root(
    monkeypatch: object, tmp_path: object
) -> None:
    """Two checkouts building one harness's snapshot never share a build container.

    `build_snapshot` carries no repo-root argument, so the name folds in a hash of the
    resolved working directory. Sharing one name would let the pre-start replace in one
    checkout destroy the other checkout's build container mid-provision — and the snapshot
    file lock only serializes builds *within* a repo.
    """
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    from_first = docker_mod.build_container_name("claude-code")
    monkeypatch.chdir(second)
    from_second = docker_mod.build_container_name("claude-code")

    assert from_first.startswith("harnessbench-build-claude-code-")
    assert from_first != from_second


def test_build_snapshot_runs_provision_bridge_script_then_commits(monkeypatch: object) -> None:
    """The Docker build mirrors the microsandbox build and seals with `docker commit`."""
    calls = _fake_build_docker(monkeypatch, stdout="sha256:committed\n")
    build_container = docker_mod.build_container_name("claude-code")
    agent = RecordingAgent()
    env = EnvConfig(script=b"apt-get install -y jq\n", script_path="s.sh")

    docker_mod.DockerBackend().build_snapshot(
        agent, "harnessbench-docker-claude-code-latest-ab12cd34", env
    )

    assert calls[0] == ("inspect", "--format", docker_mod.OWNER_LABEL_FORMAT, build_container)
    assert calls[1] == (
        "run", "--detach", "--name", build_container,
        "--user", "0:0",
        "--cpus", "2", "--memory", "2048m",
        "--label", "harnessbench.owner=harnessbench",
        "--label", f"harnessbench.run={docker_mod.RUN_NONCE}",
        "--entrypoint", "sleep", "ubuntu:latest", "infinity",
    )
    assert agent.provisioned == [build_container]
    assert calls[-2] == (
        "commit",
        build_container,
        "harnessbench-docker-claude-code-latest-ab12cd34-0d462a07",
    )
    assert calls[-1] == ("rm", "-f", build_container)


def test_build_snapshot_uses_the_declared_base_image(monkeypatch: object) -> None:
    """A declared base_image replaces the default in the build container's run."""
    calls = _fake_build_docker(monkeypatch)

    docker_mod.DockerBackend().build_snapshot(
        RecordingAgent(), "snap", EnvConfig(base_image="python:3.12-slim")
    )

    assert "python:3.12-slim" in calls[1]


def test_build_snapshot_refuses_to_remove_a_container_it_does_not_own(
    monkeypatch: object,
) -> None:
    """A same-named container without harnessbench's label is reported, never destroyed.

    `docker rm -f` on a name collision is indistinguishable from `docker rm -f` on
    somebody's long-running work. The owner label is the only thing that tells them apart,
    so an unlabelled container stops the build instead of being removed.
    """

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Report an existing container that carries no harnessbench owner label."""
        if args[0] == "inspect":
            return DockerResult(args, 0, "<no value>\n", "")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)

    with pytest.raises(docker_mod.SandboxRuntimeError, match="not owned by harnessbench"):
        docker_mod.DockerBackend().build_snapshot(RecordingAgent(), "snap", EnvConfig())


def test_build_snapshot_raises_a_neutral_error_when_the_base_run_fails(
    monkeypatch: object,
) -> None:
    """A base image that cannot start is a loud runtime failure, and leaves nothing behind.

    `docker run --detach` can leave a created-but-not-started container behind when it
    exits nonzero, so the failure path attempts a removal before it raises.
    """
    calls: list = []

    async def failing_run(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Report no stale container, fail the run, succeed on cleanup."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: build")
        if args[0] == "run":
            return DockerResult(args, 125, "", "Unable to find image 'nope:latest' locally")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", failing_run)
    build_container = docker_mod.build_container_name("claude-code")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="Unable to find image"):
        docker_mod.DockerBackend().build_snapshot(
            RecordingAgent(), "snap", EnvConfig(base_image="nope:latest")
        )

    assert calls[-1] == ("rm", "-f", build_container)


def test_build_snapshot_reports_a_failed_provision_as_a_sandbox_runtime_error(
    monkeypatch: object,
) -> None:
    """A failed provision tears the container down AND reports at the neutral error type.

    `provision` raises a bare RuntimeError, which `__main__` maps to exit 2 — a usage
    error. A build that genuinely failed is exit 1, so the Docker build boundary re-raises
    it as SandboxRuntimeError with the original chained.
    """
    calls = _fake_build_docker(monkeypatch)
    build_container = docker_mod.build_container_name("claude-code")

    class ExplodingAgent(RecordingAgent):
        """An agent whose CLI install fails mid-build."""

        async def provision(self: object, sandbox: object) -> None:
            """Fail the way a broken installer does."""
            raise RuntimeError("claude-code provision failed (exit 1)")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="provision failed") as raised:
        docker_mod.DockerBackend().build_snapshot(ExplodingAgent(), "snap", EnvConfig())

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert calls[-1] == ("rm", "-f", build_container)
    assert not any(call[0] == "commit" for call in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k build_snapshot -v`
Expected: FAIL — `AttributeError: 'DockerBackend' object has no attribute 'build_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Add `from pathlib import Path` to the stdlib import block, add `BASE_IMAGE`, `bridge_skills_home`, and `run_environment_script` to the `from harnessbench.sandbox.primitives import ...` line, and add the label format next to the other module-level constants:

```python
# Prints the owner label, or `<no value>` when the container carries none.
OWNER_LABEL_FORMAT = '{{index .Config.Labels "' + OWNER_LABEL + '"}}'
```

Add the two module-level helpers next to `_remove_container`:

```python
def build_container_name(agent_id: str) -> str:
    """Return the name of the throwaway container a snapshot is provisioned in.

    Folds in a short hash of the resolved working directory — harnessbench's repo root.
    `build_snapshot` carries no repo-root argument, and the snapshot file lock in
    `ensure_snapshot` only serializes builds within one repo, so two checkouts building a
    snapshot for the same harness at the same time would otherwise share one name and the
    pre-start replace in one would destroy the other's build mid-provision.
    """
    root_digest = hashlib.sha256(str(Path.cwd().resolve()).encode("utf-8")).hexdigest()[:8]
    return container_name(f"harnessbench-build-{agent_id}-{root_digest}")


async def _replace_container(name: str) -> None:
    """Free a container name, but only by removing a container harnessbench started.

    `replace=True` semantics: a container left behind by an aborted run must not collide
    with this one. A container harnessbench did NOT start is never removed — `docker rm -f`
    on a name collision is indistinguishable from `docker rm -f` on somebody's running
    work, and the owner label is the only thing that tells them apart.

    Raises:
        SandboxRuntimeError: If the name is held by a container harnessbench does not own,
            or the daemon could not answer, or the removal failed.
    """
    owner = await _docker(
        "inspect", "--format", OWNER_LABEL_FORMAT, name, description=f"`docker inspect {name}`"
    )
    if owner.exit_code != 0:
        if _CONTAINER_ABSENT_PATTERN.search(owner.stderr):
            return
        raise SandboxRuntimeError(
            f"docker inspect for `{name}` exited {owner.exit_code}: "
            f"{owner.stderr.strip()[-500:] or '(no output)'}"
        )
    if owner.stdout.strip() != OWNER_LABEL_VALUE:
        raise SandboxRuntimeError(
            f"container `{name}` already exists and is not owned by harnessbench — "
            f"remove it yourself or free the name; harnessbench will not delete it"
        )
    outcome = await _remove_container(name)
    if outcome.error is not None:
        raise SandboxRuntimeError(outcome.error)
```

Then append to `DockerBackend`:

```python
    def build_snapshot(self: object, agent: object, name: str, env: EnvConfig) -> None:
        """Provision a container from the base image and commit it as the snapshot image."""
        asyncio.run(self._build_snapshot_async(agent, name, env))

    async def _build_snapshot_async(
        self: object, agent: object, name: str, env: EnvConfig
    ) -> None:
        """Provision and seal the reusable snapshot image asynchronously.

        Every failure leaves at the neutral error type, which `__main__` maps to exit 1:
        a build that failed is a finding, not a usage error.

        Raises:
            SandboxRuntimeError: If the name is held by a foreign container, the base image
                cannot start, a provisioning step fails, or the commit fails.
        """
        base_image = env.base_image or BASE_IMAGE
        build_container = build_container_name(agent.id)
        await _replace_container(build_container)
        started = await _docker(
            "run",
            "--detach",
            "--name",
            build_container,
            # Agents install their CLIs under /root and run with bypassPermissions: the
            # sandbox, not the uid, is the containment boundary.
            "--user",
            "0:0",
            *_resource_flags(),
            *_label_flags(),
            # An explicit entrypoint keeps the container alive regardless of what the
            # base image declares, so provisioning has something to exec into.
            "--entrypoint",
            "sleep",
            base_image,
            "infinity",
            description=f"`docker run {base_image}`",
        )
        if started.exit_code != 0:
            # A nonzero `run --detach` can still leave a created container behind.
            await _remove_container(build_container)
            raise SandboxRuntimeError(
                f"docker run failed for base image `{base_image}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        sandbox = DockerSandbox(build_container)
        try:
            try:
                await agent.provision(sandbox)
                await bridge_skills_home(sandbox, agent)
                await run_environment_script(sandbox, agent, env)
            except SandboxRuntimeError:
                raise
            except RuntimeError as error:
                # These three raise bare RuntimeErrors, which __main__ maps to exit 2 (a
                # usage error). A build that genuinely failed is exit 1.
                raise SandboxRuntimeError(
                    f"docker snapshot build failed for `{name}`: {error}"
                ) from error
            committed = await _docker(
                "commit",
                build_container,
                image_ref(name),
                description=f"`docker commit {build_container}`",
            )
            if committed.exit_code != 0:
                raise SandboxRuntimeError(
                    f"docker commit failed for `{name}` (exit {committed.exit_code}): "
                    f"{committed.stderr.strip()[-2000:]}"
                )
        finally:
            # `_remove_container` never raises, so teardown can never mask a build failure.
            await _remove_container(build_container)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`
Expected: PASS (54 tests).

- [ ] **Step 5: Commit**

```bash
git add src/harnessbench/sandbox/docker.py tests/sandbox/test_docker.py
git commit -m "feat(sandbox): build docker snapshots by provisioning and committing a container"
```

---

### Task 11: `DockerBackend` session creation and the quiet helpers

**Files:**
- Modify: `src/harnessbench/sandbox/docker.py` (add `create_sandbox`, `create_trigger_sandbox`, `_run_container`, `_credential_env_file`, `_workspace_restore_owner`, `guest_shell`, `stop_quietly`, `kill_quietly`)
- Test: `tests/sandbox/test_docker.py` (append)

**Interfaces:**
- Produces: the remaining `SandboxBackend` protocol methods on `DockerBackend`.
- Consumes: `sandbox._agent_extra_volumes` (passed in as `extra_volumes`), `agent.secrets()` → `GuestCredential`s, `primitives.{GUEST_WORKDIR, PROJECT_MOUNT, host_mount_path}`, `_replace_container`, `_remove_container`, `_label_flags`.
- After this task `isinstance(DockerBackend(), SandboxBackend)` is True — the protocol is `runtime_checkable`.
- **Credentials go in through an `--env-file`, never `-e`.** `-e NAME=value` puts the token in the container's argv, where every user on the host reads it out of `ps`, and `docker inspect` echoes it back for as long as the container exists. Instead the values are written to a mode-`0600` temporary file that is deleted the moment `docker run` returns — the daemon has already copied them into the container's environment by then. The value is still readable *inside* the guest, which is the documented cost of choosing `sandbox = "docker"`; what this removes is the leak to the *host*.
- **`create_trigger_sandbox` exists for contract completeness.** `_route_in_sandbox_async` is not reached under Docker in this change — trigger routing stays microsandbox-only (Task 15 says so in the docs). The method is implemented and tested because `SandboxBackend` is `runtime_checkable` and a partial implementation would fail `isinstance`, and because leaving it unimplemented would make the eventual routing work a rewrite rather than a switch.
- **Every failed start cleans up.** `docker run --detach` that exits nonzero can leave a created container behind, so the failure path attempts a bounded `_remove_container` before it raises — through `try`/`finally` with the original exception chained, never masked.

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


def _fake_run_docker(monkeypatch: object) -> list:
    """Fake the async seam for a container start: no stale container, everything succeeds."""
    calls: list = []

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Record every docker call, reporting no pre-existing container."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: eval")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake_docker)
    return calls


def test_create_sandbox_mounts_workspace_and_project_and_labels_the_container(
    monkeypatch: object, tmp_path: object,
) -> None:
    """An arm container gets a writable workspace, a read-only project, and owner labels."""
    calls = _fake_run_docker(monkeypatch)
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
    assert calls[0] == (
        "inspect", "--format", docker_mod.OWNER_LABEL_FORMAT, "eval-hello-alpha-main"
    )
    assert "-v" in run_args
    assert f"{workdir.resolve()}:/workspace" in run_args
    assert f"{stage.resolve()}:/project:ro" in run_args
    assert "harnessbench.owner=harnessbench" in run_args
    assert f"harnessbench.run={docker_mod.RUN_NONCE}" in run_args
    assert ("--user", "0:0") == run_args[4:6]
    assert run_args[-4:] == (
        "--entrypoint",
        "sleep",
        "harnessbench-docker-claude-code-latest-ab12cd34-0d462a07",
        "infinity",
    )
    assert sandbox.container == "eval-hello-alpha-main"


def test_create_sandbox_never_puts_a_credential_in_the_container_argv(
    monkeypatch: object, tmp_path: object,
) -> None:
    """The token reaches the container through a 0600 env-file, never through `-e`.

    `-e NAME=value` is visible to every user on the host in `ps` output and is echoed back
    by `docker inspect` for the container's whole life. The env-file is mode 0600 and is
    deleted the moment `docker run` returns — the daemon has already copied the values into
    the container by then.
    """
    seen: dict = {}

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Read the env-file while it still exists, recording its content and mode."""
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: eval")
        if args[0] == "run":
            env_file = Path(args[args.index("--env-file") + 1])
            seen["args"] = args
            seen["content"] = env_file.read_text(encoding="utf-8")
            seen["mode"] = env_file.stat().st_mode & 0o777
            seen["path"] = env_file
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

    assert not any("sk-test-value" in argument for argument in seen["args"])
    assert seen["content"] == "ANTHROPIC_API_KEY=sk-test-value\n"
    assert seen["mode"] == 0o600
    assert seen["path"].exists() is False


def test_create_sandbox_keeps_the_credential_out_of_the_failure_message(
    monkeypatch: object, tmp_path: object,
) -> None:
    """A start failure reports the snapshot, never the argv that carried the credential.

    This message becomes `<sandbox-error>` result text, which is written to run artifacts.
    """

    async def failing_run(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Report no stale container, then fail the run."""
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: eval")
        if args[0] == "run":
            return DockerResult(args, 125, "", "No such image: snap")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", failing_run)

    with pytest.raises(docker_mod.SandboxRuntimeError) as raised:
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

    assert "sk-test-value" not in str(raised.value)
    assert "--env-file" not in str(raised.value)


def test_create_sandbox_restores_workspace_ownership_on_linux(
    monkeypatch: object, tmp_path: object,
) -> None:
    """On Linux the session carries the host uid:gid it must chown `/workspace` back to.

    Rootful Docker on Linux writes root-owned files into the bind-mounted clean room; the
    host then cannot gather facts from it or delete it. macOS maps ownership itself.
    """
    _fake_run_docker(monkeypatch)
    monkeypatch.setattr(docker_mod.sys, "platform", "linux")
    monkeypatch.setattr(docker_mod.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(docker_mod.os, "getgid", lambda: 1000, raising=False)

    sandbox = asyncio.run(
        docker_mod.DockerBackend().create_sandbox(
            agent=CredentialAgent(),
            snapshot="snap",
            name="eval-hello-alpha-main",
            host_workdir=tmp_path,
            host_repo_root=None,
            extra_volumes=lambda agent, volume_cls: {},
        )
    )

    assert sandbox._restore_owner == "1000:1000"


def test_create_sandbox_omits_the_project_mount_when_there_is_no_stage(
    monkeypatch: object, tmp_path: object,
) -> None:
    """With no staged project there is no `/project` mount at all."""
    calls = _fake_run_docker(monkeypatch)

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
    calls = _fake_run_docker(monkeypatch)
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
    """The routing container mounts the stage read-only and stages the agent's assets.

    Trigger routing itself stays microsandbox-only in this change; this method exists so
    `DockerBackend` satisfies the runtime-checkable protocol in full.
    """
    _fake_run_docker(monkeypatch)
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


def test_create_sandbox_cleans_up_after_a_container_that_will_not_start(
    monkeypatch: object, tmp_path: object,
) -> None:
    """A container that cannot boot is a runtime failure — and leaves nothing behind.

    `docker run --detach` can leave a created-but-not-started container when it exits
    nonzero, so the failure path attempts a removal before it raises.
    """
    calls: list = []

    async def failing_run(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Report no stale container, fail the run, succeed on cleanup."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: eval")
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

    assert calls[-1] == ("rm", "-f", "eval-hello-alpha-main")


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

(`pathlib.Path` is already imported at the top of the file for the AST test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -k "create_ or guest_shell or quietly or protocol" -v`
Expected: FAIL — `AttributeError: 'DockerBackend' object has no attribute 'create_sandbox'`.

- [ ] **Step 3: Write minimal implementation**

Add `import os`, `import sys`, and `import tempfile` to the stdlib imports (`contextlib` and `GUEST_WORKDIR` arrived in Task 9) and `PROJECT_MOUNT`, `host_mount_path` to the `primitives` import line. Add the two module-level helpers next to `_volume_flags`:

```python
@contextlib.contextmanager
def _credential_env_file(agent: object) -> object:
    """Yield `docker run` flags that carry the agent's credentials out of band.

    Docker has no equivalent of microsandbox's network-scoped secret substitution, so
    `allow_hosts` cannot be enforced and the value IS readable inside the guest. That much
    is the documented cost of selecting `sandbox = "docker"`.

    What is NOT acceptable is leaking the value to the HOST. `-e NAME=value` puts the
    token in the container's argv, where any user on the machine reads it out of `ps` and
    where `docker inspect` echoes it back for as long as the container exists. So the
    values go into a mode-0600 file that is deleted as soon as `docker run` returns — by
    then the daemon has copied them into the container's environment.

    Args:
        agent: The agent whose `secrets()` are rendered.

    Yields:
        The `--env-file` flags, or an empty list when the agent declares no credentials.
    """
    credentials = agent.secrets()
    if not credentials:
        yield []
        return
    handle, path = tempfile.mkstemp(prefix="harnessbench-credentials-")
    try:
        os.fchmod(handle, 0o600)
        # docker's env-file format is one NAME=value per line, unquoted and unescaped.
        with os.fdopen(handle, "w", encoding="utf-8") as env_file:
            for credential in credentials:
                env_file.write(f"{credential.env_name}={credential.value}\n")
        yield ["--env-file", path]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _workspace_restore_owner() -> str | None:
    """Return the `uid:gid` a guest must chown `/workspace` back to, or None.

    Containers run as root. Under rootful Docker on Linux that means every file the agent
    writes into the bind-mounted clean room is root-owned, and the host can then neither
    gather facts from it nor delete the temporary directory holding it. On macOS, Docker
    Desktop maps ownership in its own file-sharing layer, so nothing needs restoring.
    """
    if sys.platform != "linux":
        return None
    return f"{os.getuid()}:{os.getgid()}"
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
            snapshot=snapshot,
            name=name,
            agent=agent,
            volumes=volumes,
            restore_owner=_workspace_restore_owner(),
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
        """Boot the container used for trigger-routing probes.

        Trigger routing is microsandbox-only today; this exists so `DockerBackend`
        satisfies the runtime-checkable `SandboxBackend` protocol in full, and so enabling
        routing under Docker later is a switch rather than a rewrite. There is no
        `/workspace` bind here, so no ownership to restore.
        """
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
        self: object,
        *,
        snapshot: object,
        name: object,
        agent: object,
        volumes: dict,
        restore_owner: str | None = None,
    ) -> DockerSandbox:
        """Free the container name, then run a fresh container from the snapshot image.

        Raises:
            SandboxRuntimeError: If the name is held by a container harnessbench does not
                own, or the container fails to start.
        """
        container = container_name(str(name))
        # `replace=True` semantics: a name collision left by an earlier aborted cell must
        # not fail this one. Label-gated, so a container harnessbench did not start is
        # reported rather than removed.
        await _replace_container(container)
        reference = image_ref(str(snapshot))
        with _credential_env_file(agent) as credential_flags:
            started = await _docker(
                "run",
                "--detach",
                "--name",
                container,
                # Agents install their CLIs under /root and run with bypassPermissions:
                # the sandbox, not the uid, is the containment boundary.
                "--user",
                "0:0",
                *_resource_flags(),
                *_label_flags(),
                *_volume_flags(volumes),
                *credential_flags,
                "--entrypoint",
                "sleep",
                reference,
                "infinity",
                # Never the argv: it holds the --env-file path.
                description=f"`docker run {reference}`",
            )
        if started.exit_code != 0:
            # A nonzero `run --detach` can still leave a created container behind. This
            # helper never raises, so it cannot mask the failure being reported.
            await _remove_container(container)
            raise SandboxRuntimeError(
                f"docker run failed for snapshot `{snapshot}` "
                f"(exit {started.exit_code}): {started.stderr.strip()[-2000:]}"
            )
        return DockerSandbox(container, restore_owner=restore_owner)

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
- **Landmine 1:** `SandboxRuntimeError` IS a `RuntimeError`, so its `except` branch must come FIRST. Reversed, every Docker build failure would report as a config/usage error (exit `2`) instead of a build failure (exit `1`).
- **Landmine 2:** the fallback branch's `from microsandbox.errors import MicrosandboxError` is only safe *after a microsandbox preflight passed*. On a Docker-only install where microsandbox is not present, any non-`RuntimeError` escaping a Docker build would hit that import and raise `ModuleNotFoundError` — masking the real error with a bogus missing-package message. The import is guarded with `contextlib.suppress(ImportError)`, and a test pins it.

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


def test_sandbox_build_unexpected_error_survives_a_missing_microsandbox(
    monkeypatch: object,
) -> None:
    """On a Docker-only install the fallback branch must not mask the real error.

    The last `except` reaches for `microsandbox.errors` to classify a native microsandbox
    failure. With the package absent — a perfectly ordinary Docker-only install — an
    unguarded import raises ModuleNotFoundError, and the operator sees a bogus
    missing-package message instead of what actually broke.
    """

    def failing_cli_build(
        repo_root: object, *, set_name: object = None, config: object = None
    ) -> None:
        """Raise something the exit-code mapping does not recognize."""
        raise ValueError("something unexpected broke")

    monkeypatch.setattr(__main__.sandbox, "cli_build", failing_cli_build)
    monkeypatch.setitem(sys.modules, "microsandbox", None)
    monkeypatch.setitem(sys.modules, "microsandbox.errors", None)

    with pytest.raises(ValueError, match="something unexpected broke"):
        __main__.main(["sandbox:build", "some/dir", "--set", "dock"])
```

Add `from harnessbench.sandbox.errors import SandboxRuntimeError` and (if absent) `import sys` and `import pytest` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/test_main.py -k "docker_runtime_failure or missing_microsandbox" -v`
Expected: FAIL — `assert 2 == 1` for the first (the `except RuntimeError` branch swallowed it as USAGE), and `ModuleNotFoundError: import of microsandbox halted` for the second.

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
        # Guarded: on a Docker-only install microsandbox is not installed at all, and an
        # unguarded import here would raise ModuleNotFoundError over whatever actually
        # went wrong.
        with contextlib.suppress(ImportError):
            from microsandbox.errors import MicrosandboxError

            if isinstance(error, MicrosandboxError):
                print(f"error: {error}", file=sys.stderr)
                return ExitCode.FINDING
        raise
```

(add `import contextlib` to `__main__.py`'s stdlib imports if it is not already there).

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
- Consumes: `DockerBackend`, `DockerSandbox`, `_docker`, `image_ref`, `_label_flags`, `OWNER_LABEL`, `RUN_NONCE`, `primitives.BASE_IMAGE`. Nothing is faked here — this is the only file that talks to a real daemon.
- Skip contract: the whole module skips with the actual preflight error text when the daemon is unreachable, so `make test` on a Docker-less host reports skips with a readable reason, never failures.
- **The four cases only a real daemon can prove:** a container removed mid-exec (the killed-mid-turn path the unit suite can only simulate), stderr larger than a real 64 KiB pipe buffer, root-written files in a real bind mount, and — as the module's last test — that the suite leaked no harnessbench-labelled containers.

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
import os
import sys
import uuid

import pytest

from harnessbench.sandbox.docker import (
    OWNER_LABEL,
    OWNER_LABEL_VALUE,
    RUN_NONCE,
    DockerBackend,
    DockerSandbox,
    SandboxRuntimeError,
    _docker,
    _label_flags,
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


async def _start_container(name: str, *, volumes: list | None = None) -> DockerSandbox:
    """Run a detached, labelled base-image container and return a sandbox wrapping it.

    The labels match what the backend itself applies, so the leak check at the end of this
    module sees these containers exactly as it would see a real run's.
    """
    started = await _docker(
        "run",
        "--detach",
        "--name",
        name,
        "--user",
        "0:0",
        *_label_flags(),
        *(volumes or []),
        "--entrypoint",
        "sleep",
        BASE_IMAGE,
        "infinity",
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


def test_a_container_removed_mid_exec_raises_a_sandbox_runtime_error() -> None:
    """Killing the container under a running exec is infra failure, not a graded miss.

    This is the killed-mid-turn path: an operator runs `docker rm -f`, or the daemon
    restarts, while an agent's turn is in flight. `docker exec` returns a nonzero code that
    looks exactly like a guest exit code, so only the classification makes this an errored
    arm rather than a silently failed eval.
    """
    container = _unique("harnessbench-test")

    async def exercise() -> None:
        """Start a long exec, remove the container underneath it, and see what surfaces."""
        sandbox = await _start_container(container)
        try:
            turn = asyncio.ensure_future(sandbox.exec("/bin/sh", ["-c", "sleep 30"], stdin=b""))
            await asyncio.sleep(1)
            await _docker("rm", "-f", container)
            await turn
        finally:
            await _docker("rm", "-f", container)

    with pytest.raises(SandboxRuntimeError):
        asyncio.run(exercise())


def test_exec_stream_survives_stderr_larger_than_a_real_pipe_buffer() -> None:
    """A guest flooding stderr through a REAL pipe must not stall the stdout stream.

    The unit suite proves the drain task exists; only a real 64 KiB pipe proves it prevents
    the deadlock. Without a concurrent stderr drain this test hangs rather than fails.
    """
    container = _unique("harnessbench-test")

    async def exercise() -> list:
        """Stream a command that writes a megabyte to stderr and a line to stdout."""
        sandbox = await _start_container(container)
        try:
            handle = await sandbox.exec_stream(
                "/bin/sh",
                ["-c", "head -c 1000000 /dev/zero | tr '\\0' 'E' >&2; echo done"],
                stdin=b"",
            )
            return [event async for event in handle]
        finally:
            await sandbox.stop()

    events = asyncio.run(asyncio.wait_for(exercise(), timeout=60))

    streamed = b"".join(event.data for event in events if event.event_type == "stdout")
    assert streamed.decode().strip() == "done"
    assert events[-1].code == 0


@pytest.mark.skipif(sys.platform != "linux", reason="rootful bind ownership is Linux-only")
def test_stop_hands_workspace_files_back_to_the_host_user(tmp_path: object) -> None:
    """After a root-running container stops, the host still owns its own clean room.

    Under rootful Docker on Linux, files the guest writes into the bind mount land as
    root-owned. The host then cannot read facts out of the workspace, and the
    TemporaryDirectory holding it fails to delete — so `stop()` chowns them back first.
    """
    container = _unique("harnessbench-test")
    workspace = tmp_path / "workdir"
    workspace.mkdir()

    async def exercise() -> None:
        """Write a file as root inside the guest, then stop the session."""
        sandbox = await _start_container(
            container, volumes=["-v", f"{workspace.resolve()}:/workspace"]
        )
        sandbox._restore_owner = f"{os.getuid()}:{os.getgid()}"
        written = await sandbox.shell("echo hi > /workspace/authored.txt")
        assert written.exit_code == 0, written.stderr_text
        await sandbox.stop()

    asyncio.run(exercise())

    assert (workspace / "authored.txt").read_text(encoding="utf-8").strip() == "hi"
    assert (workspace / "authored.txt").stat().st_uid == os.getuid()


def test_the_suite_leaked_no_harnessbench_containers() -> None:
    """Last test in the module: every container this suite started is gone.

    A leaked container holds its bind mounts, its memory reservation, and its name. Run
    last so it observes the whole module's teardown, and scoped to THIS process's run
    nonce so a concurrent harnessbench run on the same host cannot fail it.
    """
    listed = asyncio.run(
        _docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label={OWNER_LABEL}={OWNER_LABEL_VALUE}",
            "--filter",
            f"label=harnessbench.run={RUN_NONCE}",
        )
    )

    assert listed.exit_code == 0, listed.stderr
    assert listed.stdout.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

With no daemon: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker_daemon.py -v`
Expected: `9 skipped` with the reason `docker daemon not reachable: docker daemon unreachable (\`docker info\` exited 1) — start Docker and retry: ...`.

With a daemon running (`docker info` exits 0), the same command runs the tests for real. If `ubuntu:latest` is not present locally, pull it first: `docker pull ubuntu:latest`.

- [ ] **Step 3: Write minimal implementation**

No production code changes. If a test fails against a real daemon, fix the backend — not the test — and note the fix in the commit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: PASS overall; the nine daemon tests either pass (daemon up) or report as skipped with the preflight reason (daemon down). On macOS the ownership test additionally skips as Linux-only.

- [ ] **Step 5: Commit**

```bash
git add tests/sandbox/test_docker_daemon.py
git commit -m "test(sandbox): add a daemon-gated behavior suite for the docker backend"
```

---

### Task 15: Documentation

**Files:**
- Modify: `README.md` (line 14, the "in a fresh microVM" lede; lines 37-39, the pre-1.0 callout)
- Modify: `docs/concepts.md` (line 155, "Every cell runs inside a microVM")
- Modify: `docs/sandbox.md` (lines 17-24 the backend paragraph; the "Host requirements and preflight" section; the snapshot cache-location line; "Inside a running cell"; the "Credentials" section; a new base-image caveat and a trigger-routing note)
- Modify: `docs/configuration.md` (line 47, the `sandbox` row)
- Modify: `docs/harnesses.md` (the "Credentials ride as scoped secrets" bullet around line 59)

**Interfaces:**
- Produces: docs that match the code. `docs/style/development.md`: "If a doc drifts from the code, fix it or delete it."
- **Note:** the spec's Documentation Plan names three files. `README.md` also claims Docker is unimplemented (lines 38-39), and `README.md:14` plus `docs/concepts.md:155` state flatly that every cell runs in a microVM — which stops being true the moment `sandbox = "docker"` resolves. Five files, not three.
- **Say what is actually supported, and no more.** "Any platform with a Docker daemon" is a claim this change cannot back: the plan never runs on Windows, and `sandbox.py:7` imports `fcntl` (POSIX-only) for the snapshot build lock, so harnessbench does not import there at all. Remote `DOCKER_HOST` is explicitly out of scope in the spec — and would be broken anyway, since every bind mount here is a host path the daemon would resolve on the wrong machine.
- **Two behaviors worth one sentence each:** a `base_image` that declares `VOLUME` over a path the provisioning writes into loses those writes at `docker commit` (Docker excludes volume paths from a commit), and trigger routing (`route_in_sandbox`) stays microsandbox-only.

- [ ] **Step 1: Write the failing test**

There is no automated doc test for these claims (`tests/test_readme_examples.py` only parses the README's embedded eval block). Verify by grep instead — this is the check that must go from "hits" to "no hits":

Run: `grep -rn -i "not implemented\|only sandbox backend\|only backend\|only implementation" README.md docs/sandbox.md docs/configuration.md docs/harnesses.md`
Expected before the edit: hits at `README.md:38-39`, `docs/sandbox.md:20-22`, `docs/configuration.md:47`.

- [ ] **Step 2: Run test to verify it fails**

Run the grep above. Expected: three files still describe `docker` as recognized-but-unimplemented, which is now false.

- [ ] **Step 3: Write minimal implementation**

`README.md` — replace the "in a fresh microVM" lede (line 14):

```markdown
harnessbench runs an agent (Claude Code, Codex, or OpenCode) against a task
in a fresh sandbox — a microVM, or a container — checks what it actually did in
the workspace, and reports the result as a comparison: with your skill versus
without, one model versus another, one harness versus another.
```

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
| `docker` | Docker containers | macOS or Linux with a **local** Docker Engine or Docker Desktop; Windows is untested and a remote `DOCKER_HOST` is not supported | Shared host kernel; credentials are plain environment variables the guest can read |

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

**Docker** needs the `docker` CLI on `PATH` and a **local** daemon that answers
`docker info` — no virtualization extensions required. Supported on macOS and
Linux; Windows is untested (harnessbench itself uses POSIX-only APIs), and a
remote `DOCKER_HOST` is not supported: every mount is a host path that a remote
daemon would resolve on the wrong machine.

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

`docs/sandbox.md` — add to "Snapshots: build once, boot many", after the cache-location paragraph:

```markdown
> **Docker caveat.** A snapshot is sealed with `docker commit`, and a commit does
> not capture paths the base image declares as a `VOLUME`. If your `base_image`
> declares a volume over somewhere the harness CLI or your `environment_script`
> installs into, those files will be missing at cell boot. Pick a base image
> without a `VOLUME` over your install paths.
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

`docs/sandbox.md` — add to the trigger-routing description:

```markdown
Trigger routing — the probe that asks whether a harness would reach for your
skill unprompted — runs under `microsandbox` only. A set with `sandbox = "docker"`
runs its evals in containers as normal; routing checks are not available for it.
```

`docs/concepts.md` — replace the "Sandbox and snapshot" opening (line 155):

```markdown
Every cell runs inside its own sandbox — a microVM under the default
`microsandbox` backend, a container under `docker` — booted from a **snapshot**:
a sealed image with the base OS, the harness CLI, and any suite-wide tools
already installed. Snapshots build once per configuration and are cached; cells
boot from them in seconds. Reference: [sandbox.md](sandbox.md).
```

`docs/configuration.md` — replace line 47:

```markdown
| `sandbox` | string | The sandbox backend: `microsandbox` (the default; microVMs, needs Apple Silicon or Linux+KVM) or `docker` (containers, needs a local Docker daemon on macOS or Linux; shared kernel and guest-readable credentials — see [`sandbox.md`](sandbox.md)). Any other value fails fast with exit `2`. |
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

Run: `grep -rn -i "every cell runs inside a microvm\|in a fresh microvm\|any platform" README.md docs/*.md`
Expected: no output — the two blanket microVM claims and the overreaching platform claim are gone. (`docs/*.md` deliberately does not descend into `docs/plans/` or `docs/specs/`, which quote the old wording.)

Run: `make test`
Expected: PASS, whole suite green (`tests/test_readme_examples.py` included).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/concepts.md docs/sandbox.md docs/configuration.md docs/harnesses.md
git commit -m "docs: document the docker sandbox backend and its isolation tradeoffs"
```

---

## Verification

Run in order. The first four are the issue's done-criteria; the rest are the supporting proofs.

- **`make test`** — proves registry resolution, the cross-backend fingerprint split, Docker preflight strings, credential mapping per backend, the whole `DockerSandbox` guest contract with the CLI faked, and the unchanged microsandbox path (including the byte-identical fingerprint payload). On a host without a daemon the nine `tests/sandbox/test_docker_daemon.py` tests report as **skipped** with the preflight reason — never as failures.
- **`make lint`** — proves the new modules and doc updates pass Ruff and houserules, with no suppressions. `make lint:ruff` alone runs on a host with nothing installed but Python, which is the lazy-import invariant in practice; `tests/sandbox/test_docker.py::test_docker_module_imports_only_stdlib_and_harnessbench` enforces it permanently.
- **`uv run harnessbench sandbox:build --set dock --config tests/fixtures/sandbox/two-backends.toml`**
  - With the daemon running: exits **`0`**, printing `building snapshot harnessbench-docker-claude-code-<version>-<fingerprint> from ubuntu:latest ...` then `built ...` and `image identity: sha256:...`. Confirm with `docker images "harnessbench-docker-*"`.
  - With the daemon stopped: exits **`2`** with `error: harnessbench sandbox preflight failed:` and the `docker daemon unreachable (\`docker info\` exited 1) — start Docker and retry: ...` line. Re-running the same command with `--set micro` still exits `0`/`2` on microsandbox's own terms — the file no longer fails as a whole, which is the accept-path flip Task 12 landed.
  - Forcing a build failure (e.g. a set whose `base_image` does not exist) exits **`1`**, not `2` — the branch order from Task 13.
- **`uv run harnessbench run --set dock --config tests/fixtures/sandbox/two-backends.toml`** on a sample eval, with a reachable daemon and a real agent credential — proves a cell stages the project, boots a container, runs `setup.sh`, invokes the agent, gathers facts, grades, tears the container down, and lands in the matrix. Check the artifacts: `provenance.json` records `sandbox.backend == "docker"` (the field is `backend`, not `backend_id` — `sandbox/provenance.py:91`) and an `image_digest` of `sha256:...`. Then check the daemon: `docker ps -a --filter label=harnessbench.owner=harnessbench` lists nothing, and `ps auxww | grep -c ANTHROPIC_API_KEY` finds no credential in any process's argv while the run is live.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -v`** — the faked-CLI unit suite: preflight remedies, snapshot lookup and its daemon-unreachable split, image identity degradation, image-reference sanitization, fingerprint distinctness, the four guest-contract methods, the timeout reap and the dead-sandbox fast fail, control-plane vs guest exit-code classification, mount and env-file credential rendering (including that no token reaches an argv or an exception), label-gated replacement, build ordering, and teardown-on-failure.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker_daemon.py -v`** with Docker running — the behavior proofs against a real daemon: shell/exec/exec_stream/stop, a container removed mid-exec surfacing as `SandboxRuntimeError`, stderr past a real 64 KiB pipe buffer, root-written bind-mount files handed back to the host user (Linux), container removal, the commit → `snapshot_exists` → `image_identity` round trip, and a final check that the suite leaked no harnessbench-labelled containers.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_backend.py tests/config/test_arms.py tests/test_main.py -v`** — proves the interface flip: `sandbox = "docker"` is a valid config value everywhere, the two-backend fixture parses, unknown names still fail listing both supported values, and the exit-code contract (`0` built/reused, `2` config/preflight, `1` build failure) holds.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/agents -v`** — proves the two seam repairs: every adapter declares `GuestCredential`s and records any backend's infra failure as an errored arm, and `test_microsandbox_imported_only_under_allowlist` proves no agent adapter imports `microsandbox` any more.
- **`grep -rn -i "not implemented" README.md docs/*.md`** returns nothing about the sandbox backend — the docs no longer lie about Docker. The glob deliberately does not descend into `docs/plans/` or `docs/specs/`: this plan and its spec both quote the old fail-fast text and the old platform claim, so including them would make the check permanently red.

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
| Hardening — timeout reaping, credential hygiene, stderr drain, container ownership, root/bind ownership, error classification, bounded cleanup, build exit codes, image-ref robustness | 5, 6, 8, 9, 10, 11, 13, 14 |
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

- **Container naming.** Run names come from `_sandbox_run_name` (`eval-<eval_id>-<config>-<worker>`) and eval ids may contain characters Docker's name grammar rejects. `container_name()` maps illegal characters to `-` and prefixes `hb-` when the first character is not alphanumeric. The build container additionally folds in a hash of the resolved repo root, because `build_snapshot` carries no repo-root argument and the snapshot lock only serializes builds within one repo.
- **Image naming.** Snapshot names fold in a harness version string, which is whatever that CLI reports and is not constrained to Docker's repository grammar. `image_ref()` lowercases, replaces illegal characters, and appends an 8-character SHA-256 of the ORIGINAL name — lowercasing alone is lossy, and two cache identities collapsing onto one image would serve the wrong snapshot.
- **Keeping the container alive.** `docker run --detach --entrypoint sleep <image> infinity` — an explicit entrypoint override, so a base image that declares its own `ENTRYPOINT` still yields something to exec into.
- **`--memory` units.** `VM_MEMORY_MIB` renders as `--memory 2048m`.
- **`--user 0:0`, plus a chown on the way out.** Agents install under `/root` and run `bypassPermissions`; the sandbox is the boundary, not the uid. The cost is root-owned files in the Linux bind mount, which `stop()` chowns back before removing the container.
- **Credentials through `--env-file`, not `-e`.** The guest can still read the value (Docker has no scoping primitive), but the host cannot: no token in `ps` output, none in `docker inspect`, none in any error this module raises. The file is mode 0600 and lives only for the duration of `docker run`.
- **Ownership labels, and a removal that respects them.** Every container carries `harnessbench.owner` and a per-process `harnessbench.run` nonce. The pre-start replace inspects the label and refuses to remove a container harnessbench did not start, so a name collision costs an error instead of somebody's work.
- **Control-plane failures are classified, not inferred from the exit code.** `docker exec` shares 125/126/127 with ordinary guest commands, so known stderr shapes decide first and a `docker inspect` of the container's state settles the rest. Guessing either way is wrong: guessing "infra" turns real agent misses into errored arms; guessing "guest" turns a vanished container into a silent failed eval.
- **`.kill()` semantics.** It kills the local `docker exec` client, not the guest process; the caller removes the container immediately afterwards, and that removal is what reaps the guest. Documented as a WARNING in the docstring. The same asymmetry is why a timed-out `exec` removes its own container rather than just abandoning the client.
- **No `failed` stream event.** Docker reports a failed launch through a nonzero exit code, so `DockerExecStream` emits `stdout` chunks then exactly one `exited` event. Routing's own `exit_code not in (None, 0)` check already covers the failure path.
- **`_NOT_IMPLEMENTED` stays, empty.** It is the seam a future named-but-unbuilt backend plugs into, and `resolve_sandbox` still reads it; deleting it would mean re-adding the mechanism for the next backend.
- **`SandboxRuntimeError` subclasses `RuntimeError`**, matching `ProcResult.require_success`'s existing infra-failure convention — which is exactly why Task 13's branch ordering is load-bearing.
- **`sandbox_error_types()` rather than a wrapper object.** Keeping microsandbox's native error reachable through one helper leaves the microsandbox path byte-for-byte unchanged (the spec's "no change to microsandbox behavior") while giving agents one neutral `except` tuple.

**Open Questions from the spec, deliberately not resolved here:** rootless Docker / Podman compatibility, whether a subscription-shaped credential should warn or refuse under Docker, and Windows support. Task 15's docs claim a local Docker Engine/Desktop on macOS or Linux only; the behavior suite in Task 14 is the instrument for answering the first and third once someone runs it on those targets.

---

## Follow-ups (out of scope)

- **`credential_preflight_error()` preflights one harness, not the resolved set.** `sandbox.preflight` (`sandbox/sandbox.py:62`) calls `credential_preflight_error()` (`agents/__init__.py:101`), which resolves the run-level agent from the `--harnessbench-agent` / `HARNESSBENCH_AGENT` / `[tool.harnessbench]` chain and checks only that one harness's credential. A set whose arms name several harnesses therefore passes preflight with credentials missing for every harness but the global one, and the failure surfaces per-cell instead of up front — exactly what preflight exists to prevent. This is pre-existing and backend-independent: it behaves identically under `microsandbox`, and nothing in this plan makes it better or worse. **File it as its own issue** rather than folding a fix into the Docker backend, since the fix is in the agents package and needs its own decision about how a partially-credentialed set should behave.
