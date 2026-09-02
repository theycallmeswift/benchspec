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
