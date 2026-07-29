from __future__ import annotations

from typing import Any


_CUSTOMER_PLACEHOLDER_TEXTS = {
    "customer_name",
    "display_name",
    "name",
    "remark",
    "description",
    "mobile",
    "phone",
    "title",
    "external_userid",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def customer_text(value: Any) -> str:
    value_text = _text(value)
    if not value_text or value_text.lower() in _CUSTOMER_PLACEHOLDER_TEXTS:
        return ""
    return value_text


def _customer_mobile(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _first_named_value(*candidates: tuple[str, Any]) -> tuple[str, str]:
    for source, value in candidates:
        value_text = customer_text(value)
        if value_text:
            return value_text, source
    return "未命名客户", "default"


def resolve_customer_payload(
    *,
    context: dict[str, Any],
    binding: dict[str, Any],
    contacts: dict[str, Any] | None,
    identity_map: dict[str, Any] | None,
    external_userid: str,
    owner_userid: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    customer = dict(context.get("customer") or {})
    customer_binding = dict(customer.get("binding") or {})
    contact = dict(customer.get("contact") or {})
    contacts_row = dict(contacts or {})
    identity_row = dict(identity_map or {})
    display_name, display_name_source = _first_named_value(
        ("contacts.remark", contacts_row.get("remark")),
        ("contacts.customer_name", contacts_row.get("customer_name")),
        ("wecom_external_contact_identity_map.name", identity_row.get("name")),
        ("customer.display_name", customer.get("display_name")),
        ("customer.customer_name", customer.get("customer_name")),
        ("customer.remark", customer.get("remark")),
        ("customer.contact.name", contact.get("name")),
        ("binding.display_name", binding.get("display_name")),
        ("binding.customer_name", binding.get("customer_name")),
        ("binding.remark", binding.get("remark")),
    )
    resolved_owner = _text(owner_userid) or _text(customer.get("owner_userid")) or _text(identity_row.get("follow_user_userid"))
    mobile = (
        _customer_mobile(binding.get("mobile"))
        or _customer_mobile(customer.get("mobile"))
        or _customer_mobile(customer_binding.get("mobile"))
    )
    context_binding = dict(context.get("binding") or {})
    if not binding:
        binding_source = "none"
    elif context_binding and binding == context_binding:
        binding_source = "context.binding"
    else:
        binding_source = "fresh_binding_status"
    return (
        {
            "display_name": display_name,
            "avatar_text": display_name[:1] if display_name else "",
            "mobile": mobile,
            "is_bound": bool(mobile),
            "external_userid": _text(external_userid),
            "owner_userid": resolved_owner,
        },
        {
            "display_name_source": display_name_source,
            "binding_source": binding_source,
        },
    )
