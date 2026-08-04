import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const sourceRoot = path.join(repoRoot, "aicrm_next");
const sourceExtensions = new Set([".html", ".js", ".ts", ".tsx"]);
const embeddedFrontendFiles = new Set([
  path.join(sourceRoot, "extensions/commerce/public_product/service.py"),
]);
const safeFormatter = /formatApiError|formatErrorValue|responseErrorMessage|errorMessage|errorText|normalized\s*\(|friendlyErrors/;
const payloadReference = String.raw`\b(?:payload|data|result|body|json|d2?|respData)\.(?:detail|error|message|errors)\b`;
const unsafePatterns = [
  new RegExp(`new Error\\([^\\n]*${payloadReference}`),
  new RegExp(`String\\(\\s*${payloadReference}`),
  new RegExp(`(?:textContent\\s*=|innerHTML\\s*=|alert\\s*\\(|showToast\\s*\\(|showError\\s*\\()[^\\n]*${payloadReference}`),
  new RegExp(`\\$\\{[^\\n]*${payloadReference}`),
];
const rawMachineErrorPatterns = [
  /return\s+(?:data|payload|result|body|json)\.(?:error|reason|error_code)\b/,
  /return\s+detail\.(?:error|reason|message|error_code)\b/,
  /return\s+detail\.(?:reason|error|message|error_code)\s*\|\|/,
  /return\s+JSON\.stringify\(value\)/,
  /payload\.error_code\s*\?\s*`/,
  /detail\.error_code/,
  /:\s*\(typeof payload\.error === ["']string["'][^\n]*payload\.error\)/,
  /if\s*\(typeof value === ["']string["']\)\s*return value(?:\.trim\(\))?/,
];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(absolute));
    else if (sourceExtensions.has(path.extname(entry.name)) || embeddedFrontendFiles.has(absolute)) files.push(absolute);
  }
  return files;
}

const violations = [];
for (const file of await walk(sourceRoot)) {
  const relative = path.relative(repoRoot, file);
  const lines = (await readFile(file, "utf8")).split(/\r?\n/);
  lines.forEach((line, index) => {
    if (unsafePatterns.some((pattern) => pattern.test(line)) && !safeFormatter.test(line)) {
      violations.push(`${relative}:${index + 1}: ${line.trim()}`);
    }
    if (relative !== "aicrm_next/app/admin_console/static/admin_console/admin_api_client.js"
      && rawMachineErrorPatterns.some((pattern) => pattern.test(line))) {
      violations.push(`${relative}:${index + 1}: raw machine error bypass`);
    }
    if (/\.catch\(\s*\(\)\s*=>\s*\{\s*\}\s*\)/.test(line)
      && relative !== "aicrm_next/app/admin_console/templates/questionnaire_h5_page.html") {
      violations.push(`${relative}:${index + 1}: silent catch`);
    }
  });
}

assert.deepEqual(violations, [], `发现可能把结构化错误直接渲染到页面的代码：\n${violations.join("\n")}`);
console.log("admin error rendering guard passed");
