from __future__ import annotations

from aicrm_next.shared.typing import JsonDict


def list_item_projection(customer: JsonDict) -> JsonDict:
    binding = dict(customer.get("binding") or {})
    identity = dict(customer.get("identity") or {})
    is_bound = bool(binding.get("is_bound")) if binding else bool(customer.get("mobile"))
    return {
        "unionid": customer.get("unionid") or identity.get("unionid") or "",
        "external_userid": customer.get("external_userid"),
        "customer_name": customer.get("customer_name"),
        "owner_userid": customer.get("owner_userid"),
        "owner_display_name": customer.get("owner_display_name"),
        "remark": customer.get("remark") or "",
        "description": customer.get("description") or "",
        "mobile": customer.get("mobile"),
        "is_bound": is_bound,
        "binding_status": binding.get("binding_status") or ("bound" if is_bound else "unbound"),
        "follow_user_userids": [
            str(item.get("userid") or "").strip()
            for item in list(customer.get("follow_users") or [])
            if str(item.get("userid") or "").strip()
        ],
        "tags": list(customer.get("tags") or []),
        "class_user_status": dict(customer.get("class_user_status") or {}),
        "last_message_at": customer.get("last_message_at"),
        "last_touch_at": customer.get("last_touch_at"),
        "updated_at": customer.get("updated_at") or customer.get("last_touch_at") or customer.get("last_message_at"),
    }


def detail_projection(customer: JsonDict) -> JsonDict:
    payload = dict(customer)
    binding = dict(payload.get("binding") or {"is_bound": bool(payload.get("mobile")), "mobile": payload.get("mobile")})
    binding.setdefault("binding_status", "bound" if binding.get("is_bound") else "unbound")
    payload["binding"] = binding
    payload["is_bound"] = bool(binding.get("is_bound"))
    payload["binding_status"] = binding.get("binding_status")
    payload.setdefault(
        "identity",
        {"person_id": payload.get("person_id"), "external_userid": payload.get("external_userid"), "mobile": payload.get("mobile")},
    )
    payload.setdefault("follow_users", [])
    payload["follow_user_userids"] = [
        str(item.get("userid") or "").strip()
        for item in list(payload.get("follow_users") or [])
        if str(item.get("userid") or "").strip()
    ]
    payload.setdefault("remark", "")
    payload.setdefault("description", "")
    payload.setdefault("owner_display_name", payload.get("owner_userid") or "")
    payload.setdefault("marketing_summary", {})
    payload.setdefault("marketing_profile", {})
    payload.setdefault("contact", {"external_userid": payload.get("external_userid"), "name": payload.get("customer_name")})
    payload.setdefault("sidebar_context", {})
    payload.setdefault("updated_at", payload.get("last_touch_at") or payload.get("last_message_at"))
    return payload
