from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime import production_environment, runtime_setting


TARGET_REF_PREFIX = "cpt_"
_TARGET_PRODUCT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _target_ref_secret() -> bytes:
    value = _text(runtime_setting("AICRM_NEXT_ACTION_TOKEN_SECRET")) or _text(
        runtime_setting("SECRET_KEY")
    )
    if not value and production_environment():
        raise ContractError("coupon target_ref secret is not configured")
    value = value or "aicrm-next-coupon-target-ref-local"
    return value.encode("utf-8")


def _target_ref_cipher() -> AESSIV:
    key = hashlib.sha256(b"aicrm:commerce:coupon-target:key:v1\x00" + _target_ref_secret()).digest()
    return AESSIV(key)


_TARGET_REF_ASSOCIATED_DATA = [b"aicrm:commerce:coupon-target:v1"]


def target_ref_for_product_id(product_id: Any) -> str:
    normalized = _text(product_id)
    if not _TARGET_PRODUCT_ID_PATTERN.fullmatch(normalized):
        raise ContractError("trade product id is required")
    encrypted = _target_ref_cipher().encrypt(
        normalized.encode("utf-8"),
        _TARGET_REF_ASSOCIATED_DATA,
    )
    encoded = base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")
    return f"{TARGET_REF_PREFIX}{encoded}"


def product_id_from_target_ref(target_ref: Any) -> str:
    """Verify and decode a server-generated opaque trade-product reference."""

    normalized = _text(target_ref)
    if not normalized.startswith(TARGET_REF_PREFIX):
        raise ContractError("invalid coupon target_ref")
    encoded = normalized[len(TARGET_REF_PREFIX) :]
    try:
        padding = "=" * (-len(encoded) % 4)
        encrypted = base64.urlsafe_b64decode(encoded + padding)
        product_id = _target_ref_cipher().decrypt(
            encrypted,
            _TARGET_REF_ASSOCIATED_DATA,
        ).decode("utf-8")
    except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
        raise ContractError("invalid coupon target_ref") from exc
    if not _TARGET_PRODUCT_ID_PATTERN.fullmatch(product_id):
        raise ContractError("invalid coupon target_ref")
    return product_id


def request_key_hash(value: str) -> str:
    normalized = _text(value)
    if not normalized:
        raise ContractError("Idempotency-Key is required")
    if len(normalized) > 200:
        raise ContractError("Idempotency-Key is too long")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "TARGET_REF_PREFIX",
    "product_id_from_target_ref",
    "request_key_hash",
    "target_ref_for_product_id",
]
