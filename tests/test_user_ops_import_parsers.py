from __future__ import annotations

from aicrm_next.automation.ops_enrollment.dto import UserOpsFilters
from aicrm_next.automation.ops_enrollment.user_ops import normalize_filters


def test_user_ops_next_filter_normalizer_accepts_current_status_filters():
    normalized = normalize_filters(
        UserOpsFilters(
            wecom_status="ADDED",
            mobile_binding_status="Bound",
            activation_bucket="Activated",
            class_term_no=" 8 ",
            keyword=" 客户 ",
            mobile=" 13800138000 ",
        )
    )

    assert normalized.wecom_status == "added"
    assert normalized.mobile_binding_status == "bound"
    assert normalized.activation_bucket == "activated"
    assert normalized.class_term_no == "8"
    assert normalized.keyword == "客户"
    assert normalized.mobile == "13800138000"


def test_user_ops_next_filter_normalizer_drops_unknown_enum_values():
    normalized = normalize_filters(UserOpsFilters(wecom_status="legacy_added", mobile_binding_status="legacy_bound"))

    assert normalized.wecom_status == ""
    assert normalized.mobile_binding_status == ""
