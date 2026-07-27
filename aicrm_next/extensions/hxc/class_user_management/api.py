from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from aicrm_next.shared.pii_audit import set_pii_audit_result_count
from aicrm_next.shared.runtime import production_data_ready

router = APIRouter()

_COMMON_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}
_CSV_HEADERS = dict(_COMMON_HEADERS)
_CSV_HEADERS["X-AICRM-External-Storage-Executed"] = "false"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _common_payload(source_status: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


def _options(source_status: str) -> JSONResponse:
    payload = _common_payload(source_status)
    payload.update({"allowed": True})
    return JSONResponse(payload, headers=_COMMON_HEADERS)


async def _payload(request: Request) -> dict[str, Any]:
    if request.method.upper() == "GET":
        return dict(request.query_params)
    if "application/json" in _text(request.headers.get("content-type")).lower():
        body = await request.json()
        return dict(body or {}) if isinstance(body, dict) else {}
    form = await request.form()
    return dict(form)


@router.api_route("/api/admin/class-user-management/export", methods=["GET", "POST", "OPTIONS"])
async def class_user_management_export(request: Request) -> Response:
    if request.method.upper() == "OPTIONS":
        return _options("next_class_user_management_export")
    if production_data_ready():
        payload = _common_payload("production_unavailable")
        payload.update(
            {
                "ok": False,
                "error": "class_user_management_export_requires_real_read_model",
                "read_model_status": "unavailable",
            }
        )
        return JSONResponse(payload, status_code=503, headers=_COMMON_HEADERS)

    payload = await _payload(request)
    signup_status = _text(payload.get("signup_status"))
    rows = [
        {
            "customer_name": "Class User Local",
            "mobile": "13800138000",
            "follow_user_display_name": "ai_crm_next",
            "current_tag_name": signup_status or "next_business",
            "external_userid": "wx_ext_001",
            "updated_at": _now(),
        }
    ]
    set_pii_audit_result_count(request, len(rows))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["route_owner", "fallback_used", "real_external_call_executed", "export_generated"])
    writer.writerow(["ai_crm_next", "false", "false", "local_only"])
    writer.writerow([])
    writer.writerow(["客户昵称", "手机号", "跟进人", "当前状态标签", "external_userid", "更新时间"])
    for row in rows:
        writer.writerow(
            [
                row["customer_name"],
                row["mobile"],
                row["follow_user_display_name"],
                row["current_tag_name"],
                row["external_userid"],
                row["updated_at"],
            ]
        )

    headers = dict(_CSV_HEADERS)
    headers["Content-Disposition"] = 'attachment; filename="class-user-management.csv"'
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
