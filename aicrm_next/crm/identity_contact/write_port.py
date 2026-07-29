from __future__ import annotations

from typing import Any, Protocol


class IdentityWritePort(Protocol):
    """Public command port for mutations owned by identity_contact."""

    def bind_sidebar_mobile(
        self,
        conn: Any,
        *,
        unionid: str,
        external_userid: str,
        mobile: str,
        owner_userid: str,
        bind_by_userid: str,
    ) -> dict[str, Any]: ...

    def update_sidebar_contact_profile(
        self,
        conn: Any,
        *,
        unionid: str,
        display_name: str,
        remark: str,
        description: str,
        updated_by: str,
    ) -> dict[str, Any] | None: ...

    def record_sidebar_material_send_plan(
        self,
        conn: Any,
        *,
        unionid: str,
        plan: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    def enqueue_sidebar_identity_resolution(
        self,
        conn: Any,
        *,
        command_id: str,
        external_userid: str,
        mobile: str,
        owner_userid: str,
        bind_by_userid: str,
    ) -> dict[str, Any]: ...


__all__ = ["IdentityWritePort"]
