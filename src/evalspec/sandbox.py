"""Document the behavior."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import platform
import shlex
from dataclasses import replace
from pathlib import Path

from evalspec import workspace
from evalspec.agents import CodingAgent, credential_preflight_error, make_agent
from evalspec.discovery import EnvConfig, resolve_environment_config
from evalspec.room import (
    changed_paths,
    parse_artifact_stream,
    parse_sha_stream,
    read_files_script,
    sha_snapshot_script,
    to_display_paths,
)
from evalspec.trigger import RoutingError

GUEST_WORKDIR = "/workspace"
PROJECT_MOUNT = "/project"
BASE_IMAGE = "ubuntu:latest"
# microsandbox's ~512 MiB default OOM-kills the agent CLI installer (exit 137) and would
# starve the agent doing real work, so size the VMs to match interactive use.
VM_CPUS = 2
VM_MEMORY_MIB = 2048


def snapshot_name(agent: CodingAgent, env: EnvConfig | None = None) -> str:
    """Handle snapshot_name."""
    base = f"evalspec-{agent.id}-{agent.version()}"
    if env:
        return f"{base}-{env.digest()}"
    return base


def snapshot_exists(name: str) -> bool:
    """Handle snapshot_exists."""
    return (Path.home() / ".microsandbox" / "snapshots" / name).exists()


def _microsandbox_installed() -> bool:
    """Handle _microsandbox_installed."""
    try:
        import microsandbox
    except ImportError:
        return False
    return bool(microsandbox.is_installed())


def preflight() -> None:
    """Fail fast if the host can't run sandboxed evals."""
    errs: list[str] = []
    system = platform.system()
    if system == "Darwin":
        if platform.machine() != "arm64":
            errs.append("x86_64 macOS is unsupported; microsandbox needs Apple Silicon")
    elif system == "Linux":
        if not Path("/dev/kvm").exists():
            errs.append("KVM not available (/dev/kvm missing)")
    else:
        errs.append(f"unsupported platform: {system} (need Apple Silicon or Linux+KVM)")
    if not _microsandbox_installed():
        errs.append("microsandbox runtime not installed — run `make evals:build`")
    cred_err = credential_preflight_error()
    if cred_err:
        errs.append(cred_err)
    if errs:
        raise RuntimeError("evalspec sandbox preflight failed:\n  - " + "\n  - ".join(errs))


@contextlib.contextmanager
def _file_lock(path: Path) -> object:
    """Handle _file_lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


async def _guest_shell(sb: object, agent: object, script: str) -> str | None:
    """Run `script` in the guest, returning stdout on success or None on any failure.

    Capture is best-effort: a failed snapshot just means nothing extra to grade, never a
    failed arm.
    """
    from microsandbox.errors import MicrosandboxError

    try:
        res = await sb.shell(script, env=agent.guest_env())
    except (MicrosandboxError, asyncio.TimeoutError, OSError):
        return None
    return res.stdout_text if res.exit_code == 0 else None


async def _snapshot_artifact_shas(sb: object, agent: object) -> dict | None:
    """Document the behavior."""
    dirs = agent.artifact_dirs()
    if not dirs:
        return {}
    out = await _guest_shell(sb, agent, sha_snapshot_script(dirs))
    return None if out is None else parse_sha_stream(out)


async def _read_authored(sb: object, agent: object, baseline_shas: dict | None) -> dict:
    """Files the agent authored in its artifact dirs since `baseline_shas`, as.

    {display-path: content}. Hashes first, then streams bytes for ONLY the changed/new
    paths — empty (no extra shell) when the agent wrote nothing there.

    Captures nothing when either snapshot failed (baseline or the current pass is
    `None`): a failed snapshot means "capture nothing this arm," never "everything is
    new" — which would grade the staged (given) skills as if the agent authored them.
    """
    if baseline_shas is None:
        return {}
    current = await _snapshot_artifact_shas(sb, agent)
    if current is None:
        return {}
    paths = changed_paths(baseline_shas, current)
    if not paths:
        return {}
    out = await _guest_shell(sb, agent, read_files_script(paths))
    if not out:
        return {}
    return to_display_paths(parse_artifact_stream(out), agent.guest_home)


async def _stop_quietly(sb: object) -> None:
    """Best-effort VM teardown: swallow only runtime/OS errors so a teardown hiccup.

    never masks the real flow (and never leaks the microVM).
    """
    from microsandbox.errors import MicrosandboxError

    with contextlib.suppress(MicrosandboxError, asyncio.TimeoutError, OSError):
        await sb.stop()


async def _run_environment_script(sb: object, agent: object, env: EnvConfig) -> None:
    """Run the host's environment script after the agent provisions, before sealing.

    Prepend `set -e` so the FIRST failing command aborts — a mid-script failure must not
    seal a half-provisioned snapshot under a success hash. Plain `set -e` only: the
    guest `/bin/sh` is dash, which rejects `pipefail`, and `-u` would fail valid host
    scripts that reference unset vars. Fail loud: a nonzero exit raises, surfacing the
    tail of stderr.
    """
    if not env.script:
        return
    script = b"set -e\n" + env.script
    res = await sb.shell(script.decode(), env=agent.guest_env())
    if res.exit_code != 0:
        raise RuntimeError(
            f"environment_script failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
        )


async def _bridge_skills_home(sb: object, agent: object) -> None:
    """Document the behavior."""
    res = await sb.shell(agent.bridge_skills_home_script(), env=agent.guest_env())
    if res.exit_code != 0:
        raise RuntimeError(
            f"skills-home bridge failed (exit {res.exit_code}): {res.stderr_text[-2000:]}"
        )


async def _build_snapshot_async(agent: object, name: str, env: EnvConfig) -> None:
    """Handle _build_snapshot_async."""
    from microsandbox import Sandbox, Snapshot

    base_image = env.base_image or BASE_IMAGE
    build_name = f"evalspec-build-{agent.id}"
    sb = await Sandbox.create(
        build_name, image=base_image, cpus=VM_CPUS, memory=VM_MEMORY_MIB, replace=True
    )
    try:
        await agent.provision(sb)
        await _bridge_skills_home(sb, agent)
        await _run_environment_script(sb, agent, env)
        await sb.stop()  # snapshots require a stopped sandbox
        await Snapshot.create(build_name, name=name, record_integrity=True)
    finally:
        from microsandbox.errors import MicrosandboxError

        with contextlib.suppress(MicrosandboxError, OSError):
            await Sandbox.remove(build_name)


def build_snapshot(agent: object, name: str, env: EnvConfig) -> None:
    """Handle build_snapshot."""
    asyncio.run(_build_snapshot_async(agent, name, env))


def ensure_snapshot(agent: object, *, repo_root: object) -> str:
    """Return the snapshot name, building it once (file-locked) if missing.

    Resolves the host's optional environment config from `repo_root` and folds it into
    both the snapshot name (cache identity) and the build, so any change to `base_image`
    or `environment_script` auto-rebuilds. The lock serializes concurrent xdist workers:
    losers wait, then find the snapshot already built and return.
    """
    env = resolve_environment_config(repo_root)
    name = snapshot_name(agent, env)
    if snapshot_exists(name):
        return name
    lock_path = workspace.workspace_parent(repo_root) / f".evalspec-snapshot-{name}.lock"
    with _file_lock(lock_path):
        if not snapshot_exists(name):
            build_snapshot(agent, name, env)
    return name


def _worker_tag() -> str:
    """Handle _worker_tag."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def _sandbox_run_name(eval_id: str, config: str) -> str:
    """Handle _sandbox_run_name."""
    return f"eval-{eval_id}-{config}-{_worker_tag()}"


DEFAULT_PROJECT_MARKER = ".claude-plugin/plugin.json"


def _plugin_dir_for(host_repo_root: object, marker: str = DEFAULT_PROJECT_MARKER) -> str | None:
    """Handle _plugin_dir_for."""
    # --plugin-dir only when the project has the configured marker. Local skills reach the
    # guest by the trigger path's stage_project_assets (_create_trigger_sandbox); output
    # evals install per-cell via setup.sh instead.
    if host_repo_root is None:
        return None
    if (Path(host_repo_root) / marker).is_file():
        return PROJECT_MOUNT
    return None


def _agent_extra_volumes(agent: object, volume_cls: object) -> dict:
    """Optional agent-owned host files that must be visible in the guest.

    Codex can run against a ChatGPT subscription login by copying a host `auth.json`
    into the guest before `codex exec`; the agent exposes that path without widening the
    core CodingAgent protocol for agents that do not need file credentials.
    """
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
    sb: object,
    agent: object,
    *,
    skill: str,
    arm: str,
    model: str,
    eval_set: str = "",
    arm_env: dict | None = None,
) -> None:
    """Document the behavior."""
    env = {**agent.cell_env(arm=arm, model=model, eval_set=eval_set), **(arm_env or {})}
    # An absent setup.sh is a clean no-op; a present-but-failing one must propagate its
    # exit, so `if [ -f ]; then ...; fi` under `set -e` (never `... || true`, which masks
    # the failure). setup.sh authors may use bashisms, so the body runs under `bash`; the
    # outer wrapper stays POSIX-sh in case a future base image drops bash. A missing suite
    # dir leaves cwd at PROJECT_MOUNT (no `./evals/setup.sh` there) — still a clean no-op.
    safe_skill = shlex.quote(skill)
    script = (
        "set -e\n"
        "for root in skills .claude/skills; do\n"
        f'  if [ -d "{PROJECT_MOUNT}/$root/"{safe_skill} ]; then\n'
        f'    cd "{PROJECT_MOUNT}/$root/"{safe_skill}; break\n'
        "  fi\n"
        "done\n"
        "if [ -f ./evals/setup.sh ]; then bash ./evals/setup.sh; fi"
    )
    res = await sb.shell(script, env=env, cwd=PROJECT_MOUNT)
    if res.exit_code != 0:
        raise RuntimeError(
            f"setup.sh failed for skill `{skill}` arm `{arm}` "
            f"(exit {res.exit_code}): {res.stderr_text[-2000:]}"
        )


async def _create_sandbox(
    *,
    agent: object,
    snapshot: object,
    name: object,
    host_workdir: object,
    host_repo_root: object,
) -> object:
    """Handle _create_sandbox."""
    from microsandbox import Sandbox, Volume

    # The project mounts read-only at /project for BOTH arms: per-cell setup.sh needs
    # /project/skills/<skill> or /project/.claude/skills/<skill> present (whichever eval
    # root holds the suite). The install is the only per-arm asymmetry; the baseline arm's
    # setup.sh is `exit 0`. The implicit `cp .claude/skills` staging stage_project_assets
    # did is retired for output evals — the skills-home bridge + setup.sh replace it.
    volumes = {GUEST_WORKDIR: Volume.bind(str(host_workdir), readonly=False)}
    if host_repo_root is not None:
        volumes[PROJECT_MOUNT] = Volume.bind(str(host_repo_root), readonly=True)
    volumes.update(_agent_extra_volumes(agent, Volume))
    return await Sandbox.create(
        name,
        snapshot=snapshot,
        volumes=volumes,
        secrets=agent.secrets(),
        cpus=VM_CPUS,
        memory=VM_MEMORY_MIB,
        replace=True,
    )


class SandboxSession:
    """Async context manager holding one microVM open across an arm's turns.

    `__aenter__`.     boots the VM and returns a per-turn async `run`; `__aexit__` tears
    it down.

    The whole lifecycle (create → turns → stop) MUST run inside a single `asyncio.run`:
    microsandbox's `Sandbox` is bound to the event loop it was created in and raises "no
    running event loop" if used from a separate loop activation. The snapshot is
    resolved by the caller *before* the `asyncio.run`, so building it (which itself uses
    `asyncio.run`) never nests inside a running loop.
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
        skill: str | None = None,
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
        # Per-arm leaky env (e.g. ANTHROPIC_BASE_URL for an OpenRouter arm), already
        # $VAR-expanded by the caller. Threads into setup.sh's env and the agent's exec
        # env; `eval_set` only stamps EVALSPEC_SET for setup.sh.
        self._arm_env = arm_env
        self._eval_set = eval_set
        self._harness_args = harness_args
        # `skill` is the suite whose `setup.sh` installs the skill for this cell; `arm` is
        # the arm-name string EVALSPEC_ARM carries to that setup.sh. `skill` defaults to
        # None so callers with no skill suite skip setup.sh (the gate in __aenter__ reads
        # `_skill` alone); `arm` defaults to the arm name (`config`) when unspecified.
        # `is not None`, not `or`: a stray empty-string `arm` is a caller bug that must
        # reach setup.sh as the visible mismatch it is, not get masked by `config`.
        self._skill = skill
        self._arm = arm if arm is not None else config
        self._project_marker = project_marker

    async def __aenter__(self: object) -> object:
        """Handle __aenter__."""
        self._sb = await _create_sandbox(
            agent=self._agent,
            snapshot=self._snapshot,
            name=_sandbox_run_name(self._eval_id, self._config),
            host_workdir=self._host_workdir,
            host_repo_root=self._host_repo_root,
        )
        # Install the skill for this cell BEFORE the artifact baseline: the populated skills
        # home is the "before" the per-turn diff subtracts, so only agent-authored files
        # surface. A non-zero setup.sh aborts the cell loudly (run_setup_sh raises); stop the
        # VM first, since the caller's `async with` never entered. Gated on `_skill is not
        # None`: a live arm passes `skill` so this runs on every production cell; only callers
        # that omit `skill` (e.g. trigger routing) skip it.
        if self._skill is not None:
            try:
                await run_setup_sh(
                    self._sb,
                    self._agent,
                    skill=self._skill,
                    arm=self._arm,
                    model=self._model,
                    eval_set=self._eval_set,
                    arm_env=self._arm_env,
                )
            except BaseException:
                await _stop_quietly(self._sb)
                raise
        self._artifact_base = await _snapshot_artifact_shas(self._sb, self._agent)
        return self._run

    async def _run(
        self: object, prompt: object, *, resume_session_id: object, detect_skill: object
    ) -> object:
        """Handle _run."""
        result = await self._agent.invoke(
            self._sb,
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
        # Capture skill artifacts the agent wrote outside the workdir mount: files new or
        # changed vs the staged baseline, labelled ~/... for the judge. Empty (and cheap) for
        # agents/evals that write solely to the workdir.
        authored = await _read_authored(self._sb, self._agent, self._artifact_base)
        if authored:
            result = replace(result, artifacts=authored)
        return result

    async def __aexit__(self: object, *exc: object) -> object:
        """Handle __aexit__."""
        await self._sb.stop()


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
    skill: str | None = None,
    arm: str | None = None,
    arm_env: dict | None = None,
    eval_set: str = "",
    project_marker: str = DEFAULT_PROJECT_MARKER,
    harness_args: list[str] | None = None,
) -> SandboxSession:
    """Handle arm_session."""
    return SandboxSession(
        agent=agent,
        snapshot=snapshot,
        eval_id=eval_id,
        config=config,
        host_workdir=host_workdir,
        host_repo_root=host_repo_root,
        model=model,
        effort=effort,
        skill=skill,
        arm=arm,
        arm_env=arm_env,
        eval_set=eval_set,
        project_marker=project_marker,
        harness_args=harness_args,
    )


async def _create_trigger_sandbox(
    *, agent: object, snapshot: object, name: object, host_repo_root: object
) -> object:
    """Handle _create_trigger_sandbox."""
    from microsandbox import Sandbox, Volume

    volumes = {PROJECT_MOUNT: Volume.bind(str(host_repo_root), readonly=True)}
    volumes.update(_agent_extra_volumes(agent, Volume))
    sb = await Sandbox.create(
        name,
        snapshot=snapshot,
        volumes=volumes,
        secrets=agent.secrets(),
        cpus=VM_CPUS,
        memory=VM_MEMORY_MIB,
        replace=True,
    )
    try:
        await agent.stage_project_assets(sb, PROJECT_MOUNT)
    except BaseException:
        await _stop_quietly(sb)
        raise
    return sb


def _trigger_command(
    agent: object,
    query: object,
    repo_root: object,
    model: object,
    effort: object,
    project_marker: object,
) -> list[str]:
    """Handle _trigger_command."""
    plugin = _plugin_dir_for(repo_root, project_marker)
    # A non-None sentinel: per the protocol it requests a streamable format for routing.
    # Routing reads the streamed events (detect_fired), so it's never matched as a skill name.
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
    project_marker: object = DEFAULT_PROJECT_MARKER,
) -> object:
    """Handle _route_in_sandbox_async."""
    # agent + snapshot are resolved by the SYNC wrapper before asyncio.run: building
    # a missing snapshot itself calls asyncio.run, which can't nest. Same constraint
    # as SandboxSession's __init__ → __aenter__ split.
    sb = await _create_trigger_sandbox(
        agent=agent,
        snapshot=snapshot,
        name=f"trigger-{_worker_tag()}",
        host_repo_root=repo_root,
    )
    lines: list[str] = []
    dispatched = False
    exit_code: int | None = None
    try:
        cmd = _trigger_command(agent, query, repo_root, model, effort, project_marker)
        handle = await sb.exec_stream(
            cmd[0],
            cmd[1:],
            cwd=agent.guest_home,
            env=agent.guest_env(),  # HOME + IS_SANDBOX
            # Force EOF on stdin: opencode run blocks reading stdin forever otherwise
            # (microsandbox leaves the pipe open), binning routing against the timeout.
            # Confirmed exec_stream accepts stdin (microsandbox/_microsandbox.pyi:85).
            stdin=b"",
        )

        async def _drain() -> None:
            """Handle _drain."""
            nonlocal dispatched, exit_code
            # exec_stream delivers stdout in arbitrary chunks that do NOT align to newlines,
            # and OpenCode's skill `tool_use` event embeds the full skill output (multi-KB),
            # so a single JSONL line routinely spans several chunks. Line-buffer here:
            # reassemble complete lines before detecting, or json.loads fails on every
            # partial chunk — the dispatch goes unseen, early-stop never fires, and the fire
            # tallies 0 (the trigger path's silent-zero bug).
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
                    # `is not None`, not truthiness: a real exit code of 0 must not become 1.
                    exit_code = event.code if event.code is not None else 1
            # Flush a trailing partial line: the final JSONL event may lack a newline
            # (or routing was killed mid-line), and count_fires' detect_fired(lines)
            # must still see that complete line to tally a last-moment fire.
            if buffer.strip():
                lines.append(buffer)

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
            timed_out = False
        except asyncio.TimeoutError:
            timed_out = True
            from microsandbox.errors import MicrosandboxError

            with contextlib.suppress(MicrosandboxError, OSError):
                await handle.kill()
    finally:
        await _stop_quietly(sb)

    if dispatched:
        return lines
    if timed_out:
        if agent.streamed_activity(lines):
            return lines  # worked but didn't route in time — a clean non-fire
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
    """Handle route_in_sandbox."""
    # Resolve agent + snapshot up-front: a missing snapshot triggers build_snapshot
    # → asyncio.run, which can't nest inside the asyncio.run below.
    agent = make_agent()
    snapshot = ensure_snapshot(agent, repo_root=repo_root)
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
        )
    )


def cli_build() -> None:
    """`make evals:build`: build the agent-ready snapshot up front (loud on error)."""
    preflight()
    agent = make_agent()
    env = resolve_environment_config(Path.cwd())
    name = snapshot_name(agent, env)
    if snapshot_exists(name):
        print(f"snapshot {name} already present")
        return
    print(f"building snapshot {name} from {env.base_image or BASE_IMAGE} ...")
    build_snapshot(agent, name, env)
    print(f"built {name}")


def cli_clean(repo_root: Path) -> None:
    """`make clean` helper: remove evalspec sandboxes + snapshots via the msb CLI,.

    plus the per-repo snapshot lock files under `<repo_root>/tmp/`.

    Tolerates 'none found'. Snapshots are regenerable via `make evals:build`.
    """
    import subprocess

    def _msb(*args: object) -> None:
        """Handle _msb."""
        try:
            subprocess.run(["msb", *args], check=False)
        except FileNotFoundError:
            pass  # msb CLI not installed — nothing to prune via it

    home = Path.home() / ".microsandbox"
    for sub, prefixes in (
        ("sandboxes", ("eval-", "evalspec-build-", "trigger-")),
        ("snapshots", ("evalspec-",)),
    ):
        d = home / sub
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if entry.name.startswith(prefixes):
                if sub == "sandboxes":
                    _msb("stop", entry.name)
                    _msb("rm", "-f", entry.name)
                else:
                    _msb("snapshot", "rm", "--force", entry.name)
    for lock in workspace.workspace_parent(repo_root).glob(".evalspec-snapshot-*.lock"):
        lock.unlink(missing_ok=True)
