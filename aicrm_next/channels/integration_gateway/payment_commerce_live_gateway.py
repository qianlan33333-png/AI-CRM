from __future__ import annotations

from typing import Any


Json = dict[str, Any]


class PaymentCommerceLiveGateway:
    def create_payment_intent_live(self, *, order_id: str, amount_cents: int, currency: str) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "payment_live_gateway_disabled",
            "provider_call_executed": False,
            "real_payment_capture_executed": False,
            "real_refund_executed": False,
            "real_settlement_executed": False,
            "production_order_state_mutation_executed": False,
            "token_used": False,
            "provider_secret_used": False,
        }

    def query_payment_status_live(self, *, payment_reference: str) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "payment_live_gateway_disabled",
            "provider_call_executed": False,
            "real_payment_capture_executed": False,
            "real_refund_executed": False,
            "real_settlement_executed": False,
            "production_order_state_mutation_executed": False,
            "token_used": False,
            "provider_secret_used": False,
        }

    def request_refund_live(self, *, payment_reference: str, amount_cents: int) -> Json:
        return {
            "ok": False,
            "result_status": "blocked",
            "error_code": "refund_not_enabled",
            "provider_call_executed": False,
            "real_payment_capture_executed": False,
            "real_refund_executed": False,
            "real_settlement_executed": False,
            "production_order_state_mutation_executed": False,
            "token_used": False,
            "provider_secret_used": False,
        }


def build_payment_commerce_live_gateway() -> PaymentCommerceLiveGateway:
    return PaymentCommerceLiveGateway()
