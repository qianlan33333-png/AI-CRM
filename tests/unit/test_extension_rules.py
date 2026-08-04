from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.extensions.commerce.commerce.domain import (
    normalize_status,
    validate_completion_redirect,
    validate_price_cents,
    validate_product_code,
    validate_quantity,
)
from aicrm_next.extensions.commerce.commerce.dto import ProductUpsertRequest
from aicrm_next.extensions.commerce.commerce.repo import InMemoryCommerceRepository
from aicrm_next.extensions.forms.questionnaire.domain import (
    score_and_tags,
    validate_required_answers,
)
from aicrm_next.extensions.radar.radar_links.domain import (
    sign_radar_state,
    validate_original_url,
    verify_radar_state,
)
from aicrm_next.platform.shared.errors import ContractError


pytestmark = pytest.mark.unit


def test_commerce_domain_rejects_invalid_money_quantity_and_redirects() -> None:
    assert validate_product_code("service_period_12m") == "service_period_12m"
    assert validate_price_cents(0) == 0
    assert validate_quantity(1) == 1
    assert normalize_status("PAID") == "paid"
    assert validate_completion_redirect(True, "/admin/orders")["completion_redirect_url"] == "/admin/orders"
    with pytest.raises(ContractError):
        validate_price_cents(-1)
    with pytest.raises(ContractError):
        validate_quantity(0)
    with pytest.raises(ContractError):
        validate_completion_redirect(True, "javascript:alert(1)")


def test_product_wecom_tagging_config_is_normalized_and_persisted() -> None:
    request = ProductUpsertRequest(
        product_code="tagged_product",
        title="企微标签商品",
        wecom_tagging={
            "enabled": True,
            "tag_ids": [" tag_paid ", "tag_paid", "tag_registered"],
        },
    )
    product = InMemoryCommerceRepository(products=[], orders=[]).save_product(request.model_dump())
    assert product["wecom_tagging"] == {
        "enabled": True,
        "tag_ids": ["tag_paid", "tag_registered"],
        "owner_userid": "",
    }
    with pytest.raises(ValidationError, match="至少选择一个标签"):
        ProductUpsertRequest(
            product_code="invalid_tagged_product",
            title="无标签商品",
            wecom_tagging={"enabled": True, "tag_ids": []},
        )


def test_questionnaire_required_answers_score_and_tag_current_options() -> None:
    questionnaire = {
        "id": 1,
        "slug": "current",
        "title": "当前问卷",
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "title": "选择阶段",
                "required": True,
                "options": [
                    {"id": "a", "label": "A", "score": 3, "tag_codes": ["stage_a"]},
                    {"id": "b", "label": "B", "score": 1, "tag_codes": ["stage_b"]},
                ],
            }
        ],
    }
    validate_required_answers(questionnaire, {"q1": "a"})
    assert score_and_tags(questionnaire, {"q1": "a"}) == (3, ["stage_a"])
    with pytest.raises(ContractError, match="missing required answer"):
        validate_required_answers(questionnaire, {})


def test_radar_state_is_signed_expiring_and_ssrf_safe() -> None:
    state = sign_radar_state(code="radar-1", secret_key="test-secret", now=1_700_000_000)
    assert verify_radar_state(state, secret_key="test-secret", now=1_700_000_100)["code"] == "radar-1"
    with pytest.raises(ContractError):
        verify_radar_state(state, secret_key="other-secret", now=1_700_000_100)
    assert validate_original_url("https://example.com/content") == "https://example.com/content"
    with pytest.raises(ContractError):
        validate_original_url("http://127.0.0.1/private")
