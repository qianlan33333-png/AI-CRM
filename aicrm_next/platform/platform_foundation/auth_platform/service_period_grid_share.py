from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any

from aicrm_next.platform.shared.runtime import require_signing_secret


TOKEN_VERSION = 1


@dataclass(frozen=True)
class ServicePeriodGridShareClaims:
    service_product_id: str
    public_id: str
    generation: int


def issue_service_period_grid_share_token(
    *,
    service_product_id: Any,
    public_id: Any,
    generation: Any,
) -> str:
    claims = {
        "v": TOKEN_VERSION,
        "spid": str(service_product_id or "").strip(),
        "pid": str(public_id or "").strip(),
        "gen": int(generation or 0),
    }
    if not claims["spid"] or not claims["pid"] or claims["gen"] < 1:
        raise ValueError("service period grid share claims are incomplete")
    body = _b64(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify_service_period_grid_share_token(token: Any) -> ServicePeriodGridShareClaims | None:
    value = str(token or "").strip()
    if "." not in value:
        return None
    body, supplied = value.rsplit(".", 1)
    try:
        supplied_signature = _unb64(supplied)
    except Exception:
        return None
    expected_signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    try:
        claims = json.loads(_unb64(body).decode("utf-8"))
        generation = int(claims.get("gen") or 0)
    except Exception:
        return None
    service_product_id = str(claims.get("spid") or "").strip()
    public_id = str(claims.get("pid") or "").strip()
    if int(claims.get("v") or 0) != TOKEN_VERSION or not service_product_id or not public_id or generation < 1:
        return None
    return ServicePeriodGridShareClaims(
        service_product_id=service_product_id,
        public_id=public_id,
        generation=generation,
    )


def _secret() -> bytes:
    return require_signing_secret(
        "AICRM_SERVICE_PERIOD_GRID_SHARE_SECRET",
        fallback_env_keys=("SECRET_KEY",),
        local_fallback="aicrm-next-dev-service-period-grid-share",
    )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    if _b64(decoded) != value:
        raise ValueError("non-canonical base64")
    return decoded
