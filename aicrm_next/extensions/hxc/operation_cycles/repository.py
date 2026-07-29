from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol, cast

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.runtime import raw_database_url

from .domain import (
    OperationCycleConflictError,
    canonical_snapshot_json,
    compute_snapshot_hash,
    validate_attempt_revision,
)
from .dto import (
    OperationCycleDocumentsSnapshot,
    OperationCycleReportReceipt,
    OperationCycleSnapshotV1,
    ReferenceSnapshot,
    RunDetailView,
    RunSummary,
    SnapshotInfo,
    StrategyDetailView,
    StrategySummary,
    StrategyVersionView,
)


DEFAULT_TENANT_ID = "aicrm"
_AI_ASSISTANT_PLAN_SOURCE_SYSTEMS = {"ai_assistant_plan", "cloud_orchestrator_plan"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_dumps(value: Any) -> str:
    return canonical_snapshot_json(value if value is not None else {})


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return deepcopy(default)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return deepcopy(default)
    return deepcopy(value)


def _snapshot_copy(snapshot: OperationCycleSnapshotV1) -> OperationCycleSnapshotV1:
    return OperationCycleSnapshotV1.model_validate(snapshot.model_dump(mode="json"))


def _receipt_id(snapshot_hash: str) -> str:
    return f"ocrcpt_{snapshot_hash[:24]}"


def _run_order_time(snapshot: OperationCycleSnapshotV1, received_at: datetime) -> datetime:
    return (
        snapshot.run.first_sent_at
        or snapshot.run.intended_send_at
        or snapshot.run.started_at
        or snapshot.reported_at
        or received_at
    )


def _fact_conflict(snapshot: OperationCycleSnapshotV1) -> bool:
    effective_sent = snapshot.funnel.effective_sent_count
    has_delivery_fact = (
        snapshot.delivery_status in {"dispatching", "partial", "completed", "failed"}
        or snapshot.run.first_sent_at is not None
        or snapshot.run.last_sent_at is not None
        or (
            effective_sent.status in {"observed", "partial_lower_bound"}
            and bool(effective_sent.value)
        )
    )
    plan_delivery_conflict = (
        snapshot.run.plan_status.lower() == "draft"
        and has_delivery_fact
    )
    return plan_delivery_conflict or bool(snapshot.retrospective.data_conflicts)


def _run_summary_from_snapshot(snapshot: OperationCycleSnapshotV1, *, received_at: datetime) -> RunSummary:
    return RunSummary(
        run_key=snapshot.run.run_key,
        strategy_key=snapshot.strategy.strategy_key,
        label=snapshot.run.label,
        objective=snapshot.run.objective,
        plan_version=snapshot.run.plan_version,
        plan_status=snapshot.run.plan_status,
        plan_source=snapshot.run.plan_source,
        started_at=snapshot.run.started_at,
        completed_at=snapshot.run.completed_at,
        intended_send_at=snapshot.run.intended_send_at,
        plan_scheduled_for=snapshot.run.plan_scheduled_for,
        first_sent_at=snapshot.run.first_sent_at,
        last_sent_at=snapshot.run.last_sent_at,
        execution_stage=snapshot.execution_stage,
        review_status=snapshot.review_status,
        delivery_status=snapshot.delivery_status,
        data_status=snapshot.data_status,
        optimization_status=snapshot.optimization_status,
        artifact_status=snapshot.artifact_status,
        funnel=snapshot.funnel,
        conclusion=snapshot.retrospective.conclusion,
        snapshot_revision=snapshot.snapshot_revision,
        received_at=received_at,
        fact_conflict=_fact_conflict(snapshot),
    )


def _run_summary_order(item: RunSummary) -> datetime:
    return (
        item.first_sent_at
        or item.intended_send_at
        or item.started_at
        or item.received_at
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _assistant_plan_references(references: list[ReferenceSnapshot]) -> list[ReferenceSnapshot]:
    """Keep exact Cloud Orchestrator plan associations without copying plan data."""

    result: list[ReferenceSnapshot] = []
    seen_plan_ids: set[str] = set()
    for reference in references:
        plan_id = _text(reference.source_id)
        if reference.source_system not in _AI_ASSISTANT_PLAN_SOURCE_SYSTEMS or not plan_id:
            continue
        if plan_id in seen_plan_ids:
            continue
        seen_plan_ids.add(plan_id)
        result.append(reference)
    return result


def _reference_from_row(row: dict[str, Any]) -> ReferenceSnapshot:
    return ReferenceSnapshot(
        reference_key=_text(row.get("reference_key")),
        reference_type=cast(Any, _text(row.get("reference_type")) or "other"),
        label=_text(row.get("label")),
        source_system=_text(row.get("source_system")),
        source_id=_text(row.get("source_id")),
        href=_text(row.get("href")),
        evidence_hash=_text(row.get("evidence_hash")),
        data_status=cast(Any, _text(row.get("data_status")) or "unknown"),
    )


def _strategy_summary(
    strategy_snapshot,
    *,
    runs: list[tuple[OperationCycleSnapshotV1, datetime]],
) -> StrategySummary:
    ordered = sorted(runs, key=lambda item: _run_order_time(item[0], item[1]), reverse=True)
    if not ordered:
        return StrategySummary(
            strategy_key=strategy_snapshot.strategy_key,
            title=strategy_snapshot.title,
            description=strategy_snapshot.description,
            cadence=strategy_snapshot.cadence,
            timezone=strategy_snapshot.timezone,
            status=strategy_snapshot.status,
            current_version=strategy_snapshot.version,
        )
    latest, received_at = ordered[0]
    return StrategySummary(
        strategy_key=strategy_snapshot.strategy_key,
        title=strategy_snapshot.title,
        description=strategy_snapshot.description,
        cadence=strategy_snapshot.cadence,
        timezone=strategy_snapshot.timezone,
        status=strategy_snapshot.status,
        current_version=strategy_snapshot.version,
        run_count=len(runs),
        latest_run_key=latest.run.run_key,
        latest_run_label=latest.run.label,
        latest_run_at=_run_order_time(latest, received_at),
        execution_stage=latest.execution_stage,
        review_status=latest.review_status,
        delivery_status=latest.delivery_status,
        data_status=latest.data_status,
        optimization_status=latest.optimization_status,
        artifact_status=latest.artifact_status,
        funnel=latest.funnel,
        conclusion=latest.retrospective.conclusion,
        next_iteration_summary=latest.next_iteration.summary,
    )


class OperationCycleRepository(Protocol):
    def save_snapshot(
        self,
        snapshot: OperationCycleSnapshotV1,
        *,
        idempotency_key: str,
        reporter_id: str = "",
        client_id: str = "",
    ) -> OperationCycleReportReceipt: ...

    def list_strategy_summaries(self, *, limit: int = 50, offset: int = 0) -> list[StrategySummary]: ...

    def get_strategy_detail(self, strategy_key: str) -> StrategyDetailView | None: ...

    def list_run_summaries(self, strategy_key: str, *, limit: int = 50, offset: int = 0) -> list[RunSummary]: ...

    def get_run_detail(self, run_key: str) -> RunDetailView | None: ...


class InMemoryOperationCycleRepository:
    """Explicit test repository; it is never selected as a production fallback."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_idempotency: dict[tuple[str, str], dict[str, Any]] = {}
        self._by_report: dict[tuple[str, str], dict[str, Any]] = {}
        self._latest_by_run: dict[tuple[str, str], dict[str, Any]] = {}
        self._strategies: dict[tuple[str, str], Any] = {}
        self._strategy_versions: dict[tuple[str, str, int], tuple[str, Any, datetime]] = {}

    @property
    def snapshot_count(self) -> int:
        return len(self._by_report)

    def save_snapshot(
        self,
        snapshot: OperationCycleSnapshotV1,
        *,
        idempotency_key: str,
        reporter_id: str = "",
        client_id: str = "",
    ) -> OperationCycleReportReceipt:
        key = _text(idempotency_key)
        if not key:
            raise ValueError("idempotency_key is required")
        if len(key) > 200:
            raise ValueError("idempotency_key must be at most 200 characters")
        snapshot = _snapshot_copy(snapshot)
        tenant_id = _text(snapshot.tenant_id) or DEFAULT_TENANT_ID
        snapshot_hash = compute_snapshot_hash(snapshot)
        received_at = _utcnow()
        with self._lock:
            existing = self._by_idempotency.get((tenant_id, key))
            if existing:
                if existing["snapshot_hash"] != snapshot_hash:
                    raise OperationCycleConflictError("idempotency_payload_mismatch")
                return existing["receipt"].model_copy(update={"projection_updated": False})

            existing_report = self._by_report.get((tenant_id, snapshot.report_id))
            if existing_report:
                if existing_report["snapshot_hash"] != snapshot_hash:
                    raise OperationCycleConflictError("report_id_payload_mismatch")
                return existing_report["receipt"].model_copy(update={"projection_updated": False})

            strategy_version_key = (tenant_id, snapshot.strategy.strategy_key, snapshot.strategy.version)
            strategy_hash = compute_snapshot_hash(snapshot.strategy)
            existing_version = self._strategy_versions.get(strategy_version_key)
            if existing_version and existing_version[0] != strategy_hash:
                raise OperationCycleConflictError("strategy_version_payload_mismatch")

            run_key = (tenant_id, snapshot.run.run_key)
            previous = self._latest_by_run.get(run_key)
            if previous:
                previous_snapshot: OperationCycleSnapshotV1 = previous["snapshot"]
                if previous_snapshot.strategy.strategy_key != snapshot.strategy.strategy_key:
                    raise OperationCycleConflictError("run_strategy_conflict")
                if previous_snapshot.strategy.version != snapshot.strategy.version:
                    raise OperationCycleConflictError("run_strategy_version_conflict")
                if snapshot.snapshot_revision < previous_snapshot.snapshot_revision:
                    raise OperationCycleConflictError("snapshot_revision_regression")
                if snapshot.snapshot_revision == previous_snapshot.snapshot_revision:
                    raise OperationCycleConflictError("snapshot_revision_conflict")
                validate_attempt_revision(previous_snapshot, snapshot)

            receipt = OperationCycleReportReceipt(
                receipt_id=_receipt_id(snapshot_hash),
                strategy_key=snapshot.strategy.strategy_key,
                run_key=snapshot.run.run_key,
                accepted_revision=snapshot.snapshot_revision,
                projection_updated=True,
                snapshot_hash=snapshot_hash,
            )
            stored = {
                "snapshot": snapshot,
                "snapshot_hash": snapshot_hash,
                "receipt": receipt,
                "idempotency_key": key,
                "reporter_id": _text(reporter_id),
                "client_id": _text(client_id),
                "received_at": received_at,
            }
            self._by_idempotency[(tenant_id, key)] = stored
            self._by_report[(tenant_id, snapshot.report_id)] = stored
            self._latest_by_run[run_key] = stored
            if not existing_version:
                self._strategy_versions[strategy_version_key] = (
                    strategy_hash,
                    deepcopy(snapshot.strategy),
                    received_at,
                )
            current = self._strategies.get((tenant_id, snapshot.strategy.strategy_key))
            if current is None or snapshot.strategy.version == current.version:
                self._strategies[(tenant_id, snapshot.strategy.strategy_key)] = deepcopy(snapshot.strategy)
            return receipt

    def _runs_for_strategy(self, tenant_id: str, strategy_key: str) -> list[dict[str, Any]]:
        return [
            stored
            for (stored_tenant, _run_key), stored in self._latest_by_run.items()
            if stored_tenant == tenant_id and stored["snapshot"].strategy.strategy_key == strategy_key
        ]

    def list_strategy_summaries(self, *, limit: int = 50, offset: int = 0) -> list[StrategySummary]:
        with self._lock:
            summaries: list[StrategySummary] = []
            for (tenant_id, strategy_key), strategy in self._strategies.items():
                if tenant_id != DEFAULT_TENANT_ID:
                    continue
                runs = [
                    (stored["snapshot"], stored["received_at"])
                    for stored in self._runs_for_strategy(tenant_id, strategy_key)
                ]
                summaries.append(_strategy_summary(strategy, runs=runs))
            summaries.sort(key=lambda item: (item.latest_run_at or datetime.min.replace(tzinfo=timezone.utc), item.strategy_key), reverse=True)
            start = max(0, int(offset or 0))
            return deepcopy(summaries[start : start + max(1, min(int(limit or 50), 100))])

    def get_strategy_detail(self, strategy_key: str) -> StrategyDetailView | None:
        strategy_key = _text(strategy_key)
        with self._lock:
            strategy = self._strategies.get((DEFAULT_TENANT_ID, strategy_key))
            if strategy is None:
                return None
            stored_runs = self._runs_for_strategy(DEFAULT_TENANT_ID, strategy_key)
            summary = _strategy_summary(
                strategy,
                runs=[(item["snapshot"], item["received_at"]) for item in stored_runs],
            )
            versions = [
                StrategyVersionView(
                    version=version,
                    label=version_snapshot.version_label,
                    objective=version_snapshot.objective,
                    definition=version_snapshot.definition,
                    effective_from=version_snapshot.version_effective_from,
                    created_at=created_at,
                )
                for (tenant_id, stored_strategy_key, version), (_hash, version_snapshot, created_at) in self._strategy_versions.items()
                if tenant_id == DEFAULT_TENANT_ID and stored_strategy_key == strategy_key
            ]
            versions.sort(key=lambda item: item.version, reverse=True)
            trend = [
                _run_summary_from_snapshot(item["snapshot"], received_at=item["received_at"])
                for item in stored_runs
            ]
            trend.sort(key=_run_summary_order, reverse=True)
            latest_sources: list[ReferenceSnapshot] = []
            latest_documents = OperationCycleDocumentsSnapshot()
            if stored_runs:
                ordered_stored_runs = sorted(
                    stored_runs,
                    key=lambda item: _run_order_time(item["snapshot"], item["received_at"]),
                    reverse=True,
                )
                latest_stored = ordered_stored_runs[0]
                latest_sources = list(latest_stored["snapshot"].references)
                latest_documents = latest_stored["snapshot"].documents
            assistant_plans = _assistant_plan_references(
                [
                    reference
                    for stored in sorted(
                        stored_runs,
                        key=lambda item: _run_order_time(item["snapshot"], item["received_at"]),
                        reverse=True,
                    )
                    for reference in stored["snapshot"].references
                ]
            )
            return StrategyDetailView(
                strategy=summary,
                versions=versions,
                trend=trend,
                sources=latest_sources,
                documents=latest_documents,
                assistant_plans=assistant_plans,
            )

    def list_run_summaries(self, strategy_key: str, *, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        with self._lock:
            rows = [
                _run_summary_from_snapshot(item["snapshot"], received_at=item["received_at"])
                for item in self._runs_for_strategy(DEFAULT_TENANT_ID, _text(strategy_key))
            ]
            rows.sort(key=_run_summary_order, reverse=True)
            start = max(0, int(offset or 0))
            return deepcopy(rows[start : start + max(1, min(int(limit or 50), 100))])

    def get_run_detail(self, run_key: str) -> RunDetailView | None:
        with self._lock:
            stored = self._latest_by_run.get((DEFAULT_TENANT_ID, _text(run_key)))
            if stored is None:
                return None
            snapshot: OperationCycleSnapshotV1 = stored["snapshot"]
            return RunDetailView(
                run=_run_summary_from_snapshot(snapshot, received_at=stored["received_at"]),
                attempts=snapshot.attempts,
                stages=snapshot.stages,
                metrics=snapshot.metrics,
                retrospective=snapshot.retrospective,
                next_iteration=snapshot.next_iteration,
                references=snapshot.references,
                documents=snapshot.documents,
                snapshot=SnapshotInfo(
                    report_id=snapshot.report_id,
                    snapshot_revision=snapshot.snapshot_revision,
                    snapshot_hash=stored["snapshot_hash"],
                    schema_version=snapshot.schema_version,
                    reporter_id=stored["reporter_id"],
                    client_id=stored["client_id"],
                    received_at=stored["received_at"],
                ),
            )


class PostgresOperationCycleRepository:
    def __init__(self, session_factory=None, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._tenant_id = _text(tenant_id) or DEFAULT_TENANT_ID

    @staticmethod
    def _existing_receipt(row: dict[str, Any], *, snapshot_hash: str) -> OperationCycleReportReceipt:
        if _text(row.get("payload_hash")) != snapshot_hash:
            raise OperationCycleConflictError("idempotency_payload_mismatch")
        return OperationCycleReportReceipt(
            receipt_id=_text(row.get("receipt_id")),
            strategy_key=_text(row.get("strategy_key")),
            run_key=_text(row.get("run_key")),
            accepted_revision=int(row.get("snapshot_revision") or 0),
            projection_updated=False,
            snapshot_hash=snapshot_hash,
        )

    def save_snapshot(
        self,
        snapshot: OperationCycleSnapshotV1,
        *,
        idempotency_key: str,
        reporter_id: str = "",
        client_id: str = "",
    ) -> OperationCycleReportReceipt:
        key = _text(idempotency_key)
        if not key:
            raise ValueError("idempotency_key is required")
        if len(key) > 200:
            raise ValueError("idempotency_key must be at most 200 characters")
        snapshot = _snapshot_copy(snapshot)
        tenant_id = _text(snapshot.tenant_id) or self._tenant_id
        if tenant_id != self._tenant_id:
            raise ValueError("snapshot tenant_id is outside repository scope")
        snapshot_hash = compute_snapshot_hash(snapshot)
        strategy_hash = compute_snapshot_hash(snapshot.strategy)
        receipt_id = _receipt_id(snapshot_hash)

        with self._session_factory() as session:
            try:
                for lock_key in sorted(
                    {
                        f"operation_cycle:idempotency:{tenant_id}:{key}",
                        f"operation_cycle:report:{tenant_id}:{snapshot.report_id}",
                        f"operation_cycle:run:{tenant_id}:{snapshot.run.run_key}",
                    }
                ):
                    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})

                existing = session.execute(
                    text(_EXISTING_SNAPSHOT_SQL + " AND snap.idempotency_key = :lookup LIMIT 1"),
                    {"tenant_id": tenant_id, "lookup": key},
                ).mappings().fetchone()
                if existing:
                    receipt = self._existing_receipt(dict(existing), snapshot_hash=snapshot_hash)
                    session.commit()
                    return receipt

                existing_report = session.execute(
                    text(_EXISTING_SNAPSHOT_SQL + " AND snap.report_id = :lookup LIMIT 1"),
                    {"tenant_id": tenant_id, "lookup": snapshot.report_id},
                ).mappings().fetchone()
                if existing_report:
                    row = dict(existing_report)
                    if _text(row.get("payload_hash")) != snapshot_hash:
                        raise OperationCycleConflictError("report_id_payload_mismatch")
                    receipt = self._existing_receipt(row, snapshot_hash=snapshot_hash)
                    session.commit()
                    return receipt

                strategy_row = session.execute(
                    text(
                        """
                        INSERT INTO operation_cycle_strategies (
                            tenant_id, strategy_key, title, description, cadence, timezone,
                            status, current_version, created_at, updated_at
                        ) VALUES (
                            :tenant_id, :strategy_key, :title, :description, :cadence, :timezone,
                            :status, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (tenant_id, strategy_key) DO UPDATE SET
                            title = CASE WHEN EXCLUDED.current_version = operation_cycle_strategies.current_version THEN EXCLUDED.title ELSE operation_cycle_strategies.title END,
                            description = CASE WHEN EXCLUDED.current_version = operation_cycle_strategies.current_version THEN EXCLUDED.description ELSE operation_cycle_strategies.description END,
                            cadence = CASE WHEN EXCLUDED.current_version = operation_cycle_strategies.current_version THEN EXCLUDED.cadence ELSE operation_cycle_strategies.cadence END,
                            timezone = CASE WHEN EXCLUDED.current_version = operation_cycle_strategies.current_version THEN EXCLUDED.timezone ELSE operation_cycle_strategies.timezone END,
                            status = CASE WHEN EXCLUDED.current_version = operation_cycle_strategies.current_version THEN EXCLUDED.status ELSE operation_cycle_strategies.status END,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "strategy_key": snapshot.strategy.strategy_key,
                        "title": snapshot.strategy.title,
                        "description": snapshot.strategy.description,
                        "cadence": snapshot.strategy.cadence,
                        "timezone": snapshot.strategy.timezone,
                        "status": snapshot.strategy.status,
                        "version": snapshot.strategy.version,
                    },
                ).mappings().one()
                strategy_row = dict(strategy_row)

                existing_version = session.execute(
                    text(
                        "SELECT * FROM operation_cycle_strategy_versions "
                        "WHERE strategy_id = :strategy_id AND version = :version FOR UPDATE"
                    ),
                    {"strategy_id": strategy_row["id"], "version": snapshot.strategy.version},
                ).mappings().fetchone()
                if existing_version and _text(existing_version.get("version_hash")) != strategy_hash:
                    raise OperationCycleConflictError("strategy_version_payload_mismatch")
                if existing_version:
                    strategy_version_row = dict(existing_version)
                else:
                    strategy_version_row = dict(
                        session.execute(
                            text(
                                """
                                INSERT INTO operation_cycle_strategy_versions (
                                    strategy_id, version, label, objective, definition_json,
                                    version_hash, effective_from, governance_status,
                                    confirmed_at, created_at
                                ) VALUES (
                                    :strategy_id, :version, :label, :objective,
                                    CAST(:definition_json AS jsonb), :version_hash,
                                    :effective_from, :governance_status,
                                    CASE WHEN :governance_status = 'legacy_confirmed' THEN CURRENT_TIMESTAMP ELSE NULL END,
                                    CURRENT_TIMESTAMP
                                ) RETURNING *
                                """
                            ),
                            {
                                "strategy_id": strategy_row["id"],
                                "version": snapshot.strategy.version,
                                "label": snapshot.strategy.version_label,
                                "objective": snapshot.strategy.objective,
                                "definition_json": _json_dumps(snapshot.strategy.definition),
                                "version_hash": strategy_hash,
                                "effective_from": snapshot.strategy.version_effective_from,
                                "governance_status": (
                                    "legacy_confirmed"
                                    if int(strategy_row.get("current_version") or 0) == snapshot.strategy.version
                                    else "observed"
                                ),
                            },
                        ).mappings().one()
                    )

                run_row = session.execute(
                    text(
                        "SELECT * FROM operation_cycle_runs "
                        "WHERE tenant_id = :tenant_id AND run_key = :run_key FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, "run_key": snapshot.run.run_key},
                ).mappings().fetchone()
                if run_row:
                    run_row = dict(run_row)
                    if int(run_row["strategy_id"]) != int(strategy_row["id"]):
                        raise OperationCycleConflictError("run_strategy_conflict")
                    if int(run_row["strategy_version_id"]) != int(strategy_version_row["id"]):
                        raise OperationCycleConflictError("run_strategy_version_conflict")
                    current_revision = int(run_row.get("latest_snapshot_revision") or 0)
                    if snapshot.snapshot_revision < current_revision:
                        raise OperationCycleConflictError("snapshot_revision_regression")
                    if snapshot.snapshot_revision == current_revision:
                        raise OperationCycleConflictError("snapshot_revision_conflict")
                    previous_snapshot_row = session.execute(
                        text("SELECT payload_json FROM operation_cycle_snapshots WHERE id = :snapshot_id"),
                        {"snapshot_id": run_row.get("latest_snapshot_id")},
                    ).mappings().fetchone()
                    if previous_snapshot_row:
                        previous_snapshot = OperationCycleSnapshotV1.model_validate(
                            _json_value(previous_snapshot_row.get("payload_json"), {})
                        )
                        validate_attempt_revision(previous_snapshot, snapshot)
                else:
                    run_row = dict(
                        session.execute(
                            text(
                                """
                                INSERT INTO operation_cycle_runs (
                                    tenant_id, strategy_id, strategy_version_id, run_key,
                                    label, objective, plan_version, plan_status, plan_source,
                                    created_at, updated_at
                                ) VALUES (
                                    :tenant_id, :strategy_id, :strategy_version_id, :run_key,
                                    :label, :objective, :plan_version, :plan_status, :plan_source,
                                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                                ) RETURNING *
                                """
                            ),
                            {
                                "tenant_id": tenant_id,
                                "strategy_id": strategy_row["id"],
                                "strategy_version_id": strategy_version_row["id"],
                                "run_key": snapshot.run.run_key,
                                "label": snapshot.run.label,
                                "objective": snapshot.run.objective,
                                "plan_version": snapshot.run.plan_version,
                                "plan_status": snapshot.run.plan_status,
                                "plan_source": snapshot.run.plan_source,
                            },
                        ).mappings().one()
                    )

                snapshot_row = dict(
                    session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_snapshots (
                                tenant_id, run_id, report_id, idempotency_key,
                                snapshot_revision, schema_version, payload_hash, payload_json,
                                reporter_id, client_id, reported_at, receipt_id, received_at
                            ) VALUES (
                                :tenant_id, :run_id, :report_id, :idempotency_key,
                                :snapshot_revision, :schema_version, :payload_hash,
                                CAST(:payload_json AS jsonb), :reporter_id, :client_id,
                                :reported_at, :receipt_id, CURRENT_TIMESTAMP
                            ) RETURNING *
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "run_id": run_row["id"],
                            "report_id": snapshot.report_id,
                            "idempotency_key": key,
                            "snapshot_revision": snapshot.snapshot_revision,
                            "schema_version": snapshot.schema_version,
                            "payload_hash": snapshot_hash,
                            "payload_json": _json_dumps(snapshot.model_dump(mode="json")),
                            "reporter_id": _text(reporter_id),
                            "client_id": _text(client_id),
                            "reported_at": snapshot.reported_at,
                            "receipt_id": receipt_id,
                        },
                    ).mappings().one()
                )
                snapshot_id = int(snapshot_row["id"])

                attempt_ids: dict[str, int] = {}
                for attempt in snapshot.attempts:
                    attempt_row = session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_attempts (
                                run_id, attempt_key, parent_attempt_key, status,
                                started_at, ended_at, blocked_reason, summary_json,
                                last_snapshot_id, created_at, updated_at
                            ) VALUES (
                                :run_id, :attempt_key, :parent_attempt_key, :status,
                                :started_at, :ended_at, :blocked_reason,
                                CAST(:summary_json AS jsonb), :snapshot_id,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (run_id, attempt_key) DO UPDATE SET
                                parent_attempt_key = EXCLUDED.parent_attempt_key,
                                status = EXCLUDED.status,
                                started_at = EXCLUDED.started_at,
                                ended_at = EXCLUDED.ended_at,
                                blocked_reason = EXCLUDED.blocked_reason,
                                summary_json = EXCLUDED.summary_json,
                                last_snapshot_id = EXCLUDED.last_snapshot_id,
                                updated_at = CURRENT_TIMESTAMP
                            RETURNING id
                            """
                        ),
                        {
                            "run_id": run_row["id"],
                            "attempt_key": attempt.attempt_key,
                            "parent_attempt_key": attempt.parent_attempt_key,
                            "status": attempt.status,
                            "started_at": attempt.started_at,
                            "ended_at": attempt.ended_at,
                            "blocked_reason": attempt.blocked_reason,
                            "summary_json": _json_dumps(attempt.summary),
                            "snapshot_id": snapshot_id,
                        },
                    ).mappings().one()
                    attempt_ids[attempt.attempt_key] = int(attempt_row["id"])
                self._delete_stale(session, "operation_cycle_attempts", "attempt_key", run_row["id"], list(attempt_ids))

                stage_keys: list[str] = []
                for stage in snapshot.stages:
                    session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_stages (
                                run_id, attempt_id, stage_key, stage_name, status,
                                started_at, ended_at, blocked_reason, summary_json,
                                last_snapshot_id, created_at, updated_at
                            ) VALUES (
                                :run_id, :attempt_id, :stage_key, :stage_name, :status,
                                :started_at, :ended_at, :blocked_reason,
                                CAST(:summary_json AS jsonb), :snapshot_id,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (run_id, stage_key) DO UPDATE SET
                                attempt_id = EXCLUDED.attempt_id,
                                stage_name = EXCLUDED.stage_name,
                                status = EXCLUDED.status,
                                started_at = EXCLUDED.started_at,
                                ended_at = EXCLUDED.ended_at,
                                blocked_reason = EXCLUDED.blocked_reason,
                                summary_json = EXCLUDED.summary_json,
                                last_snapshot_id = EXCLUDED.last_snapshot_id,
                                updated_at = CURRENT_TIMESTAMP
                            """
                        ),
                        {
                            "run_id": run_row["id"],
                            "attempt_id": attempt_ids[stage.attempt_key],
                            "stage_key": stage.stage_key,
                            "stage_name": stage.stage,
                            "status": stage.status,
                            "started_at": stage.started_at,
                            "ended_at": stage.ended_at,
                            "blocked_reason": stage.blocked_reason,
                            "summary_json": _json_dumps(stage.summary),
                            "snapshot_id": snapshot_id,
                        },
                    )
                    stage_keys.append(stage.stage_key)
                self._delete_stale(session, "operation_cycle_stages", "stage_key", run_row["id"], stage_keys)

                metric_keys: list[str] = []
                for metric in snapshot.metrics:
                    session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_metrics (
                                run_id, metric_key, label, numerator, denominator, value,
                                unit, observation_window, data_source, data_quality,
                                limitations_json, is_causal, value_status, last_snapshot_id,
                                created_at, updated_at
                            ) VALUES (
                                :run_id, :metric_key, :label, :numerator, :denominator, :value,
                                :unit, :observation_window, :data_source, :data_quality,
                                CAST(:limitations_json AS jsonb), FALSE, :value_status, :snapshot_id,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (run_id, metric_key) DO UPDATE SET
                                label = EXCLUDED.label,
                                numerator = EXCLUDED.numerator,
                                denominator = EXCLUDED.denominator,
                                value = EXCLUDED.value,
                                unit = EXCLUDED.unit,
                                observation_window = EXCLUDED.observation_window,
                                data_source = EXCLUDED.data_source,
                                data_quality = EXCLUDED.data_quality,
                                limitations_json = EXCLUDED.limitations_json,
                                is_causal = FALSE,
                                value_status = EXCLUDED.value_status,
                                last_snapshot_id = EXCLUDED.last_snapshot_id,
                                updated_at = CURRENT_TIMESTAMP
                            """
                        ),
                        {
                            "run_id": run_row["id"],
                            "metric_key": metric.metric_key,
                            "label": metric.label,
                            "numerator": metric.numerator,
                            "denominator": metric.denominator,
                            "value": metric.value,
                            "unit": metric.unit,
                            "observation_window": metric.observation_window,
                            "data_source": metric.data_source,
                            "data_quality": metric.data_quality,
                            "limitations_json": _json_dumps(metric.limitations),
                            "value_status": metric.value_status,
                            "snapshot_id": snapshot_id,
                        },
                    )
                    metric_keys.append(metric.metric_key)
                self._delete_stale(session, "operation_cycle_metrics", "metric_key", run_row["id"], metric_keys)

                reference_keys: list[str] = []
                for reference in snapshot.references:
                    session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_references (
                                run_id, reference_key, reference_type, label, source_system,
                                source_id, href, evidence_hash, data_status, last_snapshot_id,
                                created_at, updated_at
                            ) VALUES (
                                :run_id, :reference_key, :reference_type, :label, :source_system,
                                :source_id, :href, :evidence_hash, :data_status, :snapshot_id,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (run_id, reference_key) DO UPDATE SET
                                reference_type = EXCLUDED.reference_type,
                                label = EXCLUDED.label,
                                source_system = EXCLUDED.source_system,
                                source_id = EXCLUDED.source_id,
                                href = EXCLUDED.href,
                                evidence_hash = EXCLUDED.evidence_hash,
                                data_status = EXCLUDED.data_status,
                                last_snapshot_id = EXCLUDED.last_snapshot_id,
                                updated_at = CURRENT_TIMESTAMP
                            """
                        ),
                        {
                            "run_id": run_row["id"],
                            "reference_key": reference.reference_key,
                            "reference_type": reference.reference_type,
                            "label": reference.label,
                            "source_system": reference.source_system,
                            "source_id": reference.source_id,
                            "href": reference.href,
                            "evidence_hash": reference.evidence_hash,
                            "data_status": reference.data_status,
                            "snapshot_id": snapshot_id,
                        },
                    )
                    reference_keys.append(reference.reference_key)
                self._delete_stale(session, "operation_cycle_references", "reference_key", run_row["id"], reference_keys)

                session.execute(
                    text(
                        """
                        UPDATE operation_cycle_runs SET
                            label = :label,
                            objective = :objective,
                            plan_version = :plan_version,
                            plan_status = :plan_status,
                            plan_source = :plan_source,
                            started_at = :started_at,
                            completed_at = :completed_at,
                            intended_send_at = :intended_send_at,
                            plan_scheduled_for = :plan_scheduled_for,
                            first_sent_at = :first_sent_at,
                            last_sent_at = :last_sent_at,
                            execution_stage = :execution_stage,
                            review_status = :review_status,
                            delivery_status = :delivery_status,
                            data_status = :data_status,
                            optimization_status = :optimization_status,
                            artifact_status = :artifact_status,
                            funnel_json = CAST(:funnel_json AS jsonb),
                            retrospective_json = CAST(:retrospective_json AS jsonb),
                            next_iteration_json = CAST(:next_iteration_json AS jsonb),
                            fact_conflict = :fact_conflict,
                            latest_snapshot_revision = :snapshot_revision,
                            latest_snapshot_id = :snapshot_id,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :run_id
                        """
                    ),
                    {
                        "run_id": run_row["id"],
                        "label": snapshot.run.label,
                        "objective": snapshot.run.objective,
                        "plan_version": snapshot.run.plan_version,
                        "plan_status": snapshot.run.plan_status,
                        "plan_source": snapshot.run.plan_source,
                        "started_at": snapshot.run.started_at,
                        "completed_at": snapshot.run.completed_at,
                        "intended_send_at": snapshot.run.intended_send_at,
                        "plan_scheduled_for": snapshot.run.plan_scheduled_for,
                        "first_sent_at": snapshot.run.first_sent_at,
                        "last_sent_at": snapshot.run.last_sent_at,
                        "execution_stage": snapshot.execution_stage,
                        "review_status": snapshot.review_status,
                        "delivery_status": snapshot.delivery_status,
                        "data_status": snapshot.data_status,
                        "optimization_status": snapshot.optimization_status,
                        "artifact_status": snapshot.artifact_status,
                        "funnel_json": _json_dumps(snapshot.funnel.model_dump(mode="json")),
                        "retrospective_json": _json_dumps(snapshot.retrospective.model_dump(mode="json")),
                        "next_iteration_json": _json_dumps(snapshot.next_iteration.model_dump(mode="json")),
                        "fact_conflict": _fact_conflict(snapshot),
                        "snapshot_revision": snapshot.snapshot_revision,
                        "snapshot_id": snapshot_id,
                    },
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        return OperationCycleReportReceipt(
            receipt_id=receipt_id,
            strategy_key=snapshot.strategy.strategy_key,
            run_key=snapshot.run.run_key,
            accepted_revision=snapshot.snapshot_revision,
            projection_updated=True,
            snapshot_hash=snapshot_hash,
        )

    @staticmethod
    def _delete_stale(session, table_name: str, key_column: str, run_id: int, keys: list[str]) -> None:
        allowed = {
            ("operation_cycle_attempts", "attempt_key"),
            ("operation_cycle_stages", "stage_key"),
            ("operation_cycle_metrics", "metric_key"),
            ("operation_cycle_references", "reference_key"),
        }
        if (table_name, key_column) not in allowed:
            raise ValueError("unsupported operation-cycle projection table")
        if keys:
            session.execute(
                text(f"DELETE FROM {table_name} WHERE run_id = :run_id AND NOT ({key_column} = ANY(:keys))"),
                {"run_id": run_id, "keys": keys},
            )
        else:
            session.execute(text(f"DELETE FROM {table_name} WHERE run_id = :run_id"), {"run_id": run_id})

    def _one(self, statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params).mappings().fetchone()
        return dict(row) if row else None

    def _all(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params).mappings().all()
        return [dict(row) for row in rows]

    def list_strategy_summaries(self, *, limit: int = 50, offset: int = 0) -> list[StrategySummary]:
        rows = self._all(
            _STRATEGY_SUMMARY_SQL + " ORDER BY latest_run_at DESC NULLS LAST, s.strategy_key ASC LIMIT :limit OFFSET :offset",
            {"tenant_id": self._tenant_id, "limit": int(limit), "offset": int(offset)},
        )
        return [_strategy_summary_from_row(row) for row in rows]

    def get_strategy_detail(self, strategy_key: str) -> StrategyDetailView | None:
        row = self._one(
            _STRATEGY_SUMMARY_SQL + " AND s.strategy_key = :strategy_key LIMIT 1",
            {"tenant_id": self._tenant_id, "strategy_key": _text(strategy_key)},
        )
        if row is None:
            return None
        versions = self._all(
            """
            SELECT v.* FROM operation_cycle_strategy_versions v
            JOIN operation_cycle_strategies s ON s.id = v.strategy_id
            WHERE s.tenant_id = :tenant_id AND s.strategy_key = :strategy_key
            ORDER BY v.version DESC
            """,
            {"tenant_id": self._tenant_id, "strategy_key": _text(strategy_key)},
        )
        trend = self.list_run_summaries(strategy_key, limit=100, offset=0)
        latest_detail = self.get_run_detail(trend[0].run_key) if trend else None
        assistant_rows = self._all(
            """
            WITH assistant_plan_references AS (
                SELECT
                    ref.reference_key,
                    ref.reference_type,
                    ref.label,
                    ref.source_system,
                    ref.source_id,
                    ref.href,
                    ref.evidence_hash,
                    ref.data_status,
                    COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) AS sort_at,
                    ref.id AS sort_id
                FROM operation_cycle_references ref
                JOIN operation_cycle_runs r ON r.id = ref.run_id
                JOIN operation_cycle_strategies s ON s.id = r.strategy_id
                WHERE s.tenant_id = :tenant_id
                  AND s.strategy_key = :strategy_key
                  AND ref.source_system IN ('ai_assistant_plan', 'cloud_orchestrator_plan')
                  AND ref.source_id <> ''

                UNION ALL

                SELECT
                    'plan-link:' || md5(link.plan_id) AS reference_key,
                    'other' AS reference_type,
                    LEFT(link.run_key || ' · AI 助手计划', 240) AS label,
                    'cloud_orchestrator_plan' AS source_system,
                    link.plan_id AS source_id,
                    '' AS href,
                    '' AS evidence_hash,
                    'unknown' AS data_status,
                    link.created_at AS sort_at,
                    link.id AS sort_id
                FROM operation_cycle_plan_links link
                WHERE link.tenant_id = :tenant_id
                  AND link.strategy_key = :strategy_key
                  AND link.plan_id <> ''
                  AND NOT EXISTS (
                      SELECT 1
                      FROM operation_cycle_references ref
                      JOIN operation_cycle_runs r ON r.id = ref.run_id
                      JOIN operation_cycle_strategies s ON s.id = r.strategy_id
                      WHERE s.tenant_id = link.tenant_id
                        AND s.strategy_key = link.strategy_key
                        AND ref.source_system IN ('ai_assistant_plan', 'cloud_orchestrator_plan')
                        AND ref.source_id = link.plan_id
                  )
            )
            SELECT
                reference_key,
                reference_type,
                label,
                source_system,
                source_id,
                href,
                evidence_hash,
                data_status
            FROM assistant_plan_references
            ORDER BY sort_at DESC NULLS LAST, sort_id DESC
            """,
            {"tenant_id": self._tenant_id, "strategy_key": _text(strategy_key)},
        )
        return StrategyDetailView(
            strategy=_strategy_summary_from_row(row),
            versions=[
                StrategyVersionView(
                    version=int(item.get("version") or 1),
                    label=_text(item.get("label")),
                    objective=_text(item.get("objective")),
                    definition=_json_value(item.get("definition_json"), {}),
                    effective_from=item.get("effective_from"),
                    created_at=item.get("created_at"),
                )
                for item in versions
            ],
            trend=trend,
            sources=list(latest_detail.references) if latest_detail else [],
            documents=latest_detail.documents if latest_detail else OperationCycleDocumentsSnapshot(),
            assistant_plans=_assistant_plan_references([_reference_from_row(item) for item in assistant_rows]),
        )

    def list_run_summaries(self, strategy_key: str, *, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        rows = self._all(
            _RUN_SUMMARY_SQL
            + " AND s.strategy_key = :strategy_key "
            + "ORDER BY COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) DESC, r.id DESC "
            + "LIMIT :limit OFFSET :offset",
            {
                "tenant_id": self._tenant_id,
                "strategy_key": _text(strategy_key),
                "limit": int(limit),
                "offset": int(offset),
            },
        )
        return [_run_summary_from_row(row) for row in rows]

    def get_run_detail(self, run_key: str) -> RunDetailView | None:
        row = self._one(
            _RUN_SUMMARY_SQL
            + " AND r.run_key = :run_key "
            + "ORDER BY snap.received_at DESC NULLS LAST LIMIT 1",
            {"tenant_id": self._tenant_id, "run_key": _text(run_key)},
        )
        if row is None or not row.get("payload_json"):
            return None
        snapshot = OperationCycleSnapshotV1.model_validate(_json_value(row.get("payload_json"), {}))
        return RunDetailView(
            run=_run_summary_from_row(row),
            attempts=snapshot.attempts,
            stages=snapshot.stages,
            metrics=snapshot.metrics,
            retrospective=snapshot.retrospective,
            next_iteration=snapshot.next_iteration,
            references=snapshot.references,
            documents=snapshot.documents,
            snapshot=SnapshotInfo(
                report_id=_text(row.get("report_id")),
                snapshot_revision=int(row.get("snapshot_revision") or 0),
                snapshot_hash=_text(row.get("payload_hash")),
                schema_version=_text(row.get("schema_version")),
                reporter_id=_text(row.get("reporter_id")),
                client_id=_text(row.get("client_id")),
                received_at=row.get("received_at"),
            ),
        )


def _run_summary_from_row(row: dict[str, Any]) -> RunSummary:
    return RunSummary(
        run_key=_text(row.get("run_key")),
        strategy_key=_text(row.get("strategy_key")),
        label=_text(row.get("label")),
        objective=_text(row.get("objective")),
        plan_version=_text(row.get("plan_version")),
        plan_status=_text(row.get("plan_status")),
        plan_source=_text(row.get("plan_source")),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        intended_send_at=row.get("intended_send_at"),
        plan_scheduled_for=row.get("plan_scheduled_for"),
        first_sent_at=row.get("first_sent_at"),
        last_sent_at=row.get("last_sent_at"),
        execution_stage=cast(Any, _text(row.get("execution_stage")) or "scheduled"),
        review_status=cast(Any, _text(row.get("review_status")) or "not_created"),
        delivery_status=cast(Any, _text(row.get("delivery_status")) or "not_started"),
        data_status=cast(Any, _text(row.get("data_status")) or "unavailable"),
        optimization_status=cast(Any, _text(row.get("optimization_status")) or "none"),
        artifact_status=cast(Any, _text(row.get("artifact_status")) or "source_missing"),
        funnel=_json_value(row.get("funnel_json"), {}),
        conclusion=_text(_json_value(row.get("retrospective_json"), {}).get("conclusion")),
        snapshot_revision=int(row.get("latest_snapshot_revision") or row.get("snapshot_revision") or 0),
        received_at=row.get("received_at"),
        fact_conflict=bool(row.get("fact_conflict")),
    )


def _strategy_summary_from_row(row: dict[str, Any]) -> StrategySummary:
    return StrategySummary(
        strategy_key=_text(row.get("strategy_key")),
        title=_text(row.get("title")),
        description=_text(row.get("description")),
        cadence=_text(row.get("cadence")),
        timezone=_text(row.get("timezone")) or "Asia/Shanghai",
        status=_text(row.get("strategy_status")) or "active",
        current_version=int(row.get("current_version") or 1),
        run_count=int(row.get("run_count") or 0),
        latest_run_key=_text(row.get("run_key")),
        latest_run_label=_text(row.get("label")),
        latest_run_at=row.get("latest_run_at"),
        execution_stage=cast(Any, _text(row.get("execution_stage")) or "scheduled"),
        review_status=cast(Any, _text(row.get("review_status")) or "not_created"),
        delivery_status=cast(Any, _text(row.get("delivery_status")) or "not_started"),
        data_status=cast(Any, _text(row.get("data_status")) or "unavailable"),
        optimization_status=cast(Any, _text(row.get("optimization_status")) or "none"),
        artifact_status=cast(Any, _text(row.get("artifact_status")) or "source_missing"),
        funnel=_json_value(row.get("funnel_json"), {}),
        conclusion=_text(_json_value(row.get("retrospective_json"), {}).get("conclusion")),
        next_iteration_summary=_text(_json_value(row.get("next_iteration_json"), {}).get("summary")),
    )


def build_operation_cycle_repository() -> OperationCycleRepository:
    if not _text(raw_database_url()):
        raise RuntimeError("DATABASE_URL is required for operation-cycle persistence")
    return PostgresOperationCycleRepository()


_EXISTING_SNAPSHOT_SQL = """
SELECT snap.*, r.run_key, s.strategy_key
FROM operation_cycle_snapshots snap
JOIN operation_cycle_runs r ON r.id = snap.run_id
JOIN operation_cycle_strategies s ON s.id = r.strategy_id
WHERE snap.tenant_id = :tenant_id
"""


_RUN_SUMMARY_SQL = """
SELECT
    r.*,
    s.strategy_key,
    snap.report_id,
    snap.snapshot_revision,
    snap.schema_version,
    snap.payload_hash,
    snap.payload_json,
    snap.reporter_id,
    snap.client_id,
    snap.received_at
FROM operation_cycle_runs r
JOIN operation_cycle_strategies s ON s.id = r.strategy_id
LEFT JOIN operation_cycle_snapshots snap ON snap.id = r.latest_snapshot_id
WHERE r.tenant_id = :tenant_id
"""


_STRATEGY_SUMMARY_SQL = """
SELECT
    s.*,
    s.status AS strategy_status,
    COALESCE(run_count.total, 0)::int AS run_count,
    latest.run_key,
    latest.label,
    latest.execution_stage,
    latest.review_status,
    latest.delivery_status,
    latest.data_status,
    latest.optimization_status,
    latest.artifact_status,
    latest.funnel_json,
    latest.retrospective_json,
    latest.next_iteration_json,
    latest.latest_run_at
FROM operation_cycle_strategies s
LEFT JOIN LATERAL (
    SELECT COUNT(*)::int AS total
    FROM operation_cycle_runs counted
    WHERE counted.strategy_id = s.id
) run_count ON TRUE
LEFT JOIN LATERAL (
    SELECT
        r.run_key,
        r.label,
        r.execution_stage,
        r.review_status,
        r.delivery_status,
        r.data_status,
        r.optimization_status,
        r.artifact_status,
        r.funnel_json,
        r.retrospective_json,
        r.next_iteration_json,
        COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) AS latest_run_at
    FROM operation_cycle_runs r
    WHERE r.strategy_id = s.id
    ORDER BY COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) DESC, r.id DESC
    LIMIT 1
) latest ON TRUE
WHERE s.tenant_id = :tenant_id
"""
