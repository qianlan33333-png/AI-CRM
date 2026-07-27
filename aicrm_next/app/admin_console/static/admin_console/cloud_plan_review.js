(function () {
  "use strict";

  const root = document.querySelector("[data-cloud-plan-root]");
  if (!root) return;

  const api = window.AdminApi || {};
  const escapeHtml = api.escapeHtml || ((value) => String(value || ""));
  const requestJson = api.requestJson || localRequestJson;
  const PAGE_SIZE = 50;
  const mode = root.dataset.pageMode || "list";
  const planId = root.dataset.planId || "";
  const adminActionToken = root.dataset.adminActionToken || "";
  const state = {
    plans: [],
    plan: null,
    recipients: [],
    recipientTotal: 0,
    recipientOffset: 0,
    currentRecipient: null,
    currentMessages: [],
    materialPreviewCache: new Map(),
  };

  function localRequestJson(url, options) {
    const finalOptions = { ...(options || {}) };
    const headers = { ...(finalOptions.headers || {}) };
    if (!headers.Accept) headers.Accept = "application/json";
    if (finalOptions.body && typeof finalOptions.body === "object" && !(finalOptions.body instanceof FormData)) {
      if (!headers["Content-Type"]) headers["Content-Type"] = "application/json";
      finalOptions.body = JSON.stringify(finalOptions.body);
    }
    finalOptions.headers = headers;
    finalOptions.credentials = finalOptions.credentials || "same-origin";
    return fetch(url, finalOptions).then((response) => response.text().then((text) => {
      let payload = {};
      try { payload = text ? JSON.parse(text) : {}; } catch (_error) { payload = {}; }
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || payload.detail || response.statusText || "请求失败");
      }
      return payload;
    }));
  }

  function qs(selector) {
    return root.querySelector(selector);
  }

  function qsa(selector) {
    return Array.from(root.querySelectorAll(selector));
  }

  function text(selector, value) {
    const node = qs(selector);
    if (node) node.textContent = value == null || value === "" ? "--" : String(value);
  }

  function toast(message) {
    const node = qs("[data-plan-toast]");
    if (!node) return;
    node.textContent = message;
    node.classList.add("is-open");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.remove("is-open"), 2600);
  }

  function errorMessage(error) {
    return String((error && error.message) || "请求失败");
  }

  function jsonHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Admin-Action-Token": adminActionToken,
    };
  }

  function writePayload(extra) {
    return {
      admin_action_token: adminActionToken,
      operator: "admin_ui",
      ...(extra || {}),
    };
  }

  function formatDate(value) {
    if (!value) return "--";
    const textValue = String(value);
    const date = new Date(textValue);
    if (Number.isNaN(date.getTime())) return textValue;
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function planStatus(plan) {
    const review = String((plan && plan.review_status) || "");
    const run = String((plan && plan.run_status) || "");
    if (run === "active" || run === "running") return { label: "执行中", tone: "ok" };
    if (review === "approved") return { label: "已批准", tone: "ok" };
    if (review === "rejected") return { label: "已拒绝", tone: "bad" };
    return { label: "待审批", tone: "warn" };
  }

  function sendStatus(row) {
    const approval = String((row && row.approval_status) || "");
    const send = String((row && row.send_status) || "");
    if (approval === "rejected" || send === "cancelled") return { label: "已拒绝", tone: "bad" };
    if (send === "sent") return { label: "已发送", tone: "ok" };
    if (approval === "approved" || send === "queued" || send === "sending") return { label: "已批准", tone: "ok" };
    if (send === "failed") return { label: "发送失败", tone: "bad" };
    return { label: "待处理", tone: "warn" };
  }

  function badge(meta) {
    const toneClass = meta.tone ? ` cloud-plan-badge--${meta.tone}` : "";
    return `<span class="cloud-plan-badge${toneClass}">${escapeHtml(meta.label)}</span>`;
  }

  function canApproveRecipient(row) {
    if (row && row.supports_recipient_approval === false) return false;
    const approval = String((row && row.approval_status) || "");
    const send = String((row && row.send_status) || "");
    return approval !== "approved" && approval !== "rejected" && send !== "sent" && send !== "queued" && send !== "sending";
  }

  function approveLabel(row) {
    const approval = String((row && row.approval_status) || "");
    const send = String((row && row.send_status) || "");
    if (approval === "rejected") return "已拒绝";
    if (approval === "approved" || send === "sent" || send === "queued" || send === "sending") return "已批准";
    if (row && row.supports_recipient_approval === false) return "批准";
    return "批准";
  }

  function setButtonLoading(button, loadingText) {
    if (!button) return () => {};
    const originalText = button.textContent;
    const originalDisabled = button.disabled;
    button.disabled = true;
    button.textContent = loadingText;
    return () => {
      button.disabled = originalDisabled;
      button.textContent = originalText;
    };
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
    });
  }

  function contentPackageToTaskPayload(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    return {
      content_text: normalized.content_text,
      content_payload: {
        content_package: normalized,
        image_library_ids: normalized.image_library_ids,
        image_media_ids: [],
        miniprogram_library_ids: normalized.miniprogram_library_ids,
        attachment_library_ids: normalized.attachment_library_ids,
        group_invite_library_ids: normalized.group_invite_library_ids,
      },
      attachments: [],
    };
  }

  function taskContentSummary(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    const body = normalized.content_text || "";
    return {
      text: body ? (body.length > 80 ? `${body.slice(0, 80)}...` : body) : "未配置话术",
      imageCount: normalized.image_library_ids.length,
      miniprogramCount: normalized.miniprogram_library_ids.length,
      attachmentCount: normalized.attachment_library_ids.length,
      groupInviteCount: normalized.group_invite_library_ids.length,
    };
  }

  function renderTaskContentSummary(contentPackage) {
    const summary = taskContentSummary(contentPackage);
    return `
      <div class="cloud-plan-task-content-summary" data-task-content-summary>
        <strong>话术摘要：</strong><span>${escapeHtml(summary.text)}</span>
        <strong>内容数量：</strong><span>图片 ${summary.imageCount} / 小程序 ${summary.miniprogramCount} / 附件 ${summary.attachmentCount} / 客户群 ${summary.groupInviteCount}</span>
        <strong>素材明细：</strong><span data-task-material-detail>${summary.imageCount + summary.miniprogramCount + summary.attachmentCount + summary.groupInviteCount ? "素材信息加载中" : "无素材"}</span>
      </div>
    `;
  }

  function taskEditState(task) {
    const recipient = state.currentRecipient || {};
    const plan = state.plan || {};
    if (String(plan.review_status || "") === "rejected") {
      return { canEdit: false, reason: "计划已拒绝，不能再编辑" };
    }
    if (String(recipient.approval_status || "") !== "pending") {
      return { canEdit: false, reason: "目标人员已完成审批，不能再编辑" };
    }
    if (String(recipient.send_status || "") !== "pending") {
      return { canEdit: false, reason: "目标人员已进入发送流程，不能再编辑" };
    }
    if (String(task.status || "pending") !== "pending") {
      return { canEdit: false, reason: "任务已排队、发送或取消，不能再编辑" };
    }
    return { canEdit: true, reason: "" };
  }

  function initListPage() {
    const refreshButton = qs("[data-plan-refresh]");
    const keywordInput = qs("[data-plan-keyword]");
    const statusSelect = qs("[data-plan-status]");
    refreshButton && refreshButton.addEventListener("click", loadPlans);
    keywordInput && keywordInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadPlans();
    });
    statusSelect && statusSelect.addEventListener("change", loadPlans);
    loadPlans();
  }

  function renderStats(payload) {
    const plans = payload.plans || [];
    const pending = plans.filter((plan) => String(plan.review_status || "") === "pending_review").length;
    const active = plans.filter((plan) => ["active", "running"].includes(String(plan.run_status || ""))).length;
    const touch = plans.reduce((sum, plan) => sum + Number(plan.target_count || 0), 0);
    text("[data-stat-pending-plans]", pending);
    text("[data-stat-active-plans]", active);
    text("[data-stat-today-touch]", touch);
  }

  async function loadPlans() {
    const list = qs("[data-plan-list]");
    if (!list) return;
    list.innerHTML = '<div class="cloud-plan-loading">计划列表加载中</div>';
    const params = new URLSearchParams();
    params.set("limit", "20");
    params.set("offset", "0");
    const keyword = (qs("[data-plan-keyword]") || {}).value || "";
    const status = (qs("[data-plan-status]") || {}).value || "";
    if (keyword.trim()) params.set("keyword", keyword.trim());
    if (status) params.set("status", status);
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans?${params.toString()}`);
      state.plans = payload.plans || [];
      renderStats(payload);
      if (!state.plans.length) {
        list.innerHTML = '<div class="cloud-plan-empty">暂无计划</div>';
        return;
      }
      list.innerHTML = state.plans.map(renderPlanRow).join("");
    } catch (error) {
      list.innerHTML = `<div class="cloud-plan-error">${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  function renderPlanRow(plan) {
    const status = planStatus(plan);
    const planHref = `/admin/cloud-orchestrator/plans/${encodeURIComponent(plan.plan_id || "")}`;
    return `
      <article class="cloud-plan-row">
        <div>
          <div class="cloud-plan-name">${escapeHtml(plan.display_name || plan.plan_id)}</div>
        </div>
        <div>${escapeHtml(plan.owner_userid || "--")}</div>
        <div class="cloud-plan-cell-muted">${escapeHtml(formatDate(plan.updated_at))}</div>
        <div>${Number(plan.target_count || 0)}</div>
        <div>${badge(status)}</div>
        <div><a class="cloud-plan-button" href="${planHref}">查看详情</a></div>
      </article>
    `;
  }

  function initDetailPage() {
    qs("[data-plan-approve]") && qs("[data-plan-approve]").addEventListener("click", approvePlan);
    qs("[data-plan-reject]") && qs("[data-plan-reject]").addEventListener("click", rejectPlan);
    qs("[data-recipient-load-more]") && qs("[data-recipient-load-more]").addEventListener("click", () => loadRecipients({ append: true }));
    qs("[data-drawer-close]") && qs("[data-drawer-close]").addEventListener("click", closeDrawer);
    qs("[data-drawer-mask]") && qs("[data-drawer-mask]").addEventListener("click", closeDrawer);
    qs("[data-drawer-approve]") && qs("[data-drawer-approve]").addEventListener("click", () => {
      if (state.currentRecipient) approveRecipient(state.currentRecipient.recipient_id, qs("[data-drawer-approve]"));
    });
    qs("[data-drawer-reject]") && qs("[data-drawer-reject]").addEventListener("click", () => {
      if (state.currentRecipient) rejectRecipient(state.currentRecipient.recipient_id, qs("[data-drawer-reject]"));
    });
    loadPlan();
    loadRecipients({ append: false });
  }

  async function loadPlan() {
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}`);
      updatePlan(payload.plan);
    } catch (error) {
      text("[data-plan-detail-state]", errorMessage(error));
    }
  }

  function updatePlan(plan) {
    if (!plan) return;
    state.plan = plan;
    const status = planStatus(plan);
    text("[data-plan-detail-state]", `${plan.display_name || plan.plan_id} · ${status.label}`);
    text("[data-plan-name]", plan.display_name || plan.plan_id);
    text("[data-plan-code]", plan.plan_id);
    text("[data-plan-owner]", plan.owner_userid);
    text("[data-plan-updated]", formatDate(plan.updated_at));
    text("[data-plan-target-count]", Number(plan.target_count || 0));
    const statusNode = qs("[data-plan-status-label]");
    if (statusNode) statusNode.innerHTML = badge(status);
    const approveButton = qs("[data-plan-approve]");
    if (approveButton) {
      const running = ["active", "running"].includes(String(plan.run_status || ""));
      const rejected = String(plan.review_status || "") === "rejected";
      const approved = String(plan.review_status || "") === "approved";
      approveButton.disabled = running || rejected;
      approveButton.textContent = running ? "已开始执行" : (approved ? "开始执行" : "批准并开始执行");
    }
  }

  async function approvePlan(event) {
    const restore = setButtonLoading(event.currentTarget, "启动中");
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/approve`, {
        method: "POST",
        headers: jsonHeaders(),
        body: writePayload(),
      });
      updatePlan(payload.plan);
      toast("计划已批准并开始执行");
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      restore();
      if (state.currentRecipient) renderDrawer();
    }
  }

  async function rejectPlan(event) {
    const restore = setButtonLoading(event.currentTarget, "拒绝中");
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/reject`, {
        method: "POST",
        headers: jsonHeaders(),
        body: writePayload({ reason: "admin_ui_reject" }),
      });
      updatePlan(payload.plan);
      toast("计划已拒绝");
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      restore();
      if (state.currentRecipient) renderDrawer();
    }
  }

  async function loadRecipients(options) {
    const append = Boolean(options && options.append);
    const tbody = qs("[data-recipient-list]");
    const moreButton = qs("[data-recipient-load-more]");
    if (!tbody) return;
    if (!append) {
      state.recipientOffset = 0;
      state.recipients = [];
      tbody.innerHTML = '<tr><td colspan="6" class="cloud-plan-state">目标人员加载中</td></tr>';
    }
    if (moreButton) moreButton.disabled = true;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.recipientOffset) });
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/recipients?${params.toString()}`);
      state.plan = payload.plan || state.plan;
      updatePlan(state.plan);
      state.recipientTotal = Number(payload.total || 0);
      state.recipients = append ? state.recipients.concat(payload.rows || []) : (payload.rows || []);
      state.recipientOffset = state.recipients.length;
      renderRecipients();
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="6" class="cloud-plan-error">${escapeHtml(errorMessage(error))}</td></tr>`;
    } finally {
      updateRecipientLoadbar();
    }
  }

  function renderRecipients() {
    const tbody = qs("[data-recipient-list]");
    if (!tbody) return;
    if (!state.recipients.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="cloud-plan-empty">暂无目标人员</td></tr>';
      return;
    }
    tbody.innerHTML = state.recipients.map(renderRecipientRow).join("");
    qsa("[data-open-recipient]").forEach((button) => {
      button.addEventListener("click", () => openRecipient(Number(button.dataset.recipientId || 0)));
    });
    qsa("[data-approve-recipient]").forEach((button) => {
      button.addEventListener("click", () => approveRecipient(Number(button.dataset.recipientId || 0), button));
    });
  }

  function renderRecipientRow(row) {
    const status = sendStatus(row);
    const canApprove = canApproveRecipient(row);
    return `
      <tr data-recipient-row="${Number(row.recipient_id || 0)}">
        <td>
          <strong>${escapeHtml(row.display_name || row.external_userid)}</strong>
          <div class="cloud-plan-cell-muted">${escapeHtml(row.external_userid || "")}</div>
        </td>
        <td>${escapeHtml(row.owner_userid || "--")}</td>
        <td>${escapeHtml(formatDate(row.updated_at))}</td>
        <td>${Number(row.planned_message_count || 0)}</td>
        <td>${badge(status)}</td>
        <td>
          <div class="cloud-plan-actions">
            <button class="cloud-plan-button" type="button" data-open-recipient data-recipient-id="${Number(row.recipient_id || 0)}">查看详情</button>
            <button class="cloud-plan-button cloud-plan-button--primary" type="button" data-approve-recipient data-recipient-id="${Number(row.recipient_id || 0)}" ${canApprove ? "" : "disabled"}>${escapeHtml(approveLabel(row))}</button>
          </div>
        </td>
      </tr>
    `;
  }

  function updateRecipientLoadbar() {
    const loaded = state.recipients.length;
    const total = state.recipientTotal;
    text("[data-recipient-loaded]", `已加载 ${loaded} / ${total} 人`);
    const progress = qs("[data-recipient-progress]");
    if (progress) {
      progress.style.width = total > 0 ? `${Math.min(100, Math.round((loaded / total) * 100))}%` : "0%";
    }
    const moreButton = qs("[data-recipient-load-more]");
    if (moreButton) moreButton.disabled = loaded >= total || total === 0;
  }

  async function openRecipient(recipientId) {
    if (!recipientId) return;
    state.currentRecipient = null;
    state.currentMessages = [];
    openDrawer();
    text("[data-drawer-name]", "人员详情");
    text("[data-drawer-subtitle]", "人员详情加载中");
    const tasks = qs("[data-drawer-tasks]");
    if (tasks) tasks.innerHTML = '<div class="cloud-plan-loading">人员详情加载中</div>';
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/recipients/${encodeURIComponent(recipientId)}`);
      state.currentRecipient = payload.recipient;
      state.currentMessages = payload.messages || [];
      renderDrawer();
    } catch (error) {
      if (tasks) tasks.innerHTML = `<div class="cloud-plan-error">${escapeHtml(errorMessage(error))}</div>`;
      text("[data-drawer-subtitle]", errorMessage(error));
    }
  }

  function openDrawer() {
    qs("[data-drawer-mask]") && qs("[data-drawer-mask]").classList.add("is-open");
    qs("[data-recipient-drawer]") && qs("[data-recipient-drawer]").classList.add("is-open");
  }

  function closeDrawer() {
    qs("[data-drawer-mask]") && qs("[data-drawer-mask]").classList.remove("is-open");
    qs("[data-recipient-drawer]") && qs("[data-recipient-drawer]").classList.remove("is-open");
  }

  function renderDrawer() {
    const recipient = state.currentRecipient;
    if (!recipient) return;
    const messageCount = state.currentMessages.length || Number(recipient.planned_message_count || 0);
    text("[data-drawer-name]", recipient.display_name || recipient.external_userid);
    text("[data-drawer-subtitle]", `${sendStatus(recipient).label} · ${messageCount} 次话术任务`);
    text("[data-drawer-target-name]", recipient.display_name || "--");
    text("[data-drawer-external-userid]", recipient.external_userid || "--");
    text("[data-drawer-owner]", recipient.owner_userid || "--");
    text("[data-drawer-message-count]", messageCount);
    const approveButton = qs("[data-drawer-approve]");
    if (approveButton) {
      approveButton.disabled = !canApproveRecipient(recipient);
      approveButton.textContent = canApproveRecipient(recipient) ? "批准这个人发送" : approveLabel(recipient);
    }
    const rejectButton = qs("[data-drawer-reject]");
    if (rejectButton) rejectButton.disabled = String(recipient.approval_status || "") === "rejected" || String(recipient.send_status || "") === "sent";
    const tasks = qs("[data-drawer-tasks]");
    if (!tasks) return;
    if (!state.currentMessages.length) {
      tasks.innerHTML = '<div class="cloud-plan-empty">暂无话术任务</div>';
      return;
    }
    tasks.innerHTML = state.currentMessages.map(renderTask).join("");
    qsa("[data-edit-task]").forEach((button) => {
      button.addEventListener("click", () => openTaskComposer(Number(button.dataset.messageId || 0), button));
    });
    hydrateTaskMaterialPreviews();
  }

  function renderTask(task) {
    const contentPackage = taskToContentPackage(task);
    const editState = taskEditState(task);
    const attachments = Array.isArray(task.attachments) ? task.attachments : [];
    const attachmentHtml = attachments.length
      ? attachments.map((item) => `<div class="cloud-plan-cell-muted">${escapeHtml(item.msgtype || item.type || "附件")} ${escapeHtml(item.title || item.name || "")}</div>`).join("")
      : "";
    const editButton = editState.canEdit
      ? `<button class="cloud-plan-button" type="button" data-edit-task data-message-id="${Number(task.message_id || 0)}">编辑</button>`
      : "";
    const editNote = editState.canEdit
      ? ""
      : `<div class="cloud-plan-task-edit-note cloud-plan-cell-muted">${escapeHtml(editState.reason)}</div>`;
    return `
      <article class="cloud-plan-task" data-task-card data-message-id="${Number(task.message_id || 0)}">
        <div class="cloud-plan-task-head">
          <div class="cloud-plan-task-meta">第 ${Number(task.sequence_index || 0)} 次 · D+${Number(task.day_offset || 0)} · ${escapeHtml(task.send_time || "--")} · ${escapeHtml(task.status || "pending")}</div>
          ${editButton}
        </div>
        ${renderTaskContentSummary(contentPackage)}
        ${attachmentHtml}
        ${editNote}
      </article>
    `;
  }

  function contentPackageCacheKey(contentPackage) {
    return JSON.stringify(normalizeContentPackage(contentPackage));
  }

  function materialDetailText(materials, contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    if (!materials.length) {
      const fallback = [];
      normalized.image_library_ids.forEach((id) => fallback.push(`图片 #${id}`));
      normalized.miniprogram_library_ids.forEach((id) => fallback.push(`小程序 #${id}`));
      normalized.attachment_library_ids.forEach((id) => fallback.push(`附件 #${id}`));
      normalized.group_invite_library_ids.forEach((id) => fallback.push(`群邀请 #${id}`));
      return fallback.join(" / ") || "无素材";
    }
    return materials.map((item) => {
      const type = String(item.type || "");
      const title = String(item.title || "").trim() || `${type || "素材"} #${item.library_id || ""}`;
      const subtitle = String(item.subtitle || "").trim();
      if (type === "miniprogram") return `小程序：${title}${subtitle ? `（${subtitle}）` : ""}`;
      if (type === "image") return `图片：${title}${subtitle ? `（${subtitle}）` : ""}`;
      return `附件：${title}${subtitle ? `（${subtitle}）` : ""}`;
    }).join(" / ");
  }

  async function hydrateTaskMaterialPreviews() {
    const cards = qsa("[data-task-card]");
    cards.forEach(async (card) => {
      const messageId = Number(card.dataset.messageId || 0);
      const task = state.currentMessages.find((item) => Number(item.message_id || 0) === messageId);
      const detailNode = card.querySelector("[data-task-material-detail]");
      if (!task || !detailNode) return;
      const contentPackage = taskToContentPackage(task);
      const summary = taskContentSummary(contentPackage);
      if (summary.imageCount + summary.miniprogramCount + summary.attachmentCount + summary.groupInviteCount <= 0) {
        detailNode.textContent = "无素材";
        return;
      }
      const cacheKey = contentPackageCacheKey(contentPackage);
      if (state.materialPreviewCache.has(cacheKey)) {
        detailNode.textContent = state.materialPreviewCache.get(cacheKey);
        return;
      }
      try {
        const payload = await requestJson("/api/admin/send-content/preview", {
          method: "POST",
          body: {
            content_package: contentPackage,
            text_enabled: true,
            require_body: false,
          },
        });
        const detail = materialDetailText(((payload.preview || {}).materials || []), contentPackage);
        state.materialPreviewCache.set(cacheKey, detail);
        detailNode.textContent = detail;
      } catch (_error) {
        detailNode.textContent = materialDetailText([], contentPackage);
      }
    });
  }

  async function openTaskComposer(messageId, button) {
    const task = state.currentMessages.find((item) => Number(item.message_id || 0) === Number(messageId || 0));
    if (!task || !state.currentRecipient) return;
    const editState = taskEditState(task);
    if (!editState.canEdit) {
      toast(editState.reason);
      return;
    }
    if (!window.AICRMSendContentComposer || typeof window.AICRMSendContentComposer.open !== "function") {
      toast("标准发送内容组件加载失败，请刷新页面后重试");
      return;
    }
    window.AICRMSendContentComposer.open({
      title: "配置单人话术内容",
      textEnabled: true,
      value: taskToContentPackage(task),
      limits: { image: 3, miniprogram: 1, attachment: 9, group_invite: 1 },
      async onConfirm(contentPackage) {
        const restore = setButtonLoading(button, "保存中");
        try {
          const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/recipients/${encodeURIComponent(state.currentRecipient.recipient_id)}/messages/${encodeURIComponent(messageId)}`, {
            method: "PATCH",
            headers: jsonHeaders(),
            body: writePayload({
              ...contentPackageToTaskPayload(contentPackage),
              day_offset: Number(task.day_offset || 0),
              send_time: task.send_time || "",
            }),
          });
          state.currentRecipient = payload.recipient || state.currentRecipient;
          state.currentMessages = state.currentMessages.map((item) => Number(item.message_id || 0) === Number(messageId) ? payload.message : item);
          updateRecipientInState(state.currentRecipient);
          renderDrawer();
          toast("话术内容已保存");
        } catch (error) {
          toast(errorMessage(error));
        } finally {
          restore();
        }
      },
    });
  }

  function updateRecipientInState(recipient) {
    if (!recipient) return;
    state.recipients = state.recipients.map((row) => Number(row.recipient_id) === Number(recipient.recipient_id) ? recipient : row);
    if (state.currentRecipient && Number(state.currentRecipient.recipient_id) === Number(recipient.recipient_id)) {
      state.currentRecipient = recipient;
      renderDrawer();
    }
    renderRecipients();
  }

  async function approveRecipient(recipientId, button) {
    if (!recipientId) return;
    const restore = setButtonLoading(button, "批准中");
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/recipients/${encodeURIComponent(recipientId)}/approve`, {
        method: "POST",
        headers: jsonHeaders(),
        body: writePayload(),
      });
      updateRecipientInState(payload.recipient);
      toast(payload.status === "already_approved" ? "已批准" : "已批准这个人发送");
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      restore();
    }
  }

  async function rejectRecipient(recipientId, button) {
    if (!recipientId) return;
    const restore = setButtonLoading(button, "拒绝中");
    try {
      const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}/recipients/${encodeURIComponent(recipientId)}/reject`, {
        method: "POST",
        headers: jsonHeaders(),
        body: writePayload({ reason: "admin_ui_reject" }),
      });
      updateRecipientInState(payload.recipient);
      toast("已拒绝这个人");
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      restore();
    }
  }

  if (mode === "detail") {
    initDetailPage();
  } else {
    initListPage();
  }

  window.AICRMCloudPlanTaskContentAdapter = {
    parsePayloadObject,
    normalizeIdList,
    normalizeContentPackage,
    taskToContentPackage,
    contentPackageToTaskPayload,
    taskContentSummary,
    renderTaskContentSummary,
  };
})();
