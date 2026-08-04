from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


ROOT = Path(__file__).resolve().parents[3]
MIGRATION_COMPATIBILITY_MANIFEST_PATH = ROOT / "deploy" / "migration_compatibility.json"


class MigrationCompatibilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str
    parent_revision: str
    change_kind: Literal["expand", "contract"]
    previous_runtime_compatible: bool
    downgrade_policy: Literal["forward_only", "reversible"]
    compatibility_epoch: int


class MigrationCompatibilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["migration_compatibility.v1"]
    baseline_revision: str
    current_head: str
    compatibility_epoch: int
    migrations: tuple[MigrationCompatibilityEntry, ...]

    @model_validator(mode="after")
    def _linear_current_chain(self) -> "MigrationCompatibilityManifest":
        revisions = [entry.revision for entry in self.migrations]
        if not revisions or len(revisions) != len(set(revisions)):
            raise ValueError("migration_compatibility_revisions_empty_or_duplicate")
        parent = self.baseline_revision
        for entry in self.migrations:
            if entry.parent_revision != parent:
                raise ValueError("migration_compatibility_chain_not_linear")
            if entry.change_kind == "contract" and entry.previous_runtime_compatible:
                raise ValueError("contract_migration_cannot_be_previous_runtime_compatible")
            parent = entry.revision
        if parent != self.current_head:
            raise ValueError("migration_compatibility_current_head_mismatch")
        if self.migrations[-1].compatibility_epoch != self.compatibility_epoch:
            raise ValueError("migration_compatibility_epoch_mismatch")
        return self


def load_migration_compatibility_manifest(
    path: Path | None = None,
) -> MigrationCompatibilityManifest:
    source = path or MIGRATION_COMPATIBILITY_MANIFEST_PATH
    return MigrationCompatibilityManifest.model_validate_json(source.read_text(encoding="utf-8"))
