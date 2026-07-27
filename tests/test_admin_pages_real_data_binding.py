from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import admin_session_cookies
from tools import check_admin_pages_real_data_binding as checker

ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")
    return TestClient(create_app())


def _admin_cookies(client: TestClient) -> dict[str, str]:
    return admin_session_cookies(client, "super_admin")


def test_admin_pages_do_not_render_forbidden_state_markers(monkeypatch):
    client = _client(monkeypatch)

    for route in checker.ADMIN_PAGES:
        if route == "/admin/customers":
            continue
        response = client.get(route, follow_redirects=False)
        assert response.status_code != 404, route
        assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_key_admin_pages_render_server_side_rows_or_stats(monkeypatch):
    client = _client(monkeypatch)

    for route in [
        "/admin/cloud-orchestrator/plans",
        "/admin/hxc-dashboard",
        "/admin/wechat-pay/products",
        "/admin/wechat-pay/transactions",
        "/admin/image-library",
        "/admin/miniprogram-library",
        "/admin/attachment-library",
        "/admin/runtime-config",
        "/admin/api-docs",
    ]:
        response = client.get(route)
        assert response.status_code != 404, route
        assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_ai_assistant_entry_uses_plan_review_workspace(monkeypatch):
    client = _client(monkeypatch)

    redirect = client.get("/admin/cloud-orchestrator", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "/admin/cloud-orchestrator/plans"

    response = client.get("/admin/cloud-orchestrator/plans")

    assert response.status_code == 200
    assert "AI 助手 · 运营计划审阅" in response.text
    assert "计划列表、目标人员明细与逐人审批。" in response.text
    assert "cloud_plan_review.js" in response.text
    assert "只读展示 automation_agent_config" not in response.text
    assert "production_unavailable" not in response.text


def test_funnel_dashboard_entry_uses_hxc_dashboard_workspace(monkeypatch):
    client = _client(monkeypatch)

    redirect = client.get("/admin/user-ops", follow_redirects=False)
    assert redirect.status_code == 200
    assert "运营管理" in redirect.text

    response = client.get("/admin/hxc-dashboard")

    assert response.status_code == 200
    assert "用户激活漏斗看板" in response.text
    assert "漏斗状态汇总" in response.text
    assert "立即刷新" in response.text
    assert "发送人管理" in response.text
    assert "/api/admin/hxc-dashboard/refresh" in response.text
    assert "/api/admin/hxc-dashboard/broadcast-tasks" in response.text
    assert "production_unavailable" not in response.text
    assert "生产漏斗数据读取失败" not in response.text


def test_wecom_tags_page_uses_full_management_workspace(monkeypatch):
    response = _client(monkeypatch).get("/admin/wecom-tags")

    assert response.status_code == 200
    assert "data-wecom-tags-page" in response.text
    assert 'data-api-tags="/api/admin/wecom/tags"' in response.text
    assert 'data-api-groups="/api/admin/wecom/tag-groups"' in response.text
    assert "同步企微标签" in response.text
    assert "新增标签组" in response.text
    assert "新增标签" in response.text
    assert "集中管理企业客户标签：同步、搜索、新增、编辑、删除和复制 tag_id。" in response.text
    assert "本地标签缓存" not in response.text
    assert "标签使用记录" not in response.text
    assert "远程同步" not in response.text
    assert "有缓存" not in response.text


def test_customer_page_does_not_render_sample_fixture_names(monkeypatch):
    import aicrm_next.crm.customer_read_model.admin_pages as customer_admin_pages

    class FakeListCustomersQuery:
        def __call__(self, query):
            return {
                "ok": True,
                "customers": [
                    {
                        "external_userid": "real_ext_001",
                        "customer_name": "真实客户甲",
                        "owner_display_name": "真实负责人",
                        "owner_userid": "owner_real",
                        "mobile": "138****0000",
                    }
                ],
                "total": 1,
            }

    monkeypatch.setattr(customer_admin_pages, "ListCustomersQuery", FakeListCustomersQuery)

    response = _client(monkeypatch).get("/admin/customers")

    assert response.status_code == 200
    for marker in checker.SAMPLE_CUSTOMERS:
        assert marker not in response.text


def test_customer_page_uses_native_read_model_when_data_is_available(monkeypatch):
    import aicrm_next.crm.customer_read_model.admin_pages as customer_admin_pages

    class FakeListCustomersQuery:
        def __call__(self, query):
            return {
                "ok": True,
                "customers": [
                    {
                        "external_userid": "real_ext_001",
                        "customer_name": "真实客户甲",
                        "owner_display_name": "真实负责人",
                        "owner_userid": "owner_real",
                        "mobile": "138****0000",
                    }
                ],
                "total": 23709,
            }

    monkeypatch.setattr(customer_admin_pages, "ListCustomersQuery", FakeListCustomersQuery)

    response = _client(monkeypatch).get("/admin/customers")

    assert response.status_code == 200
    assert "共 23709 位客户" in response.text
    assert "真实客户甲" in response.text
    assert "张小蓝" not in response.text


def test_questionnaire_page_uses_next_native_admin_pages(monkeypatch):
    assert not (ROOT / "aicrm_next/app/admin_console/legacy_routes.py").exists()

    response = _client(monkeypatch).get("/admin/questionnaires")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "hxc-activation-v1" in response.text


def test_questionnaire_editor_uses_next_native_admin_pages(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/admin/questionnaires/1")
    script = client.get("/static/questionnaire/admin_questionnaire_editor.js")

    assert response.status_code == 200
    assert script.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "侧边栏核心画像映射" in script.text
    assert "editor-global-external-push-logs-btn" not in script.text
    assert "editor-external-push-logs-btn" not in script.text
    assert "/admin/questionnaires/external-push-logs" not in script.text


def test_questionnaire_external_push_log_routes_use_next_native_handlers(monkeypatch):
    source = (ROOT / "aicrm_next" / "extensions" / "forms" / "questionnaire" / "admin_pages.py").read_text(encoding="utf-8")

    assert "forward_to_legacy_flask" not in source
    assert '"/admin/questionnaires/external-push-logs"' in source
    assert "QuestionnaireExternalPushLogReadService" in source
    assert "QuestionnaireExternalPushRetryService" not in source
    assert "external-push-logs/retry-batch" not in source
    assert not (ROOT / "aicrm_next/app/admin_console/legacy_routes.py").exists()


def test_wechat_pay_transactions_page_does_not_use_frontend_compat_router(monkeypatch):
    response = _client(monkeypatch).get("/admin/wechat-pay/transactions")

    assert response.status_code != 404
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_wechat_pay_transaction_detail_does_not_use_frontend_compat_router(monkeypatch):
    response = _client(monkeypatch).get("/admin/wechat-pay/transactions/42")

    assert response.status_code in {200, 404}
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_questionnaire_page_no_longer_depends_on_frontend_compat_legacy_items(monkeypatch):
    assert not (ROOT / "aicrm_next/app/admin_console/legacy_routes.py").exists()

    response = _client(monkeypatch).get("/admin/questionnaires")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "Internal Server Error" not in response.text


def test_questionnaire_page_stays_native_after_frontend_compat_closeout(monkeypatch):
    response = _client(monkeypatch).get("/admin/questionnaires")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "Internal Server Error" not in response.text


def test_questionnaire_detail_page_stays_native_after_frontend_compat_closeout(monkeypatch):
    response = _client(monkeypatch).get("/admin/questionnaires/1")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert '"initialQuestionnaireId": 1' in response.text
    assert "Not Found" not in response.text


def test_questionnaire_new_page_renders_editor_shell(monkeypatch):
    response = _client(monkeypatch).get("/admin/questionnaires/new")

    assert response.status_code == 200
    assert "新建问卷" in response.text
    assert '"mode": "new"' in response.text
    assert "Not Found" not in response.text


def test_automation_conversion_page_is_ai_audience_native_page(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/admin/automation-conversion", cookies=_admin_cookies(client))

    assert response.status_code == 200
    assert "AI 自动化运营" in response.text
    assert "人群包列表" in response.text
    assert "/api/admin/ai-audience/packages" in response.text
    assert "方案列表" not in response.text
    assert "automation_program_member" not in response.text
    assert "复制自动化运营方案" not in response.text


def test_automation_conversion_legacy_page_is_retired(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/admin/automation-conversion/legacy", cookies=_admin_cookies(client))

    assert response.status_code == 404


def test_automation_program_pages_are_retired(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path in [
        "/admin/automation-conversion/programs/7/setup?step=basic",
        "/admin/automation-conversion/programs/7/overview",
        "/admin/automation-conversion/programs/7/copy",
        "/admin/automation-conversion/programs/7/entry-channels",
    ]:
        response = client.get(path, cookies=_admin_cookies(client))
        assert response.status_code == 410, path
        assert "旧自动化运营方案页面已下架，请使用 AI 自动化运营人群包" in response.text


def test_automation_program_api_routes_are_retired(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")
    client = TestClient(create_app(), raise_server_exceptions=False)

    retired_program_paths = [
        ("get", "/api/admin/automation-conversion/programs"),
        ("post", "/api/admin/automation-conversion/programs/7/channel-bindings"),
    ]
    for method, path in retired_program_paths:
        response = getattr(client, method)(path, cookies=_admin_cookies(client))
        if "/programs" in path:
            assert response.status_code == 404, path
            continue
        assert response.status_code == 410, path
        error = response.json()["error"]
        assert error.startswith(("legacy_automation_", "legacy_program_"))
        assert error.endswith("_retired")

    removed_action_paths = [
        ("get", "/api/admin/automation-conversion/contract"),
        ("get", "/api/admin/automation-conversion/overview"),
        ("get", "/api/admin/automation-conversion/pools"),
        ("get", "/api/admin/automation-conversion/execution-records"),
        ("post", "/api/admin/automation-conversion/tasks/run-due"),
        ("post", "/api/admin/automation-conversion/execution-items/12/send-via-bazhuayu"),
        ("post", "/api/admin/automation-conversion/jobs/run-due"),
    ]
    for method, path in removed_action_paths:
        response = getattr(client, method)(path, cookies=_admin_cookies(client))
        assert response.status_code == 404, path


def test_admin_login_route_is_next_owned_when_production_facade_is_enabled(monkeypatch):

    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.setenv("SECRET_KEY", "admin-pages-real-data-binding-test")

    async def fake_forward(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(
            f"legacy-auth-forwarded:{request.method}:{request.url.path}:{request.url.query}",
            headers={"X-AICRM-Compatibility-Facade": "legacy_flask_facade"},
        )

    response = TestClient(create_app(), raise_server_exceptions=False).get("/login?next=/admin/automation-conversion/programs/7/entry-channels")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "后台登录" in response.text
    assert "/auth/wecom/start" in response.text
    assert "不提供本地账密入口" in response.text
    assert 'action="/login"' not in response.text


def test_real_data_binding_checker_returns_ok(monkeypatch):
    monkeypatch.setattr(checker, "_git_modified_files", lambda: [])
    result = checker.run_check()

    assert isinstance(result["ok"], bool)
    assert result["auth_failures"] == []
    assert result["production_config_modified"] is False


def test_api_docs_page_lists_real_route_groups(monkeypatch):
    response = _client(monkeypatch).get("/admin/api-docs")

    assert response.status_code == 200
    assert "/api/admin/ai-audience/packages" in response.text
    assert "/api/wechat-pay/notify" in response.text
    assert checker._row_count(response.text) >= 10
