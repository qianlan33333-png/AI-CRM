from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.secret_store import (
    SECRET_REFERENCE_CUTOVER_KEY,
    SENSITIVE_SETTING_KEYS,
    FileSecretStore,
    SecretStoreError,
    parse_secret_reference,
)
from aicrm_next.shared.safe_logging import safe_log_exception

LOGGER = logging.getLogger(__name__)
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


@dataclass
class _RequestSettingsSnapshot:
    loaded: bool = False
    values: dict[str, str] = field(default_factory=dict)


_REQUEST_SETTINGS_SNAPSHOT: ContextVar[_RequestSettingsSnapshot | None] = ContextVar(
    "aicrm_request_settings_snapshot",
    default=None,
)


@contextmanager
def runtime_settings_request_scope() -> Iterator[None]:
    """Reuse one non-logging app-settings snapshot within a single request."""

    token = _REQUEST_SETTINGS_SNAPSHOT.set(_RequestSettingsSnapshot())
    try:
        yield
    finally:
        _REQUEST_SETTINGS_SNAPSHOT.reset(token)


def invalidate_runtime_settings_request_snapshot() -> None:
    """Force a fresh snapshot after an in-request app-settings write."""

    snapshot = _REQUEST_SETTINGS_SNAPSHOT.get()
    if snapshot is None:
        return
    snapshot.loaded = False
    snapshot.values.clear()


def _request_settings_snapshot() -> _RequestSettingsSnapshot | None:
    snapshot = _REQUEST_SETTINGS_SNAPSHOT.get()
    if snapshot is None or snapshot.loaded:
        return snapshot
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text("SELECT key, value FROM app_settings")).mappings().all()
        snapshot.values = {
            str(row.get("key") or "").strip(): str(row.get("value") or "").strip()
            for row in rows
            if str(row.get("key") or "").strip()
        }
    except (AttributeError, SQLAlchemyError, RuntimeError) as exc:
        safe_log_exception(
            LOGGER,
            "runtime app_settings request snapshot unavailable",
            exc,
            level=logging.DEBUG,
        )
        snapshot.values = {}
    snapshot.loaded = True
    return snapshot


def _cutover_enabled(conn=None) -> bool:
    if str(os.getenv(SECRET_REFERENCE_CUTOVER_KEY, "") or "").strip().lower() in _TRUE_VALUES:
        return True
    if conn is None:
        return False
    try:
        row = conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": SECRET_REFERENCE_CUTOVER_KEY},
        ).mappings().first()
    except SQLAlchemyError:
        return False
    return str((row or {}).get("value") or "").strip().lower() in _TRUE_VALUES


def _resolve_candidate(key: str, candidate: str, *, default: str, cutover_enabled: bool) -> str:
    normalized = str(candidate or "").strip()
    if normalized.startswith("secretref:"):
        try:
            reference = parse_secret_reference(normalized)
            if reference.key != key:
                raise SecretStoreError("secret reference does not match requested key")
            return FileSecretStore.from_environment().read(normalized).strip()
        except SecretStoreError:
            LOGGER.warning("runtime secret reference resolution failed for key=%s", key)
            return default
    if key in SENSITIVE_SETTING_KEYS and cutover_enabled and normalized:
        LOGGER.warning("runtime raw sensitive setting rejected after cutover for key=%s", key)
        return default
    return normalized


def runtime_setting(key: str, default: str = "") -> str:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return default
    fallback = str(default or "").strip()
    cutover_enabled = str(os.getenv(SECRET_REFERENCE_CUTOVER_KEY, "") or "").strip().lower() in _TRUE_VALUES
    request_snapshot = _request_settings_snapshot()
    if request_snapshot is not None:
        stored_value = request_snapshot.values.get(normalized_key)
        if not cutover_enabled:
            cutover_enabled = (
                request_snapshot.values.get(SECRET_REFERENCE_CUTOVER_KEY, "").strip().lower()
                in _TRUE_VALUES
            )
        candidate = stored_value if stored_value is not None else str(os.getenv(normalized_key, fallback) or "")
        return _resolve_candidate(
            normalized_key,
            candidate,
            default=fallback,
            cutover_enabled=cutover_enabled,
        )
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_settings WHERE key = :key"),
                {"key": normalized_key},
            ).mappings().first()
            stored_value = str((row or {}).get("value") or "").strip()
            if normalized_key in SENSITIVE_SETTING_KEYS and not stored_value.startswith("secretref:"):
                cutover_enabled = _cutover_enabled(conn)
    except (AttributeError, SQLAlchemyError, RuntimeError) as exc:
        safe_log_exception(LOGGER, "runtime app_settings read unavailable", exc, level=logging.DEBUG)
        row = None
    if row is not None:
        return _resolve_candidate(
            normalized_key,
            str(row.get("value") or ""),
            default=fallback,
            cutover_enabled=cutover_enabled,
        )
    return _resolve_candidate(
        normalized_key,
        str(os.getenv(normalized_key, fallback) or ""),
        default=fallback,
        cutover_enabled=cutover_enabled,
    )


def runtime_bool(key: str, default: bool = False) -> bool:
    fallback = "true" if default else ""
    return runtime_setting(key, fallback).lower() in _TRUE_VALUES


def runtime_csv(key: str) -> set[str]:
    raw = runtime_setting(key, "")
    return {item.strip() for item in re.split(r"[,\s]+", raw) if item.strip()}
