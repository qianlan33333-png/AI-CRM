from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RELEASE_GATE_RESULT_SCHEMA_VERSION = "release_gate_result.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_EVIDENCE_KEYS = {
    "external_userid",
    "external_userids",
    "mobile",
    "openid",
    "phone",
    "raw_payload",
    "raw_payload_json",
    "unionid",
    "user_id",
    "userid",
}

ReleaseGatePhase = Literal["pr_ci", "pre_merge_prod", "pre_mutation", "candidate_slot", "post_cutover"]
ReleaseGateDecision = Literal["pass", "warn", "block"]
CandidateRelation = bool | Literal["unknown"]


def _assert_aggregate_evidence(value: object) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_EVIDENCE_KEYS.intersection(str(key).lower() for key in value)
        if forbidden:
            raise ValueError("release_gate_evidence_raw_identity_key_forbidden")
        for nested in value.values():
            _assert_aggregate_evidence(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_aggregate_evidence(nested)


class ReleaseGateResult(BaseModel):
    """Stable, aggregate-only result envelope for every release decision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["release_gate_result.v1"] = RELEASE_GATE_RESULT_SCHEMA_VERSION
    gate_id: str
    phase: ReleaseGatePhase
    decision: ReleaseGateDecision
    reason_code: str
    summary: str
    actual: Any = None
    threshold: Any = None
    owner: str
    candidate_sha: str = "unknown"
    production_sha: str = "unknown"
    candidate_related: CandidateRelation = "unknown"
    first_observed_at: str = ""
    last_observed_at: str = ""
    remediation: str = ""
    replay_policy: str = "manual_after_remediation"
    evidence: dict[str, Any] = Field(default_factory=dict)
    pii_included: Literal[False] = False
    real_external_call_executed: Literal[False] = False

    @field_validator("gate_id", "reason_code", "owner")
    @classmethod
    def _identifier(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if _IDENTIFIER.fullmatch(normalized) is None:
            raise ValueError("release_gate_identifier_invalid")
        return normalized

    @field_validator("candidate_sha", "production_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        normalized = str(value or "unknown").strip().lower() or "unknown"
        if normalized != "unknown" and _FULL_SHA.fullmatch(normalized) is None:
            raise ValueError("release_gate_sha_must_be_full_or_unknown")
        return normalized

    @model_validator(mode="after")
    def _blocking_result_has_operator_path(self) -> "ReleaseGateResult":
        _assert_aggregate_evidence(self.evidence)
        if self.decision == "block" and (not self.remediation.strip() or not self.replay_policy.strip()):
            raise ValueError("blocking_release_gate_requires_remediation_and_replay_policy")
        return self
