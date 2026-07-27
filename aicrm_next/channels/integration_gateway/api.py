from __future__ import annotations

from fastapi import APIRouter, Request

from aicrm_next.shared.typing import JsonDict

from .mcp import McpJsonRpcApplication

router = APIRouter()


@router.get("/mcp")
def mcp_metadata() -> dict:
    return {"ok": True, "transport": "jsonrpc", "methods": ["initialize", "tools/list", "tools/call"]}


@router.post("/mcp")
def mcp_rpc(payload: JsonDict, request: Request) -> dict:
    application = getattr(request.app.state, "mcp_jsonrpc_application", None)
    if not isinstance(application, McpJsonRpcApplication):
        application = McpJsonRpcApplication()
    return application.handle(payload)
