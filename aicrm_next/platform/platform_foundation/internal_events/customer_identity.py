from __future__ import annotations

import hashlib
from typing import Any, Protocol

from aicrm_next.platform.platform_foundation.command_bus import CommandContext

from .config import customer_identity_internal_events_enabled, event_type_allowed
from .consumer_registry import InternalEventConsumerRegistry, current_internal_event_consumer_registry
from .legacy_path_markers import mark_legacy_path_invoked
from .models import InternalEvent, InternalEventConsumerResult, InternalEventConsumerRun
from .service import InternalEventService

CUSTOMER_PHONE_BOUND_EVENT_TYPE = "customer.phone_bound"


class BindMobileRequest(Protocol):
    external_userid: str | None
    mobile: str | None
    owner_userid: str | None
    bind_by_userid: str | None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mobile_digits(value: Any) -> str:
    return "".join(ch for ch in _text(value) if ch.isdigit())


def _mask_mobile(value: Any) -> str:
    digits = _mobile_digits(value)
    if not digits:
        return ""
    if len(digits) < 7:
        return "<redacted>"
    return f"{digits[:3]}****{digits[-4:]}"


def _mobile_hash(value: Any) -> str:
    digits = _mobile_digits(value)
    if not digits:
        return ""
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()[:16]


def _redact_external_userid(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"


def _hash_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _stable_identity_key(*, person_id: str, external_userid: str, mobile_hash: str) -> str:
    if person_id:
        return f"person:{person_id}"
    if external_userid:
        return f"external_userid:{_hash_text(external_userid)}"
    return f"mobile:{mobile_hash}"


def _stable_customer_key(*, unionid: str, person_id: str, external_userid: str, mobile_hash: str) -> str:
    if unionid:
        return f"unionid:{unionid}"
    return _stable_identity_key(person_id=person_id, external_userid=external_userid, mobile_hash=mobile_hash)


def _subject_id(*, external_userid: str, mobile_masked: str, mobile_hash: str) -> str:
    if external_userid:
        return _redact_external_userid(external_userid)
    if mobile_masked:
        return mobile_masked
    return f"mobile:{mobile_hash}" if mobile_hash else ""


def _skipped(reason: str, event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    return InternalEventConsumerResult(
        status="skipped",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary={"skipped": True, "reason": reason, "real_external_call_executed": False},
        result_summary={"reason": reason},
    )


def _mark_legacy_hook(event: InternalEvent, run: InternalEventConsumerRun, *, legacy_path: str, reason: str) -> None:
    mark_legacy_path_invoked(
        legacy_path=legacy_path,
        replacement_event_type=event.event_type,
        replacement_consumer=run.consumer_name,
        source_module="platform_foundation.internal_events.customer_identity",
        source_route=f"/internal-events/{event.event_type}/{run.consumer_name}",
        aggregate_id=event.aggregate_id or event.subject_id,
        reason=reason,
    )


def customer_identity_projection_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    binding = dict(payload.get("binding") or {}) if isinstance(payload.get("binding"), dict) else {}
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
        response_summary={
            "binding_status": _text(binding.get("binding_status") or "bound"),
            "projection": "noop",
            "real_external_call_executed": False,
        },
        result_summary={"customer_identity_projection": "phone_bound_confirmed"},
    )


def customer_summary_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="customer.phone_bound.legacy_profile_summary_hook",
        reason="phone_bound_summary_hook_replaced_by_internal_event_consumer",
    )
    return _skipped("customer_summary_not_configured", event, run)


def automation_phone_bound_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="customer.phone_bound.legacy_automation_hook",
        reason="phone_bound_automation_hook_replaced_by_internal_event_consumer",
    )
    return _skipped("automation_phone_bound_not_configured", event, run)


def customer_identity_ai_assist_notify_consumer(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
    _mark_legacy_hook(
        event,
        run,
        legacy_path="customer.phone_bound.legacy_ai_assist_notify",
        reason="phone_bound_ai_assist_notify_replaced_by_internal_event_consumer",
    )
    return _skipped("customer_identity_ai_assist_notify_not_configured", event, run)


def register_customer_identity_event_consumers(registry: InternalEventConsumerRegistry | None = None) -> None:
    registry = registry or current_internal_event_consumer_registry()
    registry.register(
        CUSTOMER_PHONE_BOUND_EVENT_TYPE,
        "customer_identity_projection_consumer",
        customer_identity_projection_consumer,
        consumer_type="projection",
    )
    registry.register(
        CUSTOMER_PHONE_BOUND_EVENT_TYPE,
        "customer_summary_consumer",
        customer_summary_consumer,
        consumer_type="projection",
    )
    registry.register(
        CUSTOMER_PHONE_BOUND_EVENT_TYPE,
        "automation_phone_bound_consumer",
        automation_phone_bound_consumer,
        consumer_type="orchestration",
    )
    registry.register(
        CUSTOMER_PHONE_BOUND_EVENT_TYPE,
        "customer_identity_ai_assist_notify_consumer",
        customer_identity_ai_assist_notify_consumer,
        consumer_type="orchestration",
    )


def emit_customer_phone_bound_event(
    *,
    request: BindMobileRequest,
    binding_result: dict[str, Any],
    source_module: str = "identity_contact.application",
    source_route: str = "identity_contact.bind_mobile",
) -> dict[str, Any]:
    if not customer_identity_internal_events_enabled():
        return {"status": "skipped", "reason": "customer_identity_internal_events_disabled"}
    if not event_type_allowed(CUSTOMER_PHONE_BOUND_EVENT_TYPE):
        return {"status": "skipped", "reason": "internal_events_disabled_or_event_type_not_allowed"}
    result = dict(binding_result or {})
    if not bool(result.get("ok")) or _text(result.get("binding_status")) != "bound":
        return {"status": "skipped", "reason": "customer_phone_binding_not_successful"}

    external_userid = _text(result.get("external_userid") or request.external_userid)
    unionid = _text(result.get("unionid"))
    mobile = _text(result.get("mobile") or request.mobile)
    person_id = _text(result.get("person_id"))
    mobile_hash = _mobile_hash(mobile)
    mobile_masked = _mask_mobile(mobile)
    if not mobile_hash:
        return {"status": "skipped", "reason": "mobile_missing"}

    register_customer_identity_event_consumers()
    stable_identity_key = _stable_customer_key(
        unionid=unionid,
        person_id=person_id,
        external_userid=external_userid,
        mobile_hash=mobile_hash,
    )
    trace_id = f"customer.phone_bound:{stable_identity_key}:{mobile_hash}"
    source = _text(request.bind_by_userid) or _text(result.get("source_status")) or "identity_contact"
    matched_by = _text(result.get("matched_by") or "bind_mobile")
    identity_map_id = result.get("identity_map_id")
    follow_user_userid = _text(result.get("follow_user_userid") or result.get("owner_userid") or request.owner_userid)

    event_result = InternalEventService().emit_event(
        event_type=CUSTOMER_PHONE_BOUND_EVENT_TYPE,
        event_version=1,
        aggregate_type="customer",
        aggregate_id=unionid or person_id or external_userid or f"mobile:{mobile_hash}",
        subject_type="unionid" if unionid else "customer",
        subject_id=unionid or _subject_id(external_userid=external_userid, mobile_masked=mobile_masked, mobile_hash=mobile_hash),
        idempotency_key=f"customer.phone_bound:{stable_identity_key}:{mobile_hash}",
        source_module=source_module,
        source_command_id=trace_id,
        correlation_id=trace_id,
        context=CommandContext(
            actor_id=_text(request.bind_by_userid),
            actor_type="system",
            trace_id=trace_id,
            request_id=trace_id,
            source_route=source_route,
        ),
        payload={
            "binding": {
                "unionid": unionid,
                "person_id": person_id,
                "external_userid": external_userid,
                "mobile": mobile,
                "mobile_masked": mobile_masked,
                "binding_status": _text(result.get("binding_status") or "bound"),
                "identity_map_id": identity_map_id,
                "follow_user_userid": follow_user_userid,
                "matched_by": matched_by,
                "owner_userid": _text(result.get("owner_userid") or request.owner_userid),
                "bind_by_userid": _text(request.bind_by_userid),
                "source_status": _text(result.get("source_status")),
            },
            "source": {
                "source_module": source_module,
                "source_route": source_route,
                "command_id": trace_id,
                "trace_id": trace_id,
            },
        },
        payload_summary={
            "unionid_present": bool(unionid),
            "person_id_present": bool(person_id),
            "external_userid_present": bool(external_userid),
            "mobile_masked": mobile_masked,
            "binding_status": _text(result.get("binding_status") or "bound"),
            "matched_by": matched_by,
            "source": source,
            "identity_map_id_present": identity_map_id not in (None, ""),
        },
    )
    return {
        "status": "emitted",
        "event_id": event_result["event"]["event_id"],
        "consumer_run_count": len(event_result.get("consumer_runs") or []),
    }
