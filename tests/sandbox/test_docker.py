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

from harnessbench.agents.claude import ClaudeCodeAgent
from harnessbench.orchestration.environments import GuestSandbox
from harnessbench.sandbox import backend as backend_mod
from harnessbench.sandbox import docker as docker_mod
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
