from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from aicrm_next.platform.shared.runtime import production_environment
from aicrm_next.platform.shared.runtime_settings import runtime_setting, startup_environment_setting

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def verify_signature(token: str, timestamp: str, nonce: str, signature: str) -> bool:
    parts = [_text(token), _text(timestamp), _text(nonce)]
    if not all(parts) or not _text(signature):
        return False
    digest = hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, _text(signature).lower())


def callback_token() -> str:
    return _text(runtime_setting("WECHAT_SHOP_CALLBACK_TOKEN"))


def should_skip_signature_without_token() -> bool:
    if (
        _text(startup_environment_setting("AICRM_NEXT_ENV")).lower() == "test"
        or not production_environment()
    ):
        return True
    logger.error("WECHAT_SHOP_CALLBACK_TOKEN is not configured; rejecting WeChat Shop callback")
    return False
