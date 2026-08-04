from __future__ import annotations

import base64

import pytest

from aicrm_next.channels.channel_entry.wecom_crypto import (
    WeComCallbackError,
    compute_signature,
    decrypt_message,
    encrypt_message,
    parse_callback_xml,
    validate_callback_timestamp,
    verify_signature,
)
from aicrm_next.channels.integration_gateway.idempotency import (
    get_or_create,
    make_idempotency_key,
    reset_idempotency_store,
)
from aicrm_next.platform.shared.wecom_payload_contract import (
    normalize_group_admin_userids,
    normalize_miniprogram_attachment_payload,
)


pytestmark = pytest.mark.unit


def test_wecom_crypto_round_trip_and_signature() -> None:
    aes_key = base64.b64encode(b"a" * 32).decode("ascii").rstrip("=")
    encrypted = encrypt_message("<xml><Event>add_external_contact</Event></xml>", aes_key, "corp-1")
    signature = compute_signature("token", "1700000000", "nonce", encrypted)
    verify_signature("token", "1700000000", "nonce", encrypted, signature)
    assert "add_external_contact" in decrypt_message(encrypted, aes_key, "corp-1")
    with pytest.raises(WeComCallbackError):
        verify_signature("token", "1700000000", "nonce", encrypted, "bad")


def test_callback_timestamp_and_xml_fail_closed() -> None:
    validate_callback_timestamp("1700000000", now=1700000000, max_age_seconds=300)
    with pytest.raises(WeComCallbackError):
        validate_callback_timestamp("1699990000", now=1700000000, max_age_seconds=300)
    assert parse_callback_xml("<xml><Event><![CDATA[change_external_contact]]></Event></xml>")["Event"] == "change_external_contact"


def test_gateway_idempotency_uses_canonical_payload() -> None:
    reset_idempotency_store()
    key_a = make_idempotency_key(operation="send", payload={"b": 2, "a": 1})
    key_b = make_idempotency_key(operation="send", payload={"a": 1, "b": 2})
    calls: list[str] = []
    first = get_or_create(key_a, lambda: calls.append("called") or {"provider_id": "fake-1"})
    second = get_or_create(key_b, lambda: calls.append("called-again") or {"provider_id": "fake-2"})
    assert key_a == key_b
    assert first == second
    assert calls == ["called"]


def test_wecom_payloads_are_normalized_once_at_the_boundary() -> None:
    assert normalize_group_admin_userids('[{"userid":"u1"},"u2","u1"]') == ["u1", "u2"]
    payload = normalize_miniprogram_attachment_payload(
        {"appid": "wx-app", "pagepath": "/pages/home", "title": "课程", "thumb_media_id": "media-1"}
    )
    assert payload == {"appid": "wx-app", "page": "/pages/home", "title": "课程", "pic_media_id": "media-1"}
