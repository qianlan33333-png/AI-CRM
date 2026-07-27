from __future__ import annotations

from typing import Any

from aicrm_next.crm.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.crm.identity_contact.resolver import resolve_identity_with_dbapi
from aicrm_next.platform.shared.postgres_connection import get_db

JsonDict = dict[str, Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row(row: Any) -> JsonDict | None:
    return dict(row) if row else None


class PostgresSendTargetRepository:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db or get_db()

    def fetch_send_target_by_unionid(self, unionid: str) -> JsonDict | None:
        return self._target(ResolvePersonIdentityRequest(unionid=_text(unionid) or None))

    def fetch_send_target_by_external_userid(self, external_userid: str) -> JsonDict | None:
        return self._target(ResolvePersonIdentityRequest(external_userid=_text(external_userid) or None))

    def _target(self, query: ResolvePersonIdentityRequest) -> JsonDict | None:
        resolution = resolve_identity_with_dbapi(self.db, query, placeholder="?")
        identity = resolution.identity if resolution.status == "resolved" else None
        if identity is None:
            return None
        return {
            "unionid": _text(identity.unionid),
            "primary_external_userid": _text(identity.external_userid),
            "primary_owner_userid": _text(identity.owner_userid),
            "owner_userid": _text(identity.owner_userid),
            "customer_name": _text(identity.customer_name),
        }

    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[JsonDict]:
        try:
            rows = self.db.execute(
                """
                SELECT reason_code, reason_text, source_type
                FROM user_ops_do_not_disturb_next
                WHERE unionid = ?
                  AND is_active = TRUE
                ORDER BY id ASC
                """,
                (_text(unionid),),
            ).fetchall()
        except Exception:
            rollback = getattr(self.db, "rollback", None)
            if callable(rollback):
                rollback()
            return []
        return [
            {
                "reason_code": _text(row.get("reason_code")),
                "reason_text": _text(row.get("reason_text")),
                "source_type": _text(row.get("source_type")),
            }
            for row in rows
        ]
