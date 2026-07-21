from __future__ import annotations

import base64
import binascii
import csv
from datetime import datetime, timezone
import html
import io
import json
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.pii_audit import set_pii_audit_result_count
from aicrm_next.shared.public_url import canonical_public_base_url
from aicrm_next.shared.repository_provider import RepositoryProviderError
from aicrm_next.shared.runtime_settings import runtime_setting
from aicrm_next.shared.sidebar_access import sidebar_owner_context_from_request
from aicrm_next.shared.wechat_identity_page import wechat_identity_failure_response
from aicrm_next.shared.wechat_h5_session import is_wechat_browser

from .application import (
    CompleteRadarOAuthCallbackCommand,
    CompleteRadarPdfUploadCommand,
    CreateRadarLinkCommand,
    ExportRadarLinkEventsQuery,
    GetRadarImageManifestQuery,
    GetRadarImageVariantResourceQuery,
    GetRadarContentResourceQuery,
    GetRadarPdfBytesQuery,
    GetRadarPdfPreviewManifestQuery,
    GetRadarPdfPreviewPageQuery,
    GetRadarPdfProcessingStatusQuery,
    GetRadarLinkQuery,
    GetRadarLinkNewOptionsQuery,
    GetRadarLinkShareQuery,
    GetRadarLinkStatsQuery,
    GetRadarViewerPageQuery,
    InitiateRadarPdfUploadCommand,
    ListExternalRadarClicksQuery,
    ListExternalRadarLinkMappingsQuery,
    ListRadarLinkEventsQuery,
    ListRadarLinksQuery,
    ListSidebarRadarLinksQuery,
    ProcessRadarPdfPreviewCommand,
    RecordRadarContentEventCommand,
    ResolveRadarLandingQuery,
    SetRadarLinkEnabledCommand,
    StartRadarOAuthQuery,
    UpdateRadarLinkCommand,
    UploadRadarPdfPartCommand,
)
from aicrm_next.media_library.application import UploadAttachmentCommand, UploadImageCommand
from .domain import verify_radar_state
from .dto import RadarLinkCreateRequest, RadarLinkUpdateRequest

router = APIRouter()
RADAR_VIEWER_COOKIE = "aicrm_radar_viewer"
EXTERNAL_RADAR_CLICK_SOURCE = "external_radar_clicks"
EXTERNAL_RADAR_LINK_SOURCE = "external_radar_links"


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, RepositoryProviderError):
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "degraded": True,
                "source_status": "production_unavailable",
                "error_code": "radar_links_repository_unavailable",
                "detail": str(exc),
            },
        ) from exc
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ContractError):
        text = str(exc)
        error_code = text.split(":", 1)[0] if ":" in text else "contract_error"
        raise HTTPException(status_code=400, detail={"ok": False, "error_code": error_code, "message": text}) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _external_error(*, error_code: str, message: str, status_code: int, source_status: str) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "route_owner": "ai_crm_next",
            "source_status": source_status,
            "fallback_used": False,
        },
        status_code=status_code,
    )


def _external_integer(
    value: str | None,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = int(token)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be greater than or equal to {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be less than or equal to {maximum}")
    return parsed


def _external_timestamp(value: str | None, name: str) -> datetime | None:
    parsed = _external_integer(value, name, minimum=0)
    if parsed is None:
        return None
    if parsed > 9_999_999_999:
        raise ValueError(f"{name} must be a Unix timestamp in seconds, not milliseconds")
    return datetime.fromtimestamp(parsed, tz=timezone.utc)


def _decode_external_cursor(cursor: str | None, key: str) -> int | None:
    token = str(cursor or "").strip()
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {key}:
            raise ValueError("cursor payload is invalid")
        value = int(payload[key])
        if value <= 0:
            raise ValueError("cursor value is invalid")
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("cursor is invalid") from exc


def _encode_external_cursor(key: str, value: int | None) -> str:
    if value is None:
        return ""
    payload = json.dumps({key: int(value)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _external_filters(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in {None, ""}}


@router.get("/api/sidebar/v2/radar-links", name="api.sidebar_v2_radar_links")
def list_sidebar_radar_links(
    request: Request,
) -> JSONResponse:
    sidebar_owner_context_from_request(request)
    try:
        payload = ListSidebarRadarLinksQuery().execute(
            base_url=canonical_public_base_url(request),
        )
    except (ContractError, RepositoryProviderError) as exc:
        _raise_http(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "degraded": True,
                "source_status": "production_unavailable",
                "error_code": "sidebar_radar_links_unavailable",
                "detail": str(exc),
            },
        ) from exc
    return JSONResponse(
        jsonable_encoder({**payload, "route_owner": "ai_crm_next"}),
        headers={
            "X-AICRM-Route-Owner": "ai_crm_next",
            "X-AICRM-Fallback-Used": "false",
            "X-AICRM-Real-External-Call-Executed": "false",
            "Cache-Control": "no-store, max-age=0",
        },
    )


def _request_meta(request: Request) -> dict[str, Any]:
    blocked_query_keys = {"openid", "unionid", "external_userid", "code", "state"}
    return {
        "user_agent": str(request.headers.get("user-agent") or ""),
        "ip": request.client.host if request.client else "",
        "referer": str(request.headers.get("referer") or ""),
        "query_params_json": {key: value for key, value in request.query_params.items() if key.strip().lower() not in blocked_query_keys},
    }


def _redirect_with_viewer_cookie(url: str, token: str = "") -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=302)
    if token:
        response.set_cookie(
            RADAR_VIEWER_COOKIE,
            token,
            max_age=2 * 60 * 60,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    return response


def _radar_oauth_return_url(state: str | None) -> str:
    try:
        context = verify_radar_state(state, secret_key=runtime_setting("SECRET_KEY"))
    except Exception:
        return "/"
    code = str(context.get("code") or "").strip()
    return f"/r/{code}" if code else "/"


def _radar_oauth_error_response(exc: Exception, *, state: str | None = None) -> HTMLResponse:
    detail = str(exc)
    if "unionid_required" in detail:
        return_url = _radar_oauth_return_url(state)
        retry_url = f"/api/h5/radar/oauth/start?state={state}" if state else return_url
        return wechat_identity_failure_response(
            title="内容授权未完成",
            message="微信未返回 UnionID，暂时无法访问受保护内容。",
            retry_url=retry_url,
            return_url=return_url,
            status_code=409,
        )
    if isinstance(exc, RepositoryProviderError):
        status_code = 503
        message = "当前内容授权服务暂不可用，请稍后重试。"
    elif isinstance(exc, NotFoundError):
        status_code = 404
        message = "当前内容不存在或已下线。"
    else:
        status_code = 400
        message = "内容授权未完成，请重新打开链接。"
    if "production mode is not enabled" in detail:
        message = "当前微信授权配置未完成，请联系管理员。"
    return_url = html.escape(_radar_oauth_return_url(state), quote=True)
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>内容授权未完成</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f6f7fb; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif; }}
    main {{ width: min(100%, 420px); padding: 28px 22px; border: 1px solid #e5e7eb; border-radius: 18px; background: #fff; text-align: center; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08); }}
    h1 {{ margin: 0 0 12px; font-size: 22px; line-height: 1.35; }}
    p {{ margin: 0; color: #6b7280; font-size: 15px; line-height: 1.7; }}
    a {{ display: inline-flex; align-items: center; justify-content: center; margin-top: 22px; min-height: 44px; padding: 0 20px; border-radius: 999px; background: #2563eb; color: #fff; font-weight: 700; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <h1>内容授权未完成</h1>
    <p>{html.escape(message)}</p>
    <a href="{return_url}">返回内容链接</a>
  </main>
</body>
</html>"""
    return HTMLResponse(body, status_code=status_code)


@router.get("/api/external/radar-clicks")
def list_external_radar_clicks(
    request: Request,
    mobile: str | None = Query(None, description="手机号"),
    unionid: str | None = Query(None, description="微信 unionid"),
    radar_id: str | None = Query(None, description="雷达数字 ID"),
    radar_code: str | None = Query(None, description="雷达代码"),
    clicked_from: str | None = Query(None, description="点击开始秒级 Unix 时间戳"),
    clicked_to: str | None = Query(None, description="点击结束秒级 Unix 时间戳"),
    limit: str = Query("100", description="分页条数，最大 500"),
    cursor: str | None = Query(None, description="下一页游标"),
) -> JSONResponse:
    try:
        start_at = _external_timestamp(clicked_from, "clicked_from")
        end_at = _external_timestamp(clicked_to, "clicked_to")
        parsed_radar_id = _external_integer(radar_id, "radar_id", minimum=1)
        parsed_limit = _external_integer(limit, "limit", minimum=1, maximum=500)
        if start_at is not None and end_at is not None and start_at > end_at:
            raise ValueError("clicked_from must be earlier than or equal to clicked_to")
        payload = ListExternalRadarClicksQuery()(
            mobile=str(mobile or "").strip(),
            unionid=str(unionid or "").strip(),
            radar_id=parsed_radar_id,
            radar_code=str(radar_code or "").strip(),
            clicked_from=start_at,
            clicked_to=end_at,
            before_event_id=_decode_external_cursor(cursor, "event_id"),
            limit=int(parsed_limit or 100),
        )
    except ValueError as exc:
        return _external_error(error_code="invalid_request", message=str(exc), status_code=400, source_status=EXTERNAL_RADAR_CLICK_SOURCE)
    except RepositoryProviderError as exc:
        return _external_error(
            error_code="production_unavailable",
            message=str(exc),
            status_code=503,
            source_status="production_unavailable",
        )
    next_before_event_id = payload.pop("next_before_event_id", None)
    payload["next_cursor"] = _encode_external_cursor("event_id", next_before_event_id)
    payload["filters"] = _external_filters(
        mobile=str(mobile or "").strip(),
        unionid=str(unionid or "").strip(),
        radar_id=parsed_radar_id,
        radar_code=str(radar_code or "").strip(),
        clicked_from=_external_integer(clicked_from, "clicked_from", minimum=0),
        clicked_to=_external_integer(clicked_to, "clicked_to", minimum=0),
    )
    set_pii_audit_result_count(request, len(payload["items"]))
    return JSONResponse(jsonable_encoder(payload), headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/api/external/radar-links")
def list_external_radar_link_mappings(
    radar_id: str | None = Query(None, description="雷达数字 ID"),
    radar_code: str | None = Query(None, description="雷达代码"),
    limit: str = Query("100", description="分页条数，最大 500"),
    cursor: str | None = Query(None, description="下一页游标"),
) -> JSONResponse:
    try:
        parsed_radar_id = _external_integer(radar_id, "radar_id", minimum=1)
        parsed_limit = _external_integer(limit, "limit", minimum=1, maximum=500)
        payload = ListExternalRadarLinkMappingsQuery()(
            radar_id=parsed_radar_id,
            radar_code=str(radar_code or "").strip(),
            before_link_id=_decode_external_cursor(cursor, "radar_id"),
            limit=int(parsed_limit or 100),
        )
    except ValueError as exc:
        return _external_error(error_code="invalid_request", message=str(exc), status_code=400, source_status=EXTERNAL_RADAR_LINK_SOURCE)
    except RepositoryProviderError as exc:
        return _external_error(
            error_code="production_unavailable",
            message=str(exc),
            status_code=503,
            source_status="production_unavailable",
        )
    next_before_link_id = payload.pop("next_before_link_id", None)
    payload["next_cursor"] = _encode_external_cursor("radar_id", next_before_link_id)
    payload["filters"] = _external_filters(radar_id=parsed_radar_id, radar_code=str(radar_code or "").strip())
    return JSONResponse(jsonable_encoder(payload))


@router.get("/api/admin/radar-links")
def list_radar_links(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    try:
        return ListRadarLinksQuery()(base_url=_base_url(request), limit=limit, offset=offset)
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/new/options")
def get_radar_link_new_options() -> dict[str, Any]:
    try:
        return GetRadarLinkNewOptionsQuery()()
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links")
def create_radar_link(request: Request, payload: RadarLinkCreateRequest) -> dict[str, Any]:
    try:
        return CreateRadarLinkCommand()(payload, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}")
def get_radar_link(request: Request, link_id: int) -> dict[str, Any]:
    try:
        return GetRadarLinkQuery()(link_id, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.patch("/api/admin/radar-links/{link_id}")
def update_radar_link(request: Request, link_id: int, payload: RadarLinkUpdateRequest) -> dict[str, Any]:
    try:
        return UpdateRadarLinkCommand()(link_id, payload, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}/share")
def get_radar_link_share(request: Request, link_id: int) -> dict[str, Any]:
    try:
        return GetRadarLinkShareQuery()(link_id, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/{link_id}/enable")
def enable_radar_link(request: Request, link_id: int) -> dict[str, Any]:
    try:
        return SetRadarLinkEnabledCommand()(link_id, enabled=True, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/{link_id}/disable")
def disable_radar_link(request: Request, link_id: int) -> dict[str, Any]:
    try:
        return SetRadarLinkEnabledCommand()(link_id, enabled=False, base_url=_base_url(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}/stats")
def get_radar_link_stats(link_id: int) -> dict[str, Any]:
    try:
        return GetRadarLinkStatsQuery()(link_id)
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}/events")
def list_radar_link_events(
    request: Request,
    link_id: int,
    limit: int = 100,
    offset: int = 0,
    stage: str = "",
    start_at: str = "",
    end_at: str = "",
) -> dict[str, Any]:
    try:
        return ListRadarLinkEventsQuery()(
            link_id,
            limit=limit,
            offset=offset,
            stage=stage,
            start_at=start_at,
            end_at=end_at,
            base_url=_base_url(request),
        )
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}/events/export")
def export_radar_link_events(
    link_id: int,
    request: Request,
    start_at: str = "",
    end_at: str = "",
) -> Response:
    try:
        payload = ExportRadarLinkEventsQuery()(link_id, start_at=start_at, end_at=end_at)
    except Exception as exc:
        _raise_http(exc)
    set_pii_audit_result_count(request, len(payload["items"]))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["unionid", "external_userid", "created_at"])
    writer.writeheader()
    writer.writerows(payload["items"])
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="radar_link_{link_id}_click_events.csv"'},
    )


@router.post("/api/admin/radar-links/{link_id}/pdf/reprocess")
def reprocess_radar_pdf_preview(link_id: int) -> dict[str, Any]:
    try:
        return ProcessRadarPdfPreviewCommand()(link_id)
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/admin/radar-links/{link_id}/pdf/processing-status")
def get_radar_pdf_processing_status(link_id: int) -> dict[str, Any]:
    try:
        return GetRadarPdfProcessingStatusQuery()(link_id)
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/pdf-uploads/initiate")
def initiate_radar_pdf_upload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return InitiateRadarPdfUploadCommand()(
            file_name=str(payload.get("file_name") or ""),
            file_size=int(payload.get("file_size") or 0),
            mime_type=str(payload.get("mime_type") or ""),
        )
    except Exception as exc:
        _raise_http(exc)


@router.put("/api/admin/radar-links/pdf-uploads/{upload_id}/parts/{part_no}")
async def upload_radar_pdf_part(upload_id: str, part_no: int, request: Request) -> dict[str, Any]:
    try:
        return UploadRadarPdfPartCommand()(upload_id, part_no, content=await request.body())
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/pdf-uploads/{upload_id}/complete")
def complete_radar_pdf_upload(upload_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    try:
        return CompleteRadarPdfUploadCommand()(upload_id, name=str(payload.get("name") or ""), tags=payload.get("tags") or "radar_content")
    except Exception as exc:
        _raise_http(exc)


@router.get("/r/{code}")
def radar_public_redirect(request: Request, code: str):
    try:
        result = ResolveRadarLandingQuery()(
            code,
            request_meta=_request_meta(request),
            viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE),
        )
    except Exception as exc:
        _raise_http(exc)
    if result["action"] == "oauth_start":
        if not is_wechat_browser(request):
            return wechat_identity_failure_response(
                title="请在微信中打开",
                message="该内容需要微信身份验证，请复制链接后在微信中打开。",
                status_code=403,
            )
        return RedirectResponse(url=result["oauth_start_url"], status_code=302)
    return _redirect_with_viewer_cookie(str(result["redirect_url"]), str(result.get("viewer_session_token") or ""))


@router.get("/api/h5/radar/oauth/start")
def radar_oauth_start(
    state: str | None = None,
    code: str | None = None,
    openid: str | None = None,
    unionid: str | None = None,
    external_userid: str | None = None,
):
    try:
        result = StartRadarOAuthQuery()(state=state, code=code, openid=openid, unionid=unionid, external_userid=external_userid)
    except Exception as exc:
        return _radar_oauth_error_response(exc, state=state)
    return _redirect_with_viewer_cookie(str(result["redirect_url"]), str(result.get("viewer_session_token") or ""))


@router.get("/radar/view/{code}")
def radar_content_view(request: Request, code: str):
    try:
        result = GetRadarViewerPageQuery()(code, viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE), request_meta=_request_meta(request))
    except Exception as exc:
        _raise_http(exc)
    radar_link = result["radar_link"]
    title = html.escape(str(radar_link.get("title") or "内容预览"), quote=True)
    target_type = str(result.get("target_type") or "")
    if target_type == "image":
        manifest_url = html.escape(f"/api/h5/radar-contents/{code}/image/manifest", quote=True)
        body = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f6f7fb; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 14px 16px; border-bottom: 1px solid #e5e7eb; background: rgba(255, 255, 255, 0.96); font-size: 16px; font-weight: 800; }}
    main {{ min-height: calc(100vh - 50px); display: flex; align-items: flex-start; justify-content: center; padding: 0; }}
    img {{ display: block; width: 100%; max-width: 960px; height: auto; background: #fff; }}
    .fallback {{ display: none; width: 100%; padding: 40px 18px; color: #6b7280; text-align: center; }}
  </style>
</head>
<body>
  <header>{title}</header>
  <main>
    <img data-image alt="{title}" loading="lazy" onerror="this.style.display='none';document.querySelector('.fallback').style.display='block';">
    <div class="fallback">图片加载失败，请稍后重试</div>
  </main>
  <script>
    (async function () {{
      try {{
        var response = await fetch("{manifest_url}", {{credentials: "same-origin", cache: "no-store"}});
        if (!response.ok) throw new Error("manifest unavailable");
        var manifest = await response.json();
        var variants = manifest.variants || {{}};
        var image = document.querySelector("[data-image]");
        image.src = variants.mobile_1080 || variants.large_1440 || variants.original || "/api/h5/radar-contents/{code}/image";
        if (variants.large_1440) image.srcset = (variants.mobile_1080 || image.src) + " 1080w, " + variants.large_1440 + " 1440w";
      }} catch (error) {{
        document.querySelector("[data-image]").style.display = "none";
        document.querySelector(".fallback").style.display = "block";
      }}
    }})();
  </script>
</body>
</html>
"""
    else:
        manifest_url = html.escape(f"/api/h5/radar-contents/{code}/manifest", quote=True)
        body = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f6f7fb; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 12px 16px; border-bottom: 1px solid #e5e7eb; background: rgba(255, 255, 255, 0.96); font-size: 16px; font-weight: 800; }}
    .viewer {{ min-height: calc(100vh - 48px); padding: 10px 0 24px; background: #eef1f6; }}
    .page-wrap {{ width: calc(100vw - 16px); max-width: 860px; min-height: 70vh; margin: 0 auto 10px; background: #fff; box-shadow: 0 1px 6px rgba(15, 23, 42, .12); display: grid; place-items: center; color: #6b7280; }}
    .page-wrap img {{ display: block; width: 100%; height: auto; }}
    .counter {{ padding: 10px 16px 24px; color: #6b7280; text-align: center; }}
    .state {{ padding: 40px 18px; color: #6b7280; text-align: center; }}
    .state[hidden] {{ display: none; }}
  </style>
</head>
<body>
  <header>{title}</header>
  <main class="viewer">
    <div class="state" data-pdf-state>内容加载中...</div>
    <div data-pdf-pages></div>
    <div class="counter" data-pdf-counter hidden></div>
  </main>
  <script>
    (function () {{
      var state = document.querySelector("[data-pdf-state]");
      var pages = document.querySelector("[data-pdf-pages]");
      var counter = document.querySelector("[data-pdf-counter]");
      var manifest = null;
      var loaded = {{}};
      var pollCount = 0;
      function showState(message) {{
        if (state) {{
          state.hidden = false;
          state.textContent = message;
        }}
      }}
      function updateCounter(pageNo) {{
        if (!counter || !manifest) return;
        counter.hidden = false;
        counter.textContent = "第 " + pageNo + " / " + manifest.page_count + " 页";
      }}
      function preload(pageNo) {{
        if (!manifest || pageNo > manifest.page_count || loaded["preload-" + pageNo]) return;
        var page = (manifest.pages || []).find(function (item) {{ return Number(item.page_no) === pageNo; }});
        if (!page) return;
        loaded["preload-" + pageNo] = true;
        var img = new Image();
        img.src = page.url;
      }}
      function loadPage(wrapper) {{
        var pageNo = Number(wrapper.dataset.pageNo || "0");
        if (!pageNo || loaded[pageNo]) return;
        loaded[pageNo] = true;
        var img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "PDF 第 " + pageNo + " 页";
        img.src = wrapper.dataset.src;
        img.onload = function () {{ wrapper.textContent = ""; wrapper.appendChild(img); updateCounter(pageNo); preload(pageNo + 1); }};
        img.onerror = function () {{ wrapper.textContent = "该页加载失败"; }};
      }}
      function renderPages() {{
        pages.innerHTML = "";
        (manifest.pages || []).forEach(function (page) {{
          var wrapper = document.createElement("div");
          wrapper.className = "page-wrap";
          wrapper.dataset.pageNo = page.page_no;
          wrapper.dataset.src = page.url;
          wrapper.textContent = "第 " + page.page_no + " 页";
          pages.appendChild(wrapper);
        }});
        if (state) state.hidden = true;
        var observer = new IntersectionObserver(function (entries) {{
          entries.forEach(function (entry) {{ if (entry.isIntersecting) loadPage(entry.target); }});
        }}, {{rootMargin: "480px 0px"}});
        document.querySelectorAll(".page-wrap").forEach(function (node) {{ observer.observe(node); }});
        var first = document.querySelector(".page-wrap");
        if (first) loadPage(first);
      }}
      async function loadManifest() {{
        var response = await fetch("{manifest_url}", {{credentials: "same-origin", cache: "no-store"}});
        if (!response.ok) throw new Error("manifest unavailable");
        manifest = await response.json();
        if (manifest.processing_status === "ready") {{
          renderPages();
        }} else if (manifest.processing_status === "processing" || manifest.processing_status === "pending") {{
          showState("内容处理中，请稍后刷新");
          if (pollCount < 15) {{
            pollCount += 1;
            window.setTimeout(loadManifest, 2000);
          }}
        }} else {{
          showState("PDF 预览生成失败，请联系管理员");
        }}
      }}
      loadManifest().catch(function () {{ showState("内容处理中，请稍后刷新"); }});
    }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=body)


@router.get("/api/h5/radar-contents/{code}/image")
def radar_content_image(request: Request, code: str):
    try:
        result = GetRadarContentResourceQuery()(
            code, target_type="image", viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE), request_meta=_request_meta(request)
        )
    except Exception as exc:
        _raise_http(exc)
    return Response(
        content=result["content"],
        media_type=str(result.get("mime_type") or "image/png"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/h5/radar-contents/{code}/image/manifest")
def radar_content_image_manifest(request: Request, code: str) -> dict[str, Any]:
    try:
        return GetRadarImageManifestQuery()(code, viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE), request_meta=_request_meta(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/h5/radar-contents/{code}/image/variants/{variant_key}")
def radar_content_image_variant(request: Request, code: str, variant_key: str):
    try:
        result = GetRadarImageVariantResourceQuery()(
            code,
            variant_key,
            viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE),
            request_meta=_request_meta(request),
        )
    except Exception as exc:
        _raise_http(exc)
    return Response(
        content=result["content"],
        media_type=str(result.get("mime_type") or "image/png"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/h5/radar-contents/{code}/manifest")
def radar_content_pdf_manifest(request: Request, code: str) -> dict[str, Any]:
    try:
        return GetRadarPdfPreviewManifestQuery()(code, viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE), request_meta=_request_meta(request))
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/h5/radar-contents/{code}/pdf/pages/{page_no}")
def radar_content_pdf_page(request: Request, code: str, page_no: int):
    try:
        result = GetRadarPdfPreviewPageQuery()(
            code,
            page_no,
            viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE),
            request_meta=_request_meta(request),
        )
    except Exception as exc:
        _raise_http(exc)
    return Response(
        content=result["content"],
        media_type=str(result.get("mime_type") or "image/jpeg"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/h5/radar-contents/{code}/pdf")
def radar_content_pdf(request: Request, code: str, range_header: str | None = Header(default=None, alias="Range")):
    try:
        result = GetRadarPdfBytesQuery()(code, viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE), request_meta=_request_meta(request))
    except Exception as exc:
        _raise_http(exc)
    file_name = str(result.get("file_name") or "content.pdf").replace('"', "")
    return _pdf_response(bytes(result["content"]), file_name=file_name, range_header=range_header)


def _pdf_response(content: bytes, *, file_name: str, range_header: str | None) -> Response:
    base_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Cache-Control": "private, max-age=300",
    }
    total = len(content)
    value = str(range_header or "").strip()
    if not value:
        return Response(content=content, media_type="application/pdf", headers={**base_headers, "Content-Length": str(total)})
    if not value.startswith("bytes=") or "," in value:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})
    start_text, _, end_text = value[len("bytes=") :].partition("-")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else total - 1
        else:
            suffix = int(end_text)
            start = max(0, total - suffix)
            end = total - 1
    except ValueError:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})
    if start < 0 or end < start or start >= total:
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{total}"})
    end = min(end, total - 1)
    chunk = content[start : end + 1]
    return Response(
        content=chunk,
        status_code=206,
        media_type="application/pdf",
        headers={
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(chunk)),
        },
    )


@router.post("/api/h5/radar-contents/{code}/events")
def radar_content_event(request: Request, code: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return RecordRadarContentEventCommand()(
            code,
            payload=payload,
            viewer_session=request.cookies.get(RADAR_VIEWER_COOKIE),
            request_meta=_request_meta(request),
        )
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/upload-image")
async def upload_radar_image(image: UploadFile = File(...), name: str = Form(""), tags: str = Form("")) -> dict[str, Any]:
    try:
        return UploadImageCommand()(
            file_bytes=await image.read(),
            file_name=image.filename or "image.png",
            content_type=image.content_type or "application/octet-stream",
            name=name,
            tags=tags,
            category="radar_content",
        )
    except Exception as exc:
        _raise_http(exc)


@router.post("/api/admin/radar-links/upload-pdf")
async def upload_radar_pdf(pdf: UploadFile = File(...), name: str = Form(""), tags: str = Form("")) -> dict[str, Any]:
    try:
        return UploadAttachmentCommand()(
            file_bytes=await pdf.read(),
            file_name=pdf.filename or "content.pdf",
            content_type=pdf.content_type or "application/octet-stream",
            name=name,
            tags=tags,
        )
    except Exception as exc:
        _raise_http(exc)


@router.get("/api/h5/radar/oauth/callback")
def radar_oauth_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    openid: str | None = None,
    unionid: str | None = None,
    external_userid: str | None = None,
):
    try:
        result = CompleteRadarOAuthCallbackCommand()(
            state=state,
            code=code,
            openid=openid,
            unionid=unionid,
            external_userid=external_userid,
            request_meta=_request_meta(request),
        )
    except Exception as exc:
        return _radar_oauth_error_response(exc, state=state)
    return _redirect_with_viewer_cookie(str(result["redirect_url"]), str(result.get("viewer_session_token") or ""))
