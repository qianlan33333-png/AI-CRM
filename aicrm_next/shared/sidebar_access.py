from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request

from aicrm_next.shared.signed_context import (
    SIDEBAR_VIEWER_SESSION_COOKIE,
    validate_sidebar_owner_context,
)


SIDEBAR_OWNER_TOKEN_HEADER = "x-aicrm-sidebar-owner-token"


def sidebar_owner_context_from_request(
    request: Request,
    *,
    external_userid: str | None = None,
    owner_userid: str | None = None,
    current_userid: str | None = None,
    bind_by_userid: str | None = None,
) -> dict[str, Any]:
    """Resolve the signed sidebar customer/viewer grant and reject scope changes."""

    context = dict(getattr(request.state, "sidebar_context", {}) or {})
    token_status = "valid"
    if not context:
        token_result = validate_sidebar_owner_context(
            token=str(request.headers.get(SIDEBAR_OWNER_TOKEN_HEADER) or "").strip(),
            viewer_session_cookie=str(request.cookies.get(SIDEBAR_VIEWER_SESSION_COOKIE) or "").strip(),
            external_userid=str(external_userid or "").strip(),
            expected_corp_id=str(os.getenv("WECOM_CORP_ID") or "").strip(),
        )
        if not token_result.get("ok"):
            raise HTTPException(status_code=403, detail="sidebar context required")
        context = dict(token_result.get("context") or {})
        token_status = str(token_result.get("status") or "valid")
    viewer = str(context.get("viewer_userid") or context.get("owner_userid") or "").strip()
    context_external = str(context.get("external_userid") or "").strip()
    if external_userid and context_external != str(external_userid or "").strip():
        raise HTTPException(status_code=403, detail="sidebar customer scope forbidden")
    claimed_values = {
        str(value or "").strip()
        for value in (owner_userid, current_userid, bind_by_userid)
        if str(value or "").strip()
    }
    if any(value != viewer for value in claimed_values):
        raise HTTPException(status_code=403, detail="sidebar owner scope forbidden")
    return {
        "owner_userid": viewer,
        "bind_by_userid": viewer,
        "owner_verified": True,
        "external_userid": context_external,
        "source": str(context.get("source") or "signed_sidebar_owner_context"),
        "token_status": token_status,
    }


__all__ = ["SIDEBAR_OWNER_TOKEN_HEADER", "sidebar_owner_context_from_request"]
