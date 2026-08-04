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
