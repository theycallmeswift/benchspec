"""A live check that E2B's injection keeps a credential out of the guest.

It boots a real sandbox, so the `e2e` marker keeps it out of `make test`; run it with
`uv run pytest -m e2e tests/sandbox/test_e2b_live.py`. The credential is a canary, so
no model is called.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from benchspec.agents.codex import CodexAgent
from benchspec.sandbox import e2b as e2b_mod
from benchspec.sandbox.sandbox import _agent_extra_volumes

pytestmark = pytest.mark.e2e

# E2B's stock template: the check needs a shell, not an agent CLI.
_TEMPLATE = "base"
_CANARY = "benchspec-canary-credential"
_SCAN = f"env; grep -rIl --exclude-dir={{proc,sys,dev}} {_CANARY} / 2>/dev/null; true"


async def _guest_view(room: Path) -> str:
    """Boot a cell whose credential is the canary; return its environment and file hits."""
    backend = e2b_mod.E2BBackend()
    sandbox = await backend.create_sandbox(
        agent=CodexAgent(auth_value=_CANARY, provider="openrouter"),
        snapshot=_TEMPLATE,
        name="benchspec-live-injection-check",
        host_workdir=room,
        staged_project=None,
        extra_volumes=_agent_extra_volumes,
        cap_seconds=120,
    )

    try:
        return (await sandbox.shell(_SCAN)).stdout_text
    finally:
        await backend.stop_quietly(sandbox)


def test_an_injected_credential_is_in_neither_the_guests_environment_nor_its_files(
    tmp_path: Path,
) -> None:
    """Verify an injected credential is in neither the guest's environment nor its files."""
    if not os.environ.get(e2b_mod.API_KEY_ENV):
        pytest.skip(f"{e2b_mod.API_KEY_ENV} is not set")

    view = asyncio.run(_guest_view(tmp_path))

    assert f"OPENROUTER_API_KEY={e2b_mod.INJECTED_PLACEHOLDER}" in view
    assert _CANARY not in view
