from __future__ import annotations

from copy import deepcopy

from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, WEBHOOK_GENERIC_PUSH, WEBHOOK_ORDER_PAID_PUSH
from aicrm_next.platform.platform_foundation.external_effects.repo import reset_external_effect_fixture_state
from aicrm_next.platform.external_push import repo, security, service


class FakeExternalPushRepository:
    def __init__(self) -> None:
        self.order = {
            "id": 101,
            "out_trade_no": "WXP_NEXT_NATIVE_PUSH",
            "product_code": "subscription_trial_month",
            "product_name": "外推测试商品",
            "amount_total": 9900,
            "payer_total": 9900,
            "paid_at": "2026-06-01T07:30:10+00:00",
            "external_userid": "wm_product_push",
            "payer_openid": "openid_product_push",
            "unionid": "unionid_product_push",
            "metadata_json": {"payer_identity": {"mobile": "13800000000"}},
        }
        self.product = {
            "id": 202,
            "product_code": "subscription_trial_month",
            "name": "外推测试商品",
            "amount_total": 9900,
        }
        self.config = {
            "id": 303,
            "tenant_id": "aicrm",
            "enabled": True,
            "webhook_url": "https://example.com/hook",
            "push_type": "premium",
            "day": 31,
            "frequency": 3,
            "remark": "69元1个月续费",
            "custom_params": {"source": "ai-crm"},
            "secret": "secret",
        }
        self.outbox = {
            "id": 404,
            "aggregate_id": str(self.order["id"]),
            "payload": {"order_id": str(self.order["id"])},
        }
        self.delivery = {
            "id": 505,
            "config_id": self.config["id"],
            "event_type": "transaction.paid",
            "delivery_id": "deliv_next_native",
            "target_type": "product",
            "target_id": str(self.product["id"]),
            "order_id": self.order["id"],
            "product_id": self.product["id"],
            "status": "pending",
            "attempt_count": 0,
            "request_url": self.config["webhook_url"],
            "request_headers": {},
            "request_body": {},
        }
        self.due_outbox_events = [deepcopy(self.outbox)]
        self.due_deliveries: list[dict] = []
        self.outbox_statuses: list[tuple[int, str]] = []
        self.updated_deliveries: list[dict] = []
        self.identity_mobiles = {"unionid_product_push": "13900000000"}

    def list_due_outbox_events(self, *, limit: int = 20):
        assert limit > 0
        return deepcopy(self.due_outbox_events[:limit])

    def list_due_deliveries(self, *, limit: int = 20):
        assert limit > 0
        return deepcopy(self.due_deliveries[:limit])

    def mark_outbox_status(self, outbox_id: int, *, status: str, retry_count=None, next_retry_at=None):
        self.outbox_statuses.append((outbox_id, status))
        return {"id": outbox_id, "status": status, "retry_count": retry_count, "next_retry_at": next_retry_at}

    def get_order_by_id(self, order_id: int):
        return deepcopy(self.order) if int(order_id) == int(self.order["id"]) else None

    def get_product_for_order(self, order: dict):
        return deepcopy(self.product) if order else {}

    def resolve_identity_mobile_by_unionid(self, unionid: str):
        return self.identity_mobiles.get(unionid, "")

    def get_product_by_id(self, product_id: int):
        return deepcopy(self.product) if int(product_id) == int(self.product["id"]) else None

    def get_product_config(self, product_id: int):
        return deepcopy(self.config) if int(product_id) == int(self.product["id"]) else None

    def get_config_with_secret(self, config_id: int):
        return deepcopy(self.config) if int(config_id) == int(self.config["id"]) else None

    def create_delivery_once(self, payload: dict):
        delivery = deepcopy(self.delivery)
        delivery.update(
            {
                "config_id": int(payload["config_id"]),
                "event_type": payload["event_type"],
                "delivery_id": payload["delivery_id"],
                "target_type": payload["target_type"],
                "target_id": payload["target_id"],
                "order_id": int(payload["order_id"]),
                "product_id": int(payload["product_id"]),
                "request_url": payload["request_url"],
            }
        )
        self.delivery = delivery
        return deepcopy(delivery)

    def create_test_delivery(self, payload: dict):
        delivery = deepcopy(self.delivery)
        delivery.update(
            {
                "event_type": "external_push.test",
                "delivery_id": payload["delivery_id"],
                "order_id": 0,
                "product_id": int(payload["product_id"]),
                "request_url": payload["request_url"],
            }
        )
        self.delivery = delivery
        return deepcopy(delivery)

    def update_delivery_result(self, delivery_id: str, **payload):
        updated = deepcopy(self.delivery)
        updated.update(payload)
        updated["delivery_id"] = delivery_id
        self.delivery = updated
        self.updated_deliveries.append(deepcopy(updated))
        return deepcopy(updated)

    def get_delivery_by_delivery_id(self, delivery_id: str):
        return deepcopy(self.delivery) if delivery_id == self.delivery["delivery_id"] else None

    def list_deliveries_for_order(self, order_id: int):
        return [deepcopy(self.delivery)] if int(order_id) == int(self.order["id"]) else []


def _allow_no_dns(monkeypatch) -> None:
    monkeypatch.setattr(service, "resolve_and_validate_public_https_url", lambda url: url)


def test_repository_builder_uses_next_session_factory() -> None:
    repository = repo.build_external_push_repository(session_factory=lambda: object())  # type: ignore[return-value]

    assert isinstance(repository, repo.SQLAlchemyExternalPushRepository)


def test_security_rejects_non_https_urls() -> None:
    try:
        security.validate_webhook_url("http://127.0.0.1/hook")
    except security.WebhookUrlValidationError as exc:
        assert "https" in str(exc) or "public IP" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("local http webhook URL should be rejected")


def test_signature_is_stable_and_sensitive_payload_is_redacted() -> None:
    sig = service.sign_webhook_payload("secret", "1778888888", '{"a":1}')

    assert sig == service.sign_webhook_payload("secret", "1778888888", '{"a":1}')
    assert sig != service.sign_webhook_payload("secret", "1778888888", '{"a":2}')
    assert service.redact_sensitive_fields({"phone": "13800000000", "buyer": {"unionid": "unionid_product_push"}}) == {
        "phone": "[pii]",
        "buyer": {"unionid": "[pii]"},
    }


def test_run_due_events_success_updates_delivery_and_outbox(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    repository = FakeExternalPushRepository()
    _allow_no_dns(monkeypatch)

    result = service.run_due_external_push_events(limit=5, repository=repository)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH})

    assert result["ok"] is True
    assert result["scanned_count"] == 1
    assert result["success_count"] == 1
    assert repository.outbox_statuses == [(repository.outbox["id"], "success")]
    assert repository.delivery["status"] == "retrying"
    assert repository.delivery["attempt_count"] == 0
    assert repository.delivery["request_headers"]["X-AICRM-Signature"].startswith("sha256=")
    assert repository.delivery["request_body"]["phone_number"] == "[pii]"
    assert repository.delivery["request_body"]["buyer"]["phone"] == "[pii]"
    assert total == 1
    assert jobs[0].status == "queued"
    assert jobs[0].execution_mode == "execute"
    assert jobs[0].payload_json["body"]["phone_number"] == "13800000000"
    assert jobs[0].payload_json["body"]["buyer"]["phone"] == "13800000000"
    assert jobs[0].payload_json["body"]["event"] == "transaction.paid"
    assert result["items"][0]["real_external_call_executed"] is False


def test_external_push_payload_falls_back_to_identity_mobile(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    repository = FakeExternalPushRepository()
    repository.order["metadata_json"] = {}
    _allow_no_dns(monkeypatch)

    result = service.run_due_external_push_events(limit=5, repository=repository)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH})

    assert result["ok"] is True
    assert total == 1
    assert jobs[0].payload_json["body"]["phone_number"] == "13900000000"
    assert jobs[0].payload_json["body"]["buyer"]["phone"] == "13900000000"


def test_run_due_events_invalid_url_fails_without_external_call(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    repository = FakeExternalPushRepository()
    repository.config["webhook_url"] = "http://127.0.0.1/hook"
    repository.delivery["request_url"] = "http://127.0.0.1/hook"

    result = service.run_due_external_push_events(limit=5, repository=repository)

    assert result["failed_count"] == 1
    assert repository.outbox_statuses == [(repository.outbox["id"], "success")]
    assert repository.delivery["status"] == "failed"
    assert repository.delivery["response_status"] is None
    assert repository.delivery["next_retry_at"] == ""
    assert ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH})[1] == 0


def test_run_due_events_skips_missing_config_without_http_call(monkeypatch) -> None:
    repository = FakeExternalPushRepository()
    repository.config = {}
    result = service.run_due_external_push_events(limit=5, repository=repository)

    assert result["skipped_count"] == 1
    assert result["items"][0]["reason"] == "config_not_found"
    assert repository.outbox_statuses == [(repository.outbox["id"], "skipped")]


def test_run_due_retries_uses_existing_delivery_and_mocked_http(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    repository = FakeExternalPushRepository()
    failed_delivery = deepcopy(repository.delivery)
    failed_delivery["status"] = "retrying"
    failed_delivery["attempt_count"] = 1
    failed_delivery["request_body"] = {}
    repository.due_deliveries = [failed_delivery]
    _allow_no_dns(monkeypatch)

    result = service.run_due_external_push_retries(limit=3, repository=repository)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH})

    assert result["ok"] is True
    assert result["retried_count"] == 1
    assert repository.delivery["status"] == "retrying"
    assert repository.delivery["attempt_count"] == 1
    assert total == 1
    assert jobs[0].payload_json["legacy_delivery_id"] == "deliv_next_native"


def test_send_product_external_push_test_uses_test_event_payload(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    repository = FakeExternalPushRepository()
    _allow_no_dns(monkeypatch)

    result = service.send_product_external_push_test(repository.product["id"], repository=repository)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_GENERIC_PUSH})

    assert result["ok"] is True
    assert repository.delivery["event_type"] == "external_push.test"
    assert result["real_external_call_executed"] is False
    assert total == 1
    assert jobs[0].payload_json["body"]["event"] == "external_push.test"
