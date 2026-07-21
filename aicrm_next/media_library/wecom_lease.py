from __future__ import annotations

import base64
import hashlib
import io
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import text

from aicrm_next.integration_gateway.wecom_media_upload_client import (
    WeComMediaUploadClientError,
    build_wecom_media_upload_client,
)
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.wecom_runtime import classify_wecom_provider_error, load_wecom_execution_config

from .postgres_repo import PostgresMediaLibraryRepository


PROVIDER_TTL = timedelta(days=3)
REFRESH_MARGIN = timedelta(hours=12)
DEFAULT_MINIMUM_VALIDITY = timedelta(minutes=30)
REFRESH_LOCK_TTL = timedelta(minutes=2)
MAX_WAIT_SECONDS = 3.0
TENANT_ID = "aicrm"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    raw = _text(value)
    if not raw:
        return None
    try:
        return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _provider_created_at(payload: dict[str, Any], now: datetime) -> datetime:
    raw = payload.get("created_at")
    try:
        parsed = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        parsed = now
    return min(parsed, now)


@dataclass(frozen=True)
class MediaLeaseKey:
    corp_id: str
    material_kind: str
    material_id: int
    upload_kind: str
    tenant_id: str = TENANT_ID


class WeComMediaLeaseError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider_errcode: int = 0,
        provider_error_classification: str = "",
        provider_stage: str = "",
        provider_result_received: bool = False,
        provider_call_executed: bool = False,
        errmsg_present: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = _text(code) or "wecom_media_lease_error"
        self.retryable = bool(retryable)
        self.provider_errcode = _safe_int(provider_errcode)
        classification = _text(provider_error_classification)
        self.provider_error_classification = classification if classification in {"blocked", "retryable", "terminal"} else ""
        stage = _text(provider_stage)
        self.provider_stage = stage if stage in {"config", "gettoken", "upload", "upload_attachment"} else ""
        self.provider_result_received = bool(provider_result_received)
        self.provider_call_executed = bool(provider_call_executed)
        self.errmsg_present = bool(errmsg_present)


def _provider_lease_error(exc: WeComMediaUploadClientError) -> WeComMediaLeaseError:
    provider_errcode = _safe_int(getattr(exc, "provider_errcode", 0))
    provider_stage = _text(getattr(exc, "stage", ""))
    provider_result_received = bool(getattr(exc, "provider_result_received", False))
    media_upload_executed = provider_stage in {"upload", "upload_attachment"}
    client_error_code = _text(getattr(exc, "error_code", ""))
    if client_error_code == "wecom_media_config_missing":
        error_code = "config_missing"
        classification = "terminal"
    elif client_error_code == "wecom_media_http_error":
        error_code, classification = classify_wecom_provider_error(transport_error=True)
    elif provider_result_received:
        error_code, classification = classify_wecom_provider_error(provider_errcode=provider_errcode)
    elif media_upload_executed:
        error_code = "external_call_unknown"
        classification = "retryable"
    else:
        error_code = "provider_response_invalid"
        classification = "terminal"
    return WeComMediaLeaseError(
        error_code,
        error_code,
        retryable=classification == "retryable",
        provider_errcode=provider_errcode,
        provider_error_classification=classification,
        provider_stage=provider_stage,
        provider_result_received=provider_result_received,
        provider_call_executed=media_upload_executed,
        errmsg_present=bool(getattr(exc, "errmsg_present", False)),
    )


class WeComMediaLeaseRepository(Protocol):
    def get(self, key: MediaLeaseKey) -> dict[str, Any] | None: ...

    def claim_refresh(self, key: MediaLeaseKey, *, token: str, now: datetime) -> bool: ...

    def mark_ready(
        self,
        key: MediaLeaseKey,
        *,
        token: str,
        media_id: str,
        content_sha256: str,
        provider_created_at: datetime,
        provider_expires_at: datetime,
        refresh_after: datetime,
    ) -> dict[str, Any]: ...

    def mark_failed(
        self,
        key: MediaLeaseKey,
        *,
        token: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime,
    ) -> None: ...

    def mark_used(self, key: MediaLeaseKey, *, now: datetime) -> None: ...

    def list_due_materials(self, *, corp_id: str, now: datetime, limit: int) -> list[dict[str, Any]]: ...

    def metrics(self, *, corp_id: str, now: datetime) -> dict[str, int]: ...


class PostgresWeComMediaLeaseRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @staticmethod
    def _params(key: MediaLeaseKey) -> dict[str, Any]:
        return {
            "tenant_id": key.tenant_id,
            "corp_id": key.corp_id,
            "material_kind": key.material_kind,
            "material_id": key.material_id,
            "upload_kind": key.upload_kind,
        }

    def get(self, key: MediaLeaseKey) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT * FROM wecom_media_leases
                    WHERE tenant_id = :tenant_id AND corp_id = :corp_id
                      AND material_kind = :material_kind AND material_id = :material_id
                      AND upload_kind = :upload_kind
                    LIMIT 1
                    """
                ),
                self._params(key),
            ).mappings().first()
        return dict(row) if row else None

    def claim_refresh(self, key: MediaLeaseKey, *, token: str, now: datetime) -> bool:
        params = {
            **self._params(key),
            "token": token,
            "now": now,
            "locked_until": now + REFRESH_LOCK_TTL,
        }
        with self._session_factory() as session:
            session.execute(
                text(
                    """
                    INSERT INTO wecom_media_leases (
                        tenant_id, corp_id, material_kind, material_id, upload_kind,
                        status, lock_token, locked_until, created_at, updated_at
                    ) VALUES (
                        :tenant_id, :corp_id, :material_kind, :material_id, :upload_kind,
                        'pending', '', NULL, :now, :now
                    )
                    ON CONFLICT (tenant_id, corp_id, material_kind, material_id, upload_kind)
                    DO NOTHING
                    """
                ),
                params,
            )
            row = session.execute(
                text(
                    """
                    UPDATE wecom_media_leases
                    SET status = 'refreshing', lock_token = :token, locked_until = :locked_until,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id AND corp_id = :corp_id
                      AND material_kind = :material_kind AND material_id = :material_id
                      AND upload_kind = :upload_kind
                      AND (status <> 'refreshing' OR locked_until IS NULL OR locked_until <= :now)
                    RETURNING id
                    """
                ),
                params,
            ).first()
            session.commit()
        return bool(row)

    def mark_ready(
        self,
        key: MediaLeaseKey,
        *,
        token: str,
        media_id: str,
        content_sha256: str,
        provider_created_at: datetime,
        provider_expires_at: datetime,
        refresh_after: datetime,
    ) -> dict[str, Any]:
        params = {
            **self._params(key),
            "token": token,
            "media_id": media_id,
            "content_sha256": content_sha256,
            "provider_created_at": provider_created_at,
            "provider_expires_at": provider_expires_at,
            "refresh_after": refresh_after,
        }
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    UPDATE wecom_media_leases
                    SET media_id = :media_id, content_sha256 = :content_sha256,
                        status = 'ready', provider_created_at = :provider_created_at,
                        provider_expires_at = :provider_expires_at, refresh_after = :refresh_after,
                        next_retry_at = NULL, locked_until = NULL, lock_token = '',
                        attempt_count = attempt_count + 1, lease_version = lease_version + 1,
                        last_error_code = '', last_error_message = '', updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = :tenant_id AND corp_id = :corp_id
                      AND material_kind = :material_kind AND material_id = :material_id
                      AND upload_kind = :upload_kind AND lock_token = :token
                    RETURNING *
                    """
                ),
                params,
            ).mappings().first()
            session.commit()
        if not row:
            raise WeComMediaLeaseError("media_refresh_lease_lost", "WeCom media refresh lease was lost", retryable=True)
        return dict(row)

    def mark_failed(
        self,
        key: MediaLeaseKey,
        *,
        token: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime,
    ) -> None:
        retry_at = now + timedelta(minutes=5 if retryable else 60)
        status = "failed" if retryable else "invalid_source"
        with self._session_factory() as session:
            session.execute(
                text(
                    """
                    UPDATE wecom_media_leases
                    SET status = :status, next_retry_at = :next_retry_at,
                        locked_until = NULL, lock_token = '', attempt_count = attempt_count + 1,
                        last_error_code = :error_code, last_error_message = :error_message,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id AND corp_id = :corp_id
                      AND material_kind = :material_kind AND material_id = :material_id
                      AND upload_kind = :upload_kind AND lock_token = :token
                    """
                ),
                {
                    **self._params(key),
                    "status": status,
                    "next_retry_at": retry_at,
                    "error_code": _text(error_code)[:120],
                    "error_message": _text(error_message)[:500],
                    "now": now,
                    "token": token,
                },
            )
            session.commit()

    def mark_used(self, key: MediaLeaseKey, *, now: datetime) -> None:
        with self._session_factory() as session:
            session.execute(
                text(
                    """
                    UPDATE wecom_media_leases SET last_used_at = :now, updated_at = updated_at
                    WHERE tenant_id = :tenant_id AND corp_id = :corp_id
                      AND material_kind = :material_kind AND material_id = :material_id
                      AND upload_kind = :upload_kind
                    """
                ),
                {**self._params(key), "now": now},
            )
            session.commit()

    def list_due_materials(self, *, corp_id: str, now: datetime, limit: int) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or 50), 500))
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT 'image'::text AS material_kind, i.id AS material_id, 'image'::text AS upload_kind,
                               l.status, l.refresh_after, l.next_retry_at, l.locked_until
                        FROM image_library i
                        LEFT JOIN wecom_media_leases l
                          ON l.tenant_id = 'aicrm' AND l.corp_id = :corp_id
                         AND l.material_kind = 'image' AND l.material_id = i.id AND l.upload_kind = 'image'
                        WHERE i.enabled IS TRUE AND COALESCE(i.data_base64, '') <> ''
                        UNION ALL
                        SELECT 'attachment', a.id, 'attachment',
                               l.status, l.refresh_after, l.next_retry_at, l.locked_until
                        FROM attachment_library a
                        LEFT JOIN wecom_media_leases l
                          ON l.tenant_id = 'aicrm' AND l.corp_id = :corp_id
                         AND l.material_kind = 'attachment' AND l.material_id = a.id AND l.upload_kind = 'attachment'
                        WHERE a.enabled IS TRUE AND COALESCE(a.data_base64, '') <> ''
                        UNION ALL
                        SELECT 'miniprogram', m.id, 'image',
                               l.status, l.refresh_after, l.next_retry_at, l.locked_until
                        FROM miniprogram_library m
                        LEFT JOIN wecom_media_leases l
                          ON l.tenant_id = 'aicrm' AND l.corp_id = :corp_id
                         AND l.material_kind = 'miniprogram' AND l.material_id = m.id AND l.upload_kind = 'image'
                        WHERE m.enabled IS TRUE AND m.thumb_image_id IS NULL
                          AND COALESCE(m.thumb_image_base64, '') <> ''
                    )
                    SELECT material_kind, material_id, upload_kind
                    FROM candidates
                    WHERE status IS NULL
                       OR (status = 'ready' AND (refresh_after IS NULL OR refresh_after <= :now))
                       OR (status IN ('pending', 'failed') AND (next_retry_at IS NULL OR next_retry_at <= :now))
                       OR (status = 'refreshing' AND (locked_until IS NULL OR locked_until <= :now))
                    ORDER BY
                        CASE WHEN status IS NULL THEN 0 ELSE 1 END,
                        material_kind, material_id
                    LIMIT :limit
                    """
                ),
                {"corp_id": corp_id, "now": now, "limit": bounded_limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def metrics(self, *, corp_id: str, now: datetime) -> dict[str, int]:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS total_count,
                        COUNT(*) FILTER (WHERE status = 'ready' AND provider_expires_at > :now) AS ready_count,
                        COUNT(*) FILTER (WHERE status = 'ready' AND (refresh_after IS NULL OR refresh_after <= :now)) AS refresh_due_count,
                        COUNT(*) FILTER (WHERE status = 'refreshing') AS refreshing_count,
                        COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
                        COUNT(*) FILTER (WHERE status = 'invalid_source') AS invalid_source_count,
                        COUNT(*) FILTER (WHERE status = 'ready' AND provider_expires_at <= :now) AS expired_count
                    FROM wecom_media_leases
                    WHERE tenant_id = 'aicrm' AND corp_id = :corp_id
                    """
                ),
                {"corp_id": corp_id, "now": now},
            ).mappings().one()
        return {key: int(value or 0) for key, value in dict(row).items()}


class InMemoryWeComMediaLeaseRepository:
    def __init__(self) -> None:
        self.rows: dict[MediaLeaseKey, dict[str, Any]] = {}

    def get(self, key: MediaLeaseKey) -> dict[str, Any] | None:
        row = self.rows.get(key)
        return dict(row) if row else None

    def claim_refresh(self, key: MediaLeaseKey, *, token: str, now: datetime) -> bool:
        row = self.rows.setdefault(key, {"status": "pending", "attempt_count": 0, "lease_version": 0})
        if row.get("status") == "refreshing" and _parse_datetime(row.get("locked_until")) and _parse_datetime(row["locked_until"]) > now:
            return False
        row.update({"status": "refreshing", "lock_token": token, "locked_until": now + REFRESH_LOCK_TTL})
        return True

    def mark_ready(self, key: MediaLeaseKey, **values: Any) -> dict[str, Any]:
        row = self.rows[key]
        if row.get("lock_token") != values.get("token"):
            raise WeComMediaLeaseError("media_refresh_lease_lost", "WeCom media refresh lease was lost", retryable=True)
        row.update(
            {
                "media_id": values["media_id"],
                "content_sha256": values["content_sha256"],
                "provider_created_at": values["provider_created_at"],
                "provider_expires_at": values["provider_expires_at"],
                "refresh_after": values["refresh_after"],
                "status": "ready",
                "lock_token": "",
                "locked_until": None,
                "attempt_count": int(row.get("attempt_count") or 0) + 1,
                "lease_version": int(row.get("lease_version") or 0) + 1,
            }
        )
        return dict(row)

    def mark_failed(self, key: MediaLeaseKey, **values: Any) -> None:
        row = self.rows[key]
        row.update(
            {
                "status": "failed" if values.get("retryable") else "invalid_source",
                "last_error_code": values.get("error_code"),
                "last_error_message": values.get("error_message"),
                "lock_token": "",
                "locked_until": None,
            }
        )

    def mark_used(self, key: MediaLeaseKey, *, now: datetime) -> None:
        if key in self.rows:
            self.rows[key]["last_used_at"] = now

    def list_due_materials(self, *, corp_id: str, now: datetime, limit: int) -> list[dict[str, Any]]:
        return []

    def metrics(self, *, corp_id: str, now: datetime) -> dict[str, int]:
        rows = [row for key, row in self.rows.items() if key.corp_id == corp_id]
        return {
            "total_count": len(rows),
            "ready_count": len([row for row in rows if row.get("status") == "ready"]),
            "refresh_due_count": 0,
            "refreshing_count": len([row for row in rows if row.get("status") == "refreshing"]),
            "failed_count": len([row for row in rows if row.get("status") == "failed"]),
            "invalid_source_count": len([row for row in rows if row.get("status") == "invalid_source"]),
            "expired_count": 0,
        }


class WeComMediaLeaseManager:
    def __init__(
        self,
        media_repository: PostgresMediaLibraryRepository,
        *,
        lease_repository: WeComMediaLeaseRepository | None = None,
        uploader: Any = None,
        corp_id: str = "",
        now: datetime | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._lease_repository = lease_repository or PostgresWeComMediaLeaseRepository()
        self._uploader = uploader
        self._corp_id = _text(corp_id) or _text(load_wecom_execution_config().corp_id)
        self._now = now

    @property
    def corp_id(self) -> str:
        return self._corp_id

    def ensure_ready(
        self,
        material_kind: str,
        material_id: int,
        *,
        upload_kind: str = "",
        force_refresh: bool = False,
        minimum_validity: timedelta = DEFAULT_MINIMUM_VALIDITY,
    ) -> dict[str, Any]:
        normalized_kind = _text(material_kind).lower()
        normalized_upload = _text(upload_kind).lower() or ("attachment" if normalized_kind == "attachment" else "image")
        item_id = int(material_id or 0)
        if normalized_kind not in {"image", "attachment", "miniprogram"} or item_id <= 0:
            raise WeComMediaLeaseError("invalid_material_reference", "Invalid media-library material reference")
        if normalized_upload not in {"image", "attachment"}:
            raise WeComMediaLeaseError("invalid_upload_kind", "Invalid WeCom media upload kind")
        if not self._corp_id:
            raise WeComMediaLeaseError("wecom_corp_id_missing", "WeCom corp id is required for media leases")

        if normalized_kind == "miniprogram":
            item = self._material("miniprogram", item_id)
            thumb_image_id = item.get("thumb_image_id")
            if thumb_image_id not in (None, ""):
                resolved = self.ensure_ready(
                    "image",
                    int(thumb_image_id),
                    upload_kind="image",
                    force_refresh=force_refresh,
                    minimum_validity=minimum_validity,
                )
                expires_at = _parse_datetime(resolved.get("provider_expires_at")) or (_utc(self._now) + PROVIDER_TTL)
                self._media_repository.cache_miniprogram_thumb_media_id(str(item_id), _text(resolved.get("media_id")), expires_at)
                return {**resolved, "requested_material_kind": "miniprogram", "requested_material_id": item_id}

        key = MediaLeaseKey(
            corp_id=self._corp_id,
            material_kind=normalized_kind,
            material_id=item_id,
            upload_kind=normalized_upload,
        )
        now = _utc(self._now)
        existing = self._lease_repository.get(key)
        if not force_refresh and self._usable(existing, now=now, minimum_validity=minimum_validity):
            self._lease_repository.mark_used(key, now=now)
            return dict(existing or {})

        token = uuid4().hex
        if not self._lease_repository.claim_refresh(key, token=token, now=now):
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.1)
                existing = self._lease_repository.get(key)
                if self._usable(existing, now=_utc(self._now), minimum_validity=minimum_validity):
                    return dict(existing or {})
            raise WeComMediaLeaseError("media_refresh_in_progress", "WeCom media refresh is already in progress", retryable=True)

        upload_invoked = False
        try:
            item = self._material(normalized_kind, item_id)
            file_name, content_type, file_bytes = self._source_payload(normalized_kind, item_id, item)
            digest = hashlib.sha256(file_bytes).hexdigest()
            upload_invoked = True
            uploaded = self._upload(normalized_upload, file_name=file_name, content_type=content_type, file_bytes=file_bytes)
            media_id = _text(uploaded.get("media_id"))
            if not media_id:
                raise WeComMediaLeaseError(
                    "provider_response_invalid",
                    "provider_response_invalid",
                    provider_error_classification="terminal",
                    provider_stage="upload_attachment" if normalized_upload == "attachment" else "upload",
                    provider_result_received=True,
                    provider_call_executed=True,
                )
            created_at = _provider_created_at(uploaded, now)
            expires_at = created_at + PROVIDER_TTL
            refresh_after = expires_at - REFRESH_MARGIN
            ready = self._lease_repository.mark_ready(
                key,
                token=token,
                media_id=media_id,
                content_sha256=digest,
                provider_created_at=created_at,
                provider_expires_at=expires_at,
                refresh_after=refresh_after,
            )
            self._cache_legacy_projection(normalized_kind, item_id, media_id, expires_at)
            return ready
        except WeComMediaLeaseError as exc:
            self._lease_repository.mark_failed(
                key,
                token=token,
                error_code=exc.code,
                error_message=str(exc),
                retryable=exc.retryable,
                now=now,
            )
            raise
        except WeComMediaUploadClientError as exc:
            provider_error = _provider_lease_error(exc)
            self._lease_repository.mark_failed(
                key,
                token=token,
                error_code=provider_error.code,
                error_message=str(provider_error),
                retryable=provider_error.retryable,
                now=now,
            )
            raise provider_error from exc
        except Exception as exc:
            error_code = "external_call_unknown" if upload_invoked else "local_execution_error"
            self._lease_repository.mark_failed(
                key,
                token=token,
                error_code=error_code,
                error_message=error_code,
                retryable=True,
                now=now,
            )
            raise WeComMediaLeaseError(
                error_code,
                error_code,
                retryable=True,
                provider_error_classification="retryable" if upload_invoked else "",
                provider_stage=("upload_attachment" if normalized_upload == "attachment" else "upload") if upload_invoked else "",
                provider_result_received=False,
                provider_call_executed=upload_invoked,
            ) from exc

    def list_due_materials(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self._corp_id:
            return []
        return self._lease_repository.list_due_materials(corp_id=self._corp_id, now=_utc(self._now), limit=limit)

    def metrics(self) -> dict[str, int]:
        if not self._corp_id:
            return {
                "total_count": 0,
                "ready_count": 0,
                "refresh_due_count": 0,
                "refreshing_count": 0,
                "failed_count": 0,
                "invalid_source_count": 0,
                "expired_count": 0,
            }
        return self._lease_repository.metrics(corp_id=self._corp_id, now=_utc(self._now))

    @staticmethod
    def _usable(lease: dict[str, Any] | None, *, now: datetime, minimum_validity: timedelta) -> bool:
        if not lease or _text(lease.get("status")) != "ready" or not _text(lease.get("media_id")):
            return False
        expires_at = _parse_datetime(lease.get("provider_expires_at"))
        return bool(expires_at and expires_at > now + minimum_validity)

    def _material(self, kind: str, item_id: int) -> dict[str, Any]:
        item = self._media_repository.get_item(kind, str(item_id), include_data=True)
        if not item:
            raise WeComMediaLeaseError("material_not_found", f"{kind} material {item_id} was not found")
        if item.get("enabled") is False:
            raise WeComMediaLeaseError("material_disabled", f"{kind} material {item_id} is disabled")
        return dict(item)

    def _source_payload(self, kind: str, item_id: int, item: dict[str, Any]) -> tuple[str, str, bytes]:
        if kind == "miniprogram":
            raw = _text(item.get("thumb_image_base64"))
            file_name = f"miniprogram_thumb_{item_id}.png"
            content_type = "image/png"
        else:
            raw = _text(item.get("data_base64"))
            file_name = _text(item.get("file_name")) or f"{kind}_{item_id}.bin"
            content_type = _text(item.get("mime_type") or item.get("content_type")) or (
                "image/png" if kind == "image" else "application/octet-stream"
            )
        if not raw:
            raise WeComMediaLeaseError("material_source_missing", f"{kind} material {item_id} has no reusable source payload")
        if raw.lower().startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            payload = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise WeComMediaLeaseError("material_source_invalid", f"{kind} material {item_id} source payload is invalid") from exc
        if not payload:
            raise WeComMediaLeaseError("material_source_missing", f"{kind} material {item_id} source payload is empty")
        if kind in {"image", "miniprogram"} and content_type == "image/webp":
            try:
                from PIL import Image

                source = Image.open(io.BytesIO(payload)).convert("RGB")
                output = io.BytesIO()
                source.save(output, format="JPEG", quality=92, optimize=True)
                payload = output.getvalue()
                file_name = file_name.rsplit(".", 1)[0] + ".jpg"
                content_type = "image/jpeg"
            except Exception as exc:
                raise WeComMediaLeaseError("image_conversion_failed", f"{kind} material {item_id} WebP conversion failed") from exc
        return file_name, content_type, payload

    def _upload(self, upload_kind: str, *, file_name: str, content_type: str, file_bytes: bytes) -> dict[str, Any]:
        if self._uploader is None:
            self._uploader = build_wecom_media_upload_client()
        if upload_kind == "attachment":
            return dict(self._uploader.upload_attachment(file_name, file_bytes, content_type) or {})
        return dict(self._uploader.upload_image(file_name, file_bytes, content_type) or {})

    def _cache_legacy_projection(self, kind: str, item_id: int, media_id: str, expires_at: datetime) -> None:
        if kind == "attachment":
            self._media_repository.cache_attachment_media_id(str(item_id), media_id, expires_at)
        elif kind == "miniprogram":
            self._media_repository.cache_miniprogram_thumb_media_id(str(item_id), media_id, expires_at)
        else:
            self._media_repository.cache_image_media_id(str(item_id), media_id, expires_at)


def build_wecom_media_lease_manager(*, uploader: Any = None, now: datetime | None = None) -> WeComMediaLeaseManager:
    from aicrm_next.shared.runtime import raw_database_url

    return WeComMediaLeaseManager(
        PostgresMediaLibraryRepository(raw_database_url()),
        uploader=uploader,
        now=now,
    )
