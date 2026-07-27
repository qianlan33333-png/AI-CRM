from __future__ import annotations


def test_admin_customer_payload_keeps_successful_live_source_fallback_rows():
    from aicrm_next.crm.customer_read_model.admin_pages import _admin_customer_payload_from_list_result

    payload, page_error = _admin_customer_payload_from_list_result(
        result={
            "ok": True,
            "degraded": True,
            "source_status": "live_source_fallback",
            "read_model_status": "fallback",
            "customers": [{"external_userid": "wx_ext_001", "customer_name": "源表客户"}],
            "total": 23749,
        },
        keyword="",
        owner="",
        mobile="",
        tag="",
        limit=50,
        offset=0,
    )

    assert page_error == ""
    assert payload["customers"] == [{"external_userid": "wx_ext_001", "customer_name": "源表客户"}]
    assert payload["pagination"]["total"] == 23749
    assert payload["pagination"]["has_next"] is True


def test_admin_customer_payload_hides_unavailable_rows():
    from aicrm_next.crm.customer_read_model.admin_pages import ADMIN_CUSTOMERS_UNAVAILABLE_MESSAGE, _admin_customer_payload_from_list_result

    payload, page_error = _admin_customer_payload_from_list_result(
        result={
            "ok": False,
            "degraded": True,
            "source_status": "production_unavailable",
            "customers": [{"external_userid": "wx_ext_001"}],
            "total": 1,
            "page_error": "customer read unavailable",
        },
        keyword="",
        owner="",
        mobile="",
        tag="",
        limit=50,
        offset=0,
    )

    assert page_error == ADMIN_CUSTOMERS_UNAVAILABLE_MESSAGE
    assert payload["customers"] == []
    assert payload["pagination"]["total"] == 0
