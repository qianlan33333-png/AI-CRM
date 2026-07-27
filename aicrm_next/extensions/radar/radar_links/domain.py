from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import secrets
import time
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime import production_environment


RADAR_LINK_OWNER = "radar_links"
RADAR_STATE_TTL_SECONDS = 10 * 60
RADAR_VIEWER_SESSION_TTL_SECONDS = 2 * 60 * 60
RADAR_VIEWER_SESSION_PURPOSE = "radar_viewer_session"
TARGET_TYPES = {"link", "image", "pdf"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
PDF_MIME_TYPE = "application/pdf"


def _urlsafe_b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _urlsafe_b64decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode((payload + padding).encode("ascii"))


def _state_secret(secret_key: str | None) -> bytes:
    secret = str(secret_key or "").strip()
    if not secret and production_environment():
        raise ContractError("radar signing secret is required")
    secret = secret or "radar-links-local-contract-secret"
    return secret.encode("utf-8")


def sign_radar_state(*, code: str, secret_key: str | None, now: int | None = None) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = {
        "code": str(code or "").strip(),
        "nonce": secrets.token_urlsafe(12),
        "exp": issued_at + RADAR_STATE_TTL_SECONDS,
    }
    if not payload["code"]:
        raise ContractError("code is required")
    body = _urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_state_secret(secret_key), body.encode("ascii"), sha256).hexdigest()
    return f"{body}.{signature}"


def verify_radar_state(state: str | None, *, secret_key: str | None, now: int | None = None) -> dict[str, Any]:
    value = str(state or "").strip()
    if "." not in value:
        raise ContractError("invalid radar oauth state")
    body, signature = value.rsplit(".", 1)
    expected = hmac.new(_state_secret(secret_key), body.encode("ascii"), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ContractError("invalid radar oauth state")
    try:
        payload = json.loads(_urlsafe_b64decode(body).decode("utf-8"))
    except Exception as exc:
        raise ContractError("invalid radar oauth state") from exc
    allowed_keys = {"code", "nonce", "exp"}
    if set(payload) != allowed_keys:
        raise ContractError("invalid radar oauth state")
    code = str(payload.get("code") or "").strip()
    exp = int(payload.get("exp") or 0)
    if not code:
        raise ContractError("invalid radar oauth state")
    if exp < int(now if now is not None else time.time()):
        raise ContractError("radar oauth state expired")
    return payload


def _digest(value: str, *, secret_key: str | None) -> str:
    secret = _state_secret(secret_key)
    return hmac.new(secret, str(value or "").encode("utf-8"), sha256).hexdigest()


def sign_viewer_session(
    *,
    code: str,
    openid: str = "",
    unionid: str = "",
    external_userid: str = "",
    secret_key: str | None,
    now: int | None = None,
) -> str:
    issued_at = int(now if now is not None else time.time())
    canonical_identity = {
        "openid": str(openid or "").strip(),
        "unionid": str(unionid or "").strip(),
        "external_userid": str(external_userid or "").strip(),
    }
    identity = str(canonical_identity["unionid"] or canonical_identity["openid"] or canonical_identity["external_userid"] or "anonymous").strip()
    payload = {
        "purpose": RADAR_VIEWER_SESSION_PURPOSE,
        "code": str(code or "").strip(),
        "identity_hash": _digest(identity, secret_key=secret_key)[:24],
        "identity": canonical_identity,
        "iat": issued_at,
        "exp": issued_at + RADAR_VIEWER_SESSION_TTL_SECONDS,
    }
    if not payload["code"]:
        raise ContractError("code is required")
    body = _urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_state_secret(secret_key), body.encode("ascii"), sha256).hexdigest()
    return f"{body}.{signature}"


def verify_viewer_session(token: str | None, *, code: str, secret_key: str | None, now: int | None = None) -> dict[str, Any]:
    value = str(token or "").strip()
    if "." not in value:
        raise ContractError("radar viewer session required")
    body, signature = value.rsplit(".", 1)
    expected = hmac.new(_state_secret(secret_key), body.encode("ascii"), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ContractError("invalid radar viewer session")
    try:
        payload = json.loads(_urlsafe_b64decode(body).decode("utf-8"))
    except Exception as exc:
        raise ContractError("invalid radar viewer session") from exc
    if set(payload) != {"purpose", "code", "identity_hash", "identity", "iat", "exp"}:
        raise ContractError("invalid radar viewer session")
    if payload.get("purpose") != RADAR_VIEWER_SESSION_PURPOSE:
        raise ContractError("invalid radar viewer session")
    if str(payload.get("code") or "") != str(code or "").strip():
        raise ContractError("invalid radar viewer session")
    current_time = int(now if now is not None else time.time())
    issued_at = int(payload.get("iat") or 0)
    expires_at = int(payload.get("exp") or 0)
    if issued_at <= 0 or issued_at > current_time:
        raise ContractError("invalid radar viewer session")
    if expires_at < current_time or expires_at - issued_at > RADAR_VIEWER_SESSION_TTL_SECONDS:
        raise ContractError("radar viewer session expired")
    identity = payload.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"openid", "unionid", "external_userid"}:
        raise ContractError("invalid radar viewer session")
    normalized_identity = {
        "openid": str(identity.get("openid") or "").strip(),
        "unionid": str(identity.get("unionid") or "").strip(),
        "external_userid": str(identity.get("external_userid") or "").strip(),
    }
    identity_value = str(normalized_identity["unionid"] or normalized_identity["openid"] or normalized_identity["external_userid"] or "anonymous")
    if str(payload.get("identity_hash") or "") != _digest(identity_value, secret_key=secret_key)[:24]:
        raise ContractError("invalid radar viewer session")
    payload["identity"] = normalized_identity
    return payload


def _is_forbidden_host(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def validate_original_url(original_url: str) -> str:
    value = str(original_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ContractError("original_url must use http or https")
    if not parsed.hostname or _is_forbidden_host(parsed.hostname):
        raise ContractError("original_url host is not allowed")
    return value


def normalize_target_type(value: str | None) -> str:
    normalized = str(value or "link").strip().lower()
    if normalized not in TARGET_TYPES:
        raise ContractError("target_type must be link, image, or pdf")
    return normalized


def validate_media_for_target(target_type: str, media_item: dict[str, Any] | None) -> dict[str, Any]:
    if target_type == "link":
        return {}
    if not media_item:
        raise ContractError("media_item_id is required")
    mime_type = str(media_item.get("mime_type") or media_item.get("content_type") or "").split(";")[0].strip().lower()
    file_name = str(media_item.get("file_name") or "").strip()
    file_size = int(media_item.get("file_size") or 0)
    if file_size <= 0:
        raise ContractError("media file is empty")
    if target_type == "image":
        if mime_type not in IMAGE_MIME_TYPES:
            raise ContractError("image radar content must be JPEG, PNG, or WEBP")
        if file_size > 10 * 1024 * 1024:
            raise ContractError("image file too large; max 10MB")
    if target_type == "pdf":
        if mime_type != PDF_MIME_TYPE:
            raise ContractError("pdf radar content must be application/pdf")
        if file_size > 50 * 1024 * 1024:
            raise ContractError("pdf file too large; max 50MB")
    return {
        "file_name_snapshot": file_name,
        "mime_type_snapshot": mime_type,
        "file_size_snapshot": file_size,
    }


def hash_ip(ip: str, *, secret_key: str | None) -> str:
    value = str(ip or "").strip()
    if not value:
        return ""
    return _digest(value, secret_key=secret_key)[:32]


def normalize_radar_link_payload(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    target_type = normalize_target_type(str(payload.get("target_type") or "link")) if not partial or "target_type" in payload else ""
    if target_type:
        normalized["target_type"] = target_type
    if not partial or "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ContractError("title is required")
        normalized["title"] = title
    if not partial or "original_url" in payload:
        original_url = str(payload.get("original_url") or "").strip()
        if target_type in {"", "link"}:
            normalized["original_url"] = validate_original_url(original_url)
        elif original_url:
            normalized["original_url"] = validate_original_url(original_url)
        else:
            normalized["original_url"] = ""
    if not partial or "media_item_id" in payload:
        normalized["media_item_id"] = str(payload.get("media_item_id") or "").strip()
    if not partial or "preview_mode" in payload:
        normalized["preview_mode"] = str(payload.get("preview_mode") or "").strip()
    for key in ("file_name_snapshot", "mime_type_snapshot"):
        if not partial or key in payload:
            normalized[key] = str(payload.get(key) or "").strip()
    if not partial or "file_size_snapshot" in payload:
        normalized["file_size_snapshot"] = int(payload.get("file_size_snapshot") or 0)
    for key in ("enabled", "auth_required"):
        if not partial or key in payload:
            normalized[key] = bool(payload.get(key))
    for key in ("source_channel", "campaign_id", "staff_id", "created_by"):
        if not partial or key in payload:
            normalized[key] = str(payload.get(key) or "").strip()
    return normalized


def radar_link_projection(item: dict[str, Any], *, base_url: str = "") -> dict[str, Any]:
    code = str(item.get("code") or "").strip()
    wrapper_path = f"/r/{code}" if code else ""
    wrapper_url = f"{base_url.rstrip('/')}{wrapper_path}" if base_url and wrapper_path else wrapper_path
    return {
        "id": int(item.get("id") or 0),
        "link_id": int(item.get("id") or 0),
        "code": code,
        "title": str(item.get("title") or ""),
        "target_type": normalize_target_type(str(item.get("target_type") or "link")),
        "original_url": str(item.get("original_url") or ""),
        "media_item_id": str(item.get("media_item_id") or ""),
        "preview_mode": str(item.get("preview_mode") or ""),
        "file_name_snapshot": str(item.get("file_name_snapshot") or ""),
        "mime_type_snapshot": str(item.get("mime_type_snapshot") or ""),
        "file_size_snapshot": int(item.get("file_size_snapshot") or 0),
        "pdf_processing_status": str(item.get("pdf_processing_status") or ""),
        "pdf_page_count": int(item.get("pdf_page_count") or 0),
        "pdf_preview_error_code": str(item.get("pdf_preview_error_code") or ""),
        "pdf_preview_error_message": str(item.get("pdf_preview_error_message") or ""),
        "wrapper_url": wrapper_url,
        "enabled": bool(item.get("enabled", True)),
        "auth_required": bool(item.get("auth_required", True)),
        "source_channel": str(item.get("source_channel") or ""),
        "campaign_id": str(item.get("campaign_id") or ""),
        "staff_id": str(item.get("staff_id") or ""),
        "created_by": str(item.get("created_by") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "stats_summary": item.get("stats_summary") if isinstance(item.get("stats_summary"), dict) else {},
    }
