"""Live checks that E2B's injection keeps a credential out of a root guest's reach.

One real sandbox, booted through the backend with a canary credential, so no model is
called. The `e2b` marker keeps it out of `make test`; run it with `uv run pytest -m e2b`.
It leans on httpbin.org, which answers any `Host` with the headers it received.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import pytest

from benchspec.agents.base import BEARER_HEADER, Credential
from benchspec.agents.codex import CodexAgent
from benchspec.sandbox import e2b as e2b_mod

pytestmark = [
    pytest.mark.e2b,
    pytest.mark.skipif(
        not os.environ.get(e2b_mod.API_KEY_ENV), reason=f"{e2b_mod.API_KEY_ENV} is not set"
    ),
]

# E2B's stock template: the checks need a shell, not an agent CLI.
_TEMPLATE = "base"
_CANARY = "benchspec-canary-credential"
# Bracketed so a scan's own command line never matches.
_CANARY_PATTERN = "benchspec-canar[y]-credential"
_ECHO_HOST = "httpbin.org"
# Allowed too, so the guest can point an allowed name at the echo's address.
_DECOY_HOST = "example.com"
_ENVD = "localhost:49983"
_METADATA = "http://169.254.169.254"

_INJECTED_REQUEST = "an HTTPS request to an allowed host"
_PLAIN_HTTP_REQUEST = "a plain HTTP request to an allowed host"


class _CanaryAgent(CodexAgent):
    """An agent whose one injected credential is the canary."""

    def secrets(self) -> list[Credential]:
        """Return the canary, bound for the echo and the decoy."""
        return [Credential("CANARY_KEY", _CANARY, (_ECHO_HOST, _DECOY_HOST), BEARER_HEADER)]


def _no_volumes(agent: object, volume_cls: object) -> dict[str, object]:
    """Mount nothing: the canary agent owns no host files."""
    return {}


def _probes(echo_address: str) -> dict[str, str]:
    """Return each place a root guest could look for the canary, as a shell script."""
    return {
        _INJECTED_REQUEST: f"curl -s -m 20 https://{_ECHO_HOST}/headers",
        _PLAIN_HTTP_REQUEST: f"curl -s -m 20 http://{_ECHO_HOST}/headers",
        "its environment": "env",
        "its files": f"grep -rIl --exclude-dir={{proc,sys,dev}} {_CANARY_PATTERN} /",
        "every process's environment and arguments": (
            f"grep -al {_CANARY_PATTERN} /proc/*/environ /proc/*/cmdline"
        ),
        "an allowed name pointed at a foreign address, over HTTPS": (
            f"curl -sk -m 20 --resolve {_DECOY_HOST}:443:{echo_address} "
            f"https://{_DECOY_HOST}/headers"
        ),
        "an allowed name pointed at a foreign address, over HTTP": (
            f"curl -s -m 20 --resolve {_DECOY_HOST}:80:{echo_address} http://{_DECOY_HOST}/headers"
        ),
        "envd": f"for path in '' envs metrics; do curl -s -m 5 {_ENVD}/$path; done",
        "the metadata service": (
            f"token=$(curl -s -m 5 -X PUT {_METADATA}/latest/api/token "
            "-H 'X-metadata-token-ttl-seconds: 60'); "
            f'curl -s -m 5 -H "X-metadata-token: $token" -H "Accept: application/json" {_METADATA}/'
        ),
    }


async def _run_probes(room: Path) -> dict[str, str]:
    """Boot a cell whose credential is the canary; return each probe's output."""
    backend = e2b_mod.E2BBackend()
    sandbox = await backend.create_sandbox(
        agent=_CanaryAgent(),
        snapshot=_TEMPLATE,
        name="benchspec-live-injection-check",
        host_workdir=room,
        staged_project=None,
        extra_volumes=_no_volumes,
        cap_seconds=180,
    )

    try:
        views = {}

        for place, script in _probes(socket.gethostbyname(_ECHO_HOST)).items():
            result = await sandbox.shell(script)
            views[place] = result.stdout_text + result.stderr_text

        return views
    finally:
        await backend.stop_quietly(sandbox)


@pytest.fixture(scope="module")
def views(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Every probe's output from one sandbox."""
    return asyncio.run(_run_probes(tmp_path_factory.mktemp("room")))


def test_the_credential_is_injected_into_an_https_request_to_an_allowed_host(
    views: dict[str, str],
) -> None:
    """Verify the credential is injected into an HTTPS request to an allowed host."""
    assert f"Bearer {_CANARY}" in views[_INJECTED_REQUEST]


def test_a_plain_http_request_reaches_the_host_without_the_credential(
    views: dict[str, str],
) -> None:
    """Verify a plain HTTP request reaches the host without the credential."""
    assert _ECHO_HOST in views[_PLAIN_HTTP_REQUEST]
    assert _CANARY not in views[_PLAIN_HTTP_REQUEST]


def test_a_root_guest_finds_the_credential_nowhere_else(views: dict[str, str]) -> None:
    """Verify a root guest finds the credential nowhere else."""
    found = [
        place for place, view in views.items() if _CANARY in view and place != _INJECTED_REQUEST
    ]

    assert f"CANARY_KEY={e2b_mod.INJECTED_PLACEHOLDER}" in views["its environment"]
    assert found == []
