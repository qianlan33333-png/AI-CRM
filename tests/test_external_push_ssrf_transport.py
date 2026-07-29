from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from aicrm_next.platform.external_push.https_transport import HttpsTransportResponse, PinnedHttpsTransport
from aicrm_next.platform.external_push.security import (
    WebhookUrlValidationError,
    resolve_and_validate_public_https_target,
)
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    ExternalEffectService,
    WEBHOOK_GENERIC_PUSH,
)
from aicrm_next.platform.platform_foundation.external_effects.adapters import WebhookAdapter
from aicrm_next.platform.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository
from tests.webhook_hmac_test_helpers import outbound_webhook_hmac_signer


PUBLIC_IP = "8.8.8.8"


def _job(*, url: str = "https://hooks.example.com/v1/events?tenant=aicrm"):
    repository = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repository)
    created = service.plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="external_push_test",
        target_id="ssrf-transport-test",
        payload={"webhook_url": url, "body": {"event": "external_push.test"}},
        context=CommandContext(
            actor_id="ssrf-test",
            actor_type="system",
            request_id="ssrf-request",
            trace_id="ssrf-trace",
            source_route="/tests/external-push-ssrf",
        ),
        idempotency_key=f"ssrf:{url}",
        status="queued",
        execution_mode="execute",
    )
    job = repository.get_job(int(created["id"]))
    assert job is not None
    return job


def _enable_webhook_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WEBHOOK_GENERIC_PUSH)


@pytest.mark.parametrize(
    ("url", "resolved"),
    [
        ("http://hooks.example.com/event", [PUBLIC_IP]),
        ("https://user:password@hooks.example.com/event", [PUBLIC_IP]),
        ("https://hooks.example.com/event#internal", [PUBLIC_IP]),
        ("https://hooks.example.com:444/event", [PUBLIC_IP]),
        ("https://localhost/event", ["127.0.0.1"]),
        ("https://hooks.example.com/event", ["127.0.0.1"]),
        ("https://hooks.example.com/event", ["10.0.0.7"]),
        ("https://hooks.example.com/event", ["100.64.0.7"]),
        ("https://hooks.example.com/event", ["169.254.169.254"]),
        ("https://hooks.example.com/event", ["::1"]),
        ("https://hooks.example.com/event", ["fc00::7"]),
        ("https://hooks.example.com/event", ["fe80::7"]),
        ("https://hooks.example.com/event", [PUBLIC_IP, "10.0.0.8"]),
    ],
)
def test_target_validation_rejects_unsafe_urls_and_any_non_public_dns_answer(url: str, resolved: list[str]) -> None:
    with pytest.raises(WebhookUrlValidationError):
        resolve_and_validate_public_https_target(url, resolver=lambda _host, _port: resolved)


def test_validated_target_is_immutable_normalized_and_resolved_once() -> None:
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int) -> list[str]:
        calls.append((hostname, port))
        return ["1.1.1.1", PUBLIC_IP, PUBLIC_IP]

    target = resolve_and_validate_public_https_target(
        "https://Hooks.Example.COM:443/v1/events?tenant=aicrm",
        resolver=resolver,
    )

    assert target.url == "https://hooks.example.com/v1/events?tenant=aicrm"
    assert target.hostname == "hooks.example.com"
    assert target.port == 443
    assert target.ip_addresses == ("1.1.1.1", PUBLIC_IP)
    assert target.selected_ip == "1.1.1.1"
    assert target.request_target == "/v1/events?tenant=aicrm"
    assert calls == [("hooks.example.com", 443)]
    with pytest.raises(FrozenInstanceError):
        target.hostname = "other.example.com"  # type: ignore[misc]


def test_pinned_transport_connects_to_validated_ip_with_original_host_sni_and_no_redirect() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200
        data = b'{"ok": true}'

    class FakePool:
        def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["request_kwargs"] = kwargs
            return FakeResponse()

        def close(self):
            captured["closed"] = True

    def pool_factory(host, **kwargs):
        captured["pool_host"] = host
        captured["pool_kwargs"] = kwargs
        return FakePool()

    target = resolve_and_validate_public_https_target(
        "https://hooks.example.com/v1/events?tenant=aicrm",
        resolver=lambda _host, _port: [PUBLIC_IP],
    )
    response = PinnedHttpsTransport(pool_factory=pool_factory).post(
        target,
        body=b'{"ok":true}',
        headers={"Content-Type": "application/json", "Host": "attacker.invalid"},
        timeout=4.5,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured["pool_host"] == PUBLIC_IP
    assert captured["pool_kwargs"]["port"] == 443
    assert captured["pool_kwargs"]["server_hostname"] == "hooks.example.com"
    assert captured["pool_kwargs"]["assert_hostname"] == "hooks.example.com"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/events?tenant=aicrm"
    assert captured["request_kwargs"]["headers"]["Host"] == "hooks.example.com"
    assert captured["request_kwargs"]["body"] == b'{"ok":true}'
    assert captured["request_kwargs"]["redirect"] is False
    assert captured["request_kwargs"]["retries"] is False
    assert captured["closed"] is True


def test_webhook_adapter_blocks_dns_rebinding_before_transport_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_webhook_execution(monkeypatch)
    transport_calls: list[Any] = []

    class Transport:
        def post(self, target, **_kwargs):
            transport_calls.append(target)
            return HttpsTransportResponse(status_code=200, text="ok")

    adapter = WebhookAdapter(
        transport=Transport(),
        resolver=lambda _host, _port: [PUBLIC_IP, "10.0.0.9"],
        signer=outbound_webhook_hmac_signer(),
    )
    result = adapter.dispatch(_job())

    assert result.status == "failed_terminal"
    assert result.error_code == "ssrf_blocked"
    assert result.real_external_call_executed is False
    assert transport_calls == []


def test_webhook_adapter_uses_one_resolution_snapshot_and_never_resolves_inside_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_webhook_execution(monkeypatch)
    resolver_calls = 0
    targets = []

    def changing_resolver(_host: str, _port: int) -> list[str]:
        nonlocal resolver_calls
        resolver_calls += 1
        return [PUBLIC_IP] if resolver_calls == 1 else ["127.0.0.1"]

    class Transport:
        def post(self, target, **_kwargs):
            targets.append(target)
            return HttpsTransportResponse(status_code=200, text='{"ok": true}')

    result = WebhookAdapter(
        transport=Transport(),
        resolver=changing_resolver,
        signer=outbound_webhook_hmac_signer(),
    ).dispatch(_job())

    assert result.status == "succeeded"
    assert result.real_external_call_executed is True
    assert resolver_calls == 1
    assert targets[0].ip_addresses == (PUBLIC_IP,)


def test_webhook_adapter_rejects_redirect_without_following_location(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_webhook_execution(monkeypatch)
    calls = 0

    class RedirectTransport:
        def post(self, _target, **_kwargs):
            nonlocal calls
            calls += 1
            return HttpsTransportResponse(
                status_code=302,
                text="redirect",
                headers={"Location": "http://127.0.0.1/internal"},
            )

    result = WebhookAdapter(
        transport=RedirectTransport(),
        resolver=lambda _host, _port: [PUBLIC_IP],
        signer=outbound_webhook_hmac_signer(),
    ).dispatch(_job())

    assert calls == 1
    assert result.status == "failed_terminal"
    assert result.error_code == "redirect_blocked"
    assert result.response_summary["status_code"] == 302
    assert result.real_external_call_executed is True
    assert "127.0.0.1" not in result.error_message
