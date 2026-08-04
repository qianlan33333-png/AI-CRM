import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");


function currentStaticScripts(directory) {
  const scripts = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) scripts.push(...currentStaticScripts(absolute));
    if (entry.isFile() && entry.name.endsWith(".js") && absolute.includes(`${path.sep}static${path.sep}`)) {
      scripts.push(absolute);
    }
  }
  return scripts;
}


test("every current shared or page script parses with the active Node runtime", () => {
  const sourceRoot = path.join(root, "aicrm_next");
  assert.equal(statSync(sourceRoot).isDirectory(), true);
  const scripts = currentStaticScripts(sourceRoot).sort();
  assert.ok(scripts.length > 20);
  for (const script of scripts) {
    execFileSync(process.execPath, ["--check", script], { cwd: root, stdio: "pipe" });
  }
});
