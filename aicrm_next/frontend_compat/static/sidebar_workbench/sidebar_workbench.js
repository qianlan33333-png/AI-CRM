(function () {
  const root = document.getElementById("sidebar-workbench-root");
  const content = document.getElementById("content");
  const tabsNode = document.getElementById("tabs");
  const toastNode = document.getElementById("toast");
  const debugWrap = document.getElementById("debug-wrap");
  const mobileModal = document.getElementById("mobile-modal");
  const mobileInput = document.getElementById("mobile-input");
  const mobileStatus = document.getElementById("mobile-status");
  const confirmMobileButton = document.getElementById("confirm-mobile-button");

  const tabs = [
    ["profile", "核心画像"],
    ["questionnaires", "问卷"],
    ["products", "商品"],
    ["orders", "订单"],
    ["coupons", "优惠券"],
    ["materials", "素材"],
    ["other_staff_messages", "其他客服聊天"],
  ];
  const materialTabs = [
    ["image", "图片素材"],
    ["radar", "雷达链接"],
  ];
  const productTabs = [
    ["regular", "普通商品"],
    ["service_period", "周期性商品"],
  ];
  const orderTabs = [
    ["regular", "普通订单"],
    ["periodic", "周期订单"],
  ];
  const profileTabs = [
    ["basic", "基础信息"],
    ["timeline", "用户时间线"],
  ];
  const WORKBENCH_STATES = {
    identifying_customer: "identifying_customer",
    sdk_unavailable: "sdk_unavailable",
    context_missing: "context_missing",
    loading_workbench: "loading_workbench",
    ready: "ready",
    degraded_ready: "degraded_ready",
    error: "error",
  };
  const DEFAULT_TIMEOUT_MS = 8000;
  const SDK_TIMEOUT_MS = 5000;
  const PANEL_TIMEOUT_MS = {
    workbench: 6500,
    questionnaires: 9000,
    products: 9000,
    orders: 12000,
    periodic_orders: 12000,
    coupons: 9000,
    materials: 10000,
    radar_links: 10000,
    timeline: 10000,
    other_staff_messages: 9000,
  };
  const PANEL_CACHE_TTL_MS = 2 * 60 * 1000;
  const JSSDK_CONFIG_CACHE_SAFETY_MS = 30 * 1000;
  const JSSDK_CONFIG_CACHE_MAX_TTL_MS = 5 * 60 * 1000;
  const PRODUCT_CARD_IMAGE_PATH = "/static/sidebar_workbench/product-card-cover.png";

  const state = {
    status: WORKBENCH_STATES.identifying_customer,
    external_userid: "",
    owner_userid: "",
    bind_by_userid: "",
    sidebar_owner_token: "",
    sidebar_owner_token_status: "",
    sidebar_oauth_url: "",
    sidebar_oauth_started: false,
    activeTab: "profile",
    profileView: "basic",
    materialType: "image",
    productType: "regular",
    orderType: "regular",
    workbench: null,
    loaded: {},
    data: {
      questionnaires: null,
      products: null,
      service_period_products: null,
      orders: null,
      periodic_orders: null,
      coupons: null,
      materials: {},
      radar_links: null,
      timeline: {
        items: [],
        total: 0,
        has_more: false,
        next_offset: 0,
      },
      other_staff_messages: null,
    },
    profileSaveTimer: null,
    periodicRemarkTimers: {},
    toastTimer: null,
    lastError: null,
    panelCache: {},
    panelRequests: new Map(),
    jssdkConfigRequests: new Map(),
    jssdkConfigCache: new Map(),
    timelineRequestVersion: 0,
  };

  const debugEnabled = root && root.dataset.debugEnabled === "true";
  debugWrap.classList.toggle("hidden", !debugEnabled);

  function endpoint(name) {
    return root.dataset[name] || "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function huangyoucanMatched(item) {
    return ["matched_unionid", "matched_mobile"].indexOf(String((item && item.huangyoucan_match_status) || "")) >= 0;
  }

  function huangyoucanBoolean(item, key, truthy, falsy) {
    return huangyoucanMatched(item) ? (item[key] ? truthy : falsy) : "—";
  }

  function huangyoucanProgress(item) {
    if (!huangyoucanMatched(item)) return "—";
    const progress = item.huangyoucan_learning_plan_progress;
    return progress ? String(Number(progress.current || 0)) + "/" + String(Number(progress.total || 0)) : "无";
  }

  function huangyoucanLastOpen(item) {
    if (!huangyoucanMatched(item)) return "—";
    const value = item.huangyoucan_last_open_at;
    return value ? String(value).replace("T", " ").slice(0, 16) : "无";
  }

  function safeJsonParse(text) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  }

  function writeDebug(label, payload) {
    if (!debugEnabled) return;
    const line = "[" + new Date().toISOString() + "] " + label + (payload === undefined ? "" : " " + JSON.stringify(payload));
    const item = document.createElement("pre");
    item.textContent = line;
    debugWrap.appendChild(item);
  }

  function customerContextQuery() {
    return {
      external_userid: state.external_userid,
      owner_userid: state.owner_userid,
      bind_by_userid: state.bind_by_userid || state.owner_userid,
    };
  }

  function productContextDiagnostics(payload) {
    const products = (payload && payload.products) || [];
    return {
      context_source: payload && payload.diagnostics ? payload.diagnostics.context_source : "",
      context_status: payload && payload.diagnostics ? payload.diagnostics.context_status : "",
      product_url_has_context: products.some((item) => {
        try {
          return new URL(item.product_url || "", window.location.origin).hash.indexOf("aicrm_ctx=") >= 0;
        } catch (_error) {
          return String(item.product_url || "").indexOf("#aicrm_ctx=") !== -1;
        }
      }),
    };
  }

  function applySidebarOwnerToken(payload) {
    const token = String((payload && payload.sidebar_owner_token) || "").trim();
    if (payload && Object.prototype.hasOwnProperty.call(payload, "sidebar_owner_token")) state.sidebar_owner_token = token;
    state.sidebar_owner_token_status = String((payload && payload.sidebar_owner_token_status) || state.sidebar_owner_token_status || "").trim();
    if (payload && Object.prototype.hasOwnProperty.call(payload, "sidebar_oauth_url")) {
      state.sidebar_oauth_url = String(payload.sidebar_oauth_url || "").trim();
    }
    const context = (payload && payload.sidebar_owner_context) || {};
    const owner = String(context.owner_userid || context.viewer_userid || "").trim();
    if (owner) state.owner_userid = owner;
    const bindBy = String(context.bind_by_userid || "").trim();
    if (bindBy) state.bind_by_userid = bindBy;
    if (token && state.external_userid && window.sessionStorage) {
      try {
        window.sessionStorage.removeItem(sidebarOAuthAttemptKey());
      } catch (_error) {
        // Ignore storage cleanup failures; the fresh owner token is the source of truth.
      }
    }
  }

  function firstPayloadValue(payload, keys) {
    const queue = [payload];
    const seen = [];
    while (queue.length) {
      const current = queue.shift();
      if (!current || typeof current !== "object" || seen.indexOf(current) >= 0) continue;
      seen.push(current);
      for (const key of keys || []) {
        const value = String(current[key] || "").trim();
        if (value) return value;
      }
      ["data", "context", "user", "member", "currentUser", "current_user"].forEach((key) => {
        if (current[key] && typeof current[key] === "object") queue.push(current[key]);
      });
    }
    return "";
  }

  function extractWeComViewerUserid(payload, options) {
    const keys = [
      "viewer_userid",
      "viewerUserid",
      "viewerUserId",
      "operator_userid",
      "operatorUserid",
      "operatorUserId",
      "owner_userid",
      "ownerUserid",
      "ownerUserId",
      "current_userid",
      "currentUserid",
      "currentUserId",
      "userid",
      "user_id",
      "UserId",
    ];
    if (options && options.allowUserId) keys.push("userId");
    return firstPayloadValue(payload, keys);
  }

  function extractWeComExternalUserid(payload) {
    return firstPayloadValue(payload, [
      "external_userid",
      "externalUserid",
      "external_userId",
      "externalUserId",
      "external_user_id",
      "externalUserID",
      "userId",
      "user_id",
    ]);
  }

  function applyWeComViewerIdentity(payload, source, options) {
    const viewer = extractWeComViewerUserid(payload, options || {});
    if (!viewer) return false;
    const previousOwner = state.owner_userid;
    state.owner_userid = viewer;
    if (!state.bind_by_userid || state.bind_by_userid === previousOwner) state.bind_by_userid = viewer;
    writeDebug("viewer identity resolved", { source: source || "", owner_userid: state.owner_userid, bind_by_userid: state.bind_by_userid });
    return true;
  }

  function jssdkConfigUrl() {
    const currentUrl = window.location.href.split("#")[0];
    const url = new URL(endpoint("jssdkConfigUrl"), window.location.origin);
    url.searchParams.set("url", currentUrl);
    if (state.external_userid) url.searchParams.set("external_userid", state.external_userid);
    return url.toString();
  }

  function jssdkConfigCacheTtlMs(payload) {
    const context = (payload && payload.sidebar_owner_context) || {};
    const expiresInMs = Number(context.expires_in) * 1000;
    if (!Number.isFinite(expiresInMs) || expiresInMs <= JSSDK_CONFIG_CACHE_SAFETY_MS) return 0;
    return Math.min(JSSDK_CONFIG_CACHE_MAX_TTL_MS, expiresInMs - JSSDK_CONFIG_CACHE_SAFETY_MS);
  }

  function readJssdkConfigCache(url) {
    const cached = state.jssdkConfigCache.get(url);
    if (!cached) return null;
    if (cached.expiresAt <= Date.now()) {
      state.jssdkConfigCache.delete(url);
      return null;
    }
    return cached.payload;
  }

  function writeJssdkConfigCache(url, payload) {
    const ttlMs = jssdkConfigCacheTtlMs(payload);
    if (ttlMs <= 0) return;
    state.jssdkConfigCache.set(url, {
      payload,
      expiresAt: Date.now() + ttlMs,
    });
  }

  async function requestJssdkConfig() {
    const url = jssdkConfigUrl();
    const cached = readJssdkConfigCache(url);
    if (cached) return cached;
    const pending = state.jssdkConfigRequests.get(url);
    if (pending) return pending;
    const request = requestJson(url, { timeoutMs: SDK_TIMEOUT_MS, retryCount: 1, retryDelayMs: 300 })
      .then((payload) => {
        writeJssdkConfigCache(url, payload);
        return payload;
      })
      .finally(() => state.jssdkConfigRequests.delete(url));
    state.jssdkConfigRequests.set(url, request);
    return request;
  }

  async function refreshSidebarOwnerToken() {
    if (!state.owner_userid && !state.external_userid) return false;
    try {
      const payload = await requestJssdkConfig();
      applySidebarOwnerToken(payload);
      writeDebug("sidebar owner token refreshed", { status: state.sidebar_owner_token_status, has_token: Boolean(state.sidebar_owner_token) });
      return Boolean(state.sidebar_owner_token);
    } catch (error) {
      writeDebug("sidebar owner token refresh failed", { message: error.message || String(error) });
      return false;
    }
  }

  function sidebarOAuthAttemptKey() {
    return "aicrm_sidebar_oauth:" + String(state.external_userid || "unknown");
  }

  function currentSidebarNextPath() {
    const params = new URLSearchParams(window.location.search);
    params.delete("sidebar_oauth_error");
    const query = params.toString();
    return window.location.pathname + (query ? "?" + query : "");
  }

  async function maybeStartSidebarOAuth(reason) {
    if (state.sidebar_owner_token || state.sidebar_oauth_started || !state.external_userid) return false;
    const params = new URLSearchParams(window.location.search);
    const oauthError = String(params.get("sidebar_oauth_error") || "").trim();
    if (oauthError) {
      writeDebug("sidebar oauth skipped after callback error", { error: oauthError, reason: reason || "" });
      return false;
    }
    if (!state.sidebar_oauth_url) {
      await refreshSidebarOwnerToken();
    }
    if (!state.sidebar_oauth_url) {
      writeDebug("sidebar oauth unavailable", { reason: reason || "", owner_token_status: state.sidebar_owner_token_status });
      return false;
    }
    if (window.sessionStorage) {
      try {
        const key = sidebarOAuthAttemptKey();
        if (window.sessionStorage.getItem(key) === "1") {
          writeDebug("sidebar oauth skipped after prior attempt", { reason: reason || "" });
          return false;
        }
        window.sessionStorage.setItem(key, "1");
      } catch (_error) {
        // Best-effort loop guard; OAuth can proceed when storage is unavailable.
      }
    }
    const target = new URL(state.sidebar_oauth_url, window.location.origin);
    target.searchParams.set("external_userid", state.external_userid);
    target.searchParams.set("next", currentSidebarNextPath());
    state.sidebar_oauth_started = true;
    writeDebug("sidebar oauth start", { reason: reason || "", target: target.pathname });
    window.location.assign(target.toString());
    return true;
  }

  function showToast(message, tone) {
    window.clearTimeout(state.toastTimer);
    toastNode.textContent = message || "";
    toastNode.className = "toast" + (tone === "error" ? " error" : "");
    toastNode.classList.remove("hidden");
    state.toastTimer = window.setTimeout(() => toastNode.classList.add("hidden"), 1900);
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function shouldRetryRequest(error) {
    if (!error) return false;
    if (error.stage === "request_timeout") return true;
    if (!error.status) return true;
    return error.status >= 500;
  }

  async function requestJson(url, options) {
    const retryCount = Math.max(0, Number((options && options.retryCount) || 0));
    const retryDelayMs = Math.max(0, Number((options && options.retryDelayMs) || 320));
    let lastError = null;
    for (let attempt = 0; attempt <= retryCount; attempt += 1) {
      try {
        return await requestJsonOnce(url, options || {});
      } catch (error) {
        lastError = error;
        if (attempt >= retryCount || !shouldRetryRequest(error)) break;
        await sleep(retryDelayMs * (attempt + 1));
      }
    }
    throw lastError;
  }

  async function requestJsonOnce(url, options) {
    const timeoutMs = Number((options && options.timeoutMs) || DEFAULT_TIMEOUT_MS);
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : null;
    const finalOptions = {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(options && options.body ? { "Content-Type": "application/json" } : {}),
        ...(state.sidebar_owner_token ? { "X-AICRM-Sidebar-Owner-Token": state.sidebar_owner_token } : {}),
        ...((options && options.headers) || {}),
      },
      ...(options || {}),
      ...(controller ? { signal: controller.signal } : {}),
    };
    delete finalOptions.timeoutMs;
    delete finalOptions.retryCount;
    delete finalOptions.retryDelayMs;
    try {
      const response = await fetch(url, finalOptions);
      const text = await response.text();
      const payload = text ? safeJsonParse(text) : null;
      if (!response.ok || (payload && payload.ok === false)) {
        const error = new Error((payload && payload.error) || "请求失败");
        error.status = response.status;
        error.payload = payload || {};
        throw error;
      }
      return payload || { ok: true };
    } catch (error) {
      if (error && error.name === "AbortError") {
        const timeoutError = new Error("请求超时，请重试");
        timeoutError.stage = "request_timeout";
        throw timeoutError;
      }
      throw error;
    } finally {
      if (timer) window.clearTimeout(timer);
    }
  }

  function queryUrl(baseUrl, params) {
    const url = new URL(baseUrl, window.location.origin);
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && String(value).trim() !== "") {
        url.searchParams.set(key, String(value).trim());
      }
    });
    return url.toString();
  }

  function panelCacheKey(tab, url) {
    return [state.external_userid || "", tab || "", url || ""].join("::");
  }

  function readPanelCache(tab, url) {
    const item = state.panelCache[panelCacheKey(tab, url)];
    if (!item || item.expiresAt < Date.now()) return null;
    return item.payload;
  }

  function writePanelCache(tab, url, payload) {
    state.panelCache[panelCacheKey(tab, url)] = {
      payload,
      expiresAt: Date.now() + PANEL_CACHE_TTL_MS,
    };
  }

  function clearPanelCache(tab) {
    Object.keys(state.panelCache || {}).forEach((key) => {
      if (key.indexOf("::" + tab + "::") >= 0) delete state.panelCache[key];
    });
  }

  async function requestPanelJson(tab, url, options) {
    const cached = readPanelCache(tab, url);
    if (cached) return cached;
    const key = panelCacheKey(tab, url);
    const pending = state.panelRequests.get(key);
    if (pending) return pending;
    const request = requestJson(url, {
      timeoutMs: PANEL_TIMEOUT_MS[tab] || DEFAULT_TIMEOUT_MS,
      retryCount: 0,
      ...(options || {}),
    })
      .then((payload) => {
        writePanelCache(tab, url, payload);
        return payload;
      })
      .finally(() => state.panelRequests.delete(key));
    state.panelRequests.set(key, request);
    return request;
  }

  function absoluteUrl(path) {
    const text = String(path || "").trim();
    if (!text) return "";
    try {
      return new URL(text, window.location.origin).toString();
    } catch (_error) {
      return text;
    }
  }

  function getQueryValue(key) {
    return new URLSearchParams(window.location.search).get(key) || "";
  }

  function firstQueryValue(keys) {
    for (const key of keys || []) {
      const value = getQueryValue(key).trim();
      if (value) return value;
    }
    return "";
  }

  function setPanelLoading(title) {
    content.innerHTML = panel(
      title || "",
      '<div class="skeleton-list" aria-busy="true">' +
        '<div class="skeleton-line strong"></div>' +
        '<div class="skeleton-line"></div>' +
        '<div class="skeleton-line short"></div>' +
      "</div>"
    );
  }

  function stateLabel(status) {
    const labels = {
      identifying_customer: "识别中",
      sdk_unavailable: "未识别到客户",
      context_missing: "未识别到客户",
      loading_workbench: "加载中",
      ready: "",
      degraded_ready: "部分加载",
      error: "加载失败",
    };
    return labels[status] || "加载失败";
  }

  function setWorkbenchState(status, detail) {
    state.status = status;
    state.lastError = detail || null;
    writeDebug("state transition", { status, detail: detail || {} });
    if (status !== WORKBENCH_STATES.ready && status !== WORKBENCH_STATES.degraded_ready) {
      renderTopState(status, detail || {});
    }
  }

  function renderTopState(status, detail) {
    const isLoading = status === WORKBENCH_STATES.identifying_customer || status === WORKBENCH_STATES.loading_workbench;
    document.getElementById("customer-name").textContent = stateLabel(status);
    document.getElementById("customer-mobile").textContent = "";
    document.getElementById("customer-external-userid").textContent = state.external_userid ? "外部联系人 ID " + state.external_userid : "";
    document.getElementById("workflow-title").textContent = "";
    const bindingState = document.getElementById("binding-state");
    bindingState.textContent = isLoading ? (status === WORKBENCH_STATES.identifying_customer ? "识别中" : "加载中...") : stateLabel(status);
    bindingState.classList.remove("hidden");
    bindingState.classList.toggle("loading", isLoading);
    bindingState.classList.toggle("unbound", !isLoading);
    if (detail && detail.message) writeDebug("top state detail", detail);
  }

  function renderRetryPanel(title, message) {
    content.innerHTML = panel(
      title || "",
      '<div class="status error">' + escapeHtml(message || "加载失败，请稍后重试。") + "</div>" +
        '<div class="row-actions"><button class="btn primary" type="button" data-retry-boot>重试</button></div>'
    );
  }

  function panel(title, body) {
    const head = title ? '<div class="head"><h2>' + escapeHtml(title) + "</h2></div>" : "";
    return '<section class="panel">' + head + body + "</section>";
  }

  function empty(message) {
    return '<div class="empty">' + escapeHtml(message) + "</div>";
  }

  function isWorkbenchReady() {
    return Boolean(state.workbench) && (state.status === WORKBENCH_STATES.ready || state.status === WORKBENCH_STATES.degraded_ready);
  }

  function renderTabs() {
    tabsNode.innerHTML = tabs
      .map(([key, label]) => {
        const active = key === state.activeTab ? " active" : "";
        const disabled = key !== "profile" && !isWorkbenchReady() ? ' disabled aria-disabled="true"' : "";
        return '<button class="tab' + active + '" type="button" data-tab="' + key + '"' + disabled + ">" + escapeHtml(label) + "</button>";
      })
      .join("");
  }

  function renderTop() {
    const workbench = state.workbench || {};
    const customer = workbench.customer || {};
    const workflow = workbench.workflow || {};
    const name = String(customer.display_name || "当前客户").trim();
    const mobile = String(customer.mobile || "").trim();
    const externalUserid = String(customer.external_userid || state.external_userid || "").trim();
    const isBound = customer.mobile_bound !== undefined ? Boolean(customer.mobile_bound) : Boolean(customer.is_bound && mobile);
    document.getElementById("customer-name").textContent = name;
    document.getElementById("customer-mobile").textContent = mobile ? "手机号 " + mobile : "";
    document.getElementById("customer-external-userid").textContent = externalUserid ? "外部联系人 ID " + externalUserid : "";
    document.getElementById("workflow-title").textContent = String(workflow.title || "").trim();
    const bindingState = document.getElementById("binding-state");
    const hideBindingState = Boolean(customer.owner_pending);
    bindingState.textContent = hideBindingState ? "" : (isBound ? "手机号已绑定" : "手机号未绑定");
    bindingState.classList.toggle("hidden", hideBindingState);
    bindingState.classList.remove("loading");
    bindingState.classList.toggle("unbound", !isBound);
    const changeButton = document.getElementById("change-mobile-button");
    if (changeButton) changeButton.disabled = Boolean(customer.owner_pending);
  }

  function updateProfileField(key, value) {
    const profile = state.workbench.profile || {};
    profile[key] = value;
    state.workbench.profile = profile;
  }

  function saveProfileSoon() {
    window.clearTimeout(state.profileSaveTimer);
    state.profileSaveTimer = window.setTimeout(saveProfile, 520);
  }

  async function saveProfile() {
    if (!state.workbench || !state.external_userid) return;
    const profile = state.workbench.profile || {};
    try {
      await requestJson(endpoint("profileUrl"), {
        method: "PUT",
        body: JSON.stringify({
          external_userid: state.external_userid,
          source: profile.source || "",
          industry: profile.industry || "",
          industry_description: profile.industry_description || "",
          needs_blockers_followup: profile.needs_blockers_followup || "",
          updated_by: state.bind_by_userid || state.owner_userid || "",
        }),
      });
      showToast("已保存");
    } catch (error) {
      showToast(error.message || "保存失败", "error");
    }
  }

  function updatePeriodicOrderRemark(id, value) {
    const rows = state.data.periodic_orders || [];
    const item = rows.find((entry) => String(entry.id || "") === String(id || ""));
    if (item) item.remark = value;
  }

  function savePeriodicOrderRemarkSoon(id) {
    window.clearTimeout(state.periodicRemarkTimers[id]);
    state.periodicRemarkTimers[id] = window.setTimeout(() => savePeriodicOrderRemark(id), 520);
  }

  async function savePeriodicOrderRemark(id) {
    const rows = state.data.periodic_orders || [];
    const item = rows.find((entry) => String(entry.id || "") === String(id || ""));
    if (!item || !state.external_userid) return;
    try {
      const remarkUrl = queryUrl(endpoint("periodicOrderRemarkUrl") + "/" + encodeURIComponent(id) + "/remark", customerContextQuery());
      const payload = await requestJson(remarkUrl, {
        method: "PUT",
        body: JSON.stringify({
          external_userid: state.external_userid,
          remark: item.remark || "",
        }),
      });
      const updated = payload.periodic_order || {};
      if (Object.prototype.hasOwnProperty.call(updated, "remark")) item.remark = updated.remark || "";
      clearPanelCache("periodic_orders");
      showToast("备注已保存");
    } catch (error) {
      showToast(error.message || "备注保存失败", "error");
    }
  }

  function textAreaField(key, label, value) {
    return (
      '<div class="field">' +
      '<div class="field-title">' + escapeHtml(label) + "</div>" +
      '<textarea class="textarea" data-profile-field="' + key + '">' + escapeHtml(value || "") + "</textarea>" +
      "</div>"
    );
  }

  function segmentedControls(items, activeKey, dataAttribute, extraClass) {
    return '<div class="seg two-column-seg ' + escapeHtml(extraClass || "") + '">' + items.map(([key, label]) => (
      '<button type="button" class="' + (key === activeKey ? "active" : "") + '" ' + dataAttribute + '="' + escapeHtml(key) + '">' + escapeHtml(label) + "</button>"
    )).join("") + "</div>";
  }

  function profileTypeControls() {
    return segmentedControls(profileTabs, state.profileView, "data-profile-view", "profile-seg");
  }

  function renderProfile() {
    if (state.profileView === "timeline") {
      renderProfileTimeline();
      return;
    }
    const profile = (state.workbench && state.workbench.profile) || {};
    content.innerHTML = panel(
      "",
      profileTypeControls() + '<div class="editor">' +
        textAreaField("source", "用户来源", profile.source || "") +
        textAreaField("industry", "行业信息", profile.industry || "") +
        textAreaField("industry_description", "行业具体描述", profile.industry_description || "") +
        textAreaField("needs_blockers_followup", "需求、卡点、跟进状态", profile.needs_blockers_followup || "") +
      "</div>"
    );
  }

  function renderProfileTimeline() {
    const timeline = state.data.timeline || {};
    const rows = timeline.items || [];
    const actions = '<div class="timeline-toolbar"><span class="mini">最新动态在前</span><button class="btn ghost" type="button" data-refresh-timeline>刷新</button></div>';
    const body = rows.length
      ? '<div class="customer-timeline">' + rows.map((item) => (
          '<article class="timeline-event"><div class="timeline-marker" aria-hidden="true"></div><div class="timeline-event-main">' +
          '<div class="timeline-event-head"><h3>' + escapeHtml(item.title || "用户动态") + '</h3><time>' + escapeHtml(item.event_time || "") + "</time></div>" +
          (item.summary ? '<div class="timeline-summary">' + escapeHtml(item.summary) + "</div>" : "") +
          '</div></article>'
        )).join("") + "</div>" +
        (timeline.has_more ? '<div class="row-actions timeline-more"><button class="btn ghost" type="button" data-load-more-timeline>加载更多</button></div>' : "")
      : empty("暂无用户时间线记录");
    content.innerHTML = panel("", profileTypeControls() + actions + body);
  }

  function renderQuestionnaires() {
    const rows = state.data.questionnaires || [];
    if (!rows.length) {
      content.innerHTML = panel("问卷", empty("暂无问卷记录"));
      return;
    }
    content.innerHTML = panel(
      "问卷",
      rows
        .map((item, index) => {
          const answers = item.answers || [];
          const count = String(item.answer_count || answers.length || 0) + "/" + String(item.total_count || item.answer_count || answers.length || 0) + " 题";
          return (
            '<article class="card" data-questionnaire-card="' + index + '">' +
            '<div class="card-title"><div><h3>' + escapeHtml(item.title || "未命名问卷") + "</h3>" +
            '<div class="mini">' + escapeHtml([item.submitted_at || "", count].filter(Boolean).join(" · ")) + "</div></div></div>" +
            '<div class="row-actions"><button class="btn primary" type="button" data-toggle-questionnaire="' + index + '">查看答案</button></div>' +
            '<div class="questions">' +
            answers.map((answer) => '<div class="question"><b>' + escapeHtml(answer.question || "未命名问题") + "</b><em>" + escapeHtml(answer.answer || "未填写") + "</em></div>").join("") +
            "</div></article>"
          );
        })
        .join("")
    );
  }

  function renderProducts() {
    const isServicePeriod = state.productType === "service_period";
    const rows = isServicePeriod ? state.data.service_period_products || [] : state.data.products || [];
    const controls = '<div class="seg product-seg">' + productTabs.map(([key, label]) => '<button type="button" class="' + (key === state.productType ? "active" : "") + '" data-product-type="' + key + '">' + escapeHtml(label) + "</button>").join("") + "</div>";
    if (!rows.length) {
      content.innerHTML = panel("商品", controls + empty(isServicePeriod ? "暂无周期性商品" : "暂无普通商品"));
      return;
    }
    content.innerHTML = panel(
      "商品",
      controls +
        rows
        .map((item, index) => {
          const meta = isServicePeriod && item.duration_days ? '<div class="mini">有效期 ' + escapeHtml(String(item.duration_days)) + " 天</div>" : "";
          return (
            '<article class="card"><div class="card-title"><h3>' + escapeHtml(item.title || "未命名商品") + "</h3>" +
            '<div class="price">' + escapeHtml(item.price_label || "") + "</div></div>" + meta +
            '<div class="row-actions"><button class="btn primary" type="button" data-product-send="' + escapeHtml(index) + '" data-product-kind="' + escapeHtml(state.productType) + '">发送商品</button></div></article>'
          );
        })
        .join("")
    );
  }

  function orderTypeControls() {
    return segmentedControls(orderTabs, state.orderType, "data-order-type", "order-seg");
  }

  function regularOrderCards() {
    const rows = state.data.orders || [];
    if (!rows.length) return empty("暂无普通订单");
    return rows
      .map((item) => (
        '<article class="card"><div class="card-title"><div><h3>' + escapeHtml(item.title || "未命名商品") + "</h3>" +
        '<div class="mini">' + escapeHtml(item.id || "") + '</div></div><div class="price">' + escapeHtml(item.amount_label || "") + "</div></div>" +
        '<div class="kv"><span>状态</span><strong>' + escapeHtml(item.status_label || "") + "</strong>" +
        '<span>时间</span><strong>' + escapeHtml(item.paid_at || "") + "</strong></div>" +
        '<div class="row-actions"><button class="btn primary" type="button" data-order-detail-url="' + escapeHtml(item.detail_url || "") + '">查看详情</button></div></article>'
      ))
      .join("");
  }

  function renderOrders() {
    const body = state.orderType === "periodic" ? periodicOrderCards() : regularOrderCards();
    content.innerHTML = panel("", orderTypeControls() + body);
  }

  function periodicOrderCards() {
    const rows = state.data.periodic_orders || [];
    if (!rows.length) return empty("暂无周期订单");
    return rows
        .map((item) => {
          const lastOrder = [item.last_out_trade_no || "", item.last_order_paid_at || ""].filter(Boolean).join(" · ");
          const detailAction = item.detail_url
            ? '<div class="row-actions"><button class="btn primary" type="button" data-order-detail-url="' + escapeHtml(item.detail_url || "") + '">查看详情</button></div>'
            : "";
          return (
            '<article class="card periodic-order-card"><div class="card-title"><div><h3>' + escapeHtml(item.title || "未命名周期商品") + "</h3>" +
            '<div class="mini">' + escapeHtml(lastOrder || item.product_code || "") + '</div></div><div class="price">' + escapeHtml(item.amount_label || "") + "</div></div>" +
            '<div class="kv"><span>剩余有效期</span><strong>' + escapeHtml(String(item.remaining_days || 0)) + " 天</strong>" +
            '<span>周期</span><strong>' + escapeHtml(String(item.duration_days || 0)) + " 天</strong>" +
            '<span>正式登录</span><strong>' + escapeHtml(huangyoucanBoolean(item, "huangyoucan_formally_logged_in", "是", "否")) + "</strong>" +
            '<span>token 消耗</span><strong>' + escapeHtml(huangyoucanBoolean(item, "huangyoucan_has_token_usage", "有", "无")) + "</strong>" +
            '<span>学习计划进度</span><strong>' + escapeHtml(huangyoucanProgress(item)) + "</strong>" +
            '<span>近 7 天打开次数</span><strong>' + escapeHtml(huangyoucanMatched(item) ? String(Number(item.huangyoucan_open_count_7d || 0)) : "—") + "</strong>" +
            '<span>最后打开时间</span><strong>' + escapeHtml(huangyoucanLastOpen(item)) + "</strong></div>" +
            '<div class="field periodic-remark"><div class="field-title">备注</div>' +
            '<textarea class="textarea periodic-remark-textarea" data-periodic-order-remark="' + escapeHtml(item.id || "") + '">' + escapeHtml(item.remark || "") + "</textarea></div>" +
            detailAction + "</article>"
          );
        })
        .join("");
  }

  function renderPeriodicOrders() {
    state.orderType = "periodic";
    renderOrders();
  }

  function renderCoupons() {
    const rows = state.data.coupons || [];
    if (!rows.length) {
      content.innerHTML = panel("", empty("暂无可领取优惠券"));
      return;
    }
    content.innerHTML = panel(
      "",
      rows.map((item) => {
        const products = (item.products || []).map((product) => product.title || "").filter(Boolean).join("、");
        return (
          '<article class="card link-card"><div class="card-title"><div><h3>' + escapeHtml(item.name || "未命名优惠券") + '</h3><div class="mini">' +
          escapeHtml(item.discount_label || "") + '</div></div></div><div class="kv"><span>适用商品</span><strong>' + escapeHtml(products || "全部已配置商品") +
          '</strong><span>领取截止</span><strong>' + escapeHtml(item.claim_ends_at || "") + '</strong></div><div class="row-actions"><button class="btn primary" type="button" data-copy-url="' +
          escapeHtml(item.url || "") + '">复制链接</button></div></article>'
        );
      }).join("")
    );
  }

  function materialTypeControls() {
    return segmentedControls(materialTabs, state.materialType, "data-material-type", "material-seg");
  }

  function renderMaterialLoadError(type, error) {
    content.innerHTML = panel(
      "素材",
      materialTypeControls() +
        '<div class="status error">' + escapeHtml((error && error.message) || "加载失败") + "</div>" +
        '<div class="row-actions"><button class="btn primary" type="button" data-retry-material-type="' + escapeHtml(type) + '">重试</button></div>'
    );
  }

  function renderMaterials() {
    const controls = materialTypeControls();
    if (state.materialType === "radar") {
      renderRadarLinks(controls);
      return;
    }
    const rows = state.data.materials.image || [];
    if (!rows.length) {
      content.innerHTML = panel("", controls + empty("暂无图片素材"));
      return;
    }
    content.innerHTML = panel(
      "",
      controls +
        rows
          .map((item) => {
            const fallbackLabel = item.thumbnail_label || "图";
            const thumb = item.thumbnail_url
              ? '<div class="material-thumb thumb image-thumb"><img src="' + escapeHtml(item.thumbnail_url) + '" alt="" data-material-thumb-img data-fallback-label="' + escapeHtml(fallbackLabel) + '"></div>'
              : '<div class="material-thumb thumb image-thumb">' + escapeHtml(fallbackLabel) + "</div>";
            return (
              '<article class="card material material--image">' + thumb +
              '<div class="material-main"><h3 class="material-title">' + escapeHtml(item.title || "未命名素材") + '</h3><div class="material-tags tags">' +
              (item.tags || []).map((tag) => '<span class="tag">' + escapeHtml(tag) + "</span>").join("") +
              '</div></div><button class="btn primary material-send" type="button" data-material-send="' + escapeHtml(item.id || "") + '">发送</button></article>'
            );
          })
          .join("")
    );
  }

  function renderRadarLinks(controls) {
    const rows = state.data.radar_links || [];
    if (!rows.length) {
      content.innerHTML = panel("", controls + empty("暂无启用中的雷达链接"));
      return;
    }
    content.innerHTML = panel(
      "",
      controls + rows.map((item) => (
        '<article class="card material material--radar"><div class="material-thumb thumb radar">' + escapeHtml(item.type_label || "雷达") +
        '</div><div class="material-main"><h3 class="material-title">' + escapeHtml(item.title || "未命名雷达") +
        '</h3><div class="mini">' + escapeHtml(item.type_label || "追踪链接") +
        '</div></div><button class="btn primary material-send" type="button" data-copy-url="' + escapeHtml(item.url || "") + '">复制链接</button></article>'
      )).join("")
    );
  }

  function messageTitle(item) {
    const customer = ((state.workbench || {}).customer || {}).display_name || "客户";
    if (item.scene === "group") {
      return (item.scene_label || "群聊") + " · 客户" + customer;
    }
    return "客户与 " + (item.staff_name || item.staff_userid || "客服") + " 私聊";
  }

  function renderOtherStaffMessages() {
    const rows = state.data.other_staff_messages || [];
    if (!rows.length) {
      if (((state.workbench || {}).customer || {}).owner_pending) {
        content.innerHTML = panel("其他客服的聊天记录", empty("员工身份待确认后可查看其他客服聊天记录"));
        return;
      }
      content.innerHTML = panel("其他客服的聊天记录", empty("暂无其他客服聊天记录"));
      return;
    }
    content.innerHTML = panel(
      "其他客服的聊天记录",
      '<div class="timeline">' +
        rows
          .map((item) => (
            '<article class="msg"><div class="msg-meta"><span>' + escapeHtml(item.send_time || "") + "</span><span>" + escapeHtml(item.scene_label || "") + "</span></div>" +
            '<div class="msg-title">' + escapeHtml(messageTitle(item)) + "</div>" +
            (item.type === "image"
              ? '<div class="imgmsg"><div class="imgph">图</div><div class="txt">' + escapeHtml(item.content || "发送了图片") + "</div></div>"
              : '<div class="txt">' + escapeHtml(item.sender_label || item.staff_name || "") + "：" + escapeHtml(item.content || "") + "</div>") +
            "</article>"
          ))
          .join("") +
      "</div>"
    );
  }

  function renderOwnerPendingWorkbench(message) {
    state.workbench = {
      customer: {
        external_userid: state.external_userid,
        display_name: "当前客户",
        owner_pending: true,
        mobile_bound: false,
        is_bound: false,
      },
      profile: {},
      workflow: {},
      diagnostics: { context_source_status: "owner_pending" },
    };
    setWorkbenchState(WORKBENCH_STATES.degraded_ready, { stage: "owner_pending", message: message || "" });
    renderTop();
    renderTabs();
    content.innerHTML = panel(
      "核心画像",
      '<div class="status error">' + escapeHtml(message || "员工身份待确认，请从企微侧边栏重新打开或稍后重试。") + "</div>" +
        '<div class="row-actions"><button class="btn primary" type="button" data-retry-boot>重试</button></div>'
    );
  }

  async function loadWorkbench() {
    setWorkbenchState(WORKBENCH_STATES.loading_workbench, { external_userid: state.external_userid });
    const payload = await requestPanelJson(
      "workbench",
      queryUrl(endpoint("workbenchUrl"), {
        external_userid: state.external_userid,
        owner_userid: state.owner_userid,
      }),
      { timeoutMs: PANEL_TIMEOUT_MS.workbench }
    );
    writeDebug("workbench response", payload);
    state.workbench = payload;
    const customer = payload.customer || {};
    state.external_userid = customer.external_userid || state.external_userid;
    state.owner_userid = customer.owner_userid || state.owner_userid;
    if (!state.bind_by_userid) state.bind_by_userid = state.owner_userid;
    setWorkbenchState(payload.diagnostics && payload.diagnostics.context_source_status === "error" ? WORKBENCH_STATES.degraded_ready : WORKBENCH_STATES.ready, payload.diagnostics || {});
    renderTop();
    renderTabs();
    renderActiveTab();
  }

  async function loadTabData(tab) {
    if (tab === "profile") {
      if (state.profileView === "timeline") await loadTimeline({ reset: true, force: true });
      return;
    }
    if (tab === "orders") {
      await loadOrders(state.orderType);
      return;
    }
    if (state.loaded[tab]) return;
    if (tab === "questionnaires") {
      const payload = await requestPanelJson("questionnaires", queryUrl(endpoint("questionnairesUrl"), customerContextQuery()));
      state.data.questionnaires = payload.questionnaires || [];
    } else if (tab === "products") {
      const payload = await requestPanelJson("products", queryUrl(endpoint("productsUrl"), customerContextQuery()));
      writeDebug("products response", productContextDiagnostics(payload));
      state.data.products = payload.products || [];
      state.data.service_period_products = payload.service_period_products || [];
    } else if (tab === "coupons") {
      const payload = await requestPanelJson("coupons", endpoint("couponsUrl"));
      state.data.coupons = payload.items || [];
    } else if (tab === "materials") {
      await loadMaterials(state.materialType);
    } else if (tab === "other_staff_messages") {
      if (((state.workbench || {}).customer || {}).owner_pending) {
        state.data.other_staff_messages = [];
        state.loaded[tab] = true;
        return;
      }
      const payload = await requestPanelJson(
        "other_staff_messages",
        queryUrl(endpoint("otherStaffMessagesUrl"), {
          external_userid: state.external_userid,
          current_userid: state.bind_by_userid || state.owner_userid,
          limit: 20,
        })
      );
      state.data.other_staff_messages = (payload.messages || [])
        .filter((item) => item && (item.type === "text" || item.type === "image"))
        .slice(-20);
    }
    state.loaded[tab] = true;
  }

  async function loadOrders(type) {
    const normalized = type === "periodic" ? "periodic" : "regular";
    const cacheKey = "orders:" + normalized;
    if (state.loaded[cacheKey]) return;
    const panelKey = normalized === "periodic" ? "periodic_orders" : "orders";
    const url = normalized === "periodic" ? endpoint("periodicOrdersUrl") : endpoint("ordersUrl");
    const payload = await requestPanelJson(panelKey, queryUrl(url, customerContextQuery()));
    if (payload.customer) {
      state.workbench.customer = Object.assign({}, state.workbench.customer || {}, payload.customer);
      renderTop();
    }
    if (normalized === "periodic") {
      writeDebug("periodic orders response", payload.diagnostics || {});
      state.data.periodic_orders = payload.periodic_orders || [];
    } else {
      writeDebug("orders response", payload.diagnostics || {});
      state.data.orders = payload.orders || [];
    }
    state.loaded[cacheKey] = true;
  }

  async function loadMaterials(type) {
    if (type === "radar") {
      if (state.data.radar_links) return;
      const payload = await requestPanelJson("radar_links", endpoint("radarLinksUrl"));
      state.data.radar_links = payload.items || [];
      return;
    }
    if (state.data.materials.image) return;
    const payload = await requestPanelJson("materials", queryUrl(endpoint("materialsUrl"), { type: "image", limit: 50 }));
    state.data.materials.image = payload.materials || [];
  }

  async function switchMaterialType(type) {
    if (!materialTabs.some((item) => item[0] === type)) return;
    state.materialType = type;
    if (state.activeTab !== "materials") return;
    setPanelLoading("素材");
    try {
      await loadMaterials(type);
      if (state.activeTab !== "materials" || state.materialType !== type) return;
      renderMaterials();
    } catch (error) {
      if (state.activeTab !== "materials" || state.materialType !== type) return;
      renderMaterialLoadError(type, error);
    }
  }

  async function switchOrderType(type) {
    if (!orderTabs.some((item) => item[0] === type)) return;
    state.orderType = type;
    if (state.activeTab !== "orders") return;
    setPanelLoading("订单");
    try {
      await loadOrders(type);
      if (state.activeTab !== "orders" || state.orderType !== type) return;
      renderOrders();
    } catch (error) {
      if (state.activeTab !== "orders" || state.orderType !== type) return;
      content.innerHTML = panel(
        "",
        orderTypeControls() + '<div class="status error">' + escapeHtml(error.message || "加载失败") + "</div>" +
          '<div class="row-actions"><button class="btn primary" type="button" data-retry-order-type="' + escapeHtml(type) + '">重试</button></div>'
      );
    }
  }

  async function loadTimeline(options) {
    const reset = Boolean(options && options.reset);
    const force = Boolean(options && options.force);
    const offset = reset ? 0 : Number((state.data.timeline || {}).next_offset || 0);
    const requestVersion = reset ? ++state.timelineRequestVersion : state.timelineRequestVersion;
    const url = queryUrl(endpoint("timelineUrl"), { limit: 20, offset });
    if (force) clearPanelCache("timeline");
    const payload = await requestPanelJson("timeline", url);
    if (requestVersion !== state.timelineRequestVersion) return;
    const current = reset ? [] : (state.data.timeline.items || []);
    state.data.timeline = {
      items: current.concat(payload.items || []),
      total: Number(payload.total || 0),
      has_more: Boolean(payload.has_more),
      next_offset: Number(payload.next_offset || 0),
    };
  }

  async function switchProfileView(view) {
    if (!profileTabs.some((item) => item[0] === view)) return;
    state.profileView = view;
    if (state.activeTab !== "profile") return;
    if (view === "basic") {
      renderProfile();
      return;
    }
    setPanelLoading("用户时间线");
    try {
      await loadTimeline({ reset: true, force: true });
      if (state.activeTab !== "profile" || state.profileView !== "timeline") return;
      renderProfileTimeline();
    } catch (error) {
      if (state.activeTab !== "profile" || state.profileView !== "timeline") return;
      content.innerHTML = panel(
        "",
        profileTypeControls() + '<div class="status error">' + escapeHtml(error.message || "时间线加载失败") + "</div>" +
          '<div class="row-actions"><button class="btn primary" type="button" data-refresh-timeline>重试</button></div>'
      );
    }
  }

  async function refreshTimeline() {
    if (state.activeTab !== "profile" || state.profileView !== "timeline") return;
    setPanelLoading("用户时间线");
    await loadTimeline({ reset: true, force: true });
    if (state.activeTab === "profile" && state.profileView === "timeline") renderProfileTimeline();
  }

  async function loadMoreTimeline() {
    if (!state.data.timeline.has_more) return;
    await loadTimeline({ reset: false, force: false });
    if (state.activeTab === "profile" && state.profileView === "timeline") renderProfileTimeline();
  }

  function renderActiveTab() {
    if (state.activeTab === "profile") renderProfile();
    if (state.activeTab === "questionnaires") renderQuestionnaires();
    if (state.activeTab === "products") renderProducts();
    if (state.activeTab === "orders") renderOrders();
    if (state.activeTab === "coupons") renderCoupons();
    if (state.activeTab === "materials") renderMaterials();
    if (state.activeTab === "other_staff_messages") renderOtherStaffMessages();
  }

  async function switchTab(tab) {
    if (tab !== "profile" && !isWorkbenchReady()) return;
    state.activeTab = tab;
    renderTabs();
    const label = tabs.find((item) => item[0] === tab)?.[1] || "";
    const materialType = tab === "materials" ? state.materialType : "";
    const orderType = tab === "orders" ? state.orderType : "";
    const profileView = tab === "profile" ? state.profileView : "";
    setPanelLoading(label);
    try {
      await loadTabData(tab);
      if (
        state.activeTab !== tab ||
        (tab === "materials" && state.materialType !== materialType) ||
        (tab === "orders" && state.orderType !== orderType) ||
        (tab === "profile" && state.profileView !== profileView)
      ) return;
      renderActiveTab();
    } catch (error) {
      if (
        state.activeTab !== tab ||
        (tab === "materials" && state.materialType !== materialType) ||
        (tab === "orders" && state.orderType !== orderType) ||
        (tab === "profile" && state.profileView !== profileView)
      ) return;
      content.innerHTML = panel(
        label,
        '<div class="status error">' + escapeHtml(error.message || "加载失败") + "</div>" +
          '<div class="row-actions"><button class="btn primary" type="button" data-retry-tab="' + escapeHtml(tab) + '">重试</button></div>'
      );
    }
  }

  async function sendMaterial(materialId) {
    if (state.materialType !== "image") {
      showToast("雷达链接请使用复制链接", "error");
      return;
    }
    try {
      const payload = await requestJson(endpoint("materialSendUrl"), {
        method: "POST",
        body: JSON.stringify({
          external_userid: state.external_userid,
          owner_userid: state.owner_userid,
          type: "image",
          material_id: materialId,
          operator: state.bind_by_userid || state.owner_userid || "",
          delivery_mode: "chat_toolbar",
        }),
      });
      if (!payload.media_id) throw new Error("图片素材未取得 media_id");
      await sendImageToCurrentChat(payload.media_id);
      showToast("已发送到当前会话");
    } catch (error) {
      showToast(error.message || "发送失败", "error");
    }
  }

  function fallbackCopyText(value) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let copied = false;
    try {
      copied = Boolean(document.execCommand && document.execCommand("copy"));
    } finally {
      document.body.removeChild(textarea);
    }
    return copied;
  }

  async function copyLink(value) {
    const link = absoluteUrl(value);
    if (!link) {
      showToast("暂无可复制链接", "error");
      return false;
    }
    try {
      const clipboard = window.navigator && window.navigator.clipboard;
      if (clipboard && typeof clipboard.writeText === "function") {
        await clipboard.writeText(link);
      } else if (!fallbackCopyText(link)) {
        throw new Error("copy unavailable");
      }
      showToast("已复制");
      return true;
    } catch (_error) {
      if (fallbackCopyText(link)) {
        showToast("已复制");
        return true;
      }
      showToast("复制失败，请长按链接复制", "error");
      return false;
    }
  }

  async function sendProduct(productIndex, kind) {
    const rows = kind === "service_period" ? state.data.service_period_products || [] : state.data.products || [];
    const item = rows[Number(productIndex)] || {};
    const fallbackPath = kind === "service_period" ? "" : item.id ? "/p/" + item.id : "";
    const link = absoluteUrl(item.product_url || fallbackPath);
    if (!link) {
      showToast("暂无商品链接", "error");
      return;
    }
    try {
      await sendLinkToCurrentChat({
        title: item.title || "未命名商品",
        url: link,
        imageUrl: absoluteUrl(PRODUCT_CARD_IMAGE_PATH),
      });
      showToast("已发送商品");
    } catch (error) {
      showToast(error.message || "发送失败", "error");
    }
  }

  function assertWeComSendOk(res) {
    const errMsg = String((res || {}).err_msg || "");
    if (!errMsg || errMsg.indexOf(":ok") >= 0) return;
    throw new Error(String((res || {}).errmsg || errMsg || "发送失败"));
  }

  async function sendLinkToCurrentChat(payload) {
    const sdkReady = await initWeComSdk();
    if (!sdkReady.ok || !window.wx || typeof window.wx.invoke !== "function") {
      throw new Error("请在企微侧边栏内发送");
    }
    const res = await invokeWeCom("sendChatMessage", {
      msgtype: "news",
      news: {
        link: String(payload.url || ""),
        title: String(payload.title || "未命名商品"),
        desc: "",
        imgUrl: String(payload.imageUrl || ""),
      },
    }, SDK_TIMEOUT_MS);
    writeDebug("sendChatMessage news result", res || {});
    assertWeComSendOk(res);
    return res;
  }

  async function sendImageToCurrentChat(mediaId) {
    const sdkReady = await initWeComSdk();
    if (!sdkReady.ok || !window.wx || typeof window.wx.invoke !== "function") {
      throw new Error("请在企微侧边栏内发送");
    }
    const res = await invokeWeCom("sendChatMessage", {
      msgtype: "image",
      image: { mediaid: mediaId },
    }, SDK_TIMEOUT_MS);
    writeDebug("sendChatMessage result", res || {});
    assertWeComSendOk(res);
    return res;
  }

  function openMobileModal() {
    mobileInput.value = ((state.workbench || {}).customer || {}).mobile || "";
    mobileStatus.textContent = "";
    mobileStatus.className = "status";
    mobileModal.classList.remove("hidden");
    mobileInput.focus();
  }

  function closeMobileModal() {
    mobileModal.classList.add("hidden");
  }

  async function saveMobile() {
    confirmMobileButton.disabled = true;
    mobileStatus.textContent = "正在保存…";
    try {
      const customer = (state.workbench || {}).customer || {};
      if (customer.owner_pending) {
        throw new Error("请先从企微侧边栏重新打开以确认当前员工身份");
      }
      const payload = await requestJson(endpoint("bindMobileUrl"), {
        method: "POST",
        body: JSON.stringify({
          external_userid: state.external_userid,
          owner_userid: state.owner_userid,
          bind_by_userid: state.bind_by_userid || state.owner_userid,
          mobile: mobileInput.value,
          force_rebind: Boolean(customer.mobile_bound !== undefined ? customer.mobile_bound : customer.is_bound && customer.mobile),
        }),
      });
      const binding = payload.binding || payload;
      state.workbench.customer.mobile = binding.mobile || mobileInput.value;
      state.workbench.customer.is_bound = true;
      state.workbench.customer.mobile_bound = true;
      renderTop();
      closeMobileModal();
      showToast("手机号已保存");
    } catch (error) {
      mobileStatus.textContent = error.message || "保存失败";
      mobileStatus.className = "status error";
      showToast(error.message || "保存失败", "error");
    } finally {
      confirmMobileButton.disabled = false;
    }
  }

  async function resolveContextFromQuery() {
    state.external_userid = firstQueryValue(["external_userid", "externalUserid", "externalUserId", "user_id", "userId"]);
    writeDebug("query context", {
      has_external_userid: Boolean(state.external_userid),
      has_owner_token: Boolean(state.sidebar_owner_token),
    });
    return Boolean(state.external_userid);
  }

  async function initWeComSdk() {
    if (!window.wx) return { ok: false, status: WORKBENCH_STATES.sdk_unavailable, reason: "wx_missing" };
    let configPayload;
    try {
      configPayload = await requestJssdkConfig();
      applySidebarOwnerToken(configPayload);
      writeDebug("jssdk config response", {
        has_config: Boolean(configPayload && configPayload.config),
        has_agent_config: Boolean(configPayload && configPayload.agent_config),
        owner_token_status: state.sidebar_owner_token_status,
      });
    } catch (error) {
      writeDebug("jssdk config error", { message: error.message || String(error) });
      return { ok: false, status: WORKBENCH_STATES.sdk_unavailable, reason: "jssdk_config_failed", error: error.message || String(error) };
    }
    return await new Promise((resolve) => {
      let resolved = false;
      const timer = window.setTimeout(() => finish(false, "sdk_timeout"), SDK_TIMEOUT_MS);
      const finish = (ok, reason) => {
        if (!resolved) {
          resolved = true;
          window.clearTimeout(timer);
          resolve({ ok, status: ok ? WORKBENCH_STATES.identifying_customer : WORKBENCH_STATES.sdk_unavailable, reason: reason || "" });
        }
      };
      window.wx.config({
        beta: true,
        debug: false,
        appId: configPayload.corp_id,
        timestamp: Number(configPayload.config.timestamp),
        nonceStr: configPayload.config.nonceStr,
        signature: configPayload.config.signature,
        jsApiList: ["getContext", "sendChatMessage"],
      });
      window.wx.ready(function () {
        writeDebug("wx.config success", { url: configPayload.config.url });
        if (typeof window.wx.agentConfig !== "function") {
          finish(false, "agentConfig_missing");
          return;
        }
        window.wx.agentConfig({
          corpid: configPayload.corp_id,
          agentid: String(configPayload.agent_id),
          timestamp: Number(configPayload.agent_config.timestamp),
          nonceStr: configPayload.agent_config.nonceStr,
          signature: configPayload.agent_config.signature,
          jsApiList: ["getContext", "getCurExternalContact", "sendChatMessage"],
          success: function (res) {
            writeDebug("wx.agentConfig success", res || {});
            applyWeComViewerIdentity(res || {}, "agentConfig", { allowUserId: true });
            finish(true, "");
          },
          fail: function (err) {
            writeDebug("wx.agentConfig fail", err || {});
            finish(false, "agentConfig_failed");
          },
        });
      });
      window.wx.error(function (err) {
        writeDebug("wx.config fail", err || {});
        finish(false, "wx_config_failed");
      });
    });
  }

  function invokeWeCom(method, payload, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (!window.wx || typeof window.wx.invoke !== "function") {
        reject(new Error("wx.invoke unavailable"));
        return;
      }
      let resolved = false;
      const timer = window.setTimeout(() => {
        if (resolved) return;
        resolved = true;
        const error = new Error(method + " timeout");
        error.stage = method;
        reject(error);
      }, timeoutMs || SDK_TIMEOUT_MS);
      window.wx.invoke(method, payload || {}, function (res) {
        if (resolved) return;
        resolved = true;
        window.clearTimeout(timer);
        resolve(res || {});
      });
    });
  }

  async function resolveContextFromWeCom() {
    const sdkReady = await initWeComSdk();
    if (!sdkReady.ok || !window.wx || typeof window.wx.invoke !== "function") return sdkReady;
    try {
      const contextPayload = await invokeWeCom("getContext", {}, SDK_TIMEOUT_MS);
      writeDebug("getContext result", contextPayload || {});
      applyWeComViewerIdentity(contextPayload || {}, "getContext", { allowUserId: true });
    } catch (error) {
      writeDebug("getContext error", { message: error.message || String(error) });
    }
    try {
      const res = await invokeWeCom("getCurExternalContact", {}, SDK_TIMEOUT_MS);
      writeDebug("getCurExternalContact result", res || {});
      const externalUserid = extractWeComExternalUserid(res || {});
      if (!externalUserid) {
        return { ok: false, status: WORKBENCH_STATES.context_missing, reason: "external_userid_missing" };
      }
      state.external_userid = externalUserid;
      applyWeComViewerIdentity(res || {}, "getCurExternalContact");
      if (!state.bind_by_userid) state.bind_by_userid = state.owner_userid;
      await refreshSidebarOwnerToken();
      writeDebug("getCurExternalContact success", {
        external_userid: state.external_userid,
        owner_userid: state.owner_userid,
        bind_by_userid: state.bind_by_userid,
      });
      return { ok: true, status: WORKBENCH_STATES.identifying_customer };
    } catch (error) {
      writeDebug("getCurExternalContact error", { message: error.message || String(error), stage: error.stage || "" });
      return { ok: false, status: WORKBENCH_STATES.context_missing, reason: error.stage || "getCurExternalContact_failed", error: error.message || String(error) };
    }
  }

  async function boot() {
    setWorkbenchState(WORKBENCH_STATES.identifying_customer);
    renderTabs();
    setPanelLoading("");
    try {
      const hasQuery = await resolveContextFromQuery();
      let contextResult = hasQuery ? { ok: true, status: WORKBENCH_STATES.identifying_customer, source: "query" } : await resolveContextFromWeCom();
      if (hasQuery && !state.sidebar_owner_token && !state.owner_userid) {
        const sdkContext = await resolveContextFromWeCom();
        if (sdkContext.ok) contextResult = sdkContext;
      }
      writeDebug("identity result", contextResult);
      if (!contextResult.ok) {
        if (!state.sidebar_owner_token && state.external_userid && await maybeStartSidebarOAuth(contextResult.reason || "context_not_ready")) return;
        setWorkbenchState(contextResult.status || WORKBENCH_STATES.context_missing, contextResult);
        renderRetryPanel("", contextResult.status === WORKBENCH_STATES.sdk_unavailable ? "企微 SDK 暂不可用，请确认从企微侧边栏打开，或带 external_userid 参数重试。" : "未识别到客户，请从企微客户侧边栏重新打开。");
        return;
      }
      if (!state.sidebar_owner_token) {
        await refreshSidebarOwnerToken();
      }
      if (!state.sidebar_owner_token && await maybeStartSidebarOAuth("owner_token_missing")) return;
      await loadWorkbench();
    } catch (error) {
      writeDebug("boot error", { message: error.message || String(error), stage: error.stage || "" });
      if (String(error.message || "").indexOf("owner_userid is required") >= 0) {
        renderOwnerPendingWorkbench(error.message || "owner_userid is required");
        return;
      }
      setWorkbenchState(WORKBENCH_STATES.error, { message: error.message || String(error), stage: error.stage || "" });
      renderRetryPanel("", error.message || "加载失败，请稍后重试。");
    }
  }

  tabsNode.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button || button.disabled) return;
    switchTab(button.dataset.tab);
  });

  content.addEventListener("change", (event) => {
    const field = event.target.dataset.profileField;
    if (!field || event.target.tagName === "TEXTAREA") return;
    updateProfileField(field, event.target.value);
    saveProfile();
  });

  content.addEventListener("input", (event) => {
    const periodicOrderId = event.target.dataset.periodicOrderRemark;
    if (periodicOrderId && event.target.tagName === "TEXTAREA") {
      updatePeriodicOrderRemark(periodicOrderId, event.target.value);
      savePeriodicOrderRemarkSoon(periodicOrderId);
      return;
    }
    const field = event.target.dataset.profileField;
    if (!field || event.target.tagName !== "TEXTAREA") return;
    updateProfileField(field, event.target.value);
    saveProfileSoon();
  });

  content.addEventListener("blur", (event) => {
    const periodicOrderId = event.target.dataset.periodicOrderRemark;
    if (periodicOrderId && event.target.tagName === "TEXTAREA") {
      updatePeriodicOrderRemark(periodicOrderId, event.target.value);
      savePeriodicOrderRemark(periodicOrderId);
      return;
    }
    const field = event.target.dataset.profileField;
    if (!field || event.target.tagName !== "TEXTAREA") return;
    updateProfileField(field, event.target.value);
    saveProfile();
  }, true);

  content.addEventListener("click", async (event) => {
    const retryButton = event.target.closest("[data-retry-boot]");
    if (retryButton) {
      retryButton.disabled = true;
      boot();
      return;
    }
    const retryTabButton = event.target.closest("[data-retry-tab]");
    if (retryTabButton) {
      retryTabButton.disabled = true;
      await switchTab(retryTabButton.dataset.retryTab);
      return;
    }
    const retryMaterialTypeButton = event.target.closest("[data-retry-material-type]");
    if (retryMaterialTypeButton) {
      retryMaterialTypeButton.disabled = true;
      await switchMaterialType(retryMaterialTypeButton.dataset.retryMaterialType);
      return;
    }
    const retryOrderTypeButton = event.target.closest("[data-retry-order-type]");
    if (retryOrderTypeButton) {
      retryOrderTypeButton.disabled = true;
      await switchOrderType(retryOrderTypeButton.dataset.retryOrderType);
      return;
    }
    const profileViewButton = event.target.closest("[data-profile-view]");
    if (profileViewButton) {
      await switchProfileView(profileViewButton.dataset.profileView);
      return;
    }
    const refreshTimelineButton = event.target.closest("[data-refresh-timeline]");
    if (refreshTimelineButton) {
      refreshTimelineButton.disabled = true;
      try {
        await refreshTimeline();
      } catch (error) {
        showToast(error.message || "时间线刷新失败", "error");
        if (state.activeTab === "profile" && state.profileView === "timeline") renderProfileTimeline();
      }
      return;
    }
    const loadMoreTimelineButton = event.target.closest("[data-load-more-timeline]");
    if (loadMoreTimelineButton) {
      loadMoreTimelineButton.disabled = true;
      try {
        await loadMoreTimeline();
      } catch (error) {
        showToast(error.message || "加载更多失败", "error");
        loadMoreTimelineButton.disabled = false;
      }
      return;
    }
    const qButton = event.target.closest("[data-toggle-questionnaire]");
    if (qButton) {
      const card = content.querySelector('[data-questionnaire-card="' + qButton.dataset.toggleQuestionnaire + '"]');
      if (card) card.classList.toggle("open");
      return;
    }
    const materialTypeButton = event.target.closest("[data-material-type]");
    if (materialTypeButton) {
      await switchMaterialType(materialTypeButton.dataset.materialType);
      return;
    }
    const orderTypeButton = event.target.closest("[data-order-type]");
    if (orderTypeButton) {
      await switchOrderType(orderTypeButton.dataset.orderType);
      return;
    }
    const copyButton = event.target.closest("[data-copy-url]");
    if (copyButton) {
      copyButton.disabled = true;
      try {
        await copyLink(copyButton.dataset.copyUrl);
      } finally {
        copyButton.disabled = false;
      }
      return;
    }
    const materialSendButton = event.target.closest("[data-material-send]");
    if (materialSendButton) {
      materialSendButton.disabled = true;
      try {
        await sendMaterial(materialSendButton.dataset.materialSend);
      } finally {
        materialSendButton.disabled = false;
      }
      return;
    }
    const productTypeButton = event.target.closest("[data-product-type]");
    if (productTypeButton) {
      state.productType = productTypeButton.dataset.productType || "regular";
      renderProducts();
      return;
    }
    const productSendButton = event.target.closest("[data-product-send]");
    if (productSendButton) {
      productSendButton.disabled = true;
      try {
        await sendProduct(productSendButton.dataset.productSend, productSendButton.dataset.productKind || state.productType);
      } finally {
        productSendButton.disabled = false;
      }
      return;
    }
    const orderDetailButton = event.target.closest("[data-order-detail-url]");
    if (orderDetailButton) {
      const link = absoluteUrl(orderDetailButton.dataset.orderDetailUrl);
      if (!link) {
        showToast("暂无订单详情链接", "error");
        return;
      }
      window.open(link, "_blank", "noopener");
      showToast("已打开订单详情");
      return;
    }
  });

  content.addEventListener("error", (event) => {
    const image = event.target.closest ? event.target.closest("[data-material-thumb-img]") : null;
    if (!image) return;
    const parent = image.parentElement;
    if (!parent) return;
    parent.textContent = image.dataset.fallbackLabel || "图";
    parent.classList.remove("image-thumb");
  }, true);

  document.getElementById("change-mobile-button").addEventListener("click", openMobileModal);
  document.getElementById("close-mobile-modal").addEventListener("click", closeMobileModal);
  document.getElementById("cancel-mobile-button").addEventListener("click", closeMobileModal);
  confirmMobileButton.addEventListener("click", saveMobile);

  boot();
})();
