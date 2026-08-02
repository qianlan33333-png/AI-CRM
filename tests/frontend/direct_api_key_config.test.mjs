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
const navigator = {};
vm.runInNewContext(source, { window, document, navigator, console });

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
assert.match(template, /direct-api-key-copy-fix-v2/, "复制修复必须更新静态资源版本，避免浏览器继续使用旧脚本");
assert.doesNotMatch(template, /<h1/i, "页面标题只能由后台统一 PageHeader 输出");

let resolveClipboardWrite;
const clipboardWrite = new Promise((resolve) => {
  resolveClipboardWrite = resolve;
});
navigator.clipboard = { writeText: () => clipboardWrite };

let copyClickHandler;
const copyButton = {
  textContent: "复制 Key",
  addEventListener: (_eventName, handler) => {
    copyClickHandler = handler;
  },
};
const secretInput = { value: "aics_test_key", type: "password" };
const alertNode = { textContent: "", hidden: true };
const elements = new Map([
  ["[data-api-key-secret-panel]", { hidden: true }],
  ["[data-api-key-value]", secretInput],
  ["[data-api-key-alert]", alertNode],
  ["[data-copy-api-key]", copyButton],
]);
window.DirectApiKeyConfig.init({ querySelector: (selector) => elements.get(selector) || null });

const event = { currentTarget: copyButton };
const copyResult = copyClickHandler(event);
event.currentTarget = null;
resolveClipboardWrite();
await copyResult;

assert.equal(copyButton.textContent, "已复制", "异步复制后必须使用预先保存的按钮引用");
assert.equal(alertNode.hidden, true, "成功复制不应显示错误提示");

console.log("direct api key config frontend contract passed");
