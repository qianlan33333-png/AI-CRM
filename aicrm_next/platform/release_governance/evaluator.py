from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .contracts import CandidateRelation, ReleaseGateDecision, ReleaseGatePhase, ReleaseGateResult
from .manifest import load_release_gate_manifest


EXTERNAL_EFFECT_CHECK_IDS = frozenset(
    {
        "external_effect_due_retryable_backlog",
        "external_effect_unclassified_terminal_recent",
        "external_effect_unclassified_blocked_recent",
    }
)


def _decision(checks: list[dict[str, Any]]) -> ReleaseGateDecision:
    decisions = {str(check.get("gate_decision") or "block") for check in checks}
    if "block" in decisions:
        return "block"
    if "warn" in decisions:
        return "warn"
    return "pass"


def _candidate_relation(checks: list[dict[str, Any]]) -> CandidateRelation:
    values = [check.get("candidate_related", "unknown") for check in checks]
    if any(value is True for value in values):
        return True
    if any(value == "unknown" for value in values):
        return "unknown"
    return False


def _observed_at(checks: Iterable[dict[str, Any]], key: str, fallback: str) -> str:
    values = sorted(str(check.get(key) or "") for check in checks if str(check.get(key) or ""))
    return values[0] if key == "first_observed_at" and values else values[-1] if values else fallback


def _result(
    *,
    gate_id: str,
    owner: str,
    checks: list[dict[str, Any]],
    phase: ReleaseGatePhase,
    candidate_sha: str,
    production_sha: str,
    registry_matches: bool,
    observed_at: str,
) -> ReleaseGateResult:
    decision = _decision(checks)
    if gate_id == "data_health_registry" and not registry_matches:
        decision = "block"
    blocking = sorted(
        {
            f"{str(check.get('check_id') or 'unknown')}:{str(check.get('reason_code') or 'unknown')}"
            for check in checks
            if str(check.get("gate_decision") or "block") == "block"
        }
    )
    warnings = sorted(
        {
            f"{str(check.get('check_id') or 'unknown')}:{str(check.get('reason_code') or 'unknown')}"
            for check in checks
            if str(check.get("gate_decision") or "block") == "warn"
        }
    )
    if gate_id == "data_health_registry" and not registry_matches:
        reason_code = "data_health_registry_mismatch"
        summary = "The production data-health registry does not match the attested release manifest."
    elif blocking:
        reason_code = f"{gate_id}_blocked"
        summary = f"{len(blocking)} blocking data-health result(s) require remediation."
    elif warnings:
        reason_code = f"{gate_id}_warning"
        summary = f"{len(warnings)} classified warning(s) remain visible without blocking release."
    else:
        reason_code = f"{gate_id}_passed"
        summary = "All manifest-declared release checks passed."
    remediation = ""
    replay_policy = "rerun_same_release_gate"
    if decision == "block":
        remediation = (
            "Refresh the complete data-health snapshot and repair the listed gate IDs; "
            "provider replay or production mutation requires its dedicated audited workflow."
        )
        replay_policy = "rerun_after_declared_remediation"
    return ReleaseGateResult(
        gate_id=gate_id,
        phase=phase,
        decision=decision,
        reason_code=reason_code,
        summary=summary,
        actual={"block_count": len(blocking), "warning_count": len(warnings)},
        threshold={"block_count": 0},
        owner=owner,
        candidate_sha=candidate_sha,
        production_sha=production_sha,
        candidate_related=_candidate_relation(checks),
        first_observed_at=_observed_at(checks, "first_observed_at", observed_at),
        last_observed_at=_observed_at(checks, "last_observed_at", observed_at),
        remediation=remediation,
        replay_policy=replay_policy,
        evidence={
            "registry_matches_manifest": registry_matches,
            "blocking_reason_codes": blocking,
            "warning_reason_codes": warnings,
            "check_count": len(checks),
        },
    )


def evaluate_data_health_release_gates(
    summary: dict[str, Any],
    *,
    phase: ReleaseGatePhase,
    candidate_sha: str = "unknown",
    production_sha: str = "unknown",
    now: datetime | None = None,
) -> tuple[ReleaseGateResult, ReleaseGateResult]:
    """Convert one aggregate snapshot into the two canonical release gates."""

    manifest = load_release_gate_manifest()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    raw_checks = summary.get("checks")
    checks = [dict(check) for check in raw_checks if isinstance(check, dict)] if isinstance(raw_checks, list) else []
    actual_ids = tuple(sorted(str(check.get("check_id") or "") for check in checks))
    registry_matches = bool(summary.get("registry_matches_manifest")) and actual_ids == manifest.data_health_check_ids
    external = [check for check in checks if check.get("check_id") in EXTERNAL_EFFECT_CHECK_IDS]
    general = [check for check in checks if check.get("check_id") not in EXTERNAL_EFFECT_CHECK_IDS]
    if len(external) != len(EXTERNAL_EFFECT_CHECK_IDS):
        external.append(
            {
                "check_id": "external_effect_delivery_registry",
                "gate_decision": "block",
                "reason_code": "external_effect_delivery_registry_incomplete",
                "candidate_related": "unknown",
            }
        )
    if not checks:
        general.append(
            {
                "check_id": "data_health_snapshot",
                "gate_decision": "block",
                "reason_code": str(summary.get("error_code") or "data_health_snapshot_unavailable"),
                "candidate_related": "unknown",
            }
        )
    return (
        _result(
            gate_id="data_health_registry",
            owner="insights_data_health",
            checks=general,
            phase=phase,
            candidate_sha=candidate_sha,
            production_sha=production_sha,
            registry_matches=registry_matches,
            observed_at=timestamp,
        ),
        _result(
            gate_id="external_effect_delivery",
            owner="platform_external_effects",
            checks=external,
            phase=phase,
            candidate_sha=candidate_sha,
            production_sha=production_sha,
            registry_matches=registry_matches,
            observed_at=timestamp,
        ),
    )


def release_gate_set_payload(results: Iterable[ReleaseGateResult]) -> dict[str, Any]:
    rows = list(results)
    decision: ReleaseGateDecision = (
        "block"
        if any(row.decision == "block" for row in rows)
        else "warn"
        if any(row.decision == "warn" for row in rows)
        else "pass"
    )
    return {
        "schema_version": "release_gate_result_set.v1",
        "decision": decision,
        "results": [row.model_dump(mode="json") for row in rows],
        "pii_included": False,
        "real_external_call_executed": False,
    }
