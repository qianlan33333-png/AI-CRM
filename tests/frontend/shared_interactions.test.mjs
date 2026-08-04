import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");


function runBrowserScript(relativePath, extras = {}) {
  const absolute = path.join(root, relativePath);
  const context = { console, structuredClone, ...extras };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(readFileSync(absolute, "utf8"), context, { filename: absolute });
  return context;
}


test("automation selector disables capabilities owned by another current package", () => {
  const context = runBrowserScript(
    "aicrm_next/app/admin_console/static/admin_console/automation_capability_selector.js",
  );
  const container = {
    innerHTML: "",
    querySelectorAll() { return []; },
  };
  const selector = context.AutomationCapabilitySelector.mount(container, {
    currentPackageId: 7,
    value: 12,
    items: [
      { id: 12, automation_type: "agent", agent_name: "Current agent", status: "active", bound_package_id: 8, bound_package_name: "Other package" },
    ],
  });
  assert.match(container.innerHTML, /disabled/);
  assert.match(container.innerHTML, /Other package/);
  assert.equal(selector.getValue(), 12);
  selector.destroy();
  assert.equal(container.innerHTML, "");
});


test("member grid state keeps sorting and grouping mutually exclusive", () => {
  const context = runBrowserScript(
    "aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid_state.js",
  );
  const state = context.ServicePeriodMemberGridState;
  const sorted = state.addOrder(state.emptyConfig(), "sorts", "expires_at", "desc", { sorts: 2 });
  assert.equal(sorted.sorts[0].direction, "desc");
  assert.equal(state.isDirty(sorted, state.emptyConfig()), true);
  assert.throws(() => state.addOrder(sorted, "groups", "expires_at", "asc", { groups: 2 }), /other order/);
});


test("image pager requires a real user interaction before loading the second five items", async () => {
  const listeners = new Map();
  let intersectionCallback = null;
  const sentinel = {
    className: "",
    hidden: false,
    isConnected: true,
    parentNode: null,
    textContent: "",
    setAttribute() {},
    getBoundingClientRect() { return { top: 100, bottom: 120 }; },
    remove() { this.isConnected = false; },
  };
  const container = {
    appendChild(node) { node.parentNode = this; },
  };
  class FakeIntersectionObserver {
    constructor(callback) { intersectionCallback = callback; }
    observe() {}
    disconnect() {}
  }
  const context = runBrowserScript(
    "aicrm_next/app/admin_console/static/admin_console/image_resource_loader.js",
    {
      AbortController,
      IntersectionObserver: FakeIntersectionObserver,
      document: { createElement: () => sentinel, documentElement: { clientHeight: 800 } },
      innerHeight: 800,
      setTimeout,
      clearTimeout,
      addEventListener(name, callback) { listeners.set(name, callback); },
      removeEventListener(name) { listeners.delete(name); },
    },
  );
  const requests = [];
  const pager = context.ImageResourceLoader.createPager({
    container,
    pageSize: 5,
    cooldownMs: 1,
    async fetchPage(page) {
      requests.push({ limit: page.limit, offset: page.offset });
      return { items: Array(5).fill({}), total: 10, has_more: page.offset === 0, next_offset: page.offset + 5 };
    },
  });
  await pager.loadInitial();
  intersectionCallback([{ isIntersecting: true }]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, [{ limit: 5, offset: 0 }]);

  listeners.get("wheel")({ isTrusted: true });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(requests, [{ limit: 5, offset: 0 }, { limit: 5, offset: 5 }]);
  pager.destroy();
});
