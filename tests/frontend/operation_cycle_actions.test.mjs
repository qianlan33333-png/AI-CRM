import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");


function source(relative) {
  return readFileSync(path.join(root, relative), "utf8");
}


test("operation cycle list exposes one current action and no retired controls", () => {
  const template = source(
    "aicrm_next/app/admin_console/templates/admin_shell/operation_cycles_list.html",
  );
  const script = source(
    "aicrm_next/extensions/hxc/operation_cycles/static/operation_cycles_actions.js",
  );

  assert.equal((template.match(/data-operation-action-start/g) || []).length, 1);
  assert.doesNotMatch(template, /暂停|删除/);
  assert.match(template, /operation-cycle-task-detail-link/);
  assert.match(template, /operation_cycles_actions\.js/);
  assert.match(script, /\/actions\/.*\/start/);
  assert.match(script, /已在本地 Codex 创建任务/);
  assert.doesNotMatch(script, /步骤|Excel 内容|原始对话/);
});


test("operation cycle detail shows final conclusions without an editor", () => {
  const template = source(
    "aicrm_next/app/admin_console/templates/admin_shell/operation_cycles_strategy.html",
  );
  assert.match(template, /operation-cycle-final-results/);
  assert.match(template, /最近执行结论/);
  assert.match(template, /AI 助手/);
  assert.match(template, /operation-cycle-formal-skill/);
  assert.doesNotMatch(template, /data-operation-skill-editor/);
});


test("AI assistant approval language is confirmation and send", () => {
  const template = source(
    "aicrm_next/app/admin_console/templates/admin_console/cloud_plan_review.html",
  );
  const script = source(
    "aicrm_next/app/admin_console/static/admin_console/cloud_plan_review.js",
  );
  assert.match(template, /确认并发送/);
  assert.match(script, /已开始发送/);
  assert.match(script, /计划已确认并开始发送/);
  assert.doesNotMatch(template, /批准并开始执行/);
});
