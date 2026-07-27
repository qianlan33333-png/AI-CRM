from __future__ import annotations

from .crm.customer_read_model.application import (
    GetCustomerContextQuery,
    GetCustomerDetailQuery,
    ListRecentMessagesQuery,
)
from .crm.customer_read_model.dto import (
    CustomerContextRequest,
    CustomerDetailRequest,
    RecentMessagesRequest,
)
from .crm.identity_contact.application import ResolvePersonIdentityQuery
from .crm.identity_contact.dto import ResolvePersonIdentityRequest
from .integration_gateway.dispatch import McpToolDispatcher
from .integration_gateway.mcp import McpJsonRpcApplication


def build_mcp_jsonrpc_application() -> McpJsonRpcApplication:
    return McpJsonRpcApplication(
        dispatcher=McpToolDispatcher(
            identity_resolver=_resolve_mobile,
            customer_detail_query=_customer_detail,
            customer_context_query=_customer_context,
            recent_messages_query=_recent_messages,
        )
    )


def _resolve_mobile(mobile: str) -> str:
    identity = ResolvePersonIdentityQuery()(ResolvePersonIdentityRequest(mobile=mobile))
    return str(getattr(identity, "external_userid", "") or "") if identity else ""


def _customer_detail(external_userid: str) -> dict:
    return GetCustomerDetailQuery()(CustomerDetailRequest(external_userid=external_userid))


def _customer_context(external_userid: str, recent_message_limit: int, timeline_limit: int) -> dict:
    return GetCustomerContextQuery()(
        CustomerContextRequest(
            external_userid=external_userid,
            recent_message_limit=recent_message_limit,
            timeline_limit=timeline_limit,
        )
    )


def _recent_messages(external_userid: str, limit: int) -> dict:
    return ListRecentMessagesQuery()(RecentMessagesRequest(external_userid=external_userid, limit=limit))


__all__ = ["build_mcp_jsonrpc_application"]
