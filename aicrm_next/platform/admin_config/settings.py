from __future__ import annotations

from aicrm_next.platform.shared.secret_store import SENSITIVE_SETTING_KEYS
from aicrm_next.platform.shared.sensitive_data import SECRET_MASK

SENSITIVE_KEYS = SENSITIVE_SETTING_KEYS


def mask_value(key: str, value: str) -> str:
    if key not in SENSITIVE_KEYS:
        return value
    if not value:
        return ""
    return SECRET_MASK
