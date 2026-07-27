from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


class PostgresCustomerTagProjectionRepository:
    """Canonical local contact-tag projection mutations."""

    def save_channel_snapshot_dbapi(
        self,
        executor: Any,
        *,
        unionid: str,
        owner_userid: str,
        tag_ids: list[str],
        tag_names: dict[str, str],
    ) -> dict[str, int]:
        normalized_unionid = _text(unionid)
        normalized_owner = _text(owner_userid)
        if not normalized_unionid:
            raise ValueError("contact tag projection unionid is required")
        updated_count = 0
        inserted_count = 0
        for raw_tag_id in tag_ids:
            tag_id = _text(raw_tag_id)
            tag_name = _text(tag_names.get(raw_tag_id))
            executor.execute(
                """
                UPDATE contact_tags
                SET tag_name = %s,
                    created_at = CURRENT_TIMESTAMP
                WHERE unionid = %s
                  AND userid = %s
                  AND tag_id = %s
                """,
                (tag_name, normalized_unionid, normalized_owner, tag_id),
            )
            if int(executor.rowcount or 0) > 0:
                updated_count += int(executor.rowcount or 0)
                continue
            executor.execute(
                """
                INSERT INTO contact_tags (unionid, userid, tag_id, tag_name, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (normalized_unionid, normalized_owner, tag_id, tag_name),
            )
            inserted_count += 1
        return {
            "updated_count": updated_count,
            "inserted_count": inserted_count,
        }


__all__ = ["PostgresCustomerTagProjectionRepository"]
