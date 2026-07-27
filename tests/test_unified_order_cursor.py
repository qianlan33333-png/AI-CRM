from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

import pytest

from aicrm_next.extensions.commerce.commerce.admin_transaction_detail import (
    UnifiedOrderPageCursor,
    decode_unified_order_cursor,
    encode_unified_order_cursor,
    unified_order_cursor_scope,
)
from aicrm_next.extensions.commerce.commerce.admin_unified_orders import list_orders


def _legacy_cursor(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def test_unified_order_cursor_keeps_legacy_offset_compatibility() -> None:
    decoded = decode_unified_order_cursor(
        _legacy_cursor(37),
        expected_scope="scope-not-required-for-legacy",
    )

    assert decoded == 37


def test_unified_order_cursor_round_trips_stable_key_and_scope() -> None:
    scope = unified_order_cursor_scope(
        ["wechat", "alipay", "wechat_shop"],
        {"status": "paid", "product_code": "course"},
    )
    token = encode_unified_order_cursor(
        created_at=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
        provider_rank=2,
        source_id=912,
        position=50,
        scope=scope,
    )

    decoded = decode_unified_order_cursor(token, expected_scope=scope)

    assert isinstance(decoded, UnifiedOrderPageCursor)
    assert decoded.created_at == datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc)
    assert decoded.provider_rank == 2
    assert decoded.source_id == 912
    assert decoded.position == 50
    assert decoded.scope == scope


def test_unified_order_cursor_rejects_filter_scope_change() -> None:
    original_scope = unified_order_cursor_scope(["wechat"], {"status": "paid"})
    changed_scope = unified_order_cursor_scope(["wechat"], {"status": "pending"})
    token = encode_unified_order_cursor(
        created_at="2026-07-25T08:30:00Z",
        provider_rank=3,
        source_id=1,
        position=1,
        scope=original_scope,
    )

    with pytest.raises(ValueError, match="cursor is invalid"):
        decode_unified_order_cursor(token, expected_scope=changed_scope)


def test_unified_order_list_rejects_cursor_with_nonzero_offset(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="cursor and offset cannot be used together"):
        list_orders(provider="all", limit=1, offset=1, cursor=_legacy_cursor(1))
