from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aicrm_next.platform_foundation.external_effects import ExternalEffectService
from aicrm_next.platform_foundation.internal_events import InternalEventService

from .repository import AudienceRepository, build_audience_repository, default_refresh_started_at, previous_watermark, _text
from .schemas import PackageCreateRequest, PackageVersionCreateRequest, PreviewRequest
from .sql_executor import build_execution_plan


class AudiencePackageService:
    def __init__(
        self,
        repository: AudienceRepository | None = None,
        internal_events: InternalEventService | None = None,
    ):
        self._repo = repository or build_audience_repository()
        self._events = internal_events or InternalEventService()

    def list_packages(self) -> dict[str, Any]:
        return {"ok": True, "packages": self._repo.list_packages()}

    def list_admin_package_summaries(self, *, limit: int = 200) -> dict[str, Any]:
        rows = self._repo.list_package_summaries(limit=limit)
        items = [_admin_package_item(row) for row in rows]
        total = int(rows[0].get("total_count") or len(items)) if rows else 0
        return {
            "ok": True,
            "items": items,
            "total": total,
            "generated_at": _admin_datetime(datetime.now(timezone.utc)),
        }

    def list_admin_packages(self, *, limit: int = 200) -> dict[str, Any]:
        return self.list_admin_package_summaries(limit=limit)

    def get_admin_package_detail(self, package_id: int) -> dict[str, Any]:
        row = self._repo.get_package_detail(int(package_id))
        if not row:
            return {"ok": False, "error": "package_not_found"}
        return {"ok": True, "package": _admin_package_detail(row)}

    def create_admin_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        package_key = _text(payload.get("package_key"))
        name = _text(payload.get("name"))
        if not package_key or not name:
            return {"ok": False, "error": "package_key_and_name_required"}
        if self._repo.get_package_by_key(package_key):
            return {"ok": False, "error": "package_key_exists"}

        refresh_mode = _text(payload.get("refresh_mode")) or "manual"
        refresh_config = refresh_mode_config(refresh_mode)
        if refresh_config is None:
            return {"ok": False, "error": "invalid_refresh_mode"}

        status = _text(payload.get("status")) or "draft"
        if status not in {"draft", "paused"}:
            return {"ok": False, "error": "invalid_initial_status"}

        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
        incremental_sql = _text(payload.get("incremental_sql_text"))
        snapshot_sql = _text(payload.get("snapshot_sql_text"))
        if _text(payload.get("sql_text")):
            if _text(payload.get("query_mode")) == "snapshot_current":
                snapshot_sql = _text(payload.get("sql_text"))
            else:
                incremental_sql = _text(payload.get("sql_text"))

        validation = _validate_sql_payload(
            incremental_sql=incremental_sql,
            snapshot_sql=snapshot_sql,
            parameters=parameters,
        )
        if validation["validation_errors"]:
            return {"ok": False, "error": "sql_validation_failed", **validation}

        package_payload = {
            **dict(payload or {}),
            **refresh_config,
            "package_key": package_key,
            "name": name,
            "status": status,
            "parameters": parameters,
        }
        package = self._repo.create_package(package_payload)
        version = None
        if incremental_sql or snapshot_sql:
            version_payload = {
                **dict(payload or {}),
                "incremental_sql_text": incremental_sql,
                "snapshot_sql_text": snapshot_sql,
                "parameters": parameters,
                "dependencies": validation["dependencies"],
                "validation_errors": [],
            }
            version = self._repo.create_version(int(package["id"]), version_payload)
            self._repo.replace_dependencies(int(package["id"]), int(version["id"]), validation["dependencies"])
        detail = self._repo.get_package_detail(int(package["id"])) or package
        return {
            "ok": True,
            "package": _admin_package_detail(detail),
            "version": _safe_version(version),
            "created": True,
        }

    def update_admin_package(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._repo.get_package(int(package_id))
        if not current:
            return {"ok": False, "error": "package_not_found"}
        refresh_mode = _text(payload.get("refresh_mode"))
        refresh_config = refresh_mode_config(refresh_mode)
        if refresh_config is None:
            return {"ok": False, "error": "invalid_refresh_mode"}
        update_payload = {
            "name": _text(payload.get("name")) or _text(current.get("name")),
            "natural_language_definition": _text(payload.get("natural_language_definition"))
            if "natural_language_definition" in payload
            else _text(current.get("natural_language_definition")),
            "timezone": _text(current.get("timezone")) or "Asia/Shanghai",
            **refresh_config,
        }
        updated = self._repo.update_package_config(int(package_id), update_payload)
        return {
            "ok": bool(updated),
            "package": _admin_package_detail(self._repo.get_package_detail(int(package_id)) or updated or {}),
            "error": "" if updated else "package_not_found",
        }

    def copy_admin_package(self, package_id: int) -> dict[str, Any]:
        source = self._repo.get_package(int(package_id))
        if not source:
            return {"ok": False, "error": "package_not_found"}
        base_key = f"{_text(source.get('package_key'))}_copy_{int(package_id)}"
        package_key = self._available_copy_key(base_key)
        copied = self._repo.copy_package(int(package_id), package_key=package_key, name=f"{_text(source.get('name')) or _text(source.get('package_key'))}副本")
        return {
            "ok": bool(copied),
            "package": _admin_package_detail(self._repo.get_package_detail(int((copied or {}).get("id") or 0)) or copied or {}),
            "error": "" if copied else "package_not_found",
        }

    def pause_admin_package(self, package_id: int) -> dict[str, Any]:
        package = self._repo.update_package_status(int(package_id), "paused", reason="admin_paused")
        return {
            "ok": bool(package),
            "package": _admin_package_detail(self._repo.get_package_detail(int(package_id)) or package or {}),
            "error": "" if package else "package_not_found",
        }

    def activate_admin_package(self, package_id: int) -> dict[str, Any]:
        previous = self._repo.get_package(int(package_id))
        package = self._repo.activate_package(int(package_id))
        if not package:
            return {"ok": False, "package": _admin_package_detail({}), "error": "package_not_found"}
        launch_refresh = None
        if _text((previous or {}).get("status")) != "active":
            launch_refresh = self._refresh_package_on_launch(int(package_id))
        return {
            "ok": True,
            "package": _admin_package_detail(self._repo.get_package_detail(int(package_id)) or package or {}),
            "launch_refresh": launch_refresh,
            "error": "",
        }

    def archive_admin_package(self, package_id: int) -> dict[str, Any]:
        package = self._repo.update_package_status(int(package_id), "archived", reason="admin_archived")
        return {
            "ok": bool(package),
            "package": _admin_package_detail(self._repo.get_package_detail(int(package_id)) or package or {}),
            "error": "" if package else "package_not_found",
        }

    def list_admin_members(self, package_id: int, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not self._repo.get_package(int(package_id)):
            return {"ok": False, "error": "package_not_found", "items": [], "total": 0, "limit": limit, "offset": offset}
        rows, total = self._repo.list_admin_members(int(package_id), limit=limit, offset=offset)
        items = [
            {
                "nickname": _text(row.get("nickname")) or "未命名客户",
                "external_userid": _text(row.get("external_userid")),
                "entered_at": _admin_datetime(row.get("entered_at")),
            }
            for row in rows
        ]
        return {"ok": True, "items": items, "total": total, "limit": max(1, min(int(limit or 50), 200)), "offset": max(0, int(offset or 0))}

    def get_admin_webhook(self, package_id: int, *, request_base_url: str = "") -> dict[str, Any]:
        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        subscriptions = self._repo.list_subscriptions(int(package_id), active_only=False, trigger_event_type="entered")
        subscription = _first_webhook_subscription(subscriptions)
        return {
            "ok": True,
            "webhook": {
                "inbound_webhook_url": inbound_webhook_url(package, request_base_url=request_base_url),
                "inbound_auth_mode": "aicrm_hmac_sha256",
                "outbound_enabled": bool(subscription and _text(subscription.get("status")) == "active"),
                "outbound_webhook_url": _text((subscription or {}).get("webhook_url")),
                "outbound_auth_mode": "aicrm_hmac_sha256",
                "last_outbound_at": "",
                "last_inbound_at": "",
            },
        }

    def update_admin_webhook(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        webhook_url = _text(payload.get("outbound_webhook_url"))
        enabled = bool(payload.get("outbound_enabled"))
        existing = _first_webhook_subscription(self._repo.list_subscriptions(int(package_id), active_only=False, trigger_event_type="entered"))
        if existing:
            update_payload = {"status": "active" if enabled else "paused", "webhook_url": webhook_url}
            self._repo.update_subscription(int(existing["id"]), update_payload)
        elif enabled or webhook_url:
            self._repo.create_subscription(
                int(package_id),
                {
                    "trigger_event_type": "entered",
                    "dispatch_mode": "per_run",
                    "target_type": "webhook",
                    "webhook_url": webhook_url,
                    "execution_mode": "execute",
                },
            )
        return self.get_admin_webhook(int(package_id))

    def list_admin_senders(self, package_id: int) -> dict[str, Any]:
        if not self._repo.get_package(int(package_id)):
            return {"ok": False, "error": "package_not_found", "items": []}
        return {"ok": True, "items": [_sender_item(row) for row in self._repo.list_senders(int(package_id))]}

    def replace_admin_senders(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._repo.get_package(int(package_id)):
            return {"ok": False, "error": "package_not_found", "items": []}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            sender_userid = _text(item.get("sender_userid"))
            if not sender_userid or sender_userid in seen:
                continue
            status = _text(item.get("status")) or "active"
            if status not in {"active", "paused"}:
                return {"ok": False, "error": "invalid_sender_status"}
            seen.add(sender_userid)
            normalized.append(
                {
                    "sender_userid": sender_userid,
                    "display_name": _text(item.get("display_name")) or sender_userid,
                    "priority": int(item.get("priority") or 100),
                    "status": status,
                }
            )
        rows = self._repo.replace_senders(int(package_id), normalized)
        return {"ok": True, "items": [_sender_item(row) for row in rows]}

    def create_admin_version(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        request = PackageVersionCreateRequest(**dict(payload or {}))
        result = self.create_version(int(package_id), request)
        return {
            "ok": bool(result.get("ok")),
            "version": _safe_version(result.get("version")),
            "validation_errors": result.get("validation_errors", []),
            "error": result.get("error", ""),
        }

    def preview_admin_package(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        version = None
        sql_text = _text(payload.get("sql_text"))
        sql_kind = _text(payload.get("sql_kind")) or "incremental"
        if not sql_text:
            version_id = int(payload.get("version_id") or 0)
            version = self._repo.get_version(version_id) if version_id > 0 else self._repo.get_latest_version(int(package_id))
            if version and int(version.get("package_id") or 0) != int(package_id):
                version = None
            if not version:
                return {"ok": False, "error": "version_not_found"}
            sql_text = _text(version.get("snapshot_sql_text" if sql_kind in {"daily", "snapshot", "snapshot_current"} else "incremental_sql_text"))
        params = build_preview_runtime_params(
            package,
            version,
            payload.get("params") if isinstance(payload.get("params"), dict) else None,
            sql_kind,
        )
        result = self.preview(int(package_id), PreviewRequest(sql_text=sql_text, sql_kind=sql_kind, params=params, limit=int(payload.get("limit") or 20)))
        return {
            "ok": bool(result.get("ok")),
            "sample_rows": result.get("sample_rows", []),
            "dependencies": result.get("dependencies", []),
            "validation_errors": (result.get("validation") or {}).get("errors", []),
            "natural_language_summary": _text(package.get("natural_language_definition")),
            "error": result.get("error", ""),
        }

    def publish_admin_package(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.publish(int(package_id), version_id=payload.get("version_id"))
        if not result.get("ok"):
            return result
        package = self._repo.get_package_detail(int(package_id)) or result.get("package") or {}
        return {
            "ok": True,
            "package": _admin_package_detail(package),
            "version": _safe_version(result.get("version")),
            "launch_refresh": result.get("launch_refresh"),
        }

    def publish_external_package(self, package_id: int, *, version_id: int | None = None) -> dict[str, Any]:
        result = self._publish_validated(int(package_id), version_id=version_id, activate=False)
        if not result.get("ok"):
            return result
        package = self._repo.get_package_detail(int(package_id)) or result.get("package") or {}
        return {
            "ok": True,
            "package": _admin_package_detail(package),
            "version": _safe_version(result.get("version")),
        }

    def _available_copy_key(self, base_key: str) -> str:
        candidate = base_key
        suffix = 2
        while self._repo.get_package_by_key(candidate):
            candidate = f"{base_key}_{suffix}"
            suffix += 1
        return candidate

    def create_package(self, request: PackageCreateRequest) -> dict[str, Any]:
        payload = request.model_dump()
        if _text(payload.get("refresh_mode")):
            refresh_config = refresh_mode_config(_text(payload.get("refresh_mode")))
            if refresh_config is None:
                raise ValueError("invalid_refresh_mode")
            payload.update(refresh_config)
        package = self._repo.create_package(payload)
        version = None
        incremental_sql = _text(payload.get("incremental_sql_text"))
        snapshot_sql = _text(payload.get("snapshot_sql_text"))
        if _text(payload.get("sql_text")):
            if _text(payload.get("query_mode")) == "snapshot_current":
                snapshot_sql = _text(payload.get("sql_text"))
            else:
                incremental_sql = _text(payload.get("sql_text"))
        if incremental_sql or snapshot_sql:
            version = self.create_version(
                int(package["id"]), PackageVersionCreateRequest(**{**payload, "incremental_sql_text": incremental_sql, "snapshot_sql_text": snapshot_sql})
            )["version"]
        return {"ok": True, "package": package, "version": version}

    def get_package(self, package_id: int) -> dict[str, Any]:
        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "error": "package_not_found"}
        return {"ok": True, "package": package, "current_version": self._repo.get_current_version(int(package_id))}

    def create_version(self, package_id: int, request: PackageVersionCreateRequest) -> dict[str, Any]:
        payload = request.model_dump()
        if _text(payload.get("sql_text")) and not _text(payload.get("incremental_sql_text")) and not _text(payload.get("snapshot_sql_text")):
            payload["incremental_sql_text"] = _text(payload.get("sql_text"))
        dependencies: list[str] = []
        validation_errors: list[str] = []
        for sql_text in (_text(payload.get("incremental_sql_text")), _text(payload.get("snapshot_sql_text"))):
            if not sql_text:
                continue
            plan = build_execution_plan(sql_text, payload.get("parameters") or {})
            dependencies.extend(plan.dependencies)
            validation_errors.extend(plan.validation.errors)
        payload["dependencies"] = sorted(set(dependencies))
        payload["validation_errors"] = sorted(set(validation_errors))
        version = self._repo.create_version(package_id, payload)
        self._repo.replace_dependencies(package_id, int(version["id"]), sorted(set(dependencies)))
        return {"ok": not validation_errors, "version": version, "validation_errors": sorted(set(validation_errors))}

    def publish(self, package_id: int, *, version_id: int | None = None) -> dict[str, Any]:
        return self._publish_validated(package_id, version_id=version_id, activate=True)

    def _publish_validated(self, package_id: int, *, version_id: int | None = None, activate: bool = True) -> dict[str, Any]:
        package = self._repo.get_package(package_id)
        if not package:
            return {"ok": False, "error": "package_not_found"}
        if version_id is not None:
            version = self._repo.get_version(int(version_id))
            if not version or int(version.get("package_id") or 0) != int(package_id):
                return {"ok": False, "error": "version_not_found"}
        else:
            version = self._repo.get_latest_version(package_id)
        if not version:
            return {"ok": False, "error": "version_not_found"}
        errors = []
        dependencies: list[str] = []
        if bool(package.get("incremental_enabled", True)) and not _text(version.get("incremental_sql_text")):
            errors.append("incremental_sql_required")
        if bool(package.get("daily_enabled", False)) and not _text(version.get("snapshot_sql_text")):
            errors.append("snapshot_sql_required")
        for sql_text in (_text(version.get("incremental_sql_text")), _text(version.get("snapshot_sql_text"))):
            if not sql_text:
                continue
            plan = build_execution_plan(sql_text, version.get("parameters_json") or {})
            errors.extend(plan.validation.errors)
            dependencies.extend(plan.dependencies)
        if errors:
            self._repo.update_version_validation(int(version["id"]), dependencies=sorted(set(dependencies)), validation_errors=sorted(set(errors)))
            return {"ok": False, "error": "sql_validation_failed", "validation_errors": sorted(set(errors))}
        was_active = _text(package.get("status")) == "active"
        had_current_version = int(package.get("current_version_id") or 0) > 0
        self._repo.update_version_validation(int(version["id"]), dependencies=sorted(set(dependencies)), validation_errors=[])
        self._repo.replace_dependencies(package_id, int(version["id"]), sorted(set(dependencies)))
        published = (
            self._repo.publish_version(package_id, int(version["id"]))
            if activate
            else self._repo.publish_version_without_activation(package_id, int(version["id"]))
        )
        result = {"ok": True, "package": self._repo.get_package(package_id), "version": published}
        if activate and (not was_active or not had_current_version):
            result["launch_refresh"] = self._refresh_package_on_launch(package_id)
        return result

    def _refresh_package_on_launch(self, package_id: int) -> dict[str, Any]:
        from .refresh_intents import AudienceRefreshIntentService

        package = self._repo.get_package(int(package_id))
        if not package:
            return {"ok": False, "skipped": True, "reason": "package_not_found", "real_external_call_executed": False}
        version = self._repo.get_current_version(int(package_id))
        if not version:
            return {"ok": False, "skipped": True, "reason": "current_version_not_found", "real_external_call_executed": False}
        run_type = "daily" if _text(version.get("snapshot_sql_text")) else "incremental"
        result = AudienceRefreshIntentService().request_package_refresh(
            int(package_id),
            refresh_kind=run_type,
            source_event_key=f"package_launch:{int(package_id)}:{int(version.get('id') or 0)}:{datetime.now(timezone.utc).isoformat()}",
            parent_execution_id="",
        )
        return {
            "trigger": "package_launch",
            "run_type": run_type,
            **result,
            "real_external_call_executed": False,
        }

    def pause(self, package_id: int, *, reason: str = "") -> dict[str, Any]:
        package = self._repo.update_package_status(package_id, "paused", reason=reason)
        return {"ok": bool(package), "package": package, "error": "" if package else "package_not_found"}

    def archive(self, package_id: int, *, reason: str = "") -> dict[str, Any]:
        package = self._repo.update_package_status(package_id, "archived", reason=reason)
        return {"ok": bool(package), "package": package, "error": "" if package else "package_not_found"}

    def preview(self, package_id: int, request: PreviewRequest) -> dict[str, Any]:
        sql_text = request.sql_text
        if not sql_text:
            version = self._repo.get_current_version(package_id)
            sql_text = _text((version or {}).get("snapshot_sql_text" if request.sql_kind == "daily" else "incremental_sql_text"))
        from .refresh_service import AudienceRefreshService

        return AudienceRefreshService(repository=self._repo, internal_events=self._events).preview_sql(sql_text, request.params, limit=request.limit)

    def list_subscriptions(self, package_id: int) -> dict[str, Any]:
        return {"ok": True, "subscriptions": self._repo.list_subscriptions(package_id)}

    def create_subscription(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        subscription = self._repo.create_subscription(package_id, payload)
        return {"ok": True, "subscription": subscription, "deduplicated": bool(subscription.get("deduplicated"))}

    def update_subscription(self, subscription_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        subscription = self._repo.update_subscription(subscription_id, payload)
        return {"ok": bool(subscription), "subscription": subscription, "error": "" if subscription else "subscription_not_found"}

    def emit_tick(self, tick_type: str, *, actor_id: str = "ai_audience_scheduler") -> dict[str, Any]:
        from .refresh_intents import AudienceRefreshIntentService

        bucket = _tick_bucket(tick_type)
        return AudienceRefreshIntentService().request_due_refreshes(
            "daily" if tick_type == "daily" else "incremental",
            bucket=bucket,
            actor_id=actor_id,
        )

    def has_launch_refresh_due(self, refresh_kind: str = "daily") -> bool:
        return self._repo.has_launch_refresh_due("daily" if refresh_kind == "daily" else "incremental")

    def emit_source_changed(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .refresh_intents import AudienceRefreshIntentService

        return AudienceRefreshIntentService().request_source_change(payload, emit_source_audit=True)

    def diagnostics(self, package_id: int, kind: str) -> dict[str, Any]:
        if kind == "runs":
            return {"ok": True, "runs": self._repo.list_runs(package_id)}
        if kind == "members":
            return {"ok": True, "members": self._repo.list_members(package_id)}
        if kind == "events":
            return {"ok": True, "events": self._repo.list_events(package_id)}
        return {"ok": False, "error": "unknown_diagnostic_kind"}

    def external_effects(self, package_id: int) -> dict[str, Any]:
        member_events = self._repo.list_events(package_id, limit=500)
        member_event_ids = {str(item.get("id")) for item in member_events}
        jobs, total = ExternalEffectService().list_jobs({"business_type": "ai_audience_member_event"}, limit=200)
        items = [job.to_dict() for job in jobs if str(job.business_id) in member_event_ids]
        run_ids = {str(item.get("run_id")) for item in member_events if item.get("run_id")}
        run_jobs, run_total = ExternalEffectService().list_jobs({"business_type": "ai_audience_package_run"}, limit=200)
        items.extend(job.to_dict() for job in run_jobs if str(job.business_id) in run_ids)
        return {"ok": True, "total_scanned": total, "external_effect_jobs": items}

    def health(self) -> dict[str, Any]:
        return {"ok": True, **self._repo.health()}


def _tick_bucket(tick_type: str) -> str:
    from datetime import datetime, timezone

    now = datetime.now(ZoneInfo("Asia/Shanghai")) if tick_type == "daily" else datetime.now(timezone.utc)
    if tick_type == "daily":
        return now.strftime("%Y-%m-%d")
    minute = (now.minute // 3) * 3
    return now.replace(minute=minute, second=0, microsecond=0).isoformat()


def _admin_package_item(row: dict[str, Any]) -> dict[str, Any]:
    refresh_mode = refresh_mode_from_row(row)
    return {
        "id": int(row.get("id") or 0),
        "package_key": _text(row.get("package_key")),
        "name": _text(row.get("name")) or _text(row.get("package_key")) or "未命名人群包",
        "status": _text(row.get("status")) or "draft",
        "member_count": int(row.get("member_count") or 0),
        "last_refreshed_at": _admin_datetime(row.get("last_refreshed_at")) if row.get("last_refreshed_at") else None,
        "refresh_mode": refresh_mode,
        "refresh_mode_label": refresh_mode_label(refresh_mode),
    }


def _admin_package_detail(row: dict[str, Any]) -> dict[str, Any]:
    item = _admin_package_item(row)
    item["natural_language_definition"] = _text(row.get("natural_language_definition"))
    return item


def refresh_mode_config(refresh_mode: str) -> dict[str, Any] | None:
    if refresh_mode == "manual":
        return {"incremental_enabled": False, "incremental_interval_seconds": 180, "daily_enabled": False, "daily_refresh_time": "02:00"}
    if refresh_mode == "incremental_3m":
        return {"incremental_enabled": True, "incremental_interval_seconds": 180, "daily_enabled": False, "daily_refresh_time": "02:00"}
    if refresh_mode == "daily_0200":
        return {"incremental_enabled": False, "incremental_interval_seconds": 180, "daily_enabled": True, "daily_refresh_time": "02:00"}
    if refresh_mode == "incremental_3m_plus_daily_0200":
        return {"incremental_enabled": True, "incremental_interval_seconds": 180, "daily_enabled": True, "daily_refresh_time": "02:00"}
    return None


def refresh_mode_from_row(row: dict[str, Any]) -> str:
    incremental = bool(row.get("incremental_enabled"))
    daily = bool(row.get("daily_enabled"))
    if incremental and daily:
        return "incremental_3m_plus_daily_0200"
    if incremental:
        return "incremental_3m"
    if daily:
        return "daily_0200"
    return "manual"


def refresh_mode_label(refresh_mode: str) -> str:
    return {
        "manual": "手动",
        "incremental_3m": "每 3 分钟",
        "daily_0200": "每日 2:00",
        "incremental_3m_plus_daily_0200": "每 3 分钟 + 每日 2:00",
    }.get(refresh_mode, "手动")


def build_preview_runtime_params(
    package: dict[str, Any],
    version: dict[str, Any] | None,
    payload_params: dict[str, Any] | None,
    sql_kind: str,
) -> dict[str, Any]:
    started_at = default_refresh_started_at()
    refresh_kind = "daily" if _text(sql_kind) in {"daily", "snapshot", "snapshot_current"} else "incremental"
    params: dict[str, Any] = dict((version or {}).get("parameters_json") or {})
    if isinstance(payload_params, dict):
        params.update(payload_params)
    params.update(
        {
            "package_key": _text(package.get("package_key")),
            "package_id": int(package.get("id") or 0),
            "refresh_started_at": started_at,
            "last_watermark_at": previous_watermark(package, refresh_kind, started_at=started_at),
            "lookback_seconds": max(0, int(package.get("lookback_seconds") or 600)),
        }
    )
    return params


def inbound_webhook_url(package: dict[str, Any], *, request_base_url: str = "") -> str:
    base_url = _text(os.getenv("AICRM_PUBLIC_BASE_URL")) or _text(request_base_url) or "https://www.youcangogogo.com"
    base_url = base_url.rstrip("/")
    package_key = quote(_text(package.get("package_key")), safe="")
    return f"{base_url}/api/ai/audience/packages/{package_key}/webhook"


def _first_webhook_subscription(subscriptions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in sorted(subscriptions, key=lambda row: int(row.get("id") or 0)):
        if _text(item.get("target_type")) == "webhook":
            return item
    return None


def _sender_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "sender_userid": _text(row.get("sender_userid")),
        "display_name": _text(row.get("display_name")),
        "priority": int(row.get("priority") or 100),
        "status": _text(row.get("status")) or "active",
    }


def _validate_sql_payload(*, incremental_sql: str, snapshot_sql: str, parameters: dict[str, Any]) -> dict[str, Any]:
    dependencies: list[str] = []
    validation_errors: list[str] = []
    for sql_text in (incremental_sql, snapshot_sql):
        if not _text(sql_text):
            continue
        plan = build_execution_plan(sql_text, parameters)
        dependencies.extend(plan.dependencies)
        validation_errors.extend(plan.validation.errors)
    return {
        "dependencies": sorted(set(dependencies)),
        "validation_errors": sorted(set(validation_errors)),
    }


def _safe_version(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row.get("id") or 0),
        "package_id": int(row.get("package_id") or 0),
        "version_number": int(row.get("version_number") or 0),
        "status": _text(row.get("status")) or "draft",
        "dependencies": list(row.get("dependencies_json") or []),
        "validation_errors": list(row.get("validation_errors_json") or []),
        "parameters": dict(row.get("parameters_json") or {}),
        "created_at": _admin_datetime(row.get("created_at")) if row.get("created_at") else "",
        "published_at": _admin_datetime(row.get("published_at")) if row.get("published_at") else "",
    }


def _admin_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
