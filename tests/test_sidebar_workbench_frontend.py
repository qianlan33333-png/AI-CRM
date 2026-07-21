from __future__ import annotations

from pathlib import Path


WORKBENCH_TEMPLATE = Path("aicrm_next/frontend_compat/templates/sidebar_customer_workbench.html")
WORKBENCH_JS = Path("aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js")
WORKBENCH_CSS = Path("aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.css")
WORKBENCH_PRODUCT_CARD_COVER = Path("aicrm_next/frontend_compat/static/sidebar_workbench/product-card-cover.png")


def test_sidebar_workbench_v2_page_is_next_owned(client):
    response = client.get("/sidebar/bind-mobile?external_userid=wm_frontend&owner_userid=sales_01")
    html = response.text

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "客户侧边栏 V2 工作台" in html
    assert 'data-workbench-url="/api/sidebar/v2/workbench"' in html
    assert 'data-material-send-url="/api/sidebar/v2/materials/send"' in html
    assert 'data-periodic-orders-url="/api/sidebar/v2/periodic-orders"' in html
    assert 'data-periodic-order-remark-url="/api/sidebar/v2/periodic-orders"' in html
    assert 'data-coupons-url="/api/sidebar/v2/coupons"' in html
    assert 'data-radar-links-url="/api/sidebar/v2/radar-links"' in html
    assert 'data-timeline-url="/api/sidebar/v2/timeline"' in html
    assert "sidebar_workbench/sidebar_workbench.js" in html
    assert "sidebar_workbench/sidebar_workbench.js?v=20260717-capability-optimization" in html
    assert "sidebar_workbench/sidebar_workbench.css?v=20260717-capability-optimization" in html
    assert "自动化转化操作区" not in html


def test_sidebar_workbench_static_contract_has_next_surface_only():
    template = WORKBENCH_TEMPLATE.read_text(encoding="utf-8")
    script = WORKBENCH_JS.read_text(encoding="utf-8")
    css = WORKBENCH_CSS.read_text(encoding="utf-8")
    combined = template + "\n" + script + "\n" + css

    assert """const tabs = [
    ["profile", "核心画像"],
    ["questionnaires", "问卷"],
    ["products", "商品"],
    ["orders", "订单"],
    ["coupons", "优惠券"],
    ["materials", "素材"],
    ["other_staff_messages", "其他客服聊天"],
  ];""" in script
    assert '["profile", "核心画像"]' in script
    assert '["questionnaires", "问卷"]' in script
    assert '["products", "商品"]' in script
    assert '["orders", "订单"]' in script
    assert '["coupons", "优惠券"]' in script
    assert '["periodic_orders", "周期订单"]' not in script
    assert '["regular", "普通订单"]' in script
    assert '["periodic", "周期订单"]' in script
    assert '["basic", "基础信息"]' in script
    assert '["timeline", "用户时间线"]' in script
    assert '["image", "图片素材"]' in script
    assert '["radar", "雷达链接"]' in script
    assert '["regular", "普通商品"]' in script
    assert '["service_period", "周期性商品"]' in script
    assert 'invokeWeCom("getCurExternalContact"' in script
    assert "sendChatMessage" in script
    assert "PRODUCT_CARD_IMAGE_PATH" in script
    assert "product-card-cover.png" in script
    assert "material-thumb" in script
    assert "skeleton-list" in script
    assert "requestPanelJson" in script
    assert "PANEL_TIMEOUT_MS" in script
    assert "PANEL_CACHE_TTL_MS" in script
    assert "panelRequests: new Map()" in script
    assert 'cache: "no-store"' in script
    assert "sidebar_owner_token" in script
    assert "data-material-thumb-img" in script
    assert "service_period_products" in script
    assert "data-product-type" in script
    assert "data-product-kind" in script
    assert "product-seg" in css
    assert 'payload.service_period_products || []' in script
    assert 'await sendProduct(productSendButton.dataset.productSend, productSendButton.dataset.productKind || state.productType)' in script
    assert "data-order-detail-url" in script
    assert "renderPeriodicOrders" in script
    assert "data-periodic-order-remark" in script
    assert "savePeriodicOrderRemarkSoon" in script
    periodic_renderer = script[script.index("function periodicOrderCards()") : script.index("function renderPeriodicOrders()")]
    for label in ("剩余有效期", "正式登录", "token 消耗", "学习计划进度", "近 7 天打开次数", "最后打开时间"):
        assert label in periodic_renderer
    assert '<span>状态</span>' not in periodic_renderer
    assert '<span>到期时间</span>' not in periodic_renderer
    assert 'const url = normalized === "periodic" ? endpoint("periodicOrdersUrl") : endpoint("ordersUrl")' in script
    assert "requestPanelJson(panelKey, queryUrl(url, customerContextQuery()))" in script
    assert 'queryUrl(endpoint("periodicOrderRemarkUrl") + "/" + encodeURIComponent(id) + "/remark", customerContextQuery())' in script
    assert 'data-periodic-orders-url="/api/sidebar/v2/periodic-orders"' in template
    assert 'data-periodic-order-remark-url="/api/sidebar/v2/periodic-orders"' in template
    assert 'data-coupons-url="/api/sidebar/v2/coupons"' in template
    assert 'data-radar-links-url="/api/sidebar/v2/radar-links"' in template
    assert 'data-timeline-url="/api/sidebar/v2/timeline"' in template
    assert "暂无小程序素材" not in script
    assert "暂无 PDF 素材" not in script
    assert 'type: "mini"' not in script
    assert 'type: "pdf"' not in script
    assert "renderCoupons" in script
    assert "renderRadarLinks" in script
    assert '>复制链接</button></article>' in script
    assert "renderProfileTimeline" in script
    assert "fallbackCopyText" in script
    assert "periodic-remark-textarea" in css
    assert "grid-template-columns: minmax(0, 1fr) auto" in css
    assert "@keyframes sidebar-skeleton" in css
    assert WORKBENCH_PRODUCT_CARD_COVER.exists()
    assert "context_token" not in script
    assert 'prefetchTabs(["questionnaires", "orders", "periodic_orders"])' not in script
    assert "function prefetchTabs" not in script
    panel_request = script[script.index("async function requestPanelJson") : script.index("function absoluteUrl")]
    assert "state.panelRequests.get(key)" in panel_request
    assert "state.panelRequests.set(key, request)" in panel_request
    assert "state.panelRequests.delete(key)" in panel_request
    assert "retryCount: 0" in panel_request
    switch_tab = script[script.index("async function switchTab") : script.index("async function sendMaterial")]
    assert "function isWorkbenchReady" in script
    assert 'key !== "profile" && !isWorkbenchReady()' in script
    assert 'if (tab !== "profile" && !isWorkbenchReady()) return;' in switch_tab
    assert "state.activeTab !== tab" in switch_tab
    assert 'tab === "materials" && state.materialType !== materialType' in switch_tab
    assert 'tab === "orders" && state.orderType !== orderType' in switch_tab
    assert 'tab === "profile" && state.profileView !== profileView' in switch_tab
    assert "data-retry-tab" in switch_tab
    assert 'event.target.closest("[data-retry-tab]")' in script
    material_switch = script[script.index("async function switchMaterialType") : script.index("function renderActiveTab")]
    assert 'state.activeTab !== "materials"' in material_switch
    assert "state.materialType !== type" in material_switch
    assert "data-retry-material-type" in script
    assert 'event.target.closest("[data-retry-material-type]")' in script
    assert "data-retry-order-type" in script
    assert "data-refresh-timeline" in script
    assert "data-load-more-timeline" in script
    assert 'requestPanelJson("coupons", endpoint("couponsUrl"))' in script
    assert 'requestPanelJson("radar_links", endpoint("radarLinksUrl"))' in script
    assert 'queryUrl(endpoint("timelineUrl"), { limit: 20, offset })' in script
    assert "customer-avatar" not in combined
    assert "复制商品链接" not in combined
    assert "待确认员工身份" not in combined
    assert "demo" not in combined.lower()


def test_sidebar_workbench_query_context_skips_wecom_sdk_path():
    script = WORKBENCH_JS.read_text(encoding="utf-8")

    boot = script[script.index("async function boot()") : script.index('tabsNode.addEventListener("click"')]
    assert boot.index("setWorkbenchState(WORKBENCH_STATES.identifying_customer);") < boot.index("renderTabs();")
    assert "const hasQuery = await resolveContextFromQuery();" in script
    assert "await resolveContextFromWeCom();" in script


def test_sidebar_workbench_questionnaire_requests_reuse_owner_scoped_context_query():
    script = WORKBENCH_JS.read_text(encoding="utf-8")

    assert 'queryUrl(endpoint("questionnairesUrl"), customerContextQuery())' in script
    assert 'queryUrl(endpoint("questionnairesUrl"), { external_userid: state.external_userid })' not in script
