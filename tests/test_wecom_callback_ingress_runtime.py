from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from aicrm_next.channels.channel_entry.ingress_app import create_wecom_callback_ingress_app
from scripts.ops import check_callback_quick_ack_state as quick_ack_state
from scripts.ops.check_wecom_callback_ingress_cutover import analyze_nginx_config, run as run_cutover_check


def _event() -> dict:
    return {
        "ToUserName": "corp-1",
        "Event": "change_external_contact",
        "ChangeType": "add_external_contact",
        "ExternalUserID": "wm-a",
        "UserID": "sales-a",
        "CreateTime": "1782530000",
        "WelcomeCode": "welcome-a",
        "State": "scene-a",
    }


def test_wecom_callback_ingress_runtime_only_exposes_callback_and_health_routes() -> None:
    app = create_wecom_callback_ingress_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    client = TestClient(app, raise_server_exceptions=False)

    health = client.get("/health")
    admin = client.get("/admin/webhook-inbox")

    assert "/health" in paths
    assert "/wecom/external-contact/callback" in paths
    assert "/api/wecom/events" in paths
    assert "/admin/webhook-inbox" not in paths
    assert health.status_code == 200
    assert health.json()["runtime"] == "ai_crm_wecom_ingress"
    assert health.json()["durable_inbox_only"] is True
    assert health.json()["ack_boundary"] == "signature_decrypt_and_durable_inbox_only"
    assert health.headers["X-AICRM-App"] == "ai_crm_wecom_ingress"
    assert admin.status_code == 404


def test_wecom_callback_ingress_runtime_fast_acks_after_inbox_ingest(monkeypatch) -> None:
    calls: list[dict] = []
    app = create_wecom_callback_ingress_app()
    client = TestClient(app, raise_server_exceptions=False)

    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.encrypted_success_reply", lambda query: "success")
    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.ingest_wecom_external_contact_callback", lambda **kwargs: calls.append(kwargs) or {"ok": True, "id": 1})

    response = client.post(
        "/wecom/external-contact/callback?timestamp=1&nonce=n&msg_signature=s",
        content=b"<xml>encrypted</xml>",
    )

    assert response.status_code == 200
    assert response.text == "success"
    assert response.headers["X-AICRM-App"] == "ai_crm_wecom_ingress"
    assert len(calls) == 1
    assert calls[0]["route"] == "/wecom/external-contact/callback"


def test_wecom_callback_ingress_runtime_never_fake_acks_when_inbox_fails(monkeypatch) -> None:
    app = create_wecom_callback_ingress_app()
    client = TestClient(app, raise_server_exceptions=False)

    monkeypatch.setattr("aicrm_next.channels.channel_entry.api.ingest_wecom_external_contact_callback", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    response = client.post("/api/wecom/events?timestamp=1&nonce=n&msg_signature=s", content=b"<xml>encrypted</xml>")

    assert response.status_code == 503
    assert "webhook ingress unavailable" in response.text


def test_wecom_callback_ingress_runtime_scopes_settings_once_per_request(monkeypatch) -> None:
    events: list[str] = []

    @contextmanager
    def settings_scope():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.ingress_app.runtime_settings_request_scope",
        settings_scope,
    )
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.api.ingest_wecom_external_contact_callback",
        lambda **kwargs: {"ok": True, "id": 1},
    )
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.api.encrypted_success_reply",
        lambda query: "success",
    )
    client = TestClient(create_wecom_callback_ingress_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/wecom/events?timestamp=1&nonce=n&msg_signature=s",
        content=b"<xml>encrypted</xml>",
    )

    assert response.status_code == 200
    assert events == ["enter", "exit"]


def test_expired_callback_is_rejected_before_runtime_settings_are_loaded(monkeypatch) -> None:
    config_reads: list[bool] = []
    monkeypatch.setattr(
        "aicrm_next.channels.channel_entry.application.callback_config",
        lambda: config_reads.append(True) or {},
    )
    client = TestClient(create_wecom_callback_ingress_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/wecom/events?msg_signature=invalid&timestamp=1&nonce=deploy-smoke-probe-api-events",
        content=b"<xml>invalid</xml>",
    )

    assert response.status_code == 400
    assert "expired callback timestamp" in response.text
    assert config_reads == []


def test_wecom_callback_ingress_nginx_template_routes_to_5002_with_short_timeouts_and_backpressure() -> None:
    template = open("deploy/nginx-wecom-callback-ingress.conf.example", encoding="utf-8").read()
    payload = analyze_nginx_config("deploy/nginx-wecom-callback-ingress.conf.example")

    assert "upstream aicrm_web" in template
    assert "server 127.0.0.1:5001" in template
    assert "location /" in template
    assert "proxy_pass http://aicrm_web" in template
    assert "upstream aicrm_wecom_ingress" in template
    assert "server 127.0.0.1:5002" in template
    assert payload["nginx_config_found"] is True
    assert payload["callback_routes_present"] is True
    assert payload["emergency_quick_ack_enabled"] is False
    assert payload["callback_routes_proxy_to_5002"] is True
    assert payload["short_callback_timeouts_configured"] is True
    assert payload["callback_backpressure_configured"] is True
    assert template.count("client_max_body_size 1m;") == 2


def test_wecom_callback_ingress_cutover_check_rejects_emergency_quick_ack(tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        location = /wecom/external-contact/callback {
            return 200 "success";
        }
        location = /api/wecom/events {
            return 200 "success";
        }
        """,
        encoding="utf-8",
    )

    payload = run_cutover_check(["--nginx-config", str(config), "--skip-health-probe", "--skip-invalid-callback-probe"])

    assert payload["ready_for_cutover"] is False
    assert payload["nginx"]["emergency_quick_ack_enabled"] is True
    assert any("quick ACK" in warning for warning in payload["warnings"])


def test_callback_quick_ack_state_ignores_comments_and_unrelated_success_returns(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        # location = /wecom/external-contact/callback { return 200 "success"; }
        location = /healthz {
            return 200 "success";
        }
        location = /wecom/external-contact/callback {
            proxy_pass http://127.0.0.1:5002;
        }
        location = /api/wecom/events {
            proxy_pass http://127.0.0.1:5002;
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        quick_ack_state,
        "_recent_callback_events",
        lambda minutes: {"database_checked": True, "recent_app_callback_events": 3, "error": ""},
    )

    payload = quick_ack_state.run(["--nginx-config", str(config), "--skip-probe"])

    assert payload["ok"] is True
    assert payload["emergency_quick_ack_enabled"] is False
    assert payload["quick_ack_routes"] == []
    assert payload["callback_route_details"]["/wecom/external-contact/callback"]["present"] is True
    assert payload["callback_route_details"]["/wecom/external-contact/callback"]["emergency_quick_ack_enabled"] is False


def test_callback_quick_ack_state_reports_route_level_quick_ack(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        location = /wecom/external-contact/callback {
            if ($request_method = POST) {
                return 200 success;
            }
        }
        location = /api/wecom/events {
            return 200 "success";
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        quick_ack_state,
        "_recent_callback_events",
        lambda minutes: {"database_checked": True, "recent_app_callback_events": 0, "error": ""},
    )

    payload = quick_ack_state.run(["--nginx-config", str(config), "--skip-probe"])

    assert payload["ok"] is True
    assert payload["emergency_quick_ack_enabled"] is True
    assert payload["business_processing_suppressed"] is True
    assert payload["quick_ack_routes"] == ["/wecom/external-contact/callback", "/api/wecom/events"]
    assert payload["callback_route_details"]["/api/wecom/events"]["emergency_quick_ack_enabled"] is True


def test_callback_quick_ack_state_requires_explicit_probe_urls(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        location = /wecom/external-contact/callback { return 200 "success"; }
        location = /api/wecom/events { return 200 "success"; }
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("AICRM_CALLBACK_QUICK_ACK_PROBE_URL", raising=False)
    monkeypatch.delenv("AICRM_CALLBACK_QUICK_ACK_PROBE_URLS", raising=False)
    monkeypatch.setattr(
        quick_ack_state,
        "_recent_callback_events",
        lambda minutes: {"database_checked": True, "recent_app_callback_events": 0, "error": ""},
    )
    monkeypatch.setattr(
        quick_ack_state,
        "_probe_callback_post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not probe without explicit URL")),
    )

    payload = quick_ack_state.run(["--nginx-config", str(config)])

    assert payload["callback_post_checked"] is False
    assert payload["callback_post_probe_urls"] == []
    assert "probe url required" in payload["callback_post_error"]


def test_callback_quick_ack_state_probes_env_callback_urls(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        location = /wecom/external-contact/callback { return 200 "success"; }
        location = /api/wecom/events { return 200 "success"; }
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("AICRM_CALLBACK_QUICK_ACK_PROBE_URL", raising=False)
    monkeypatch.setenv(
        "AICRM_CALLBACK_QUICK_ACK_PROBE_URLS",
        "https://example.test/wecom/external-contact/callback?codex_quick_ack_probe=1,https://example.test/api/wecom/events?codex_quick_ack_probe=1",
    )
    monkeypatch.setattr(
        quick_ack_state,
        "_recent_callback_events",
        lambda minutes: {"database_checked": True, "recent_app_callback_events": 0, "error": ""},
    )
    seen_urls: list[str] = []

    def fake_probe(url: str, timeout_seconds: float) -> dict:
        seen_urls.append(url)
        return {"callback_post_checked": True, "callback_post_nginx_200": True, "status_code": 200, "body": "success", "error": ""}

    monkeypatch.setattr(quick_ack_state, "_probe_callback_post", fake_probe)

    payload = quick_ack_state.run(["--nginx-config", str(config)])

    assert payload["callback_post_checked"] is True
    assert payload["callback_post_nginx_200"] is True
    assert payload["callback_post_nginx_200_all"] is True
    assert payload["callback_post_nginx_200_any"] is True
    assert len(payload["callback_post_probes"]) == 2
    assert len(seen_urls) == 2
    assert any("/wecom/external-contact/callback" in url for url in seen_urls)
    assert any("/api/wecom/events" in url for url in seen_urls)


def test_callback_quick_ack_state_reports_partial_probe_success(monkeypatch, tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        location = /wecom/external-contact/callback { return 200 "success"; }
        location = /api/wecom/events { proxy_pass http://127.0.0.1:5002; }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        quick_ack_state,
        "_recent_callback_events",
        lambda minutes: {"database_checked": True, "recent_app_callback_events": 0, "error": ""},
    )

    def fake_probe(url: str, timeout_seconds: float) -> dict:
        is_primary = "/wecom/external-contact/callback" in url
        return {
            "callback_post_checked": True,
            "callback_post_nginx_200": is_primary,
            "status_code": 200 if is_primary else 400,
            "body": "success" if is_primary else "invalid callback",
            "error": "",
        }

    monkeypatch.setattr(quick_ack_state, "_probe_callback_post", fake_probe)

    payload = quick_ack_state.run(
        [
            "--nginx-config",
            str(config),
            "--probe-url",
            "https://example.test/wecom/external-contact/callback",
            "--probe-url",
            "https://example.test/api/wecom/events",
        ]
    )

    assert payload["callback_post_checked"] is True
    assert payload["callback_post_nginx_200"] is False
    assert payload["callback_post_nginx_200_all"] is False
    assert payload["callback_post_nginx_200_any"] is True
    assert [item["callback_post_nginx_200"] for item in payload["callback_post_probes"]] == [True, False]


def test_wecom_callback_ingress_cutover_check_rejects_missing_backpressure(tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        upstream aicrm_wecom_ingress {
            server 127.0.0.1:5002;
        }
        location = /wecom/external-contact/callback {
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        location = /api/wecom/events {
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        """,
        encoding="utf-8",
    )

    payload = run_cutover_check(["--nginx-config", str(config), "--skip-health-probe", "--skip-invalid-callback-probe"])

    assert payload["ready_for_cutover"] is False
    assert payload["nginx"]["callback_routes_proxy_to_5002"] is True
    assert payload["nginx"]["short_callback_timeouts_configured"] is True
    assert payload["nginx"]["callback_backpressure_configured"] is False
    assert any("backpressure" in warning for warning in payload["warnings"])


def test_wecom_callback_ingress_cutover_check_rejects_partial_route_cutover(tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        limit_req_zone $binary_remote_addr zone=aicrm_wecom_callback_req:10m rate=30r/s;
        limit_conn_zone $binary_remote_addr zone=aicrm_wecom_callback_conn:10m;
        upstream aicrm_wecom_ingress {
            server 127.0.0.1:5002;
        }
        location = /wecom/external-contact/callback {
            limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
            limit_conn aicrm_wecom_callback_conn 20;
            limit_req_status 429;
            limit_conn_status 429;
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        location = /api/wecom/events {
            limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
            limit_conn aicrm_wecom_callback_conn 20;
            limit_req_status 429;
            limit_conn_status 429;
            proxy_pass http://127.0.0.1:5001;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        """,
        encoding="utf-8",
    )

    payload = run_cutover_check(["--nginx-config", str(config), "--skip-health-probe", "--skip-invalid-callback-probe"])

    assert payload["ready_for_cutover"] is False
    assert payload["nginx"]["callback_routes_present"] is True
    assert payload["nginx"]["callback_route_details"]["/wecom/external-contact/callback"]["proxy_to_5002"] is True
    assert payload["nginx"]["callback_route_details"]["/api/wecom/events"]["proxy_to_5002"] is False
    assert any("127.0.0.1:5002" in warning for warning in payload["warnings"])


def test_wecom_callback_ingress_cutover_check_rejects_route_level_missing_backpressure(tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        limit_req_zone $binary_remote_addr zone=aicrm_wecom_callback_req:10m rate=30r/s;
        limit_conn_zone $binary_remote_addr zone=aicrm_wecom_callback_conn:10m;
        upstream aicrm_wecom_ingress {
            server 127.0.0.1:5002;
        }
        location = /wecom/external-contact/callback {
            limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
            limit_conn aicrm_wecom_callback_conn 20;
            limit_req_status 429;
            limit_conn_status 429;
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        location = /api/wecom/events {
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        """,
        encoding="utf-8",
    )

    payload = run_cutover_check(["--nginx-config", str(config), "--skip-health-probe", "--skip-invalid-callback-probe"])

    assert payload["ready_for_cutover"] is False
    assert payload["nginx"]["callback_routes_proxy_to_5002"] is True
    assert payload["nginx"]["callback_route_details"]["/api/wecom/events"]["backpressure_configured"] is False
    assert payload["nginx"]["callback_backpressure_configured"] is False
    assert any("backpressure" in warning for warning in payload["warnings"])


def test_wecom_callback_ingress_cutover_check_ignores_commented_backpressure(tmp_path) -> None:
    config = tmp_path / "nginx.conf"
    config.write_text(
        """
        # limit_req_zone $binary_remote_addr zone=aicrm_wecom_callback_req:10m rate=30r/s;
        # limit_conn_zone $binary_remote_addr zone=aicrm_wecom_callback_conn:10m;
        upstream aicrm_wecom_ingress {
            server 127.0.0.1:5002;
        }
        location = /wecom/external-contact/callback {
            # limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
            # limit_conn aicrm_wecom_callback_conn 20;
            # limit_req_status 429;
            # limit_conn_status 429;
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        location = /api/wecom/events {
            # limit_req zone=aicrm_wecom_callback_req burst=120 nodelay;
            # limit_conn aicrm_wecom_callback_conn 20;
            # limit_req_status 429;
            # limit_conn_status 429;
            proxy_pass http://aicrm_wecom_ingress;
            proxy_connect_timeout 1s;
            proxy_send_timeout 3s;
            proxy_read_timeout 3s;
        }
        """,
        encoding="utf-8",
    )

    payload = run_cutover_check(["--nginx-config", str(config), "--skip-health-probe", "--skip-invalid-callback-probe"])

    assert payload["ready_for_cutover"] is False
    assert payload["nginx"]["callback_backpressure_configured"] is False
    assert any("backpressure" in warning for warning in payload["warnings"])


def test_wecom_callback_ingress_cutover_check_accepts_5002_template_without_probe() -> None:
    payload = run_cutover_check(
        [
            "--nginx-config",
            "deploy/nginx-wecom-callback-ingress.conf.example",
            "--skip-health-probe",
            "--skip-invalid-callback-probe",
        ]
    )

    assert payload["ready_for_cutover"] is True
    assert payload["nginx"]["callback_routes_proxy_to_5002"] is True
    assert payload["nginx"]["short_callback_timeouts_configured"] is True
    assert payload["nginx"]["callback_backpressure_configured"] is True


def test_wecom_callback_ingress_cutover_probe_rejects_plain_success(monkeypatch) -> None:
    monkeypatch.setattr("scripts.ops.check_wecom_callback_ingress_cutover.probe_json", lambda url, timeout: {"checked": True, "ok": True, "status_code": 200, "body": "ok", "error": ""})
    monkeypatch.setattr(
        "scripts.ops.check_wecom_callback_ingress_cutover.probe_invalid_callback",
        lambda url, timeout: {"checked": True, "ok": False, "status_code": 200, "body": "success", "plain_success": True, "error": ""},
    )

    payload = run_cutover_check(["--nginx-config", "deploy/nginx-wecom-callback-ingress.conf.example"])

    assert payload["ready_for_cutover"] is False
    assert payload["invalid_callback"]["plain_success"] is True
    assert any("plain success" in warning for warning in payload["warnings"])


def test_wecom_callback_ingress_cutover_probe_accepts_app_level_invalid_callback(monkeypatch) -> None:
    monkeypatch.setattr("scripts.ops.check_wecom_callback_ingress_cutover.probe_json", lambda url, timeout: {"checked": True, "ok": True, "status_code": 200, "body": "ok", "error": ""})
    monkeypatch.setattr(
        "scripts.ops.check_wecom_callback_ingress_cutover.probe_invalid_callback",
        lambda url, timeout: {"checked": True, "ok": True, "status_code": 400, "body": "invalid callback", "plain_success": False, "error": ""},
    )

    payload = run_cutover_check(["--nginx-config", "deploy/nginx-wecom-callback-ingress.conf.example"])

    assert payload["ready_for_cutover"] is True
    assert payload["invalid_callback"]["status_code"] == 400
