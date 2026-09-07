"""Tests for the runtime-free sandbox seam: fingerprints, mounts, the lazy-import guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

from benchspec.agents.claude import ClaudeCodeAgent
from benchspec.sandbox import backend, registry
from benchspec.specs.discovery import EnvConfig


def _agent() -> object:
    """Build a claude agent test fixture."""
    return ClaudeCodeAgent(auth_value="test-token", version="1.2.3")


def test_docker_fingerprint_folds_in_its_own_backend_id(monkeypatch: object) -> None:
    """The Docker fingerprint records `docker` as the backend that produced it."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    docker_backend = registry.resolve_sandbox("docker")

    inputs = docker_backend.fingerprint_inputs(_agent(), EnvConfig())

    assert inputs.backend_id == "docker"


def test_docker_and_microsandbox_fingerprints_differ_for_one_agent_and_env(
    monkeypatch: object,
) -> None:
    """Two backends over the same agent+env never share a snapshot cache fingerprint."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")

    docker_fingerprint = registry.resolve_sandbox("docker").cache_fingerprint(_agent(), env)
    microsandbox_fingerprint = registry.resolve_sandbox("microsandbox").cache_fingerprint(
        _agent(), env
    )

    assert docker_fingerprint != microsandbox_fingerprint


def test_snapshot_names_differ_across_the_two_backends(monkeypatch: object) -> None:
    """A snapshot name is namespaced by backend, so the two never collide on disk."""
    monkeypatch.setenv("BENCHSPEC_DOCKER_PATH", "/nonexistent/docker")
    from benchspec.sandbox.sandbox import snapshot_name

    docker_name = snapshot_name(_agent(), backend=registry.resolve_sandbox("docker"))
    microsandbox_name = snapshot_name(_agent(), backend=registry.resolve_sandbox("microsandbox"))

    assert docker_name.startswith("benchspec-docker-")
    assert microsandbox_name.startswith("benchspec-microsandbox-")


def test_cache_fingerprint_stable_when_nothing_changes() -> None:
    """The fingerprint is reproducible for the same backend, agent, and env."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    assert microsandbox_backend.cache_fingerprint(
        _agent(), env
    ) == microsandbox_backend.cache_fingerprint(_agent(), env)


def test_cache_fingerprint_changes_with_base_image() -> None:
    """A different declared base-image reference changes the fingerprint."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")

    first = microsandbox_backend.cache_fingerprint(_agent(), EnvConfig(base_image="ubuntu:22.04"))
    second = microsandbox_backend.cache_fingerprint(_agent(), EnvConfig(base_image="ubuntu:24.04"))

    assert first != second


def test_cache_fingerprint_changes_with_install_fingerprint(monkeypatch: object) -> None:
    """A changed agent install fingerprint changes the cache fingerprint."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent_a = _agent()
    agent_b = _agent()
    monkeypatch.setattr(agent_b, "install_fingerprint", lambda: "installer-rev-9")

    assert microsandbox_backend.cache_fingerprint(
        agent_a, env
    ) != microsandbox_backend.cache_fingerprint(agent_b, env)


def test_install_fingerprint_changes_with_provision_script(monkeypatch: object) -> None:
    """Changing the resolved provision script invalidates the agent install fingerprint."""
    agent = _agent()
    original = agent.install_fingerprint()

    monkeypatch.setattr(agent, "provision_script", lambda: "install a different revision")

    assert agent.install_fingerprint() != original


def test_cache_fingerprint_changes_with_env_script_bytes() -> None:
    """Different environment script bytes yield a different fingerprint."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    one = microsandbox_backend.cache_fingerprint(
        _agent(), EnvConfig(script=b"echo one\n", script_path="s.sh")
    )
    two = microsandbox_backend.cache_fingerprint(
        _agent(), EnvConfig(script=b"echo two\n", script_path="s.sh")
    )
    assert one != two


def test_fingerprint_inputs_digest_matches_cache_fingerprint() -> None:
    """`fingerprint_inputs(...).digest` equals `cache_fingerprint(...)` for the same args."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    env = EnvConfig(script=b"echo one\n", script_path="s.sh")
    agent = _agent()

    inputs = microsandbox_backend.fingerprint_inputs(agent, env)

    assert inputs.digest == microsandbox_backend.cache_fingerprint(agent, env)


def test_fingerprint_inputs_carries_structured_fields() -> None:
    """`fingerprint_inputs` exposes the raw ingredients the digest was built from."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")
    env = EnvConfig(base_image="ubuntu:22.04", script=b"echo hi\n", script_path="s.sh")
    agent = _agent()

    inputs = microsandbox_backend.fingerprint_inputs(agent, env)

    assert inputs.backend_id == "microsandbox"
    assert inputs.base_image_ref == "ubuntu:22.04"
    assert inputs.install_fingerprint == agent.install_fingerprint()
    assert inputs.env_script_sha256 == hashlib.sha256(b"echo hi\n").hexdigest()


def test_fingerprint_inputs_defaults_base_image_ref_when_unset() -> None:
    """With no declared base image, `base_image_ref` falls back to `backend.BASE_IMAGE`."""
    microsandbox_backend = registry.resolve_sandbox("microsandbox")

    inputs = microsandbox_backend.fingerprint_inputs(_agent(), EnvConfig())

    assert inputs.base_image_ref == backend.BASE_IMAGE


def test_microsandbox_imported_only_under_allowlist() -> None:
    """No source file outside the allowlist imports the microsandbox package.

    `microsandbox.py` is the ONLY source file that imports microsandbox at all: agent
    credentials are backend-neutral `Credential`s (rendered to a microsandbox `Secret`
    only inside `microsandbox_secrets`), and every microsandbox error is translated to
    the neutral `SandboxError` before it leaves the backend. This guards that boundary
    so a future edit cannot reintroduce a scattered `import microsandbox` elsewhere.
    """
    import re
    from pathlib import Path

    allowlist = {"microsandbox.py"}
    src = Path("src/benchspec")
    pattern = re.compile(r"^\s*(import microsandbox|from microsandbox)", re.MULTILINE)
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name in allowlist:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == [], f"microsandbox imported outside the allowlist: {offenders}"


def test_host_mount_path_resolves_symlinked_roots(tmp_path: Path) -> None:
    """Verify a bind-mount path is handed to the runtime with symlinked ancestors resolved."""
    real_root = tmp_path / "real"
    (real_root / "room").mkdir(parents=True)
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root)

    mount_path = backend.host_mount_path(linked_root / "room")

    assert mount_path == str((real_root / "room").resolve())
    assert "linked" not in mount_path
