from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.runtime import raw_database_url

from .domain import OperationCycleConflictError, compute_snapshot_hash
from .dto import OperationCycleSnapshotV1, RunDetailView, RunSummary, StrategySummary
from .repository import (
    DEFAULT_TENANT_ID,
    InMemoryOperationCycleRepository,
    OperationCycleRepository,
    PostgresOperationCycleRepository,
    _run_summary_from_row,
)
from .strategy_context_dto import (
    OperationCycleContextIndexItem,
    OperationCycleStrategyDocumentPackV1,
    OperationCycleSystemFactProjectionV1,
    StrategyChangeProposalV1,
    StrategyChangeProposalView,
    StrategyMarkdownDocumentV1,
    StrategyVersionContextView,
)
from .action_dto import OperationCycleSkillV1


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return deepcopy(default)
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return deepcopy(default)


def _json_dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _empty_pack() -> OperationCycleStrategyDocumentPackV1:
    return OperationCycleStrategyDocumentPackV1()


def _pack_complete(pack: OperationCycleStrategyDocumentPackV1) -> bool:
    return all(
        bool(document.markdown.strip())
        for document in (pack.execution_guide, pack.copy_guide, pack.measurement_guide)
    )


class StrategyContextRepository(Protocol):
    def list_context_index(self, *, limit: int, offset: int) -> list[OperationCycleContextIndexItem]: ...

    def get_execution_version(self, strategy_key: str) -> StrategyVersionContextView | None: ...

    def get_strategy_summary(self, strategy_key: str) -> StrategySummary | None: ...

    def list_recent_run_details(self, strategy_key: str, *, limit: int) -> list[RunDetailView]: ...

    def list_history(
        self,
        strategy_key: str,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> list[RunSummary]: ...

    def list_system_facts(
        self, strategy_key: str, *, limit: int
    ) -> list[OperationCycleSystemFactProjectionV1]: ...

    def create_proposal(
        self,
        proposal: StrategyChangeProposalV1,
        *,
        idempotency_key: str,
        submitted_by: str,
        client_id: str,
    ) -> StrategyChangeProposalView: ...

    def list_proposals(
        self,
        strategy_key: str,
        *,
        statuses: tuple[str, ...] = (),
        limit: int,
        offset: int,
    ) -> list[StrategyChangeProposalView]: ...

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        note: str,
        decided_by: str,
    ) -> StrategyChangeProposalView: ...


class InMemoryStrategyContextRepository:
    def __init__(self, operation_repo: OperationCycleRepository | None = None) -> None:
        self._operation_repo = operation_repo or InMemoryOperationCycleRepository()
        self._lock = RLock()
        self._confirmed: dict[str, StrategyVersionContextView] = {}
        self._proposals: dict[str, StrategyChangeProposalView] = {}
        self._idempotency: dict[str, str] = {}

    @property
    def operation_repo(self) -> OperationCycleRepository:
        return self._operation_repo

    def _legacy_execution(self, strategy_key: str) -> StrategyVersionContextView | None:
        detail = self._operation_repo.get_strategy_detail(strategy_key)
        if detail is None:
            return None
        current = detail.strategy.current_version
        version = next((item for item in detail.versions if item.version == current), None)
        return StrategyVersionContextView(
            strategy_key=strategy_key,
            version=current,
            version_label=version.label if version else "",
            objective=version.objective if version else "",
            definition=version.definition if version else {},
            governance_status="legacy_confirmed",
            document_pack=_empty_pack(),
            operation_skill=None,
            confirmed_at=version.created_at if version else None,
        )

    def get_execution_version(self, strategy_key: str) -> StrategyVersionContextView | None:
        with self._lock:
            return deepcopy(self._confirmed.get(_text(strategy_key)) or self._legacy_execution(_text(strategy_key)))

    def get_strategy_summary(self, strategy_key: str) -> StrategySummary | None:
        detail = self._operation_repo.get_strategy_detail(_text(strategy_key))
        if detail is None:
            return None
        execution = self.get_execution_version(strategy_key)
        return detail.strategy.model_copy(update={"current_version": execution.version if execution else detail.strategy.current_version})

    def list_context_index(self, *, limit: int, offset: int) -> list[OperationCycleContextIndexItem]:
        summaries = self._operation_repo.list_strategy_summaries(limit=limit, offset=offset)
        result: list[OperationCycleContextIndexItem] = []
        for summary in summaries:
            execution = self.get_execution_version(summary.strategy_key)
            pending = len(
                [item for item in self._proposals.values() if item.strategy_key == summary.strategy_key and item.status == "pending"]
            )
            result.append(
                OperationCycleContextIndexItem(
                    strategy=summary.model_copy(
                        update={"current_version": execution.version if execution else summary.current_version}
                    ),
                    execution_version=execution.version if execution else summary.current_version,
                    document_pack_complete=bool(execution and _pack_complete(execution.document_pack)),
                    pending_proposal_count=pending,
                )
            )
        return result

    def list_recent_run_details(self, strategy_key: str, *, limit: int) -> list[RunDetailView]:
        summaries = self._operation_repo.list_run_summaries(_text(strategy_key), limit=limit, offset=0)
        return [detail for item in summaries if (detail := self._operation_repo.get_run_detail(item.run_key)) is not None]

    def list_history(
        self,
        strategy_key: str,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> list[RunSummary]:
        items = self._operation_repo.list_run_summaries(_text(strategy_key), limit=100, offset=0)
        for key in ("execution_stage", "review_status", "delivery_status"):
            if _text(filters.get(key)):
                items = [item for item in items if _text(getattr(item, key)) == _text(filters[key])]
        return deepcopy(items[offset : offset + limit])

    def list_system_facts(
        self, strategy_key: str, *, limit: int
    ) -> list[OperationCycleSystemFactProjectionV1]:
        del strategy_key, limit
        return []

    def create_proposal(
        self,
        proposal: StrategyChangeProposalV1,
        *,
        idempotency_key: str,
        submitted_by: str,
        client_id: str,
    ) -> StrategyChangeProposalView:
        key = _text(idempotency_key)
        if not key:
            raise ValueError("idempotency_key_required")
        proposal_hash = compute_snapshot_hash(proposal)
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id:
                existing = self._proposals[existing_id]
                if existing.proposal_hash != proposal_hash:
                    raise OperationCycleConflictError("strategy_proposal_idempotency_mismatch")
                return deepcopy(existing)
            execution = self.get_execution_version(proposal.strategy_key)
            if execution is None:
                raise LookupError("operation_cycle_strategy_not_found")
            if execution.version != proposal.base_strategy_version:
                raise OperationCycleConflictError("strategy_base_version_conflict")
            proposal_id = f"ocprop_{proposal_hash[:24]}"
            view = StrategyChangeProposalView(
                proposal_id=proposal_id,
                proposal_hash=proposal_hash,
                strategy_key=proposal.strategy_key,
                base_strategy_version=proposal.base_strategy_version,
                source_run_key=proposal.source_run_key,
                proposal=proposal,
                submitted_by=_text(submitted_by),
                created_at=_utcnow(),
            )
            self._proposals[proposal_id] = view
            self._idempotency[key] = proposal_id
            return deepcopy(view)

    def list_proposals(
        self,
        strategy_key: str,
        *,
        statuses: tuple[str, ...] = (),
        limit: int,
        offset: int,
    ) -> list[StrategyChangeProposalView]:
        values = [item for item in self._proposals.values() if item.strategy_key == _text(strategy_key)]
        if statuses:
            values = [item for item in values if item.status in statuses]
        values.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return deepcopy(values[offset : offset + limit])

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        note: str,
        decided_by: str,
    ) -> StrategyChangeProposalView:
        with self._lock:
            view = self._proposals.get(_text(proposal_id))
            if view is None:
                raise LookupError("strategy_change_proposal_not_found")
            wanted = "accepted" if decision == "accept" else "rejected"
            if view.status != "pending":
                if view.status == wanted and view.decision_note == _text(note):
                    return deepcopy(view)
                raise OperationCycleConflictError("strategy_proposal_already_decided")
            execution = self.get_execution_version(view.strategy_key)
            if decision == "accept" and (execution is None or execution.version != view.base_strategy_version):
                raise OperationCycleConflictError("strategy_base_version_conflict")
            now = _utcnow()
            applied = None
            if decision == "accept":
                applied = view.base_strategy_version + 1
                target = view.proposal.target_version
                operation_skill = target.operation_skill or (execution.operation_skill if execution else None)
                effective_target = target.model_copy(update={"operation_skill": operation_skill})
                self._confirmed[view.strategy_key] = StrategyVersionContextView(
                    strategy_key=view.strategy_key,
                    version=applied,
                    version_label=target.version_label,
                    objective=target.objective,
                    definition=target.definition,
                    governance_status="confirmed",
                    document_pack=target.document_pack,
                    operation_skill=operation_skill,
                    version_hash=compute_snapshot_hash(effective_target),
                    confirmed_by=_text(decided_by),
                    confirmed_at=now,
                    confirmation_note=_text(note),
                )
            updated = view.model_copy(
                update={
                    "status": wanted,
                    "decided_by": _text(decided_by),
                    "decided_at": now,
                    "decision_note": _text(note),
                    "applied_strategy_version": applied,
                }
            )
            self._proposals[view.proposal_id] = updated
            return deepcopy(updated)


class PostgresStrategyContextRepository:
    def __init__(self, session_factory=None, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._tenant_id = _text(tenant_id) or DEFAULT_TENANT_ID
        self._operation_repo = PostgresOperationCycleRepository(self._session_factory, tenant_id=self._tenant_id)

    def _all(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params).mappings().all()
        return [dict(row) for row in rows]

    def _one(self, statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params).mappings().fetchone()
        return dict(row) if row else None

    @staticmethod
    def _document(key: str, row: dict[str, Any]) -> StrategyMarkdownDocumentV1:
        return StrategyMarkdownDocumentV1(
            markdown=_text(row.get(f"{key}_markdown")),
            sha256=_text(row.get(f"{key}_sha256")),
            generated_at=row.get(f"{key}_generated_at"),
            source=_text(row.get(f"{key}_source")),
        )

    def _execution_from_row(self, row: dict[str, Any]) -> StrategyVersionContextView:
        pack = OperationCycleStrategyDocumentPackV1(
            execution_guide=self._document("execution_guide", row),
            copy_guide=self._document("copy_guide", row),
            measurement_guide=self._document("measurement_guide", row),
            execution_contract=_json(row.get("execution_contract_json"), {}),
        )
        raw_skill = _json(row.get("operation_skill_json"), {})
        operation_skill = OperationCycleSkillV1.model_validate(raw_skill) if raw_skill else None
        return StrategyVersionContextView(
            strategy_key=_text(row.get("strategy_key")),
            version=int(row.get("version") or 1),
            version_label=_text(row.get("label")),
            objective=_text(row.get("objective")),
            definition=_json(row.get("definition_json"), {}),
            governance_status=_text(row.get("governance_status")) or "legacy_confirmed",
            document_pack=pack,
            operation_skill=operation_skill,
            version_hash=_text(row.get("version_hash")),
            confirmed_by=_text(row.get("confirmed_by")),
            confirmed_at=row.get("confirmed_at"),
            confirmation_note=_text(row.get("confirmation_note")),
        )

    def get_execution_version(self, strategy_key: str) -> StrategyVersionContextView | None:
        row = self._one(_EXECUTION_VERSION_SQL, {"tenant_id": self._tenant_id, "strategy_key": _text(strategy_key)})
        return self._execution_from_row(row) if row else None

    def get_strategy_summary(self, strategy_key: str) -> StrategySummary | None:
        detail = self._operation_repo.get_strategy_detail(_text(strategy_key))
        return detail.strategy if detail else None

    def list_context_index(self, *, limit: int, offset: int) -> list[OperationCycleContextIndexItem]:
        summaries = self._operation_repo.list_strategy_summaries(limit=limit, offset=offset)
        keys = [item.strategy_key for item in summaries]
        if not keys:
            return []
        rows = self._all(
            _EXECUTION_VERSION_SQL
            + " AND s.strategy_key = ANY(:strategy_keys) ORDER BY s.strategy_key",
            {"tenant_id": self._tenant_id, "strategy_key": "", "strategy_keys": keys},
        )
        by_key = {_text(row.get("strategy_key")): self._execution_from_row(row) for row in rows}
        pending_rows = self._all(
            """
            SELECT s.strategy_key, COUNT(*)::int AS pending_count
            FROM operation_cycle_strategy_change_proposals p
            JOIN operation_cycle_strategies s ON s.id = p.strategy_id
            WHERE p.tenant_id = :tenant_id AND p.status = 'pending'
              AND s.strategy_key = ANY(:strategy_keys)
            GROUP BY s.strategy_key
            """,
            {"tenant_id": self._tenant_id, "strategy_keys": keys},
        )
        pending = {_text(row.get("strategy_key")): int(row.get("pending_count") or 0) for row in pending_rows}
        return [
            OperationCycleContextIndexItem(
                strategy=summary,
                execution_version=(by_key.get(summary.strategy_key).version if by_key.get(summary.strategy_key) else summary.current_version),
                document_pack_complete=bool(by_key.get(summary.strategy_key) and _pack_complete(by_key[summary.strategy_key].document_pack)),
                pending_proposal_count=pending.get(summary.strategy_key, 0),
            )
            for summary in summaries
        ]

    def list_recent_run_details(self, strategy_key: str, *, limit: int) -> list[RunDetailView]:
        rows = self._all(
            """
            SELECT snap.payload_json
            FROM operation_cycle_runs r
            JOIN operation_cycle_strategies s ON s.id = r.strategy_id
            JOIN operation_cycle_snapshots snap ON snap.id = r.latest_snapshot_id
            WHERE r.tenant_id = :tenant_id AND s.strategy_key = :strategy_key
            ORDER BY COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) DESC, r.id DESC
            LIMIT :limit
            """,
            {"tenant_id": self._tenant_id, "strategy_key": _text(strategy_key), "limit": int(limit)},
        )
        result: list[RunDetailView] = []
        for row in rows:
            snapshot = OperationCycleSnapshotV1.model_validate(_json(row.get("payload_json"), {}))
            detail = self._operation_repo.get_run_detail(snapshot.run.run_key)
            if detail is not None:
                result.append(detail)
        return result

    def list_history(
        self,
        strategy_key: str,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> list[RunSummary]:
        clauses = ["r.tenant_id = :tenant_id", "s.strategy_key = :strategy_key"]
        params: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "strategy_key": _text(strategy_key),
            "limit": int(limit),
            "offset": int(offset),
        }
        for key in ("execution_stage", "review_status", "delivery_status"):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"r.{key} = :{key}")
                params[key] = value
        if _text(filters.get("date_from")):
            clauses.append("COALESCE(r.intended_send_at, r.started_at, r.created_at) >= CAST(:date_from AS timestamptz)")
            params["date_from"] = _text(filters["date_from"])
        if _text(filters.get("date_to")):
            clauses.append("COALESCE(r.intended_send_at, r.started_at, r.created_at) <= CAST(:date_to AS timestamptz)")
            params["date_to"] = _text(filters["date_to"])
        if _text(filters.get("metric_window")):
            clauses.append(
                "EXISTS (SELECT 1 FROM operation_cycle_metrics m WHERE m.run_id = r.id AND m.observation_window = :metric_window)"
            )
            params["metric_window"] = _text(filters["metric_window"])
        if _text(filters.get("plan_id")):
            clauses.append(
                "EXISTS (SELECT 1 FROM operation_cycle_references ref WHERE ref.run_id = r.id "
                "AND ref.source_system IN ('ai_assistant_plan','cloud_orchestrator_plan') AND ref.source_id = :plan_id)"
            )
            params["plan_id"] = _text(filters["plan_id"])
        rows = self._all(
            """
            SELECT r.*, s.strategy_key,
                   snap.report_id, snap.snapshot_revision, snap.schema_version,
                   snap.payload_hash, snap.payload_json, snap.reporter_id,
                   snap.client_id, snap.received_at
            FROM operation_cycle_runs r
            JOIN operation_cycle_strategies s ON s.id = r.strategy_id
            LEFT JOIN operation_cycle_snapshots snap ON snap.id = r.latest_snapshot_id
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY COALESCE(r.first_sent_at, r.intended_send_at, r.started_at, r.updated_at) DESC, r.id DESC "
            + "LIMIT :limit OFFSET :offset",
            params,
        )
        return [_run_summary_from_row(row) for row in rows]

    def list_system_facts(
        self, strategy_key: str, *, limit: int
    ) -> list[OperationCycleSystemFactProjectionV1]:
        rows = self._all(
            """
            SELECT plan_id, run_key, approved_at, task_count, finalized_count,
                   sent_count, failed_count, last_delivery_at
            FROM operation_cycle_plan_links
            WHERE tenant_id = :tenant_id AND strategy_key = :strategy_key
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {
                "tenant_id": self._tenant_id,
                "strategy_key": _text(strategy_key),
                "limit": int(limit),
            },
        )
        return [OperationCycleSystemFactProjectionV1.model_validate(row) for row in rows]

    def _proposal_from_row(self, row: dict[str, Any]) -> StrategyChangeProposalView:
        proposal = StrategyChangeProposalV1.model_validate(_json(row.get("proposal_json"), {}))
        return StrategyChangeProposalView(
            proposal_id=_text(row.get("proposal_id")),
            proposal_hash=_text(row.get("proposal_hash")),
            strategy_key=_text(row.get("strategy_key")) or proposal.strategy_key,
            base_strategy_version=int(row.get("base_strategy_version") or proposal.base_strategy_version),
            source_run_key=_text(row.get("source_run_key")),
            status=_text(row.get("status")) or "pending",
            proposal=proposal,
            submitted_by=_text(row.get("submitted_by")),
            created_at=row.get("created_at"),
            decided_by=_text(row.get("decided_by")),
            decided_at=row.get("decided_at"),
            decision_note=_text(row.get("decision_note")),
            applied_strategy_version=row.get("applied_strategy_version"),
        )

    def create_proposal(
        self,
        proposal: StrategyChangeProposalV1,
        *,
        idempotency_key: str,
        submitted_by: str,
        client_id: str,
    ) -> StrategyChangeProposalView:
        key = _text(idempotency_key)
        if not key:
            raise ValueError("idempotency_key_required")
        if len(key) > 200:
            raise ValueError("idempotency_key_too_long")
        proposal_hash = compute_snapshot_hash(proposal)
        proposal_id = f"ocprop_{proposal_hash[:24]}"
        with self._session_factory() as session:
            try:
                session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"strategy-proposal:{self._tenant_id}:{key}"})
                existing = session.execute(
                    text(
                        """
                        SELECT p.*, s.strategy_key
                        FROM operation_cycle_strategy_change_proposals p
                        JOIN operation_cycle_strategies s ON s.id = p.strategy_id
                        WHERE p.tenant_id = :tenant_id AND p.idempotency_key = :idempotency_key
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": self._tenant_id, "idempotency_key": key},
                ).mappings().fetchone()
                if existing:
                    row = dict(existing)
                    if _text(row.get("proposal_hash")) != proposal_hash:
                        raise OperationCycleConflictError("strategy_proposal_idempotency_mismatch")
                    session.commit()
                    return self._proposal_from_row(row)
                strategy = session.execute(
                    text(
                        "SELECT * FROM operation_cycle_strategies "
                        "WHERE tenant_id = :tenant_id AND strategy_key = :strategy_key FOR UPDATE"
                    ),
                    {"tenant_id": self._tenant_id, "strategy_key": proposal.strategy_key},
                ).mappings().fetchone()
                if strategy is None:
                    raise LookupError("operation_cycle_strategy_not_found")
                if int(strategy.get("current_version") or 0) != proposal.base_strategy_version:
                    raise OperationCycleConflictError("strategy_base_version_conflict")
                if proposal.source_run_key:
                    run = session.execute(
                        text(
                            "SELECT 1 FROM operation_cycle_runs WHERE tenant_id = :tenant_id "
                            "AND strategy_id = :strategy_id AND run_key = :run_key"
                        ),
                        {
                            "tenant_id": self._tenant_id,
                            "strategy_id": strategy["id"],
                            "run_key": proposal.source_run_key,
                        },
                    ).fetchone()
                    if run is None:
                        raise ValueError("strategy_proposal_source_run_not_found")
                row = session.execute(
                    text(
                        """
                        INSERT INTO operation_cycle_strategy_change_proposals (
                            proposal_id, tenant_id, strategy_id, base_strategy_version,
                            source_run_key, idempotency_key, proposal_hash, proposal_json,
                            status, submitted_by, client_id, created_at
                        ) VALUES (
                            :proposal_id, :tenant_id, :strategy_id, :base_strategy_version,
                            :source_run_key, :idempotency_key, :proposal_hash,
                            CAST(:proposal_json AS jsonb), 'pending', :submitted_by, :client_id,
                            CURRENT_TIMESTAMP
                        ) RETURNING *
                        """
                    ),
                    {
                        "proposal_id": proposal_id,
                        "tenant_id": self._tenant_id,
                        "strategy_id": strategy["id"],
                        "base_strategy_version": proposal.base_strategy_version,
                        "source_run_key": proposal.source_run_key,
                        "idempotency_key": key,
                        "proposal_hash": proposal_hash,
                        "proposal_json": _json_dump(proposal),
                        "submitted_by": _text(submitted_by),
                        "client_id": _text(client_id),
                    },
                ).mappings().one()
                session.commit()
            except Exception:
                session.rollback()
                raise
        return self._proposal_from_row({**dict(row), "strategy_key": proposal.strategy_key})

    def list_proposals(
        self,
        strategy_key: str,
        *,
        statuses: tuple[str, ...] = (),
        limit: int,
        offset: int,
    ) -> list[StrategyChangeProposalView]:
        params: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "strategy_key": _text(strategy_key),
            "limit": int(limit),
            "offset": int(offset),
        }
        status_sql = ""
        if statuses:
            status_sql = " AND p.status = ANY(:statuses)"
            params["statuses"] = list(statuses)
        rows = self._all(
            """
            SELECT p.*, s.strategy_key
            FROM operation_cycle_strategy_change_proposals p
            JOIN operation_cycle_strategies s ON s.id = p.strategy_id
            WHERE p.tenant_id = :tenant_id AND s.strategy_key = :strategy_key
            """
            + status_sql
            + " ORDER BY p.created_at DESC, p.proposal_id DESC LIMIT :limit OFFSET :offset",
            params,
        )
        return [self._proposal_from_row(row) for row in rows]

    @staticmethod
    def _insert_document_pack(session, *, strategy_version_id: int, pack: OperationCycleStrategyDocumentPackV1) -> None:
        session.execute(
            text(
                """
                INSERT INTO operation_cycle_strategy_version_documents (
                    strategy_version_id, schema_version,
                    execution_guide_markdown, execution_guide_sha256,
                    execution_guide_generated_at, execution_guide_source,
                    copy_guide_markdown, copy_guide_sha256,
                    copy_guide_generated_at, copy_guide_source,
                    measurement_guide_markdown, measurement_guide_sha256,
                    measurement_guide_generated_at, measurement_guide_source,
                    execution_contract_json, document_pack_hash, created_at
                ) VALUES (
                    :strategy_version_id, :schema_version,
                    :execution_guide_markdown, :execution_guide_sha256,
                    :execution_guide_generated_at, :execution_guide_source,
                    :copy_guide_markdown, :copy_guide_sha256,
                    :copy_guide_generated_at, :copy_guide_source,
                    :measurement_guide_markdown, :measurement_guide_sha256,
                    :measurement_guide_generated_at, :measurement_guide_source,
                    CAST(:execution_contract_json AS jsonb), :document_pack_hash,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "strategy_version_id": int(strategy_version_id),
                "schema_version": pack.schema_version,
                "execution_guide_markdown": pack.execution_guide.markdown,
                "execution_guide_sha256": pack.execution_guide.sha256,
                "execution_guide_generated_at": pack.execution_guide.generated_at,
                "execution_guide_source": pack.execution_guide.source,
                "copy_guide_markdown": pack.copy_guide.markdown,
                "copy_guide_sha256": pack.copy_guide.sha256,
                "copy_guide_generated_at": pack.copy_guide.generated_at,
                "copy_guide_source": pack.copy_guide.source,
                "measurement_guide_markdown": pack.measurement_guide.markdown,
                "measurement_guide_sha256": pack.measurement_guide.sha256,
                "measurement_guide_generated_at": pack.measurement_guide.generated_at,
                "measurement_guide_source": pack.measurement_guide.source,
                "execution_contract_json": _json_dump(pack.execution_contract),
                "document_pack_hash": compute_snapshot_hash(pack),
            },
        )

    def decide_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        note: str,
        decided_by: str,
    ) -> StrategyChangeProposalView:
        wanted = "accepted" if decision == "accept" else "rejected"
        with self._session_factory() as session:
            try:
                row = session.execute(
                    text(
                        """
                        SELECT p.*, s.strategy_key, s.current_version, s.id AS strategy_id
                        FROM operation_cycle_strategy_change_proposals p
                        JOIN operation_cycle_strategies s ON s.id = p.strategy_id
                        WHERE p.tenant_id = :tenant_id AND p.proposal_id = :proposal_id
                        FOR UPDATE OF p, s
                        """
                    ),
                    {"tenant_id": self._tenant_id, "proposal_id": _text(proposal_id)},
                ).mappings().fetchone()
                if row is None:
                    raise LookupError("strategy_change_proposal_not_found")
                row = dict(row)
                if _text(row.get("status")) != "pending":
                    if _text(row.get("status")) == wanted and _text(row.get("decision_note")) == _text(note):
                        session.commit()
                        return self._proposal_from_row(row)
                    raise OperationCycleConflictError("strategy_proposal_already_decided")
                proposal = StrategyChangeProposalV1.model_validate(_json(row.get("proposal_json"), {}))
                applied_version = None
                if decision == "accept":
                    if int(row.get("current_version") or 0) != proposal.base_strategy_version:
                        raise OperationCycleConflictError("strategy_base_version_conflict")
                    max_version = session.execute(
                        text(
                            "SELECT COALESCE(MAX(version), 0) FROM operation_cycle_strategy_versions "
                            "WHERE strategy_id = :strategy_id"
                        ),
                        {"strategy_id": row["strategy_id"]},
                    ).scalar_one()
                    applied_version = max(int(max_version or 0), proposal.base_strategy_version) + 1
                    target = proposal.target_version
                    previous_skill_row = session.execute(
                        text(
                            "SELECT operation_skill_json FROM operation_cycle_strategy_versions "
                            "WHERE strategy_id = :strategy_id AND version = :version"
                        ),
                        {
                            "strategy_id": row["strategy_id"],
                            "version": proposal.base_strategy_version,
                        },
                    ).mappings().fetchone()
                    previous_skill_json = _json(
                        previous_skill_row.get("operation_skill_json") if previous_skill_row else None,
                        {},
                    )
                    operation_skill = target.operation_skill
                    if operation_skill is None and previous_skill_json:
                        operation_skill = OperationCycleSkillV1.model_validate(previous_skill_json)
                    version_hash = compute_snapshot_hash(
                        target.model_copy(update={"operation_skill": operation_skill})
                    )
                    version_row = session.execute(
                        text(
                            """
                            INSERT INTO operation_cycle_strategy_versions (
                                strategy_id, version, label, objective, definition_json,
                                operation_skill_json, operation_skill_hash,
                                version_hash, effective_from, governance_status,
                                confirmed_by, confirmed_at, confirmation_note, created_at
                            ) VALUES (
                                :strategy_id, :version, :label, :objective,
                                CAST(:definition_json AS jsonb), CAST(:operation_skill_json AS jsonb),
                                :operation_skill_hash, :version_hash,
                                CURRENT_TIMESTAMP, 'confirmed', :confirmed_by,
                                CURRENT_TIMESTAMP, :confirmation_note, CURRENT_TIMESTAMP
                            ) RETURNING id
                            """
                        ),
                        {
                            "strategy_id": row["strategy_id"],
                            "version": applied_version,
                            "label": target.version_label,
                            "objective": target.objective,
                            "definition_json": _json_dump(target.definition),
                            "operation_skill_json": _json_dump(operation_skill) if operation_skill else "{}",
                            "operation_skill_hash": operation_skill.skill_hash if operation_skill else "",
                            "version_hash": version_hash,
                            "confirmed_by": _text(decided_by),
                            "confirmation_note": _text(note),
                        },
                    ).mappings().one()
                    self._insert_document_pack(
                        session,
                        strategy_version_id=int(version_row["id"]),
                        pack=target.document_pack,
                    )
                    session.execute(
                        text(
                            "UPDATE operation_cycle_strategies SET current_version = :version, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = :strategy_id"
                        ),
                        {"version": applied_version, "strategy_id": row["strategy_id"]},
                    )
                updated = session.execute(
                    text(
                        """
                        UPDATE operation_cycle_strategy_change_proposals SET
                            status = :status, decided_by = :decided_by,
                            decided_at = CURRENT_TIMESTAMP, decision_note = :decision_note,
                            applied_strategy_version = :applied_strategy_version
                        WHERE proposal_id = :proposal_id
                        RETURNING *
                        """
                    ),
                    {
                        "status": wanted,
                        "decided_by": _text(decided_by),
                        "decision_note": _text(note),
                        "applied_strategy_version": applied_version,
                        "proposal_id": _text(proposal_id),
                    },
                ).mappings().one()
                session.commit()
            except Exception:
                session.rollback()
                raise
        return self._proposal_from_row({**dict(updated), "strategy_key": proposal.strategy_key})


def build_strategy_context_repository() -> StrategyContextRepository:
    if not _text(raw_database_url()):
        raise RuntimeError("DATABASE_URL is required for operation-cycle strategy context")
    return PostgresStrategyContextRepository()


_EXECUTION_VERSION_SQL = """
SELECT
    s.strategy_key,
    v.version,
    v.label,
    v.objective,
    v.definition_json,
    COALESCE(v.operation_skill_json, '{}'::jsonb) AS operation_skill_json,
    COALESCE(v.operation_skill_hash, '') AS operation_skill_hash,
    v.version_hash,
    v.governance_status,
    v.confirmed_by,
    v.confirmed_at,
    v.confirmation_note,
    docs.execution_guide_markdown,
    docs.execution_guide_sha256,
    docs.execution_guide_generated_at,
    docs.execution_guide_source,
    docs.copy_guide_markdown,
    docs.copy_guide_sha256,
    docs.copy_guide_generated_at,
    docs.copy_guide_source,
    docs.measurement_guide_markdown,
    docs.measurement_guide_sha256,
    docs.measurement_guide_generated_at,
    docs.measurement_guide_source,
    COALESCE(docs.execution_contract_json, '{}'::jsonb) AS execution_contract_json
FROM operation_cycle_strategies s
JOIN operation_cycle_strategy_versions v
  ON v.strategy_id = s.id AND v.version = s.current_version
LEFT JOIN operation_cycle_strategy_version_documents docs
  ON docs.strategy_version_id = v.id
WHERE s.tenant_id = :tenant_id
  AND (:strategy_key = '' OR s.strategy_key = :strategy_key)
"""


__all__ = [
    "InMemoryStrategyContextRepository",
    "PostgresStrategyContextRepository",
    "StrategyContextRepository",
    "build_strategy_context_repository",
]
