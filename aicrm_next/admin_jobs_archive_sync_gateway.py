from __future__ import annotations

import re
from typing import Any

from aicrm_next.message_archive.repo import build_archive_sync_repository
from aicrm_next.message_archive.sync_service import execute_archive_sync as _execute_archive_sync


def record_archive_source_change(
    conn: Any,
    inserted_count: int,
    last_seq: int,
    batch_key: str,
    message_row_ids: list[int],
) -> dict[str, object]:
    """Persist one PII-free source event in the archive write transaction."""

    from aicrm_next.platform_foundation.command_bus.models import CommandContext
    from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
    from aicrm_next.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox

    normalized_batch_key = str(batch_key or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", normalized_batch_key) is None:
        raise ValueError("archive source batch key must be one opaque SHA-256 value")
    normalized_row_ids = sorted({int(row_id) for row_id in message_row_ids if int(row_id) > 0})
    if not normalized_row_ids or len(normalized_row_ids) != int(inserted_count):
        raise ValueError("archive source message row ids must match inserted count")
    return enqueue_transactional_internal_event_outbox(
        conn,
        InternalEventCreateRequest(
            event_type="message_archive.batch_ingested",
            aggregate_type="message_archive_sync_batch",
            aggregate_id=normalized_batch_key,
            payload={
                "inserted_count": int(inserted_count),
                "last_seq": int(last_seq),
                "message_row_ids": normalized_row_ids,
            },
            payload_summary={"inserted_count": int(inserted_count), "last_seq": int(last_seq)},
            context=CommandContext(
                actor_id="archive_sync",
                actor_type="system",
                source_route="aicrm_next.admin_jobs_archive_sync_gateway",
            ),
            idempotency_key=f"message_archive.batch_ingested:{normalized_batch_key}",
            source_module="message_archive.repo",
            source_command_id=f"archive_sync_batch:{normalized_batch_key}",
            execution_id=f"exe_archive_sync_{normalized_batch_key[:32]}",
        ),
    )


def _build_archive_sync_repository():
    return build_archive_sync_repository(source_change_recorder=record_archive_source_change)


def execute_archive_sync(**kwargs: Any) -> dict[str, Any]:
    """Compose every archive entry point with its transactional dirty-event adapter."""

    call_kwargs = dict(kwargs)
    if call_kwargs.get("repo") is None:
        call_kwargs["repo"] = _build_archive_sync_repository()
    return _execute_archive_sync(**call_kwargs)
