from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.shared.errors import NotFoundError

from .domain import TENANT_ID, isoformat, text, utcnow
from .member_grid_access import (
    MemberGridAccessConflictError,
    new_public_share_id,
    normalize_grid_permission,
)


class MemberGridAccessRepositoryProtocol(Protocol):
    def get_member_grid_collaborator(self, service_product_id: str, admin_user_id: str) -> dict[str, Any] | None: ...
    def list_member_grid_collaborators(self, service_product_id: str) -> list[dict[str, Any]]: ...
    def create_member_grid_collaborator(self, service_product_id: str, *, admin_user_id: str, wecom_userid: str, display_name: str, avatar_url: str, permission: str, actor: str) -> dict[str, Any]: ...
    def update_member_grid_collaborator(self, service_product_id: str, collaborator_id: str, *, permission: str, expected_version: int, actor: str) -> dict[str, Any]: ...
    def delete_member_grid_collaborator(self, service_product_id: str, collaborator_id: str, *, expected_version: int) -> dict[str, Any]: ...
    def count_member_grid_collaborations(self, admin_user_id: str) -> int: ...
    def get_member_grid_share(self, service_product_id: str) -> dict[str, Any]: ...
    def set_member_grid_share_enabled(self, service_product_id: str, *, enabled: bool, expected_version: int, actor: str) -> dict[str, Any]: ...


def _serialize_collaborator(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": text(row.get("id")),
        "service_product_id": text(row.get("service_product_id")),
        "admin_user_id": text(row.get("admin_user_id")),
        "wecom_userid": text(row.get("wecom_userid")),
        "display_name": text(row.get("display_name")) or text(row.get("wecom_userid")),
        "avatar_url": text(row.get("avatar_url")),
        "permission": normalize_grid_permission(row.get("permission")),
        "version": int(row.get("version") or 1),
        "created_by": text(row.get("created_by")),
        "updated_by": text(row.get("updated_by")),
        "created_at": isoformat(row.get("created_at")),
        "updated_at": isoformat(row.get("updated_at")),
        "implicit": False,
        "removable": True,
    }


def _serialize_share(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_product_id": text(row.get("service_product_id")),
        "enabled": bool(row.get("enabled")),
        "public_id": text(row.get("public_id")),
        "generation": int(row.get("generation") or 0),
        "version": int(row.get("version") or 1),
        "created_by": text(row.get("created_by")),
        "updated_by": text(row.get("updated_by")),
        "created_at": isoformat(row.get("created_at")),
        "updated_at": isoformat(row.get("updated_at")),
    }


class InMemoryMemberGridAccessRepositoryMixin:
    _member_grid_collaborators: list[dict[str, Any]]
    _member_grid_shares: list[dict[str, Any]]
    _next_member_grid_collaborator_id: int

    def get_member_grid_collaborator(self, service_product_id: str, admin_user_id: str) -> dict[str, Any] | None:
        row = next(
            (
                item
                for item in self._member_grid_collaborators
                if text(item.get("service_product_id")) == text(service_product_id)
                and text(item.get("admin_user_id")) == text(admin_user_id)
            ),
            None,
        )
        return _serialize_collaborator(row) if row else None

    def list_member_grid_collaborators(self, service_product_id: str) -> list[dict[str, Any]]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        rows = [
            _serialize_collaborator(item)
            for item in self._member_grid_collaborators
            if text(item.get("service_product_id")) == text(service_product_id)
        ]
        return sorted(rows, key=lambda item: (item["display_name"].casefold(), item["id"]))

    def create_member_grid_collaborator(
        self,
        service_product_id: str,
        *,
        admin_user_id: str,
        wecom_userid: str,
        display_name: str,
        avatar_url: str,
        permission: str,
        actor: str,
    ) -> dict[str, Any]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        if self.get_member_grid_collaborator(service_product_id, admin_user_id):
            raise MemberGridAccessConflictError("该员工已经是协作者")
        now = utcnow().isoformat()
        row = {
            "id": f"spc_{self._next_member_grid_collaborator_id:03d}",
            "tenant_id": TENANT_ID,
            "service_product_id": text(service_product_id),
            "admin_user_id": text(admin_user_id),
            "wecom_userid": text(wecom_userid),
            "display_name": text(display_name) or text(wecom_userid),
            "avatar_url": text(avatar_url),
            "permission": normalize_grid_permission(permission),
            "version": 1,
            "created_by": text(actor),
            "updated_by": text(actor),
            "created_at": now,
            "updated_at": now,
        }
        self._next_member_grid_collaborator_id += 1
        self._member_grid_collaborators.append(row)
        return _serialize_collaborator(row)

    def update_member_grid_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        permission: str,
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        row = self._find_member_grid_collaborator(service_product_id, collaborator_id)
        if not row:
            raise NotFoundError("member grid collaborator not found")
        if int(row.get("version") or 0) != int(expected_version or 0):
            raise MemberGridAccessConflictError("协作者权限已被其他管理员更新")
        row.update(
            permission=normalize_grid_permission(permission),
            version=int(row.get("version") or 0) + 1,
            updated_by=text(actor),
            updated_at=utcnow().isoformat(),
        )
        return _serialize_collaborator(row)

    def delete_member_grid_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        row = self._find_member_grid_collaborator(service_product_id, collaborator_id)
        if not row:
            raise NotFoundError("member grid collaborator not found")
        if int(row.get("version") or 0) != int(expected_version or 0):
            raise MemberGridAccessConflictError("协作者权限已被其他管理员更新")
        self._member_grid_collaborators = [item for item in self._member_grid_collaborators if item is not row]
        return _serialize_collaborator(row)

    def count_member_grid_collaborations(self, admin_user_id: str) -> int:
        return sum(1 for item in self._member_grid_collaborators if text(item.get("admin_user_id")) == text(admin_user_id))

    def get_member_grid_share(self, service_product_id: str) -> dict[str, Any]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        row = next(
            (item for item in self._member_grid_shares if text(item.get("service_product_id")) == text(service_product_id)),
            None,
        )
        if row is None:
            now = utcnow().isoformat()
            row = {
                "tenant_id": TENANT_ID,
                "service_product_id": text(service_product_id),
                "enabled": False,
                "public_id": "",
                "generation": 0,
                "version": 1,
                "created_by": "system",
                "updated_by": "system",
                "created_at": now,
                "updated_at": now,
            }
            self._member_grid_shares.append(row)
        return _serialize_share(row)

    def _append_default_member_grid_share(self, service_product_id: str, *, actor: str) -> None:
        if any(text(item.get("service_product_id")) == text(service_product_id) for item in self._member_grid_shares):
            return
        now = utcnow().isoformat()
        self._member_grid_shares.append(
            {
                "tenant_id": TENANT_ID,
                "service_product_id": text(service_product_id),
                "enabled": False,
                "public_id": "",
                "generation": 0,
                "version": 1,
                "created_by": text(actor),
                "updated_by": text(actor),
                "created_at": now,
                "updated_at": now,
            }
        )

    def set_member_grid_share_enabled(
        self,
        service_product_id: str,
        *,
        enabled: bool,
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        self.get_member_grid_share(service_product_id)
        row = next(item for item in self._member_grid_shares if text(item.get("service_product_id")) == text(service_product_id))
        if int(row.get("version") or 0) != int(expected_version or 0):
            raise MemberGridAccessConflictError("外部分享设置已被其他管理员更新")
        next_enabled = bool(enabled)
        if next_enabled and not bool(row.get("enabled")):
            row["public_id"] = new_public_share_id()
            row["generation"] = int(row.get("generation") or 0) + 1
        row.update(
            enabled=next_enabled,
            version=int(row.get("version") or 0) + 1,
            updated_by=text(actor),
            updated_at=utcnow().isoformat(),
        )
        return _serialize_share(row)

    def _find_member_grid_collaborator(self, service_product_id: str, collaborator_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self._member_grid_collaborators
                if text(item.get("service_product_id")) == text(service_product_id)
                and text(item.get("id")) == text(collaborator_id)
            ),
            None,
        )

    def _delete_member_grid_access(self, service_product_id: str) -> None:
        self._member_grid_collaborators = [
            item for item in self._member_grid_collaborators if text(item.get("service_product_id")) != text(service_product_id)
        ]
        self._member_grid_shares = [
            item for item in self._member_grid_shares if text(item.get("service_product_id")) != text(service_product_id)
        ]


class PostgresMemberGridAccessRepositoryMixin:
    def _insert_default_member_grid_share(self, conn, service_product_id: str, *, actor: str) -> None:
        conn.execute(
            """
            INSERT INTO service_period_member_shares (
                tenant_id, service_product_id, enabled, public_id, generation,
                version, created_by, updated_by, created_at, updated_at
            ) VALUES ('aicrm', %s, FALSE, '', 0, 1, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id, service_product_id) DO NOTHING
            """,
            (int(text(service_product_id)), text(actor), text(actor)),
        )

    def get_member_grid_collaborator(self, service_product_id: str, admin_user_id: str) -> dict[str, Any] | None:
        if not text(admin_user_id):
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM service_period_member_collaborators
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                  AND admin_user_id::text = %s
                LIMIT 1
                """,
                (text(service_product_id), text(admin_user_id)),
            ).fetchone()
        return _serialize_collaborator(dict(row)) if row else None

    def list_member_grid_collaborators(self, service_product_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM service_period_member_collaborators
                WHERE tenant_id = 'aicrm' AND service_product_id::text = %s
                ORDER BY LOWER(display_name), id
                """,
                (text(service_product_id),),
            ).fetchall()
        return [_serialize_collaborator(dict(row)) for row in rows]

    def create_member_grid_collaborator(
        self,
        service_product_id: str,
        *,
        admin_user_id: str,
        wecom_userid: str,
        display_name: str,
        avatar_url: str,
        permission: str,
        actor: str,
    ) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO service_period_member_collaborators (
                        tenant_id, service_product_id, admin_user_id, wecom_userid,
                        display_name, avatar_url, permission, version,
                        created_by, updated_by, created_at, updated_at
                    ) VALUES ('aicrm', %s, %s, %s, %s, %s, %s, 1, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING *
                    """,
                    (
                        int(text(service_product_id)),
                        int(text(admin_user_id)),
                        text(wecom_userid),
                        text(display_name) or text(wecom_userid),
                        text(avatar_url),
                        normalize_grid_permission(permission),
                        text(actor),
                        text(actor),
                    ),
                ).fetchone()
                conn.commit()
        except Exception as exc:
            if getattr(exc, "sqlstate", "") == "23505":
                raise MemberGridAccessConflictError("该员工已经是协作者") from exc
            raise
        return _serialize_collaborator(dict(row))

    def update_member_grid_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        permission: str,
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE service_period_member_collaborators
                SET permission = %s, version = version + 1, updated_by = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = 'aicrm' AND service_product_id::text = %s
                  AND id::text = %s AND version = %s
                RETURNING *
                """,
                (
                    normalize_grid_permission(permission),
                    text(actor),
                    text(service_product_id),
                    text(collaborator_id),
                    int(expected_version or 0),
                ),
            ).fetchone()
            if not row:
                exists = conn.execute(
                    "SELECT 1 FROM service_period_member_collaborators WHERE service_product_id::text = %s AND id::text = %s",
                    (text(service_product_id), text(collaborator_id)),
                ).fetchone()
                if exists:
                    raise MemberGridAccessConflictError("协作者权限已被其他管理员更新")
                raise NotFoundError("member grid collaborator not found")
            conn.commit()
        return _serialize_collaborator(dict(row))

    def delete_member_grid_collaborator(
        self,
        service_product_id: str,
        collaborator_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                DELETE FROM service_period_member_collaborators
                WHERE tenant_id = 'aicrm' AND service_product_id::text = %s
                  AND id::text = %s AND version = %s
                RETURNING *
                """,
                (text(service_product_id), text(collaborator_id), int(expected_version or 0)),
            ).fetchone()
            if not row:
                exists = conn.execute(
                    "SELECT 1 FROM service_period_member_collaborators WHERE service_product_id::text = %s AND id::text = %s",
                    (text(service_product_id), text(collaborator_id)),
                ).fetchone()
                if exists:
                    raise MemberGridAccessConflictError("协作者权限已被其他管理员更新")
                raise NotFoundError("member grid collaborator not found")
            conn.commit()
        return _serialize_collaborator(dict(row))

    def count_member_grid_collaborations(self, admin_user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count(*) AS total FROM service_period_member_collaborators WHERE tenant_id = 'aicrm' AND admin_user_id::text = %s",
                (text(admin_user_id),),
            ).fetchone() or {}
        return int(row.get("total") or 0)

    def get_member_grid_share(self, service_product_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM service_period_member_shares
                WHERE tenant_id = 'aicrm' AND service_product_id::text = %s
                """,
                (text(service_product_id),),
            ).fetchone()
        if not row:
            raise NotFoundError("member grid share settings not found")
        return _serialize_share(dict(row))

    def set_member_grid_share_enabled(
        self,
        service_product_id: str,
        *,
        enabled: bool,
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        current = self.get_member_grid_share(service_product_id)
        if int(current.get("version") or 0) != int(expected_version or 0):
            raise MemberGridAccessConflictError("外部分享设置已被其他管理员更新")
        next_public_id = new_public_share_id() if enabled and not current.get("enabled") else text(current.get("public_id"))
        next_generation = int(current.get("generation") or 0) + (1 if enabled and not current.get("enabled") else 0)
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE service_period_member_shares
                SET enabled = %s, public_id = %s, generation = %s,
                    version = version + 1, updated_by = %s, updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = 'aicrm' AND service_product_id::text = %s AND version = %s
                RETURNING *
                """,
                (
                    bool(enabled),
                    next_public_id,
                    next_generation,
                    text(actor),
                    text(service_product_id),
                    int(expected_version or 0),
                ),
            ).fetchone()
            if not row:
                raise MemberGridAccessConflictError("外部分享设置已被其他管理员更新")
            conn.commit()
        return _serialize_share(dict(row))
