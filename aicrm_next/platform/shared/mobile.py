from __future__ import annotations

import re
from typing import Any


_MAINLAND_MOBILE_PATTERN = re.compile(r"1[3-9]\d{9}")
MOBILE_VALIDATION_MESSAGE = "手机号必须为11位有效的中国大陆手机号"


def normalize_mainland_mobile(value: Any, *, allow_country_code: bool = False) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    digits = re.sub(r"\D+", "", str(value or ""))
    if allow_country_code and len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits if _MAINLAND_MOBILE_PATTERN.fullmatch(digits) else ""
