import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import vm from "node:vm";

const root = resolve(import.meta.dirname, "../..");
const source = readFileSync(resolve(root, "aicrm_next/app/admin_console/static/admin_console/api_client_config.js"), "utf8");
const detailTemplate = readFileSync(resolve(root, "aicrm_next/app/admin_console/templates/admin_console/config_api_client_detail.html"), "utf8");
const listTemplate = readFileSync(resolve(root, "aicrm_next/app/admin_console/templates/admin_console/config_api_clients.html"), "utf8");

const window = {
  AdminApi: { errorMessage: (_error, fallback) => fallback },
};
const document = {
  addEventListener: () => {},
  querySelector: () => null,
};
vm.runInNewContext(source, { window, document, navigator: {}, FormData: class FormData {} });

assert.deepEqual(
  Array.from(window.ApiClientConfig.splitCidrs("203.0.113.1/32\n2001:db8::/64, 10.0.0.0/8")),
  ["203.0.113.1/32", "2001:db8::/64", "10.0.0.0/8"],
);
assert.equal(source.includes("localStorage"), false, "一次性 Secret 不得写入 localStorage");
assert.equal(source.includes("sessionStorage"), false, "一次性 Secret 不得写入 sessionStorage");
assert.match(source, /state\.secret = ""/);
assert.match(source, /\/activate/);
assert.match(source, /\/rotate-secret/);
assert.match(source, /enabled: false/);
assert.match(source, /openConfirmation\("rotate"/);
assert.match(source, /data-client-secret-modal/);
assert.match(source, /credential_hint/);
assert.doesNotMatch(source, /global\.confirm/);

assert.match(detailTemplate, /data-client-current/);
assert.match(detailTemplate, /当前 Client Secret/);
assert.match(detailTemplate, /data-client-secret-modal hidden/);
assert.match(detailTemplate, /关闭后无法再次查看完整值/);
assert.match(detailTemplate, /data-client-credential-hint/);
assert.match(detailTemplate, /轮换 Secret/);
assert.match(detailTemplate, /client\.enabled or client\.system_managed/);
assert.match(detailTemplate, /系统预置客户端/);
assert.doesNotMatch(detailTemplate, /<h1/i, "页面标题只能由后台统一 PageHeader 输出");
assert.doesNotMatch(listTemplate, /<h1/i, "列表页标题只能由后台统一 PageHeader 输出");
assert.match(listTemplate, /新建客户端/);
assert.match(listTemplate, /管理 Secret/);
assert.match(listTemplate, /查看状态/);

console.log("api client config frontend contract passed");
