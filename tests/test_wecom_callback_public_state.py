from __future__ import annotations

from urllib.error import HTTPError

from scripts.ops import check_wecom_callback_public_state as public_state


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


def test_public_state_requires_explicit_base_url(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_CALLBACK_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(public_state, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not probe")))

    payload = public_state.run([])

    assert payload["ok"] is False
    assert payload["error"] == "base_url_required"
    assert "no longer defaults to production" in " ".join(payload["warnings"])
    assert payload["probes"] == {}


def test_public_state_accepts_base_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_CALLBACK_PUBLIC_BASE_URL", "https://example.test")
    seen_urls: list[str] = []

    def fake_urlopen(request, timeout=3.0):
        seen_urls.append(request.full_url)
        raise HTTPError(request.full_url, 400, "error", hdrs=None, fp=_Response(400, "invalid callback"))

    monkeypatch.setattr(public_state, "urlopen", fake_urlopen)

    payload = public_state.run(["--probe-timeout", "0.1"])

    assert payload["base_url"] == "https://example.test"
    assert seen_urls
    assert all(url.startswith("https://example.test/") for url in seen_urls)


def test_public_state_reports_current_emergency_quick_ack_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (404, '{"detail":"Not Found"}'),
                "/api/admin/webhook-inbox/metrics": (404, '{"detail":"Not Found"}'),
                "/api/admin/webhook-inbox/items": (404, '{"detail":"Not Found"}'),
                "/api/admin/webhook-inbox/0": (404, '{"detail":"Not Found"}'),
                "/api/admin/wecom/callback/reconciliation": (404, '{"detail":"Not Found"}'),
                "/wecom/external-contact/callback": (200, "success"),
                "/api/wecom/events": (200, "success"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["user_facing_available"] is True
    assert payload["admin_webhook_inbox_api_deployed"] is False
    assert payload["invalid_callback_plain_success"] is True
    assert "emergency quick ACK" in " ".join(payload["warnings"])


def test_public_state_accepts_public_signals_after_cutover(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
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
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is True
    assert payload["user_facing_available"] is True
    assert payload["admin_webhook_inbox_api_deployed"] is True
    assert payload["admin_webhook_inbox_detail_route_deployed"] is True
    assert payload["invalid_callback_plain_success"] is False
    assert payload["app_level_callback_signal"] is True
    assert len(payload["callback_route_signals"]) == 2
    assert all(item["app_level_callback_signal"] is True for item in payload["callback_route_signals"])


def test_public_state_rejects_secondary_callback_route_quick_ack(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
                "/api/admin/webhook-inbox/metrics": (
                    200,
                    '{"ok":true,"queue_metrics":{"provider_distribution":[],"route_distribution":[],"recent_errors":[]}}',
                ),
                "/api/admin/webhook-inbox/items": (200, '{"ok":true,"items":[]}'),
                "/api/admin/webhook-inbox/0": (404, '{"ok":false,"error":"webhook_inbox_item_not_found"}'),
                "/api/admin/wecom/callback/reconciliation": (200, '{"ok":true,"recent_items":[]}'),
                "/wecom/external-contact/callback": (400, "invalid callback"),
                "/api/wecom/events": (200, "success"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["invalid_callback_plain_success"] is True
    assert payload["app_level_callback_signal"] is False
    assert any(item["path"].startswith("/api/wecom/events") and item["plain_success"] is True for item in payload["callback_route_signals"])
    assert any("emergency quick ACK" in warning for warning in payload["warnings"])


def test_public_state_rejects_json_api_200_without_required_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
                "/api/admin/webhook-inbox/metrics": (200, '{"ok":true,"queue_metrics":{}}'),
                "/api/admin/webhook-inbox/items": (200, '{"ok":true,"items":[]}'),
                "/api/admin/webhook-inbox/0": (404, '{"ok":false,"error":"webhook_inbox_item_not_found"}'),
                "/api/admin/wecom/callback/reconciliation": (200, '{"ok":true,"recent_items":[]}'),
                "/wecom/external-contact/callback": (400, "invalid callback"),
                "/api/wecom/events": (400, "invalid callback"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["admin_webhook_inbox_api_deployed"] is False
    assert payload["admin_webhook_inbox_detail_route_deployed"] is True
    assert any("admin webhook inbox APIs" in warning for warning in payload["warnings"])


def test_public_state_rejects_generic_404_for_webhook_inbox_detail_route(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
                "/api/admin/webhook-inbox/metrics": (401, '{"detail":"auth required"}'),
                "/api/admin/webhook-inbox/items": (401, '{"detail":"auth required"}'),
                "/api/admin/webhook-inbox/0": (404, '{"detail":"Not Found"}'),
                "/api/admin/wecom/callback/reconciliation": (401, '{"detail":"auth required"}'),
                "/wecom/external-contact/callback": (400, "invalid callback"),
                "/api/wecom/events": (400, "invalid callback"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["admin_webhook_inbox_api_deployed"] is False
    assert payload["admin_webhook_inbox_detail_route_deployed"] is False
    assert any("detail processing-chain route" in warning for warning in payload["warnings"])


def test_public_state_rejects_admin_webhook_inbox_server_error(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
                "/api/admin/webhook-inbox/metrics": (500, '{"detail":"db down"}'),
                "/api/admin/webhook-inbox/items": (401, '{"detail":"auth required"}'),
                "/api/admin/webhook-inbox/0": (404, '{"ok":false,"error":"webhook_inbox_item_not_found"}'),
                "/api/admin/wecom/callback/reconciliation": (401, '{"detail":"auth required"}'),
                "/wecom/external-contact/callback": (400, "invalid callback"),
                "/api/wecom/events": (400, "invalid callback"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["admin_webhook_inbox_api_deployed"] is False
    assert payload["app_level_callback_signal"] is True


def test_public_state_rejects_invalid_callback_upstream_error(monkeypatch) -> None:
    monkeypatch.setattr(
        public_state,
        "urlopen",
        _fake_urlopen(
            {
                "/health": (200, '{"ok":true}'),
                "/sidebar/bind-mobile": (200, "<html></html>"),
                "/admin/automation-conversion": (302, ""),
                "/admin/webhook-inbox": (302, ""),
                "/api/admin/webhook-inbox/metrics": (401, '{"detail":"auth required"}'),
                "/api/admin/webhook-inbox/items": (401, '{"detail":"auth required"}'),
                "/api/admin/webhook-inbox/0": (404, '{"ok":false,"error":"webhook_inbox_item_not_found"}'),
                "/api/admin/wecom/callback/reconciliation": (401, '{"detail":"auth required"}'),
                "/wecom/external-contact/callback": (502, "bad gateway"),
                "/api/wecom/events": (400, "invalid callback"),
            }
        ),
    )

    payload = public_state.run(["--base-url", "https://example.test"])

    assert payload["ok"] is False
    assert payload["admin_webhook_inbox_api_deployed"] is True
    assert payload["invalid_callback_plain_success"] is False
    assert payload["app_level_callback_signal"] is False
