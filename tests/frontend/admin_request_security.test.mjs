import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/admin_api_client.js", import.meta.url),
  "utf8",
);

const captured = [];
const tokens = {
  "POST /api/admin/external-effects/jobs/{job_id}/retry": "bound-retry-token",
  "POST /setup/wizard/save": "bound-wizard-token",
};
const tokenNode = { textContent: JSON.stringify(tokens) };
const document = {
  cookie: "aicrm_next_csrf=csrf-from-cookie; preference=compact",
  getElementById(id) {
    return id === "aicrmAdminActionGrants" ? tokenNode : null;
  },
  addEventListener() {},
  createElement() {
    return { type: "", name: "", value: "" };
  },
};
const window = {
  location: {
    href: "https://crm.example.test/admin/jobs",
    origin: "https://crm.example.test",
  },
  fetch: async (input, options) => {
    captured.push({ input, options });
    return { ok: true, status: 200, text: async () => "{}" };
  },
};

vm.runInNewContext(source, {
  window,
  document,
  URL,
  URLSearchParams,
  FormData,
  Headers,
  decodeURIComponent,
  encodeURIComponent,
});

await window.fetch("/api/admin/external-effects/jobs/42/retry", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Admin-Action-Token": "",
  },
  body: "{}",
});

assert.equal(captured[0].options.headers["X-CSRF-Token"], "csrf-from-cookie");
assert.equal(captured[0].options.headers["X-Admin-Action-Token"], "bound-retry-token");
assert.deepEqual(
  Object.entries(captured[0].options.headers).filter(([name]) => name.toLowerCase() === "x-admin-action-token"),
  [["X-Admin-Action-Token", "bound-retry-token"]],
);
assert.equal(
  window.AdminApi.actionToken("POST", "/api/admin/external-effects/jobs/99/retry"),
  "bound-retry-token",
);

await window.fetch("https://outside.example.test/write", { method: "POST" });
assert.equal(captured[1].options.headers["X-CSRF-Token"], undefined);
assert.equal(captured[1].options.headers["X-Admin-Action-Token"], undefined);

const inputs = {};
const form = {
  method: "POST",
  action: "https://crm.example.test/setup/wizard/save",
  querySelector(selector) {
    return inputs[selector] || null;
  },
  appendChild(input) {
    inputs[`input[name="${input.name}"]`] = input;
  },
};
window.AdminApi.prepareUnsafeForm(form);

assert.equal(inputs['input[name="csrf_token"]'].value, "csrf-from-cookie");
assert.equal(inputs['input[name="admin_action_token"]'].value, "bound-wizard-token");

console.log("admin request security contract passed");
