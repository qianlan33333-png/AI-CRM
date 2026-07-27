from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import secrets
from typing import Any, Protocol

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox
from aicrm_next.shared.repository_provider import RepositoryProviderError, assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


class RadarLinksRepository(Protocol):
    def list_links(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
    def get_link(self, link_id: int) -> dict[str, Any] | None: ...
    def get_link_by_code(self, code: str) -> dict[str, Any] | None: ...
    def save_link(self, payload: dict[str, Any], link_id: int | None = None) -> dict[str, Any]: ...
    def set_enabled(self, link_id: int, enabled: bool) -> dict[str, Any] | None: ...
    def record_click_event(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def record_logical_open_event(self, payload: dict[str, Any], *, radar_title: str) -> dict[str, Any]: ...
    def list_click_events(
        self,
        link_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        stage: str = "",
        start_at: str = "",
        end_at: str = "",
        require_unionid: bool = False,
        enrich_external_userid: bool = False,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def list_external_clicks(
        self,
        *,
        mobile: str = "",
        unionid: str = "",
        radar_id: int | None = None,
        radar_code: str = "",
        clicked_from: datetime | None = None,
        clicked_to: datetime | None = None,
        before_event_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]: ...
    def list_external_link_mappings(
        self,
        *,
        radar_id: int | None = None,
        radar_code: str = "",
        before_link_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]: ...
    def stats(self, link_id: int) -> dict[str, Any] | None: ...
    def set_pdf_processing_status(
        self,
        link_id: int,
        *,
        status: str,
        page_count: int = 0,
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any] | None: ...
    def replace_pdf_preview_assets(self, link_id: int, media_item_id: str, assets: list[dict[str, Any]]) -> None: ...
    def list_pdf_preview_assets(self, link_id: int, media_item_id: str) -> list[dict[str, Any]]: ...
    def get_pdf_preview_asset(self, link_id: int, media_item_id: str, page_no: int) -> dict[str, Any] | None: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today_prefix() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class InMemoryRadarLinksRepository:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._links: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._pdf_assets: list[dict[str, Any]] = []
        self._logical_open_events: list[dict[str, Any]] = []
        self._next_id = 1
        self._next_event_id = 1
        self._next_pdf_asset_id = 1
        self._external_identities = [
            {"unionid": "unionid_001", "mobile": "13800138000", "openids": ["openid_001"], "external_userids": ["wx_ext_001"]},
            {"unionid": "unionid_002", "mobile": "", "openids": ["openid_002"], "external_userids": ["wx_ext_002"]},
            {"unionid": "unionid_conflict_a", "mobile": "13900139001", "openids": ["openid_conflict"], "external_userids": []},
            {"unionid": "unionid_conflict_b", "mobile": "13900139002", "openids": ["openid_conflict"], "external_userids": []},
        ]

    def _new_code(self) -> str:
        while True:
            code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
            if code and not self.get_link_by_code(code):
                return code

    def list_links(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = deepcopy(sorted(self._links, key=lambda item: int(item["id"]), reverse=True))
        return rows[offset : offset + limit], len(rows)

    def get_link(self, link_id: int) -> dict[str, Any] | None:
        for item in self._links:
            if int(item["id"]) == int(link_id):
                return deepcopy(item)
        return None

    def get_link_by_code(self, code: str) -> dict[str, Any] | None:
        normalized = str(code or "").strip()
        for item in self._links:
            if item.get("code") == normalized:
                return deepcopy(item)
        return None

    def save_link(self, payload: dict[str, Any], link_id: int | None = None) -> dict[str, Any]:
        now = _now()
        if link_id is None:
            item = {
                "id": self._next_id,
                "code": self._new_code(),
                "created_at": now,
            }
            self._next_id += 1
            self._links.append(item)
        else:
            item = next((entry for entry in self._links if int(entry["id"]) == int(link_id)), None)
            if item is None:
                return {}
        item.update(
            {
                "title": str(payload.get("title", item.get("title", "")) or "").strip(),
                "target_type": str(payload.get("target_type", item.get("target_type", "link")) or "link").strip() or "link",
                "original_url": str(payload.get("original_url", item.get("original_url", "")) or "").strip(),
                "media_item_id": str(payload.get("media_item_id", item.get("media_item_id", "")) or "").strip(),
                "preview_mode": str(payload.get("preview_mode", item.get("preview_mode", "")) or "").strip(),
                "file_name_snapshot": str(payload.get("file_name_snapshot", item.get("file_name_snapshot", "")) or "").strip(),
                "mime_type_snapshot": str(payload.get("mime_type_snapshot", item.get("mime_type_snapshot", "")) or "").strip(),
                "file_size_snapshot": int(payload.get("file_size_snapshot", item.get("file_size_snapshot", 0)) or 0),
                "pdf_processing_status": str(payload.get("pdf_processing_status", item.get("pdf_processing_status", "")) or "").strip(),
                "pdf_page_count": int(payload.get("pdf_page_count", item.get("pdf_page_count", 0)) or 0),
                "pdf_preview_error_code": str(payload.get("pdf_preview_error_code", item.get("pdf_preview_error_code", "")) or "").strip(),
                "pdf_preview_error_message": str(payload.get("pdf_preview_error_message", item.get("pdf_preview_error_message", "")) or "").strip(),
                "enabled": bool(payload.get("enabled", item.get("enabled", True))),
                "auth_required": bool(payload.get("auth_required", item.get("auth_required", True))),
                "source_channel": str(payload.get("source_channel", item.get("source_channel", "")) or "").strip(),
                "campaign_id": str(payload.get("campaign_id", item.get("campaign_id", "")) or "").strip(),
                "staff_id": str(payload.get("staff_id", item.get("staff_id", "")) or "").strip(),
                "created_by": str(payload.get("created_by", item.get("created_by", "")) or "").strip(),
                "updated_at": now,
            }
        )
        return deepcopy(item)

    def set_enabled(self, link_id: int, enabled: bool) -> dict[str, Any] | None:
        item = next((entry for entry in self._links if int(entry["id"]) == int(link_id)), None)
        if item is None:
            return None
        item["enabled"] = bool(enabled)
        item["updated_at"] = _now()
        return deepcopy(item)

    def record_click_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = deepcopy(payload)
        event["id"] = self._next_event_id
        event["event_id"] = self._next_event_id
        event.setdefault("target_type_snapshot", "")
        event.setdefault("person_id", "")
        event.setdefault("ip_hash", "")
        event.setdefault("referer", "")
        event.setdefault("query_params_json", {})
        event.setdefault("source_channel_snapshot", event.get("source_channel", ""))
        event.setdefault("campaign_id_snapshot", event.get("campaign_id", ""))
        event.setdefault("staff_id_snapshot", event.get("staff_id", ""))
        event.setdefault("error_code", "")
        event["created_at"] = event.get("created_at") or _now()
        self._next_event_id += 1
        self._events.append(event)
        return deepcopy(event)

    def record_logical_open_event(self, payload: dict[str, Any], *, radar_title: str) -> dict[str, Any]:
        event = self.record_click_event(payload)
        if str(event.get("unionid") or "").strip():
            self._logical_open_events.append(
                {
                    "event_type": "radar.opened",
                    "click_event_id": int(event.get("id") or 0),
                    "unionid": str(event.get("unionid") or ""),
                    "radar_title": str(radar_title or ""),
                }
            )
        return event

    def list_click_events(
        self,
        link_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        stage: str = "",
        start_at: str = "",
        end_at: str = "",
        require_unionid: bool = False,
        enrich_external_userid: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [
            deepcopy(item)
            for item in reversed(self._events)
            if int(item.get("link_id") or 0) == int(link_id)
        ]
        stage_filter = str(stage or "").strip()
        if stage_filter:
            rows = [item for item in rows if str(item.get("stage") or "") == stage_filter]
        start_filter = str(start_at or "").strip()
        if start_filter:
            rows = [item for item in rows if str(item.get("created_at") or "") >= start_filter]
        end_filter = str(end_at or "").strip()
        if end_filter:
            rows = [item for item in rows if str(item.get("created_at") or "") <= end_filter]
        if require_unionid:
            rows = [item for item in rows if str(item.get("unionid") or "").strip()]
        if enrich_external_userid:
            rows = [self._with_resolved_external_userid(item) for item in rows]
        return rows[offset : offset + limit], len(rows)

    def _with_resolved_external_userid(self, event: dict[str, Any]) -> dict[str, Any]:
        projected = deepcopy(event)
        if str(projected.get("external_userid") or "").strip():
            return projected
        unionid = str(projected.get("unionid") or "").strip()
        candidates = [item for item in self._external_identities if str(item.get("unionid") or "") == unionid]
        if len(candidates) != 1:
            return projected
        external_userids = [str(value or "").strip() for value in candidates[0].get("external_userids", []) if str(value or "").strip()]
        if len(external_userids) == 1:
            projected["external_userid"] = external_userids[0]
        return projected

    def _external_identity(self, event: dict[str, Any]) -> dict[str, str]:
        raw_unionid = str(event.get("unionid") or "").strip()
        raw_openid = str(event.get("openid") or "").strip()
        raw_external_userid = str(event.get("external_userid") or "").strip()
        matched_by = "unionid" if raw_unionid else ("openid" if raw_openid else ("external_userid" if raw_external_userid else ""))
        if matched_by == "unionid":
            candidates = [item for item in self._external_identities if item["unionid"] == raw_unionid]
        elif matched_by == "openid":
            candidates = [item for item in self._external_identities if raw_openid in item["openids"]]
        elif matched_by == "external_userid":
            candidates = [item for item in self._external_identities if raw_external_userid in item["external_userids"]]
        else:
            candidates = []
        if len(candidates) > 1:
            return {"mobile": "", "unionid": "", "identity_status": "conflict", "identity_matched_by": ""}
        if len(candidates) == 1:
            candidate = candidates[0]
            resolved_unionid = str(candidate.get("unionid") or raw_unionid)
            resolved_mobile = str(candidate.get("mobile") or "")
            return {
                "mobile": resolved_mobile,
                "unionid": resolved_unionid,
                "identity_status": "complete" if resolved_mobile and resolved_unionid else "mobile_missing",
                "identity_matched_by": matched_by,
            }
        if raw_unionid:
            return {"mobile": "", "unionid": raw_unionid, "identity_status": "mobile_missing", "identity_matched_by": "unionid"}
        return {"mobile": "", "unionid": "", "identity_status": "unresolved", "identity_matched_by": ""}

    def list_external_clicks(
        self,
        *,
        mobile: str = "",
        unionid: str = "",
        radar_id: int | None = None,
        radar_code: str = "",
        clicked_from: datetime | None = None,
        clicked_to: datetime | None = None,
        before_event_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        links = {int(item["id"]): item for item in self._links}
        rows: list[dict[str, Any]] = []
        for event in self._events:
            stage = str(event.get("stage") or "")
            has_identity = bool(str(event.get("unionid") or event.get("openid") or event.get("external_userid") or "").strip())
            if stage not in {"authorized", "authorized_click"} and not (stage == "landing" and has_identity):
                continue
            link_id = int(event.get("link_id") or 0)
            link = links.get(link_id)
            if not link:
                continue
            created_at = _datetime_value(event.get("created_at"))
            identity = self._external_identity(event)
            projected = {
                "event_id": int(event.get("event_id") or event.get("id") or 0),
                **identity,
                "radar_id": link_id,
                "radar_code": str(link.get("code") or event.get("code") or ""),
                "clicked_at": str(event.get("created_at") or ""),
            }
            if mobile and projected["mobile"] != str(mobile).strip():
                continue
            if unionid and projected["unionid"] != str(unionid).strip():
                continue
            if radar_id is not None and projected["radar_id"] != int(radar_id):
                continue
            if radar_code and projected["radar_code"] != str(radar_code).strip():
                continue
            if clicked_from is not None and (created_at is None or created_at < clicked_from):
                continue
            if clicked_to is not None and (created_at is None or created_at > clicked_to):
                continue
            rows.append(projected)
        rows.sort(key=lambda item: int(item["event_id"]), reverse=True)
        total = len(rows)
        if before_event_id is not None:
            rows = [item for item in rows if int(item["event_id"]) < int(before_event_id)]
        page = rows[: max(1, min(int(limit or 100), 500)) + 1]
        has_more = len(page) > limit
        return deepcopy(page[:limit]), total, has_more

    def list_external_link_mappings(
        self,
        *,
        radar_id: int | None = None,
        radar_code: str = "",
        before_link_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        rows = [
            {"radar_id": int(item["id"]), "radar_code": str(item.get("code") or ""), "title": str(item.get("title") or "")}
            for item in self._links
            if (radar_id is None or int(item["id"]) == int(radar_id))
            and (not radar_code or str(item.get("code") or "") == str(radar_code).strip())
        ]
        rows.sort(key=lambda item: int(item["radar_id"]), reverse=True)
        total = len(rows)
        if before_link_id is not None:
            rows = [item for item in rows if int(item["radar_id"]) < int(before_link_id)]
        page = rows[: max(1, min(int(limit or 100), 500)) + 1]
        has_more = len(page) > limit
        return deepcopy(page[:limit]), total, has_more

    def stats(self, link_id: int) -> dict[str, Any] | None:
        if not self.get_link(link_id):
            return None
        events = [item for item in self._events if int(item.get("link_id") or 0) == int(link_id)]
        landing_events = [item for item in events if item.get("stage") == "landing"]
        authorized_events = [item for item in events if item.get("stage") in {"authorized", "authorized_click"}]
        redirect_events = [item for item in events if item.get("stage") == "redirect"]
        viewer_events = [item for item in events if item.get("stage") == "viewer_open"]
        image_loaded_events = [item for item in events if item.get("stage") == "image_loaded"]
        pdf_opened_events = [item for item in events if item.get("stage") == "pdf_opened"]
        unique_users = {
            str(item.get("unionid") or item.get("openid") or item.get("external_userid") or "").strip()
            for item in authorized_events
            if str(item.get("unionid") or item.get("openid") or item.get("external_userid") or "").strip()
        }
        today = _today_prefix()
        last_clicked_at = ""
        if landing_events:
            last_clicked_at = max(str(item.get("created_at") or "") for item in landing_events)
        last_event_at = max([str(item.get("created_at") or "") for item in events] or [""])
        last_viewed_at = max([str(item.get("created_at") or "") for item in viewer_events + image_loaded_events + pdf_opened_events] or [""])
        return {
            "total_clicks": len(landing_events),
            "total_landings": len(landing_events),
            "authorized_clicks": len(authorized_events),
            "unique_users": len(unique_users),
            "authorized_users": len(unique_users),
            "redirects": len(redirect_events),
            "viewer_opens": len(viewer_events),
            "view_opens": len(viewer_events),
            "image_loaded": len(image_loaded_events),
            "pdf_opened": len(pdf_opened_events),
            "today_clicks": len([item for item in landing_events if str(item.get("created_at") or "").startswith(today)]),
            "today_landings": len([item for item in landing_events if str(item.get("created_at") or "").startswith(today)]),
            "last_clicked_at": last_clicked_at,
            "last_event_at": last_event_at,
            "last_viewed_at": last_viewed_at,
        }

    def set_pdf_processing_status(
        self,
        link_id: int,
        *,
        status: str,
        page_count: int = 0,
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any] | None:
        item = next((entry for entry in self._links if int(entry["id"]) == int(link_id)), None)
        if item is None:
            return None
        item["pdf_processing_status"] = str(status or "").strip()
        item["pdf_page_count"] = int(page_count or 0)
        item["pdf_preview_error_code"] = str(error_code or "").strip()
        item["pdf_preview_error_message"] = str(error_message or "").strip()
        item["updated_at"] = _now()
        return deepcopy(item)

    def replace_pdf_preview_assets(self, link_id: int, media_item_id: str, assets: list[dict[str, Any]]) -> None:
        normalized_media_id = str(media_item_id or "").strip()
        self._pdf_assets = [
            item
            for item in self._pdf_assets
            if not (int(item.get("link_id") or 0) == int(link_id) and str(item.get("media_item_id") or "") == normalized_media_id)
        ]
        for asset in assets:
            row = deepcopy(asset)
            row["id"] = self._next_pdf_asset_id
            self._next_pdf_asset_id += 1
            row["link_id"] = int(link_id)
            row["media_item_id"] = normalized_media_id
            row["created_at"] = row.get("created_at") or _now()
            row["updated_at"] = row.get("updated_at") or _now()
            self._pdf_assets.append(row)

    def list_pdf_preview_assets(self, link_id: int, media_item_id: str) -> list[dict[str, Any]]:
        normalized_media_id = str(media_item_id or "").strip()
        rows = [
            deepcopy(item)
            for item in self._pdf_assets
            if int(item.get("link_id") or 0) == int(link_id) and str(item.get("media_item_id") or "") == normalized_media_id
        ]
        rows.sort(key=lambda item: int(item.get("page_no") or 0))
        return rows

    def get_pdf_preview_asset(self, link_id: int, media_item_id: str, page_no: int) -> dict[str, Any] | None:
        for item in self.list_pdf_preview_assets(link_id, media_item_id):
            if int(item.get("page_no") or 0) == int(page_no):
                return item
        return None


class PostgresRadarLinksRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = _psycopg_url(str(database_url or raw_database_url()).strip())
        if not self._database_url:
            raise RepositoryProviderError("radar_links production repository unavailable: DATABASE_URL is required")

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self._database_url, row_factory=dict_row)
        except Exception as exc:
            raise RepositoryProviderError(f"radar_links production repository unavailable: {exc}") from exc

    @staticmethod
    def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _link_select_columns() -> str:
        return """
            id, code, title, target_type, original_url, media_item_id, preview_mode,
            file_name_snapshot, mime_type_snapshot, file_size_snapshot,
            pdf_processing_status, pdf_page_count, pdf_preview_error_code, pdf_preview_error_message,
            enabled, auth_required, source_channel, campaign_id, staff_id, created_by,
            created_at, updated_at, deleted_at
        """

    def _new_code(self, conn) -> str:
        while True:
            code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
            exists = conn.execute("SELECT 1 FROM radar_links WHERE code = %s", (code,)).fetchone()
            if code and not exists:
                return code

    def list_links(self, *, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        with self._connect() as conn:
            total = int((conn.execute("SELECT COUNT(*) AS total FROM radar_links WHERE deleted_at IS NULL").fetchone() or {}).get("total") or 0)
            rows = conn.execute(
                f"""
                SELECT {self._link_select_columns()}
                FROM radar_links
                WHERE deleted_at IS NULL
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def get_link(self, link_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._link_select_columns()}
                FROM radar_links
                WHERE id = %s AND deleted_at IS NULL
                """,
                (int(link_id),),
            ).fetchone()
        return self._row(row)

    def get_link_by_code(self, code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._link_select_columns()}
                FROM radar_links
                WHERE code = %s AND deleted_at IS NULL
                """,
                (str(code or "").strip(),),
            ).fetchone()
        return self._row(row)

    def save_link(self, payload: dict[str, Any], link_id: int | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            if link_id is None:
                row = conn.execute(
                    """
                    INSERT INTO radar_links (
                        code, title, target_type, original_url, media_item_id, preview_mode,
                        file_name_snapshot, mime_type_snapshot, file_size_snapshot,
                        pdf_processing_status, pdf_page_count, pdf_preview_error_code, pdf_preview_error_message,
                        enabled, auth_required, source_channel, campaign_id, staff_id, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        self._new_code(conn),
                        str(payload.get("title") or "").strip(),
                        str(payload.get("target_type") or "link").strip() or "link",
                        str(payload.get("original_url") or "").strip(),
                        str(payload.get("media_item_id") or "").strip(),
                        str(payload.get("preview_mode") or "").strip(),
                        str(payload.get("file_name_snapshot") or "").strip(),
                        str(payload.get("mime_type_snapshot") or "").strip(),
                        int(payload.get("file_size_snapshot") or 0),
                        str(payload.get("pdf_processing_status") or "").strip(),
                        int(payload.get("pdf_page_count") or 0),
                        str(payload.get("pdf_preview_error_code") or "").strip(),
                        str(payload.get("pdf_preview_error_message") or "").strip(),
                        bool(payload.get("enabled", True)),
                        bool(payload.get("auth_required", True)),
                        str(payload.get("source_channel") or "").strip(),
                        str(payload.get("campaign_id") or "").strip(),
                        str(payload.get("staff_id") or "").strip(),
                        str(payload.get("created_by") or "").strip(),
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE radar_links
                    SET title = %s,
                        target_type = %s,
                        original_url = %s,
                        media_item_id = %s,
                        preview_mode = %s,
                        file_name_snapshot = %s,
                        mime_type_snapshot = %s,
                        file_size_snapshot = %s,
                        pdf_processing_status = %s,
                        pdf_page_count = %s,
                        pdf_preview_error_code = %s,
                        pdf_preview_error_message = %s,
                        enabled = %s,
                        auth_required = %s,
                        source_channel = %s,
                        campaign_id = %s,
                        staff_id = %s,
                        created_by = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        str(payload.get("title") or "").strip(),
                        str(payload.get("target_type") or "link").strip() or "link",
                        str(payload.get("original_url") or "").strip(),
                        str(payload.get("media_item_id") or "").strip(),
                        str(payload.get("preview_mode") or "").strip(),
                        str(payload.get("file_name_snapshot") or "").strip(),
                        str(payload.get("mime_type_snapshot") or "").strip(),
                        int(payload.get("file_size_snapshot") or 0),
                        str(payload.get("pdf_processing_status") or "").strip(),
                        int(payload.get("pdf_page_count") or 0),
                        str(payload.get("pdf_preview_error_code") or "").strip(),
                        str(payload.get("pdf_preview_error_message") or "").strip(),
                        bool(payload.get("enabled", True)),
                        bool(payload.get("auth_required", True)),
                        str(payload.get("source_channel") or "").strip(),
                        str(payload.get("campaign_id") or "").strip(),
                        str(payload.get("staff_id") or "").strip(),
                        str(payload.get("created_by") or "").strip(),
                        int(link_id),
                    ),
                ).fetchone()
        return self.get_link(int((row or {}).get("id") or link_id or 0)) or {}

    def set_enabled(self, link_id: int, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                UPDATE radar_links
                SET enabled = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING {self._link_select_columns()}
                """,
                (bool(enabled), int(link_id)),
            ).fetchone()
        return self._row(row)

    def record_click_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._insert_click_event(conn, payload)
        return dict(row or {})

    def record_logical_open_event(self, payload: dict[str, Any], *, radar_title: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = self._insert_click_event(conn, payload)
            event = dict(row or {})
            unionid = str(event.get("unionid") or "").strip()
            if unionid:
                click_event_id = str(event.get("id") or "").strip()
                enqueue_transactional_internal_event_outbox(
                    conn,
                    InternalEventCreateRequest(
                        event_type="radar.opened",
                        aggregate_type="radar_click_event",
                        aggregate_id=click_event_id,
                        subject_type="unionid",
                        subject_id=unionid,
                        idempotency_key=f"radar.opened:{click_event_id}",
                        source_module="radar_links.application",
                        source_command_id=click_event_id,
                        occurred_at=event.get("created_at"),
                        context=CommandContext(
                            actor_id="radar_viewer",
                            actor_type="customer",
                            source_route="radar_links.logical_open",
                        ),
                        payload={
                            "click_event_id": click_event_id,
                            "radar_id": int(event.get("link_id") or 0),
                            "radar_title": str(radar_title or ""),
                            "target_type": str(event.get("target_type_snapshot") or "link"),
                            "unionid": unionid,
                            "opened_at": str(event.get("created_at") or ""),
                        },
                        payload_summary={
                            "click_event_id": click_event_id,
                            "radar_id": int(event.get("link_id") or 0),
                            "target_type": str(event.get("target_type_snapshot") or "link"),
                            "unionid_present": True,
                        },
                    ),
                )
        return event

    @staticmethod
    def _insert_click_event(conn: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
        return conn.execute(
                """
                INSERT INTO radar_click_events (
                    link_id, code, stage, openid, unionid, external_userid,
                    target_type_snapshot, person_id, ip_hash, user_agent, referer, query_params_json,
                    source_channel, campaign_id, staff_id, source_channel_snapshot, campaign_id_snapshot, staff_id_snapshot, error_code, ip
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, id AS event_id, link_id, code, stage, openid, unionid, external_userid,
                    target_type_snapshot, person_id, ip_hash, user_agent, referer, query_params_json,
                    source_channel, campaign_id, staff_id, source_channel_snapshot, campaign_id_snapshot, staff_id_snapshot, error_code, created_at
                """,
                (
                    int(payload.get("link_id") or 0),
                    str(payload.get("code") or ""),
                    str(payload.get("stage") or ""),
                    str(payload.get("openid") or ""),
                    str(payload.get("unionid") or ""),
                    str(payload.get("external_userid") or ""),
                    str(payload.get("target_type_snapshot") or ""),
                    str(payload.get("person_id") or ""),
                    str(payload.get("ip_hash") or ""),
                    str(payload.get("user_agent") or ""),
                    str(payload.get("referer") or ""),
                    json.dumps(payload.get("query_params_json") or {}, ensure_ascii=False),
                    str(payload.get("source_channel") or ""),
                    str(payload.get("campaign_id") or ""),
                    str(payload.get("staff_id") or ""),
                    str(payload.get("source_channel_snapshot") or payload.get("source_channel") or ""),
                    str(payload.get("campaign_id_snapshot") or payload.get("campaign_id") or ""),
                    str(payload.get("staff_id_snapshot") or payload.get("staff_id") or ""),
                    str(payload.get("error_code") or ""),
                    "",
                ),
            ).fetchone()

    def list_click_events(
        self,
        link_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        stage: str = "",
        start_at: str = "",
        end_at: str = "",
        require_unionid: bool = False,
        enrich_external_userid: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        conditions = ["event.link_id = %s"]
        params: list[Any] = [int(link_id)]
        stage_filter = str(stage or "").strip()
        if stage_filter:
            conditions.append("event.stage = %s")
            params.append(stage_filter)
        start_filter = str(start_at or "").strip()
        if start_filter:
            conditions.append("event.created_at >= %s")
            params.append(start_filter)
        end_filter = str(end_at or "").strip()
        if end_filter:
            conditions.append("event.created_at <= %s")
            params.append(end_filter)
        if require_unionid:
            conditions.append("NULLIF(event.unionid, '') IS NOT NULL")
        where_sql = " AND ".join(conditions)
        external_userid_sql = "event.external_userid"
        identity_join_sql = ""
        if enrich_external_userid:
            external_userid_sql = "COALESCE(NULLIF(event.external_userid, ''), NULLIF(identity.primary_external_userid, ''), '')"
            identity_join_sql = """
                LEFT JOIN crm_user_identity identity
                  ON identity.unionid = event.unionid
                 AND COALESCE(identity.identity_status, 'active') = 'active'
            """
        with self._connect() as conn:
            total = int(
                (conn.execute(f"SELECT COUNT(*) AS total FROM radar_click_events event WHERE {where_sql}", tuple(params)).fetchone() or {}).get("total") or 0
            )
            rows = conn.execute(
                f"""
                SELECT event.id, event.id AS event_id, event.link_id, event.code, event.stage, event.openid, event.unionid,
                    {external_userid_sql} AS external_userid,
                    event.target_type_snapshot, event.person_id, event.ip_hash, event.user_agent, event.referer, event.query_params_json,
                    event.source_channel, event.campaign_id, event.staff_id, event.source_channel_snapshot,
                    event.campaign_id_snapshot, event.staff_id_snapshot, event.error_code, event.created_at
                FROM radar_click_events event
                {identity_join_sql}
                WHERE {where_sql}
                ORDER BY event.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
        return [dict(row) for row in rows], total

    @staticmethod
    def _external_click_query_sql() -> str:
        return """
            WITH logical_events AS (
                SELECT event.id AS event_id, event.link_id, event.code, event.created_at,
                       event.unionid, event.openid, event.external_userid,
                       link.id AS radar_id, link.code AS radar_code
                FROM radar_click_events event
                JOIN radar_links link ON link.id = event.link_id AND link.deleted_at IS NULL
                WHERE (
                        event.stage IN ('authorized', 'authorized_click')
                        OR (
                            event.stage = 'landing'
                            AND COALESCE(NULLIF(event.unionid, ''), NULLIF(event.openid, ''), NULLIF(event.external_userid, '')) IS NOT NULL
                        )
                      )
                  AND (%(radar_id)s IS NULL OR link.id = %(radar_id)s)
                  AND (%(radar_code)s = '' OR link.code = %(radar_code)s)
                  AND (%(clicked_from)s IS NULL OR event.created_at >= %(clicked_from)s)
                  AND (%(clicked_to)s IS NULL OR event.created_at <= %(clicked_to)s)
            ), projected AS (
                SELECT event.event_id,
                       CASE WHEN resolution.active_candidate_count = 1 THEN resolution.mobile ELSE '' END AS mobile,
                       CASE
                           WHEN resolution.candidate_count > 1
                                OR (resolution.candidate_count = 1 AND resolution.active_candidate_count <> 1) THEN ''
                           WHEN resolution.active_candidate_count = 1 THEN resolution.resolved_unionid
                           ELSE event.unionid
                       END AS unionid,
                       event.radar_id,
                       event.radar_code,
                       event.created_at AS clicked_at,
                       CASE
                           WHEN resolution.candidate_count > 1
                                OR (resolution.candidate_count = 1 AND resolution.active_candidate_count <> 1) THEN 'conflict'
                           WHEN COALESCE(NULLIF(resolution.resolved_unionid, ''), NULLIF(event.unionid, '')) IS NOT NULL
                                AND COALESCE(resolution.mobile, '') <> '' THEN 'complete'
                           WHEN COALESCE(NULLIF(resolution.resolved_unionid, ''), NULLIF(event.unionid, '')) IS NOT NULL THEN 'mobile_missing'
                           ELSE 'unresolved'
                       END AS identity_status,
                       CASE
                           WHEN resolution.active_candidate_count = 1 THEN resolution.matched_by
                           WHEN resolution.candidate_count = 0 AND event.unionid <> '' THEN 'unionid'
                           ELSE ''
                       END AS identity_matched_by
                FROM logical_events event
                LEFT JOIN LATERAL (
                    SELECT COUNT(*)::integer AS candidate_count,
                           COUNT(*) FILTER (
                               WHERE COALESCE(identity.identity_status, 'active') = 'active'
                           )::integer AS active_candidate_count,
                           CASE
                               WHEN COUNT(*) = 1 AND COUNT(*) FILTER (
                                   WHERE COALESCE(identity.identity_status, 'active') = 'active'
                               ) = 1 THEN MIN(identity.unionid)
                               ELSE ''
                           END AS resolved_unionid,
                           CASE
                               WHEN COUNT(*) = 1 AND COUNT(*) FILTER (
                                   WHERE COALESCE(identity.identity_status, 'active') = 'active'
                               ) = 1 THEN MIN(identity.mobile)
                               ELSE ''
                           END AS mobile,
                           CASE
                               WHEN COUNT(*) <> 1 OR COUNT(*) FILTER (
                                   WHERE COALESCE(identity.identity_status, 'active') = 'active'
                               ) <> 1 THEN ''
                               WHEN event.unionid <> '' THEN 'unionid'
                               WHEN event.openid <> '' THEN 'openid'
                               WHEN event.external_userid <> '' THEN 'external_userid'
                               ELSE ''
                           END AS matched_by
                    FROM crm_user_identity identity
                    WHERE (
                            (event.unionid <> '' AND identity.unionid = event.unionid)
                            OR (
                                event.unionid = '' AND event.openid <> '' AND (
                                    identity.primary_openid = event.openid
                                    OR identity.openids_json @> jsonb_build_array(event.openid)
                                    OR identity.openids_json @> jsonb_build_array(jsonb_build_object('openid', event.openid))
                                )
                            )
                            OR (
                                event.unionid = '' AND event.openid = '' AND event.external_userid <> '' AND (
                                    identity.primary_external_userid = event.external_userid
                                    OR identity.external_userids_json @> jsonb_build_array(event.external_userid)
                                    OR identity.external_userids_json @> jsonb_build_array(jsonb_build_object('external_userid', event.external_userid))
                                )
                            )
                          )
                ) resolution ON TRUE
            ), filtered AS (
                SELECT *
                FROM projected
                WHERE (%(mobile)s = '' OR mobile = %(mobile)s)
                  AND (%(unionid)s = '' OR unionid = %(unionid)s)
            ), total AS (
                SELECT COUNT(*)::integer AS total
                FROM filtered
            ), page AS (
                SELECT event_id, mobile, unionid, radar_id, radar_code, clicked_at,
                       identity_status, identity_matched_by
                FROM filtered
                WHERE (%(before_event_id)s IS NULL OR event_id < %(before_event_id)s)
                ORDER BY event_id DESC
                LIMIT %(limit)s
            )
            SELECT page.event_id, page.mobile, page.unionid, page.radar_id, page.radar_code,
                   page.clicked_at, page.identity_status, page.identity_matched_by, total.total
            FROM total
            LEFT JOIN page ON TRUE
            ORDER BY page.event_id DESC NULLS LAST
        """

    def list_external_clicks(
        self,
        *,
        mobile: str = "",
        unionid: str = "",
        radar_id: int | None = None,
        radar_code: str = "",
        clicked_from: datetime | None = None,
        clicked_to: datetime | None = None,
        before_event_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        safe_limit = max(1, min(int(limit or 100), 500))
        params = {
            "mobile": str(mobile or "").strip(),
            "unionid": str(unionid or "").strip(),
            "radar_id": int(radar_id) if radar_id is not None else None,
            "radar_code": str(radar_code or "").strip(),
            "clicked_from": clicked_from,
            "clicked_to": clicked_to,
            "before_event_id": int(before_event_id) if before_event_id is not None else None,
            "limit": safe_limit + 1,
        }
        try:
            with self._connect() as conn:
                rows = conn.execute(self._external_click_query_sql(), params).fetchall()
        except RepositoryProviderError:
            raise
        except Exception as exc:
            raise RepositoryProviderError(f"external radar click read unavailable: {exc}") from exc
        total = int((rows[0] if rows else {}).get("total") or 0)
        items = [
            {key: value for key, value in dict(row).items() if key != "total"}
            for row in rows
            if row.get("event_id") is not None
        ]
        has_more = len(items) > safe_limit
        return items[:safe_limit], total, has_more

    def list_external_link_mappings(
        self,
        *,
        radar_id: int | None = None,
        radar_code: str = "",
        before_link_id: int | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        safe_limit = max(1, min(int(limit or 100), 500))
        conditions = ["deleted_at IS NULL"]
        params: list[Any] = []
        if radar_id is not None:
            conditions.append("id = %s")
            params.append(int(radar_id))
        if radar_code:
            conditions.append("code = %s")
            params.append(str(radar_code).strip())
        where_sql = " AND ".join(conditions)
        try:
            with self._connect() as conn:
                total = int((conn.execute(f"SELECT COUNT(*) AS total FROM radar_links WHERE {where_sql}", tuple(params)).fetchone() or {}).get("total") or 0)
                cursor_conditions = list(conditions)
                cursor_params = list(params)
                if before_link_id is not None:
                    cursor_conditions.append("id < %s")
                    cursor_params.append(int(before_link_id))
                rows = conn.execute(
                    f"""
                    SELECT id AS radar_id, code AS radar_code, title
                    FROM radar_links
                    WHERE {' AND '.join(cursor_conditions)}
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    tuple(cursor_params + [safe_limit + 1]),
                ).fetchall()
        except RepositoryProviderError:
            raise
        except Exception as exc:
            raise RepositoryProviderError(f"external radar link read unavailable: {exc}") from exc
        items = [dict(row) for row in rows]
        has_more = len(items) > safe_limit
        return items[:safe_limit], total, has_more

    def stats(self, link_id: int) -> dict[str, Any] | None:
        if not self.get_link(link_id):
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE stage = 'landing') AS total_landings,
                    COUNT(*) FILTER (WHERE stage IN ('authorized', 'authorized_click')) AS authorized_clicks,
                    COUNT(DISTINCT NULLIF(COALESCE(NULLIF(unionid, ''), NULLIF(openid, ''), NULLIF(external_userid, '')), ''))
                        FILTER (WHERE stage IN ('authorized', 'authorized_click')) AS unique_users,
                    COUNT(*) FILTER (WHERE stage = 'redirect') AS redirects,
                    COUNT(*) FILTER (WHERE stage = 'viewer_open') AS viewer_opens,
                    COUNT(*) FILTER (WHERE stage = 'image_loaded') AS image_loaded,
                    COUNT(*) FILTER (WHERE stage = 'pdf_opened') AS pdf_opened,
                    COUNT(*) FILTER (WHERE stage = 'landing' AND created_at::date = CURRENT_DATE) AS today_clicks,
                    MAX(created_at) FILTER (WHERE stage = 'landing') AS last_clicked_at,
                    MAX(created_at) AS last_event_at,
                    MAX(created_at) FILTER (WHERE stage IN ('viewer_open', 'image_loaded', 'pdf_opened')) AS last_viewed_at
                FROM radar_click_events
                WHERE link_id = %s
                """,
                (int(link_id),),
            ).fetchone()
            total_landings = int((row or {}).get("total_landings") or 0)
            unique_users = int((row or {}).get("unique_users") or 0)
        return {
            "total_clicks": total_landings,
            "total_landings": total_landings,
            "authorized_clicks": int((row or {}).get("authorized_clicks") or 0),
            "unique_users": unique_users,
            "authorized_users": unique_users,
            "redirects": int((row or {}).get("redirects") or 0),
            "viewer_opens": int((row or {}).get("viewer_opens") or 0),
            "view_opens": int((row or {}).get("viewer_opens") or 0),
            "image_loaded": int((row or {}).get("image_loaded") or 0),
            "pdf_opened": int((row or {}).get("pdf_opened") or 0),
            "today_clicks": int((row or {}).get("today_clicks") or 0),
            "today_landings": int((row or {}).get("today_clicks") or 0),
            "last_clicked_at": str((row or {}).get("last_clicked_at") or ""),
            "last_event_at": str((row or {}).get("last_event_at") or ""),
            "last_viewed_at": str((row or {}).get("last_viewed_at") or ""),
        }

    def set_pdf_processing_status(
        self,
        link_id: int,
        *,
        status: str,
        page_count: int = 0,
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                UPDATE radar_links
                SET pdf_processing_status = %s,
                    pdf_page_count = %s,
                    pdf_preview_error_code = %s,
                    pdf_preview_error_message = %s,
                    updated_at = NOW()
                WHERE id = %s AND deleted_at IS NULL
                RETURNING {self._link_select_columns()}
                """,
                (str(status or ""), int(page_count or 0), str(error_code or ""), str(error_message or ""), int(link_id)),
            ).fetchone()
        return self._row(row)

    def replace_pdf_preview_assets(self, link_id: int, media_item_id: str, assets: list[dict[str, Any]]) -> None:
        normalized_media_id = str(media_item_id or "").strip()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM radar_pdf_preview_assets WHERE link_id = %s AND media_item_id = %s",
                (int(link_id), normalized_media_id),
            )
            for asset in assets:
                conn.execute(
                    """
                    INSERT INTO radar_pdf_preview_assets (
                        media_item_id, radar_link_id, link_id, source_file_hash, page_no, page_count,
                        preview_mime_type, preview_storage_key, preview_data_base64, preview_public_url,
                        width, height, file_size, render_dpi, render_quality,
                        status, error_code, error_message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        normalized_media_id,
                        int(link_id),
                        int(link_id),
                        str(asset.get("source_file_hash") or ""),
                        int(asset.get("page_no") or 0),
                        int(asset.get("page_count") or 0),
                        str(asset.get("preview_mime_type") or "image/jpeg"),
                        str(asset.get("preview_storage_key") or ""),
                        str(asset.get("preview_data_base64") or ""),
                        str(asset.get("preview_public_url") or ""),
                        int(asset.get("width") or 0),
                        int(asset.get("height") or 0),
                        int(asset.get("file_size") or 0),
                        int(asset.get("render_dpi") or 144),
                        int(asset.get("render_quality") or 82),
                        str(asset.get("status") or "ready"),
                        str(asset.get("error_code") or ""),
                        str(asset.get("error_message") or ""),
                    ),
                )

    def list_pdf_preview_assets(self, link_id: int, media_item_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, media_item_id, link_id, radar_link_id, source_file_hash, page_no, page_count,
                    preview_mime_type, preview_storage_key, preview_data_base64, preview_public_url,
                    width, height, file_size, render_dpi, render_quality,
                    status, error_code, error_message, created_at, updated_at
                FROM radar_pdf_preview_assets
                WHERE link_id = %s AND media_item_id = %s
                ORDER BY page_no ASC
                """,
                (int(link_id), str(media_item_id or "").strip()),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_pdf_preview_asset(self, link_id: int, media_item_id: str, page_no: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, media_item_id, link_id, radar_link_id, source_file_hash, page_no, page_count,
                    preview_mime_type, preview_storage_key, preview_data_base64, preview_public_url,
                    width, height, file_size, render_dpi, render_quality,
                    status, error_code, error_message, created_at, updated_at
                FROM radar_pdf_preview_assets
                WHERE link_id = %s AND media_item_id = %s AND page_no = %s
                """,
                (int(link_id), str(media_item_id or "").strip(), int(page_no)),
            ).fetchone()
        return self._row(row)


_DEFAULT_REPO = InMemoryRadarLinksRepository()


def build_radar_links_repository() -> RadarLinksRepository:
    if production_data_ready():
        return PostgresRadarLinksRepository()
    return assert_repository_allowed(_DEFAULT_REPO, capability_owner="radar_links")


def reset_radar_links_fixture_state() -> None:
    _DEFAULT_REPO.reset()


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
