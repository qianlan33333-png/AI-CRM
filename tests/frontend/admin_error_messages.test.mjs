import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/admin_api_client.js", import.meta.url),
  "utf8",
);

const responses = [];
const nodes = new Map();
const document = {
  cookie: "",
  getElementById(id) { return nodes.get(id) || null; },
  addEventListener() {},
};
const window = {
  location: {
    href: "https://crm.example.test/admin/products",
    origin: "https://crm.example.test",
  },
  fetch: async () => {
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  },
};

const context = {
  window,
  document,
  URL,
  URLSearchParams,
  FormData,
  Headers,
  decodeURIComponent,
  encodeURIComponent,
};
context.fetch = (...args) => context.window.fetch(...args);
vm.runInNewContext(source, context);

const { AdminApi } = window;
const pydanticPayload = {
  detail: [
    { type: "missing", loc: ["body", "title"], msg: "Field required", input: {} },
    { type: "missing", loc: ["body", "product_code"], msg: "Field required", input: {} },
  ],
};
const normalized = AdminApi.normalizeApiError(
  { status: 422 },
  pydanticPayload,
  { fieldLabels: { title: "商品名称", product_code: "商品编码" } },
);

assert.equal(normalized.message, "商品名称：必填；商品编码：必填");
assert.equal(JSON.stringify(normalized.fieldErrors.map(({ field, label, message }) => ({ field, label, message }))), JSON.stringify([
  { field: "title", label: "商品名称", message: "必填" },
  { field: "product_code", label: "商品编码", message: "必填" },
]));
assert.equal(normalized.message.includes("[object Object]"), false);

assert.equal(
  AdminApi.formatErrorValue([{ message: "第一项失败" }, { detail: "第二项失败" }]),
  "第一项失败；第二项失败",
);
assert.equal(
  AdminApi.formatErrorValue({ error: { code: "permission_denied", message: "should not leak" } }),
  "当前账号没有执行此操作的权限",
);
assert.equal(
  AdminApi.errorMessage({ message: "[object Object]", payload: pydanticPayload, status: 422 }, "保存失败", {
    fieldLabels: { title: "商品名称", product_code: "商品编码" },
  }),
  "商品名称：必填；商品编码：必填",
);
assert.equal(
  AdminApi.normalizeApiError(null, { ok: false, error: "unknown_internal_code" }, { fallback: "保存失败" }).message,
  "保存失败",
);

let focusedInput = "";
let reportedValidity = 0;
let displayedValidation = "";
function fakeInput(id, validity, label) {
  const classes = new Set();
  const attributes = new Map();
  const container = { classList: { add: (value) => classes.add(value), remove: (value) => classes.delete(value) } };
  return {
    id,
    validity,
    labels: [{ textContent: label }],
    minLength: 3,
    maxLength: 120,
    min: "0",
    checkValidity: () => false,
    closest: () => container,
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
    focus: () => { focusedInput = id; },
    classes,
    attributes,
  };
}
const titleInput = fakeInput("productTitle", { valueMissing: true }, "商品名称");
const codeInput = fakeInput("productCode", { valueMissing: true }, "商品编码");
const titleError = { textContent: "" };
const codeError = { textContent: "" };
const form = {
  elements: [titleInput, codeInput],
  checkValidity: () => false,
  reportValidity: () => { reportedValidity += 1; },
};
nodes.set("productTitle", titleInput);
nodes.set("productCode", codeInput);
nodes.set("productTitleError", titleError);
nodes.set("productCodeError", codeError);
const formErrors = AdminApi.createFormErrorController({
  form,
  fieldLabels: { title: "商品名称", product_code: "商品编码" },
  fieldIds: { title: "productTitle", product_code: "productCode" },
  validationPrefix: "商品未保存",
  showMessage: (message) => { displayedValidation = message; },
});
assert.equal(formErrors.validate(), false);
assert.equal(displayedValidation, "商品未保存：商品名称：必填；商品编码：必填");
assert.equal(titleError.textContent, "必填");
assert.equal(codeError.textContent, "必填");
assert.equal(titleInput.attributes.get("aria-invalid"), "true");
assert.equal(focusedInput, "productTitle");
assert.equal(reportedValidity, 1);

responses.push({ ok: false, status: 413, statusText: "Payload Too Large", text: async () => "<html>nginx</html>" });
await assert.rejects(
  AdminApi.requestJson("/api/admin/image-library/upload", { method: "POST", body: "binary" }),
  (error) => error.message === "提交内容过大，请压缩后重试" && error.status === 413,
);

responses.push({ ok: false, status: 500, statusText: "Internal Server Error", text: async () => "database password leaked" });
await assert.rejects(
  AdminApi.requestJson("/api/admin/products"),
  (error) => error.message === "服务暂时不可用，请稍后重试" && !error.message.includes("database"),
);

responses.push(new TypeError("Failed to fetch"));
await assert.rejects(
  AdminApi.requestJson("/api/admin/products"),
  (error) => error.message === "网络连接异常，请检查网络后重试",
);

console.log("admin error message contract passed");
