"""Typed runtime-provenance records for arm-aware sandbox/harness metadata.

`RuntimeProvenance` is captured once per arm as execution resolves and uses a
snapshot: the sandbox's static identity (`SandboxProvenance`, including the
backend-native `ImageIdentity`) plus the task-harness binary version observed
inside the guest. Records persist beside a sample (`provenance.json`) and
aggregate at session-finish via `aggregate_observed` into the `observed_arms`
shape of `meta.json`/`benchmark.json`. Aggregation never synthesizes an entry
for an arm that has no persisted record, and it raises loudly rather than
silently merging two disagreeing environments.

Every `available`/`unavailable` pair below makes the illegal state — a
successful lookup with no value, or a failed one with no explanation —
unrepresentable: constructing one raises `ValueError` immediately.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Status = Literal["available", "unavailable"]


def _check_available_unavailable(
    *, status: str, value: str | None, error: str | None, label: str
) -> None:
    """Enforce the available/unavailable invariant for one value/status/error triple.

    Args:
        status: Either "available" or "unavailable".
        value: The observed value, required when available, forbidden otherwise.
        error: The explanation, forbidden when available, required otherwise.
        label: Name of the field group, used to make the error message specific.

    Raises:
        ValueError: If status/value/error violate the available/unavailable contract.
    """
    if status == "available":
        if value is None:
            raise ValueError(f"{label}: available status requires a non-null value")
        if error is not None:
            raise ValueError(f"{label}: available status requires a null error")
    elif status == "unavailable":
        if value is not None:
            raise ValueError(f"{label}: unavailable status requires a null value")
        if not error:
            raise ValueError(f"{label}: unavailable status requires a non-empty error")
    else:
        raise ValueError(f"{label}: unknown status `{status}`")


@dataclass(frozen=True)
class ImageIdentity:
    """Backend-native image manifest digest, or why it could not be read."""

    image_digest: str | None
    image_digest_status: Status
    image_digest_error: str | None

    def __post_init__(self) -> None:
        """Enforce the available/unavailable digest contract."""
        _check_available_unavailable(
            status=self.image_digest_status,
            value=self.image_digest,
            error=self.image_digest_error,
            label="ImageIdentity",
        )

    @classmethod
    def available(cls, digest: str) -> ImageIdentity:
        """Build an image identity for a successfully read digest."""
        return cls(image_digest=digest, image_digest_status="available", image_digest_error=None)

    @classmethod
    def unavailable(cls, error: str) -> ImageIdentity:
        """Build an image identity for a failed digest lookup."""
        return cls(image_digest=None, image_digest_status="unavailable", image_digest_error=error)


@dataclass(frozen=True)
class SandboxProvenance:
    """Static identity of the sandbox an arm resolved and used.

    `image_digest`/`image_digest_status`/`image_digest_error` are the fields of an
    `ImageIdentity` flattened onto this record rather than nested, matching the
    `observed_arms[arm].sandbox` artifact shape.
    """

    backend: str
    snapshot: str
    fingerprint: str
    base_image_ref: str
    install_fingerprint: str
    env_script_sha256: str
    image_digest: str | None
    image_digest_status: Status
    image_digest_error: str | None

    def __post_init__(self) -> None:
        """Enforce the available/unavailable digest contract."""
        _check_available_unavailable(
            status=self.image_digest_status,
            value=self.image_digest,
            error=self.image_digest_error,
            label="SandboxProvenance",
        )

    @classmethod
    def from_image_identity(
        cls,
        *,
        backend: str,
        snapshot: str,
        fingerprint: str,
        base_image_ref: str,
        install_fingerprint: str,
        env_script_sha256: str,
        image_identity: ImageIdentity,
    ) -> SandboxProvenance:
        """Build sandbox provenance, flattening a resolved `ImageIdentity` onto it."""
        return cls(
            backend=backend,
            snapshot=snapshot,
            fingerprint=fingerprint,
            base_image_ref=base_image_ref,
            install_fingerprint=install_fingerprint,
            env_script_sha256=env_script_sha256,
            image_digest=image_identity.image_digest,
            image_digest_status=image_identity.image_digest_status,
            image_digest_error=image_identity.image_digest_error,
        )


@dataclass(frozen=True)
class RuntimeProvenance:
    """Observed runtime identity for one arm: guest harness version + sandbox."""

    arm: str
    actual_version: str | None
    actual_version_status: Status
    actual_version_error: str | None
    sandbox: SandboxProvenance

    def __post_init__(self) -> None:
        """Enforce the available/unavailable actual-version contract."""
        _check_available_unavailable(
            status=self.actual_version_status,
            value=self.actual_version,
            error=self.actual_version_error,
            label="RuntimeProvenance",
        )

    def to_observed_dict(self) -> dict:
        """Render this record as one `observed_arms[arm]` value.

        The `*_error` keys are included only when the matching status is
        `unavailable` — the available path never emits an unexplained null
        error key, matching the spec's "explained unavailable" contract
        (a `*_error` string exists precisely when there is something to explain).

        Returns:
            The `observed_arms[arm]` dict as defined by spec lines 57–71.
        """
        observed: dict = {
            "actual_version": self.actual_version,
            "actual_version_status": self.actual_version_status,
        }
        if self.actual_version_status == "unavailable":
            observed["actual_version_error"] = self.actual_version_error

        sandbox: dict = {
            "backend": self.sandbox.backend,
            "snapshot": self.sandbox.snapshot,
            "fingerprint": self.sandbox.fingerprint,
            "base_image_ref": self.sandbox.base_image_ref,
            "install_fingerprint": self.sandbox.install_fingerprint,
            "env_script_sha256": self.sandbox.env_script_sha256,
            "image_digest": self.sandbox.image_digest,
            "image_digest_status": self.sandbox.image_digest_status,
        }
        if self.sandbox.image_digest_status == "unavailable":
            sandbox["image_digest_error"] = self.sandbox.image_digest_error

        observed["sandbox"] = sandbox
        return observed

    def to_disk_dict(self) -> dict:
        """Serialize this record for persistence as `provenance.json`.

        Unlike `to_observed_dict`, every field is always present — including a
        null `*_error` on the available path — so `from_disk_dict` can
        reconstruct the record exactly. This is a storage format, not the
        published artifact shape.

        Returns:
            A JSON-serializable dict carrying every field of this record.
        """
        return {
            "arm": self.arm,
            "actual_version": self.actual_version,
            "actual_version_status": self.actual_version_status,
            "actual_version_error": self.actual_version_error,
            "sandbox": {
                "backend": self.sandbox.backend,
                "snapshot": self.sandbox.snapshot,
                "fingerprint": self.sandbox.fingerprint,
                "base_image_ref": self.sandbox.base_image_ref,
                "install_fingerprint": self.sandbox.install_fingerprint,
                "env_script_sha256": self.sandbox.env_script_sha256,
                "image_digest": self.sandbox.image_digest,
                "image_digest_status": self.sandbox.image_digest_status,
                "image_digest_error": self.sandbox.image_digest_error,
            },
        }

    @classmethod
    def from_disk_dict(cls, data: dict) -> RuntimeProvenance:
        """Reconstruct a runtime-provenance record from its disk representation.

        Args:
            data: A dict previously produced by `to_disk_dict`.

        Returns:
            The equivalent `RuntimeProvenance` record.
        """
        sandbox_data = data["sandbox"]
        sandbox = SandboxProvenance(
            backend=sandbox_data["backend"],
            snapshot=sandbox_data["snapshot"],
            fingerprint=sandbox_data["fingerprint"],
            base_image_ref=sandbox_data["base_image_ref"],
            install_fingerprint=sandbox_data["install_fingerprint"],
            env_script_sha256=sandbox_data["env_script_sha256"],
            image_digest=sandbox_data["image_digest"],
            image_digest_status=sandbox_data["image_digest_status"],
            image_digest_error=sandbox_data["image_digest_error"],
        )
        return cls(
            arm=data["arm"],
            actual_version=data["actual_version"],
            actual_version_status=data["actual_version_status"],
            actual_version_error=data["actual_version_error"],
            sandbox=sandbox,
        )


_CONFLICT_FIELDS = ("snapshot", "image_digest", "actual_version")


def _conflict_field_value(record: RuntimeProvenance, field_name: str) -> str | None:
    """Read one of the fields `aggregate_observed` checks for cross-record conflicts."""
    if field_name == "actual_version":
        return record.actual_version
    return getattr(record.sandbox, field_name)


def aggregate_observed(records: Iterable[RuntimeProvenance]) -> dict[str, dict]:
    """Group persisted runtime records by arm into the `observed_arms` mapping.

    Identical records for one arm (for example, one per sample) dedupe to a
    single entry. Records for one arm that disagree on `snapshot`,
    `image_digest`, or `actual_version` describe unlike environments and raise
    rather than silently combining into one report.

    Args:
        records: Runtime-provenance records loaded from `provenance.json` files.

    Returns:
        `{arm_name: observed_dict}`, one entry per arm with at least one record.

    Raises:
        ValueError: If two records for the same arm conflict.
    """
    by_arm: dict[str, RuntimeProvenance] = {}
    for record in records:
        existing = by_arm.get(record.arm)
        if existing is None:
            by_arm[record.arm] = record
            continue
        if existing == record:
            continue

        for field_name in _CONFLICT_FIELDS:
            existing_value = _conflict_field_value(existing, field_name)
            new_value = _conflict_field_value(record, field_name)
            if existing_value != new_value:
                raise ValueError(
                    f"conflicting runtime provenance for arm `{record.arm}`: "
                    f"`{field_name}` differs ({existing_value!r} vs {new_value!r})"
                )
        raise ValueError(
            f"conflicting runtime provenance for arm `{record.arm}`: records differ "
            "but none of the tracked fields (snapshot, image_digest, actual_version) "
            "diverged"
        )

    return {arm: record.to_observed_dict() for arm, record in by_arm.items()}
