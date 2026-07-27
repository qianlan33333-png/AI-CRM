from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.shared.sync_request import read_request_body

from .application import AutomationAgentWebhookService

router = APIRouter()


def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(payload),
        status_code=status_code,
        headers={
            "X-AICRM-Route-Owner": "ai_crm_next",
            "X-AICRM-Fallback-Used": "false",
            "X-AICRM-Real-External-Call-Executed": "false",
        },
    )


@router.post("/api/ai/agents/{agent_code}/audience-webhook", name="api.ai_automation_agent_audience_webhook")
def automation_agent_audience_webhook(agent_code: str, request: Request) -> JSONResponse:
    raw_body = read_request_body(request)
    try:
        payload: Any = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        return _json({"ok": False, "error": "invalid_json", "detail": str(exc)}, status_code=400)
    headers = {key: value for key, value in request.headers.items()}
    result, status_code = AutomationAgentWebhookService().handle(
        agent_code,
        payload,
        raw_body=raw_body,
        headers=headers,
    )
    return _json(result, status_code=status_code)
