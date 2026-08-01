import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const selectorSource = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/automation_capability_selector.js", import.meta.url),
  "utf8",
);
const listTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/ai_audience_ops/templates/admin_console/ai_audience_package_list.html", import.meta.url),
  "utf8",
);
const detailTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/ai_audience_ops/templates/admin_console/ai_audience_package_detail.html", import.meta.url),
  "utf8",
);
const automationEditTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_edit.html", import.meta.url),
  "utf8",
);

function loadSelector() {
  const window = {};
  vm.runInNewContext(selectorSource, { window });
  return window.AutomationCapabilitySelector;
}

function createContainer() {
  const tabListeners = new Map();
  return {
    innerHTML: "",
    tabListeners,
    querySelectorAll(selector) {
      if (selector === "[data-capability-type]") {
        return ["agent", "fixed_script"].map((capabilityType) => ({
          dataset: { capabilityType },
          addEventListener(event, listener) {
            assert.equal(event, "click");
            tabListeners.set(capabilityType, listener);
          },
        }));
      }
      return [];
    },
  };
}

test("selector filters capability types and enforces binding availability", () => {
  const container = createContainer();
  const selector = loadSelector();
  selector.mount(container, {
    currentPackageId: 10,
    value: 4,
    items: [
      { id: 1, agent_name: "可用 Agent", automation_type: "agent", status: "active" },
      { id: 2, agent_name: "停止 Agent", automation_type: "agent", status: "paused" },
      { id: 3, agent_name: "占用 Agent", automation_type: "agent", status: "active", bound_package_id: 11, bound_package_name: "其他人群包" },
      { id: 4, agent_name: "当前 Agent", automation_type: "agent", status: "active", bound_package_id: 10, bound_package_name: "当前包" },
      { id: 5, agent_name: "固定欢迎语", automation_type: "fixed_script", status: "active" },
    ],
  });

  assert.match(container.innerHTML, /Agent 机器人/);
  assert.match(container.innerHTML, /可用 Agent/);
  assert.match(container.innerHTML, /is-disabled[\s\S]*停止 Agent/);
  assert.match(container.innerHTML, /is-disabled[\s\S]*占用 Agent/);
  assert.match(container.innerHTML, /已绑定「其他人群包」/);
  assert.match(container.innerHTML, /当前 Agent/);
  assert.match(container.innerHTML, /已绑定当前人群包/);
  assert.doesNotMatch(container.innerHTML, /固定欢迎语/);

  container.tabListeners.get("fixed_script")();
  assert.match(container.innerHTML, /固定欢迎语/);
  assert.doesNotMatch(container.innerHTML, /可用 Agent/);
});

test("audience pages expose group master-detail and capability binding without webhook configuration", () => {
  for (const expected of ["人群包分组", "未分组", "新增分组", "编辑组名", "group_id"]) {
    assert.match(listTemplate, new RegExp(expected));
  }
  assert.doesNotMatch(listTemplate, /搜索人群包|type="search"|id="search/);

  for (const expected of ["所属分组", "自动化话术能力", "AutomationCapabilitySelector", "bindingApiUrl"]) {
    assert.match(detailTemplate, new RegExp(expected));
  }
  assert.doesNotMatch(detailTemplate, /Webhook 地址|发送地址|接收地址|send_webhook_url|receive_webhook_url/);

  assert.match(automationEditTemplate, /当前绑定人群包/);
  assert.doesNotMatch(automationEditTemplate, /send_webhook_url|receive_webhook_url|发送地址|接收地址/);
});

test("selector opens on the type of the current fixed-script binding", () => {
  const container = createContainer();
  loadSelector().mount(container, {
    currentPackageId: 10,
    value: 7,
    items: [
      { id: 6, agent_name: "普通 Agent", automation_type: "agent", status: "active" },
      { id: 7, agent_name: "当前固定话术", automation_type: "fixed_script", status: "active", bound_package_id: 10 },
    ],
  });

  assert.match(container.innerHTML, /data-capability-type="fixed_script" aria-selected="true"/);
  assert.match(container.innerHTML, /当前固定话术/);
  assert.doesNotMatch(container.innerHTML, /普通 Agent/);
});

test("binding controller loads, saves, and unbinds through the internal binding API", async () => {
  const container = createContainer();
  const requests = [];
  const statuses = [];
  const unbindButton = { disabled: true };
  let binding = { automation_id: 4, automation_name: "当前 Agent", agent_code: "agent_4" };
  const fetchJson = async (url, options = {}) => {
    requests.push({ url, method: options.method || "GET", body: options.body });
    if (url.startsWith("/agents")) {
      return { items: [{ id: 4, agent_name: "当前 Agent", automation_type: "agent", status: "active", bound_package_id: binding ? 10 : null }] };
    }
    if (options.method === "PUT") {
      assert.equal(options.body.automation_id, 4);
      assert.deepEqual(Object.keys(options.body), ["automation_id"]);
      binding = { automation_id: 4, automation_name: "当前 Agent", agent_code: "agent_4" };
    } else if (options.method === "DELETE") {
      binding = null;
    }
    return { binding };
  };
  const controller = loadSelector().createBindingController(container, {
    automationAgentsApiUrl: "/agents",
    bindingApiUrl: "/binding",
    currentPackageId: 10,
    fetchJson,
    confirm: () => true,
    unbindButton,
    setStatus: (message, tone) => statuses.push({ message, tone }),
  });

  await controller.load();
  assert.equal(controller.getBinding().automation_id, 4);
  assert.equal(unbindButton.disabled, false);
  await controller.save();
  await controller.unbind();

  assert.equal(controller.getBinding(), null);
  assert.equal(unbindButton.disabled, true);
  assert.deepEqual(requests.filter((item) => item.method !== "GET").map((item) => item.method), ["PUT", "DELETE"]);
  assert.deepEqual(statuses.at(-1), { message: "绑定已解除", tone: "success" });
});
