from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects.adapters import webhook_execution_settings, wecom_execution_settings
from aicrm_next.platform_foundation.external_effects.realtime import realtime_wakeup_state
from aicrm_next.shared.db_session import reset_engine_cache_for_tests
from aicrm_next.shared.secret_store import FileSecretStore, is_secret_reference
from tests.admin_auth_test_helpers import install_admin_session


ROOT = Path(__file__).resolve().parents[1]


def _prepare_client(monkeypatch, tmp_path) -> TestClient:
    db_path = tmp_path / "admin_config_next.sqlite3"
    database_url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_SECRET_STORE_DIR", str(tmp_path / "secret-store"))
    monkeypatch.setenv("SECRET_KEY", "admin-config-next-test")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-env-corp")
    reset_engine_cache_for_tests()
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL DEFAULT '',
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wecom_userid TEXT NOT NULL,
                    wecom_corpid TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    auth_source TEXT NOT NULL DEFAULT 'wecom_sso',
                    last_login_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_by TEXT NOT NULL DEFAULT '',
                    login_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    admin_level TEXT NOT NULL DEFAULT 'admin',
                    session_version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_user_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_user_id INTEGER NOT NULL,
                    role_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(admin_user_id, role_code)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE admin_login_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_user_id INTEGER,
                    login_type TEXT NOT NULL DEFAULT '',
                    login_result TEXT NOT NULL DEFAULT '',
                    ip TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mcp_tool_settings (
                    tool_name TEXT PRIMARY KEY,
                    tool_group TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    description_override TEXT NOT NULL DEFAULT '',
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    visible_in_console BOOLEAN NOT NULL DEFAULT TRUE,
                    show_sample_args BOOLEAN NOT NULL DEFAULT FALSE,
                    show_sample_output BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE questionnaires (
                    id INTEGER PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    is_disabled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE questionnaire_questions (
                    id INTEGER PRIMARY KEY,
                    questionnaire_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    required BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE questionnaire_options (
                    id INTEGER PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    option_text TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE marketing_automation_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_key TEXT NOT NULL UNIQUE,
                    automation_name TEXT NOT NULL DEFAULT '',
                    target_event TEXT NOT NULL DEFAULT 'signup_success',
                    channel_type TEXT NOT NULL DEFAULT 'text_message',
                    status TEXT NOT NULL DEFAULT 'active',
                    do_not_start_after_hour INTEGER NOT NULL DEFAULT 23,
                    config_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE marketing_automation_question_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_config_id INTEGER NOT NULL,
                    questionnaire_id INTEGER,
                    question_id INTEGER,
                    rule_code TEXT NOT NULL DEFAULT '',
                    rule_name TEXT NOT NULL DEFAULT '',
                    answer_match_type TEXT NOT NULL DEFAULT 'any_of',
                    answer_match_value_json TEXT NOT NULL DEFAULT '[]',
                    score_delta INTEGER NOT NULL DEFAULT 0,
                    segment_hint TEXT NOT NULL DEFAULT '',
                    stage_hint TEXT NOT NULL DEFAULT '',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    rule_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECOM_SECRET', 'super-secret-value')"))
        conn.execute(text("INSERT INTO questionnaires (id, slug, name, title) VALUES (81, 'signup-test', '报名问卷', '报名问卷')"))
        conn.execute(
            text(
                """
                INSERT INTO questionnaire_questions (id, questionnaire_id, type, title, required, sort_order)
                VALUES
                    (811, 81, 'single_choice', '你想咨询什么课程？', TRUE, 1),
                    (812, 81, 'multi_choice', '你关注哪些服务？', FALSE, 2)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO questionnaire_options (id, question_id, option_text, sort_order)
                VALUES
                    (8111, 811, 'AI 课程', 1),
                    (8112, 811, '私域运营', 2),
                    (8121, 812, '训练营', 1)
                """
            )
        )
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, "super_admin")
    return client


def _token(
    html: str,
    *,
    method: str = "PUT",
    path: str = "/api/admin/config/app-settings",
) -> str:
    match = re.search(
        r'<script id="aicrmAdminActionGrants" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))[f"{method} {path}"]


def _db_url(monkeypatch) -> str:
    return str(__import__("os").environ["DATABASE_URL"])


def _scalar(database_url: str, sql: str, params: dict | None = None):
    engine = create_engine(database_url, future=True)
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def test_admin_config_pages_are_next_owned_and_nonblank(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    for path, marker in [
        ("/admin/config", "系统配置"),
        ("/admin/config/app-settings", "系统设置"),
        ("/admin/config/detail/admin_access", "后台访问"),
        ("/admin/config/checklist", "配置检查清单"),
        ("/setup/wizard", "系统配置向导"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.text
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        assert "X-AICRM-Compatibility-Facade" not in response.text

    config_home = client.get("/admin/config")
    assert "admin-quick-links" not in config_home.text
    assert "config-editor-layout" not in config_home.text
    for marker in ["类目", "是否生效", "生效开关", "配置"]:
        assert marker in config_home.text


def test_legacy_login_access_page_redirects_to_admin_access_detail(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    response = client.get("/admin/config/login-access?edit_id=7&error=needs-member", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/admin/config/detail/admin_access?")
    assert "edit_id=7" in response.headers["location"]
    assert "error=needs-member" in response.headers["location"]

    wecom_tags_alias = client.get("/admin/config/wecom-tags", follow_redirects=False)
    assert wecom_tags_alias.status_code == 302
    assert wecom_tags_alias.headers["location"] == "/admin/wecom-tags"
    assert "X-AICRM-Compatibility-Facade" not in wecom_tags_alias.headers


def test_app_settings_api_masks_secrets_and_save_is_idempotent(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    token = _token(client.get("/admin/config/app-settings").text)

    masked_response = client.get("/api/admin/config/app-settings")
    assert masked_response.status_code == 200
    text_payload = masked_response.text
    assert "super-secret-value" not in text_payload
    assert "[redacted]" in text_payload

    save_payload = {
        "admin_action_token": token,
        "confirm": True,
        "operator": "next-test",
        "settings": {"WECOM_CORP_ID": "ww-next-corp", "WECOM_SECRET": ""},
    }
    first = client.put("/api/admin/config/app-settings", json=save_payload)
    second = client.put("/api/admin/config/app-settings", json=save_payload)

    assert first.status_code == 200
    assert first.json()["source_status"] == "next_command"
    assert first.json()["fallback_used"] is False
    assert first.json()["real_external_call_executed"] is False
    assert first.json()["changed_count"] == 1
    assert second.status_code == 200
    assert second.json()["changed_count"] == 0
    database_url = _db_url(monkeypatch)
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECOM_CORP_ID'") == "ww-next-corp"
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECOM_SECRET'") == "super-secret-value"
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'app_setting' AND target_id = 'WECOM_CORP_ID'") == 1


def test_app_settings_api_secret_write_returns_metadata_without_raw_value_or_reference(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    token = _token(client.get("/admin/config/app-settings").text)
    raw_secret = "complete-contact-secret"

    response = client.put(
        "/api/admin/config/app-settings",
        json={
            "admin_action_token": token,
            "confirm": True,
            "operator": "next-test",
            "settings": {"WECOM_CONTACT_SECRET": raw_secret},
        },
    )

    assert response.status_code == 200
    assert raw_secret not in response.text
    assert "secretref:file:" not in response.text
    changed = response.json()["changed"]
    assert changed == [
        {
            "key": "WECOM_CONTACT_SECRET",
            "configured": True,
            "display_value": "[redacted]",
            "version": changed[0]["version"],
            "updated_at": changed[0]["updated_at"],
        }
    ]
    stored = _scalar(_db_url(monkeypatch), "SELECT value FROM app_settings WHERE key = 'WECOM_CONTACT_SECRET'")
    assert is_secret_reference(stored)
    assert FileSecretStore(tmp_path / "secret-store").read(stored) == raw_secret


def test_app_settings_save_requires_clear_token_and_confirm_errors(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    missing_token = client.put("/api/admin/config/app-settings", json={"confirm": True, "settings": {"WECOM_CORP_ID": "ww"}})
    missing_confirm = client.put(
        "/api/admin/config/app-settings",
        json={"admin_action_token": _token(client.get("/admin/config/app-settings").text), "settings": {"WECOM_CORP_ID": "ww"}},
    )

    assert missing_token.status_code == 400
    assert "admin_action_token" in missing_token.json()["error"]
    assert missing_confirm.status_code == 400
    assert "confirm is required" in missing_confirm.json()["error"]


def test_config_categories_api_lists_category_summaries(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    response = client.get("/api/admin/config/categories")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_status"] == "next_read_model"
    rows = payload["config"]["rows"]
    category_keys = {row["key"] for row in rows}
    assert {"wecom_base", "wechat_pay", "alipay", "wechat_shop", "admin_access"}.issubset(category_keys)
    wechat_pay = next(row for row in rows if row["key"] == "wechat_pay")
    assert wechat_pay["label"] == "微信支付"
    assert wechat_pay["enabled"] is False
    assert wechat_pay["detail_href"] == "/admin/config/detail/wechat_pay"
    admin_access = next(row for row in rows if row["key"] == "admin_access")
    assert admin_access["enabled"] is True


def test_config_category_detail_returns_blocks_and_masks_sensitive_fields(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    response = client.get("/api/admin/config/categories/wecom_base")
    page_endpoint = client.get("/admin/config/detail/wecom_base")

    assert response.status_code == 200
    assert page_endpoint.status_code == 200
    assert "企业微信基础" in page_endpoint.text
    assert "config-detail-block" in page_endpoint.text
    assert "config-editor-layout" not in page_endpoint.text
    assert 'placeholder="已设置"' in page_endpoint.text
    config = response.json()["config"]
    assert config["category"]["key"] == "wecom_base"
    fields = [field for block in config["blocks"] for field in block["fields"]]
    secret = next(field for field in fields if field["key"] == "WECOM_SECRET")
    assert secret["sensitive"] is True
    assert secret["value"] == ""
    assert secret["display_value"] == "[redacted]"
    assert secret["configured"] is True
    assert any(field["key"] == "WECOM_CALLBACK_TOKEN" for field in fields)


def test_admin_access_detail_combines_settings_and_member_picker_authorization(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    database_url = _db_url(monkeypatch)
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO admin_users (wecom_userid, wecom_corpid, display_name, is_active, login_enabled, admin_level)
                VALUES ('owner.admin', 'ww-next-corp', 'Owner Admin', TRUE, TRUE, 'super_admin')
                """
            )
        )
        conn.execute(text("INSERT INTO admin_user_roles (admin_user_id, role_code) VALUES (1, 'super_admin')"))

    response = client.get("/admin/config/detail/admin_access?edit_id=1")

    assert response.status_code == 200
    assert "后台访问" in response.text
    assert "后台管理员" in response.text
    assert "超级管理员" in response.text
    assert "编辑管理员" in response.text
    assert "data-admin-access-detail" in response.text
    assert "data-admin-access-member-picker" in response.text
    assert "data-admin-access-member-feedback" in response.text
    assert "data-admin-access-super-admin" in response.text
    assert 'type="hidden" name="wecom_userid"' in response.text
    assert 'type="text" name="wecom_userid"' not in response.text
    assert '<select name="wecom_userid"' not in response.text
    assert 'type="text" name="display_name"' not in response.text
    assert 'type="text" name="wecom_corpid"' not in response.text
    assert 'name="role_codes" value="viewer"' in response.text
    assert "访问概览" not in response.text
    assert "当前企业 ID" not in response.text
    assert "后台授权成员" not in response.text
    assert "最近登录审计" not in response.text
    assert "角色分配" not in response.text
    assert "授权来源" not in response.text
    assert "操作人" not in response.text
    assert "未缓存通讯录时保留手工输入兜底" not in response.text
    assert "手动输入客服 ID" not in response.text

    add_response = client.get("/admin/config/detail/admin_access")
    assert add_response.status_code == 200
    assert "加入管理员" in add_response.text


def test_retired_reply_monitor_chat_settings_are_not_exposed(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    response = client.get("/api/admin/config/categories/ai_automation")

    assert response.status_code == 200
    fields = [field for block in response.json()["config"]["blocks"] for field in block["fields"]]
    keys = {field["key"] for field in fields}
    assert "DEEPSEEK_ENABLED" in keys
    assert "AUTOMATION_INTERNAL_API_TOKEN" not in keys
    assert "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID" in keys
    assert "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_SECRET_REF" in keys
    assert not {key for key in keys if key.startswith("LAOHUANG_CHAT_")}


def test_config_category_enabled_update_uses_app_settings_and_preserves_auth_tables(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    database_url = _db_url(monkeypatch)
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO admin_users (wecom_userid, wecom_corpid, display_name, is_active, login_enabled, admin_level)
                VALUES ('owner.admin', 'ww-next-corp', 'Owner Admin', TRUE, TRUE, 'super_admin')
                """
            )
        )
        conn.execute(text("INSERT INTO admin_user_roles (admin_user_id, role_code) VALUES (1, 'super_admin')"))
        conn.execute(
            text(
                """
                INSERT INTO admin_login_audit (admin_user_id, login_type, login_result, ip, user_agent)
                VALUES (1, 'wecom_sso', 'success', '127.0.0.1', 'pytest')
                """
            )
        )
    token = _token(
        client.get("/admin/config/app-settings").text,
        path="/api/admin/config/categories/{category_key}/enabled",
    )

    response = client.put(
        "/api/admin/config/categories/wechat_pay/enabled",
        json={"admin_action_token": token, "enabled": True, "operator": "category-test"},
    )

    assert response.status_code == 200
    assert response.json()["source_status"] == "next_command"
    assert response.json()["fallback_used"] is False
    assert response.json()["real_external_call_executed"] is False
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECHAT_PAY_ENABLED'") == "true"
    assert (
        _scalar(
            database_url,
            "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'config_category_enabled' AND target_id = 'wechat_pay'",
        )
        == 1
    )
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_users WHERE wecom_userid = 'owner.admin'") == 1
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_user_roles WHERE role_code = 'super_admin'") == 1
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_login_audit WHERE login_result = 'success'") == 1


def test_config_category_settings_save_skips_empty_sensitive_and_rejects_cross_category_keys(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    database_url = _db_url(monkeypatch)
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('WECHAT_PAY_API_V3_KEY', 'original-v3-secret')"))
    token_page = client.get("/admin/config/app-settings").text
    category_settings_token = _token(
        token_page,
        path="/api/admin/config/categories/{category_key}/settings",
    )
    saved = client.put(
        "/api/admin/config/categories/wechat_pay/settings",
        json={
            "admin_action_token": category_settings_token,
            "operator": "category-settings-test",
            "settings": {
                "WECHAT_PAY_NOTIFY_URL": "https://pay.example.test/notify",
                "WECHAT_PAY_TIMEOUT_SECONDS": "15",
                "WECHAT_PAY_API_V3_KEY": "",
            },
        },
    )
    cross_category = client.put(
        "/api/admin/config/categories/wechat_pay/settings",
        json={
            "admin_action_token": category_settings_token,
            "operator": "category-settings-test",
            "settings": {"ALIPAY_APP_ID": "alipay-app"},
        },
    )

    assert saved.status_code == 200
    assert saved.json()["changed_count"] == 2
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECHAT_PAY_NOTIFY_URL'") == "https://pay.example.test/notify"
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECHAT_PAY_TIMEOUT_SECONDS'") == "15"
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECHAT_PAY_API_V3_KEY'") == "original-v3-secret"
    assert cross_category.status_code == 400
    assert "not in category" in cross_category.json()["error"]


def test_webhooks_push_category_controls_external_effect_runtime(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    database_url = _db_url(monkeypatch)
    token_page = client.get("/admin/config/app-settings").text
    category_settings_token = _token(
        token_page,
        path="/api/admin/config/categories/{category_key}/settings",
    )
    push_capability_token = _token(
        token_page,
        method="PATCH",
        path="/api/admin/config/push-capabilities/{capability_key}",
    )

    detail_page = client.get("/admin/config/detail/webhooks_push")
    assert detail_page.status_code == 200
    assert "推送能力配置" in detail_page.text
    assert "/api/admin/config/push-capabilities" in detail_page.text
    assert "/api/admin/push-center/stats" not in detail_page.text
    assert "/api/admin/push-center/legacy-deprecations" not in detail_page.text
    assert "Webhook 队列真实执行" not in detail_page.text
    assert "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES" not in detail_page.text

    rejected_legacy_save = client.put(
        "/api/admin/config/categories/webhooks_push/settings",
        json={
            "admin_action_token": category_settings_token,
            "operator": "webhook-runtime-test",
            "settings": {
                "AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE": "queue",
                "AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE": True,
                "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES": "webhook.questionnaire_submission.push",
                "AICRM_EXTERNAL_EFFECT_WEBHOOK_TIMEOUT_SECONDS": "9",
                "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE": True,
                "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS": "HuangYouCan",
                "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS": "wm_fixture_a",
                "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_OPS_WEBHOOK_KEYS": "测试运营计划-ce2519",
                "AICRM_EXTERNAL_EFFECT_ALLOWED_GROUP_CHAT_IDS": "wr_chat_fixture",
                "AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED": True,
                "AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY": True,
                "AICRM_EXTERNAL_EFFECT_ALLOWED_BASE_HOSTS": "www.youcangogogo.com,youcangogogo.com",
                "AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE": True,
                "AICRM_EXTERNAL_EFFECT_FEISHU_EXECUTE": False,
                "AICRM_EXTERNAL_EFFECT_OPENCLAW_EXECUTE": False,
                "AICRM_EXTERNAL_EFFECT_MEDIA_UPLOAD_EXECUTE": False,
            },
        },
    )

    assert rejected_legacy_save.status_code == 400
    assert "managed by push capabilities API" in rejected_legacy_save.json()["error"]

    saved = client.patch(
        "/api/admin/config/push-capabilities/questionnaire_external_push",
        headers={"X-Admin-Action-Token": push_capability_token},
        json={"enabled": True, "operator": "webhook-runtime-test"},
    )

    assert saved.status_code == 200
    assert saved.json()["capability"]["enabled"] is True
    assert saved.json()["derived_gates"]["allowed_effect_types"] == ["webhook.questionnaire_submission.push"]
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE'") == "true"
    execution = webhook_execution_settings()
    assert execution["enabled"] is True
    assert execution["allowed_types"] == ["webhook.questionnaire_submission.push"]
    wecom_execution = wecom_execution_settings()
    assert wecom_execution["enabled"] is False
    assert wecom_execution["execution_mode"] == "disabled"
    assert "wecom_execution_disabled" in wecom_execution["blocking_reasons"]
    assert saved.json()["derived_gates"]["wecom_execute"] is False
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE'") == "false"

    welcome_saved = client.patch(
        "/api/admin/config/push-capabilities/welcome_message",
        headers={"X-Admin-Action-Token": push_capability_token},
        json={"enabled": True, "operator": "webhook-runtime-test"},
    )

    assert welcome_saved.status_code == 200
    assert welcome_saved.json()["capability"]["enabled"] is True
    assert "wecom.welcome_message.send" in welcome_saved.json()["derived_gates"]["allowed_effect_types"]
    assert "wecom.message.private.send" in welcome_saved.json()["derived_gates"]["allowed_effect_types"]
    assert welcome_saved.json()["derived_gates"]["realtime_enabled"] is True
    assert welcome_saved.json()["derived_gates"]["realtime_allowed_types"] == ["wecom.welcome_message.send"]
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'AICRM_EXTERNAL_EFFECT_REALTIME_ENABLED'") == "true"
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'AICRM_EXTERNAL_EFFECT_REALTIME_ALLOWED_TYPES'") == "wecom.welcome_message.send"
    realtime_state = realtime_wakeup_state()
    assert realtime_state["status"] == "durable_signal_only"
    assert realtime_state["channel_entry_missing_types"] == []
    assert realtime_state["channel_entry_ready"] is True
    assert realtime_state["dispatch_boundary"] == "postgres_execution_runtime_claim_one"
    assert realtime_state["provider_dispatch_allowed"] is False

    rejected = client.put(
        "/api/admin/config/categories/webhooks_push/settings",
        json={
            "admin_action_token": category_settings_token,
            "operator": "webhook-runtime-test",
            "settings": {"AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES": "*"},
        },
    )

    assert rejected.status_code == 400
    assert "push capabilities API" in rejected.json()["error"]

    rejected_host = client.put(
        "/api/admin/config/categories/webhooks_push/settings",
        json={
            "admin_action_token": category_settings_token,
            "operator": "webhook-runtime-test",
            "settings": {"AICRM_EXTERNAL_EFFECT_ALLOWED_BASE_HOSTS": "localhost"},
        },
    )

    assert rejected_host.status_code == 400
    assert "push capabilities API" in rejected_host.json()["error"]


def test_config_category_check_and_invalid_category_are_controlled(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    check = client.post("/api/admin/config/categories/wechat_pay/check", json={"operator": "check-test"})
    missing = client.get("/api/admin/config/categories/not-a-category")
    save_missing = client.put(
        "/api/admin/config/categories/not-a-category/settings",
        json={
            "admin_action_token": _token(
                client.get("/admin/config/app-settings").text,
                path="/api/admin/config/categories/{category_key}/settings",
            ),
            "settings": {"WECOM_CORP_ID": "ww"},
        },
    )

    assert check.status_code == 200
    payload = check.json()
    assert payload["source_status"] == "next_command"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["adapter_preview"]["adapter"] == "wechat_pay"
    assert payload["adapter_preview"]["real_external_call_executed"] is False
    assert missing.status_code == 404
    assert save_missing.status_code == 404


def test_setup_wizard_saves_and_repeated_submit_is_noop(monkeypatch, tmp_path) -> None:
    required_auth_settings = {
        "AICRM_AUTH_ISSUER": "https://crm.example.test/oauth",
        "AICRM_AUTH_SESSION_HASH_PEPPER": "secretref:file:AICRM_AUTH_SESSION_HASH_PEPPER:v1_test",
        "AICRM_AUTH_JWT_SIGNING_KEY": "secretref:file:AICRM_AUTH_JWT_SIGNING_KEY:v1_test",
        "AICRM_AUTH_OUTBOUND_WEBHOOK_CLIENT_ID": "aicrm-outbound-webhook",
    }
    for purpose in (
        "AUTOMATION_WORKER",
        "ARCHIVE_WORKER",
        "CALLBACK_WORKER",
        "GROUP_BROADCAST",
        "IDENTITY",
        "MCP",
        "EXTERNAL_AGENT",
        "CAMPAIGN_AGENT",
        "OPS_REPORTER",
    ):
        required_auth_settings[f"AICRM_AUTH_{purpose}_CLIENT_ID"] = f"pytest-{purpose.lower().replace('_', '-')}"
        required_auth_settings[f"AICRM_AUTH_{purpose}_CLIENT_SECRET_REF"] = f"secretref:file:AICRM_AUTH_{purpose}_CLIENT_SECRET:v1_test"
    for key, value in required_auth_settings.items():
        monkeypatch.setenv(key, value)
    client = _prepare_client(monkeypatch, tmp_path)
    token = _token(
        client.get("/setup/wizard").text,
        method="POST",
        path="/setup/wizard/save",
    )
    payload = {
        "admin_action_token": token,
        "operator": "wizard-test",
        "setting__WECOM_CORP_ID": "ww-wizard",
        "setting__WECOM_SECRET": "",
        "setting__WECOM_AGENT_ID": "1000",
        "setting__WECOM_CONTACT_SECRET": "contact",
        "setting__WECOM_DEFAULT_OWNER_USERID": "owner",
        "setting__WECOM_CALLBACK_TOKEN": "callback",
        "setting__WECOM_CALLBACK_AES_KEY": "aes",
    }

    first = client.post("/setup/wizard/save", data=payload)
    second = client.post("/setup/wizard/save", data=payload)

    assert first.status_code == 200
    assert "配置已保存成功" in first.text
    assert second.status_code == 200
    assert "配置已保存成功" in second.text
    database_url = _db_url(monkeypatch)
    assert _scalar(database_url, "SELECT value FROM app_settings WHERE key = 'WECOM_AGENT_ID'") == "1000"
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'app_setting' AND target_id = 'WECOM_AGENT_ID'") == 1


def test_login_access_save_and_directory_refresh_do_not_call_wecom(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)
    token_page = client.get("/admin/config/detail/admin_access").text
    refresh_token = _token(
        token_page,
        method="POST",
        path="/admin/config/login-access/directory/refresh",
    )
    save_token = _token(
        token_page,
        method="POST",
        path="/admin/config/login-access/save",
    )

    refresh = client.post(
        "/admin/config/login-access/directory/refresh",
        data={"admin_action_token": refresh_token},
        follow_redirects=False,
    )
    save = client.post(
        "/admin/config/login-access/save",
        data={
            "admin_action_token": save_token,
            "wecom_userid": "root.admin",
            "wecom_corpid": "ww-next-corp",
            "display_name": "Root Admin",
            "auth_source": "wecom_sso",
            "is_active": "1",
            "login_enabled": "1",
            "admin_level": "admin",
            "role_codes": "viewer",
            "operator": "access-test",
        },
        follow_redirects=False,
    )

    assert refresh.status_code == 302
    assert refresh.headers["location"].startswith("/admin/config/detail/admin_access")
    assert "real_external" not in refresh.headers.get("location", "")
    assert save.status_code == 302
    assert save.headers["location"].startswith("/admin/config/detail/admin_access")
    database_url = _db_url(monkeypatch)
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_users WHERE wecom_userid = 'root.admin'") == 1
    assert _scalar(database_url, "SELECT session_version FROM admin_users WHERE wecom_userid = 'root.admin'") == 2
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_user_roles WHERE role_code = 'viewer'") == 1
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'admin_user'") == 1


def test_mcp_tool_settings_api_is_next_owned_and_audited(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    page = client.get("/admin/config/mcp-tools", follow_redirects=False)
    before = client.get("/api/admin/config/mcp-tools")
    save = client.post(
        "/api/admin/config/mcp-tools",
        json={
            "tool_name": "resolve_customer",
            "tool_group": "crm",
            "display_name": "Resolve Customer",
            "description_override": "disabled for test",
            "enabled": False,
            "visible_in_console": True,
            "show_sample_args": False,
            "show_sample_output": False,
            "sort_order": 99,
            "operator": "mcp-test",
        },
    )
    after = client.get("/api/admin/config/mcp-tools")

    assert page.status_code == 302
    assert page.headers["location"] == "/admin/api-docs"
    assert before.status_code == 200
    assert before.json()["source_status"] == "next_read_model"
    assert "resolve_customer" in {row["tool_name"] for row in before.json()["config"]["rows"]}
    assert save.status_code == 200
    assert save.json()["source_status"] == "next_command"
    assert save.json()["fallback_used"] is False
    assert save.json()["real_external_call_executed"] is False
    assert save.json()["item"]["enabled"] is False
    resolve_row = next(row for row in after.json()["config"]["rows"] if row["tool_name"] == "resolve_customer")
    assert resolve_row["enabled"] is False
    assert resolve_row["description_override"] == "disabled for test"
    database_url = _db_url(monkeypatch)
    assert _scalar(database_url, "SELECT COUNT(*) FROM mcp_tool_settings WHERE tool_name = 'resolve_customer' AND enabled = 0") == 1
    assert _scalar(database_url, "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'mcp_tool_setting' AND target_id = 'resolve_customer'") == 1


def test_signup_conversion_config_alias_is_next_owned_and_audited(monkeypatch, tmp_path) -> None:
    client = _prepare_client(monkeypatch, tmp_path)

    initial = client.get("/api/admin/config/marketing-automation/signup-conversion")
    assert initial.status_code == 200
    assert initial.json()["config"]["configured"] is False
    assert initial.json()["source_status"] == "next_read_model"
    assert "X-AICRM-Compatibility-Facade" not in initial.headers

    payload = {
        "operator": "marketing-config-test",
        "enabled": True,
        "questionnaire_id": 81,
        "core_threshold": 2,
        "top_threshold": 5,
        "day_start_hour": 8,
        "quiet_hour_start": 22,
        "timezone": "Asia/Shanghai",
        "silent_threshold_days_by_pool": {
            "new_user": 3,
            "inactive_normal": 4,
            "inactive_focus": 5,
            "active_normal": 6,
            "active_focus": 7,
        },
        "question_rules": [
            {"questionnaire_question_id": 811, "hit_option_ids_json": [8111, 8112], "sort_order": 1},
            {"questionnaire_question_id": 812, "hit_option_ids_json": [8121], "sort_order": 2},
        ],
    }
    saved = client.put("/api/admin/config/marketing-automation/signup-conversion", json=payload)
    loaded = client.get("/api/admin/config/marketing-automation/signup-conversion")

    assert saved.status_code == 200
    assert saved.json()["source_status"] == "next_command"
    assert saved.json()["fallback_used"] is False
    assert saved.json()["real_external_call_executed"] is False
    assert saved.json()["config"]["configured"] is True
    assert saved.json()["config"]["questionnaire_id"] == 81
    assert saved.json()["config"]["core_threshold"] == 2
    assert saved.json()["config"]["top_threshold"] == 5
    assert saved.json()["config"]["question_rules"][0]["question_title"] == "你想咨询什么课程？"
    assert loaded.json()["config"]["silent_threshold_days_by_pool"]["inactive_focus"] == 5
    database_url = _db_url(monkeypatch)
    assert _scalar(database_url, "SELECT COUNT(*) FROM marketing_automation_configs WHERE automation_key = 'signup_conversion_v1'") == 1
    assert _scalar(database_url, "SELECT COUNT(*) FROM marketing_automation_question_rules WHERE automation_config_id = 1") == 2
    assert (
        _scalar(
            database_url,
            "SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'marketing_automation_config' AND target_id = 'signup_conversion_v1'",
        )
        == 1
    )


def test_admin_config_routes_no_longer_forward_to_legacy_facade() -> None:
    assert not (ROOT / "aicrm_next/frontend_compat/legacy_routes.py").exists()
    admin_config_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "aicrm_next/admin_config").glob("*.py"))
    assert "legacy_flask_facade" not in admin_config_source
    assert "forward_to_legacy_flask" not in admin_config_source
    assert "wecom_ability" + "_service" not in admin_config_source


def test_marketing_automation_config_page_points_to_ai_audience_not_legacy_programs() -> None:
    template = (ROOT / "aicrm_next/frontend_compat/templates/admin_console/config_marketing_automation.html").read_text(encoding="utf-8")

    assert "AI 自动化运营入口" in template
    assert "进入 AI 自动化运营" in template
    assert "automation_program_overview_href" not in template
    assert "自动化转化兼容入口" not in template
    assert "进入数据概览" not in template
    assert "按方案维护" not in template
    assert "任务流与节点" not in template
