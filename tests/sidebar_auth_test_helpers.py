from __future__ import annotations

from time import time
from uuid import uuid4

from fastapi.testclient import TestClient

from aicrm_next.platform.shared.signed_context import (
    SIDEBAR_VIEWER_SESSION_COOKIE,
    build_sidebar_owner_context_token,
)
from aicrm_next.platform.shared.signed_session import sign_session_payload


def install_sidebar_auth(
    client: TestClient,
    *,
    viewer_userid: str,
    external_userid: str,
    corp_id: str = "ww-test",
    session_id: str | None = None,
) -> dict[str, str]:
    active_session_id = session_id or f"pytest-{uuid4().hex}"
    client.cookies.set(
        SIDEBAR_VIEWER_SESSION_COOKIE,
        sign_session_payload(
            {
                "auth_source": "wecom_sidebar_oauth",
                "wecom_userid": viewer_userid,
                "external_userid": external_userid,
                "corp_id": corp_id,
                "session_id": active_session_id,
                "iat": int(time()),
            }
        ),
    )
    token = build_sidebar_owner_context_token(
        viewer_userid=viewer_userid,
        external_userid=external_userid,
        session_id=active_session_id,
        corp_id=corp_id,
    )
    return {"X-AICRM-Sidebar-Owner-Token": token}


def install_sidebar_viewer_session(
    client: TestClient,
    *,
    viewer_userid: str,
    external_userid: str,
    corp_id: str = "ww-test",
    session_id: str | None = None,
) -> str:
    active_session_id = session_id or f"pytest-{uuid4().hex}"
    client.cookies.set(
        SIDEBAR_VIEWER_SESSION_COOKIE,
        sign_session_payload(
            {
                "auth_source": "wecom_sidebar_oauth",
                "wecom_userid": viewer_userid,
                "external_userid": external_userid,
                "corp_id": corp_id,
                "session_id": active_session_id,
                "iat": int(time()),
            }
        ),
    )
    return active_session_id
