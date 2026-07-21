from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


TEMPLATE = Path("aicrm_next/frontend_compat/templates/sidebar_customer_workbench.html")
SCRIPT = Path("aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js")


def test_frontend_declares_and_consumes_jssdk_config_contract() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'data-jssdk-config-url="/api/sidebar/jssdk-config"' in template
    assert "sidebar_workbench.js?v=20260717-capability-optimization" in template
    assert "jssdkConfigUrl()" in script
    assert "jssdkConfigRequests: new Map()" in script
    assert "jssdkConfigCache: new Map()" in script
    assert "JSSDK_CONFIG_CACHE_SAFETY_MS" in script
    assert "JSSDK_CONFIG_CACHE_MAX_TTL_MS" in script
    config_request = script[script.index("function jssdkConfigCacheTtlMs") : script.index("async function refreshSidebarOwnerToken")]
    assert "sidebar_owner_context" in config_request
    assert "expires_in" in config_request
    assert "Math.min(JSSDK_CONFIG_CACHE_MAX_TTL_MS" in config_request
    assert "expiresInMs - JSSDK_CONFIG_CACHE_SAFETY_MS" in config_request
    assert "cached.expiresAt <= Date.now()" in config_request
    assert "const url = jssdkConfigUrl();" in config_request
    assert "state.jssdkConfigRequests.get(url)" in config_request
    assert "state.jssdkConfigRequests.set(url, request)" in config_request
    assert "state.jssdkConfigRequests.delete(url)" in config_request
    assert "if (ttlMs <= 0) return;" in config_request
    assert "expiresAt: Date.now() + ttlMs" in config_request
    assert "requestJson(jssdkConfigUrl()" not in script
    assert 'url.searchParams.set("external_userid", state.external_userid)' in script
    assert "applySidebarOwnerToken(configPayload)" in script
    assert "extractWeComViewerUserid" in script
    assert "applyWeComViewerIdentity" in script
    assert "maybeStartSidebarOAuth" in script
    assert '"X-AICRM-Sidebar-Owner-Token": state.sidebar_owner_token' in script
    assert "configPayload.corp_id" in script
    assert "configPayload.agent_id" in script
    assert "configPayload.config.timestamp" in script
    assert "configPayload.config.nonceStr" in script
    assert "configPayload.config.signature" in script
    assert "configPayload.agent_config.signature" in script
    assert "sendChatMessage" in script


def test_frontend_refreshes_owner_token_after_external_userid_resolution() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "if (!state.owner_userid && !state.external_userid) return false;" in script
    assert "await refreshSidebarOwnerToken();" in script
    assert "state.sidebar_oauth_url" in script
    assert 'target.searchParams.set("next", currentSidebarNextPath())' in script
    assert 'window.location.assign(target.toString())' in script
    assert "if (!state.sidebar_owner_token) {" in script
    assert 'firstQueryValue(["owner_userid"' not in script
    assert 'firstQueryValue(["sidebar_owner_token"' not in script
    assert 'url.searchParams.set("viewer_userid"' not in script
    assert 'url.searchParams.set("bind_by_userid"' not in script
    assert 'applyWeComViewerIdentity(res || {}, "agentConfig", { allowUserId: true });' in script
    assert 'const contextPayload = await invokeWeCom("getContext", {}, SDK_TIMEOUT_MS);' in script
    assert 'applyWeComViewerIdentity(contextPayload || {}, "getContext", { allowUserId: true });' in script
    assert 'const externalUserid = extractWeComExternalUserid(res || {});' in script
    assert 'applyWeComViewerIdentity(res || {}, "getCurExternalContact");' in script
    assert "if (hasQuery && !state.sidebar_owner_token && !state.owner_userid)" in script
    assert 'await maybeStartSidebarOAuth("owner_token_missing")' in script
    assert "renderOwnerPendingWorkbench(ownerPendingMessage())" not in script
    assert "await loadWorkbench();" in script


def test_sidebar_page_and_jssdk_api_contract_are_compatible(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "sidebar-jssdk-frontend")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    page = client.get("/sidebar/bind-mobile")
    api = client.get("/api/sidebar/jssdk-config", params={"url": "http://127.0.0.1:5001/sidebar/bind-mobile"})
    payload = api.json()

    assert page.status_code == 200
    assert "客户侧边栏 V2 工作台" in page.text
    assert "/api/sidebar/jssdk-config" in page.text
    assert api.status_code == 200
    assert payload["corp_id"] == payload["appId"] == payload["corpId"]
    assert payload["agent_id"] == payload["agentId"]
    assert payload["config"]["timestamp"] == payload["timestamp"]
    assert payload["config"]["nonceStr"] == payload["nonceStr"]
    assert payload["config"]["signature"] == payload["signature"]
    assert payload["agent_config"]["signature"]
