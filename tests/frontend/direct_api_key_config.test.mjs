import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import vm from "node:vm";

const root = resolve(import.meta.dirname, "../..");
const source = readFileSync(resolve(root, "aicrm_next/app/admin_console/static/admin_console/direct_api_key_config.js"), "utf8");
const template = readFileSync(resolve(root, "aicrm_next/app/admin_console/templates/admin_console/config_api_key.html"), "utf8");

const window = {
  AdminApi: { errorMessage: (_error, fallback) => fallback },
  confirm: () => true,
};
const document = {
  addEventListener: () => {},
  querySelector: () => null,
};
vm.runInNewContext(source, { window, document, navigator: {}, console });

assert.equal(source.includes("localStorage"), false, "一次性 API Key 不得写入 localStorage");
assert.equal(source.includes("sessionStorage"), false, "一次性 API Key 不得写入 sessionStorage");
assert.match(source, /\/api\/admin\/config\/api-key\/generate/);
assert.match(source, /\/api\/admin\/config\/api-key\/rotate/);
assert.match(source, /enabled: false/);
assert.match(source, /payload\.api_key/);

assert.match(template, /data-api-key-secret-panel hidden/);
assert.match(template, /页面刷新后无法找回/);
assert.match(template, /不需要 Client ID/);
assert.match(template, /可查询开放接口，不允许调用写操作/);
assert.match(template, /can_manage_direct_api_key/);
assert.doesNotMatch(template, /can_manage_api_clients/);
assert.doesNotMatch(template, /<h1/i, "页面标题只能由后台统一 PageHeader 输出");

console.log("direct api key config frontend contract passed");
