from __future__ import annotations

import time
from scripts.ops import probe_wecom_callback_pressure as probe
from aicrm_next.channels.channel_entry.wecom_crypto import compute_signature, encrypt_message


def test_pressure_probe_percentile_interpolates() -> None:
    assert probe.percentile([10, 20, 30, 40], 50) == 25
    assert probe.percentile([10, 20, 30, 40], 95) == 38.5
    assert probe.percentile([], 95) is None


def test_pressure_probe_summarizes_latency_and_status_targets() -> None:
    results = [
        probe.ProbeResult(label="callback", method="POST", url="http://example.test/callback", status_code=200, latency_ms=20),
        probe.ProbeResult(label="callback", method="POST", url="http://example.test/callback", status_code=200, latency_ms=30),
        probe.ProbeResult(label="callback", method="POST", url="http://example.test/callback", status_code=200, latency_ms=40),
    ]

    summary = probe.summarize_results(results, expected_status_min=200, expected_status_max=200, target_p95_ms=50, target_p99_ms=100)

    assert summary["request_count"] == 3
    assert summary["ok_count"] == 3
    assert summary["status_counts"] == {"200": 3}
    assert summary["latency_ms"]["p95"] == 39
    assert summary["meets_status_target"] is True
    assert summary["meets_p95_target"] is True
    assert summary["meets_p99_target"] is True


def test_pressure_probe_rejects_page_sample_failures(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, *, body: bytes, timeout_seconds: float, label: str) -> probe.ProbeResult:
        calls.append(label)
        if label == "callback":
            return probe.ProbeResult(label=label, method=method, url=url, status_code=200, latency_ms=50)
        return probe.ProbeResult(label=label, method=method, url=url, status_code=503, latency_ms=10)

    monkeypatch.setattr(probe, "_request", fake_request)

    payload = probe.run(
        [
            "--callback-url",
            "http://example.test/callback",
            "--callback-body-text",
            "<xml>valid</xml>",
            "--total-requests",
            "2",
            "--rate-per-minute",
            "60000",
            "--sample-interval-seconds",
            "0",
        ]
    )

    assert payload["callback"]["meets_p95_target"] is True
    assert payload["page_samples"]["health"]["meets_status_target"] is False
    assert payload["ok"] is False
    assert "callback" in calls
    assert "health" in calls


def test_pressure_probe_allows_custom_sample_target(monkeypatch) -> None:
    def fake_request(method: str, url: str, *, body: bytes, timeout_seconds: float, label: str) -> probe.ProbeResult:
        return probe.ProbeResult(label=label, method=method, url=url, status_code=204 if label == "custom" else 200, latency_ms=12)

    monkeypatch.setattr(probe, "_request", fake_request)

    payload = probe.run(
        [
            "--callback-url",
            "http://example.test/callback",
            "--callback-body-text",
            "<xml>valid</xml>",
            "--total-requests",
            "1",
            "--rate-per-minute",
            "60000",
            "--sample-interval-seconds",
            "0",
            "--no-default-samples",
            "--sample-url",
            "custom=http://example.test/custom,target_p95_ms=20,expected_status_min=200,expected_status_max=299",
        ]
    )

    assert payload["page_samples"]["custom"]["status_counts"] == {"204": 1}
    assert payload["page_samples"]["custom"]["meets_p95_target"] is True
    assert payload["ok"] is True


def _valid_callback_sample() -> tuple[str, str, str, str, str, str]:
    corp_id = "ww-test"
    token = "token"
    aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    timestamp = str(int(time.time()))
    nonce = "nonce-a"
    plain_xml = (
        "<xml>"
        "<ToUserName>ww-test</ToUserName>"
        "<Event>change_external_contact</Event>"
        "<ChangeType>add_external_contact</ChangeType>"
        "<ExternalUserID>wm-a</ExternalUserID>"
        "<UserID>sales-a</UserID>"
        f"<CreateTime>{timestamp}</CreateTime>"
        "<WelcomeCode>welcome-a</WelcomeCode>"
        "<State>scene-a</State>"
        "</xml>"
    )
    encrypted = encrypt_message(plain_xml, aes_key, corp_id)
    signature = compute_signature(token, timestamp, nonce, encrypted)
    body = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"
    url = f"http://example.test/callback?timestamp={timestamp}&nonce={nonce}&msg_signature={signature}"
    return corp_id, token, aes_key, timestamp, url, body


def test_pressure_probe_validates_encrypted_callback_sample(monkeypatch) -> None:
    corp_id, token, aes_key, timestamp, url, body = _valid_callback_sample()
    monkeypatch.setenv("WECOM_CORP_ID", corp_id)
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", token)
    monkeypatch.setenv("WECOM_CALLBACK_AES_KEY", aes_key)

    payload = probe.validate_callback_sample(url, body.encode("utf-8"))

    assert payload["ok"] is True
    assert payload["event_summary"]["Event"] == "change_external_contact"
    assert payload["event_summary"]["ExternalUserID_present"] is True
    assert payload["idempotency_key"] == f"ww-test|change_external_contact|add_external_contact|wm-a|sales-a|{timestamp}|welcome-a|scene-a"


def test_pressure_probe_requires_valid_sample_before_sending_requests(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("WECOM_CORP_ID", "ww-test")
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", "token")
    monkeypatch.setenv("WECOM_CALLBACK_AES_KEY", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")
    monkeypatch.setattr(probe, "_request", lambda *args, **kwargs: calls.append("sent"))

    payload = probe.run(
        [
            "--callback-url",
            "http://example.test/callback?timestamp=1&nonce=n&msg_signature=bad",
            "--callback-body-text",
            "<xml><Encrypt>invalid</Encrypt></xml>",
            "--require-valid-callback-sample",
            "--total-requests",
            "1",
        ]
    )

    assert payload["ok"] is False
    assert payload["sample_validation"]["ok"] is False
    assert payload["pressure"]["total_requests"] == 0
    assert calls == []
