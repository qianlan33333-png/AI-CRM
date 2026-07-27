#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aicrm_next.engagement.media_library.postgres_repo import _psycopg_url
from aicrm_next.engagement.media_library.variants import generate_image_variants


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_psycopg_url(database_url), row_factory=dict_row)


def _upsert_variants(cur: Any, image: dict[str, Any]) -> None:
    data_base64 = str(image.get("data_base64") or "")
    mime_type = str(image.get("mime_type") or "image/png")
    if not data_base64 and image.get("source_url"):
        response = requests.get(str(image["source_url"]), timeout=10)
        response.raise_for_status()
        data_base64 = base64.b64encode(response.content).decode("ascii")
        mime_type = response.headers.get("content-type", mime_type).split(";")[0] or mime_type
    variants = generate_image_variants(
        image_id=int(image["id"]),
        data_base64=data_base64,
        mime_type=mime_type,
    )
    for variant in variants.values():
        cur.execute(
            """
            INSERT INTO image_library_variants
                (image_id, variant_key, storage_backend, storage_key, public_url,
                 mime_type, width, height, file_size, checksum, data_base64)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (image_id, variant_key) DO UPDATE SET
                storage_backend = EXCLUDED.storage_backend,
                storage_key = EXCLUDED.storage_key,
                public_url = EXCLUDED.public_url,
                mime_type = EXCLUDED.mime_type,
                width = EXCLUDED.width,
                height = EXCLUDED.height,
                file_size = EXCLUDED.file_size,
                checksum = EXCLUDED.checksum,
                data_base64 = EXCLUDED.data_base64,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(image["id"]),
                variant.variant_key,
                variant.storage_backend,
                variant.storage_key,
                variant.public_url,
                variant.mime_type,
                variant.width,
                variant.height,
                variant.file_size,
                variant.checksum,
                variant.data_base64,
            ),
        )


def _variants_table_exists(cur: Any) -> bool:
    cur.execute("SELECT to_regclass('public.image_library_variants') AS table_name")
    return bool((cur.fetchone() or {}).get("table_name"))


def run(*, database_url: str, dry_run: bool, limit: int, batch_size: int, image_id: int | None = None) -> dict[str, int]:
    stats = {"processed": 0, "generated": 0, "failed": 0, "skipped": 0}
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            if not _variants_table_exists(cur):
                print("image_library_variants table does not exist; generated=0 skipped=0 failed=0")
                return stats
            where = ""
            params: list[Any] = []
            if image_id is not None:
                where = "WHERE img.id = %s"
                params.append(image_id)
            else:
                where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM image_library_variants AS v
                    WHERE v.image_id = img.id AND v.variant_key = 'thumb_320'
                )
                """
            params.append(limit)
            cur.execute(
                f"""
                SELECT img.id, img.data_base64, img.mime_type, img.source_url
                FROM image_library AS img
                {where}
                ORDER BY img.id
                LIMIT %s
                """,
                tuple(params),
            )
            rows = [dict(row) for row in cur.fetchall() or []]
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            with conn.cursor() as cur:
                for image in batch:
                    stats["processed"] += 1
                    if not image.get("data_base64") and not image.get("source_url"):
                        stats["skipped"] += 1
                        continue
                    try:
                        if not dry_run:
                            _upsert_variants(cur, image)
                        stats["generated"] += 1
                    except Exception as exc:
                        stats["failed"] += 1
                        print(f"failed image_id={image.get('id')}: {exc}")
                if dry_run:
                    conn.rollback()
                else:
                    conn.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill image_library_variants from image_library.data_base64.")
    parser.add_argument("--database-url", default=os.getenv("AICRM_MEDIA_LIBRARY_DATABASE_URL") or os.getenv("DATABASE_URL") or "")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--image-id", type=int, default=None)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or AICRM_MEDIA_LIBRARY_DATABASE_URL/DATABASE_URL is required")
    stats = run(database_url=args.database_url, dry_run=args.dry_run, limit=max(1, args.limit), batch_size=max(1, args.batch_size), image_id=args.image_id)
    print("processed={processed} generated={generated} failed={failed} skipped={skipped}".format(**stats))
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
