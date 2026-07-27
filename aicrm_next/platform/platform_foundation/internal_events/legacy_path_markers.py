from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from aicrm_next.platform.shared.safe_logging import safe_log_exception

from .config import legacy_path_markers_enabled, legacy_path_retire_after_days

LOGGER = logging.getLogger(__name__)

_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "openid",
    "unionid",
    "mobile",
    "phone",
    "external_userid",
    "external_user_id",
    "userid",
    "webhook_url",
    "url",
)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)

_LOCK = Lock()
_COUNTERS: dict[str, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hash16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _looks_sensitive(key: str, value: str) -> bool:
    lowered_key = key.lower()
    lowered_value = value.lower()
    if any(fragment in lowered_key for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return True
    if _URL_RE.search(value) or _PHONE_RE.search(value):
        return True
    if any(fragment in lowered_value for fragment in ("access_token=", "corpsecret=", "openid", "unionid", "secret")):
        return True
    return False


def _redacted_ref(value: str, *, prefix: str = "ref") -> str:
    text = _text(value)
    return f"{prefix}:{_hash16(text)}" if text else ""


def _safe_field(key: str, value: Any, *, hash_always: bool = False) -> str:
    text = _text(value)
    if not text:
        return ""
    if hash_always or _looks_sensitive(key, text):
        return _redacted_ref(text, prefix=f"{key}_ref")
    return text


def reset_legacy_path_marker_state() -> None:
    with _LOCK:
        _COUNTERS.clear()


def mark_legacy_path_invoked(
    *,
    legacy_path: str,
    replacement_event_type: str,
    replacement_consumer: str | None,
    source_module: str,
    source_route: str,
    aggregate_id: str = "",
    reason: str = "",
    severity: str = "info",
) -> dict[str, Any]:
    """Record an observation-only marker for a legacy path.

    This function must never affect business behavior. It returns a small public
    marker payload for tests/diagnostics and swallows logging/counter failures.
    """

    if not legacy_path_markers_enabled():
        return {"recorded": False, "reason": "legacy_path_markers_disabled", "real_external_call_executed": False}

    try:
        now = _now()
        path = _safe_field("legacy_path", legacy_path)
        event_type = _safe_field("replacement_event_type", replacement_event_type)
        consumer = _safe_field("replacement_consumer", replacement_consumer or "")
        module = _safe_field("source_module", source_module)
        route = _safe_field("source_route", source_route)
        aggregate_ref = _safe_field("aggregate_id", aggregate_id, hash_always=True)
        safe_reason = _safe_field("reason", reason)
        safe_severity = _safe_field("severity", severity) or "info"
        retire_after_days = legacy_path_retire_after_days()
        retire_after = f"{retire_after_days}d_after_no_hits"
        marker = {
            "event": "legacy_internal_event_path_invoked",
            "legacy_path": path,
            "replacement_event_type": event_type,
            "replacement_consumer": consumer,
            "source_module": module,
            "source_route": route,
            "aggregate_id": aggregate_ref,
            "reason": safe_reason,
            "severity": safe_severity,
            "retire_after": retire_after,
            "real_external_call_executed": False,
        }
        with _LOCK:
            current = _COUNTERS.get(path, {})
            count = int(current.get("legacy_path_invocation_count") or 0) + 1
            last_seen = now.isoformat().replace("+00:00", "Z")
            _COUNTERS[path] = {
                "legacy_path": path,
                "replacement_event_type": event_type,
                "replacement_consumer": consumer,
                "legacy_path_invocation_count": count,
                "last_invoked_at": last_seen,
                "last_aggregate_id_redacted": aggregate_ref,
                "last_source_module": module,
                "last_source_route": route,
                "last_reason": safe_reason,
                "last_severity": safe_severity,
                "retire_after": retire_after,
            }
        LOGGER.info("legacy_internal_event_path_invoked", extra=marker)
        return {"recorded": True, **marker}
    except Exception as exc:
        safe_log_exception(LOGGER, "legacy_internal_event_path_marker_failed", exc)
        return {"recorded": False, "reason": "legacy_path_marker_failed", "real_external_call_executed": False}


def legacy_path_marker_diagnostics() -> dict[str, Any]:
    retire_after_days = legacy_path_retire_after_days()
    cutoff = _now() - timedelta(days=retire_after_days)
    with _LOCK:
        items = [dict(item) for item in _COUNTERS.values()]
    items.sort(key=lambda item: item.get("legacy_path") or "")
    total = sum(int(item.get("legacy_path_invocation_count") or 0) for item in items)
    last_seen = max([_text(item.get("last_invoked_at")) for item in items] or [""])
    for item in items:
        last_invoked = _text(item.get("last_invoked_at"))
        try:
            parsed = datetime.fromisoformat(last_invoked.replace("Z", "+00:00")) if last_invoked else None
        except ValueError:
            parsed = None
        item["legacy_path_retire_candidate"] = bool(parsed and parsed <= cutoff)
    return {
        "legacy_path_markers_enabled": legacy_path_markers_enabled(),
        "legacy_path_retire_after_days": retire_after_days,
        "legacy_paths": items,
        "legacy_path_invocation_count": total,
        "legacy_path_last_seen": last_seen,
        "legacy_path_retire_candidate": bool(items) and all(bool(item.get("legacy_path_retire_candidate")) for item in items),
        "real_external_call_executed": False,
    }
