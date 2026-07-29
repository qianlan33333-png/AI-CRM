from __future__ import annotations

from typing import Any

from .identity_bridge_service import (
    SIDEBAR_IDENTITY_REFRESH_INTERVAL_SECONDS,
    build_identity_bridge_service,
)


def sync_external_contact_identity_for_event(event: dict[str, Any], *, corp_id: str) -> dict[str, Any]:
    return build_identity_bridge_service().sync_external_contact_identity_for_event(event, corp_id=corp_id)


def ensure_external_contact_identity_for_sidebar(
    *,
    external_userid: str,
    owner_userid: str = "",
    corp_id: str = "",
    min_interval_seconds: int = SIDEBAR_IDENTITY_REFRESH_INTERVAL_SECONDS,
) -> dict[str, Any]:
    return build_identity_bridge_service().ensure_external_contact_identity_for_sidebar(
        external_userid=external_userid,
        owner_userid=owner_userid,
        corp_id=corp_id,
        min_interval_seconds=min_interval_seconds,
    )
