"""Tests for provenance."""

from __future__ import annotations

import pytest

from evalspec.sandbox.provenance import (
    ImageIdentity,
    RuntimeProvenance,
    SandboxProvenance,
    aggregate_observed,
)


def _sandbox(
    *,
    snapshot: str = "evalspec-microsandbox-claude-code-latest-ab12cd34",
    image_digest: str | None = "sha256:aaaa",
    image_digest_status: str = "available",
    image_digest_error: str | None = None,
) -> SandboxProvenance:
    """Build a sandbox-provenance fixture with sensible defaults."""
    return SandboxProvenance(
        backend="microsandbox",
        snapshot=snapshot,
        fingerprint="ab12cd34",
        base_image_ref="ubuntu:latest",
        install_fingerprint="install-abc",
        env_script_sha256="env-abc",
        image_digest=image_digest,
        image_digest_status=image_digest_status,
        image_digest_error=image_digest_error,
    )


def _runtime(
    *,
    arm: str = "opus",
    actual_version: str | None = "1.2.3",
    actual_version_status: str = "available",
    actual_version_error: str | None = None,
    sandbox: SandboxProvenance | None = None,
) -> RuntimeProvenance:
    """Build a runtime-provenance fixture with sensible defaults."""
    return RuntimeProvenance(
        arm=arm,
        actual_version=actual_version,
        actual_version_status=actual_version_status,
        actual_version_error=actual_version_error,
        sandbox=sandbox if sandbox is not None else _sandbox(),
    )


class TestImageIdentityInvariants:
    """Verify ImageIdentity enforces the available/unavailable contract."""

    def test_available_constructor_sets_digest_and_null_error(self) -> None:
        """Verify available constructor sets digest and null error."""
        identity = ImageIdentity.available("sha256:aaaa")

        assert identity.image_digest == "sha256:aaaa"
        assert identity.image_digest_status == "available"
        assert identity.image_digest_error is None

    def test_unavailable_constructor_sets_null_digest_and_error(self) -> None:
        """Verify unavailable constructor sets null digest and error."""
        identity = ImageIdentity.unavailable("manifest fetch timed out")

        assert identity.image_digest is None
        assert identity.image_digest_status == "unavailable"
        assert identity.image_digest_error == "manifest fetch timed out"

    def test_available_with_null_digest_raises(self) -> None:
        """Verify available with null digest raises."""
        with pytest.raises(ValueError, match="non-null value"):
            ImageIdentity(
                image_digest=None, image_digest_status="available", image_digest_error=None
            )

    def test_available_with_error_raises(self) -> None:
        """Verify available with a non-null error raises."""
        with pytest.raises(ValueError, match="null error"):
            ImageIdentity(
                image_digest="sha256:aaaa",
                image_digest_status="available",
                image_digest_error="oops",
            )

    def test_unavailable_with_digest_raises(self) -> None:
        """Verify unavailable with a non-null digest raises."""
        with pytest.raises(ValueError, match="null value"):
            ImageIdentity(
                image_digest="sha256:aaaa",
                image_digest_status="unavailable",
                image_digest_error="oops",
            )

    def test_unavailable_with_empty_error_raises(self) -> None:
        """Verify unavailable with an empty error raises."""
        with pytest.raises(ValueError, match="non-empty error"):
            ImageIdentity(
                image_digest=None, image_digest_status="unavailable", image_digest_error=""
            )


class TestSandboxProvenanceInvariants:
    """Verify SandboxProvenance enforces the flattened image-identity contract."""

    def test_available_digest_fields_construct_cleanly(self) -> None:
        """Verify available digest fields construct cleanly."""
        sandbox = _sandbox(image_digest="sha256:bbbb", image_digest_status="available")

        assert sandbox.image_digest == "sha256:bbbb"

    def test_unavailable_digest_with_present_value_raises(self) -> None:
        """Verify unavailable digest with a present value raises."""
        with pytest.raises(ValueError, match="null value"):
            _sandbox(
                image_digest="sha256:bbbb",
                image_digest_status="unavailable",
                image_digest_error="denied",
            )

    def test_unavailable_digest_without_error_raises(self) -> None:
        """Verify unavailable digest without an error raises."""
        with pytest.raises(ValueError, match="non-empty error"):
            _sandbox(image_digest=None, image_digest_status="unavailable", image_digest_error=None)

    def test_from_image_identity_flattens_fields(self) -> None:
        """Verify from_image_identity flattens fields onto the sandbox record."""
        identity = ImageIdentity.unavailable("registry unreachable")

        sandbox = SandboxProvenance.from_image_identity(
            backend="microsandbox",
            snapshot="snap",
            fingerprint="fp",
            base_image_ref="ubuntu:latest",
            install_fingerprint="install",
            env_script_sha256="env",
            image_identity=identity,
        )

        assert sandbox.image_digest is None
        assert sandbox.image_digest_status == "unavailable"
        assert sandbox.image_digest_error == "registry unreachable"


class TestRuntimeProvenanceInvariants:
    """Verify RuntimeProvenance enforces the actual-version available/unavailable contract."""

    def test_available_version_with_error_raises(self) -> None:
        """Verify available version with a non-null error raises."""
        with pytest.raises(ValueError, match="null error"):
            _runtime(
                actual_version="1.2.3",
                actual_version_status="available",
                actual_version_error="oops",
            )

    def test_unavailable_version_with_value_raises(self) -> None:
        """Verify unavailable version with a present value raises."""
        with pytest.raises(ValueError, match="null value"):
            _runtime(
                actual_version="1.2.3",
                actual_version_status="unavailable",
                actual_version_error="probe failed",
            )

    def test_unavailable_version_without_error_raises(self) -> None:
        """Verify unavailable version without an error raises."""
        with pytest.raises(ValueError, match="non-empty error"):
            _runtime(
                actual_version=None, actual_version_status="unavailable", actual_version_error=None
            )


class TestToObservedDict:
    """Verify to_observed_dict matches the observed_arms[arm] artifact shape."""

    def test_available_shape_omits_error_keys(self) -> None:
        """Verify the available-path shape omits both *_error keys."""
        record = _runtime()

        observed = record.to_observed_dict()

        assert observed == {
            "actual_version": "1.2.3",
            "actual_version_status": "available",
            "sandbox": {
                "backend": "microsandbox",
                "snapshot": "evalspec-microsandbox-claude-code-latest-ab12cd34",
                "fingerprint": "ab12cd34",
                "base_image_ref": "ubuntu:latest",
                "install_fingerprint": "install-abc",
                "env_script_sha256": "env-abc",
                "image_digest": "sha256:aaaa",
                "image_digest_status": "available",
            },
        }
        assert "actual_version_error" not in observed
        assert "image_digest_error" not in observed["sandbox"]

    def test_unavailable_shape_includes_error_keys(self) -> None:
        """Verify the unavailable-path shape includes both *_error keys."""
        record = _runtime(
            actual_version=None,
            actual_version_status="unavailable",
            actual_version_error="guest probe failed: no such binary",
            sandbox=_sandbox(
                image_digest=None,
                image_digest_status="unavailable",
                image_digest_error="manifest fetch timed out",
            ),
        )

        observed = record.to_observed_dict()

        assert observed == {
            "actual_version": None,
            "actual_version_status": "unavailable",
            "actual_version_error": "guest probe failed: no such binary",
            "sandbox": {
                "backend": "microsandbox",
                "snapshot": "evalspec-microsandbox-claude-code-latest-ab12cd34",
                "fingerprint": "ab12cd34",
                "base_image_ref": "ubuntu:latest",
                "install_fingerprint": "install-abc",
                "env_script_sha256": "env-abc",
                "image_digest": None,
                "image_digest_status": "unavailable",
                "image_digest_error": "manifest fetch timed out",
            },
        }


class TestDiskRoundTrip:
    """Verify to_disk_dict/from_disk_dict round-trip exactly."""

    def test_round_trip_available_record(self) -> None:
        """Verify an available record round-trips through disk dict exactly."""
        record = _runtime()

        restored = RuntimeProvenance.from_disk_dict(record.to_disk_dict())

        assert restored == record

    def test_round_trip_unavailable_record(self) -> None:
        """Verify an unavailable record round-trips through disk dict exactly."""
        record = _runtime(
            actual_version=None,
            actual_version_status="unavailable",
            actual_version_error="guest probe failed",
            sandbox=_sandbox(
                image_digest=None,
                image_digest_status="unavailable",
                image_digest_error="manifest fetch timed out",
            ),
        )

        restored = RuntimeProvenance.from_disk_dict(record.to_disk_dict())

        assert restored == record

    def test_disk_dict_is_json_serializable(self) -> None:
        """Verify to_disk_dict output round-trips through actual JSON."""
        import json

        record = _runtime()

        restored = RuntimeProvenance.from_disk_dict(json.loads(json.dumps(record.to_disk_dict())))

        assert restored == record


class TestAggregateObserved:
    """Verify aggregate_observed dedupes and conflict-checks per arm."""

    def test_identical_records_for_one_arm_dedupe(self) -> None:
        """Verify identical records across samples for one arm dedupe to one entry."""
        sample_0 = _runtime(arm="opus")
        sample_1 = _runtime(arm="opus")

        observed = aggregate_observed([sample_0, sample_1])

        assert set(observed) == {"opus"}
        assert observed["opus"] == sample_0.to_observed_dict()

    def test_unavailable_diagnostics_do_not_conflict_for_same_identity(self) -> None:
        """Transient diagnostic text does not make equivalent unavailable records conflict."""
        sample_0 = _runtime(
            arm="opus",
            actual_version=None,
            actual_version_status="unavailable",
            actual_version_error="guest probe timed out after 10 seconds",
            sandbox=_sandbox(
                image_digest=None,
                image_digest_status="unavailable",
                image_digest_error="registry request timed out",
            ),
        )
        sample_1 = _runtime(
            arm="opus",
            actual_version=None,
            actual_version_status="unavailable",
            actual_version_error="guest probe connection reset",
            sandbox=_sandbox(
                image_digest=None,
                image_digest_status="unavailable",
                image_digest_error="registry returned 503",
            ),
        )

        observed = aggregate_observed([sample_0, sample_1])

        assert observed == {"opus": sample_0.to_observed_dict()}

    def test_multiple_arms_each_get_an_entry(self) -> None:
        """Verify each arm with a record gets its own observed entry."""
        opus = _runtime(arm="opus")
        haiku = _runtime(arm="haiku", sandbox=_sandbox(snapshot="snap-haiku"))

        observed = aggregate_observed([opus, haiku])

        assert set(observed) == {"opus", "haiku"}

    def test_arm_with_no_record_has_no_entry(self) -> None:
        """Verify an arm with zero persisted records never appears (Ground rule 4)."""
        observed = aggregate_observed([_runtime(arm="opus")])

        assert "haiku" not in observed

    def test_conflicting_snapshot_raises_naming_arm_and_field(self) -> None:
        """Verify conflicting snapshot values for one arm raise, naming arm and field."""
        first = _runtime(arm="opus", sandbox=_sandbox(snapshot="snap-a"))
        second = _runtime(arm="opus", sandbox=_sandbox(snapshot="snap-b"))

        with pytest.raises(ValueError, match=r"arm `opus`.*`snapshot`"):
            aggregate_observed([first, second])

    def test_conflicting_image_digest_raises_naming_arm_and_field(self) -> None:
        """Verify conflicting image digests for one arm raise, naming arm and field."""
        first = _runtime(arm="opus", sandbox=_sandbox(image_digest="sha256:aaaa"))
        second = _runtime(arm="opus", sandbox=_sandbox(image_digest="sha256:bbbb"))

        with pytest.raises(ValueError, match=r"arm `opus`.*`image_digest`"):
            aggregate_observed([first, second])

    def test_conflicting_actual_version_raises_naming_arm_and_field(self) -> None:
        """Verify conflicting actual_version values for one arm raise, naming arm and field."""
        first = _runtime(arm="opus", actual_version="1.2.3")
        second = _runtime(arm="opus", actual_version="1.2.4")

        with pytest.raises(ValueError, match=r"arm `opus`.*`actual_version`"):
            aggregate_observed([first, second])
