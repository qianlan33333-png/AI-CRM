from __future__ import annotations

"""Canonical product-code vocabulary shared across bounded contexts."""

from typing import Any


SUBSCRIPTION_TRIAL_MONTH_NAME = "订阅会员"
WECHAT_SHOP_SUBSCRIPTION_PRODUCT_NAME = "老黄的一人公司实践与思考.订阅会员"

PRODUCT_CODE_ALIASES = {
    "prd_20260518095708_9f77db": "subscription_trial_month",
    "prd_20260601055439_3c4f56": "premium_monthly_trial",
    "15383271146": "subscription_trial_month",
    WECHAT_SHOP_SUBSCRIPTION_PRODUCT_NAME: "subscription_trial_month",
}

PRODUCT_NAME_ALIASES = {
    "subscription_trial_month": SUBSCRIPTION_TRIAL_MONTH_NAME,
    WECHAT_SHOP_SUBSCRIPTION_PRODUCT_NAME: SUBSCRIPTION_TRIAL_MONTH_NAME,
}


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def canonical_product_code(product_code: Any) -> str:
    code = _normalized_text(product_code)
    return PRODUCT_CODE_ALIASES.get(code, code)


def canonical_product_name(product_code: Any, product_name: Any = "") -> str:
    code = canonical_product_code(product_code)
    name = _normalized_text(product_name)
    if code in PRODUCT_NAME_ALIASES:
        return PRODUCT_NAME_ALIASES[code]
    return PRODUCT_NAME_ALIASES.get(name, name)


def product_code_filter_values(product_code: Any) -> list[str]:
    code = _normalized_text(product_code)
    if not code:
        return []
    canonical = canonical_product_code(code)
    values = {code, canonical}
    values.update(alias for alias, target in PRODUCT_CODE_ALIASES.items() if target == canonical)
    return sorted(value for value in values if value)
