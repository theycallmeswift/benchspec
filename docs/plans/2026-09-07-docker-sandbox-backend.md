# Docker Sandbox Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `DockerBackend` as the second concrete `SandboxBackend`, make `docker` the default `sandbox`, teach `sandbox:clean` to prune Docker resources, and repair the two seam leaks (agent credentials, sandbox error) so evals run on any host with a Docker daemon.

**Architecture:** Docker is driven through the `docker` CLI with `asyncio.create_subprocess_exec` / `subprocess.run` (no client library, no new dependency). A new `benchspec/sandbox/docker.py` holds `DockerBackend` plus `DockerSandbox` / `DockerVolume`, which satisfy the implicit guest contract (`.shell`, `.exec`, `.exec_stream`, `.stop`, `Volume.bind`). `backend.py` keeps the seam, the microsandbox implementation, the registry, and the shared build helpers. Agents declare credentials as a neutral `Credential` dataclass and catch a neutral `SandboxError`; each backend maps both to its runtime. Every test that touches Docker goes through a shell shim selected by `BENCHSPEC_DOCKER_PATH`, mirroring the merged `MSB_PATH` pattern; one explicitly daemon-gated test exercises the real daemon.

**Tech Stack:** Python 3.11+, asyncio subprocesses, the `docker` CLI, pytest.

**Spec:** `docs/specs/2026-09-07-docker-sandbox-backend.md` (the issue #75 body, captured verbatim).

## Global Constraints

- **Lazy-import invariant:** importing `benchspec.sandbox.backend` or `benchspec.sandbox.docker` must not import `microsandbox` or any Docker client package. `docker.py` uses only the stdlib. `import microsandbox` stays inside method bodies in `backend.py`, and only there (`tests/sandbox/test_backend.py::test_microsandbox_imported_only_under_allowlist` shrinks its allowlist to `{"backend.py"}`).
- **Docker binary resolution:** `docker_binary()` returns `Path(os.environ["BENCHSPEC_DOCKER_PATH"])` when that variable is set and non-empty, else `shutil.which("docker")` as a `Path`, else `None`.
- **Image naming:** repository `benchspec-snapshot`; tag = the snapshot name; full reference `benchspec-snapshot:<snapshot_name>` (e.g. `benchspec-snapshot:benchspec-docker-claude-code-latest-c30e39d4`).
- **Container naming:** callers pass names unchanged as `--name`: `benchspec-build-<agent.id>`, `benchspec-eval-<id>-<config>-<worker>`, `benchspec-trigger-<worker>`. `NAME_PREFIX = "benchspec-"` selects every benchspec-owned container and image tag.
- **Resource flags:** `--cpus 2 --memory 2048m`, rendered from `VM_CPUS` and `VM_MEMORY_MIB`. Never introduce new sizing constants.
- **Mounts:** `/workspace` read-write, `/project` read-only (`-v <host>:/project:ro`); extra agent volumes via `DockerVolume.bind(path, readonly=True)`; every host path goes through `host_mount_path()`.
- **Fingerprint:** the same four ingredients (backend id, declared base image ref, install fingerprint, env script bytes) with `backend_id="docker"`; a Docker and a microsandbox snapshot of one agent+env get different names.
- **Default flip:** `DEFAULT_SANDBOX = "docker"`; `MicrosandboxBackend.id` and its registry key are the literal `"microsandbox"`. No change to microsandbox behavior, VM sizing, or cache-key ingredients.
- **Exit-code contract for `sandbox:build`:** `0` built or reused, `2` config or preflight (`SchemaError` / `RuntimeError`), `1` build failure (`SandboxError`).
- **`sandbox:clean` always exits `0`** once parsed, including on a host with neither runtime.
- **Test isolation:** no test may reach the developer's real Docker daemon except the one daemon-gated test in Task 5. Every CLI e2e test and every unit test that constructs a `DockerBackend` sets `BENCHSPEC_DOCKER_PATH` to a shim script or to a missing path. The daemon-gated test never calls `prune()`.
- **Code style:** `docs/style/development.md` binds every file: `from __future__ import annotations` first; Google-style docstrings on every module, class, and function (including private helpers); no single-letter names; no `# noqa` / `# type: ignore`; no comments referencing issues, PRs, or specs; imports at the top, absolute; tests in four-phase layout with blank lines between phases; test plumbing DRY, test stories DAMP.
- **Verification per task:** `make test` must be green and `make lint:ruff` clean before committing. `make lint:houserules` needs `GEMINI_API_KEY`; run it if the variable is set, otherwise say so in the report.
- **Commits:** conventional-commit messages (`feat:`, `test:`, `docs:`, `refactor:`); no attribution lines.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/benchspec/sandbox/errors.py` (new) | `SandboxError`, the backend-neutral runtime failure. No imports, so agents can import it without a cycle. |
| `src/benchspec/agents/base.py` | Gains `Credential`; the `CodingAgent.secrets()` contract becomes "return `list[Credential]`". |
| `src/benchspec/agents/{claude,codex,opencode}.py` | Return `Credential`s; catch `SandboxError`; drop every `microsandbox` import. |
| `src/benchspec/sandbox/backend.py` | The seam: protocol (gains `prune`), constants, `fingerprint_inputs_for`, shared build helpers (`bridge_skills_home`, `run_environment_script`), `MicrosandboxBackend` + `MicrosandboxGuest` wrapper, the registry and `resolve_sandbox`, `DEFAULT_SANDBOX`. |
| `src/benchspec/sandbox/docker.py` (new) | `docker_binary`, argv renderers, `DockerExecOutput`, `DockerExecEvent`, `DockerExecHandle`, `DockerSandbox`, `DockerMount`, `DockerVolume`, `DockerBackend`. |
| `src/benchspec/sandbox/sandbox.py` | `cli_clean` prunes every registered backend; docstrings stop calling microsandbox the default. |
| `src/benchspec/__main__.py` | `_run_sandbox_build` maps `SandboxError` to `FINDING`; no `microsandbox` import. |
| `tests/support/docker.py` (new) | `write_docker_shim(shim_dir) -> Path`: the logging `docker` stand-in every Docker test uses. |
| `tests/sandbox/test_docker.py` (new) | Unit tests for `docker.py` against the shim. |
| `tests/sandbox/test_docker_daemon.py` (new) | The one daemon-gated real-lifecycle test. |
| `tests/cli/test_sandbox_build_e2e.py`, `tests/cli/test_sandbox_clean_e2e.py` | Docker twins of the merged e2e cases. |
| `tests/fixtures/sandbox/two-backends.toml` | Becomes an accept fixture with one set per backend. |
| Docs: `docs/quickstart.md`, `README.md`, `docs/sandbox.md`, `docs/configuration.md`, `docs/harnesses.md`, `Makefile` | Docker as the default, microsandbox as the isolation opt-in, tradeoffs stated. |

---

### Task 1: Backend-neutral credentials and sandbox error (seam repairs)

**Files:**
- Create: `src/benchspec/sandbox/errors.py`
- Modify: `src/benchspec/agents/base.py` (add `Credential`; fix the `secrets()` docstring on the protocol)
- Modify: `src/benchspec/agents/claude.py:166-177, 300-330`
- Modify: `src/benchspec/agents/codex.py:185-197, 328-360`
- Modify: `src/benchspec/agents/opencode.py:33-36, 318-326, 397-433`
- Modify: `src/benchspec/sandbox/backend.py` (wrapper, credential mapping, error translation; re-export `SandboxError`)
- Modify: `src/benchspec/__main__.py:72-113`
- Test: `tests/agents/test_claude.py`, `tests/agents/test_codex.py`, `tests/agents/test_opencode.py`, `tests/sandbox/test_backend.py`, `tests/test_main.py`

**Interfaces:**
- Produces:
  ```python
  # src/benchspec/sandbox/errors.py
  class SandboxError(RuntimeError):
      """A sandbox runtime failure: daemon gone, VM or container killed, exec torn down."""

  # src/benchspec/agents/base.py
  @dataclass(frozen=True)
  class Credential:
      """A provider credential an agent needs in the guest, declared backend-neutrally."""
      env_var: str
      value: str
      allow_hosts: tuple[str, ...]

  # CodingAgent.secrets(self) -> list[Credential]

  # src/benchspec/sandbox/backend.py
  from benchspec.sandbox.errors import SandboxError   # re-exported for sandbox.py / __main__.py
  def microsandbox_secrets(agent: object) -> list:      # Credential -> microsandbox Secret.env(...)
  class MicrosandboxGuest:                               # wraps a native Sandbox; translates MicrosandboxError -> SandboxError
      async def shell(self, script, *, env=None, cwd=None)
      async def exec(self, cmd, args=None, *, cwd=None, env=None, timeout=None, stdin=None)
      async def exec_stream(self, cmd, args=None, *, cwd=None, env=None, stdin=None)  # -> MicrosandboxExecHandle
      async def stop(self)
  class MicrosandboxExecHandle:                          # wraps ExecHandle: __aiter__/__anext__/kill translate errors
  ```
- Consumes: nothing from later tasks.

- [ ] **Step 1: Write the failing credential tests**

Replace the `FakeSecret` monkeypatch tests (`tests/agents/test_claude.py::test_secrets_uses_configured_auth_env`, `tests/agents/test_codex.py` around line 41, and the four `FakeSecret` tests in `tests/agents/test_opencode.py` around lines 471-563) with direct assertions on `Credential`. Keep each test's story (which env var, which host) intact. Example for Claude:

```python
from benchspec.agents.base import Credential


def test_secrets_declares_the_configured_credential_scoped_to_anthropic() -> None:
    """The configured credential name reaches the guest, scoped to the Anthropic API host."""
    agent = ClaudeCodeAgent(auth_value="tok-123", auth_env="CLAUDE_CODE_OAUTH_TOKEN")

    credentials = agent.secrets()

    assert credentials == [
        Credential("CLAUDE_CODE_OAUTH_TOKEN", "tok-123", ("api.anthropic.com",))
    ]
```

Codex: with `auth_json_path` set, `secrets()` is `[]`; otherwise `Credential(auth_env, value, tuple(_PROVIDER_HOSTS[auth_env]))`. OpenCode: `Credential(guest_env_name, value, (allow_host,))` where `GEMINI_API_KEY` maps to the guest name `GOOGLE_GENERATIVE_AI_API_KEY` (preserve the existing rename tests' stories).

Add one invoke test per agent proving a `SandboxError` raised by the sandbox's `exec` records an `is_error` result whose text starts with `<sandbox-error>` (mirror the existing `FakeSandbox` shape in each test module; add a `raise_on_exec` hook or a fake whose `exec` raises `SandboxError("container gone")`).

- [ ] **Step 2: Write the failing backend tests** in `tests/sandbox/test_backend.py`

```python
from benchspec.agents.base import Credential
from benchspec.sandbox.errors import SandboxError


def test_microsandbox_secrets_render_scoped_secret_entries() -> None:
    """One neutral credential renders as a microsandbox Secret scoped to its hosts."""
    agent = _agent()

    entries = backend.microsandbox_secrets(agent)

    assert [(entry.env_var, entry.value, entry.allow_hosts) for entry in entries] == [
        ("ANTHROPIC_API_KEY", "test-token", ("api.anthropic.com",))
    ]


def test_microsandbox_guest_translates_runtime_errors() -> None:
    """A MicrosandboxError from the native sandbox surfaces as the neutral SandboxError."""
    from microsandbox.errors import MicrosandboxError

    class ExplodingSandbox:
        """A native sandbox whose shell call fails the way a dead VM does."""

        async def shell(self: object, script: str, **kwargs: object) -> object:
            """Fail like a torn-down VM."""
            raise MicrosandboxError("vm gone")

    guest = backend.MicrosandboxGuest(ExplodingSandbox())

    with pytest.raises(SandboxError, match="vm gone"):
        asyncio.run(guest.shell("true"))
```

(Check what `_agent()` in that file uses for `auth_env`; assert the value it actually configures.) Also update `test_microsandbox_imported_only_under_allowlist` so `allowlist = {"backend.py"}` and its docstring no longer mentions the agents or the CLI wrapper.

In `tests/test_main.py`, change `test_sandbox_build_build_error_exits_one` to raise `SandboxError("snapshot build failed")` (import from `benchspec.sandbox.errors`) and rewrite `test_sandbox_build_missing_package_exits_two_before_importing_errors`'s docstring to say the build path never imports microsandbox at any point now (keep the poisoned-module assertion: it still proves that).

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/agents tests/sandbox/test_backend.py tests/test_main.py -q`
Expected: FAIL with `ImportError` on `Credential` / `SandboxError` and `AttributeError` on `microsandbox_secrets` / `MicrosandboxGuest`.

- [ ] **Step 4: Implement**

1. `src/benchspec/sandbox/errors.py`: module docstring, `from __future__ import annotations`, `SandboxError(RuntimeError)` with the docstring above plus one sentence: every backend raises it, or wraps its runtime's exceptions into it, so agents and the CLI classify a runtime failure without naming a runtime.
2. `agents/base.py`: add `Credential` (frozen dataclass, fields as above, Google docstring with `Attributes:`), and change the protocol's `secrets()` docstring to "Return the provider credentials to inject into the guest." Update the module docstring sentence "which secrets it needs" to "which credentials it needs".
3. Each agent's `secrets()` builds `Credential`s (no `microsandbox` import). Each agent's `invoke` imports `SandboxError` from `benchspec.sandbox.errors` at the top of the module and catches `(TimeoutError, SandboxError, OSError)` (codex keeps its extra `RuntimeError`). Fix the comment in `opencode.py:33-36` and `claude.py:24` and `claude.py:160` so they no longer say "microsandbox secret"; say "scoped credential the backend injects".
4. `backend.py`:
   - `from benchspec.sandbox.errors import SandboxError` at the top; add `SandboxError` to the module docstring's description of the seam.
   - `microsandbox_secrets(agent)`: `from microsandbox import Secret` inside; returns `[Secret.env(credential.env_var, value=credential.value, allow_hosts=list(credential.allow_hosts)) for credential in agent.secrets()]`.
   - A private async context manager `_translate_runtime_errors()` that imports `MicrosandboxError` inside and re-raises it as `SandboxError(str(error)) from error`.
   - `MicrosandboxGuest(native)` and `MicrosandboxExecHandle(native_handle)` forwarding the four guest methods (and the handle's `__aiter__`, `__anext__`, `kill`) under `_translate_runtime_errors()`. `exec_stream` returns the wrapped handle.
   - `create_sandbox`, `create_trigger_sandbox`, and `_build_snapshot_async` wrap `Sandbox.create(...)` in `_translate_runtime_errors()`, pass `secrets=microsandbox_secrets(agent)`, and hand `MicrosandboxGuest(native)` to the agent / caller. `build_snapshot`'s `Snapshot.create` call is also under the translation, so a build failure reaches the CLI as `SandboxError`.
   - `guest_shell`, `stop_quietly`, `kill_quietly` catch `SandboxError` instead of importing `MicrosandboxError`.
5. `__main__.py::_run_sandbox_build`: `except SandboxError as error: print(f"error: {error}", file=sys.stderr); return ExitCode.FINDING`; delete the generic `except Exception` branch and the lazy import; rewrite the docstring's error-mapping bullets (SchemaError → 2, RuntimeError → 2, SandboxError → 1) without mentioning microsandbox.

- [ ] **Step 5: Run the full suite and lint**

Run: `make test` then `make lint:ruff`
Expected: all green; no `microsandbox` import outside `backend.py` (the allowlist test proves it).

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "refactor: make agent credentials and the sandbox error backend-neutral"
```

---

### Task 2: DockerSandbox guest surface

**Files:**
- Create: `src/benchspec/sandbox/docker.py`
- Create: `tests/support/docker.py`
- Test: `tests/sandbox/test_docker.py` (new)

**Interfaces:**
- Consumes: `SandboxError` (`benchspec.sandbox.errors`).
- Produces (all in `benchspec.sandbox.docker`):
  ```python
  def docker_binary() -> Path | None
  RUNTIME_FAILURE_MARKERS = ("Cannot connect to the Docker daemon", "Error response from daemon", "Error: No such container")

  @dataclass(frozen=True)
  class DockerExecOutput:
      exit_code: int
      stdout_bytes: bytes
      stderr_bytes: bytes
      @property stdout_text -> str   # utf-8, errors="replace"
      @property stderr_text -> str

  @dataclass(frozen=True)
  class DockerExecEvent:
      event_type: str                # "stdout" | "stderr" | "exited" | "failed"
      data: bytes | None = None
      code: int | None = None

  @dataclass(frozen=True)
  class DockerMount:
      host_path: str
      readonly: bool
      def flag(self, guest_path: str) -> str   # "<host>:<guest>" or "<host>:<guest>:ro"

  class DockerVolume:
      @staticmethod
      def bind(path: str, *, readonly: bool = False) -> DockerMount

  def exec_argv(container: str, command: str, args: list[str], *, cwd: str | None, env: dict[str, str] | None, interactive: bool) -> list[str]
  def shell_argv(container: str, script: str, *, cwd: str | None, env: dict[str, str] | None) -> list[str]

  class DockerExecHandle:            # async iterator of DockerExecEvent; async kill()
  class DockerSandbox:
      def __init__(self, name: str, *, binary: Path)
      name: str
      async def shell(self, script: str, *, env: dict | None = None, cwd: str | None = None) -> DockerExecOutput
      async def exec(self, cmd: str, args: list[str] | None = None, *, cwd: str | None = None, env: dict | None = None, timeout: float | None = None, stdin: bytes | None = None) -> DockerExecOutput
      async def exec_stream(self, cmd: str, args: list[str] | None = None, *, cwd: str | None = None, env: dict | None = None, stdin: bytes | None = None) -> DockerExecHandle
      async def stop(self) -> None
  ```
  And in `tests/support/docker.py`: `write_docker_shim(shim_dir: Path) -> Path`.

**Behavior to implement exactly:**
- `exec_argv` renders `["exec", *("-i" if interactive), *("-w", cwd) if cwd, *("-e", f"{key}={value}") per env item in insertion order, container, command, *args]`. It never includes the binary itself; the runner prepends it.
- `shell_argv` = `exec_argv(container, "bash", ["-c", script], cwd=cwd, env=env, interactive=False)`.
- `DockerSandbox.exec`: `stdin=None` → no `-i`, subprocess stdin `DEVNULL`; `stdin=b"..."` (including `b""`) → `-i`, bytes written then stdin closed via `communicate`. Under `asyncio.wait_for(timeout)` when `timeout` is not None; on timeout kill the subprocess, await it, and raise `TimeoutError(f"docker exec of {cmd} timed out after {timeout}s")`. After completion, if `stderr_text` contains any `RUNTIME_FAILURE_MARKERS` entry, raise `SandboxError(stderr_text.strip()[-2000:])`; otherwise return `DockerExecOutput`. `OSError` from a missing binary propagates unchanged.
- `DockerSandbox.shell` = `exec`-style run of `shell_argv` (no stdin, no timeout).
- `DockerSandbox.exec_stream`: start the subprocess with `stdout=PIPE`, `stderr=PIPE`, stdin as above; return a `DockerExecHandle` that reads stdout and stderr concurrently in 64 KiB chunks into an `asyncio.Queue`, emitting `DockerExecEvent("stdout", data=chunk)` / `DockerExecEvent("stderr", data=chunk)`; when both streams hit EOF, await `proc.wait()` and emit `DockerExecEvent("failed", code=returncode)` if the accumulated stderr carries a runtime-failure marker, else `DockerExecEvent("exited", code=returncode)`; then end iteration. `kill()` sends `proc.kill()` when the process is still running and awaits it; iteration then drains to its terminal event. The router in `sandbox.py` only reads `event_type in {"stdout", "exited", "failed"}` and `.data` / `.code`, so `stderr` events are ignored there by design.
- `DockerSandbox.stop`: `docker rm -f <name>`, ignoring the exit status (a container that is already gone is already stopped; the why goes in a comment).

**The shim** (`tests/support/docker.py::write_docker_shim`) writes an executable `docker` script that appends `"$*"` to `$BENCHSPEC_DOCKER_COMMAND_LOG` when that variable is set, then dispatches on the subcommand:
- `info` → exit 0.
- `image inspect`: with `--format` → prints `sha256:deadbeef` and exits 0; without → exits `${BENCHSPEC_SHIM_INSPECT_EXIT:-1}`.
- `ps` → prints `$BENCHSPEC_SHIM_CONTAINERS` (newline-separated, may be empty), exit 0.
- `images` → prints `$BENCHSPEC_SHIM_IMAGES`, exit 0.
- `exec` → runs `"${BENCHSPEC_SHIM_EXEC:-true}"` through `sh -c` with stdin passed through (so a test can make exec echo, sleep, print to stderr, or exit nonzero), forwarding that command's exit code.
- anything else → exit 0.

Write it with `textwrap.dedent` and `chmod 0o755`; return the path. Unit tests point `BENCHSPEC_DOCKER_PATH` at it via `monkeypatch.setenv`, or pass it as `binary=` to `DockerSandbox` directly.

- [ ] **Step 1: Write the failing rendering tests** in `tests/sandbox/test_docker.py`

```python
from benchspec.sandbox import docker


def test_exec_argv_renders_workdir_env_and_interactive_flags() -> None:
    """Verify the exec argv carries -i, -w, and every -e in insertion order before the command."""
    argv = docker.exec_argv(
        "benchspec-eval-hello-trial-gw0",
        "claude",
        ["-p", "hi"],
        cwd="/workspace",
        env={"HOME": "/root", "TZ": "UTC"},
        interactive=True,
    )

    assert argv == [
        "exec", "-i", "-w", "/workspace", "-e", "HOME=/root", "-e", "TZ=UTC",
        "benchspec-eval-hello-trial-gw0", "claude", "-p", "hi",
    ]


def test_shell_argv_wraps_the_script_in_bash() -> None:
    """Verify a shell script runs as `bash -c` without -i."""
    argv = docker.shell_argv("benchspec-build-claude-code", "set -e\necho hi", cwd=None, env=None)

    assert argv == ["exec", "benchspec-build-claude-code", "bash", "-c", "set -e\necho hi"]


def test_docker_mount_flag_marks_readonly() -> None:
    """Verify a read-only bind renders with the :ro suffix and a writable one without."""
    assert docker.DockerVolume.bind("/tmp/stage", readonly=True).flag("/project") == "/tmp/stage:/project:ro"
    assert docker.DockerVolume.bind("/tmp/room").flag("/workspace") == "/tmp/room:/workspace"


def test_docker_binary_prefers_the_env_override(monkeypatch: object, tmp_path: object) -> None:
    """Verify BENCHSPEC_DOCKER_PATH wins over PATH lookup."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", str(tmp_path / "docker"))

    assert docker.docker_binary() == tmp_path / "docker"
```

- [ ] **Step 2: Write the failing behavior tests** (same file, all through the shim)

Each test writes the shim into `tmp_path / "bin"`, constructs `DockerSandbox("benchspec-eval-x-y-main", binary=shim)`, and drives one behavior:

1. `exec` returns the command's exit code, stdout, and stderr: `BENCHSPEC_SHIM_EXEC='printf out; printf err >&2; exit 3'` → `exit_code == 3`, `stdout_text == "out"`, `stderr_text == "err"`.
2. `exec` feeds `stdin` bytes and closes it: `BENCHSPEC_SHIM_EXEC='cat'`, `stdin=b"payload"` → `stdout_text == "payload"`; the command log line contains ` -i `.
3. `exec` with `stdin=None` renders no `-i` (assert on the command log).
4. `exec` raises `TimeoutError` when the command outlives `timeout`: `BENCHSPEC_SHIM_EXEC='sleep 5'`, `timeout=0.2`.
5. `exec` raises `SandboxError` when stderr carries a daemon marker: `BENCHSPEC_SHIM_EXEC='echo "Error response from daemon: container x is not running" >&2; exit 1'`.
6. `shell` runs `bash -c <script>` (assert the log line ends with `bash -c echo hi`) and returns its output.
7. `exec_stream` yields stdout chunks and a terminal `exited` event: `BENCHSPEC_SHIM_EXEC='printf "a\nb\n"; exit 0'` → collected `[("stdout", b"a\nb\n"), ("exited", 0)]` (join stdout chunks before asserting so chunking cannot flake).
8. `exec_stream` yields `failed` when stderr carries a marker and a nonzero exit.
9. `exec_stream().kill()` ends a long-running command: `BENCHSPEC_SHIM_EXEC='sleep 5'`; after the first event or a short wait, `await handle.kill()`; iterating to the end finishes well under 5 s and the terminal event is `exited` with a nonzero code.
10. `stop` runs `rm -f <name>` and ignores a nonzero exit (shim: add `rm` → exit `${BENCHSPEC_SHIM_RM_EXIT:-0}` to the dispatcher; set it to 1).

Set env with `monkeypatch.setenv` for the shim knobs; use `asyncio.run` per test; four-phase layout.

- [ ] **Step 3: Run to verify failure**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox/test_docker.py -q`
Expected: FAIL with `ModuleNotFoundError: benchspec.sandbox.docker`.

- [ ] **Step 4: Implement `docker.py` (guest half) and `tests/support/docker.py`**

Module docstring for `docker.py`: what it is (the Docker implementation of the sandbox seam, driven through the `docker` CLI), the lazy-import invariant restated as "stdlib only", and the isolation caveat in one line (containers share the host kernel and cannot scope credentials to hosts; the docs carry the tradeoff). Keep `DockerBackend` for Task 3; this task ships everything listed under Interfaces above. Put the subprocess runner in one private coroutine `_run_docker(binary, argv, *, stdin, timeout) -> DockerExecOutput` that `exec` and `shell` share and that applies the marker check; `exec_stream` starts its own process because it needs the streams.

- [ ] **Step 5: Run the suite and lint**

Run: `make test` then `make lint:ruff`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/benchspec/sandbox/docker.py tests/support/docker.py tests/sandbox/test_docker.py
git commit -m "feat: add the Docker guest surface (DockerSandbox, DockerVolume) over the docker CLI"
```

---

### Task 3: DockerBackend lifecycle, registration, and the default flip

**Files:**
- Modify: `src/benchspec/sandbox/docker.py` (add `DockerBackend`, `run_argv`, `image_ref`)
- Modify: `src/benchspec/sandbox/backend.py` (`fingerprint_inputs_for`, shared build helpers, `prune` on the protocol, registry, `DEFAULT_SANDBOX`, docstrings)
- Modify: `src/benchspec/sandbox/sandbox.py` (docstrings only: the module docstring, `preflight`, `cli_build` no longer say microsandbox is the default)
- Modify: `src/benchspec/orchestration/execution.py:410` comment, `src/benchspec/orchestration/environments.py:5,98` docstrings ("a sandbox guest", not "a microsandbox guest")
- Modify: `tests/fixtures/sandbox/two-backends.toml`
- Test: `tests/sandbox/test_docker.py`, `tests/sandbox/test_backend.py`, `tests/config/test_arms.py`, `tests/test_main.py`, `tests/runners/test_pytest.py`, `tests/orchestration/test_execution.py`, `tests/cli/test_sandbox_build_e2e.py`, `tests/sandbox/test_sandbox.py`

**Interfaces:**
- Consumes: Task 2's `DockerSandbox`, `DockerVolume`, `DockerMount`, `docker_binary`, `DockerExecOutput`; Task 1's `SandboxError`, `Credential`.
- Produces:
  ```python
  # backend.py
  DEFAULT_SANDBOX = "docker"
  SNAPSHOT_REPOSITORY = "benchspec-snapshot"          # lives in docker.py; listed here for the reviewer
  def fingerprint_inputs_for(backend_id: str, agent: CodingAgent, env: EnvConfig) -> FingerprintInputs
  async def bridge_skills_home(sandbox: object, agent: object) -> None      # moved out of MicrosandboxBackend
  async def run_environment_script(sandbox: object, agent: object, env: EnvConfig) -> None
  def registered_backends() -> dict[str, type]        # {"microsandbox": MicrosandboxBackend, "docker": DockerBackend}
  class SandboxBackend(Protocol): ... def prune(self) -> None   # new protocol method (implemented in Task 4 for both)

  # docker.py
  def image_ref(snapshot: str) -> str                  # f"{SNAPSHOT_REPOSITORY}:{snapshot}"
  def run_argv(name: str, image: str, *, mounts: dict[str, DockerMount], env: dict[str, str]) -> list[str]
  class DockerBackend:
      id = "docker"
      def preflight(self) -> list[str]
      def snapshot_exists(self, name: str) -> bool
      def fingerprint_inputs(self, agent, env) -> FingerprintInputs
      def cache_fingerprint(self, agent, env) -> str
      def image_identity(self, snapshot: str) -> ImageIdentity
      def build_snapshot(self, agent, name: str, env) -> None
      async def create_sandbox(self, *, agent, snapshot, name, host_workdir, host_repo_root, extra_volumes) -> DockerSandbox
      async def create_trigger_sandbox(self, *, agent, snapshot, name, host_repo_root, extra_volumes) -> DockerSandbox
      async def guest_shell(self, sandbox, agent, script) -> str | None
      async def stop_quietly(self, sandbox) -> None
      async def kill_quietly(self, handle) -> None
      def prune(self) -> None       # stub for now: `raise NotImplementedError` is NOT acceptable — implement as a no-op returning None with a one-line docstring; Task 4 fills it in
  ```

**Behavior to implement exactly:**
- `run_argv` renders `["run", "-d", "--name", name, "--cpus", "2", "--memory", "2048m", *("-v", mount.flag(guest_path)) per mount in insertion order, *("-e", f"{key}={value}") per env item, image, "sleep", "infinity"]`.
- `DockerBackend` resolves the binary on every call via `docker_binary()` (no caching, so tests that set `BENCHSPEC_DOCKER_PATH` after construction still work). A private `_binary()` raises `RuntimeError("docker CLI not found ...")` when it is `None`; only `preflight`, `snapshot_exists`, and `prune` tolerate `None` (they return the remedy / `False` / nothing).
- Sync commands (`preflight`, `snapshot_exists`, `image_identity`, `prune`) go through one private `_docker_sync(argv, *, timeout=DOCKER_COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess` (`capture_output=True, text=True, check=False`), with `DOCKER_COMMAND_TIMEOUT_SECONDS = 60` named at module level. Async lifecycle commands (`rm -f`, `run -d`, `commit`) go through one private coroutine `_docker(argv) -> DockerExecOutput` that raises `SandboxError` naming the subcommand and the stderr tail on a nonzero exit.
- `preflight()`:
  - binary `None` → `"docker CLI not found — install Docker Engine or Docker Desktop, or set BENCHSPEC_DOCKER_PATH to the binary"`.
  - else run `docker info` (timeout 30 s via the constant `DOCKER_INFO_TIMEOUT_SECONDS = 30`); nonzero exit, `OSError`, or `subprocess.TimeoutExpired` → `"Docker daemon unreachable (`docker info` failed: <last non-empty stderr line, or the exception>) — start Docker Desktop or the docker service"`.
  - No platform check.
- `snapshot_exists(name)`: `False` when binary is `None`; else `docker image inspect <image_ref(name)>` exits 0.
- `fingerprint_inputs` → `fingerprint_inputs_for(self.id, agent, env)`; `cache_fingerprint` → `.digest`. In `backend.py`, `MicrosandboxBackend.fingerprint_inputs` becomes a one-line call to the same helper (move the docstring's explanation onto the helper).
- `image_identity(snapshot)`: `docker image inspect --format {{.Id}} <ref>` → `ImageIdentity.available(stdout.strip())` on exit 0 with non-empty stdout; otherwise `ImageIdentity.unavailable(<stderr tail or "docker image inspect returned no id">)`. Never raises (wrap `OSError` / timeout into `unavailable`).
- `build_snapshot(agent, name, env)` = `asyncio.run(self._build_snapshot_async(...))`, which: computes `build_name = f"{NAME_PREFIX}build-{agent.id}"` and `base_image = env.base_image or BASE_IMAGE`; runs `docker rm -f <build_name>` ignoring failure (replace semantics); runs `docker run` via `run_argv(build_name, base_image, mounts={}, env={})`; wraps `DockerSandbox(build_name, binary=...)`; `await agent.provision(sandbox)`; `await bridge_skills_home(sandbox, agent)`; `await run_environment_script(sandbox, agent, env)`; `docker commit <build_name> <image_ref(name)>`; `finally: docker rm -f <build_name>` ignoring failure.
- `create_sandbox(...)`: `mounts = {GUEST_WORKDIR: DockerVolume.bind(host_mount_path(host_workdir))}`; if `host_repo_root is not None`, `mounts[PROJECT_MOUNT] = DockerVolume.bind(host_mount_path(host_repo_root), readonly=True)` (keep the why-comment from the microsandbox version); `mounts.update(extra_volumes(agent, DockerVolume))`; `env = {credential.env_var: credential.value for credential in agent.secrets()}`; `docker rm -f <name>` ignoring failure; `docker run` via `run_argv(name, image_ref(snapshot), mounts=mounts, env=env)`; return `DockerSandbox(name, binary=...)`.
- `create_trigger_sandbox(...)`: same with only the `/project` mount plus extras, then `await agent.stage_project_assets(sandbox, PROJECT_MOUNT)`; on `BaseException` call `stop_quietly` and re-raise (mirror the microsandbox version).
- `guest_shell` / `stop_quietly` / `kill_quietly`: identical bodies to the microsandbox ones but catching `SandboxError` — since both backends now have byte-identical versions, move all three onto a small concrete base class `SharedBackendBehavior` in `backend.py` that both backends inherit (its docstring says what is shared and why).
- `backend.py` registry: delete `_NOT_IMPLEMENTED` and the `_REGISTRY` module constant; add

  ```python
  def registered_backends() -> dict[str, type]:
      """Return every implemented backend keyed by its `sandbox` value."""
      # docker.py imports this module for the seam, so the registry imports it lazily
      # to keep the dependency one-directional at import time.
      from benchspec.sandbox.docker import DockerBackend

      return {"microsandbox": MicrosandboxBackend, "docker": DockerBackend}
  ```

  `resolve_sandbox(name)` returns `registered_backends()[name]()` or raises `SchemaError(f"unsupported sandbox `{name}` (supported: {sorted(...)})")`. `MicrosandboxBackend.id = "microsandbox"`. `DEFAULT_SANDBOX = "docker"` with its comment rewritten ("the default every unpinned set, the bare build, and trigger routing inherit; microsandbox is the microVM opt-in"). Rewrite the module docstring's second paragraph: two implemented backends, Docker default, microsandbox opt-in; adding a third is additive.
- `two-backends.toml`: rewrite the header comment to describe an accept fixture (both sets parse; `micro` resolves to microsandbox, `dock` to docker; `dock` reaches Docker preflight, which the e2e test redirects through `BENCHSPEC_DOCKER_PATH`). Keep both sets and `default-set = "micro"`.

**Tests to write first (four-phase, DAMP):**
- `tests/sandbox/test_docker.py`: `run_argv` renders mounts, `:ro`, resource flags, env, and the `sleep infinity` command in that order; `image_ref`; `preflight` with `BENCHSPEC_DOCKER_PATH` at a missing path returns the CLI-not-found remedy; `preflight` with a shim whose `info` fails (add `BENCHSPEC_SHIM_INFO_EXIT` to the shim dispatcher) returns the daemon-unreachable remedy containing "docker info"; `preflight` with a healthy shim returns `[]`; `snapshot_exists` True/False via `BENCHSPEC_SHIM_INSPECT_EXIT`; `image_identity` available with `sha256:deadbeef` and unavailable when `BENCHSPEC_SHIM_INSPECT_EXIT=1` and `--format` is forced to fail (add `BENCHSPEC_SHIM_INSPECT_FORMAT_EXIT`, default 0); `build_snapshot` with a fake agent (id `probe`, `provision` runs `sandbox.shell("echo provisioned")`, `bridge_skills_home_script()` returns `"true"`, `guest_env()` `{}`) logs `rm -f benchspec-build-probe`, `run -d --name benchspec-build-probe --cpus 2 --memory 2048m ubuntu:latest sleep infinity`, two `exec` lines, `commit benchspec-build-probe benchspec-snapshot:snap`, and a trailing `rm -f benchspec-build-probe`; `build_snapshot` still removes the build container when provisioning fails (`BENCHSPEC_SHIM_EXEC='exit 1'` → `RuntimeError` from the agent, log ends with `rm -f benchspec-build-probe`); `create_sandbox` logs `run -d --name benchspec-eval-hello-trial-main ... -v <room>:/workspace -v <stage>:/project:ro -e ANTHROPIC_API_KEY=test-token benchspec-snapshot:snap sleep infinity` and returns a `DockerSandbox` named `benchspec-eval-hello-trial-main` (use `ClaudeCodeAgent(auth_value="test-token", version="1.2.3")` and `sandbox._agent_extra_volumes`); `create_trigger_sandbox` mounts only `/project:ro` and calls `stage_project_assets` (fake agent records the call).
- `tests/sandbox/test_backend.py`: `resolve_sandbox("docker")` returns a backend with `id == "docker"`; `resolve_sandbox("qemu")` raises listing both `docker` and `microsandbox`; delete `test_resolve_sandbox_docker_fails_naming_not_implemented`; `DEFAULT_SANDBOX == "docker"`; the Docker fingerprint folds `backend_id="docker"` (assert `fingerprint_inputs(...).backend_id == "docker"`) and differs from the microsandbox fingerprint for the same agent+env; `sandbox.snapshot_name` differs across the two backends (starts with `benchspec-docker-` vs `benchspec-microsandbox-`).
- `tests/config/test_arms.py`: `test_resolve_set_defaults_runner_and_sandbox` asserts `"docker"` (rename it to say docker); `test_two_backends_fixture_fails_whole_file` becomes `test_two_backends_fixture_parses_and_resolves_each_backend` asserting `micro` → `"microsandbox"` and `dock` → `"docker"` via `resolve_set(rawsets, default, set_name=...)`; the parametrized/inline `"sandbox": "docker"` rejection test near line 551-565 (find it by grepping `"docker"`) flips to an unknown name like `"qemu"` with the message `unsupported sandbox `qemu``.
- `tests/test_main.py::test_sandbox_build_docker_set_exits_two` → rename to `test_sandbox_build_unknown_sandbox_exits_two` raising `SchemaError("...unsupported sandbox `qemu` (supported: ['docker', 'microsandbox'])")` and asserting `"qemu"` in stderr.
- `tests/runners/test_pytest.py`: the two comments and the assertion `set(captured) == {"microsandbox"}` → `{"docker"}`; update the `# None => preflight resolves DEFAULT_SANDBOX itself` comment only if it names microsandbox.
- `tests/orchestration/test_execution.py`: grep for `microsandbox`; any assertion that encodes the default (not an explicitly configured backend id on a fake) flips to `docker`. Fakes that declare `id = "microsandbox"` explicitly stay as they are.
- `tests/cli/test_sandbox_build_e2e.py`: replace `test_sandbox_build_docker_set_is_a_usage_error` with `test_sandbox_build_docker_set_without_docker_cli_fails_preflight`: same invocation plus `env={"BENCHSPEC_DOCKER_PATH": str(tmp_path / "missing" / "docker"), "ANTHROPIC_API_KEY": "test-token"}` → exit 2, stderr contains `"error: benchspec sandbox preflight failed:"` and `"docker CLI not found"`, stdout lacks `"building snapshot"`. This flip lands in THIS task so no e2e test can reach the real daemon between tasks.
- `tests/sandbox/test_sandbox.py`: any test asserting a snapshot-name prefix of `benchspec-microsandbox-` while resolving the default flips to `benchspec-docker-`; tests that resolve `"microsandbox"` explicitly stay.

- [ ] **Step 1: Write the failing tests listed above**
- [ ] **Step 2: Run to verify failure** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox tests/config tests/test_main.py tests/runners tests/cli/test_sandbox_build_e2e.py -q` — expected: failures on `DockerBackend`, `registered_backends`, the default value, and the fixture tests.
- [ ] **Step 3: Implement** everything under Behavior above.
- [ ] **Step 4: Run the whole suite and lint** — `make test`, `make lint:ruff` — green. Confirm `grep -rn "not implemented" src docs tests` has no Docker hit left in `src` or `tests` (docs are Task 6).
- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: implement the Docker sandbox backend and make it the default"
```

---

### Task 4: `sandbox:clean` prunes every registered backend

**Files:**
- Modify: `src/benchspec/sandbox/backend.py` (`MicrosandboxBackend.prune`)
- Modify: `src/benchspec/sandbox/docker.py` (`DockerBackend.prune`, plus `ps_argv()` / `images_argv()` renderers)
- Modify: `src/benchspec/sandbox/sandbox.py:604-640` (`cli_clean`)
- Modify: `src/benchspec/__main__.py:116-135` (docstring)
- Test: `tests/sandbox/test_docker.py`, `tests/sandbox/test_backend.py` or `tests/sandbox/test_sandbox.py` (wherever `cli_clean` is unit-tested today; grep `cli_clean`), `tests/cli/test_sandbox_clean_e2e.py`

**Interfaces:**
- Consumes: `registered_backends()` from Task 3; `write_docker_shim` from Task 2.
- Produces: `MicrosandboxBackend.prune()`, `DockerBackend.prune()`, `ps_argv() -> list[str]`, `images_argv() -> list[str]`.

**Behavior to implement exactly:**
- `ps_argv()` = `["ps", "-a", "--filter", "name=benchspec-", "--format", "{{.Names}}"]`; `images_argv()` = `["images", "benchspec-snapshot", "--format", "{{.Repository}}:{{.Tag}}"]`.
- `DockerBackend.prune()`: return when the binary is `None`. Run `ps_argv()`; a nonzero exit or `OSError` means the daemon is unreachable and there is nothing reachable to prune — return without raising. For each stdout line that `startswith(NAME_PREFIX)` (Docker's name filter is a substring match, so filter again by prefix), run `docker rm -f <name>` with `check=False`. Then run `images_argv()`; for each line `repository:tag` whose tag part `startswith(NAME_PREFIX)`, run `docker rmi -f <line>` with `check=False`. Order: all containers first, then images (an image cannot be removed while a container uses it).
- `MicrosandboxBackend.prune()`: the exact `~/.microsandbox` walk that `cli_clean` performs today (`msb stop` + `msb rm -f` per sandbox, `msb snapshot rm --force` per snapshot; a `None` binary means nothing to prune; `FileNotFoundError` tolerated), moved verbatim with its docstring.
- `cli_clean(repo_root)`: `for backend_class in registered_backends().values(): backend_class().prune()`, then the lock-file unlink loop as today. Rewrite the docstring: every registered backend prunes its own `benchspec-*` resources (microsandbox: sandboxes and snapshots under `~/.microsandbox`; Docker: containers and `benchspec-snapshot` images), then lock files. Remove the now-unused `msb_binary` import from `sandbox.py` if nothing else uses it.
- `__main__._run_sandbox_clean` docstring: replace "tolerates a missing microsandbox runtime" with "each backend tolerates a missing runtime (nothing to prune)".

**Tests to write first:**
- `tests/sandbox/test_docker.py`: `prune` with `BENCHSPEC_SHIM_CONTAINERS="benchspec-eval-hello-trial-gw0\nbenchspec-build-abc\nother-benchspec-thing"` and `BENCHSPEC_SHIM_IMAGES="benchspec-snapshot:benchspec-docker-claude-code-latest-c30e39d4\nbenchspec-snapshot:other-tag"` logs exactly (sorted) `["images benchspec-snapshot --format {{.Repository}}:{{.Tag}}", "ps -a --filter name=benchspec- --format {{.Names}}", "rm -f benchspec-build-abc", "rm -f benchspec-eval-hello-trial-gw0", "rmi -f benchspec-snapshot:benchspec-docker-claude-code-latest-c30e39d4"]`; `prune` with a missing binary logs nothing; `prune` when `ps` fails (add `BENCHSPEC_SHIM_PS_EXIT`) logs only the `ps` line and does not raise.
- `tests/sandbox/test_backend.py`: `MicrosandboxBackend.prune` against a seeded `HOME` and an `msb` shim (mirror the e2e seeding but as a unit test with `monkeypatch.setattr(backend.Path, "home", ...)` and `MSB_PATH`) removes only `benchspec-*` entries.
- `tests/cli/test_sandbox_clean_e2e.py`:
  - `_run_sandbox_clean` also writes the docker shim and passes `BENCHSPEC_DOCKER_PATH` and `BENCHSPEC_DOCKER_COMMAND_LOG=str(tmp_path / "docker-commands.log")`; accept optional `containers: str = ""` / `images: str = ""` keyword args that set `BENCHSPEC_SHIM_CONTAINERS` / `BENCHSPEC_SHIM_IMAGES`.
  - The existing microsandbox test stays as is, plus one assertion that the docker log holds only the two listing commands (nothing removed).
  - New `test_sandbox_clean_removes_leaked_docker_containers_and_images`: seeds the shim lists above (including the `other-benchspec-thing` decoy and the `other-tag` image), asserts exit 0 and the sorted docker log equals the five-line list from the unit test.
  - New `test_sandbox_clean_with_neither_runtime_is_a_no_op_success`: `MSB_PATH` and `BENCHSPEC_DOCKER_PATH` both point at missing paths under `tmp_path`; exit 0; neither log file exists.
  - The existing no-op test asserts the msb log is absent and the docker log has exactly the two listing lines.

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run to verify failure** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox tests/cli/test_sandbox_clean_e2e.py -q`
- [ ] **Step 3: Implement**
- [ ] **Step 4: `make test` and `make lint:ruff`** — green
- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: prune Docker containers and images from sandbox:clean"
```

---

### Task 5: `sandbox:build` Docker e2e twin and the daemon-gated lifecycle test

**Files:**
- Modify: `tests/cli/test_sandbox_build_e2e.py`
- Create: `tests/sandbox/test_docker_daemon.py`
- Modify: `tests/support/docker.py` only if the build test needs a shim knob it lacks

**Interfaces:**
- Consumes: everything from Tasks 2-4; `run_benchspec`, `SANDBOX_FIXTURES`, `write_docker_shim`.

**Tests to write:**

1. `test_sandbox_build_docker_set_builds_through_the_docker_cli` (shim-backed):

```python
def test_sandbox_build_docker_set_builds_through_the_docker_cli(tmp_path: Path) -> None:
    """Verify a `docker` set drives preflight, build, commit, and identity through the CLI."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    shim = write_docker_shim(tmp_path / "bin")
    command_log = tmp_path / "docker-commands.log"

    result = run_benchspec(
        "sandbox:build",
        str(repo_root),
        "--set",
        "dock",
        "--config",
        str(SANDBOX_FIXTURES / "two-backends.toml"),
        cwd=tmp_path,
        env={
            "BENCHSPEC_DOCKER_PATH": str(shim),
            "BENCHSPEC_DOCKER_COMMAND_LOG": str(command_log),
            "ANTHROPIC_API_KEY": "test-token",
            "BENCHSPEC_CLAUDE_VERSION": "1.2.3",
        },
        drop=("CLAUDE_CODE_OAUTH_TOKEN",),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "building snapshot benchspec-docker-claude-code-1.2.3-" in result.stdout
    assert "built benchspec-docker-claude-code-1.2.3-" in result.stdout
    assert "image identity: sha256:deadbeef" in result.stdout
    commands = command_log.read_text().splitlines()
    assert commands[0] == "info"
    assert any(line.startswith("run -d --name benchspec-build-claude-code ") for line in commands)
    assert any(line.startswith("commit benchspec-build-claude-code benchspec-snapshot:benchspec-docker-claude-code-1.2.3-") for line in commands)
    assert commands[-2].startswith("rm -f benchspec-build-claude-code")
    assert commands[-1].startswith("image inspect --format")
```

   Adjust the exact index assertions to the real command order once observed, but keep every named command asserted. If `make_agent` needs anything else from the environment to construct the Claude agent, set it in `env` and say so in the report.

2. Keep `test_sandbox_build_docker_set_without_docker_cli_fails_preflight` (from Task 3) and `test_sandbox_build_without_credentials_fails_preflight`. Update the module docstring: preflight and config still stop before provisioning; the Docker case additionally runs a shim-backed build end to end.

3. `tests/sandbox/test_docker_daemon.py` — the only test allowed to reach a real daemon:

```python
"""Real-daemon lifecycle test for the Docker backend.

Skipped with an explicit reason unless `docker info` succeeds. Everything it creates
carries a unique suffix and is removed by name in teardown; it never calls `prune()`,
which would sweep the developer's own benchspec snapshots.
"""
```

   - Module-level gate: `_DAEMON_REACHABLE = <docker_binary() is not None and subprocess.run([binary, "info"], capture_output=True, timeout=30).returncode == 0>` guarded so exceptions mean unreachable; `pytestmark = pytest.mark.skipif(not _DAEMON_REACHABLE, reason="Docker daemon unreachable (docker info failed)")`.
   - One test `test_docker_backend_builds_boots_and_tears_down_a_cell`: a fake agent (`id = "probe"`, `guest_home = "/root"`, `provision` runs `sandbox.shell("echo provisioned > /provisioned")` and raises on nonzero, `bridge_skills_home_script()` returns `"true"`, `guest_env()` returns `{}`, `secrets()` returns `[]`, `stage_project_assets` no-op); `name = f"benchspec-docker-probe-test-{uuid4().hex[:8]}"`; `env = EnvConfig(base_image="ubuntu:latest")`. Exercise: `backend.build_snapshot(agent, name, env)`; assert `snapshot_exists(name)`; `image_identity(name).image_digest.startswith("sha256:")`; `create_sandbox(... host_workdir=tmp room, host_repo_root=tmp stage with a file, extra_volumes=sandbox._agent_extra_volumes)` with `name=f"{NAME_PREFIX}eval-probe-test-{suffix}"`; `shell("cat /provisioned && cat /project/marker.txt > /workspace/out.txt")` exits 0 and `room / "out.txt"` holds the marker; `exec_stream("printf", ["a\\nb\\n"])` yields stdout `b"a\nb\n"` (joined) then `exited` 0; `shell("touch /project/nope")` exits nonzero (read-only mount); `await sandbox.stop()`; afterwards `docker ps -a --filter name=<name>` prints nothing. Teardown in `finally`: `docker rm -f <cell name>` and `docker rmi -f benchspec-snapshot:<name>` by name.
   - Keep the whole test under one `asyncio.run` for the async part; sync calls outside it (the backend's `build_snapshot` and `image_identity` run their own loops).

- [ ] **Step 1: Write the tests**
- [ ] **Step 2: Run** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/cli/test_sandbox_build_e2e.py tests/sandbox/test_docker_daemon.py -q -rs`. The daemon test must PASS on this machine (Docker 29 is running); report its runtime. If it fails, fix the backend, not the test, unless the test itself is wrong; report what you changed.
- [ ] **Step 3: `make test` and `make lint:ruff`** — green
- [ ] **Step 4: Commit**

```bash
git add tests
git commit -m "test: cover sandbox:build under Docker end to end and run a real cell against the daemon"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/quickstart.md:9-33, 221-227`
- Modify: `README.md:33-41, 89-93, 115-120`
- Modify: `docs/sandbox.md` (intro, "Host requirements and preflight", snapshot cache sentence at 55, `sandbox:clean` block at 90-100, cell sizing at 105-107, "Credentials")
- Modify: `docs/configuration.md:47, 131-141`
- Modify: `docs/harnesses.md` (the credentials row/bullets near lines 43 and 59-63)
- Modify: `Makefile` (`e2e` help text and the `WORKERS` comment: "each e2e cell reserves a 2 GB sandbox")

**What to say (content requirements, verbatim where quoted):**
- Docker is the default backend: any host with a running Docker daemon runs evals; no platform check. microsandbox is the opt-in for stronger isolation (`sandbox = "microsandbox"` on the set) and needs Apple Silicon or Linux with `/dev/kvm` plus the `microsandbox` extra.
- The tradeoff, stated plainly and first in `docs/sandbox.md`: a container shares the host kernel; the agent runs as root with `bypassPermissions` on the promise that the sandbox is the containment boundary; under Docker that boundary is the container, not a VM. Credentials under Docker are injected as container environment variables and are readable by the agent and by `setup.sh`; there is no host scoping. microsandbox scopes each credential to its provider host at the network boundary.
- Image cache: `docker images benchspec-snapshot` lists the committed snapshots, tagged `benchspec-snapshot:benchspec-docker-<harness>-<version>-<fingerprint>`; each cell is a container named `benchspec-eval-…`; `sandbox:clean` removes `benchspec-*` containers and `benchspec-snapshot:benchspec-*` images alongside the microsandbox entries and lock files.
- Preflight remedies: "docker CLI not found" and "Docker daemon unreachable" join the quickstart's common-failure list; the microsandbox remedies move under the opt-in subsection.
- `docs/configuration.md` `sandbox` row: "`docker` (the default) or `microsandbox`. Docker needs a reachable daemon; microsandbox needs Apple Silicon or Linux with KVM and gives microVM isolation. See `sandbox.md`."
- `docs/harnesses.md`: the credential bullets gain a Docker line: env-var injection, no host scoping; microsandbox keeps the scoped-secret description. Codex `CODEX_AUTH_JSON_PATH` is still a read-only mount under both.
- `README.md`: install line becomes `pip install benchspec` with a following line for the microsandbox extra; the pre-1.0 note names Docker as the default and microsandbox as the isolation opt-in; the platform row reads "Any OS with a Docker daemon (default). Apple Silicon or Linux with `/dev/kvm` for the microsandbox opt-in. Python 3.11+."; the `sandbox:clean` sentence covers Docker resources; the mermaid diagram's "fresh microVM" labels become "fresh sandbox".
- Remove every remaining "only backend" / "fails fast as not implemented" phrase (`grep -rn "not implemented\|only backend\|only sandbox backend\|only implementation" README.md docs/ src/` must return nothing about Docker).
- Open Questions from the spec (rootless Docker / Podman untested; subscription-token exposure; Windows) get one short "Untested" paragraph at the end of the Docker section in `docs/sandbox.md`.

- [ ] **Step 1: Make the doc edits**
- [ ] **Step 2: `make test`** (README examples are tested) and `make lint:ruff`
- [ ] **Step 3: Commit**

```bash
git add README.md docs Makefile
git commit -m "docs: present Docker as the default sandbox and microsandbox as the isolation opt-in"
```

---

## Self-Review

- **Spec coverage:** registry/default flip (Task 3), Docker lifecycle (Tasks 2-3), `sandbox:clean` pruning (Task 4), seam repairs (Task 1), CLI e2e twins and daemon-gated case (Tasks 3-5), lazy-import invariant (Global Constraints + Task 2 stdlib-only + allowlist test), `observed_arms` backend id and image digest (Task 3 `image_identity` + unchanged provenance capture), docs (Task 6).
- **Placeholder scan:** none. `prune` on `DockerBackend` in Task 3 is an explicit no-op placeholder that Task 4 replaces, stated as such.
- **Type consistency:** `Credential(env_var, value, allow_hosts)`; `SandboxError` from `benchspec.sandbox.errors`; `DockerSandbox(name, *, binary)`; `DockerVolume.bind(path, *, readonly=False) -> DockerMount`; `run_argv(name, image, *, mounts, env)`; `registered_backends()`; `fingerprint_inputs_for(backend_id, agent, env)`; `write_docker_shim(shim_dir) -> Path` with knobs `BENCHSPEC_DOCKER_COMMAND_LOG`, `BENCHSPEC_SHIM_EXEC`, `BENCHSPEC_SHIM_INSPECT_EXIT`, `BENCHSPEC_SHIM_INSPECT_FORMAT_EXIT`, `BENCHSPEC_SHIM_INFO_EXIT`, `BENCHSPEC_SHIM_PS_EXIT`, `BENCHSPEC_SHIM_RM_EXIT`, `BENCHSPEC_SHIM_CONTAINERS`, `BENCHSPEC_SHIM_IMAGES`.

---

### Task 7: Split the seam from the concrete backends and the registry

**Files:**
- Create: `src/benchspec/sandbox/microsandbox.py`
- Create: `src/benchspec/sandbox/registry.py`
- Modify: `src/benchspec/sandbox/backend.py` (trim to the seam)
- Modify: `src/benchspec/config/arms.py:19`, `src/benchspec/orchestration/execution.py:31`, `src/benchspec/orchestration/cases.py:36`, `src/benchspec/sandbox/sandbox.py:25-33` (import sites)
- Modify: `docs/sandbox.md` (the closing "authoritative modules" sentence)
- Test: `tests/sandbox/test_backend.py` (seam tests stay), `tests/sandbox/test_microsandbox.py` (new; microsandbox tests move here), `tests/sandbox/test_sandbox.py`, `tests/sandbox/test_docker_daemon.py`, `tests/orchestration/test_execution.py`, and any other test that references `backend.resolve_sandbox`, `backend.DEFAULT_SANDBOX`, `backend.registered_backends`, `backend.MicrosandboxBackend`, `backend.msb_binary`, `backend.microsandbox_secrets`, `backend.MicrosandboxGuest`, or monkeypatches `backend.platform` / `backend.Path`

**Interfaces:**
- Consumes: everything Tasks 1-6 produced. No behavior changes.
- Produces (module layout after the split; every public name keeps its current signature):
  ```python
  # benchspec/sandbox/backend.py — the runtime-free seam, nothing else
  GUEST_WORKDIR, PROJECT_MOUNT, host_mount_path, BASE_IMAGE, NAME_PREFIX, VM_CPUS, VM_MEMORY_MIB
  FingerprintInputs, SandboxBackend, fingerprint_inputs_for, bridge_skills_home, run_environment_script, SharedBackendBehavior
  SandboxError            # still re-exported from benchspec.sandbox.errors

  # benchspec/sandbox/microsandbox.py — the microVM backend (mirrors docker.py)
  msb_binary, microsandbox_secrets, MicrosandboxGuest, MicrosandboxExecHandle, MicrosandboxBackend
  # plus the private error translator; every `import microsandbox` stays inside a method body

  # benchspec/sandbox/registry.py — the one place that names concrete runtimes
  DEFAULT_SANDBOX = "docker"
  def registered_backends() -> dict[str, type]     # {"microsandbox": MicrosandboxBackend, "docker": DockerBackend}
  def resolve_sandbox(name: str) -> SandboxBackend
  ```
- Dependency direction after the split, enforced by plain top-of-file imports: `registry → {docker, microsandbox} → backend → {errors, provenance, agents}`. No lazy import remains in `registry.py`; `docker.py` does not change.

**Rules:**
- Pure motion: no function body changes beyond import statements and docstrings. The suite must stay at 1024 passing with no test deleted; tests move, they do not disappear.
- Module docstrings: `backend.py` describes the seam and points at the two backend modules and the registry; `microsandbox.py` carries the lazy-import invariant ("importing this module must NOT import the `microsandbox` package; every `import microsandbox` stays inside a method body") and notes that absolute imports keep its own name from shadowing the third-party package; `registry.py` says it is the only module that names a concrete runtime and that adding a backend means implementing the protocol and registering it here.
- The microsandbox import allowlist test becomes `{"microsandbox.py"}`.
- `docs/sandbox.md`'s closing paragraph names `benchspec.sandbox.backend` (the seam), `benchspec.sandbox.docker` and `benchspec.sandbox.microsandbox` (the backends), `benchspec.sandbox.registry` (backend selection), `benchspec.sandbox.sandbox` (the cell lifecycle), and `benchspec.sandbox.project` (the `/project` stage).
- `grep -rn "lazily\|import DockerBackend" src/benchspec/sandbox/backend.py` returns nothing afterwards.

- [ ] **Step 1: Create `microsandbox.py` and `registry.py` by moving code**, then trim `backend.py`.
- [ ] **Step 2: Retarget every import site** listed above; `grep -rn "sandbox.backend import" src tests` shows only seam names imported from `backend`.
- [ ] **Step 3: Move and retarget tests**; run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytester tests/sandbox tests/config tests/orchestration -q` while iterating.
- [ ] **Step 4: `make test` (1024 passed) and `make lint:ruff`**; `make lint:houserules` if `GEMINI_API_KEY` is set.
- [ ] **Step 5: Commit**

```bash
git add -A src tests docs/sandbox.md
git commit -m "refactor: split the sandbox seam from the concrete backends and the registry"
```
