import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const clientPath = path.join(root, "aicrm_next/app/admin_console/static/admin_console/admin_api_client.js");


function loadClient() {
  const calls = [];
  const grants = JSON.stringify({
    "POST /api/admin/customers/{customer_id}/tags": "action-current",
  });
  const context = {
    URL,
    URLSearchParams,
    Headers,
    FormData,
    console,
    location: { href: "https://crm.current.test/admin", origin: "https://crm.current.test" },
    document: {
      cookie: "aicrm_next_csrf=csrf-current",
      addEventListener() {},
      createElement() { return {}; },
      getElementById(id) {
        return id === "aicrmAdminActionGrants" ? { textContent: grants } : null;
      },
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, saved: true }) };
    },
  };
  context.window = context;
  vm.runInNewContext(readFileSync(clientPath, "utf8"), context, { filename: clientPath });
  return { api: context.AdminApi, calls };
}


function headerValue(headers, expectedName) {
  const key = Object.keys(headers).find((name) => name.toLowerCase() === expectedName.toLowerCase());
  return key ? headers[key] : undefined;
}


test("shared request client serializes JSON and binds unsafe requests to CSRF and action grants", async () => {
  const { api, calls } = loadClient();
  const payload = await api.requestJson("/api/admin/customers/customer-current/tags", {
    method: "POST",
    body: { tag_id: "tag-current" },
  });
  assert.equal(payload.saved, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(headerValue(calls[0].options.headers, "Content-Type"), "application/json");
  assert.equal(headerValue(calls[0].options.headers, "X-CSRF-Token"), "csrf-current");
  assert.equal(headerValue(calls[0].options.headers, "X-Admin-Action-Token"), "action-current");
  assert.deepEqual(JSON.parse(calls[0].options.body), { tag_id: "tag-current" });
});


test("shared error normalization produces one current user-facing message", () => {
  const { api } = loadClient();
  const normalized = api.normalizeApiError(
    { status: 422 },
    { detail: [{ loc: ["body", "mobile"], msg: "field required" }] },
    { fieldLabels: { mobile: "手机号" } },
  );
  assert.equal(normalized.message, "手机号：必填");
  assert.equal(normalized.fieldErrors[0].field, "mobile");
});
