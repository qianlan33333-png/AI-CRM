import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");


function walk(directory, predicate) {
  const result = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...walk(absolute, predicate));
    if (entry.isFile() && predicate(absolute)) result.push(absolute);
  }
  return result;
}


test("current templates reference existing shared scripts without duplicate page wiring", () => {
  const sourceRoot = path.join(root, "aicrm_next");
  const scripts = walk(sourceRoot, (file) => file.includes(`${path.sep}static${path.sep}`) && file.endsWith(".js"));
  const scriptNames = new Set(scripts.map((file) => path.basename(file)));
  const templates = walk(sourceRoot, (file) => file.includes(`${path.sep}templates${path.sep}`) && file.endsWith(".html"));
  let localReferenceCount = 0;
  for (const template of templates) {
    const source = readFileSync(template, "utf8");
    const scriptTags = [...source.matchAll(/<script\b[^>]*\bsrc=[\s\S]*?<\/script>/gi)].map((match) => match[0]);
    const localNames = scriptTags
      .filter((tag) => !/src\s*=\s*["'](?:https?:)?\/\//i.test(tag))
      .map((tag) => tag.match(/([A-Za-z0-9_.-]+\.js)\b/))
      .filter(Boolean)
      .map((match) => match[1]);
    assert.equal(new Set(localNames).size, localNames.length, `${path.relative(root, template)} has duplicate script sources`);
    for (const reference of localNames) {
      const match = reference.match(/([A-Za-z0-9_.-]+\.js)\b/);
      if (!match) continue;
      localReferenceCount += 1;
      assert.equal(scriptNames.has(match[1]), true, `${path.relative(root, template)} references missing ${match[1]}`);
    }
  }
  assert.ok(localReferenceCount > 20);
});


test("product editor wires configurable WeCom tagging through the shared picker", () => {
  const template = path.join(
    root,
    "aicrm_next/extensions/commerce/commerce/templates/wechat_products.html",
  );
  const source = readFileSync(template, "utf8");
  assert.match(source, /admin_console\/wecom_tag_picker\.js/);
  assert.match(source, /data-product-panel="tags"/);
  assert.match(source, /id="panel-tags" data-product-panel-content="tags"/);
  assert.match(source, /api\("\/api\/admin\/wecom\/tags"\)/);
  assert.match(source, /window\.AICRMWeComTagPicker\.open/);
  assert.match(source, /没有外部联系人 ID/);
  assert.match(source, /直接跳过且不进入重试队列/);
  const inlineScripts = [...source.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.ok(inlineScripts.length > 0);
  Function(inlineScripts.at(-1)[1]);
});


test("all media entrypoints share five-item lazy loading and bounded image downloads", () => {
  const loader = readFileSync(path.join(root, "aicrm_next/app/admin_console/static/admin_console/image_resource_loader.js"), "utf8");
  const picker = readFileSync(path.join(root, "aicrm_next/app/admin_console/static/admin_console/image_picker.js"), "utf8");
  const grid = readFileSync(path.join(root, "aicrm_next/app/admin_console/static/admin_console/image_library_grid.js"), "utf8");
  const library = readFileSync(path.join(root, "aicrm_next/app/admin_console/templates/admin_console/image_library.html"), "utf8");
  const sidebar = readFileSync(path.join(root, "aicrm_next/app/admin_console/static/sidebar_workbench/sidebar_workbench.js"), "utf8");
  assert.match(loader, /MAX_CONCURRENT = 2/);
  assert.match(loader, /RETRY_DELAYS_MS = \[1000, 2000, 4000\]/);
  assert.match(loader, /event\.isTrusted === false/);
  assert.match(loader, /cancelOutsideViewport/);
  assert.match(picker, /limit=5&offset=/);
  assert.match(picker, /pageSize: 5/);
  assert.match(picker, /data-picker-thumb-retry/);
  assert.match(grid, /typeof IntersectionObserver !== 'undefined'/);
  assert.match(grid, /else loadCardThumbnail\(card\)/);
  assert.match(grid, /error\.reason === 'outside_viewport'[\s\S]*?state\.thumbObserver\.observe\(card\)/);
  assert.match(grid, /data-image-thumb-retry/);
  assert.match(library, /params\.set\('limit', '5'\)/);
  assert.match(library, /pageSize: 5/);
  assert.match(sidebar, /limit: 5, offset: 0/);
  assert.match(sidebar, /pageSize: 5/);
  assert.match(sidebar, /endpoint\("contextTokenUrl"\)/);
});


test("lead QR copy is wired on standard, questionnaire, and service-period configuration pages", () => {
  const files = [
    "aicrm_next/extensions/commerce/commerce/templates/wechat_products.html",
    "aicrm_next/extensions/forms/questionnaire/templates/admin_console/questionnaire_operations.html",
    "aicrm_next/extensions/commerce/service_period/templates/service_period_products.html",
  ];
  for (const relative of files) {
    const source = readFileSync(path.join(root, relative), "utf8");
    assert.match(source, /leadQrTitle|qo-lead-qr-title/);
    assert.match(source, /leadQrSubtitle|qo-lead-qr-subtitle/);
  }
  const wiringSource = files
    .map((relative) => readFileSync(path.join(root, relative), "utf8"))
    .join("\n") + readFileSync(
      path.join(root, "aicrm_next/extensions/forms/questionnaire/static/questionnaire_operations.js"),
      "utf8",
    );
  assert.match(wiringSource, /lead_qr_title/g);
  assert.match(wiringSource, /lead_qr_subtitle/g);
});


test("public lead QR renderers consume configured copy with legacy fallbacks", () => {
  const modalSource = readFileSync(
    path.join(root, "aicrm_next/extensions/commerce/public_product/service.py"),
    "utf8",
  );
  assert.match(modalSource, /id="leadQrModalTitle">报名成功/);
  assert.match(modalSource, /id="leadQrModalSubtitle">扫码添加企微领取后续资料/);
  assert.match(modalSource, /leadQr\.title/);
  assert.match(modalSource, /leadQr\.subtitle/);

  const questionnaireSource = readFileSync(
    path.join(root, "aicrm_next/extensions/forms/questionnaire/static/questionnaire_completion_action.js"),
    "utf8",
  );
  assert.match(questionnaireSource, /leadQr\.title \|\| leadQr\.channel_name \|\| "扫码继续"/);
  assert.match(questionnaireSource, /leadQr\.subtitle \|\| "长按识别二维码，继续后续服务"/);
});
