"""Unit tests for the Docker sandbox backend.

Every test here fakes the `docker` CLI at the three seam functions
(`_docker_sync`, `_docker`, `_docker_stream`). Nothing in this file needs a daemon;
the daemon-gated behavior suite lives in `test_docker_daemon.py`.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

from harnessbench.agents.base import GuestCredential
from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.orchestration.environments import GuestSandbox
from harnessbench.sandbox import backend as backend_mod
from harnessbench.sandbox import docker as docker_mod
from harnessbench.sandbox import sandbox as sandbox_mod
from harnessbench.sandbox.docker import DockerResult, DockerVolume
from harnessbench.sandbox.sandbox import snapshot_name
from harnessbench.specs.discovery import EnvConfig


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


def test_write_env_file_writes_mode_0600_name_equals_value_lines(tmp_path: object) -> None:
    """The low-level writer never puts a value in argv: it lands in a 0600 file instead."""
    path = docker_mod._write_env_file({"HOME": "/root", "TZ": "UTC"})

    try:
        assert Path(path).read_text(encoding="utf-8") == "HOME=/root\nTZ=UTC\n"
        assert Path(path).stat().st_mode & 0o777 == 0o600
    finally:
        docker_mod._delete_env_file(path)


def test_write_env_file_of_empty_mapping_writes_no_file() -> None:
    """No pairs means no file at all, so a caller skips the `--env-file` flag entirely."""
    assert docker_mod._write_env_file({}) is None


def test_delete_env_file_of_none_is_a_noop() -> None:
    """A caller that never wrote a file can still unconditionally call the deleter."""
    docker_mod._delete_env_file(None)


def test_env_file_yields_no_flags_for_an_empty_mapping() -> None:
    """The context-manager wrapper mirrors `_write_env_file`'s empty-mapping shortcut."""
    with docker_mod._env_file({}) as flags:
        assert flags == []


def test_env_file_yields_the_flag_pair_and_deletes_the_file_on_exit() -> None:
    """The file exists inside the block and is gone the moment the block exits."""
    with docker_mod._env_file({"HOME": "/root"}) as flags:
        assert flags[0] == "--env-file"
        path = Path(flags[1])
        assert path.read_text(encoding="utf-8") == "HOME=/root\n"

    assert path.exists() is False


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
    r"""Any snapshot name yields a LEGAL reference — slashes, colons, and spaces included.

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


def test_shell_carries_env_through_an_env_file_never_the_argv(monkeypatch: object) -> None:
    """`shell` becomes `docker exec -i --env-file <path> -w ... <container> /bin/sh -c ...`.

    Guest env is a documented host-secret channel (arm `env`), so it must never ride
    `docker exec` argv the way `docker run`'s credentials never do.
    """
    seen: dict = {}

    async def fake(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Read the env-file while it still exists, then return a canned exec result."""
        env_file = Path(args[args.index("--env-file") + 1])
        seen["args"] = args
        seen["content"] = env_file.read_text(encoding="utf-8")
        seen["mode"] = env_file.stat().st_mode & 0o777
        seen["path"] = env_file
        return DockerResult(args, 0, "done\n", "")

    monkeypatch.setattr(docker_mod, "_docker", fake)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    result = asyncio.run(sandbox.shell("echo done", env={"HOME": "/root"}, cwd="/project"))

    assert seen["args"][:2] == ("exec", "-i")
    assert seen["args"][-4:] == ("eval-hello-alpha-main", "/bin/sh", "-c", "echo done")
    assert "-w" in seen["args"]
    assert "/project" in seen["args"]
    assert not any(argument == "HOME=/root" for argument in seen["args"])
    assert seen["content"] == "HOME=/root\n"
    assert seen["mode"] == 0o600
    assert seen["path"].exists() is False
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
    """`exec` passes argv straight through, so no quoting rule can mangle a prompt.

    Its env, like `shell`'s, reaches the guest through `--env-file`, never `-e`.
    """
    seen: dict = {}

    async def fake(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Read the env-file while it still exists, then return a canned exec result."""
        env_file = Path(args[args.index("--env-file") + 1])
        seen["args"] = args
        seen["content"] = env_file.read_text(encoding="utf-8")
        seen["stdin"] = stdin
        seen["timeout"] = timeout
        return DockerResult(args, 0, "{}", "")

    monkeypatch.setattr(docker_mod, "_docker", fake)
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

    assert seen["args"][:2] == ("exec", "-i")
    assert seen["args"][-4:] == (
        "eval-hello-alpha-main", "/root/.local/bin/claude", "-p", "write a haiku",
    )
    assert "-w" in seen["args"]
    assert "/workspace" in seen["args"]
    assert not any(argument == "TZ=UTC" for argument in seen["args"])
    assert seen["content"] == "TZ=UTC\n"
    assert seen["stdin"] == b""
    assert seen["timeout"] == 600


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


def test_exec_timeout_restores_workspace_ownership_before_reaping(monkeypatch: object) -> None:
    """A timed-out turn still hands `/workspace` back before the container is removed.

    `_reap` used to skip the chown entirely: a turn whose own exec call wedged — not just
    ran long — left a Linux host with a root-owned workspace, since a reaped sandbox never
    reaches `stop()` (the only place the chown used to run).
    """
    calls: list = []

    async def fake(*args: str, stdin: object = None, timeout: object = None,
                   description: object = None) -> object:
        """Time out only the first exec (the turn itself); the chown and the rm succeed."""
        calls.append(args)
        if args[0] == "exec" and len(calls) == 1:
            raise docker_mod.DockerCallTimeout("timed out")
        return DockerResult(args, 0, "", "")

    monkeypatch.setattr(docker_mod, "_docker", fake)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main", restore_owner="501:20")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="timed out"):
        asyncio.run(sandbox.exec("/root/.local/bin/claude", ["-p", "slow"], timeout=1))

    assert calls[1] == (
        "exec", "-i", "eval-hello-alpha-main", "chown", "-R", "501:20", "/workspace",
    )
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


def test_exec_classifies_a_wedged_disambiguation_inspect_as_an_infra_failure(
    monkeypatch: object,
) -> None:
    """A daemon too wedged to answer `docker inspect` is a control-plane failure.

    An ambiguous 125-127 exit is normally settled by asking the container's running
    state. If that ask itself times out, the daemon is unresponsive — which is worse
    than ambiguous, not a reason to let a guest exit code through ungraded.
    """

    async def fake(*args: str, stdin: object = None, timeout: object = None,
                   description: object = None) -> object:
        """Report a 126 exec, then time out on the disambiguating inspect."""
        if args[0] == "inspect":
            raise docker_mod.DockerCallTimeout("`docker inspect` timed out after 30.0s")
        return DockerResult(args, 126, "", "sh: 1: nosuchcmd: not found")

    monkeypatch.setattr(docker_mod, "_docker", fake)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="disambiguating"):
        asyncio.run(sandbox.shell("nosuchcmd"))


def test_exec_timeout_reports_when_the_reaping_removal_itself_fails(
    monkeypatch: object,
) -> None:
    """A timeout whose reap also fails must say so, not falsely claim the guest was stopped.

    If the daemon goes down right after a hung exec, `docker rm -f` fails too. Claiming
    "the container was removed to reap it" in that case would tell an operator the
    runaway agent is stopped when it may still be running.
    """
    async def timing_out_then_failing_to_remove(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Time out the exec, then fail the removal that follows with a daemon error."""
        if args[0] == "exec":
            raise docker_mod.DockerCallTimeout("timed out")
        return DockerResult(args, 1, "", "Cannot connect to the Docker daemon at unix:///...")

    monkeypatch.setattr(docker_mod, "_docker", timing_out_then_failing_to_remove)
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    with pytest.raises(docker_mod.SandboxRuntimeError, match="removal FAILED") as raised:
        asyncio.run(sandbox.exec("claude", timeout=1))

    assert "the guest may still be running" in str(raised.value)
    assert "Cannot connect to the Docker daemon" in str(raised.value)
    assert sandbox.alive is False


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
        self.kill_count = 0
        self._code = code

    async def wait(self: object) -> int:
        """Report the process's exit code and mark it reaped."""
        self.returncode = self._code
        return self._code

    def kill(self: object) -> None:
        """Record every call so a test can prove a double-kill only kills once."""
        self.kill_count += 1


async def _collect_events(stream: object) -> list:
    """Drain a `DockerExecStream` (or double) into the list of events it yields."""
    return [event async for event in stream]


def test_exec_stream_yields_stdout_chunks_then_a_terminal_exit_event() -> None:
    """The stream shape matches what trigger routing drains: stdout chunks, then `exited`."""
    stream = docker_mod.DockerExecStream(FakeProcess([b'{"a":1}\n', b'{"b":2}\n'], code=0))

    events = asyncio.run(_collect_events(stream))

    assert [event.event_type for event in events] == ["stdout", "stdout", "exited"]
    assert [event.data for event in events[:2]] == [b'{"a":1}\n', b'{"b":2}\n']
    assert events[-1].code == 0


def test_exec_stream_reports_a_nonzero_exit_code() -> None:
    """A crashed guest command surfaces its code, which routing turns into a RoutingError."""
    stream = docker_mod.DockerExecStream(FakeProcess([], code=127))

    events = asyncio.run(_collect_events(stream))

    assert [event.event_type for event in events] == ["exited"]
    assert events[0].code == 127


def test_exec_stream_kill_terminates_the_process_once() -> None:
    """Killing a stream is idempotent: a second kill on a reaped process is a no-op."""
    process = FakeProcess([b"chunk"], code=0)
    stream = docker_mod.DockerExecStream(process)

    asyncio.run(stream.kill())
    asyncio.run(stream.kill())

    assert process.kill_count == 1


def test_exec_stream_drains_stderr_larger_than_a_pipe_buffer() -> None:
    """A guest that floods stderr must not stall the stream, and the tail stays bounded.

    `_docker_stream` pipes stderr. A piped stderr that nobody reads blocks the guest the
    moment it writes past one pipe buffer (64 KiB on Linux): stdout stops arriving and the
    routing turn deadlocks until its own timeout. The payload here is four buffers' worth,
    so a stream that only drains stdout cannot pass this test.
    """
    process = FakeProcess([b"out\n"], code=0, stderr=b"E" * (256 * 1024))
    stream = docker_mod.DockerExecStream(process)

    events = asyncio.run(_collect_events(stream))

    assert [event.event_type for event in events] == ["stdout", "exited"]
    assert process.stderr.exhausted is True
    assert len(stream.stderr_tail) == docker_mod.DockerExecStream._STDERR_TAIL_BYTES


def test_exec_stream_spawns_docker_exec_with_stdin_closed(monkeypatch: object) -> None:
    """The streaming spawn carries the same exec arguments and forces EOF on stdin.

    Env reaches the guest through `--env-file`, same as `shell`/`exec` — never `-e`.
    """
    calls: list = []

    async def fake_stream(
        *args: str, stdin: object = None, description: object = None
    ) -> object:
        """Record the streaming spawn and return a process double."""
        calls.append({"args": args, "stdin": stdin})
        return FakeProcess([], code=0)

    monkeypatch.setattr(docker_mod, "_docker_stream", fake_stream)
    sandbox = docker_mod.DockerSandbox("trigger-main")

    stream = asyncio.run(
        sandbox.exec_stream(
            "/root/.local/bin/claude", ["-p", "route"], cwd="/root", env={"HOME": "/root"},
            stdin=b"",
        )
    )

    args = calls[0]["args"]
    env_file = Path(args[args.index("--env-file") + 1])
    assert args[:2] == ("exec", "-i")
    assert args[-4:] == ("trigger-main", "/root/.local/bin/claude", "-p", "route")
    assert not any(argument == "HOME=/root" for argument in args)
    assert env_file.read_text(encoding="utf-8") == "HOME=/root\n"
    assert calls[0]["stdin"] == b""

    asyncio.run(stream.kill())


def test_exec_stream_env_file_outlives_the_spawn_until_kill(monkeypatch: object) -> None:
    """The env-file must not be deleted the instant `_docker_stream` returns.

    Unlike `shell`/`exec`'s one blocking `_docker` call, the spawned client process may
    still be starting up (and reading its own `--env-file` argument) after `exec_stream`
    returns, so deleting eagerly here would risk a spawn that reads a half-deleted file.
    """
    async def fake_stream(
        *args: str, stdin: object = None, description: object = None
    ) -> object:
        """Return a process double without ever inspecting the env-file."""
        return FakeProcess([], code=0)

    monkeypatch.setattr(docker_mod, "_docker_stream", fake_stream)
    sandbox = docker_mod.DockerSandbox("trigger-main")

    stream = asyncio.run(sandbox.exec_stream("claude", env={"HOME": "/root"}, stdin=b""))
    env_file = Path(stream._env_file_path)
    assert env_file.exists()

    asyncio.run(stream.kill())

    assert env_file.exists() is False


def test_exec_stream_deletes_its_env_file_on_the_terminal_exited_event(
    monkeypatch: object,
) -> None:
    """A stream drained to natural completion cleans up its env-file without a kill()."""

    async def fake_stream(
        *args: str, stdin: object = None, description: object = None
    ) -> object:
        """Return a process double that yields one chunk then exits cleanly."""
        return FakeProcess([b"chunk"], code=0)

    monkeypatch.setattr(docker_mod, "_docker_stream", fake_stream)
    sandbox = docker_mod.DockerSandbox("trigger-main")

    stream = asyncio.run(sandbox.exec_stream("claude", env={"HOME": "/root"}, stdin=b""))
    env_file = Path(stream._env_file_path)
    assert env_file.exists()

    asyncio.run(_collect_events(stream))

    assert env_file.exists() is False


def test_exec_stream_kill_is_idempotent_with_an_env_file(monkeypatch: object) -> None:
    """A second kill, after the file is already gone, must not raise."""

    async def fake_stream(
        *args: str, stdin: object = None, description: object = None
    ) -> object:
        """Return a process double."""
        return FakeProcess([], code=0)

    monkeypatch.setattr(docker_mod, "_docker_stream", fake_stream)
    sandbox = docker_mod.DockerSandbox("trigger-main")
    stream = asyncio.run(sandbox.exec_stream("claude", env={"HOME": "/root"}, stdin=b""))

    asyncio.run(stream.kill())
    asyncio.run(stream.kill())


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

    assert calls[0]["args"] == (
        "exec", "-i", "eval-hello-alpha-main", "chown", "-R", "501:20", "/workspace",
    )
    assert calls[0]["timeout"] == docker_mod.REMOVE_TIMEOUT_SECONDS
    assert calls[1]["args"] == ("rm", "-f", "eval-hello-alpha-main")


def test_stop_without_a_restore_owner_skips_the_chown(monkeypatch: object) -> None:
    """On macOS, Docker Desktop's file-sharing layer maps ownership, so no chown runs."""
    calls = _record_docker(monkeypatch, DockerResult(("rm",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(sandbox.stop())

    assert [call["args"][0] for call in calls] == ["rm"]


def test_restore_workspace_owner_is_a_noop_without_a_restore_owner(monkeypatch: object) -> None:
    """No `restore_owner` means no chown at all, matching the macOS no-op case."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main")

    asyncio.run(sandbox.restore_workspace_owner())

    assert calls == []


def test_restore_workspace_owner_is_a_noop_once_the_sandbox_is_dead(monkeypatch: object) -> None:
    """A reaped or stopped sandbox must not attempt a chown against a gone container."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main", restore_owner="501:20")
    sandbox._alive = False

    asyncio.run(sandbox.restore_workspace_owner())

    assert calls == []


def test_restore_workspace_owner_is_idempotent(monkeypatch: object) -> None:
    """Calling it twice (once per turn, again at teardown) just chowns twice, harmlessly."""
    calls = _record_docker(monkeypatch, DockerResult(("exec",), 0, "", ""))
    sandbox = docker_mod.DockerSandbox("eval-hello-alpha-main", restore_owner="501:20")

    asyncio.run(sandbox.restore_workspace_owner())
    asyncio.run(sandbox.restore_workspace_owner())

    assert len(calls) == 2
    assert calls[0]["args"][-4:] == ("chown", "-R", "501:20", "/workspace")


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


def _fake_build_docker(monkeypatch: object, *, run_stdout: str = "") -> list:
    """Fake the async seam for a build: no stale container, `run` reports `run_stdout`.

    `run_stdout` stands in for the container ID `docker run --detach` prints; an empty
    default (the common case in these tests) exercises the documented no-ID fallback to
    the container's name.
    """
    calls: list = []

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Record every docker call, reporting no pre-existing container."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: build")
        if args[0] == "run":
            return DockerResult(args, 0, run_stdout, "")
        return DockerResult(args, 0, "", "")

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
    calls = _fake_build_docker(monkeypatch, run_stdout="abc123containerid\n")
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
    assert agent.provisioned == ["abc123containerid"]
    assert calls[-2] == (
        "commit",
        "abc123containerid",
        "harnessbench-docker-claude-code-latest-ab12cd34-0d462a07",
    )
    assert calls[-1] == ("rm", "-f", "abc123containerid")


def test_build_snapshot_falls_back_to_the_name_when_run_prints_no_id(
    monkeypatch: object,
) -> None:
    """An empty `run --detach` stdout falls back to the name, not an empty exec target."""
    calls = _fake_build_docker(monkeypatch)
    build_container = docker_mod.build_container_name("claude-code")
    agent = RecordingAgent()

    docker_mod.DockerBackend().build_snapshot(agent, "snap", EnvConfig())

    assert agent.provisioned == [build_container]
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


def _fake_run_docker(monkeypatch: object, *, run_stdout: str = "") -> list:
    """Fake the async seam for a container start: no stale container, run reports `run_stdout`.

    `run_stdout` stands in for the container ID `docker run --detach` prints; an empty
    default (the common case in these tests) exercises the documented no-ID fallback to
    the container's name.
    """
    calls: list = []

    async def fake_docker(
        *args: str, stdin: object = None, timeout: object = None, description: object = None
    ) -> object:
        """Record every docker call, reporting no pre-existing container."""
        calls.append(args)
        if args[0] == "inspect":
            return DockerResult(args, 1, "", "Error: No such object: eval")
        if args[0] == "run":
            return DockerResult(args, 0, run_stdout, "")
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


def test_create_sandbox_execs_and_removes_by_the_containers_id_not_its_name(
    monkeypatch: object, tmp_path: object,
) -> None:
    """A concurrent process that reuses the freed name can't steal our exec or removal.

    The container's name is free again the instant `docker run --detach` returns, so a
    concurrent run naming a container the same way could grab it. Addressing every later
    call by the ID `run --detach` printed — never the name — closes that race.
    """
    calls = _fake_run_docker(monkeypatch, run_stdout="abc123containerid\n")

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

    assert sandbox.container == "abc123containerid"
    assert sandbox.name == "eval-hello-alpha-main"

    asyncio.run(sandbox.shell("echo hi"))
    assert calls[-1][:4] == ("exec", "-i", "abc123containerid", "/bin/sh")

    asyncio.run(sandbox.stop())
    assert calls[-1] == ("rm", "-f", "abc123containerid")


def test_create_sandbox_falls_back_to_the_name_when_run_prints_no_id(
    monkeypatch: object, tmp_path: object,
) -> None:
    """An empty `run --detach` stdout falls back to the name, not an empty exec target."""
    calls = _fake_run_docker(monkeypatch)

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

    asyncio.run(sandbox.stop())

    assert sandbox.container == sandbox.name == "eval-hello-alpha-main"
    assert calls[-1] == ("rm", "-f", "eval-hello-alpha-main")


def test_create_sandbox_never_puts_a_credential_in_the_container_argv(
    monkeypatch: object, tmp_path: object,
) -> None:
    """The token reaches the container through a 0600 env-file, never through `-e`.

    `-e NAME=value` puts the token in the container's argv, which every user on the host
    can read via `ps`. `--env-file` fixes that specific leak — `docker inspect` still shows
    the value for as long as the container exists, exactly as `-e` would — and the 0600
    file itself is deleted the moment `docker run` returns, by which point the daemon has
    already read it into the container's environment.
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
