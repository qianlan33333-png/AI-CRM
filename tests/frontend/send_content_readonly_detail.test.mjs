import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const componentSource = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/send_content_readonly_detail.js", import.meta.url),
  "utf8",
);
const detailTemplate = await readFile(
  new URL("../../aicrm_next/extensions/ai/ai_audience_ops/templates/admin_console/ai_audience_package_detail.html", import.meta.url),
  "utf8",
);
const assistantTemplate = await readFile(
  new URL("../../aicrm_next/app/admin_console/templates/admin_console/cloud_plan_review.html", import.meta.url),
  "utf8",
);
const assistantSource = await readFile(
  new URL("../../aicrm_next/app/admin_console/static/admin_console/cloud_plan_review.js", import.meta.url),
  "utf8",
);

function loadComponent() {
  const window = {};
  vm.runInNewContext(componentSource, { window });
  return window.AICRMSendContentReadonlyDetail;
}

test("full detail keeps complete copy and renders every attachment type", () => {
  const component = loadComponent();
  const longCopy = `第一段完整话术\n${"这段内容不会被截断。".repeat(30)}`;
  const html = component.renderFull({
    content_text: longCopy,
    content_basis_label: "发送任务冻结内容",
    attachment_basis_label: "实际发送附件",
    attachments: [
      { type: "image", type_label: "图片", name: "课程海报", thumbnail_url: "https://cdn.example.test/poster.png" },
      { type: "miniprogram", type_label: "小程序", name: "报名卡片" },
      { type: "attachment", type_label: "文件 / PDF", name: "课程手册.pdf" },
      { type: "group_invite", type_label: "客户群邀请", name: "加入学习群" },
    ],
  });

  assert.match(html, /完整话术/);
  assert.match(html, new RegExp(longCopy.slice(-40).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(html, /是，共 4 个/);
  for (const label of ["课程海报", "报名卡片", "课程手册.pdf", "加入学习群"]) {
    assert.match(html, new RegExp(label.replace(".", "\\.")));
  }
});

test("readonly renderer escapes content, marks deleted assets, and rejects unsafe thumbnails", () => {
  const html = loadComponent().renderFull({
    content_text: '<img src=x onerror="alert(1)">',
    attachments: [
      {
        type: "image",
        type_label: "图片",
        name: "已删除素材",
        availability: "missing",
        thumbnail_url: "javascript:alert(1)",
      },
    ],
  });

  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /素材已删除/);
  assert.doesNotMatch(html, /javascript:/);
});

test("readonly renderer allows same-origin API thumbnails and rejects protocol-relative URLs", () => {
  const component = loadComponent();
  const sameOrigin = component.renderFull({
    attachments: [{ type: "image", name: "同源缩略图", thumbnail_url: "/api/admin/image-library/12/thumbnail?size=160" }],
  });
  const protocolRelative = component.renderFull({
    attachments: [{ type: "image", name: "外部缩略图", thumbnail_url: "//evil.example.test/tracker.png" }],
  });

  assert.match(sameOrigin, /src="\/api\/admin\/image-library\/12\/thumbnail\?size=160"/);
  assert.doesNotMatch(protocolRelative, /evil\.example\.test/);
});

test("compact AI Assistant mode delegates to the shared readonly component", () => {
  assert.ok(assistantTemplate.indexOf("send_content_readonly_detail.js") < assistantTemplate.indexOf("cloud_plan_review.js"));
  for (const method of ["normalizeContentPackage", "taskToContentPackage", "summary", "renderCompact", "materialDetailText"]) {
    assert.match(assistantSource, new RegExp(`AICRMSendContentReadonlyDetail\\.${method}`));
  }
});

test("audience records are lazy loaded and render the shared full-detail drawer", () => {
  for (const marker of [
    'data-panel="records"',
    'id="panel-records"',
    "sendRecordsApiUrl",
    'key === "records" && !sendRecordsLoaded',
    "AICRMSendContentReadonlyDetail.renderFull(record)",
    'role="dialog"',
    'els.manualRefreshBtn.hidden = currentPanel === "records"',
    "暂无可准确追溯的发送记录",
    "发送记录加载失败",
  ]) {
    assert.match(detailTemplate, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.doesNotMatch(detailTemplate, /refreshSendRecordsBtn/);
});

test("audience detail inline controller remains valid JavaScript after Jinja rendering", () => {
  const inlineScripts = [...detailTemplate.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.ok(inlineScripts.length > 0);
  const renderedSource = inlineScripts.at(-1)[1].replace(/\{\{[\s\S]*?\}\}/g, '"/api/test"');
  assert.doesNotThrow(() => new vm.Script(renderedSource));
});
