from __future__ import annotations

import json
import time
from typing import Any, Protocol

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.query_telemetry import current_query_count


class CampaignPreparationRepositoryError(Exception):
    pass


class CampaignPreparationConflict(CampaignPreparationRepositoryError):
    pass


class CampaignPreparationRepository(Protocol):
    def get(self, preparation_id: str) -> dict[str, Any] | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None: ...

    def create(self, preparation: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]: ...

    def cleanup_expired_staging(self) -> None: ...


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _public_detail(header: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "preparation_id": str(header.get("preparation_id") or ""),
        "preparation_hash": str(header.get("preparation_hash") or ""),
        "strategy_key": str(header.get("strategy_key") or ""),
        "strategy_version": int(header.get("strategy_version") or 0),
        "context_hash": str(header.get("context_hash") or ""),
        "status": str(header.get("status") or ""),
        "scheduled_for": header.get("scheduled_for"),
        "timezone": str(header.get("timezone") or ""),
        "input_count": int(header.get("input_count") or 0),
        "eligible_count": int(header.get("eligible_count") or 0),
        "skipped_count": int(header.get("skipped_count") or 0),
        "counts": _json(header.get("counts_json"), {}),
        "blockers": _json(header.get("blockers_json"), []),
        "timings_ms": _json(header.get("timings_json"), {}),
        "sql_batch_count": int(header.get("sql_batch_count") or 0),
        "plan_id": str(header.get("plan_id") or ""),
        "expires_at": header.get("expires_at"),
        "rows": [
            {
                "row_key": str(row.get("row_key") or ""),
                "identity_status": str(row.get("identity_status") or ""),
                "policy_status": str(row.get("policy_status") or ""),
                "row_status": str(row.get("row_status") or ""),
                "reason_code": str(row.get("reason_code") or ""),
            }
            for row in rows
        ],
    }


class PostgresCampaignPreparationRepository:
    def get(self, preparation_id: str) -> dict[str, Any] | None:
        Session = get_session_factory()
        with Session() as session:
            header = session.execute(
                text("SELECT * FROM external_campaign_preparations WHERE preparation_id = :preparation_id"),
                {"preparation_id": preparation_id},
            ).mappings().first()
            if not header:
                return None
            rows = session.execute(
                text(
                    """
                    SELECT row_key, identity_status, policy_status, row_status, reason_code
                    FROM external_campaign_preparation_recipients
                    WHERE preparation_id = :preparation_id
                    ORDER BY id
                    """
                ),
                {"preparation_id": preparation_id},
            ).mappings().all()
        return _public_detail(dict(header), [dict(row) for row in rows])

    def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        Session = get_session_factory()
        with Session() as session:
            row = session.execute(
                text(
                    """
                    SELECT preparation_id
                    FROM external_campaign_preparations
                    WHERE tenant_id = 'aicrm' AND idempotency_key = :idempotency_key
                    """
                ),
                {"idempotency_key": idempotency_key},
            ).mappings().first()
        return self.get(str(row.get("preparation_id") or "")) if row else None

    def create(self, preparation: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        persist_started = time.perf_counter()
        Session = get_session_factory()
        rows_json = json.dumps(rows, ensure_ascii=False, default=str)
        created_new = False
        with Session.begin() as session:
            inserted = session.execute(
                text(
                    """
                    INSERT INTO external_campaign_preparations (
                        preparation_id, tenant_id, idempotency_key, preparation_hash,
                        source_hash, strategy_key, strategy_version, context_hash, run_key,
                        owner_userid, scheduled_for, timezone, display_name, status,
                        input_count, eligible_count, skipped_count, counts_json, blockers_json,
                        timings_json, sql_batch_count, created_by, expires_at
                    ) VALUES (
                        :preparation_id, 'aicrm', :idempotency_key, :preparation_hash,
                        :source_hash, :strategy_key, :strategy_version, :context_hash, :run_key,
                        :owner_userid, :scheduled_for, :timezone, :display_name, :status,
                        :input_count, :eligible_count, :skipped_count,
                        CAST(:counts_json AS jsonb), CAST(:blockers_json AS jsonb),
                        CAST(:timings_json AS jsonb), :sql_batch_count, :created_by, :expires_at
                    )
                    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                    RETURNING preparation_id
                    """
                ),
                {
                    **preparation,
                    "counts_json": json.dumps(preparation.get("counts") or {}, ensure_ascii=False),
                    "blockers_json": json.dumps(preparation.get("blockers") or [], ensure_ascii=False),
                    "timings_json": json.dumps(preparation.get("timings_ms") or {}, ensure_ascii=False),
                },
            ).mappings().first()
            if not inserted:
                existing = session.execute(
                    text(
                        """
                        SELECT preparation_id, preparation_hash
                        FROM external_campaign_preparations
                        WHERE tenant_id = 'aicrm' AND idempotency_key = :idempotency_key
                        FOR UPDATE
                        """
                    ),
                    {"idempotency_key": preparation["idempotency_key"]},
                ).mappings().first()
                if not existing or str(existing.get("preparation_hash") or "") != preparation["preparation_hash"]:
                    raise CampaignPreparationConflict("idempotency_payload_conflict")
                preparation_id = str(existing.get("preparation_id") or "")
            else:
                created_new = True
                preparation_id = str(inserted.get("preparation_id") or "")
                session.execute(
                    text(
                        """
                        INSERT INTO external_campaign_preparation_recipients (
                            preparation_id, row_key, identity_external_userid,
                            identity_unionid, identity_mobile_normalized,
                            resolved_external_userid, resolved_unionid, resolved_owner_userid,
                            identity_status, policy_status, row_status, reason_code,
                            content_text, dynamic_card_json, analysis_json, row_hash
                        )
                        SELECT :preparation_id, source.row_key, source.identity_external_userid,
                               source.identity_unionid, source.identity_mobile_normalized,
                               source.resolved_external_userid, source.resolved_unionid,
                               source.resolved_owner_userid, source.identity_status,
                               source.policy_status, source.row_status, source.reason_code,
                               source.content_text, source.dynamic_card_json,
                               source.analysis_json, source.row_hash
                        FROM jsonb_to_recordset(CAST(:rows_json AS jsonb)) AS source(
                            row_key text,
                            identity_external_userid text,
                            identity_unionid text,
                            identity_mobile_normalized text,
                            resolved_external_userid text,
                            resolved_unionid text,
                            resolved_owner_userid text,
                            identity_status text,
                            policy_status text,
                            row_status text,
                            reason_code text,
                            content_text text,
                            dynamic_card_json jsonb,
                            analysis_json jsonb,
                            row_hash text
                        )
                        """
                    ),
                    {"preparation_id": preparation_id, "rows_json": rows_json},
                )
                persist_ms = max(0, int((time.perf_counter() - persist_started) * 1000))
                session.execute(
                    text(
                        """
                        UPDATE external_campaign_preparations
                        SET timings_json = timings_json || jsonb_build_object('persist', :persist_ms),
                            sql_batch_count = :sql_batch_count,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE preparation_id = :preparation_id
                        """
                    ),
                    {
                        "preparation_id": preparation_id,
                        "persist_ms": persist_ms,
                        "sql_batch_count": int(preparation.get("sql_batch_count") or 0),
                    },
                )
        detail = self.get(preparation_id)
        if detail is None:
            raise CampaignPreparationRepositoryError("preparation_persist_failed")
        query_count_started = preparation.get("_query_count_started")
        current_count = current_query_count()
        if created_new and query_count_started is not None and current_count is not None:
            actual_batch_count = max(0, int(current_count) - int(query_count_started) + 1)
            with Session.begin() as session:
                session.execute(
                    text(
                        """
                        UPDATE external_campaign_preparations
                        SET sql_batch_count = :sql_batch_count,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE preparation_id = :preparation_id
                        """
                    ),
                    {
                        "preparation_id": preparation_id,
                        "sql_batch_count": actual_batch_count,
                    },
                )
            detail["sql_batch_count"] = actual_batch_count
        return detail

    def cleanup_expired_staging(self) -> None:
        Session = get_session_factory()
        with Session.begin() as session:
            session.execute(
                text(
                    """
                    UPDATE external_campaign_preparations
                    SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                    WHERE status IN ('preparing','ready','blocked') AND expires_at <= CURRENT_TIMESTAMP
                    """
                )
            )
            session.execute(
                text(
                    """
                    DELETE FROM external_campaign_preparation_recipients recipient
                    USING external_campaign_preparations preparation
                    WHERE recipient.preparation_id = preparation.preparation_id
                      AND preparation.status IN ('expired', 'committed')
                      AND preparation.expires_at <= CURRENT_TIMESTAMP
                    """
                )
            )


def build_campaign_preparation_repository() -> CampaignPreparationRepository:
    return PostgresCampaignPreparationRepository()


__all__ = [
    "CampaignPreparationConflict",
    "CampaignPreparationRepository",
    "CampaignPreparationRepositoryError",
    "PostgresCampaignPreparationRepository",
    "build_campaign_preparation_repository",
]
