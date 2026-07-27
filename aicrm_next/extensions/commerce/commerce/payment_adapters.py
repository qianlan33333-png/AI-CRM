from __future__ import annotations

from typing import Any


class FakePaymentAdapter:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def build_checkout(self, *, order_no: str, amount_cents: int, return_url: str | None) -> dict[str, Any]:
        base = f"https://fake-pay.local/{self.provider}/checkout/{order_no}"
        return {
            "checkout_url": f"{base}?return_url={return_url or ''}",
            "qr_code_url": f"https://fake-pay.local/{self.provider}/qr/{order_no}.png",
            "provider_payload": {
                "provider": self.provider,
                "order_no": order_no,
                "amount_cents": amount_cents,
                "signature_verified": False,
                "source_status": "fake",
            },
            "fake_payment": True,
        }


def build_fake_payment_adapter(provider: str) -> FakePaymentAdapter:
    return FakePaymentAdapter(provider)
