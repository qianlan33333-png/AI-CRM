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
