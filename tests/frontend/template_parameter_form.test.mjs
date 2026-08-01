import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const formSource = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/template_parameter_form.js", import.meta.url),
  "utf8",
);
const detailTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/ai_audience_ops/templates/admin_console/ai_audience_package_detail.html", import.meta.url),
  "utf8",
);
const listTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/ai_audience_ops/templates/admin_console/ai_audience_package_list.html", import.meta.url),
  "utf8",
);

test("template parameter form is a reusable schema-driven component", () => {
  assert.doesNotThrow(() => new vm.Script(formSource));
  for (const contract of [
    "setSchema(fields",
    "field.type === \"condition_list\"",
    "field.type === \"enum\"",
    "field.type === \"boolean\"",
    "reference_list",
    "visible_when",
    "getValue(options",
    "datetimeLocalValue",
  ]) {
    assert.match(formSource, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(formSource, /input\.disabled = this\.readOnly/);
  assert.match(formSource, /TemplateParameterForm/);
});

test("audience detail keeps one title and supports preview save historical and active-readonly states", () => {
  assert.equal((detailTemplate.match(/id="packageTitle"/g) || []).length, 1);
  for (const contract of [
    "模板与筛选条件",
    "templateParameterForm",
    "template-preview",
    "template-config",
    "重新预览",
    "保存新版本",
    "历史配置",
  ]) {
    assert.match(detailTemplate, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(formSource, /packageInfo\.status === "active"/);
  assert.match(formSource, /readOnly: active/);
  assert.match(detailTemplate, /createPackageController/);
});

test("audience list only labels template source beneath the package name", () => {
  assert.match(listTemplate, /aud-template-tag/);
  assert.match(listTemplate, /item\.template_label\|\|"历史配置"/);
  assert.doesNotMatch(listTemplate, /templateParameterForm|保存新版本/);
});
