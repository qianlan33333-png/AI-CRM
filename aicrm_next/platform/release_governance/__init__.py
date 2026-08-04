"""Trusted release-governance contracts shared by CI and production gates."""

from .contracts import ReleaseGateResult
from .manifest import (
    RELEASE_GATE_MANIFEST_PATH,
    ReleaseGateDefinition,
    ReleaseGateManifest,
    data_health_registry_digest,
    load_release_gate_manifest,
)
from .migration_compatibility import (
    MIGRATION_COMPATIBILITY_MANIFEST_PATH,
    MigrationCompatibilityEntry,
    MigrationCompatibilityManifest,
    load_migration_compatibility_manifest,
)

__all__ = [
    "RELEASE_GATE_MANIFEST_PATH",
    "ReleaseGateDefinition",
    "ReleaseGateManifest",
    "ReleaseGateResult",
    "MIGRATION_COMPATIBILITY_MANIFEST_PATH",
    "MigrationCompatibilityEntry",
    "MigrationCompatibilityManifest",
    "data_health_registry_digest",
    "load_release_gate_manifest",
    "load_migration_compatibility_manifest",
]
