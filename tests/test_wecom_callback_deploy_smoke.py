from __future__ import annotations

from urllib.error import HTTPError

from scripts.ops import check_wecom_callback_deploy_smoke as smoke


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body[:limit] if limit >= 0 else self._body

    def close(self) -> None:
        return None


def _fake_urlopen(mapping: dict[str, tuple[int, str]]):
    def fake(request, timeout=3.0):
        url = request.full_url
        for marker, (status, body) in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
            if marker in url:
                if status >= 400:
                    raise HTTPError(url, status, "error", hdrs=None, fp=_Response(status, body))
                return _Response(status, body)
        raise AssertionError(f"unexpected url: {url}")

    return fake


def _healthy_mapping() -> dict[str, tuple[int, str]]:
    return {
        "127.0.0.1:5001/health": (200, '{"ok":true}'),
        "127.0.0.1:5002/health": (
            200,
            '{"ok":true,"runtime":"ai_crm_wecom_ingress","durable_inbox_only":true,"ack_boundary":"signature_decrypt_and_durable_inbox_only"}',
        ),
        "/api/admin/webhook-inbox/metrics": (
            200,
            '{"ok":true,"queue_metrics":{"provider_distribution":[],"route_distribution":[],"recent_errors":[]}}',
        ),
        "/api/admin/webhook-inbox/items": (200, '{"ok":true,"items":[]}'),
        "/api/admin/webhook-inbox/0": (404, '{"ok":false,"error":"webhook_inbox_item_not_found"}'),
        "/api/admin/wecom/callback/reconciliation": (200, '{"ok":true,"recent_items":[]}'),
        "/wecom/external-contact/callback": (400, "invalid callback"),
        "/api/wecom/events": (400, "invalid callback"),
    }


def test_deploy_smoke_accepts_local_callback_runtime_and_admin_routes(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(_healthy_mapping()))

    payload = smoke.run([])

    assert payload["ok"] is True
    assert payload["base_urls_distinct"] is True
    assert payload["web_health_ok"] is True
    assert payload["ingress_health_ok"] is True
    assert payload["ingress_durable_ack_ready"] is True
    assert payload["admin_api_deployed"] is True
    assert payload["admin_detail_route_deployed"] is True
    assert payload["ingress_callback_routes_ready"] is True
    assert len(payload["ingress_callback_route_signals"]) == 2


def test_deploy_smoke_rejects_missing_webhook_inbox_admin_routes(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/api/admin/webhook-inbox/metrics"] = (404, '{"detail":"Not Found"}')
    mapping["/api/admin/webhook-inbox/items"] = (404, '{"detail":"Not Found"}')
    mapping["/api/admin/webhook-inbox/0"] = (404, '{"detail":"Not Found"}')
    mapping["/api/admin/wecom/callback/reconciliation"] = (404, '{"detail":"Not Found"}')
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["admin_api_deployed"] is False
    assert any("webhook inbox admin" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_same_web_and_ingress_base_url(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["same.example/health"] = (
        200,
        '{"ok":true,"runtime":"ai_crm_wecom_ingress","durable_inbox_only":true,"ack_boundary":"signature_decrypt_and_durable_inbox_only"}',
    )
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run(["--web-base-url", "https://same.example", "--ingress-base-url", "https://same.example"])

    assert payload["ok"] is False
    assert payload["base_urls_distinct"] is False
    assert payload["web_health_ok"] is True
    assert payload["ingress_health_ok"] is True
    assert payload["ingress_durable_ack_ready"] is True
    assert any("must be distinct" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_missing_webhook_inbox_detail_route(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/api/admin/webhook-inbox/0"] = (500, "server error")
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["admin_detail_route_deployed"] is False
    assert any("detail processing-chain route" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_json_api_200_without_required_shape(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/api/admin/webhook-inbox/metrics"] = (200, '{"ok":true,"queue_metrics":{}}')
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["admin_api_deployed"] is False
    assert any("required list fields" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_generic_404_for_webhook_inbox_detail_route(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/api/admin/webhook-inbox/0"] = (404, '{"detail":"Not Found"}')
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["admin_detail_route_deployed"] is False
    assert any("detail processing-chain route" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_missing_ingress_callback_route(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/api/wecom/events"] = (404, '{"detail":"Not Found"}')
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["ingress_callback_routes_ready"] is False
    assert any(item["path"].startswith("/api/wecom/events") and item["status_code"] == 404 for item in payload["ingress_callback_route_signals"])
    assert any("5002 callback ingress routes" in warning for warning in payload["warnings"])


def test_deploy_smoke_rejects_ingress_callback_plain_success(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["/wecom/external-contact/callback"] = (200, "success")
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["ingress_callback_routes_ready"] is False
    assert any(item["path"].startswith("/wecom/external-contact/callback") and item["plain_success"] is True for item in payload["ingress_callback_route_signals"])


def test_deploy_smoke_rejects_missing_isolated_ingress_runtime(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["127.0.0.1:5002/health"] = (502, "bad gateway")
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["web_health_ok"] is True
    assert payload["ingress_health_ok"] is False
    assert payload["admin_api_deployed"] is True


def test_deploy_smoke_rejects_ingress_runtime_without_durable_ack_signal(monkeypatch) -> None:
    mapping = _healthy_mapping()
    mapping["127.0.0.1:5002/health"] = (200, '{"ok":true,"runtime":"ai_crm_wecom_ingress"}')
    monkeypatch.setattr(smoke, "urlopen", _fake_urlopen(mapping))

    payload = smoke.run([])

    assert payload["ok"] is False
    assert payload["ingress_health_ok"] is True
    assert payload["ingress_durable_ack_ready"] is False
    assert any("durable-only ACK" in warning for warning in payload["warnings"])
