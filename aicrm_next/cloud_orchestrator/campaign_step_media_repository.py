from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


class PostgresCampaignStepMediaReferenceRepository:
    """Canonical DBAPI writer for media references stored on campaign steps."""

    def clear_image_references_dbapi(
        self,
        executor: Any,
        *,
        image_library_id: int | str,
    ) -> int:
        normalized_image_id = _text(image_library_id)
        if not normalized_image_id:
            raise ValueError("image_library_id is required")
        executor.execute(
            """
            UPDATE campaign_steps
            SET content_payload_json = jsonb_set(
                COALESCE(content_payload_json, '{}'::jsonb),
                '{image_library_ids}',
                COALESCE((
                    SELECT jsonb_agg(elem)
                    FROM jsonb_array_elements(
                        COALESCE(content_payload_json->'image_library_ids', '[]'::jsonb)
                    ) AS elem
                    WHERE trim(both '"' from elem::text) <> %s
                ), '[]'::jsonb),
                true
            ),
            updated_at = CURRENT_TIMESTAMP
            WHERE EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(
                    COALESCE(content_payload_json->'image_library_ids', '[]'::jsonb)
                ) AS iid
                WHERE iid = %s
            )
            """,
            (normalized_image_id, normalized_image_id),
        )
        return int(executor.rowcount or 0)


__all__ = ["PostgresCampaignStepMediaReferenceRepository"]
