from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.shared.sync_request import read_request_body

from .refresh_intents import AudienceRefreshIntentService
from .schemas import (
    InboundWebhookRequest,
    OutboundSubscriptionCreateRequest,
    OutboundSubscriptionUpdateRequest,
    PackageCreateRequest,
    PackagePublishRequest,
    PackageVersionCreateRequest,
    PreviewRequest,
    RefreshRequest,
    SourceDirtyRequest,
)
from .service import AudiencePackageService
from .sql_catalog import schema_catalog_payload
from .test_agent_service import AudienceTestAgentService
from .webhook_service import AudienceInboundWebhookService

router = APIRouter()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(payload),
        status_code=status_code,
        headers={
            "X-AICRM-Route-Owner": "ai_crm_next",
            "X-AICRM-Real-External-Call-Executed": "true" if payload.get("real_external_call_executed") else "false",
        },
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


@router.get("/api/ai/audience/schema-catalog", name="api.ai_audience_schema_catalog")
async def schema_catalog(request: Request) -> JSONResponse:
    return _json({"ok": True, **schema_catalog_payload()})


@router.get("/api/ai/audience/packages", name="api.ai_audience_list_packages")
async def list_packages(request: Request) -> JSONResponse:
    return _json(AudiencePackageService().list_packages())


@router.post("/api/ai/audience/packages", name="api.ai_audience_create_package")
async def create_package(request: Request) -> JSONResponse:
    try:
        payload = PackageCreateRequest(**await _body(request))
        result = AudiencePackageService().create_package(payload)
        return _json(result)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/ai/audience/packages/{package_id}", name="api.ai_audience_get_package")
async def get_package(package_id: int, request: Request) -> JSONResponse:
    result = AudiencePackageService().get_package(package_id)
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/packages/{package_id}/versions", name="api.ai_audience_create_version")
async def create_version(package_id: int, request: Request) -> JSONResponse:
    try:
        result = AudiencePackageService().create_version(package_id, PackageVersionCreateRequest(**await _body(request)))
        return _json(result, status_code=200 if result.get("ok") else 400)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/ai/audience/packages/{package_id}/preview", name="api.ai_audience_preview_package")
async def preview_package(package_id: int, request: Request) -> JSONResponse:
    try:
        payload = PreviewRequest(**await _body(request))
        result = AudiencePackageService().preview(package_id, payload)
        return _json(result, status_code=200 if result.get("ok") else 400)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/ai/audience/packages/{package_id}/publish", name="api.ai_audience_publish_package")
async def publish_package(package_id: int, request: Request) -> JSONResponse:
    try:
        payload = PackagePublishRequest(**await _body(request))
        result = AudiencePackageService().publish(package_id, version_id=payload.version_id)
        return _json(result, status_code=200 if result.get("ok") else 400)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/ai/audience/packages/{package_id}/pause", name="api.ai_audience_pause_package")
async def pause_package(package_id: int, request: Request) -> JSONResponse:
    payload = await _body(request)
    result = AudiencePackageService().pause(package_id, reason=_text(payload.get("reason")))
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/packages/{package_id}/archive", name="api.ai_audience_archive_package")
async def archive_package(package_id: int, request: Request) -> JSONResponse:
    payload = await _body(request)
    result = AudiencePackageService().archive(package_id, reason=_text(payload.get("reason")))
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/packages/{package_id}/refresh", name="api.ai_audience_refresh_package")
async def refresh_package(package_id: int, request: Request) -> JSONResponse:
    try:
        payload = RefreshRequest(**await _body(request))
        idempotency_key = _text(request.headers.get("X-Idempotency-Key"))
        execution_id = _text(request.headers.get("X-AICRM-Execution-Id"))
        parent_execution_id = _text(request.headers.get("X-AICRM-Parent-Execution-Id"))
        result = AudienceRefreshIntentService().request_package_refresh(
            package_id,
            refresh_kind="manual" if payload.run_type not in {"daily", "snapshot", "snapshot_current"} else "daily",
            source_event_key=idempotency_key,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            params=payload.params,
            row_limit=payload.row_limit,
        )
        result = {"accepted": bool(result.get("ok")), **result}
        return _json(result, status_code=200 if result.get("ok") else 400)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/ai/audience/ticks/incremental", name="api.ai_audience_incremental_tick")
async def incremental_tick(request: Request) -> JSONResponse:
    return _json(AudiencePackageService().emit_tick("incremental"))


@router.post("/api/ai/audience/ticks/daily", name="api.ai_audience_daily_tick")
async def daily_tick(request: Request) -> JSONResponse:
    return _json(AudiencePackageService().emit_tick("daily"))


@router.post("/api/ai/audience/source-dirty", name="api.ai_audience_source_dirty")
async def source_dirty(request: Request) -> JSONResponse:
    try:
        payload = SourceDirtyRequest(**await _body(request))
        return _json(AudiencePackageService().emit_source_changed(payload.model_dump()))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/ai/audience/packages/{package_id}/outbound-subscriptions", name="api.ai_audience_list_outbound_subscriptions")
async def list_subscriptions(package_id: int, request: Request) -> JSONResponse:
    return _json(AudiencePackageService().list_subscriptions(package_id))


@router.post("/api/ai/audience/packages/{package_id}/outbound-subscriptions", name="api.ai_audience_create_outbound_subscription")
async def create_subscription(package_id: int, request: Request) -> JSONResponse:
    try:
        payload = OutboundSubscriptionCreateRequest(**await _body(request))
        return _json(AudiencePackageService().create_subscription(package_id, payload.model_dump()))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.patch("/api/ai/audience/outbound-subscriptions/{subscription_id}", name="api.ai_audience_update_outbound_subscription")
async def update_subscription(subscription_id: int, request: Request) -> JSONResponse:
    payload = OutboundSubscriptionUpdateRequest(**await _body(request))
    result = AudiencePackageService().update_subscription(subscription_id, payload.model_dump(exclude_none=True))
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/outbound-subscriptions/{subscription_id}/pause", name="api.ai_audience_pause_outbound_subscription")
async def pause_subscription(subscription_id: int, request: Request) -> JSONResponse:
    result = AudiencePackageService().update_subscription(subscription_id, {"status": "paused"})
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/packages/{package_key}/webhook", name="api.ai_audience_inbound_webhook")
def inbound_webhook(package_key: str, request: Request) -> JSONResponse:
    raw_body = read_request_body(request)
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        parsed = InboundWebhookRequest(**(payload if isinstance(payload, dict) else {}))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)
    result = AudienceInboundWebhookService().handle(
        package_key,
        parsed.model_dump(),
        raw_body=raw_body,
    )
    return _json(result, status_code=200 if result.get("ok") else 404)


@router.post("/api/ai/audience/test-agent/webhook", name="api.ai_audience_test_agent_webhook")
def test_agent_webhook(request: Request) -> JSONResponse:
    raw_body = read_request_body(request)
    try:
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            payload = {}
        result = AudienceTestAgentService().handle(
            payload,
            headers=dict(request.headers),
        )
        return _json(result, status_code=int(result.get("status_code") or (200 if result.get("ok") else 400)))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/ai/audience/packages/{package_id}/runs", name="api.ai_audience_package_runs")
async def package_runs(package_id: int, request: Request) -> JSONResponse:
    return _json(AudiencePackageService().diagnostics(package_id, "runs"))


@router.get("/api/ai/audience/packages/{package_id}/members", name="api.ai_audience_package_members")
async def package_members(package_id: int, request: Request) -> JSONResponse:
    return _json(AudiencePackageService().diagnostics(package_id, "members"))


@router.get("/api/ai/audience/packages/{package_id}/events", name="api.ai_audience_package_events")
async def package_events(package_id: int, request: Request) -> JSONResponse:
    return _json(AudiencePackageService().diagnostics(package_id, "events"))


@router.get("/api/ai/audience/packages/{package_id}/external-effects", name="api.ai_audience_package_external_effects")
async def package_external_effects(package_id: int, request: Request) -> JSONResponse:
    return _json(AudiencePackageService().external_effects(package_id))


@router.get("/api/ai/audience/health", name="api.ai_audience_health")
async def health(request: Request) -> JSONResponse:
    return _json(AudiencePackageService().health())
