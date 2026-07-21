from __future__ import annotations

import base64
import binascii
from datetime import datetime
from io import BytesIO
import hashlib
import secrets
from typing import Any

from aicrm_next.integration_gateway.questionnaire_adapters import WeChatOAuthAdapter, build_wechat_oauth_adapter
from aicrm_next.identity_contact.application import ResolvePersonIdentityQuery
from aicrm_next.identity_contact.wechat_unionid_guard import resolve_oauth_unionid
from aicrm_next.media_library.application import GetImageVariantQuery, GetMediaItemQuery, UploadAttachmentCommand
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.runtime import fixture_mode
from aicrm_next.shared.runtime_settings import runtime_setting
from aicrm_next.shared.share_qr import safe_qr_download_filename, svg_qr_data_url

from .domain import (
    hash_ip,
    normalize_radar_link_payload,
    normalize_target_type,
    radar_link_projection,
    sign_radar_state,
    sign_viewer_session,
    validate_media_for_target,
    verify_radar_state,
    verify_viewer_session,
)
from .dto import RadarLinkCreateRequest, RadarLinkUpdateRequest
from .repo import RadarLinksRepository, build_radar_links_repository


PDF_MAX_FILE_SIZE = 50 * 1024 * 1024
PDF_UPLOAD_PART_SIZE = 1024 * 1024
PDF_PAGE_MAX_FILE_SIZE = 2 * 1024 * 1024

_PDF_UPLOADS: dict[str, dict[str, Any]] = {}


def _secret_key() -> str:
    return runtime_setting("SECRET_KEY")


def _fixture_oauth_identity_inputs_allowed(adapter: WeChatOAuthAdapter) -> bool:
    return fixture_mode() and str(getattr(adapter, "mode", "") or "").strip() != "production"


class ListRadarLinksQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, *, base_url: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        rows, total = self._repo.list_links(limit=limit, offset=offset)
        items = []
        for item in rows:
            projection = radar_link_projection(item, base_url=base_url)
            stats = self._repo.stats(int(item.get("id") or 0)) or {}
            summary = _list_stats_summary(stats)
            projection.update(summary)
            projection["stats_summary"] = summary
            items.append(projection)
        return {"ok": True, "items": items, "radar_links": items, "total": total, "limit": limit, "offset": offset}

    __call__ = execute


class ListSidebarRadarLinksQuery:
    """Return enabled radar wrappers without targets, identities, or click statistics."""

    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, *, base_url: str, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        scan_offset = 0
        while True:
            batch, total = self._repo.list_links(limit=200, offset=scan_offset)
            rows.extend(batch)
            scan_offset += len(batch)
            if not batch or scan_offset >= int(total or 0):
                break
        enabled = [item for item in rows if bool(item.get("enabled", True))]
        safe_offset = max(0, int(offset))
        page = (
            enabled[safe_offset:]
            if limit is None
            else enabled[safe_offset : safe_offset + max(1, min(int(limit), 10_000))]
        )
        type_labels = {"link": "链接", "image": "图片", "pdf": "PDF"}
        items = []
        for item in page:
            target_type = normalize_target_type(str(item.get("target_type") or "link"))
            code = str(item.get("code") or "").strip()
            items.append(
                {
                    "id": int(item.get("id") or 0),
                    "title": str(item.get("title") or ""),
                    "target_type": target_type,
                    "type_label": type_labels.get(target_type, "链接"),
                    "url": f"{base_url.rstrip('/')}/r/{code}",
                    "updated_at": str(item.get("updated_at") or ""),
                }
            )
        return {"ok": True, "items": items, "total": len(enabled), "limit": len(items), "offset": safe_offset}

    __call__ = execute


class CreateRadarLinkCommand:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, payload: RadarLinkCreateRequest, *, base_url: str = "") -> dict[str, Any]:
        saved = self._repo.save_link(_normalize_with_media(payload.model_dump()))
        if normalize_target_type(str(saved.get("target_type") or "link")) == "pdf":
            ProcessRadarPdfPreviewCommand(self._repo)(int(saved["id"]))
            saved = self._repo.get_link(int(saved["id"])) or saved
        return {"ok": True, "radar_link": radar_link_projection(saved, base_url=base_url)}

    __call__ = execute


class GetRadarLinkQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int, *, base_url: str = "") -> dict[str, Any]:
        item = self._repo.get_link(link_id)
        if not item:
            raise NotFoundError("radar link not found")
        projection = radar_link_projection(item, base_url=base_url)
        projection["media_item_snapshot"] = _media_item_snapshot(projection)
        return {"ok": True, "radar_link": projection}

    __call__ = execute


class UpdateRadarLinkCommand:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int, payload: RadarLinkUpdateRequest, *, base_url: str = "") -> dict[str, Any]:
        raw_updates = payload.model_dump(exclude_unset=True)
        updates = normalize_radar_link_payload(raw_updates, partial=True)
        if not updates:
            item = self._repo.get_link(link_id)
        else:
            current = self._repo.get_link(link_id)
            if not current:
                item = None
            else:
                item = self._repo.save_link(_normalize_with_media({**current, **updates}), link_id)
        if not item:
            raise NotFoundError("radar link not found")
        if normalize_target_type(str(item.get("target_type") or "link")) == "pdf":
            ProcessRadarPdfPreviewCommand(self._repo)(int(item["id"]))
            item = self._repo.get_link(int(item["id"])) or item
        return {"ok": True, "radar_link": radar_link_projection(item, base_url=base_url)}

    __call__ = execute


class SetRadarLinkEnabledCommand:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int, *, enabled: bool, base_url: str = "") -> dict[str, Any]:
        item = self._repo.set_enabled(link_id, enabled)
        if not item:
            raise NotFoundError("radar link not found")
        return {"ok": True, "radar_link": radar_link_projection(item, base_url=base_url)}

    __call__ = execute


class GetRadarLinkNewOptionsQuery:
    def execute(self) -> dict[str, Any]:
        return {
            "ok": True,
            "target_types": [
                {"value": "link", "label": "外部链接"},
                {"value": "image", "label": "图片预览"},
                {"value": "pdf", "label": "PDF 预览"},
            ],
            "defaults": {
                "enabled": True,
                "auth_required": True,
                "source_channel": "manual",
                "staff_id": "HuangYouCan",
            },
        }

    __call__ = execute


class GetRadarLinkShareQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int, *, base_url: str = "") -> dict[str, Any]:
        item = self._repo.get_link(link_id)
        if not item:
            raise NotFoundError("radar link not found")
        projection = radar_link_projection(item, base_url=base_url)
        url = str(projection.get("wrapper_url") or "")
        if not url.startswith(("http://", "https://")):
            raise ContractError("radar share url is required")
        title = str(projection.get("title") or "内容雷达")
        return {
            "ok": True,
            "share": {
                "title": title,
                "url": url,
                "path": f"/r/{projection['code']}",
                "qr_data_url": svg_qr_data_url(url),
                "download_filename": safe_qr_download_filename(title, fallback="内容雷达"),
            },
        }

    __call__ = execute


class GetRadarLinkStatsQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int) -> dict[str, Any]:
        link = self._repo.get_link(link_id)
        stats = self._repo.stats(link_id)
        if stats is None:
            raise NotFoundError("radar link not found")
        return {"ok": True, "link_id": link_id, "target_type": normalize_target_type(str((link or {}).get("target_type") or "link")), "stats": stats, **stats}

    __call__ = execute


class ListRadarLinkEventsQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(
        self,
        link_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        stage: str = "",
        start_at: str = "",
        end_at: str = "",
        base_url: str = "",
    ) -> dict[str, Any]:
        link = self._repo.get_link(link_id)
        if not link:
            raise NotFoundError("radar link not found")
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        events, total = self._repo.list_click_events(
            link_id,
            limit=limit,
            offset=offset,
            stage=stage,
            start_at=start_at,
            end_at=end_at,
            require_unionid=True,
            enrich_external_userid=True,
        )
        masked = [_event_projection(item) for item in events]
        link_projection = radar_link_projection(link, base_url=base_url)
        brief_link = {
            "id": link_projection["id"],
            "title": link_projection["title"],
            "target_type": link_projection["target_type"],
            "wrapper_url": link_projection["wrapper_url"],
        }
        return {
            "ok": True,
            "link": brief_link,
            "items": masked,
            "events": masked,
            "total": total,
            "limit": limit,
            "offset": offset,
            "pagination": {"limit": limit, "offset": offset, "has_more": offset + limit < int(total or 0)},
        }

    __call__ = execute


class ListExternalRadarClicksQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(
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
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        items, total, has_more = self._repo.list_external_clicks(
            mobile=mobile,
            unionid=unionid,
            radar_id=radar_id,
            radar_code=radar_code,
            clicked_from=clicked_from,
            clicked_to=clicked_to,
            before_event_id=before_event_id,
            limit=safe_limit,
        )
        return {
            "ok": True,
            "items": items,
            "total": int(total or 0),
            "limit": safe_limit,
            "has_more": bool(has_more),
            "next_before_event_id": int(items[-1]["event_id"]) if has_more and items else None,
            "route_owner": "ai_crm_next",
            "source_status": "external_radar_clicks",
            "fallback_used": False,
        }

    __call__ = execute


class ListExternalRadarLinkMappingsQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(
        self,
        *,
        radar_id: int | None = None,
        radar_code: str = "",
        before_link_id: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        items, total, has_more = self._repo.list_external_link_mappings(
            radar_id=radar_id,
            radar_code=radar_code,
            before_link_id=before_link_id,
            limit=safe_limit,
        )
        return {
            "ok": True,
            "items": items,
            "total": int(total or 0),
            "limit": safe_limit,
            "has_more": bool(has_more),
            "next_before_link_id": int(items[-1]["radar_id"]) if has_more and items else None,
            "route_owner": "ai_crm_next",
            "source_status": "external_radar_links",
            "fallback_used": False,
        }

    __call__ = execute


class ExportRadarLinkEventsQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(
        self,
        link_id: int,
        *,
        start_at: str = "",
        end_at: str = "",
    ) -> dict[str, Any]:
        link = self._repo.get_link(link_id)
        if not link:
            raise NotFoundError("radar link not found")
        rows: list[dict[str, Any]] = []
        offset = 0
        page_size = 500
        while True:
            batch, total = self._repo.list_click_events(
                link_id,
                limit=page_size,
                offset=offset,
                start_at=start_at,
                end_at=end_at,
                require_unionid=True,
                enrich_external_userid=True,
            )
            rows.extend(
                {
                    "unionid": str(item.get("unionid") or ""),
                    "external_userid": str(item.get("external_userid") or ""),
                    "created_at": str(item.get("created_at") or ""),
                }
                for item in batch
            )
            offset += page_size
            if offset >= int(total or 0) or not batch:
                break
        return {"ok": True, "link_id": link_id, "items": rows, "total": len(rows)}

    __call__ = execute


class InitiateRadarPdfUploadCommand:
    def execute(self, *, file_name: str, file_size: int, mime_type: str) -> dict[str, Any]:
        normalized_name = str(file_name or "").strip() or "content.pdf"
        normalized_mime = str(mime_type or "application/octet-stream").split(";")[0].strip().lower()
        if not normalized_name.lower().endswith(".pdf") and normalized_mime != "application/pdf":
            raise ContractError("unsupported_mime_type: only PDF uploads are supported")
        if int(file_size or 0) <= 0:
            raise ContractError("invalid_pdf: file_size is required")
        if int(file_size or 0) > PDF_MAX_FILE_SIZE:
            raise ContractError("request_body_too_large: pdf file too large; max 50MB")
        upload_id = secrets.token_urlsafe(18)
        _PDF_UPLOADS[upload_id] = {
            "file_name": normalized_name,
            "file_size": int(file_size),
            "mime_type": "application/pdf",
            "part_size": PDF_UPLOAD_PART_SIZE,
            "parts": {},
        }
        return {"ok": True, "upload_id": upload_id, "part_size": PDF_UPLOAD_PART_SIZE, "max_file_size": PDF_MAX_FILE_SIZE}

    __call__ = execute


class UploadRadarPdfPartCommand:
    def execute(self, upload_id: str, part_no: int, *, content: bytes) -> dict[str, Any]:
        upload = _PDF_UPLOADS.get(str(upload_id or "").strip())
        if not upload:
            raise NotFoundError("pdf upload not found")
        if int(part_no or 0) <= 0:
            raise ContractError("invalid_part_no: part_no must start from 1")
        if not content:
            raise ContractError("invalid_pdf: upload part is empty")
        if len(content) > int(upload["part_size"]):
            raise ContractError("request_body_too_large: upload part is too large")
        upload["parts"][int(part_no)] = bytes(content)
        return {"ok": True, "upload_id": upload_id, "part_no": int(part_no), "received_size": len(content)}

    __call__ = execute


class CompleteRadarPdfUploadCommand:
    def execute(self, upload_id: str, *, name: str = "", tags: Any = None) -> dict[str, Any]:
        upload = _PDF_UPLOADS.get(str(upload_id or "").strip())
        if not upload:
            raise NotFoundError("pdf upload not found")
        expected_parts = (int(upload["file_size"]) + int(upload["part_size"]) - 1) // int(upload["part_size"])
        missing = [part_no for part_no in range(1, expected_parts + 1) if part_no not in upload["parts"]]
        if missing:
            raise ContractError("missing_upload_part: missing part " + ",".join(str(item) for item in missing))
        content = b"".join(upload["parts"][part_no] for part_no in range(1, expected_parts + 1))
        if len(content) != int(upload["file_size"]):
            raise ContractError("invalid_pdf: uploaded file size mismatch")
        if not content.startswith(b"%PDF-"):
            raise ContractError("invalid_pdf: invalid PDF file")
        result = UploadAttachmentCommand()(
            file_bytes=content,
            file_name=str(upload["file_name"]),
            content_type="application/pdf",
            name=name,
            tags=tags,
        )
        _PDF_UPLOADS.pop(str(upload_id or "").strip(), None)
        item = result["item"]
        return {
            "ok": True,
            "media_item_id": str(item.get("id") or ""),
            "item": item,
            "pdf_processing_status": "pending",
        }

    __call__ = execute


class ProcessRadarPdfPreviewCommand:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int) -> dict[str, Any]:
        link = self._repo.get_link(link_id)
        if not link:
            raise NotFoundError("radar link not found")
        if normalize_target_type(str(link.get("target_type") or "link")) != "pdf":
            raise ContractError("pdf preview is only available for pdf radar content")
        media_id = str(link.get("media_item_id") or "").strip()
        if not media_id:
            raise ContractError("media_item_id is required")
        self._repo.set_pdf_processing_status(link_id, status="processing")
        self._record_processing_event(link, "pdf_processing_started")
        try:
            item = GetMediaItemQuery("attachment")(media_id, include_data=True)["item"]
            data = _decode_media_base64(str(item.get("data_base64") or ""))
            assets = _render_pdf_preview_assets(data, link_id=link_id, media_item_id=media_id)
            self._repo.replace_pdf_preview_assets(link_id, media_id, assets)
            page_count = max([int(asset.get("page_no") or 0) for asset in assets] or [0])
            self._repo.set_pdf_processing_status(link_id, status="ready", page_count=page_count)
            self._record_processing_event(link, "pdf_processing_succeeded")
            return {"ok": True, "link_id": link_id, "pdf_processing_status": "ready", "pdf_page_count": page_count}
        except Exception as exc:
            message = str(exc) or "pdf preview processing failed"
            self._repo.set_pdf_processing_status(
                link_id,
                status="failed",
                error_code="pdf_processing_failed",
                error_message=message[:500],
            )
            self._record_processing_event(link, "pdf_processing_failed", error_code="pdf_processing_failed")
            return {"ok": False, "link_id": link_id, "pdf_processing_status": "failed", "error_code": "pdf_processing_failed", "error_message": message}

    def _record_processing_event(self, link: dict[str, Any], stage: str, *, error_code: str = "") -> None:
        payload = _view_event_payload(link, stage=stage, request_meta={})
        payload["error_code"] = error_code
        self._repo.record_click_event(payload)

    __call__ = execute


class GetRadarPdfProcessingStatusQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, link_id: int) -> dict[str, Any]:
        link = self._repo.get_link(link_id)
        if not link:
            raise NotFoundError("radar link not found")
        return {
            "ok": True,
            "link_id": link_id,
            "pdf_processing_status": str(link.get("pdf_processing_status") or "pending"),
            "pdf_page_count": int(link.get("pdf_page_count") or 0),
            "pdf_preview_error_code": str(link.get("pdf_preview_error_code") or ""),
            "pdf_preview_error_message": str(link.get("pdf_preview_error_message") or ""),
        }

    __call__ = execute


class GetRadarPdfPreviewManifestQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        if normalize_target_type(str(link.get("target_type") or "link")) != "pdf":
            raise NotFoundError("radar pdf manifest not found")
        media_id = str(link.get("media_item_id") or "")
        assets = self._repo.list_pdf_preview_assets(int(link["id"]), media_id)
        status = str(link.get("pdf_processing_status") or ("ready" if assets else "processing"))
        if not assets and status in {"", "pending", "processing"}:
            result = ProcessRadarPdfPreviewCommand(self._repo)(int(link["id"]))
            link = self._repo.get_link(int(link["id"])) or link
            assets = self._repo.list_pdf_preview_assets(int(link["id"]), media_id)
            status = str(result.get("pdf_processing_status") or link.get("pdf_processing_status") or "processing")
        self._repo.record_click_event(_view_event_payload(link, stage="pdf_manifest_loaded", request_meta=request_meta))
        pages = [
            {
                "page_no": int(item.get("page_no") or 0),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "file_size": int(item.get("file_size") or 0),
                "status": str(item.get("status") or "ready"),
                "error_code": str(item.get("error_code") or ""),
                "url": f"/api/h5/radar-contents/{code}/pdf/pages/{int(item.get('page_no') or 0)}",
            }
            for item in assets
        ]
        return {
            "ok": True,
            "target_type": "pdf",
            "title": str(link.get("title") or "PDF 预览"),
            "preview_mode": "page_image",
            "processing_status": status or "processing",
            "page_count": int(link.get("pdf_page_count") or len(pages) or 0),
            "pages": pages,
            "error_code": str(link.get("pdf_preview_error_code") or ""),
            "error_message": str(link.get("pdf_preview_error_message") or ""),
        }

    __call__ = execute


class GetRadarPdfPreviewPageQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, page_no: int, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        if normalize_target_type(str(link.get("target_type") or "link")) != "pdf":
            raise NotFoundError("radar pdf page not found")
        media_id = str(link.get("media_item_id") or "")
        asset = self._repo.get_pdf_preview_asset(int(link["id"]), media_id, int(page_no))
        if not asset or str(asset.get("status") or "") != "ready":
            payload = _view_event_payload(link, stage="pdf_page_error", request_meta=request_meta)
            payload["query_params_json"] = {"page_no": int(page_no)}
            payload["error_code"] = str((asset or {}).get("error_code") or "pdf_page_not_ready")
            self._repo.record_click_event(payload)
            raise NotFoundError("radar pdf page not found")
        content = _decode_media_base64(str(asset.get("preview_data_base64") or ""))
        payload = _view_event_payload(link, stage="pdf_page_loaded", request_meta=request_meta)
        payload["query_params_json"] = {"page_no": int(page_no)}
        self._repo.record_click_event(payload)
        return {
            "ok": True,
            "content": content,
            "mime_type": str(asset.get("preview_mime_type") or "image/jpeg"),
            "file_size": len(content),
        }

    __call__ = execute


class GetRadarImageManifestQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        if normalize_target_type(str(link.get("target_type") or "link")) != "image":
            raise NotFoundError("radar image manifest not found")
        self._repo.record_click_event(_view_event_payload(link, stage="image_manifest_loaded", request_meta=request_meta))
        image_id = str(link.get("media_item_id") or "")
        return {
            "ok": True,
            "target_type": "image",
            "title": str(link.get("title") or "图片预览"),
            "variants": {
                "mobile_1080": f"/api/h5/radar-contents/{code}/image/variants/mobile_1080",
                "large_1440": f"/api/h5/radar-contents/{code}/image/variants/large_1440",
                "original": f"/api/h5/radar-contents/{code}/image/variants/original",
            },
            "media_item_id": image_id,
        }

    __call__ = execute


class GetRadarImageVariantResourceQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, variant_key: str, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        if normalize_target_type(str(link.get("target_type") or "link")) != "image":
            raise NotFoundError("radar image variant not found")
        image_id = str(link.get("media_item_id") or "")
        try:
            variant = GetImageVariantQuery()(image_id, variant_key)["variant"]
            content = variant.get("bytes") or b""
            mime_type = str(variant.get("mime_type") or "image/png")
        except Exception:
            fallback = GetRadarContentResourceQuery(self._repo)(code, target_type="image", viewer_session=viewer_session, request_meta=request_meta)
            content = fallback["content"]
            mime_type = str(fallback.get("mime_type") or "image/png")
        payload = _view_event_payload(link, stage="image_variant_loaded", request_meta=request_meta)
        payload["query_params_json"] = {"variant_key": variant_key}
        self._repo.record_click_event(payload)
        return {"ok": True, "content": content, "mime_type": mime_type}

    __call__ = execute


class GetRadarPdfBytesQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        return GetRadarContentResourceQuery(self._repo)(code, target_type="pdf", viewer_session=viewer_session, request_meta=request_meta)

    __call__ = execute


class ResolveRadarLandingQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(
        self,
        code: str,
        *,
        request_meta: dict[str, Any],
        viewer_session: str | None = None,
    ) -> dict[str, Any]:
        link = self._repo.get_link_by_code(code)
        if not link or not bool(link.get("enabled", True)):
            raise NotFoundError("radar link not found")
        identity = {"openid": "", "unionid": "", "external_userid": ""}
        has_session = False
        try:
            session = verify_viewer_session(viewer_session, code=str(link["code"]), secret_key=_secret_key())
            identity = dict(session.get("identity") or identity)
            has_session = bool(identity.get("unionid")) if bool(link.get("auth_required")) else True
        except ContractError:
            has_session = False
        self._record_event(link, stage="landing", identity=identity, request_meta=request_meta)
        if bool(link.get("auth_required")) and not has_session:
            state = sign_radar_state(code=str(link["code"]), secret_key=_secret_key())
            self._record_event(link, stage="oauth_start", identity=identity, request_meta=request_meta)
            return {"ok": True, "action": "oauth_start", "oauth_start_url": f"/api/h5/radar/oauth/start?state={state}"}
        target_type = normalize_target_type(str(link.get("target_type") or "link"))
        viewer_token = ""
        if target_type in {"image", "pdf"} and not has_session:
            viewer_token = sign_viewer_session(code=str(link["code"]), **identity, secret_key=_secret_key())
        if target_type == "link":
            self._record_event(link, stage="redirect", identity=identity, request_meta=request_meta)
            return {"ok": True, "action": "redirect", "redirect_url": str(link.get("original_url") or ""), "viewer_session_token": viewer_token}
        return {"ok": True, "action": "redirect", "redirect_url": f"/radar/view/{link['code']}", "viewer_session_token": viewer_token}

    def _record_event(self, link: dict[str, Any], *, stage: str, identity: dict[str, str], request_meta: dict[str, Any]) -> None:
        payload = {
                "link_id": int(link["id"]),
                "code": str(link.get("code") or ""),
                "target_type_snapshot": normalize_target_type(str(link.get("target_type") or "link")),
                "stage": stage,
                "openid": identity.get("openid", ""),
                "unionid": identity.get("unionid", ""),
                "external_userid": identity.get("external_userid", ""),
                "source_channel": str(link.get("source_channel") or ""),
                "campaign_id": str(link.get("campaign_id") or ""),
                "staff_id": str(link.get("staff_id") or ""),
                "source_channel_snapshot": str(link.get("source_channel") or ""),
                "campaign_id_snapshot": str(link.get("campaign_id") or ""),
                "staff_id_snapshot": str(link.get("staff_id") or ""),
                "user_agent": request_meta.get("user_agent", ""),
                "ip_hash": hash_ip(str(request_meta.get("ip") or ""), secret_key=_secret_key()),
                "referer": request_meta.get("referer", ""),
                "query_params_json": request_meta.get("query_params_json") if isinstance(request_meta.get("query_params_json"), dict) else {},
            }
        if stage == "landing" and str(identity.get("unionid") or "").strip():
            self._repo.record_logical_open_event(payload, radar_title=str(link.get("title") or ""))
        else:
            self._repo.record_click_event(payload)

    __call__ = execute


class StartRadarOAuthQuery:
    def __init__(self, adapter: WeChatOAuthAdapter | None = None) -> None:
        self._adapter = adapter or build_wechat_oauth_adapter()

    def execute(
        self, *, state: str | None, code: str | None = None, openid: str | None = None, unionid: str | None = None, external_userid: str | None = None
    ) -> dict[str, Any]:
        signed_state = str(state or "").strip() or sign_radar_state(code=str(code or ""), secret_key=_secret_key())
        context = verify_radar_state(signed_state, secret_key=_secret_key())
        callback_url = f"/api/h5/radar/oauth/callback?state={signed_state}"
        allow_fixture_identity = _fixture_oauth_identity_inputs_allowed(self._adapter)
        adapter_result = self._adapter.build_authorize_url(
            slug=str(context["code"]),
            state=signed_state,
            redirect=callback_url,
            openid=openid if allow_fixture_identity else None,
            unionid=unionid if allow_fixture_identity else None,
            external_userid=external_userid if allow_fixture_identity else None,
        )
        result = adapter_result.get("result") if isinstance(adapter_result.get("result"), dict) else {}
        if not adapter_result.get("ok"):
            raise ContractError(str(adapter_result.get("error_message") or "radar oauth adapter unavailable"))
        return {
            "ok": True,
            "redirect_url": result.get("redirect_url") or callback_url,
            "state": signed_state,
            "source_status": result.get("source_status", "fake"),
        }

    __call__ = execute


class CompleteRadarOAuthCallbackCommand:
    def __init__(self, repo: RadarLinksRepository | None = None, adapter: WeChatOAuthAdapter | None = None) -> None:
        self._repo = repo or build_radar_links_repository()
        self._adapter = adapter or build_wechat_oauth_adapter()

    def execute(
        self,
        *,
        state: str | None,
        code: str | None = None,
        openid: str | None = None,
        unionid: str | None = None,
        external_userid: str | None = None,
        request_meta: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        context = verify_radar_state(state, secret_key=_secret_key())
        link = self._repo.get_link_by_code(str(context["code"]))
        if not link or not bool(link.get("enabled", True)):
            raise NotFoundError("radar link not found")
        allow_fixture_identity = _fixture_oauth_identity_inputs_allowed(self._adapter)
        adapter_result = self._adapter.resolve_oauth_identity(
            state=state,
            code=code,
            openid=openid if allow_fixture_identity else None,
            unionid=unionid if allow_fixture_identity else None,
            external_userid=external_userid if allow_fixture_identity else None,
        )
        result = adapter_result.get("result") if isinstance(adapter_result.get("result"), dict) else {}
        if not adapter_result.get("ok"):
            raise ContractError(str(adapter_result.get("error_message") or "radar oauth adapter unavailable"))
        identity = {
            "openid": str(result.get("openid") or "").strip(),
            "unionid": str(result.get("unionid") or "").strip(),
            "external_userid": str(result.get("external_userid") or "").strip(),
        }
        identity["unionid"] = resolve_oauth_unionid(
            identity,
            identity_query=ResolvePersonIdentityQuery(),
        )
        if not identity["unionid"]:
            raise ContractError("unionid_required: radar oauth canonical UnionID is missing")
        meta = request_meta or {}
        for stage in ("oauth_callback", "authorized"):
            event_payload = {
                    "link_id": int(link["id"]),
                    "code": str(link.get("code") or ""),
                    "target_type_snapshot": normalize_target_type(str(link.get("target_type") or "link")),
                    "stage": stage,
                    **identity,
                    "source_channel": str(link.get("source_channel") or ""),
                    "campaign_id": str(link.get("campaign_id") or ""),
                    "staff_id": str(link.get("staff_id") or ""),
                    "source_channel_snapshot": str(link.get("source_channel") or ""),
                    "campaign_id_snapshot": str(link.get("campaign_id") or ""),
                    "staff_id_snapshot": str(link.get("staff_id") or ""),
                    "user_agent": meta.get("user_agent", ""),
                    "ip_hash": hash_ip(str(meta.get("ip") or ""), secret_key=_secret_key()),
                    "referer": meta.get("referer", ""),
                    "query_params_json": meta.get("query_params_json") if isinstance(meta.get("query_params_json"), dict) else {},
                }
            if stage == "authorized" and identity["unionid"]:
                self._repo.record_logical_open_event(event_payload, radar_title=str(link.get("title") or ""))
            else:
                self._repo.record_click_event(event_payload)
        target_type = normalize_target_type(str(link.get("target_type") or "link"))
        viewer_token = sign_viewer_session(code=str(link["code"]), **identity, secret_key=_secret_key())
        redirect_url = str(link.get("original_url") or "") if target_type == "link" else f"/radar/view/{link['code']}"
        if target_type == "link":
            self._repo.record_click_event(
                {
                    "link_id": int(link["id"]),
                    "code": str(link.get("code") or ""),
                    "target_type_snapshot": target_type,
                    "stage": "redirect",
                    **identity,
                    "source_channel": str(link.get("source_channel") or ""),
                    "campaign_id": str(link.get("campaign_id") or ""),
                    "staff_id": str(link.get("staff_id") or ""),
                    "source_channel_snapshot": str(link.get("source_channel") or ""),
                    "campaign_id_snapshot": str(link.get("campaign_id") or ""),
                    "staff_id_snapshot": str(link.get("staff_id") or ""),
                    "user_agent": meta.get("user_agent", ""),
                    "ip_hash": hash_ip(str(meta.get("ip") or ""), secret_key=_secret_key()),
                    "referer": meta.get("referer", ""),
                    "query_params_json": meta.get("query_params_json") if isinstance(meta.get("query_params_json"), dict) else {},
                }
            )
        return {
            "ok": True,
            "redirect_url": redirect_url,
            "identity": identity,
            "source_status": result.get("source_status", "fake"),
            "viewer_session_token": viewer_token,
        }

    __call__ = execute


class GetRadarViewerPageQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        self._record_view_event(link, "viewer_open", request_meta=request_meta)
        return {"ok": True, "radar_link": radar_link_projection(link), "target_type": normalize_target_type(str(link.get("target_type") or "link"))}

    def _record_view_event(self, link: dict[str, Any], stage: str, *, request_meta: dict[str, Any]) -> None:
        self._repo.record_click_event(_view_event_payload(link, stage=stage, request_meta=request_meta))

    __call__ = execute


class GetRadarContentResourceQuery:
    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, target_type: str, viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        link = _require_viewable_content(self._repo, code, viewer_session)
        resolved_target_type = normalize_target_type(str(link.get("target_type") or "link"))
        if resolved_target_type != target_type:
            raise NotFoundError("radar content not found")
        media_kind = "image" if target_type == "image" else "attachment"
        media_id = str(link.get("media_item_id") or "").strip()
        item = GetMediaItemQuery(media_kind)(media_id, include_data=True)["item"]
        data_base64 = str(item.get("data_base64") or "")
        if not data_base64:
            raise NotFoundError("radar content data not found")
        try:
            content = base64.b64decode(data_base64)
        except Exception as exc:
            raise ContractError("radar content data is invalid") from exc
        stage = "image_loaded" if target_type == "image" else "pdf_opened"
        self._repo.record_click_event(_view_event_payload(link, stage=stage, request_meta=request_meta))
        mime_type = str(item.get("mime_type") or link.get("mime_type_snapshot") or ("image/png" if target_type == "image" else "application/pdf"))
        return {
            "ok": True,
            "content": content,
            "mime_type": mime_type,
            "file_name": str(item.get("file_name") or link.get("file_name_snapshot") or ("image" if target_type == "image" else "content.pdf")),
        }

    __call__ = execute


class RecordRadarContentEventCommand:
    ALLOWED_STAGES = {
        "viewer_open",
        "image_loaded",
        "pdf_opened",
        "pdf_manifest_loaded",
        "pdf_page_loaded",
        "pdf_page_error",
        "image_manifest_loaded",
        "image_variant_loaded",
    }

    def __init__(self, repo: RadarLinksRepository | None = None) -> None:
        self._repo = repo or build_radar_links_repository()

    def execute(self, code: str, *, payload: dict[str, Any], viewer_session: str | None, request_meta: dict[str, Any]) -> dict[str, Any]:
        stage = str(payload.get("stage") or "").strip()
        if stage not in self.ALLOWED_STAGES:
            raise ContractError("radar content event stage is not allowed")
        link = _require_viewable_content(self._repo, code, viewer_session)
        target_type = normalize_target_type(str(link.get("target_type") or "link"))
        if stage == "image_loaded" and target_type != "image":
            raise ContractError("image_loaded is only allowed for image radar content")
        if stage == "pdf_opened" and target_type != "pdf":
            raise ContractError("pdf_opened is only allowed for pdf radar content")
        event_payload = _view_event_payload(link, stage=stage, request_meta=request_meta)
        event_payload["query_params_json"] = {
            "page": payload.get("page"),
            "extra": payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
        }
        event = self._repo.record_click_event(event_payload)
        return {"ok": True, "event_id": event.get("event_id") or event.get("id")}

    __call__ = execute


def _list_stats_summary(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_landings": int(stats.get("total_landings") or stats.get("total_clicks") or 0),
        "authorized_users": int(stats.get("authorized_users") or stats.get("unique_users") or 0),
        "view_count": int(stats.get("view_opens") or stats.get("viewer_opens") or 0),
        "last_viewed_at": str(stats.get("last_viewed_at") or ""),
    }


def _decode_media_base64(data_base64: str) -> bytes:
    try:
        return base64.b64decode(str(data_base64 or ""), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ContractError("invalid_media_data: media data is invalid") from exc


def _pdf_page_count(pdf_bytes: bytes) -> int:
    markers = pdf_bytes.count(b"/Type /Page")
    if markers > 0:
        pages_marker = pdf_bytes.count(b"/Type /Pages")
        return max(1, markers - pages_marker)
    return 1


def _render_pdf_preview_assets(pdf_bytes: bytes, *, link_id: int, media_item_id: str) -> list[dict[str, Any]]:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ContractError("invalid_pdf: invalid PDF file")
    rendered = _render_pdf_preview_assets_with_pymupdf(pdf_bytes, link_id=link_id, media_item_id=media_item_id)
    if rendered:
        return rendered
    page_count = _pdf_page_count(pdf_bytes)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    assets: list[dict[str, Any]] = []
    for page_no in range(1, page_count + 1):
        payload, width, height, mime_type, quality = _render_placeholder_pdf_page(page_no, page_count)
        assets.append(
            {
                "media_item_id": media_item_id,
                "link_id": int(link_id),
                "radar_link_id": int(link_id),
                "source_file_hash": digest,
                "page_no": page_no,
                "page_count": page_count,
                "preview_mime_type": mime_type,
                "preview_storage_key": f"radar_pdf_preview/{media_item_id}/{digest[:16]}/{page_no}.jpg",
                "preview_data_base64": base64.b64encode(payload).decode("ascii"),
                "preview_public_url": "",
                "width": width,
                "height": height,
                "file_size": len(payload),
                "render_dpi": 144,
                "render_quality": quality,
                "status": "ready" if len(payload) <= PDF_PAGE_MAX_FILE_SIZE else "failed",
                "error_code": "" if len(payload) <= PDF_PAGE_MAX_FILE_SIZE else "preview_too_large",
                "error_message": "" if len(payload) <= PDF_PAGE_MAX_FILE_SIZE else "preview page exceeds 2MB",
            }
        )
    return assets


def _render_pdf_preview_assets_with_pymupdf(pdf_bytes: bytes, *, link_id: int, media_item_id: str) -> list[dict[str, Any]]:
    try:
        import fitz  # type: ignore
        from PIL import Image
    except Exception:
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ContractError("invalid_pdf: invalid PDF file") from exc
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    page_count = max(1, int(getattr(doc, "page_count", 0) or len(doc)))
    assets: list[dict[str, Any]] = []
    for index in range(page_count):
        page_no = index + 1
        try:
            page = doc.load_page(index)
            pix = page.get_pixmap(matrix=fitz.Matrix(144 / 72, 144 / 72), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            payload, width, height, quality = _encode_pdf_page_image(image)
            status = "ready" if len(payload) <= PDF_PAGE_MAX_FILE_SIZE else "failed"
            error_code = "" if status == "ready" else "preview_too_large"
            error_message = "" if status == "ready" else "preview page exceeds 2MB"
        except Exception as exc:
            payload, width, height, quality = b"", 0, 0, 0
            status = "failed"
            error_code = "pdf_page_render_failed"
            error_message = str(exc)[:500]
        assets.append(
            {
                "media_item_id": media_item_id,
                "link_id": int(link_id),
                "radar_link_id": int(link_id),
                "source_file_hash": digest,
                "page_no": page_no,
                "page_count": page_count,
                "preview_mime_type": "image/jpeg",
                "preview_storage_key": f"radar_pdf_preview/{media_item_id}/{digest[:16]}/{page_no}.jpg",
                "preview_data_base64": base64.b64encode(payload).decode("ascii") if payload else "",
                "preview_public_url": "",
                "width": width,
                "height": height,
                "file_size": len(payload),
                "render_dpi": 144,
                "render_quality": quality,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
            }
        )
    return assets


def _encode_pdf_page_image(image: Any) -> tuple[bytes, int, int, int]:
    quality = 82
    current = image
    while True:
        out = BytesIO()
        current.save(out, "JPEG", quality=quality, optimize=True)
        payload = out.getvalue()
        if len(payload) <= PDF_PAGE_MAX_FILE_SIZE:
            return payload, int(current.width), int(current.height), quality
        if quality > 46:
            quality -= 8
            continue
        longest = max(int(current.width), int(current.height))
        if longest <= 1600:
            return payload, int(current.width), int(current.height), quality
        scale = 1600 / float(longest)
        current = current.resize((max(1, int(current.width * scale)), max(1, int(current.height * scale))))


def _render_placeholder_pdf_page(page_no: int, page_count: int) -> tuple[bytes, int, int, str, int]:
    try:
        from PIL import Image, ImageDraw

        width, height = 1080, 1528
        image = Image.new("RGB", (width, height), "#ffffff")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 94), fill="#f3f4f6")
        draw.text((42, 34), f"PDF preview page {page_no} / {page_count}", fill="#111827")
        draw.rectangle((42, 150, width - 42, height - 80), outline="#d1d5db", width=3)
        draw.text((72, 190), "PDF preview generated by AI-CRM Next", fill="#374151")
        out = BytesIO()
        quality = 82
        image.save(out, "JPEG", quality=quality, optimize=True)
        payload = out.getvalue()
        while len(payload) > PDF_PAGE_MAX_FILE_SIZE and quality > 45:
            quality -= 8
            out = BytesIO()
            image.save(out, "JPEG", quality=quality, optimize=True)
            payload = out.getvalue()
        return payload, width, height, "image/jpeg", quality
    except Exception:
        # 1x1 JPEG fallback. Keeps the adapter usable even when Pillow is absent.
        payload = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
            "////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
            "////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAARD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAARD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAARD/2gAIAQEAAT8QH//Z"
        )
        return payload, 1, 1, "image/jpeg", 82


def _media_item_snapshot(link: dict[str, Any]) -> dict[str, Any]:
    if str(link.get("target_type") or "link") == "link":
        return {}
    return {
        "media_item_id": str(link.get("media_item_id") or ""),
        "file_name": str(link.get("file_name_snapshot") or ""),
        "mime_type": str(link.get("mime_type_snapshot") or ""),
        "file_size": int(link.get("file_size_snapshot") or 0),
    }


def _mask_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 10:
        return text[:2] + "***"
    return f"{text[:6]}...{text[-4:]}"


def _event_projection(item: dict[str, Any]) -> dict[str, Any]:
    projected = dict(item)
    projected["unionid_masked"] = _mask_identity(projected.get("unionid"))
    projected["openid_masked"] = _mask_identity(projected.get("openid"))
    projected.pop("unionid", None)
    projected.pop("openid", None)
    projected.pop("ip", None)
    return projected


def _normalize_with_media(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_radar_link_payload(payload)
    target_type = normalize_target_type(str(normalized.get("target_type") or payload.get("target_type") or "link"))
    if target_type == "link":
        normalized.update({"media_item_id": "", "preview_mode": "", "file_name_snapshot": "", "mime_type_snapshot": "", "file_size_snapshot": 0})
        return normalized
    media_id = str(normalized.get("media_item_id") or payload.get("media_item_id") or "").strip()
    media_kind = "image" if target_type == "image" else "attachment"
    media_item = GetMediaItemQuery(media_kind)(media_id, include_data=False)["item"] if media_id else None
    normalized.update(validate_media_for_target(target_type, media_item))
    normalized["original_url"] = ""
    normalized["preview_mode"] = str(normalized.get("preview_mode") or ("inline_image" if target_type == "image" else "page_image"))
    if target_type == "pdf":
        normalized.setdefault("pdf_processing_status", "pending")
        normalized.setdefault("pdf_page_count", 0)
        normalized.setdefault("pdf_preview_error_code", "")
        normalized.setdefault("pdf_preview_error_message", "")
    return normalized


def _require_viewable_content(repo: RadarLinksRepository, code: str, viewer_session: str | None) -> dict[str, Any]:
    link = repo.get_link_by_code(code)
    if not link or not bool(link.get("enabled", True)):
        raise NotFoundError("radar content not found")
    target_type = normalize_target_type(str(link.get("target_type") or "link"))
    if target_type not in {"image", "pdf"}:
        raise NotFoundError("radar content not found")
    session = verify_viewer_session(viewer_session, code=str(link["code"]), secret_key=_secret_key())
    identity = dict(session.get("identity") or {})
    if bool(link.get("auth_required")) and not str(identity.get("unionid") or "").strip():
        raise ContractError("unionid_required: radar viewer session is missing UnionID")
    return {**link, "_viewer_identity": identity}


def _view_event_payload(link: dict[str, Any], *, stage: str, request_meta: dict[str, Any]) -> dict[str, Any]:
    identity = dict(link.get("_viewer_identity") or {})
    return {
        "link_id": int(link["id"]),
        "code": str(link.get("code") or ""),
        "target_type_snapshot": normalize_target_type(str(link.get("target_type") or "link")),
        "stage": stage,
        "openid": str(identity.get("openid") or ""),
        "unionid": str(identity.get("unionid") or ""),
        "external_userid": str(identity.get("external_userid") or ""),
        "source_channel": str(link.get("source_channel") or ""),
        "campaign_id": str(link.get("campaign_id") or ""),
        "staff_id": str(link.get("staff_id") or ""),
        "source_channel_snapshot": str(link.get("source_channel") or ""),
        "campaign_id_snapshot": str(link.get("campaign_id") or ""),
        "staff_id_snapshot": str(link.get("staff_id") or ""),
        "user_agent": request_meta.get("user_agent", ""),
        "ip_hash": hash_ip(str(request_meta.get("ip") or ""), secret_key=_secret_key()),
        "referer": request_meta.get("referer", ""),
        "query_params_json": request_meta.get("query_params_json") if isinstance(request_meta.get("query_params_json"), dict) else {},
    }
