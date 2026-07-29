from __future__ import annotations

from typing import Any

from .repo import ExternalEffectRepository, build_external_effect_repository


RECONCILIATION_COUNT_KEYS = (
    "stale_dispatching_count",
    "unknown_after_dispatch_count",
    "reconciliation_required_count",
    "succeeded_without_evidence_count",
    "simulated_recorded_as_succeeded_count",
    "dispatching_without_active_lease_count",
    "lease_on_non_dispatching_count",
)

DELIVERY_EVIDENCE_CUTOVER_AT = "2026-07-13T09:46:09Z"
_CUTOVER_SCOPED_KEYS = (
    "succeeded_without_evidence_count",
    "simulated_recorded_as_succeeded_count",
)


class ExternalEffectDispatchReconciliationService:
    """Count-only delivery truth diagnostics; it never repairs or dispatches."""

    def __init__(self, repository: ExternalEffectRepository | None = None):
        self._repo = repository or build_external_effect_repository()

    def diagnose(self) -> dict[str, Any]:
        metrics = self._repo.queue_metrics({})
        current_evidence_metrics = self._repo.queue_metrics({"completed_from": DELIVERY_EVIDENCE_CUTOVER_AT})
        counts = {key: int(metrics.get(key) or 0) for key in RECONCILIATION_COUNT_KEYS}
        historical_counts: dict[str, int] = {}
        for key in _CUTOVER_SCOPED_KEYS:
            current_count = int(current_evidence_metrics.get(key) or 0)
            historical_counts[key] = max(0, counts[key] - current_count)
            counts[key] = current_count
        return {
            "ok": True,
            "mode": "count_only",
            "repair_supported": False,
            "database_mutation_performed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
            "has_anomalies": any(counts.values()),
            "counts": counts,
            "historical_counts": historical_counts,
            "evidence_cutover_at": DELIVERY_EVIDENCE_CUTOVER_AT,
        }
