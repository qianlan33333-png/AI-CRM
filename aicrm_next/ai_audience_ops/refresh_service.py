from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events import InternalEventService

from .constants import AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT
from .diff_service import identity_key, member_event_idempotency_key, normalize_audience_row
from .event_types import MEMBER_EVENT_PREFIX, RUN_REFRESHED_EVENT
from .repository import (
    AudienceRepository,
    build_audience_repository,
    default_refresh_started_at,
    previous_watermark,
    _text,
)
from .sql_executor import build_execution_plan


AI_AUDIENCE_REFRESH_QUERY_TIMEOUT_SECONDS = 120


class AudienceRefreshService:
    def __init__(
        self,
        repository: AudienceRepository | None = None,
        internal_events: InternalEventService | None = None,
    ):
        self._repo = repository or build_audience_repository()
        self._events = internal_events or InternalEventService()

    def preview_sql(self, sql: str, params: dict[str, Any] | None = None, *, limit: int = 20) -> dict[str, Any]:
        plan = build_execution_plan(sql, params or {}, limit=limit)
        if not plan.validation.ok:
            return {"ok": False, "validation": plan.validation.to_dict(), "dependencies": plan.dependencies, "sample_rows": [], "explain": {}}
        try:
            sample_rows = self._repo.execute_readonly_query(plan.sql, plan.params, limit=plan.limit, timeout_seconds=10)
            explain = self._repo.explain_readonly_query(plan.sql, plan.params, timeout_seconds=10)
        except Exception as exc:
            return {
                "ok": False,
                "validation": plan.validation.to_dict(),
                "dependencies": plan.dependencies,
                "sample_rows": [],
                "explain": {},
                "error": str(exc),
            }
        return {
            "ok": True,
            "validation": plan.validation.to_dict(),
            "dependencies": plan.dependencies,
            "sample_rows": sample_rows,
            "explain": explain,
        }

    def run_due(self, refresh_kind: str, *, limit: int = 20) -> dict[str, Any]:
        kind = "daily" if refresh_kind == "daily" else "incremental"
        packages = self._repo.acquire_due_packages(kind, limit=limit)
        items: list[dict[str, Any]] = []
        counts = Counter()
        for package in packages:
            result = self.refresh_package(int(package["id"]), run_type=kind, package=package)
            items.append(result)
            counts["processed_count"] += 1
            if result.get("ok"):
                counts["succeeded_count"] += 1
            else:
                counts["failed_count"] += 1
            counts["member_event_count"] += int(result.get("member_event_count") or 0)
        return {
            "ok": True,
            "refresh_kind": kind,
            "candidate_count": len(packages),
            "processed_count": counts["processed_count"],
            "succeeded_count": counts["succeeded_count"],
            "failed_count": counts["failed_count"],
            "member_event_count": counts["member_event_count"],
            "items": items,
            "real_external_call_executed": False,
        }

    def refresh_package(
        self,
        package_id: int,
        *,
        run_type: str = "incremental",
        package: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        row_limit: int = AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
        emit_completion_event: bool = True,
    ) -> dict[str, Any]:
        package = package or self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        version = self._repo.get_current_version(int(package["id"]))
        if not version:
            return {"ok": False, "error": "current_version_not_found"}
        refresh_kind = "daily" if run_type in {"daily", "snapshot", "snapshot_current"} else "incremental"
        sql_text = _text(version.get("snapshot_sql_text")) if refresh_kind == "daily" else _text(version.get("incremental_sql_text"))
        if not sql_text:
            return {"ok": False, "error": f"{refresh_kind}_sql_not_configured"}

        started_at = default_refresh_started_at()
        watermark = previous_watermark(package, refresh_kind, started_at=started_at)
        system_params = {
            **dict(version.get("parameters_json") or {}),
            "package_key": _text(package.get("package_key")),
            "last_watermark_at": watermark,
            "refresh_started_at": started_at,
            "lookback_seconds": int(package.get("lookback_seconds") or 600),
            "package_id": int(package["id"]),
            **dict(params or {}),
        }
        plan = build_execution_plan(sql_text, system_params, limit=row_limit)
        if not plan.validation.ok:
            self._repo.update_refresh_failure(int(package["id"]), refresh_kind=refresh_kind, reason=";".join(plan.validation.errors))
            return {"ok": False, "error": "sql_validation_failed", "validation": plan.validation.to_dict()}

        run = self._repo.create_run(int(package["id"]), int(version["id"]), run_type=refresh_kind, started_at=started_at, last_watermark_at=watermark)
        try:
            raw_rows = self._repo.execute_readonly_query(
                plan.sql,
                plan.params,
                limit=plan.limit,
                timeout_seconds=AI_AUDIENCE_REFRESH_QUERY_TIMEOUT_SECONDS,
            )
            normalized_rows = [normalize_audience_row(row) for row in raw_rows]
            diff = self._apply_diff(package, run, normalized_rows, refresh_kind=refresh_kind, occurred_at=started_at)
            completed = self._repo.complete_run(
                int(run["id"]),
                status="succeeded",
                returned_count=len(raw_rows),
                entered_count=diff["entered_count"],
                updated_count=diff["updated_count"],
                exited_count=diff["exited_count"],
                member_event_count=diff["member_event_count"],
                next_watermark_at=started_at,
            )
            self._repo.update_refresh_success(
                int(package["id"]),
                refresh_kind=refresh_kind,
                started_at=started_at,
                interval_seconds=int(package.get("incremental_interval_seconds") or 180),
                daily_refresh_time=_text(package.get("daily_refresh_time")) or "02:00",
                timezone_name=_text(package.get("timezone")) or "Asia/Shanghai",
            )
            run_event = (
                self._emit_run_refreshed_event(
                    package,
                    completed or run,
                    refresh_kind=refresh_kind,
                    returned_count=len(raw_rows),
                    diff=diff,
                    occurred_at=started_at,
                )
                if emit_completion_event
                else None
            )
            return {
                "ok": True,
                "package_id": int(package["id"]),
                "run": completed or run,
                "run_event": run_event,
                "returned_count": len(raw_rows),
                **diff,
                "real_external_call_executed": False,
            }
        except Exception as exc:
            self._repo.complete_run(int(run["id"]), status="failed", error_message=str(exc))
            self._repo.update_refresh_failure(int(package["id"]), refresh_kind=refresh_kind, reason=str(exc))
            return {"ok": False, "package_id": int(package["id"]), "run": run, "error": str(exc), "real_external_call_executed": False}

    def _apply_diff(
        self,
        package: dict[str, Any],
        run: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        refresh_kind: str,
        occurred_at: datetime,
    ) -> dict[str, int]:
        package_id = int(package["id"])
        current_rows = self._repo.list_current_members(package_id)
        current_by_identity = {identity_key(item): item for item in current_rows}
        seen_keys: set[tuple[str, str]] = set()
        counts = Counter()
        for normalized in rows:
            unionid = _text(normalized.get("unionid")) or self._repo.resolve_member_unionid(normalized)
            if not unionid:
                self._repo.enqueue_identity_resolution(normalized, reason="missing_unionid")
                continue
            normalized["unionid"] = unionid
            normalized["identity_type"] = "unionid"
            normalized["identity_value"] = unionid
            key = identity_key(normalized)
            seen_keys.add(key)
            previous = current_by_identity.get(key)
            previous_status = _text((previous or {}).get("status"))
            previous_hash = _text((previous or {}).get("payload_hash"))
            member = self._repo.upsert_active_member(package_id, normalized, occurred_at=normalized.get("event_at") or occurred_at)
            if previous is None or previous_status == "exited":
                self._record_member_event(package, run, member, normalized, "entered", occurred_at)
                counts["entered_count"] += 1
                counts["member_event_count"] += 1
            elif previous_hash and previous_hash != _text(normalized.get("payload_hash")):
                self._record_member_event(package, run, member, normalized, "updated", occurred_at)
                counts["updated_count"] += 1
                counts["member_event_count"] += 1

        if refresh_kind == "daily":
            for previous in current_rows:
                key = identity_key(previous)
                if key in seen_keys or _text(previous.get("status")) != "active":
                    continue
                member = self._repo.mark_member_exited(int(previous["id"]), occurred_at=occurred_at) or previous
                normalized = {
                    "identity_type": previous.get("identity_type"),
                    "identity_value": previous.get("identity_value"),
                    "event_source_key": previous.get("event_source_key") or f"{previous.get('identity_type')}:{previous.get('identity_value')}",
                    "payload_hash": previous.get("payload_hash"),
                    "payload_json": previous.get("payload_json") or {},
                    "unionid": previous.get("unionid"),
                    "mobile_hash": previous.get("mobile_hash"),
                    "owner_userid": previous.get("owner_userid"),
                }
                self._record_member_event(package, run, member, normalized, "exited", occurred_at)
                counts["exited_count"] += 1
                counts["member_event_count"] += 1
        return {
            "entered_count": counts["entered_count"],
            "updated_count": counts["updated_count"],
            "exited_count": counts["exited_count"],
            "member_event_count": counts["member_event_count"],
        }

    def _record_member_event(
        self,
        package: dict[str, Any],
        run: dict[str, Any],
        member: dict[str, Any],
        normalized: dict[str, Any],
        event_type: str,
        occurred_at: datetime,
    ) -> dict[str, Any] | None:
        event = self._repo.insert_member_event(
            {
                "package_id": int(package["id"]),
                "run_id": int(run["id"]),
                "member_current_id": int(member["id"]),
                "event_type": event_type,
                "identity_type": normalized.get("identity_type"),
                "identity_value": normalized.get("identity_value"),
                "unionid": normalized.get("unionid"),
                "mobile_hash": normalized.get("mobile_hash"),
                "owner_userid": normalized.get("owner_userid"),
                "event_source_key": normalized.get("event_source_key"),
                "payload_hash": normalized.get("payload_hash"),
                "payload_json": normalized.get("payload_json"),
                "idempotency_key": member_event_idempotency_key(
                    package_id=int(package["id"]),
                    event_type=event_type,
                    normalized=normalized,
                    run_id=int(run["id"]),
                ),
                "occurred_at": occurred_at,
            }
        )
        if not event:
            return None
        emitted = self._events.emit_event(
            event_type=f"{MEMBER_EVENT_PREFIX}{event_type}",
            aggregate_type="ai_audience_member_event",
            aggregate_id=str(event["id"]),
            subject_type="ai_audience_package",
            subject_id=str(package["id"]),
            idempotency_key=f"internal:{event['idempotency_key']}",
            source_module="ai_audience_ops.refresh_service",
            payload={
                "member_event_id": int(event["id"]),
                "event_type": event_type,
                "package_id": int(package["id"]),
                "package_key": package.get("package_key"),
                "package_name": package.get("name"),
                "member": {
                    "identity_type": event.get("identity_type"),
                    "identity_value": event.get("identity_value"),
                    "unionid": event.get("unionid"),
                    "mobile_hash": event.get("mobile_hash"),
                    "owner_userid": event.get("owner_userid"),
                },
                "payload": event.get("payload_json") or {},
                "idempotency_key": event.get("idempotency_key"),
            },
            payload_summary={
                "member_event_id": int(event["id"]),
                "event_type": event_type,
                "package_key": package.get("package_key"),
                "identity_type": event.get("identity_type"),
            },
            context=CommandContext(actor_id="ai_audience_refresh", actor_type="system", source_route="ai_audience.refresh"),
        )
        event_id = _text((emitted.get("event") or {}).get("event_id"))
        if event_id:
            return self._repo.update_member_event_internal_event_id(int(event["id"]), event_id)
        return event

    def _emit_run_refreshed_event(
        self,
        package: dict[str, Any],
        run: dict[str, Any],
        *,
        refresh_kind: str,
        returned_count: int,
        diff: dict[str, int],
        occurred_at: datetime,
    ) -> dict[str, Any]:
        run_id = int(run.get("id") or 0)
        payload = {
            "run_id": run_id,
            "run_type": refresh_kind,
            "package_id": int(package["id"]),
            "package_key": package.get("package_key"),
            "package_name": package.get("name"),
            "returned_count": int(returned_count or 0),
            "entered_count": int(diff.get("entered_count") or 0),
            "updated_count": int(diff.get("updated_count") or 0),
            "exited_count": int(diff.get("exited_count") or 0),
            "member_event_count": int(diff.get("member_event_count") or 0),
        }
        return self._events.emit_event(
            event_type=RUN_REFRESHED_EVENT,
            aggregate_type="ai_audience_package_run",
            aggregate_id=str(run_id),
            subject_type="ai_audience_package",
            subject_id=str(package["id"]),
            idempotency_key=f"ai_audience.run.refreshed:{run_id}",
            source_module="ai_audience_ops.refresh_service",
            payload=payload,
            payload_summary={
                "run_id": run_id,
                "run_type": refresh_kind,
                "package_key": package.get("package_key"),
                "entered_count": payload["entered_count"],
                "updated_count": payload["updated_count"],
                "exited_count": payload["exited_count"],
            },
            context=CommandContext(actor_id="ai_audience_refresh", actor_type="system", source_route="ai_audience.refresh"),
            occurred_at=occurred_at,
        )
