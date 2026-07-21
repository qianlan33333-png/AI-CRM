from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime import production_environment, runtime_setting


def canonical_public_base_url(request: Request) -> str:
    """Return the configured browser origin without trusting a production Host header."""

    configured = str(
        runtime_setting("AICRM_PUBLIC_BASE_URL")
        or runtime_setting("PUBLIC_BASE_URL")
        or runtime_setting("APP_BASE_URL")
        or ""
    ).strip()
    candidate = configured or str(request.base_url).rstrip("/")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError("public_base_url_invalid")
    if production_environment() and not configured:
        raise ContractError("public_base_url_required")
    if production_environment() and parsed.scheme != "https":
        raise ContractError("public_base_url_https_required")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


__all__ = ["canonical_public_base_url"]
