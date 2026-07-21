from __future__ import annotations

"""Neutral typed configuration and token runtime for WeCom providers."""

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Any, Callable

from aicrm_next.shared.runtime_settings import runtime_setting


WECOM_EXECUTION_MODE_KEY = "AICRM_WECOM_EXECUTION_MODE"
WECOM_ENABLED_EFFECT_TYPES_KEY = "AICRM_WECOM_ENABLED_EFFECT_TYPES"
WECOM_TIMEOUT_KEY = "AICRM_WECOM_TIMEOUT_SECONDS"
LEGACY_DELETE_AFTER = "2026-10-01"
LEGACY_OWNER = "integration_gateway"
RUNTIME_ENVIRONMENT_KEYS = {
    WECOM_EXECUTION_MODE_KEY,
    WECOM_ENABLED_EFFECT_TYPES_KEY,
    WECOM_TIMEOUT_KEY,
    "AICRM_WECOM_API_BASE",
    "AICRM_WECOM_DEFAULT_SENDER_USERID",
    "AICRM_WECOM_PROVIDER_TARGET_POLICY",
    "AICRM_WECOM_CANARY_ALLOWED_MEDIA_TARGETS",
    "AICRM_NEXT_WECOM_REAL_CALLS_ENABLED",
    "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE",
    "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES",
    "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS",
    "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS",
    "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_OPS_WEBHOOK_KEYS",
    "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_CHAT_IDS",
    "WECOM_API_BASE",
    "WECOM_TIMEOUT_SECONDS",
    "WECOM_CORP_ID",
    "WECOM_CONTACT_SECRET",
    "WECOM_SECRET",
}
_MISSING = "__aicrm_wecom_runtime_missing__"
_TRUE = {"1", "true", "yes", "y", "on", "enabled", "execute"}
_FALSE = {"0", "false", "no", "n", "off", "disabled"}
TOKEN_INVALID_ERRCODES = {40014, 42001, 42007, 42009}
RETRYABLE_PROVIDER_ERRCODES = {-1, 45009, 45011}
TERMINAL_CONFIG_ERRCODES = {40001, 40013, 41001}
TERMINAL_PERMISSION_ERRCODES = {48002, 60011, 301002}
TERMINAL_TRUST_ERRCODES = {60020}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _setting(name: str) -> tuple[str, bool]:
    value = runtime_setting(name, _MISSING)
    return ("" if value == _MISSING else _text(value), value != _MISSING)


def _bool_value(value: str) -> bool | None:
    normalized = _text(value).lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return None


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in _text(value).replace("\n", ",").split(",") if item.strip()))


def _bounded_timeout(value: str) -> float:
    try:
        parsed = float(value or "15")
    except (TypeError, ValueError):
        parsed = 15.0
    return max(0.1, min(parsed, 60.0))


@dataclass(frozen=True)
class WeComExecutionConfig:
    execution_mode: str
    execution_mode_source: str
    api_base: str
    timeout_seconds: float
    timeout_source: str
    corp_id: str
    contact_secret: str
    contact_secret_source: str
    enabled_effect_types: tuple[str, ...]
    enabled_effect_types_source: str
    default_sender_userid: str
    deprecated_settings_present: tuple[str, ...]
    conflict: bool
    blocking_reasons: tuple[str, ...]

    @property
    def real_calls_enabled(self) -> bool:
        return self.execution_mode == "execute" and not self.conflict and not {
            "wecom_corp_id_missing",
            "wecom_contact_secret_missing",
        }.intersection(self.blocking_reasons)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.real_calls_enabled,
            "execution_mode": self.execution_mode,
            "execution_mode_source": self.execution_mode_source,
            "api_base_configured": bool(self.api_base),
            "timeout_seconds": self.timeout_seconds,
            "timeout_source": self.timeout_source,
            "corp_id_present": bool(self.corp_id),
            "contact_secret_present": bool(self.contact_secret),
            "contact_secret_source": self.contact_secret_source,
            "enabled_effect_types": list(self.enabled_effect_types),
            "enabled_effect_types_source": self.enabled_effect_types_source,
            "default_sender_userid_present": bool(self.default_sender_userid),
            "deprecated_settings_present": list(self.deprecated_settings_present),
            "deprecated_settings_owner": LEGACY_OWNER,
            "deprecated_settings_delete_after": LEGACY_DELETE_AFTER,
            "conflict": self.conflict,
            "blocking_reasons": list(self.blocking_reasons),
        }


def load_wecom_execution_config() -> WeComExecutionConfig:
    explicit_mode, explicit_present = _setting(WECOM_EXECUTION_MODE_KEY)
    normalized_explicit = explicit_mode.lower()
    legacy_mode_settings = (
        "AICRM_NEXT_WECOM_REAL_CALLS_ENABLED",
        "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE",
    )
    legacy_values: list[tuple[str, bool]] = []
    deprecated_present: list[str] = []
    invalid_settings: list[str] = []
    for name in legacy_mode_settings:
        value, present = _setting(name)
        if not present:
            continue
        deprecated_present.append(name)
        parsed = _bool_value(value)
        if parsed is None:
            invalid_settings.append(name)
        else:
            legacy_values.append((name, parsed))

    conflict = bool(legacy_values and len({value for _name, value in legacy_values}) > 1)
    if explicit_present:
        if normalized_explicit not in {"disabled", "dry_run", "execute"}:
            execution_mode = "disabled"
            invalid_settings.append(WECOM_EXECUTION_MODE_KEY)
        else:
            execution_mode = normalized_explicit
        execution_mode_source = WECOM_EXECUTION_MODE_KEY
        expected_enabled = execution_mode == "execute"
        if any(value != expected_enabled for _name, value in legacy_values):
            conflict = True
    elif legacy_values:
        execution_mode = "execute" if legacy_values[0][1] and not conflict else "disabled"
        execution_mode_source = legacy_values[0][0] if not conflict else "legacy_conflict"
    else:
        execution_mode = "disabled"
        execution_mode_source = "default"

    enabled_types, enabled_types_present = _setting(WECOM_ENABLED_EFFECT_TYPES_KEY)
    legacy_types, legacy_types_present = _setting("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")
    if legacy_types_present:
        deprecated_present.append("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")
    if enabled_types_present:
        effect_types = _csv(enabled_types)
        effect_types_source = WECOM_ENABLED_EFFECT_TYPES_KEY
    else:
        effect_types = _csv(legacy_types) if legacy_types_present else ()
        effect_types_source = "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES" if legacy_types_present else "default_empty"

    api_base, api_base_present = _setting("AICRM_WECOM_API_BASE")
    legacy_api_base, legacy_api_base_present = _setting("WECOM_API_BASE")
    if legacy_api_base_present:
        deprecated_present.append("WECOM_API_BASE")
    if not api_base_present:
        api_base = legacy_api_base
    api_base = (api_base or "https://qyapi.weixin.qq.com").rstrip("/")

    timeout_raw, timeout_present = _setting(WECOM_TIMEOUT_KEY)
    timeout_source = WECOM_TIMEOUT_KEY if timeout_present else "default"
    legacy_timeout, legacy_timeout_present = _setting("WECOM_TIMEOUT_SECONDS")
    if legacy_timeout_present:
        deprecated_present.append("WECOM_TIMEOUT_SECONDS")
    if not timeout_present:
        if legacy_timeout_present:
            timeout_raw = legacy_timeout
            timeout_source = "WECOM_TIMEOUT_SECONDS"

    corp_id, _corp_present = _setting("WECOM_CORP_ID")
    contact_secret, contact_secret_present = _setting("WECOM_CONTACT_SECRET")
    contact_secret_source = "WECOM_CONTACT_SECRET" if contact_secret_present else ""
    legacy_secret, legacy_secret_present = _setting("WECOM_SECRET")
    if legacy_secret_present:
        deprecated_present.append("WECOM_SECRET")
    if not contact_secret_present:
        if legacy_secret_present:
            contact_secret = legacy_secret
            contact_secret_source = "WECOM_SECRET"
    sender, sender_present = _setting("AICRM_WECOM_DEFAULT_SENDER_USERID")
    legacy_sender, legacy_sender_present = _setting("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS")
    if legacy_sender_present:
        deprecated_present.append("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS")
        if not sender_present:
            sender = (_csv(legacy_sender) or ("",))[0]

    blocking_reasons = list(dict.fromkeys(f"invalid_setting:{name}" for name in invalid_settings))
    if conflict:
        blocking_reasons.append("wecom_execution_config_conflict")
        execution_mode = "disabled"
    if execution_mode == "disabled":
        blocking_reasons.append("wecom_execution_disabled")
    if execution_mode == "execute" and not corp_id:
        blocking_reasons.append("wecom_corp_id_missing")
    if execution_mode == "execute" and not contact_secret:
        blocking_reasons.append("wecom_contact_secret_missing")
    return WeComExecutionConfig(
        execution_mode=execution_mode,
        execution_mode_source=execution_mode_source,
        api_base=api_base,
        timeout_seconds=_bounded_timeout(timeout_raw),
        timeout_source=timeout_source,
        corp_id=corp_id,
        contact_secret=contact_secret,
        contact_secret_source=contact_secret_source,
        enabled_effect_types=effect_types,
        enabled_effect_types_source=effect_types_source,
        default_sender_userid=sender,
        deprecated_settings_present=tuple(dict.fromkeys(deprecated_present)),
        conflict=conflict,
        blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
    )


class WeComProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        classification: str,
        payload: dict[str, Any] | None = None,
        provider_errcode: int = 0,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        real_external_call_executed: bool = True,
    ) -> None:
        super().__init__(error_code)
        self.message = _text(message) or error_code
        self.error_code = error_code
        self.classification = classification
        self.payload = dict(payload or {})
        self.provider_errcode = int(provider_errcode or 0)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.real_external_call_executed = bool(real_external_call_executed)

    @property
    def retryable(self) -> bool:
        return self.classification == "retryable"


def classify_wecom_provider_error(
    *,
    provider_errcode: int = 0,
    status_code: int | None = None,
    transport_error: bool = False,
    timeout: bool = False,
) -> tuple[str, str]:
    if timeout:
        return "timeout", "retryable"
    if transport_error:
        return "network_error", "retryable"
    if status_code in {408, 429}:
        return ("http_408" if status_code == 408 else "rate_limited"), "retryable"
    if status_code is not None and status_code >= 500:
        return "http_5xx", "retryable"
    if status_code in {401, 403}:
        return "permission_denied", "terminal"
    if status_code is not None and status_code >= 400:
        return f"http_{status_code}", "terminal"
    if provider_errcode in RETRYABLE_PROVIDER_ERRCODES:
        return ("rate_limited" if provider_errcode in {45009, 45011} else "provider_busy"), "retryable"
    if provider_errcode in TERMINAL_CONFIG_ERRCODES:
        return "config_invalid", "terminal"
    if provider_errcode in TERMINAL_TRUST_ERRCODES:
        return "ip_not_trusted", "terminal"
    if provider_errcode in TERMINAL_PERMISSION_ERRCODES:
        return "permission_denied", "terminal"
    if provider_errcode:
        return f"wecom_error_{provider_errcode}", "terminal"
    return "provider_response_invalid", "terminal"


class SingleFlightAccessTokenProvider:
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._condition = threading.Condition()
        self._refreshing = False
        self._token = ""
        self._expires_at = 0.0
        self._last_error: Exception | None = None
        self._last_error_at = 0.0

    def get(self, refresh: Callable[[], tuple[str, int]]) -> str:
        with self._condition:
            while self._refreshing:
                self._condition.wait()
            if self._token and self._expires_at > self._now():
                return self._token
            if self._last_error is not None and self._last_error_at + 1.0 > self._now():
                raise self._last_error
            self._refreshing = True
        try:
            token, expires_in = refresh()
            token = _text(token)
            if not token:
                raise ValueError("access token refresh returned an empty token")
        except Exception as exc:
            with self._condition:
                self._last_error = exc
                self._last_error_at = self._now()
                self._refreshing = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._token = token
            self._expires_at = self._now() + max(1, int(expires_in or 7200) - 60)
            self._last_error = None
            self._refreshing = False
            self._condition.notify_all()
            return self._token

    def invalidate(self, token: str = "") -> None:
        with self._condition:
            if not token or token == self._token:
                self._token = ""
                self._expires_at = 0.0


_TOKEN_PROVIDER_LOCK = threading.Lock()
_TOKEN_PROVIDERS: dict[str, SingleFlightAccessTokenProvider] = {}


def shared_token_provider(*, corp_id: str, secret: str, api_base: str) -> SingleFlightAccessTokenProvider:
    digest = hashlib.sha256(f"{corp_id}\0{secret}\0{api_base}".encode("utf-8")).hexdigest()
    with _TOKEN_PROVIDER_LOCK:
        return _TOKEN_PROVIDERS.setdefault(digest, SingleFlightAccessTokenProvider())


def reset_shared_token_providers() -> None:
    with _TOKEN_PROVIDER_LOCK:
        _TOKEN_PROVIDERS.clear()
