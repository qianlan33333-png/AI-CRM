from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from aicrm_next.platform.shared.db_session import get_session_factory


MAX_INCREMENTAL_SOURCE_EVENTS = 5_000
MAX_INCREMENTAL_MESSAGE_ROWS = 20_000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_ids(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    normalized: set[int] = set()
    for value in values:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized.add(parsed)
    return sorted(normalized)


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class CustomerReadModelIncrementalScope:
    unionids: tuple[str, ...] = ()
    external_userids: tuple[str, ...] = ()
    event_count: int = 0
    requires_full_refresh: bool = False
    fallback_reason: str = ""

    @classmethod
    def full(cls, reason: str, *, event_count: int = 0) -> CustomerReadModelIncrementalScope:
        return cls(
            event_count=max(0, int(event_count or 0)),
            requires_full_refresh=True,
            fallback_reason=_text(reason) or "incremental_scope_unavailable",
        )


class CustomerReadModelRefreshScopeRepository:
    """Resolve one coalesced generation range to bounded canonical customer keys."""

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def load(
        self,
        *,
        completed_generation: int,
        target_generation: int,
    ) -> CustomerReadModelIncrementalScope:
        completed = max(0, int(completed_generation or 0))
        target = max(0, int(target_generation or 0))
        if target <= completed:
            return CustomerReadModelIncrementalScope.full("incremental_generation_range_invalid")

        with self._session_factory() as session:
            rows = list(
                session.execute(
                    text(
                        """
                        SELECT receipt.generation,
                               receipt.source_event_id,
                               receipt.source_event_type,
                               event.event_id,
                               event.event_type,
                               event.subject_type,
                               event.subject_id,
                               event.payload_json
                        FROM customer_read_model_refresh_source_receipt receipt
                        LEFT JOIN internal_event event
                          ON event.event_id = receipt.source_event_id
                        WHERE receipt.generation > :completed_generation
                          AND receipt.generation <= :target_generation
                        ORDER BY receipt.generation ASC, receipt.id ASC
                        LIMIT :limit
                        """
                    ),
                    {
                        "completed_generation": completed,
                        "target_generation": target,
                        "limit": MAX_INCREMENTAL_SOURCE_EVENTS + 1,
                    },
                ).mappings()
            )
            if not rows:
                return CustomerReadModelIncrementalScope.full("incremental_source_receipts_missing")
            if len(rows) > MAX_INCREMENTAL_SOURCE_EVENTS:
                return CustomerReadModelIncrementalScope.full(
                    "incremental_source_event_limit_exceeded",
                    event_count=len(rows),
                )

            unionids: set[str] = set()
            external_userids: set[str] = set()
            message_row_ids: set[int] = set()
            for raw_row in rows:
                row = dict(raw_row or {})
                source_event_id = _text(row.get("source_event_id"))
                event_id = _text(row.get("event_id"))
                source_event_type = _text(row.get("source_event_type"))
                event_type = _text(row.get("event_type"))
                if not source_event_id or not event_id or source_event_id != event_id:
                    return CustomerReadModelIncrementalScope.full(
                        "incremental_source_event_missing",
                        event_count=len(rows),
                    )
                if not event_type or event_type != source_event_type:
                    return CustomerReadModelIncrementalScope.full(
                        "incremental_source_event_type_mismatch",
                        event_count=len(rows),
                    )
                payload = _payload(row.get("payload_json"))
                scoped_unionid, scoped_external_userid, scoped_message_ids = _event_scope(
                    event_type=event_type,
                    subject_type=_text(row.get("subject_type")),
                    subject_id=_text(row.get("subject_id")),
                    payload=payload,
                )
                if scoped_unionid:
                    unionids.add(scoped_unionid)
                if scoped_external_userid:
                    external_userids.add(scoped_external_userid)
                message_row_ids.update(scoped_message_ids)
                if not scoped_unionid and not scoped_external_userid and not scoped_message_ids:
                    return CustomerReadModelIncrementalScope.full(
                        "incremental_customer_identity_unavailable",
                        event_count=len(rows),
                    )

            if len(message_row_ids) > MAX_INCREMENTAL_MESSAGE_ROWS:
                return CustomerReadModelIncrementalScope.full(
                    "incremental_message_row_limit_exceeded",
                    event_count=len(rows),
                )
            if message_row_ids:
                message_scope = self._message_unionids(session, sorted(message_row_ids))
                if message_scope is None:
                    return CustomerReadModelIncrementalScope.full(
                        "incremental_message_scope_incomplete",
                        event_count=len(rows),
                    )
                unionids.update(message_scope)

        if not unionids and not external_userids:
            return CustomerReadModelIncrementalScope.full(
                "incremental_customer_scope_empty",
                event_count=len(rows),
            )
        return CustomerReadModelIncrementalScope(
            unionids=tuple(sorted(unionids)),
            external_userids=tuple(sorted(external_userids)),
            event_count=len(rows),
        )

    @staticmethod
    def _message_unionids(session: Session, message_row_ids: list[int]) -> set[str] | None:
        if not message_row_ids:
            return set()
        if str(session.get_bind().dialect.name) == "postgresql":
            statement = text(
                """
                SELECT id, unionid
                FROM archived_messages
                WHERE id = ANY(CAST(:message_row_ids AS BIGINT[]))
                """
            )
        else:
            statement = text(
                """
                SELECT id, unionid
                FROM archived_messages
                WHERE id IN :message_row_ids
                """
            ).bindparams(bindparam("message_row_ids", expanding=True))
        rows = list(session.execute(statement, {"message_row_ids": message_row_ids}).mappings())
        found_ids = {int(row.get("id") or 0) for row in rows}
        unionids = {_text(row.get("unionid")) for row in rows if _text(row.get("unionid"))}
        if found_ids != set(message_row_ids) or not unionids:
            return None
        return unionids


def _event_scope(
    *,
    event_type: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any],
) -> tuple[str, str, list[int]]:
    unionid = ""
    external_userid = ""
    message_row_ids: list[int] = []

    if event_type == "channel_entry.entered":
        unionid = _text(payload.get("unionid"))
        external_userid = _text(payload.get("external_userid"))
    elif event_type == "customer.phone_bound":
        binding = dict(payload.get("binding") or {})
        unionid = _text(binding.get("unionid"))
        external_userid = _text(binding.get("external_userid"))
    elif event_type == "identity.resolved":
        unionid = _text(payload.get("unionid"))
    elif event_type == "questionnaire.submitted":
        submission = dict(payload.get("submission") or {})
        unionid = _text(submission.get("unionid"))
        external_userid = _text(submission.get("external_userid"))
    elif event_type == "payment.succeeded":
        order = dict(payload.get("order") or {})
        unionid = _text(order.get("unionid"))
        external_userid = _text(order.get("external_userid"))
    elif event_type == "message_archive.batch_ingested":
        message_row_ids = _positive_ids(payload.get("message_row_ids"))
        inserted_count = _non_negative_int(payload.get("inserted_count"))
        if inserted_count is None or len(message_row_ids) != inserted_count:
            return "", "", []
    else:
        return "", "", []

    if not unionid and subject_type == "unionid":
        unionid = subject_id
    return unionid, "" if unionid else external_userid, message_row_ids


__all__ = [
    "CustomerReadModelIncrementalScope",
    "CustomerReadModelRefreshScopeRepository",
]
