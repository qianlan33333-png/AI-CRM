from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aicrm_next.shared.runtime_settings import runtime_bool, runtime_setting

from .payment_commerce_live_gateway import PaymentCommerceLiveGateway, build_payment_commerce_live_gateway


Json = dict[str, Any]
FLAG_ENABLED = "AICRM_PAYMENT_COMMERCE_LIVE_ADAPTER_ENABLED"
FLAG_APPROVED = "AICRM_PAYMENT_COMMERCE_LIVE_CALL_APPROVED"
FLAG_CONFIG_REVIEWED = "AICRM_PAYMENT_COMMERCE_PROVIDER_CONFIG_REVIEWED"
FLAG_SANDBOX_APPROVED = "AICRM_PAYMENT_COMMERCE_SANDBOX_MODE_APPROVED"
FLAG_NO_MONEY = "AICRM_PAYMENT_COMMERCE_NO_MONEY_MOVEMENT_CONFIRMED"
FLAG_PROVIDER_NAME = "AICRM_PAYMENT_COMMERCE_PROVIDER_NAME"
FLAG_PROVIDER_SECRET = "AICRM_PAYMENT_COMMERCE_PROVIDER_SECRET"
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_PAYMENT_COMMERCE_LIVE_ADAPTER_ENABLED",
        "AICRM_PAYMENT_COMMERCE_LIVE_CALL_APPROVED",
        "AICRM_PAYMENT_COMMERCE_NO_MONEY_MOVEMENT_CONFIRMED",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_CONFIG_REVIEWED",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_NAME",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_SECRET",
        "AICRM_PAYMENT_COMMERCE_SANDBOX_MODE_APPROVED",
    }
)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Json


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _present(name: str) -> bool:
    return bool(runtime_setting(name))


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(payload: Json) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact(value: str) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "***" if text else ""
    return f"{text[:2]}***{text[-2:]}"


def _side_effect_safety(*, provider_call_executed: bool = False) -> Json:
    return {
        "provider_call_executed": provider_call_executed,
        "network_call_executed": provider_call_executed,
        "real_payment_capture_executed": False,
        "real_refund_executed": False,
        "real_settlement_executed": False,
        "real_charge_executed": False,
        "production_payment_webhook_cutover_executed": False,
        "production_order_state_mutation_executed": False,
        "financial_reconciliation_mutation_executed": False,
        "token_used": False,
        "provider_secret_used": False,
        "raw_payment_secret_output": False,
        "route_owner_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
        "outbound_send_executed": False,
        "media_upload_executed": False,
        "oauth_callback_executed": False,
        "wecom_live_call_executed": False,
        "openclaw_mcp_executed": False,
        "timer_execution_executed": False,
        "automation_execution_executed": False,
    }


def _base_response(**safety: bool) -> Json:
    side_effect_safety = _side_effect_safety(**safety)
    return {
        "adapter_mode": "payment_commerce_live_adapter_behind_explicit_flag",
        **side_effect_safety,
        "sandbox_mode_required_by_default": True,
        "no_money_movement_confirmed": _enabled(FLAG_NO_MONEY),
        "provider_secret_redacted": True,
        "production_success_claimed": False,
        "side_effect_safety": side_effect_safety,
        "timestamp": _timestamp(),
    }


class PaymentCommerceLiveAdapter:
    def __init__(self, *, gateway: PaymentCommerceLiveGateway | None = None, confirm_no_money_movement: bool = False) -> None:
        self._gateway = gateway or build_payment_commerce_live_gateway()
        self._confirm_no_money_movement = confirm_no_money_movement
        self._idempotency: dict[str, IdempotencyRecord] = {}

    def reset_idempotency(self) -> None:
        self._idempotency.clear()

    def create_payment_intent_live(self, *, order_id: str, amount_cents: int, currency: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        if not str(order_id or "").strip():
            return self._invalid("order_id_missing")
        if int(amount_cents or 0) <= 0:
            return self._invalid("amount_invalid")
        payload = {
            "operation": "create_payment_intent_live",
            "order_id_redacted": _redact(order_id),
            "amount_cents": int(amount_cents),
            "currency": str(currency or "").upper(),
            "operator": operator,
        }
        request_hash = _hash(payload)
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {**_base_response(), "ok": False, "result_status": "conflict", "error_code": "duplicate_idempotency_key", "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay
        gate = self._gate_status()
        if not gate["ok"]:
            response = self._blocked(gate, idempotency_key=normalized_key, request_hash=request_hash, **payload)
            self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
            return response
        result = self._gateway.create_payment_intent_live(order_id=order_id, amount_cents=amount_cents, currency=currency)
        response = self._gateway_response(result=result, operation="create_payment_intent_live", idempotency_key=normalized_key, request_hash=request_hash, **payload)
        self._idempotency[normalized_key] = IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response

    def query_payment_status_live(self, *, payment_reference: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        if not str(payment_reference or "").strip():
            return self._invalid("payment_reference_missing")
        payload = {
            "operation": "query_payment_status_live",
            "payment_reference_redacted": _redact(payment_reference),
            "operator": operator,
        }
        request_hash = _hash(payload)
        gate = self._gate_status()
        if not gate["ok"]:
            return self._blocked(gate, idempotency_key=normalized_key, request_hash=request_hash, **payload)
        result = self._gateway.query_payment_status_live(payment_reference=payment_reference)
        return self._gateway_response(result=result, idempotency_key=normalized_key, request_hash=request_hash, **payload)

    def request_refund_live(self, *, payment_reference: str, amount_cents: int, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        payload = {
            "operation": "request_refund_live",
            "payment_reference_redacted": _redact(payment_reference),
            "amount_cents": int(amount_cents or 0),
            "operator": operator,
        }
        request_hash = _hash(payload)
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": "refund_not_enabled",
            "idempotency_key": normalized_key,
            "request_hash": request_hash,
            **payload,
        }

    def verify_payment_webhook_live(self, *, payload_hash: str, signature: str, operator: str, idempotency_key: str) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return self._invalid("idempotency_key_required")
        request_hash = _hash({"operation": "verify_payment_webhook_live", "payload_hash": payload_hash, "signature_present": bool(signature), "operator": operator})
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": "webhook_cutover_not_enabled",
            "idempotency_key": normalized_key,
            "request_hash": request_hash,
            "signature_validated": "shape_only",
        }

    def _gate_status(self) -> Json:
        if not _enabled(FLAG_ENABLED):
            return {"ok": False, "error_code": "live_adapter_not_enabled", "missing_gate": FLAG_ENABLED}
        if not _enabled(FLAG_APPROVED):
            return {"ok": False, "error_code": "live_payment_call_not_approved", "missing_gate": FLAG_APPROVED}
        if not _enabled(FLAG_CONFIG_REVIEWED):
            return {"ok": False, "error_code": "payment_config_missing", "missing_gate": FLAG_CONFIG_REVIEWED}
        if not _enabled(FLAG_SANDBOX_APPROVED):
            return {"ok": False, "error_code": "sandbox_mode_required", "missing_gate": FLAG_SANDBOX_APPROVED}
        if not _enabled(FLAG_NO_MONEY) or not self._confirm_no_money_movement:
            return {"ok": False, "error_code": "no_money_movement_confirmation_required", "missing_gate": "no_money_movement_confirmation"}
        if not _present(FLAG_PROVIDER_NAME) or not _present(FLAG_PROVIDER_SECRET):
            return {"ok": False, "error_code": "payment_config_missing", "missing_gate": "provider_secret_material"}
        return {"ok": True}

    def _blocked(self, gate: Json, **extra: Any) -> Json:
        return {
            **_base_response(),
            "ok": False,
            "result_status": "blocked",
            "error_code": gate.get("error_code") or "live_payment_not_enabled",
            "missing_gate": gate.get("missing_gate") or "",
            "live_adapter_enabled": _enabled(FLAG_ENABLED),
            "provider_config_reviewed": _enabled(FLAG_CONFIG_REVIEWED),
            "sandbox_mode_approved": _enabled(FLAG_SANDBOX_APPROVED),
            "approval_present": _enabled(FLAG_APPROVED),
            **extra,
        }

    def _invalid(self, error_code: str) -> Json:
        return {**_base_response(), "ok": False, "result_status": "invalid", "error_code": error_code}

    def _gateway_response(self, *, result: Json, **extra: Any) -> Json:
        ok = bool(result.get("ok"))
        return {
            **_base_response(provider_call_executed=bool(result.get("provider_call_executed"))),
            "ok": ok,
            "result_status": "payment_live_operation_completed" if ok else str(result.get("result_status") or "blocked"),
            "error_code": "" if ok else str(result.get("error_code") or "payment_live_call_failed"),
            **extra,
        }


def build_payment_commerce_live_adapter(*, confirm_no_money_movement: bool = False) -> PaymentCommerceLiveAdapter:
    return PaymentCommerceLiveAdapter(confirm_no_money_movement=confirm_no_money_movement)
