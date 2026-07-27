from __future__ import annotations

import hashlib
from typing import Any, Mapping

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.public_product import h5_wechat_pay


_FIXTURE_UNIONIDS = {
    "openid_001": "unionid_001",
    "openid_002": "unionid_002",
    "wx_ext_001": "unionid_001",
    "wx_ext_002": "unionid_002",
}


def authorize_wechat_client(
    client: TestClient,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Install a server-signed OAuth session for H5 route tests."""

    requested = dict(identity or {})
    openid = str(requested.get("openid") or "").strip()
    external_userid = str(requested.get("external_userid") or "").strip()
    respondent_key = str(requested.get("respondent_key") or "").strip()
    unionid = str(requested.get("unionid") or "").strip()
    unionid = unionid or _FIXTURE_UNIONIDS.get(openid) or _FIXTURE_UNIONIDS.get(external_userid) or ""
    if not unionid:
        stable_key = respondent_key or external_userid or openid or "default-questionnaire-user"
        unionid = "unionid_test_" + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:16]
    if not openid:
        openid = "openid_test_" + hashlib.sha256(unionid.encode("utf-8")).hexdigest()[:16]
    payload = {
        "openid": openid,
        "unionid": unionid,
        "external_userid": external_userid,
        "respondent_key": respondent_key or unionid,
    }
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob(payload),
    )
    return payload


__all__ = ["authorize_wechat_client"]
