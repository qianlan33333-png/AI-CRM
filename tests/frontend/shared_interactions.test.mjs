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
