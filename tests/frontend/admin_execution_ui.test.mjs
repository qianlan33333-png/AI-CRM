import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(
  new URL("../../aicrm_next/frontend_compat/static/admin_console/admin_execution_ui.js", import.meta.url),
  "utf8",
);

for (const forbidden of [
  "/api/admin/push-center/stats",
  'addEventListener("input"',
  "setInterval",
  "window.prompt",
  "admin_action_token",
]) {
  assert.equal(source.includes(forbidden), false, `${forbidden} must not be used by the execution UI`);
}
assert.equal(source.match(/\/api\/admin\/push-center\/jobs\?/g)?.length, 1);
assert.equal(source.includes("AdminApi"), true);
assert.equal(source.includes("AdminFmt"), true);
assert.equal(source.includes("duplicate_risk_confirmed"), true);
assert.equal(source.includes("actor: confirmation.actor"), true);
assert.equal(source.includes("expected_version: confirmation.expectedVersion"), true);

const window = {
  AdminApi: {
    escapeHtml(value) {
      return String(value).replace(/</g, "&lt;");
    },
  },
  AdminFmt: { localTime: (value) => `local:${value}` },
  location: { pathname: "/admin/push-center", search: "" },
  history: { replaceState() {} },
};
const document = {
  readyState: "complete",
  querySelector() { return null; },
};

vm.runInNewContext(source, {
  window,
  document,
  URL,
  URLSearchParams,
  FormData,
  console,
  encodeURIComponent,
});

const ui = window.AdminExecutionUI;
assert.equal(typeof ui.requestVersionedQueueCommand, "function");
assert.equal(typeof ui.readJson, "function");
assert.equal(ui.statusTone("sent"), "ok");
assert.equal(ui.statusTone("failed_terminal"), "danger");
assert.equal(ui.statusTone("unknown_after_dispatch"), "warn");
assert.equal(ui.escapeHtml("<secret>"), "&lt;secret>");
assert.equal(ui.csvCell("=HYPERLINK(\"https://evil.invalid\")"), '"\'=HYPERLINK(""https://evil.invalid"")"');
assert.equal(ui.csvCell("  +1+1"), '"\'  +1+1"');
assert.equal(ui.csvCell({ safe: "value" }), '"{""safe"":""value""}"');
assert.match(ui.QueueStateBadge("waiting", "waiting_for_lane_capacity"), /正常排队/);
const capacity = ui.CapacitySummary({ lanes: [{ lane: "wecom_bulk", max_in_flight: 1, in_flight: 0, held: 2, policy_gated: 3 }] }, ["wecom_bulk"]);
assert.match(capacity, /wecom_bulk/);
assert.match(capacity, /Policy Gate/);
assert.match(capacity, /2 \/ 3/);
assert.match(ui.ExecutionTimeline({ items: [{ item_kind: "external_effect", item_type: "wecom.message.group.send", status: "queued" }] }), /wecom\.message\.group\.send/);
assert.match(source, /Placeholder Consumers \(not actionable\)/);

const params = ui.formParams(null, { section: "group_ops", offset: 0, empty: "" });
assert.equal(params.get("section"), "group_ops");
assert.equal(params.get("offset"), "0");
assert.equal(params.has("empty"), false);

console.log("admin execution UI contract passed");
