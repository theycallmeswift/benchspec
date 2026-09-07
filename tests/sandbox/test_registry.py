"""Tests for backend selection: the registry that names the concrete runtimes."""

from __future__ import annotations

import pytest

from benchspec.sandbox import registry
from benchspec.specs.schema import SchemaError


def test_resolve_sandbox_returns_microsandbox_backend() -> None:
    """`microsandbox` resolves to a backend whose id is `microsandbox`."""
    resolved = registry.resolve_sandbox("microsandbox")
    assert resolved.id == "microsandbox"


def test_resolve_sandbox_returns_docker_backend() -> None:
    """`docker` resolves to a backend whose id is `docker`."""
    resolved = registry.resolve_sandbox("docker")
    assert resolved.id == "docker"


def test_resolve_sandbox_unknown_fails_listing_both_backends() -> None:
    """An unknown backend name fails fast listing every supported value."""
    with pytest.raises(SchemaError, match=r"unsupported sandbox `qemu`") as exc_info:
        registry.resolve_sandbox("qemu")

    message = str(exc_info.value)
    assert "docker" in message
    assert "microsandbox" in message


def test_default_sandbox_is_docker() -> None:
    """Docker is the default every unpinned set and the bare build inherit."""
    assert registry.DEFAULT_SANDBOX == "docker"
