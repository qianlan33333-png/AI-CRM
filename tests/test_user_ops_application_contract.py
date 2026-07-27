from __future__ import annotations

from aicrm_next.automation.ops_enrollment.application import (
    GetUserOpsCardsQuery,
    GetUserOpsCustomerQuery,
    GetUserOpsCustomerTimelineQuery,
    GetUserOpsFilterOptionsQuery,
    GetUserOpsOverviewQuery,
    ListUserOpsCustomersQuery,
    reset_user_ops_fixture_state,
)
from aicrm_next.automation.ops_enrollment.dto import UserOpsFilters, UserOpsListRequest


def test_user_ops_next_application_read_queries_are_importable_and_executable():
    reset_user_ops_fixture_state()
    request = UserOpsListRequest(filters=UserOpsFilters(wecom_status="added"), limit=5)

    overview = GetUserOpsOverviewQuery().execute(request)
    cards = GetUserOpsCardsQuery().execute(request)
    filters = GetUserOpsFilterOptionsQuery().execute()
    customers = ListUserOpsCustomersQuery().execute(request)
    first = customers["items"][0]["unionid"]
    detail = GetUserOpsCustomerQuery().execute(first)
    timeline = GetUserOpsCustomerTimelineQuery().execute(first)

    for payload in (overview, cards, filters, customers, detail, timeline):
        assert payload["ok"] is True
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False

    assert overview["cards"]
    assert filters["filter_options"]
    assert detail["customer"]["unionid"] == first
    assert timeline["items"]
