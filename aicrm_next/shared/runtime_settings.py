from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from aicrm_next.runtime_configuration import (
    MANAGED_RUNTIME_SETTING_KEYS,
    RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
    parse_runtime_config_cutover_keys,
)
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


def environment_fallback(
    key: str,
    default: str = "",
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Centralize the temporary environment fallback used during config cutover.

    Business modules must resolve published settings through ``runtime_setting``.
    Repository adapters that already hold an explicit database transaction may
    use this helper only for the compatibility fallback, avoiding nested engine
    lookups while keeping direct environment access inside the platform boundary.
    """

    normalized_key = str(key or "").strip()
    if not normalized_key:
        return str(default or "")
    source = os.environ if environment is None else environment
    return str(source.get(normalized_key, default) or "")


def startup_environment_setting(key: str, default: str = "") -> str:
    """Read a process-start setting through the single environment boundary.

    Database URLs, public origins, release identity, and environment labels are
    deployment inputs rather than publishable business configuration.  Business
    modules may consume those values through this explicit boundary only.
    """

    return environment_fallback(key, default)


def environment_snapshot(
    keys: Iterable[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only requested compatibility values from the central env boundary."""

    source = os.environ if environment is None else environment
    return {
        normalized: str(source.get(normalized) or "")
        for key in keys
        for normalized in (str(key or "").strip(),)
        if normalized and normalized in source
    }


def environment_contains(
    key: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> bool:
    source = os.environ if environment is None else environment
    return str(key or "").strip() in source


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

    if _REQUEST_SETTINGS_SNAPSHOT.get() is not None:
        yield
        return
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
    if environment_fallback(SECRET_REFERENCE_CUTOVER_KEY).strip().lower() in _TRUE_VALUES:
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
            if key.endswith("_SECRET_REF"):
                if reference.key != key.removesuffix("_REF"):
                    raise SecretStoreError("secret reference does not match requested reference key")
                return normalized
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


def _raw_runtime_setting(key: str, default: str = "") -> str:
    """Resolve a setting without applying the expand/contract compatibility rule."""

    normalized_key = str(key or "").strip()
    if not normalized_key:
        return default
    fallback = str(default or "").strip()
    cutover_enabled = environment_fallback(SECRET_REFERENCE_CUTOVER_KEY).strip().lower() in _TRUE_VALUES
    request_snapshot = _request_settings_snapshot()
    if request_snapshot is not None:
        stored_value = request_snapshot.values.get(normalized_key)
        if not cutover_enabled:
            cutover_enabled = (
                request_snapshot.values.get(SECRET_REFERENCE_CUTOVER_KEY, "").strip().lower()
                in _TRUE_VALUES
            )
        candidate = stored_value if stored_value is not None else environment_fallback(normalized_key, fallback)
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
        environment_fallback(normalized_key, fallback),
        default=fallback,
        cutover_enabled=cutover_enabled,
    )


def _managed_runtime_setting(key: str, default: str = "") -> str:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return str(default or "")
    fallback = str(default or "").strip()
    with runtime_settings_request_scope():
        cutover_keys = parse_runtime_config_cutover_keys(
            _raw_runtime_setting(RUNTIME_CONFIG_CUTOVER_KEYS_KEY, "")
        )
        if normalized_key in cutover_keys or not environment_contains(normalized_key):
            return _raw_runtime_setting(normalized_key, fallback)
        candidate = environment_fallback(normalized_key, fallback)
        secret_cutover = (
            environment_fallback(SECRET_REFERENCE_CUTOVER_KEY).strip().lower()
            in _TRUE_VALUES
            or _raw_runtime_setting(SECRET_REFERENCE_CUTOVER_KEY, "").lower()
            in _TRUE_VALUES
        )
        return _resolve_candidate(
            normalized_key,
            candidate,
            default=fallback,
            cutover_enabled=secret_cutover,
        )


def runtime_setting(key: str, default: str = "") -> str:
    """Resolve one runtime setting through its registered cutover policy.

    Registration is intentionally enforced here, rather than only at selected
    callers, so existing helpers that resolve a dynamic key cannot bypass the
    environment-to-release expand/contract boundary.
    """

    normalized_key = str(key or "").strip()
    if normalized_key in MANAGED_RUNTIME_SETTING_KEYS:
        return _managed_runtime_setting(normalized_key, default)
    return _raw_runtime_setting(normalized_key, default)


def runtime_bool(key: str, default: bool = False) -> bool:
    raw = runtime_setting(key, "")
    if not raw:
        return bool(default)
    return raw.lower() in _TRUE_VALUES


def runtime_int(
    key: str,
    default: int = 0,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = runtime_setting(key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def managed_runtime_setting(key: str, default: str = "") -> str:
    """Resolve a migrated key without changing behavior before cutover.

    An existing environment value wins in observe mode.  ConfigRelease becomes
    authoritative only after the exact key is added to the cutover catalog.
    """

    return _managed_runtime_setting(key, default)


def managed_runtime_bool(key: str, default: bool = False) -> bool:
    raw = managed_runtime_setting(key, "")
    if not raw:
        return bool(default)
    return raw.lower() in _TRUE_VALUES


def managed_runtime_int(
    key: str,
    default: int = 0,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = managed_runtime_setting(key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def runtime_csv(key: str) -> set[str]:
    raw = runtime_setting(key, "")
    return {item.strip() for item in re.split(r"[,\s]+", raw) if item.strip()}


__all__ = [
    "environment_fallback",
    "environment_contains",
    "environment_snapshot",
    "invalidate_runtime_settings_request_snapshot",
    "managed_runtime_bool",
    "managed_runtime_int",
    "managed_runtime_setting",
    "runtime_bool",
    "runtime_csv",
    "runtime_int",
    "runtime_setting",
    "runtime_settings_request_scope",
]
