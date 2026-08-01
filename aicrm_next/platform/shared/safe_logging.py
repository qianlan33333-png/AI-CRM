from __future__ import annotations

import logging
from typing import Any

from .sensitive_data import redact_sensitive_data, redact_sensitive_text


def safe_exception_summary(exc: BaseException, *, limit: int = 300) -> str:
    """Return one redacted diagnostic line without serializing SQL parameters."""

    first_line = next(
        (line.strip() for line in str(exc or "").splitlines() if line.strip()),
        type(exc).__name__,
    )
    return redact_sensitive_text(first_line)[: max(0, int(limit or 0))]


def safe_log_fields(**fields: Any) -> dict[str, Any]:
    redacted = redact_sensitive_data(fields)
    return dict(redacted) if isinstance(redacted, dict) else {}


def safe_log_exception(
    logger: Any,
    message: str,
    exc: BaseException,
    *,
    level: int = logging.ERROR,
    **fields: Any,
) -> None:
    extra = safe_log_fields(**fields)
    extra["error_type"] = type(exc).__name__
    extra["error_detail"] = safe_exception_summary(exc)
    logger.log(level, redact_sensitive_text(message), extra=extra)
