from __future__ import annotations

from .crm.customer_read_model.application import GetCustomerDetailQuery
from .crm.customer_read_model.dto import CustomerDetailRequest
from .crm.identity_contact.application import GetSidebarContactBindingStatusQuery


def build_sidebar_contact_binding_status_query() -> GetSidebarContactBindingStatusQuery:
    return GetSidebarContactBindingStatusQuery(customer_detail_query=get_customer_detail)


def get_customer_detail(external_userid: str) -> dict:
    return GetCustomerDetailQuery()(CustomerDetailRequest(external_userid=external_userid))


__all__ = ["build_sidebar_contact_binding_status_query", "get_customer_detail"]
