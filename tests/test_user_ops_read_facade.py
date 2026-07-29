from __future__ import annotations

from aicrm_next.automation.ops_enrollment.application import GetUserOpsOverviewQuery, ListUserOpsCustomersQuery
from aicrm_next.automation.ops_enrollment.dto import UserOpsFilters, UserOpsListRequest


def test_user_ops_read_facade_is_next_native_query_surface():
    request = UserOpsListRequest(filters=UserOpsFilters(keyword="张"), limit=10)

    overview = GetUserOpsOverviewQuery().execute(request)
    customers = ListUserOpsCustomersQuery().execute(request)

    assert overview["route_owner"] == "ai_crm_next"
    assert customers["route_owner"] == "ai_crm_next"
    assert overview["fallback_used"] is False
    assert customers["fallback_used"] is False
    assert customers["items"]
