"""The pytest-status -> benchspec exit-code mapping.

Each test pins one raw pytest status to the `ExitCode` the contract promises for it.
"""

from __future__ import annotations

from benchspec.exit_codes import ExitCode, exit_code_for_pytest_status


def test_pytest_success_maps_to_success() -> None:
    """Verify pytest status 0 maps to SUCCESS."""
    assert exit_code_for_pytest_status(0) == ExitCode.SUCCESS


def test_pytest_tests_failed_maps_to_finding() -> None:
    """Verify pytest status 1 (tests failed) maps to FINDING."""
    assert exit_code_for_pytest_status(1) == ExitCode.FINDING


def test_pytest_collection_usage_error_maps_to_usage() -> None:
    """Verify pytest status 2 (a collection-time UsageError) maps to USAGE."""
    assert exit_code_for_pytest_status(2) == ExitCode.USAGE


def test_pytest_usage_error_status_maps_to_usage() -> None:
    """Verify pytest status 4 (usage error) maps to USAGE."""
    assert exit_code_for_pytest_status(4) == ExitCode.USAGE


def test_pytest_internal_error_maps_to_finding() -> None:
    """Verify pytest status 3 (internal error) maps to FINDING."""
    assert exit_code_for_pytest_status(3) == ExitCode.FINDING


def test_pytest_no_tests_collected_maps_to_nothing_to_do() -> None:
    """Verify pytest status 5 (no tests collected) maps to NOTHING_TO_DO."""
    assert exit_code_for_pytest_status(5) == ExitCode.NOTHING_TO_DO


def test_unexpected_pytest_status_maps_to_finding() -> None:
    """Verify an unexpected pytest status maps to FINDING by default."""
    assert exit_code_for_pytest_status(99) == ExitCode.FINDING
