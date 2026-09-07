"""Shared test support: fakes and builders re-exported from conftest, plus CLI plumbing in `cli`."""

from __future__ import annotations

from tests.conftest import FakeExecOutput, FakeSandbox, seed_arm

__all__ = ["FakeExecOutput", "FakeSandbox", "seed_arm"]
