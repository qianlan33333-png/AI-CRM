import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../aicrm_next/frontend_compat/static/sidebar_workbench/sidebar_workbench.js", import.meta.url),
  "utf8",
);

const bootMarker = "  boot();\n})();";
assert.equal(source.includes(bootMarker), true, "workbench boot marker changed; update the test harness");

function createNode() {
  return {
    attributes: {},
    className: "",
    dataset: {},
    disabled: false,
    innerHTML: "",
    parentElement: null,
    style: {},
    textContent: "",
    value: "",
    addEventListener() {},
    appendChild(child) {
      child.parentElement = this;
    },
    removeChild(child) {
      child.parentElement = null;
    },
    closest() {
      return null;
    },
    focus() {},
    querySelector() {
      return null;
    },
    select() {},
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
  };
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return JSON.stringify(payload);
    },
  };
}

function loadHarness(fetchImpl, options = {}) {
  const nodes = new Map();
  const copiedValues = [];
  let fallbackCopyCalls = 0;
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, createNode());
    return nodes.get(id);
  };
  node("sidebar-workbench-root").dataset = {
    debugEnabled: "false",
    jssdkConfigUrl: "/api/sidebar/jssdk-config",
    materialsUrl: "/api/sidebar/v2/materials",
    periodicOrdersUrl: "/api/sidebar/v2/periodic-orders",
    couponsUrl: "/api/sidebar/v2/coupons",
    radarLinksUrl: "/api/sidebar/v2/radar-links",
    timelineUrl: "/api/sidebar/v2/timeline",
    questionnairesUrl: "/api/sidebar/v2/questionnaires",
    ordersUrl: "/api/sidebar/v2/orders",
    workbenchUrl: "/api/sidebar/v2/workbench",
  };

  const document = {
    body: createNode(),
    createElement() {
      return createNode();
    },
    execCommand(command) {
      if (command !== "copy") return false;
      fallbackCopyCalls += 1;
      return options.fallbackCopyResult !== false;
    },
    getElementById(id) {
      return node(id);
    },
  };
  const nativeSetTimeout = globalThis.setTimeout;
  let nowMs = 1_000_000;
  class HarnessDate extends Date {
    static now() {
      return nowMs;
    }
  }
  const window = {
    clearTimeout: globalThis.clearTimeout,
    location: {
      href: "https://crm.example.test/sidebar/bind-mobile",
      origin: "https://crm.example.test",
      pathname: "/sidebar/bind-mobile",
      search: "",
    },
    open() {},
    navigator: options.clipboardWrite
      ? {
          clipboard: {
            async writeText(value) {
              copiedValues.push(value);
              return options.clipboardWrite(value);
            },
          },
        }
      : {},
    setTimeout(callback, delay, ...args) {
      return nativeSetTimeout(callback, delay === 420 ? 0 : delay, ...args);
    },
  };
  const instrumented = source.replace(
    bootMarker,
    "  globalThis.__sidebarTestApi = { jssdkConfigUrl, loadWorkbench, requestJssdkConfig, requestPanelJson, switchTab, switchMaterialType, switchOrderType, switchProfileView, loadMoreTimeline, refreshTimeline, copyLink, state };\n})();",
  );
  const context = {
    AbortController,
    Date: HarnessDate,
    URL,
    URLSearchParams,
    console,
    document,
    encodeURIComponent,
    fetch: fetchImpl,
    window,
  };
  vm.runInNewContext(instrumented, context);
  return {
    api: context.__sidebarTestApi,
    copiedValues,
    nodes,
    get fallbackCopyCalls() {
      return fallbackCopyCalls;
    },
    advanceTime(ms) {
      nowMs += ms;
    },
  };
}

test("same-URL JSSDK config calls share one request until the token-safe cache expiry", async () => {
  let fetchCalls = 0;
  const { api, advanceTime } = loadHarness(async () => {
    fetchCalls += 1;
    return jsonResponse({
      ok: true,
      corp_id: "corp",
      config: { signature: `sig-${fetchCalls}` },
      sidebar_owner_context: { expires_in: 60 },
    });
  });

  const [first, second] = await Promise.all([
    api.requestJssdkConfig(),
    api.requestJssdkConfig(),
  ]);
  const resolved = await api.requestJssdkConfig();

  assert.equal(fetchCalls, 1);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(JSON.stringify(first), JSON.stringify(resolved));
  assert.equal(api.state.jssdkConfigRequests.size, 0);
  assert.equal(api.state.jssdkConfigCache.size, 1);

  advanceTime(30_001);
  const refreshed = await api.requestJssdkConfig();
  assert.equal(fetchCalls, 2);
  assert.equal(refreshed.config.signature, "sig-2");
});

test("JSSDK config without a valid owner expiry is single-flight only", async () => {
  let fetchCalls = 0;
  const { api } = loadHarness(async () => {
    fetchCalls += 1;
    return jsonResponse({ ok: true, corp_id: "corp", config: { signature: `sig-${fetchCalls}` } });
  });

  await Promise.all([api.requestJssdkConfig(), api.requestJssdkConfig()]);
  assert.equal(fetchCalls, 1);
  assert.equal(api.state.jssdkConfigCache.size, 0);

  await api.requestJssdkConfig();
  assert.equal(fetchCalls, 2);
});

test("failed JSSDK config calls clear pending state and can be retried", async () => {
  let fetchCalls = 0;
  const { api } = loadHarness(async () => {
    fetchCalls += 1;
    if (fetchCalls <= 2) return jsonResponse({ ok: false, error: "temporary config failure" }, 503);
    return jsonResponse({
      ok: true,
      corp_id: "corp",
      config: { signature: "retry-sig" },
      sidebar_owner_context: { expires_in: 60 },
    });
  });

  await assert.rejects(api.requestJssdkConfig(), /temporary config failure/);
  assert.equal(fetchCalls, 2, "the existing one-retry JSSDK network policy remains intact");
  assert.equal(api.state.jssdkConfigRequests.size, 0);
  assert.equal(api.state.jssdkConfigCache.size, 0);

  const retried = await api.requestJssdkConfig();
  assert.equal(fetchCalls, 3);
  assert.equal(retried.config.signature, "retry-sig");
});

test("JSSDK config cache separates URLs with and without external_userid", async () => {
  const fetchedUrls = [];
  const { api } = loadHarness(async (url) => {
    fetchedUrls.push(String(url));
    return jsonResponse({ ok: true, request_url: String(url), sidebar_owner_context: { expires_in: 60 } });
  });

  const withoutCustomer = api.jssdkConfigUrl();
  await api.requestJssdkConfig();
  api.state.external_userid = "wm_jssdk_context";
  const withCustomer = api.jssdkConfigUrl();
  await api.requestJssdkConfig();

  assert.notEqual(withoutCustomer, withCustomer);
  assert.equal(new URL(withoutCustomer).searchParams.has("external_userid"), false);
  assert.equal(new URL(withCustomer).searchParams.get("external_userid"), "wm_jssdk_context");
  assert.equal(fetchedUrls.length, 2);
  assert.equal(api.state.jssdkConfigCache.size, 2);
});

test("non-profile tabs cannot request panels before the workbench is ready", async () => {
  let releaseWorkbench;
  let orderCalls = 0;
  const pendingWorkbench = new Promise((resolve) => {
    releaseWorkbench = resolve;
  });
  const { api } = loadHarness(async (url) => {
    if (String(url).includes("/workbench")) return pendingWorkbench;
    if (String(url).includes("/orders")) {
      orderCalls += 1;
      return jsonResponse({ ok: true, orders: [] });
    }
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.external_userid = "wm_ready_gate";
  api.state.owner_userid = "sales_01";

  const loading = api.loadWorkbench();
  await api.switchTab("orders");
  assert.equal(orderCalls, 0);
  assert.equal(api.state.activeTab, "profile");

  releaseWorkbench(jsonResponse({
    ok: true,
    customer: { external_userid: "wm_ready_gate", owner_userid: "sales_01" },
    profile: {},
    workflow: {},
    diagnostics: {},
  }));
  await loading;
  await api.switchTab("orders");

  assert.equal(api.state.status, "ready");
  assert.equal(orderCalls, 1);
  assert.equal(api.state.activeTab, "orders");
});

test("a failed radar subtype has a manual retry path", async () => {
  let materialCalls = 0;
  const { api, nodes } = loadHarness(async (url) => {
    if (!String(url).includes("/radar-links")) throw new Error(`unexpected URL: ${url}`);
    materialCalls += 1;
    if (materialCalls === 1) return jsonResponse({ ok: false, error: "radar failed" }, 503);
    return jsonResponse({ ok: true, items: [{ id: "radar-1", type_label: "链接", title: "Radar Ready" }] });
  });
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };
  api.state.activeTab = "materials";

  await api.switchMaterialType("radar");
  assert.equal(materialCalls, 1);
  assert.equal(nodes.get("content").innerHTML.includes('data-retry-material-type="radar"'), true);

  await api.switchMaterialType("radar");
  assert.equal(materialCalls, 2);
  assert.equal(nodes.get("content").innerHTML.includes("Radar Ready"), true);
});

test("an old material subtype error cannot replace a newer subtype", async () => {
  let releaseImage;
  const pendingImage = new Promise((resolve) => {
    releaseImage = resolve;
  });
  const { api, nodes } = loadHarness(async (url) => {
    if (String(url).includes("/materials")) return pendingImage;
    if (String(url).includes("/radar-links")) return jsonResponse({ ok: true, items: [{ id: "radar-2", title: "New Radar" }] });
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };
  api.state.activeTab = "materials";

  const oldType = api.switchMaterialType("image");
  await api.switchMaterialType("radar");
  const currentPanel = nodes.get("content").innerHTML;
  releaseImage(jsonResponse({ ok: false, error: "old image failed" }, 503));
  await oldType;

  assert.equal(api.state.materialType, "radar");
  assert.equal(nodes.get("content").innerHTML, currentPanel);
});

test("an old material subtype result cannot replace a later tab", async () => {
  let releaseImage;
  const pendingImage = new Promise((resolve) => {
    releaseImage = resolve;
  });
  const { api, nodes } = loadHarness(async (url) => {
    if (String(url).includes("/materials")) return pendingImage;
    if (String(url).includes("/orders")) return jsonResponse({ ok: true, orders: [] });
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };
  api.state.activeTab = "materials";

  const oldType = api.switchMaterialType("image");
  await api.switchTab("orders");
  const currentPanel = nodes.get("content").innerHTML;
  releaseImage(jsonResponse({ ok: true, materials: [{ id: "image-1", type: "image", title: "Old Image" }] }));
  await oldType;

  assert.equal(api.state.activeTab, "orders");
  assert.equal(nodes.get("content").innerHTML, currentPanel);
});

test("concurrent requests for one customer panel share the production request", async () => {
  let fetchCalls = 0;
  const { api } = loadHarness(async () => {
    fetchCalls += 1;
    return jsonResponse({ ok: true, questionnaires: [{ id: "q-1" }] });
  });
  api.state.external_userid = "wm_single_flight";

  const url = "https://crm.example.test/api/sidebar/v2/questionnaires?external_userid=wm_single_flight";
  const [first, second] = await Promise.all([
    api.requestPanelJson("questionnaires", url),
    api.requestPanelJson("questionnaires", url),
  ]);

  assert.equal(fetchCalls, 1);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(api.state.panelRequests.size, 0);
});

test("a failed panel request is not replayed automatically and can be requested again", async () => {
  let fetchCalls = 0;
  const { api } = loadHarness(async () => {
    fetchCalls += 1;
    if (fetchCalls === 1) return jsonResponse({ ok: false, error: "temporary panel failure" }, 503);
    return jsonResponse({ ok: true, questionnaires: [{ id: "q-retry" }] });
  });
  api.state.external_userid = "wm_manual_retry";

  const url = "https://crm.example.test/api/sidebar/v2/questionnaires?external_userid=wm_manual_retry";
  await assert.rejects(api.requestPanelJson("questionnaires", url), /temporary panel failure/);
  assert.equal(fetchCalls, 1);
  assert.equal(api.state.panelRequests.size, 0);

  const retried = await api.requestPanelJson("questionnaires", url);
  assert.equal(fetchCalls, 2);
  assert.equal(retried.questionnaires[0].id, "q-retry");
});

test("an old tab failure cannot replace the currently active tab", async () => {
  let releaseQuestionnaires;
  let questionnaireCalls = 0;
  const pendingQuestionnaires = new Promise((resolve) => {
    releaseQuestionnaires = resolve;
  });
  const { api, nodes } = loadHarness(async (url) => {
    if (String(url).includes("/questionnaires")) {
      questionnaireCalls += 1;
      if (questionnaireCalls === 1) return pendingQuestionnaires;
      return jsonResponse({ ok: false, error: "old questionnaire failure" }, 503);
    }
    if (String(url).includes("/orders")) return jsonResponse({ ok: true, orders: [] });
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.external_userid = "wm_tab_race";
  api.state.owner_userid = "sales_01";
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };

  const oldTab = api.switchTab("questionnaires");
  assert.equal(questionnaireCalls, 1);
  await api.switchTab("orders");
  const currentPanel = nodes.get("content").innerHTML;
  assert.equal(currentPanel.includes("订单"), true);

  releaseQuestionnaires(jsonResponse({ ok: false, error: "old questionnaire failure" }, 503));
  await oldTab;

  assert.equal(api.state.activeTab, "orders");
  assert.equal(nodes.get("content").innerHTML, currentPanel);
});

test("workbench startup does not prefetch orders, coupons, radar, or timeline", async () => {
  const fetchedUrls = [];
  const { api } = loadHarness(async (url) => {
    fetchedUrls.push(String(url));
    if (String(url).includes("/workbench")) {
      return jsonResponse({
        ok: true,
        customer: { external_userid: "wm_lazy", owner_userid: "sales_01" },
        profile: {},
        workflow: {},
        diagnostics: {},
      });
    }
    throw new Error(`unexpected startup URL: ${url}`);
  });
  api.state.external_userid = "wm_lazy";
  api.state.owner_userid = "sales_01";

  await api.loadWorkbench();

  assert.equal(fetchedUrls.length, 1);
  assert.equal(fetchedUrls[0].includes("/workbench"), true);
  for (const path of ["/orders", "/periodic-orders", "/coupons", "/radar-links", "/timeline"]) {
    assert.equal(fetchedUrls.some((url) => url.includes(path)), false, `${path} must stay lazy`);
  }
});

test("order secondary tabs lazy-load regular and periodic APIs independently", async () => {
  const fetchedUrls = [];
  const { api, nodes } = loadHarness(async (url) => {
    fetchedUrls.push(String(url));
    if (String(url).includes("/periodic-orders")) {
      return jsonResponse({ ok: true, periodic_orders: [{ id: "period-1", title: "周期权益", remaining_days: 12 }] });
    }
    if (String(url).includes("/orders")) {
      return jsonResponse({ ok: true, orders: [{ id: "order-1", title: "普通订单" }] });
    }
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.external_userid = "wm_orders";
  api.state.owner_userid = "sales_01";
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };

  await api.switchTab("orders");
  assert.equal(fetchedUrls.filter((url) => url.includes("/orders")).length, 1);
  assert.equal(fetchedUrls.some((url) => url.includes("/periodic-orders")), false);
  assert.equal(nodes.get("content").innerHTML.includes("普通订单"), true);

  await api.switchOrderType("periodic");
  assert.equal(fetchedUrls.filter((url) => url.includes("/periodic-orders")).length, 1);
  assert.equal(nodes.get("content").innerHTML.includes("周期权益"), true);
  assert.equal(nodes.get("content").innerHTML.includes("data-periodic-order-remark"), true);

  await api.switchOrderType("regular");
  assert.equal(fetchedUrls.filter((url) => url.includes("/orders")).length, 1, "regular orders reuse their loaded state");
});

test("timeline refreshes on entry and paginates twenty items at a time", async () => {
  const offsets = [];
  const { api, nodes } = loadHarness(async (url) => {
    if (!String(url).includes("/timeline")) throw new Error(`unexpected URL: ${url}`);
    const offset = Number(new URL(String(url)).searchParams.get("offset"));
    offsets.push(offset);
    if (offset === 0) {
      return jsonResponse({
        ok: true,
        items: [{ event_type: "channel_entry", title: "最新渠道", event_time: "2026-07-17T10:00:00Z" }],
        total: 21,
        has_more: true,
        next_offset: 20,
      });
    }
    return jsonResponse({
      ok: true,
      items: [{ event_type: "radar_opened", title: "更早雷达", event_time: "2026-07-16T10:00:00Z" }],
      total: 21,
      has_more: false,
      next_offset: 21,
    });
  });
  api.state.external_userid = "wm_timeline";
  api.state.owner_userid = "sales_01";
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };

  await api.switchProfileView("timeline");
  assert.deepEqual(offsets, [0]);
  assert.equal(nodes.get("content").innerHTML.includes("最新渠道"), true);
  assert.equal(nodes.get("content").innerHTML.includes("加载更多"), true);

  await api.loadMoreTimeline();
  assert.deepEqual(offsets, [0, 20]);
  assert.equal(nodes.get("content").innerHTML.indexOf("最新渠道") < nodes.get("content").innerHTML.indexOf("更早雷达"), true);

  await api.refreshTimeline();
  assert.deepEqual(offsets, [0, 20, 0]);
  assert.equal(nodes.get("content").innerHTML.includes("更早雷达"), false, "refresh replaces the previous page set");
});

test("coupon and radar links use clipboard copy without any send request", async () => {
  let fetchCalls = 0;
  const { api, copiedValues } = loadHarness(
    async () => {
      fetchCalls += 1;
      throw new Error("copy must not request an API");
    },
    { clipboardWrite: async () => undefined },
  );

  assert.equal(await api.copyLink("/c/coupon-1"), true);
  assert.equal(await api.copyLink("https://id-dev.youcangogogo.com/r/radar-1"), true);
  assert.deepEqual(copiedValues, [
    "https://crm.example.test/c/coupon-1",
    "https://id-dev.youcangogogo.com/r/radar-1",
  ]);
  assert.equal(fetchCalls, 0);
});

test("copy link falls back to a hidden textarea when Clipboard API is unavailable", async () => {
  const harness = loadHarness(async () => {
    throw new Error("copy must not request an API");
  });

  assert.equal(await harness.api.copyLink("/r/fallback"), true);
  assert.equal(harness.fallbackCopyCalls, 1);
});

test("coupons and radar links remain lazy until their exact views are opened", async () => {
  const fetchedUrls = [];
  const { api, nodes } = loadHarness(async (url) => {
    fetchedUrls.push(String(url));
    if (String(url).includes("/coupons")) return jsonResponse({ ok: true, items: [] });
    if (String(url).includes("/materials")) return jsonResponse({ ok: true, materials: [] });
    if (String(url).includes("/radar-links")) return jsonResponse({ ok: true, items: [] });
    throw new Error(`unexpected URL: ${url}`);
  });
  api.state.external_userid = "wm_lazy_views";
  api.state.owner_userid = "sales_01";
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };

  await api.switchTab("coupons");
  assert.equal(fetchedUrls.filter((url) => url.includes("/coupons")).length, 1);
  assert.equal(fetchedUrls.some((url) => url.includes("/radar-links")), false);
  assert.equal(nodes.get("content").innerHTML.includes("暂无可领取优惠券"), true);

  await api.switchTab("materials");
  assert.equal(fetchedUrls.filter((url) => url.includes("/materials")).length, 1);
  assert.equal(fetchedUrls.some((url) => url.includes("/radar-links")), false);

  await api.switchMaterialType("radar");
  assert.equal(fetchedUrls.filter((url) => url.includes("/radar-links")).length, 1);
  assert.equal(nodes.get("content").innerHTML.includes("暂无启用中的雷达链接"), true);
});

test("timeline exposes error retry and legal empty states", async () => {
  let timelineCalls = 0;
  const { api, nodes } = loadHarness(async (url) => {
    if (!String(url).includes("/timeline")) throw new Error(`unexpected URL: ${url}`);
    timelineCalls += 1;
    if (timelineCalls === 1) return jsonResponse({ ok: false, error: "timeline unavailable" }, 503);
    return jsonResponse({ ok: true, items: [], total: 0, has_more: false, next_offset: 0 });
  });
  api.state.external_userid = "wm_timeline_error";
  api.state.owner_userid = "sales_01";
  api.state.status = "ready";
  api.state.workbench = { customer: {}, profile: {}, workflow: {} };

  await api.switchProfileView("timeline");
  assert.equal(nodes.get("content").innerHTML.includes("timeline unavailable"), true);
  assert.equal(nodes.get("content").innerHTML.includes("data-refresh-timeline"), true);

  await api.switchProfileView("timeline");
  assert.equal(nodes.get("content").innerHTML.includes("暂无用户时间线记录"), true);
});

test("copy link reports a clear failure when both copy mechanisms are unavailable", async () => {
  const harness = loadHarness(
    async () => {
      throw new Error("copy must not request an API");
    },
    { fallbackCopyResult: false },
  );

  assert.equal(await harness.api.copyLink("/r/unavailable"), false);
  assert.equal(harness.fallbackCopyCalls, 2);
  assert.equal(harness.nodes.get("toast").textContent, "复制失败，请长按链接复制");
});
