(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function parsePayloadObject(raw) {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw;
    if (typeof raw === "string" && raw) {
      try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      } catch (_error) {
        return {};
      }
    }
    return {};
  }

  function normalizeIdList(value) {
    const raw = Array.isArray(value) ? value : String(value || "").split(",");
    const ids = [];
    raw.forEach((item) => {
      const id = parseInt(String(item).trim(), 10);
      if (id > 0 && ids.indexOf(id) === -1) ids.push(id);
    });
    return ids;
  }

  function normalizeContentPackage(value) {
    const data = value && typeof value === "object" ? value : {};
    return {
      content_text: String(data.content_text || "").trim(),
      image_library_ids: normalizeIdList(data.image_library_ids),
      miniprogram_library_ids: normalizeIdList(data.miniprogram_library_ids),
      attachment_library_ids: normalizeIdList(data.attachment_library_ids),
      group_invite_library_ids: normalizeIdList(data.group_invite_library_ids),
      dynamic_miniprogram_card: parsePayloadObject(data.dynamic_miniprogram_card),
    };
  }

  function taskToContentPackage(task) {
    const current = task || {};
    const payload = parsePayloadObject(current.content_payload || current.content_payload_json);
    const contentPackage = parsePayloadObject(payload.content_package || current.content_package);
    return normalizeContentPackage({
      content_text: current.content_text || contentPackage.content_text || payload.content_text || "",
      image_library_ids: contentPackage.image_library_ids || payload.image_library_ids || [],
      miniprogram_library_ids: contentPackage.miniprogram_library_ids || payload.miniprogram_library_ids || [],
      attachment_library_ids: contentPackage.attachment_library_ids || payload.attachment_library_ids || [],
      group_invite_library_ids: contentPackage.group_invite_library_ids || payload.group_invite_library_ids || [],
      dynamic_miniprogram_card: contentPackage.dynamic_miniprogram_card || payload.dynamic_miniprogram_card || null,
    });
  }

  function summary(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    const body = normalized.content_text || "";
    return {
      text: body ? (body.length > 80 ? `${body.slice(0, 80)}...` : body) : "未配置话术",
      imageCount: normalized.image_library_ids.length,
      miniprogramCount: normalized.miniprogram_library_ids.length + (Object.keys(normalized.dynamic_miniprogram_card).length ? 1 : 0),
      attachmentCount: normalized.attachment_library_ids.length,
      groupInviteCount: normalized.group_invite_library_ids.length,
    };
  }

  function renderCompact(contentPackage) {
    const contentSummary = summary(contentPackage);
    const total = contentSummary.imageCount + contentSummary.miniprogramCount + contentSummary.attachmentCount + contentSummary.groupInviteCount;
    return `
      <div class="cloud-plan-task-content-summary" data-task-content-summary>
        <strong>话术摘要：</strong><span>${escapeHtml(contentSummary.text)}</span>
        <strong>内容数量：</strong><span>图片 ${contentSummary.imageCount} / 小程序 ${contentSummary.miniprogramCount} / 附件 ${contentSummary.attachmentCount} / 客户群 ${contentSummary.groupInviteCount}</span>
        <strong>素材明细：</strong><span data-task-material-detail>${total ? "素材信息加载中" : "无素材"}</span>
      </div>
    `;
  }

  function materialDetailText(materials, contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    const rows = Array.isArray(materials) ? materials : [];
    if (!rows.length) {
      const fallback = [];
      normalized.image_library_ids.forEach((id) => fallback.push(`图片 #${id}`));
      normalized.miniprogram_library_ids.forEach((id) => fallback.push(`小程序 #${id}`));
      normalized.attachment_library_ids.forEach((id) => fallback.push(`附件 #${id}`));
      normalized.group_invite_library_ids.forEach((id) => fallback.push(`群邀请 #${id}`));
      if (Object.keys(normalized.dynamic_miniprogram_card).length) fallback.push("动态小程序卡片");
      return fallback.join(" / ") || "无素材";
    }
    return rows.map((item) => {
      const type = String(item.type || "");
      const title = String(item.title || item.name || "").trim() || `${type || "素材"} #${item.library_id || ""}`;
      const subtitle = String(item.subtitle || item.description || "").trim();
      if (type === "miniprogram") return `小程序：${title}${subtitle ? `（${subtitle}）` : ""}`;
      if (type === "image") return `图片：${title}${subtitle ? `（${subtitle}）` : ""}`;
      if (type === "group_invite") return `客户群邀请：${title}${subtitle ? `（${subtitle}）` : ""}`;
      return `附件：${title}${subtitle ? `（${subtitle}）` : ""}`;
    }).join(" / ");
  }

  function safeThumbnail(value) {
    const url = String(value || "").trim();
    if (/^https?:\/\//i.test(url) || (url.startsWith("/") && !url.startsWith("//"))) return url;
    return "";
  }

  function renderAttachment(item) {
    const current = item && typeof item === "object" ? item : {};
    const thumbnail = safeThumbnail(current.thumbnail_url);
    const availability = String(current.availability || "available");
    const state = availability === "missing" ? '<span class="send-readonly-missing">素材已删除</span>' : "";
    return `
      <article class="send-readonly-attachment">
        ${thumbnail ? `<img src="${escapeHtml(thumbnail)}" alt="" loading="lazy">` : `<div class="send-readonly-attachment-icon">${escapeHtml(String(current.type_label || "附件").slice(0, 2))}</div>`}
        <div class="send-readonly-attachment-body">
          <div class="send-readonly-attachment-type">${escapeHtml(current.type_label || "附件")}${state}</div>
          <strong>${escapeHtml(current.name || "未命名素材")}</strong>
          ${current.description ? `<p>${escapeHtml(current.description)}</p>` : ""}
        </div>
      </article>
    `;
  }

  function renderFull(detail) {
    const current = detail && typeof detail === "object" ? detail : {};
    const attachments = Array.isArray(current.attachments) ? current.attachments : [];
    const contentText = String(current.content_text || "");
    return `
      <section class="send-readonly-detail" aria-label="发送内容详情">
        <div class="send-readonly-section">
          <div class="send-readonly-label">完整话术</div>
          <div class="send-readonly-fact">${escapeHtml(current.content_basis_label || "发送内容快照")}</div>
          <div class="send-readonly-message${contentText ? "" : " is-empty"}">${contentText ? escapeHtml(contentText) : "无文本话术"}</div>
        </div>
        <div class="send-readonly-section">
          <div class="send-readonly-label">是否携带附件</div>
          <div class="send-readonly-attachment-answer">${attachments.length ? `是，共 ${attachments.length} 个` : "否"}</div>
          <div class="send-readonly-fact">${escapeHtml(current.attachment_basis_label || (attachments.length ? "附件快照" : "未携带附件"))}</div>
          ${attachments.length ? `<div class="send-readonly-attachments">${attachments.map(renderAttachment).join("")}</div>` : '<div class="send-readonly-empty">本次发送未携带附件</div>'}
        </div>
      </section>
    `;
  }

  global.AICRMSendContentReadonlyDetail = Object.freeze({
    escapeHtml,
    parsePayloadObject,
    normalizeIdList,
    normalizeContentPackage,
    taskToContentPackage,
    summary,
    renderCompact,
    materialDetailText,
    renderFull,
  });
})(window);
