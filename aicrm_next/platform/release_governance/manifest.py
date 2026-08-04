from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ROOT = Path(__file__).resolve().parents[3]
RELEASE_GATE_MANIFEST_PATH = ROOT / "deploy" / "release_gate_manifest.json"
_ALLOWED_COMMAND_ROOTS = ("scripts/ci/", "scripts/ops/", "tests/")


class ReleaseGateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str
    title: str
    owner: str
    phases: tuple[Literal["pr_ci", "pre_merge_prod", "pre_mutation", "candidate_slot", "post_cutover"], ...]
    scopes: tuple[Literal["merge", "promotion", "post_deploy", "warning_only"], ...]
    timeout_seconds: int = Field(ge=1, le=300)
    requires_postgres: bool
    network_policy: Literal["disabled", "database_only", "localhost_only", "public_health_only"]
    mutation_policy: Literal["none", "fixture_only", "inactive_slot", "release_transaction"]
    ci_contract: str
    command_argv: tuple[str, ...] = ()
    remediation: str

    @field_validator("phases", "scopes")
    @classmethod
    def _non_empty_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("release_gate_manifest_sequence_empty_or_duplicate")
        return value

    @field_validator("ci_contract")
    @classmethod
    def _ci_contract_path(cls, value: str) -> str:
        normalized = str(value or "").strip()
        path_text = normalized.split("::", 1)[0]
        if not path_text.startswith("tests/") or not path_text.endswith(".py"):
            raise ValueError("release_gate_ci_contract_must_be_pytest_node")
        return normalized

    @field_validator("command_argv")
    @classmethod
    def _safe_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            return value
        executable = value[0]
        if executable in {"python", "python3", ".venv/bin/python"}:
            if len(value) < 2 or not value[1].startswith(_ALLOWED_COMMAND_ROOTS):
                raise ValueError("release_gate_command_must_target_trusted_repo_path")
        elif not executable.startswith(_ALLOWED_COMMAND_ROOTS):
            raise ValueError("release_gate_command_executable_not_allowed")
        if any(any(token in item for token in (";", "&&", "||", "`", "$(")) for item in value):
            raise ValueError("release_gate_command_shell_syntax_forbidden")
        return value


class ReleaseGateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["release_gate_manifest.v1"]
    result_schema_version: Literal["release_gate_result.v1"]
    gate_set_id: str
    data_health_check_ids: tuple[str, ...]
    gates: tuple[ReleaseGateDefinition, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> "ReleaseGateManifest":
        gate_ids = [gate.gate_id for gate in self.gates]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("release_gate_ids_not_unique")
        if len(self.data_health_check_ids) != len(set(self.data_health_check_ids)):
            raise ValueError("data_health_check_ids_not_unique")
        if tuple(sorted(self.data_health_check_ids)) != self.data_health_check_ids:
            raise ValueError("data_health_check_ids_must_be_sorted")
        return self


def load_release_gate_manifest(path: Path | None = None) -> ReleaseGateManifest:
    source = path or RELEASE_GATE_MANIFEST_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    return ReleaseGateManifest.model_validate(payload)


def data_health_registry_digest(check_ids: tuple[str, ...] | list[str]) -> str:
    canonical = json.dumps(sorted(str(item) for item in check_ids), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
