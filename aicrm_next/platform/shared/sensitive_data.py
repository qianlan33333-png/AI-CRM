from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from typing import Any


SECRET_MASK = "[redacted]"
PII_MASK = "[pii]"

_SECRET_KEY_FRAGMENTS = {
    "accesstoken",
    "aeskey",
    "apikey",
    "authorization",
    "callbacktoken",
    "cookie",
    "credential",
    "password",
    "passwd",
    "paysign",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "signaturesecret",
    "signingsecret",
    "token",
}
_PII_IDENTIFIER_KEY_FRAGMENTS = {
    "actorid",
    "businessid",
    "email",
    "externaluserid",
    "idcard",
    "mobile",
    "openid",
    "outtradeno",
    "owneruserid",
    "phonenumber",
    "phone",
    "senderuserid",
    "targetid",
    "transactionid",
    "unionid",
    "userid",
}
_PII_CONTENT_KEYS = {
    "answer",
    "answers",
    "message",
    "messages",
    "messagebody",
    "messagecontent",
    "questionnaireanswers",
    "remarkmobiles",
}
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(authorization|token|secret|password|passwd|cookie|api[_-]?key)\s*([:=])\s*"
    r"(?:Bearer\s+|Basic\s+)?[^\s,;]+"
)
_AUTHORIZATION_PATTERN = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{4,}")
_PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_WECOM_IDENTIFIER_PATTERN = re.compile(r"\b(?:wm|wo|wx)[A-Za-z0-9_-]{6,}\b", re.IGNORECASE)
_NAMED_IDENTIFIER_PATTERN = re.compile(
    r"(?i)\b(external_userid|externaluserid|unionid|openid)\s*([:=])\s*[^\s,;]+"
)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _is_secret_key(key: Any, *, sensitive_keys: frozenset[str]) -> bool:
    raw = str(key or "").strip().upper()
    if raw and raw in sensitive_keys:
        return True
    normalized = _normalized_key(key)
    if normalized.endswith(("alg", "configured", "count", "fingerprint", "hash", "length", "present")):
        return False
    return bool(normalized) and any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def _is_pii_identifier_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized.endswith(("configured", "count", "fingerprint", "hash", "length", "present")):
        return False
    return bool(normalized) and any(fragment in normalized for fragment in _PII_IDENTIFIER_KEY_FRAGMENTS)


def _redact_pii_content(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(item_key): _redact_pii_content(item_value) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_pii_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_pii_content(item) for item in value)
    if isinstance(value, set):
        return sorted((_redact_pii_content(item) for item in value), key=str)
    return PII_MASK


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return text
    text = _PEM_PRIVATE_KEY_PATTERN.sub(SECRET_MASK, text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{SECRET_MASK}", text)
    text = _AUTHORIZATION_PATTERN.sub(SECRET_MASK, text)
    text = _NAMED_IDENTIFIER_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{PII_MASK}", text)
    text = _MOBILE_PATTERN.sub(PII_MASK, text)
    return _WECOM_IDENTIFIER_PATTERN.sub(PII_MASK, text)


def redact_sensitive_data(
    value: Any,
    *,
    key: str = "",
    sensitive_keys: Iterable[str] = (),
) -> Any:
    normalized_sensitive_keys = frozenset(str(item or "").strip().upper() for item in sensitive_keys if str(item or "").strip())
    if _is_secret_key(key, sensitive_keys=normalized_sensitive_keys):
        return SECRET_MASK
    if _normalized_key(key) in _PII_CONTENT_KEYS:
        return _redact_pii_content(value)
    if _is_pii_identifier_key(key):
        return PII_MASK
    if isinstance(value, Mapping):
        setting_key = str(value.get("key") or value.get("setting_key") or value.get("name") or "").strip().upper()
        sensitive_setting = bool(setting_key and setting_key in normalized_sensitive_keys)
        return {
            str(item_key): (
                SECRET_MASK
                if sensitive_setting and _normalized_key(item_key) in {"value", "displayvalue", "currentvalue"}
                else redact_sensitive_data(item_value, key=str(item_key), sensitive_keys=normalized_sensitive_keys)
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item, sensitive_keys=normalized_sensitive_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item, sensitive_keys=normalized_sensitive_keys) for item in value)
    if isinstance(value, set):
        return sorted(
            (redact_sensitive_data(item, sensitive_keys=normalized_sensitive_keys) for item in value),
            key=str,
        )
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def stable_hmac_identifier(value: Any, *, secret: str | bytes, namespace: str = "identifier") -> str:
    secret_bytes = secret if isinstance(secret, bytes) else str(secret or "").encode("utf-8")
    if not secret_bytes:
        raise ValueError("HMAC secret is required")
    normalized_namespace = str(namespace or "identifier").strip() or "identifier"
    material = f"{normalized_namespace}\0{str(value if value is not None else '')}".encode("utf-8")
    digest = hmac.new(secret_bytes, material, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest[:32]}"
