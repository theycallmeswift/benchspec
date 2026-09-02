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

from harnessbench.agents.claude import ClaudeCodeAgent
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
