"""Manage microsandbox snapshots and per-arm eval sessions."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import shlex
from dataclasses import replace
from pathlib import Path

from evalspec import workspace
from evalspec.agents import CodingAgent, credential_preflight_error, make_agent
from evalspec.arms import parse_sets, resolve_set
from evalspec.backend import BASE_IMAGE, DEFAULT_SANDBOX, SandboxBackend, resolve_sandbox
from evalspec.room import (
    changed_paths,
    parse_artifact_stream,
    parse_sha_stream,
    read_files_script,
    sha_snapshot_script,
    to_display_paths,
)
from evalspec.specs.discovery import EnvConfig, pyproject_table, resolve_environment_config
from evalspec.specs.schema import SchemaError
from evalspec.trigger import RoutingError

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"


def snapshot_name(
    agent: CodingAgent, env: EnvConfig | None = None, *, backend: SandboxBackend
) -> str:
    """Build the snapshot cache name for an agent, environment, and backend.

    The backend id prefixes the name (two backends over one agent+env never collide)
    and the backend owns the fingerprint (base-image digest, install inputs, env bytes).
    """
    fingerprint = backend.cache_fingerprint(agent, env or EnvConfig())
    return f"evalspec-{backend.id}-{agent.id}-{agent.version()}-{fingerprint}"


def preflight(backend: SandboxBackend | None = None) -> None:
    """Fail fast if the host can't run sandboxed evals.

    Host-readiness checks come from the resolved backend (default: microsandbox); the
    credential check is shared across backends. Raises RuntimeError (the exit-2 signal)
    listing every failure.
    """
    backend = backend or resolve_sandbox(DEFAULT_SANDBOX)
    errs: list[str] = list(backend.preflight())
    cred_err = credential_preflight_error()
    if cred_err:
        errs.append(cred_err)
    if errs:
        raise RuntimeError("evalspec sandbox preflight failed:\n  - " + "\n  - ".join(errs))


@contextlib.contextmanager
def _file_lock(path: Path) -> object:
    """Hold an exclusive filesystem lock for snapshot build coordination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


async def _snapshot_artifact_shas(
    sandbox: object, agent: object, backend: SandboxBackend
) -> dict | None:
    """Snapshot agent artifact paths to SHA-256 digests inside the VM."""
    dirs = agent.artifact_dirs()
    if not dirs:
        return {}
    out = await backend.guest_shell(sandbox, agent, sha_snapshot_script(dirs))
    return None if out is None else parse_sha_stream(out)


async def _read_authored(
    sandbox: object, agent: object, baseline_shas: dict | None, backend: SandboxBackend
) -> dict:
    """Return display-path content for files authored since `baseline_shas`."""
    if baseline_shas is None:
        return {}
    current = await _snapshot_artifact_shas(sandbox, agent, backend)
    if current is None:
        return {}
    paths = changed_paths(baseline_shas, current)
    if not paths:
        return {}
    out = await backend.guest_shell(sandbox, agent, read_files_script(paths))
    if not out:
        return {}
    return to_display_paths(parse_artifact_stream(out), agent.guest_home)


def ensure_snapshot(
    agent: object, *, repo_root: object, backend: SandboxBackend, env: object = None
) -> str:
    """Return the snapshot name, building it once (file-locked) if missing.

    Folds the host's optional environment config into both the snapshot name (cache
    identity) and the build via the backend. `env` may be passed pre-resolved so a caller
    that also records provenance uses the exact same config that selected the snapshot;
    when omitted it is resolved from `repo_root`. The lock serializes concurrent xdist
    workers: losers wait, then return the built snapshot.
    """
    if env is None:
        env = resolve_environment_config(repo_root)
    name = snapshot_name(agent, env, backend=backend)
    if backend.snapshot_exists(name):
        return name
    lock_path = workspace.workspace_parent(repo_root) / f".evalspec-snapshot-{name}.lock"
    with _file_lock(lock_path):
        if not backend.snapshot_exists(name):
            backend.build_snapshot(agent, name, env)
    return name


def _worker_tag() -> str:
    """Return the pytest worker suffix used for per-worker snapshot names."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def _sandbox_run_name(eval_id: str, config: str) -> str:
    """Build a stable microsandbox run name for a snapshot or cell."""
    return f"eval-{eval_id}-{config}-{_worker_tag()}"


DEFAULT_PROJECT_MARKER = ".claude-plugin/plugin.json"


def _plugin_dir_for(host_repo_root: object, marker: str = DEFAULT_PROJECT_MARKER) -> str | None:
    """Resolve the plugin directory mounted for an eval run."""
    # --plugin-dir only when the project has the configured marker. Local skills reach the
    # guest by the trigger path's stage_project_assets (_create_trigger_sandbox); output
    # evals install per-cell via setup.sh instead.
    if host_repo_root is None:
        return None
    if (Path(host_repo_root) / marker).is_file():
        return PROJECT_MOUNT
    return None


def _agent_extra_volumes(agent: object, volume_cls: object) -> dict:
    """Optional agent-owned host files that must be visible in the guest."""
    path_getter = getattr(agent, "auth_json_path", None)
    if not callable(path_getter):
        return {}
    path = path_getter()
    if not path:
        return {}
    return {
        "/evalspec-codex-auth/auth.json": volume_cls.bind(str(path), readonly=True),
    }


async def run_setup_sh(
    sandbox: object,
    agent: object,
    *,
    setup_reldir: str,
    arm: str,
    model: str,
    eval_set: str = "",
    arm_env: dict | None = None,
) -> None:
    """Run an eval's own setup.sh inside the arm sandbox when present.

    Locates the script at `PROJECT_MOUNT/<setup_reldir>/setup.sh` (the eval folder's
    path relative to the repo root). Missing ⇒ no-op; present ⇒ runs under bash from
    the eval dir and fails loudly on a nonzero exit. The `EVALSPEC_*` cell env reaches
    the script.
    """
    env = {**agent.cell_env(arm=arm, model=model, eval_set=eval_set), **(arm_env or {})}
    eval_dir = shlex.quote(f"{PROJECT_MOUNT}/{setup_reldir}")
    script = (
        "set -e\n"
        f"if [ -f {eval_dir}/setup.sh ]; then cd {eval_dir}; bash ./setup.sh; fi"
    )
    res = await sandbox.shell(script, env=env, cwd=PROJECT_MOUNT)
    if res.exit_code != 0:
        raise RuntimeError(
            f"setup.sh failed for `{setup_reldir}` arm `{arm}` "
            f"(exit {res.exit_code}): {res.stderr_text[-2000:]}"
        )


class SandboxSession:
    """Async context manager holding one microVM open across an arm's turns.

    `__aenter__` boots the VM and returns a per-turn async `run`; `__aexit__` tears it
    down.

    The whole lifecycle (create → turns → stop) runs inside a single `asyncio.run`.
    """

    def __init__(
        self: object,
        *,
        agent: object,
        snapshot: object,
        eval_id: object,
        config: object,
        host_workdir: object,
        host_repo_root: object,
        model: object,
        effort: object,
        backend: SandboxBackend,
        setup_reldir: str | None = None,
        arm: str | None = None,
        arm_env: dict | None = None,
        eval_set: str = "",
        project_marker: str = DEFAULT_PROJECT_MARKER,
        harness_args: list[str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._agent = agent
        self._snapshot = snapshot
        self._eval_id = eval_id
        self._config = config
        self._host_workdir = host_workdir
        self._host_repo_root = host_repo_root
        self._model = model
        self._effort = effort
        self._backend = backend
        # Per-arm environment, already expanded, is shared by setup.sh and the agent exec.
        self._arm_env = arm_env
        self._eval_set = eval_set
        self._harness_args = harness_args
        # The eval folder's path relative to the mount; None skips per-cell setup.
        self._setup_reldir = setup_reldir
        self._arm = arm if arm is not None else config
        self._project_marker = project_marker

    async def __aenter__(self: object) -> object:
        """Enter the arm session and capture baseline artifact state."""
        self._sandbox = await self._backend.create_sandbox(
            agent=self._agent,
            snapshot=self._snapshot,
            name=_sandbox_run_name(self._eval_id, self._config),
            host_workdir=self._host_workdir,
            host_repo_root=self._host_repo_root,
            extra_volumes=_agent_extra_volumes,
        )
        # Install before the artifact baseline so only later agent-authored files surface.
        if self._setup_reldir is not None:
            try:
                await run_setup_sh(
                    self._sandbox,
                    self._agent,
                    setup_reldir=self._setup_reldir,
                    arm=self._arm,
                    model=self._model,
                    eval_set=self._eval_set,
                    arm_env=self._arm_env,
                )
            except BaseException:
                await self._backend.stop_quietly(self._sandbox)
                raise
        self._artifact_base = await _snapshot_artifact_shas(
            self._sandbox, self._agent, self._backend
        )
        return self._run

    async def _run(
        self: object, prompt: object, *, resume_session_id: object, detect_skill: object
    ) -> object:
        """Provide the run helper."""
        result = await self._agent.invoke(
            self._sandbox,
            prompt,
            eval_id=self._eval_id,
            config=self._config,
            workdir=GUEST_WORKDIR,
            plugin_dir=None,
            model=self._model,
            effort=self._effort,
            resume_session_id=resume_session_id,
            detect_skill=detect_skill,
            extra_env=self._arm_env,
            harness_args=self._harness_args,
        )
        # Capture new or changed skill artifacts written outside the workdir mount.
        authored = await _read_authored(
            self._sandbox, self._agent, self._artifact_base, self._backend
        )
        if authored:
            result = replace(result, artifacts=authored)
        return result

    async def __aexit__(self: object, *exc: object) -> object:
        """Close the arm session and release sandbox resources."""
        await self._sandbox.stop()


def arm_session(
    *,
    agent: object,
    snapshot: object,
    eval_id: object,
    config: object,
    host_workdir: object,
    host_repo_root: object,
    model: object,
    effort: object,
    backend: SandboxBackend,
    setup_reldir: str | None = None,
    arm: str | None = None,
    arm_env: dict | None = None,
    eval_set: str = "",
    project_marker: str = DEFAULT_PROJECT_MARKER,
    harness_args: list[str] | None = None,
) -> SandboxSession:
    """Open an async arm session around one sandboxed eval cell."""
    return SandboxSession(
        agent=agent,
        snapshot=snapshot,
        eval_id=eval_id,
        config=config,
        host_workdir=host_workdir,
        host_repo_root=host_repo_root,
        model=model,
        effort=effort,
        backend=backend,
        setup_reldir=setup_reldir,
        arm=arm,
        arm_env=arm_env,
        eval_set=eval_set,
        project_marker=project_marker,
        harness_args=harness_args,
    )


def _trigger_command(
    agent: object,
    query: object,
    repo_root: object,
    model: object,
    effort: object,
    project_marker: object,
) -> list[str]:
    """Build the command that asks an agent to route a trigger query."""
    plugin = _plugin_dir_for(repo_root, project_marker)
    # Non-None sentinel requesting streamable routing output.
    return agent.build_command(
        query,
        plugin_dir=plugin,
        model=model,
        effort=effort,
        resume_session_id=None,
        detect_skill="__route__",
    )


async def _route_in_sandbox_async(
    query: object,
    repo_root: object,
    model: object,
    timeout: object,
    *,
    effort: object,
    skill_name: object,
    agent: object,
    snapshot: object,
    backend: SandboxBackend,
    project_marker: object = DEFAULT_PROJECT_MARKER,
) -> object:
    """Route in sandbox async."""
    # Snapshot resolution happens before this coroutine because snapshot builds run loops.
    sandbox = await backend.create_trigger_sandbox(
        agent=agent,
        snapshot=snapshot,
        name=f"trigger-{_worker_tag()}",
        host_repo_root=repo_root,
        extra_volumes=_agent_extra_volumes,
    )
    lines: list[str] = []
    dispatched = False
    exit_code: int | None = None
    try:
        cmd = _trigger_command(agent, query, repo_root, model, effort, project_marker)
        handle = await sandbox.exec_stream(
            cmd[0],
            cmd[1:],
            cwd=agent.guest_home,
            env=agent.guest_env(),
            # Force EOF on stdin so routing cannot block on an open pipe.
            stdin=b"",
        )

        async def _drain() -> None:
            """Drain streamed exec events into output lines and exit state."""
            nonlocal dispatched, exit_code
            # Reassemble arbitrary stdout chunks into complete JSONL lines before detection.
            buffer = ""
            async for event in handle:
                if event.event_type == "stdout":
                    buffer += event.data.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        lines.append(line)
                        if agent.detect_dispatch(line, skill_name):
                            dispatched = True
                            await handle.kill()
                            return
                elif event.event_type == "exited":
                    exit_code = event.code
                elif event.event_type == "failed":
                    # Preserve real exit code 0.
                    exit_code = event.code if event.code is not None else 1
            # Flush a trailing partial JSONL line if the process exits without a newline.
            if buffer.strip():
                lines.append(buffer)

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
            await backend.kill_quietly(handle)
    finally:
        await backend.stop_quietly(sandbox)

    if dispatched:
        return lines
    if timed_out:
        if agent.streamed_activity(lines):
            return lines
        raise RoutingError(f"routing streamed no model activity before the {timeout}s cutoff")
    if (exit_code not in (None, 0)) or not any(line.strip() for line in lines):
        raise RoutingError(
            f"routing exited {exit_code} with {sum(len(line) for line in lines)} bytes of stdout"
        )
    return lines


def route_in_sandbox(
    query: object,
    repo_root: object,
    model: object,
    timeout: object,
    *,
    effort: object = "low",
    skill_name: object = None,
    project_marker: object = DEFAULT_PROJECT_MARKER,
) -> object:
    """Run trigger routing inside a sandbox and return the fire count."""
    # Resolve agent + snapshot up-front: a missing snapshot triggers build_snapshot
    # → asyncio.run, which can't nest inside the asyncio.run below.
    agent = make_agent()
    backend = resolve_sandbox(DEFAULT_SANDBOX)
    snapshot = ensure_snapshot(agent, repo_root=repo_root, backend=backend)
    return asyncio.run(
        _route_in_sandbox_async(
            query,
            repo_root,
            model,
            timeout,
            effort=effort,
            skill_name=skill_name,
            agent=agent,
            snapshot=snapshot,
            project_marker=project_marker,
            backend=backend,
        )
    )


def _layer_build_config(table: dict, config_path: str | None) -> dict:
    """Merge a `--config` file's [tool.evalspec.sets.*] over the pyproject table.

    Mirrors plugin._layer_config_sets for the build path. A malformed config (missing
    [tool.evalspec]) raises SchemaError, which the CLI maps to exit 2.
    """
    if not config_path:
        return table
    import tomllib

    try:
        raw = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as err:
        raise SchemaError(f"--config {config_path}: {err}") from None
    scratch = raw.get("tool", {}).get("evalspec")
    if not isinstance(scratch, dict):
        raise SchemaError(
            f"--config {config_path}: expected a [tool.evalspec] table with "
            "[tool.evalspec.sets.<name>]"
        )
    # Both layers' `sets` must be tables; a scalar/array (e.g. `sets = ["oops"]`) would raise a
    # bare `TypeError: ... is not a mapping` on the unpack below instead of the exit-2 SchemaError.
    base_sets = table.get("sets", {})
    if not isinstance(base_sets, dict):
        raise SchemaError("pyproject [tool.evalspec].sets must be a table")
    scratch_sets = scratch.get("sets", {})
    if not isinstance(scratch_sets, dict):
        raise SchemaError(f"--config {config_path}: [tool.evalspec].sets must be a table")
    merged = dict(table)
    merged["sets"] = {**base_sets, **scratch_sets}
    if scratch.get("default-set"):
        merged["default-set"] = scratch["default-set"]
    return merged


def cli_build(
    repo_root: Path | None = None, *, set_name: str | None = None, config: str | None = None
) -> None:
    """Build the agent-ready snapshot(s) (loud on error).

    With neither `--set` nor `--config`, preserves the Phase-5 bare-build path: a single
    `make_agent()`, env from the repo root, and the default microsandbox backend — NO sets
    table required. With `--set`/`--config`, layers config over pyproject, resolves the set,
    and drives the resolved set's sandbox backend (a `docker` set raises SchemaError → exit 2),
    building one snapshot per DISTINCT harness in the set (two arms sharing a harness build
    once) via `make_agent(harness)`.

    Args:
        repo_root: Repo root whose pyproject + environment config drive the build.
            Defaults to the current working directory.
        set_name: The eval set to resolve (`--set`); None with no config selects the bare path.
        config: An optional `--config` file layered over pyproject.
    """
    root = repo_root or Path.cwd()
    if set_name or config:
        table = _layer_build_config(pyproject_table(root), config)
        rawsets, default_set = parse_sets(table)
        resolved = resolve_set(rawsets, default_set, set_name=set_name)
        backend = resolve_sandbox(resolved.sandbox)
        preflight(backend)
        env = resolve_environment_config(root)
        # dict.fromkeys dedupes while preserving first-seen order — two arms sharing a
        # harness build once.
        for harness in dict.fromkeys(resolved_arm.harness for resolved_arm in resolved.arms):
            _build_or_reuse_snapshot(make_agent(harness), env, backend)
    else:
        backend = resolve_sandbox(DEFAULT_SANDBOX)
        preflight(backend)
        env = resolve_environment_config(root)
        _build_or_reuse_snapshot(make_agent(), env, backend)


def _build_or_reuse_snapshot(agent: CodingAgent, env: EnvConfig, backend: SandboxBackend) -> None:
    """Build `agent`'s snapshot on `backend` if absent, then report it and its image identity.

    Preserves the existing "built" vs "reused" ("already present") reporting distinction,
    and additionally surfaces each snapshot's `image_identity()` status: available
    prints the digest, unavailable prints the explaining error rather than a bare null.
    """
    name = snapshot_name(agent, env, backend=backend)
    if backend.snapshot_exists(name):
        print(f"snapshot {name} already present")
    else:
        print(f"building snapshot {name} from {_display_base_image(env)} ...")
        backend.build_snapshot(agent, name, env)
        print(f"built {name}")
    identity = backend.image_identity(name)
    if identity.image_digest_status == "available":
        print(f"  image identity: {identity.image_digest}")
    else:
        print(f"  image identity: unavailable ({identity.image_digest_error})")


def _display_base_image(env: EnvConfig) -> str:
    """Return the base image label for the build message (default when unset)."""
    return env.base_image or BASE_IMAGE


def cli_clean(repo_root: Path) -> None:
    """Remove evalspec sandboxes and snapshots via the msb CLI.

    Also removes the per-repo snapshot lock files under `<repo_root>/tmp/`.

    Tolerates 'none found'. Snapshots are regenerable via `make evals:build`.
    """
    import subprocess

    def _msb(*args: object) -> None:
        """Provide the msb helper."""
        try:
            subprocess.run(["msb", *args], check=False)
        except FileNotFoundError:
            pass  # msb CLI not installed — nothing to prune via it

    home = Path.home() / ".microsandbox"
    for sub, prefixes in (
        ("sandboxes", ("eval-", "evalspec-build-", "trigger-")),
        ("snapshots", ("evalspec-",)),
    ):
        target_dir = home / sub
        if not target_dir.is_dir():
            continue
        for entry in target_dir.iterdir():
            if entry.name.startswith(prefixes):
                if sub == "sandboxes":
                    _msb("stop", entry.name)
                    _msb("rm", "-f", entry.name)
                else:
                    _msb("snapshot", "rm", "--force", entry.name)
    for lock in workspace.workspace_parent(repo_root).glob(".evalspec-snapshot-*.lock"):
        lock.unlink(missing_ok=True)
