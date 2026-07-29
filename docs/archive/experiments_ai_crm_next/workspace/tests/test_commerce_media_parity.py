from __future__ import annotations

import json
import re
from pathlib import Path

from aicrm_next.commerce.parity_spec import DEFAULT_SAFE_ENDPOINTS as COMMERCE_DEFAULT_SAFE_ENDPOINTS
from aicrm_next.commerce.parity_spec import ENDPOINT_SPECS as COMMERCE_SPECS
from aicrm_next.commerce.parity_spec import WRITE_ENDPOINTS as COMMERCE_WRITE_ENDPOINTS
from aicrm_next.commerce.parity_spec import compare_endpoint_payloads as compare_commerce_payloads
from aicrm_next.commerce.parity_spec import validate_payload as validate_commerce_payload
from aicrm_next.engagement.media_library.parity_spec import ENDPOINT_SPECS as MEDIA_SPECS
from aicrm_next.engagement.media_library.parity_spec import compare_endpoint_payloads as compare_media_payloads
from aicrm_next.engagement.media_library.parity_spec import validate_payload as validate_media_payload
from conftest import make_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMERCE_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "old_commerce"
MEDIA_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "old_media_library"


def _fixture_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["payload"]


def test_commerce_and_media_specs_cover_required_surfaces() -> None:
    assert {"products.default", "product_detail.default", "checkout_wechat.default", "checkout_alipay.default", "wechat_transactions.default", "alipay_transactions.default"} <= set(COMMERCE_SPECS)
    assert {"image_library.default", "attachment_library.default", "miniprogram_library.default"} <= set(MEDIA_SPECS)


def test_commerce_default_safe_endpoints_exclude_checkout_writes() -> None:
    assert "checkout_wechat.default" not in COMMERCE_DEFAULT_SAFE_ENDPOINTS
    assert "checkout_alipay.default" not in COMMERCE_DEFAULT_SAFE_ENDPOINTS
    assert {"checkout_wechat.default", "checkout_alipay.default"} == set(COMMERCE_WRITE_ENDPOINTS)
    assert set(COMMERCE_DEFAULT_SAFE_ENDPOINTS) == {"products.default", "product_detail.default", "wechat_transactions.default", "alipay_transactions.default"}


def test_old_commerce_and_media_fixtures_conform_and_are_masked() -> None:
    phone_like = re.compile(r"1[3-9]\d{9}")
    for fixture_dir, validator in [(COMMERCE_FIXTURE_DIR, validate_commerce_payload), (MEDIA_FIXTURE_DIR, validate_media_payload)]:
        for path in fixture_dir.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            assert not phone_like.search(text)
            assert "openid_masked" in text or "openid" not in text
            assert "appid_masked" in text or "appid" not in text
            assert "external_user_masked" in text or "external_userid" not in text
            assert "transaction_masked" in text or "transaction_id" not in text
            assert not validator(path.stem, json.loads(text)["payload"])


def test_next_commerce_fake_checkout_still_conforms_to_parity_specs() -> None:
    client = make_client()
    for endpoint_name in COMMERCE_WRITE_ENDPOINTS:
        spec = COMMERCE_SPECS[endpoint_name]
        response = client.request(spec.method, spec.path, json=spec.body)
        assert response.status_code == spec.expected_status
        payload = response.json()
        assert payload["fake_payment"] is True
        assert payload["payment_status"] == "pending"
        assert not validate_commerce_payload(endpoint_name, payload)


def test_compare_tools_detect_missing_required_key() -> None:
    commerce_payload = _fixture_payload(COMMERCE_FIXTURE_DIR / "products.default.json")
    assert any(issue["rule"] == "required_key" for issue in compare_commerce_payloads("products.default", commerce_payload, {k: v for k, v in commerce_payload.items() if k != "items"}))
    media_payload = _fixture_payload(MEDIA_FIXTURE_DIR / "image_library.default.json")
    assert any(issue["rule"] == "required_key" for issue in compare_media_payloads("image_library.default", media_payload, {k: v for k, v in media_payload.items() if k != "items"}))
