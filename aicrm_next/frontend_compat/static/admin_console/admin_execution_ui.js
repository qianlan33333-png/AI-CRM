(function (window, document) {
  "use strict";

  const PAGE_SIZE = 50;

  function api() {
    return window.AdminApi;
  }

  async function readJson(response) {
    try { return await response.json(); } catch (_) { return {}; }
  }

  function escapeHtml(value) {
    if (api() && api().escapeHtml) return api().escapeHtml(value);
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function text(value, fallback = "-") {
    const normalized = String(value == null ? "" : value).trim();
    return normalized || fallback;
  }

  function number(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function formatTime(value) {
    if (!value) return "-";
    return window.AdminFmt && window.AdminFmt.localTime
      ? window.AdminFmt.localTime(value)
      : text(value);
  }

  function statusTone(status) {
    const value = String(status || "").toLowerCase();
    if (["sent", "succeeded", "success"].includes(value)) return "ok";
    if (["failed", "failed_terminal", "blocked", "cancelled"].includes(value)) return "danger";
    if ([
      "pending",
      "queued",
      "running",
      "claimed",
      "dispatching",
      "failed_retryable",
      "unknown_after_dispatch",
      "waiting_approval",
      "sent_with_shadow_warning",
      "shadow_failed_not_business_failed",
    ].includes(value)) return "warn";
    return "neutral";
  }

  function statusBadge(status, label) {
    return `<span class="execution-status execution-status--${statusTone(status)}">${escapeHtml(text(label || status))}</span>`;
  }

  function queueStateLabel(value) {
    return ({
      held: "等待放行",
      scheduled: "计划等待",
      waiting: "正常排队",
      running: "执行中",
      retry_wait: "重试等待",
      rate_limited: "限流等待",
      unknown: "结果待核对",
      terminal: "已结束",
    })[value] || text(value);
  }

  function deliveryStateLabel(value) {
    return ({
      pending: "尚无交付结果",
      provider_accepted: "服务商已受理",
      delivered: "已送达",
      failed: "交付失败",
      unknown: "交付结果未知",
      not_applicable: "非消息交付动作",
    })[value] || text(value);
  }

  function QueueStateBadge(queueState, waitReason) {
    const detail = String(waitReason || "").trim();
    return `<span class="execution-status execution-status--${statusTone(queueState)}">${escapeHtml(queueStateLabel(queueState))}</span>${detail ? `<span class="execution-muted">${escapeHtml(detail)}</span>` : ""}`;
  }

  function CapacitySummary(runtime, laneNames) {
    const expected = new Set((laneNames || []).filter(Boolean));
    const lanes = (runtime && runtime.lanes || []).filter((lane) => !expected.size || expected.has(lane.lane));
    if (!lanes.length) return '<div class="admin-state">暂无 lane runtime 数据</div>';
    return `<div class="execution-capacity-grid">${lanes.map((lane) => `
      <article class="execution-capacity-item">
        <div class="execution-item-head"><strong>${escapeHtml(lane.lane)}</strong>${statusBadge(lane.rollout_mode, lane.rollout_mode)}</div>
        ${definitionList([
          ["容量", `${number(lane.in_flight)} / ${number(lane.max_in_flight)}`],
          ["可领取", lane.eligible],
          ["等待 / 计划", `${number(lane.raw_open)} / ${number(lane.scheduled)}`],
          ["Hold / Policy Gate", `${number(lane.held)} / ${number(lane.policy_gated)}`],
          ["限流", lane.rate_limited],
          ["Unknown / DLQ", `${number(lane.unknown)} / ${number(lane.dlq)}`],
          ["最老 eligible", `${number(lane.oldest_eligible_age_seconds)} 秒`],
        ])}
      </article>`).join("")}</div>`;
  }

  function ExecutionTimeline(timeline) {
    const items = timeline && timeline.items || [];
    if (!items.length) return '<div class="admin-state">暂无统一执行时间线</div>';
    return `<div class="execution-event-list">${items.map((item) => `
      <div class="execution-event">
        <div class="execution-item-head"><strong>${escapeHtml(text(item.item_type || item.item_kind))}</strong>${statusBadge(item.status, item.status)}</div>
        <div class="execution-muted">${escapeHtml(text(item.item_kind))} · ${escapeHtml(text(item.lane))} · ${escapeHtml(formatTime(item.updated_at || item.created_at))}</div>
      </div>`).join("")}</div>`;
  }

  function loadCapacitySummary(root, laneNames) {
    const node = root.querySelector("[data-capacity-summary]");
    if (!node) return Promise.resolve(null);
    node.innerHTML = '<div class="admin-state admin-state--loading">正在读取 lane runtime...</div>';
    return api().requestJson("/api/admin/execution-runtime").then((runtime) => {
      node.innerHTML = CapacitySummary(runtime, laneNames);
      return runtime;
    }).catch((error) => {
      node.innerHTML = `<div class="admin-state">${escapeHtml(text(error && error.message, "runtime 暂不可用"))}</div>`;
      return null;
    });
  }

  function loadExecutionTimeline(root, executionId) {
    const node = root.querySelector("[data-execution-timeline]");
    if (!node) return Promise.resolve(null);
    if (!executionId) {
      node.innerHTML = '<div class="admin-state">该记录尚无公开 execution_id</div>';
      return Promise.resolve(null);
    }
    node.innerHTML = '<div class="admin-state admin-state--loading">正在读取统一执行时间线...</div>';
    return api().requestJson(`/api/admin/executions/${encodeURIComponent(executionId)}`).then((timeline) => {
      node.innerHTML = ExecutionTimeline(timeline);
      return timeline;
    }).catch((error) => {
      node.innerHTML = `<div class="admin-state">${escapeHtml(text(error && error.message, "时间线暂不可用"))}</div>`;
      return null;
    });
  }

  function definitionList(items) {
    return `<dl class="admin-definition-list">${items.map(([label, value]) => (
      `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(text(value))}</dd></div>`
    )).join("")}</dl>`;
  }

  function jsonDetails(label, value) {
    if (!value || (typeof value === "object" && !Object.keys(value).length)) return "";
    return `<details class="execution-json-details"><summary>${escapeHtml(label)}</summary><pre class="admin-code-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`;
  }

  function formParams(form, additions = {}) {
    const params = new URLSearchParams();
    if (form) {
      new FormData(form).forEach((value, key) => {
        const normalized = String(value || "").trim();
        if (normalized) params.set(key, normalized);
      });
    }
    Object.entries(additions).forEach(([key, value]) => {
      if (value !== "" && value !== null && value !== undefined) params.set(key, String(value));
      else params.delete(key);
    });
    return params;
  }

  function applyQueryToForm(form, params) {
    if (!form) return;
    Array.from(form.elements || []).forEach((field) => {
      if (!field.name || !params.has(field.name)) return;
      field.value = params.get(field.name) || "";
    });
  }

  function replaceBrowserQuery(params) {
    if (!window.history || !window.history.replaceState) return;
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }

  function showError(root, error) {
    const node = root.querySelector("[data-page-error]");
    if (!node) return;
    node.textContent = text(error && error.message, "读取失败，请稍后重试。");
    node.hidden = false;
  }

  function clearError(root) {
    const node = root.querySelector("[data-page-error]");
    if (node) node.hidden = true;
  }

  function populateOptions(select, options, currentValue) {
    if (!select) return;
    const firstLabel = select.options.length ? select.options[0].textContent : "全部";
    select.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>` + (options || []).map((option) => (
      `<option value="${escapeHtml(option.key)}">${escapeHtml(option.label || option.key)}</option>`
    )).join("");
    select.value = currentValue || "";
  }

  function clearForm(form) {
    Array.from(form.elements || []).forEach((field) => {
      if (!field.name) return;
      if (field.type === "checkbox" || field.type === "radio") field.checked = false;
      else field.value = "";
    });
  }

  function renderPagination(root, state, load) {
    const pagination = root.querySelector("[data-pagination]");
    if (!pagination) return;
    const cursorMode = Boolean(state.cursorMode);
    pagination.hidden = cursorMode
      ? !state.hasMore && !(state.cursorStack || []).length
      : state.total <= state.limit;
    const summary = pagination.querySelector("[data-page-summary]");
    if (summary) {
      const pageOffset = cursorMode ? (state.cursorStack || []).length * state.limit : state.offset;
      const first = state.total ? pageOffset + 1 : 0;
      summary.textContent = `${first}-${Math.min(pageOffset + state.items.length, state.total)} / ${state.total}`;
    }
    const previous = pagination.querySelector('[data-page-direction="previous"]');
    const next = pagination.querySelector('[data-page-direction="next"]');
    if (previous) previous.disabled = cursorMode ? !(state.cursorStack || []).length : state.offset <= 0;
    if (next) next.disabled = cursorMode ? !state.hasMore : state.offset + state.limit >= state.total;
    pagination.onclick = (event) => {
      const button = event.target.closest("[data-page-direction]");
      if (!button || button.disabled) return;
      if (cursorMode) {
        if (button.dataset.pageDirection === "previous") {
          state.cursor = state.cursorStack.pop() || "";
        } else {
          state.cursorStack.push(state.cursor || "");
          state.cursor = state.nextCursor || "";
        }
      } else {
        state.offset = button.dataset.pageDirection === "previous"
          ? Math.max(0, state.offset - state.limit)
          : state.offset + state.limit;
      }
      load();
    };
  }

  function requestAction(root, options) {
    const dialog = root.querySelector("[data-execution-action-dialog]");
    if (!dialog || typeof dialog.showModal !== "function") {
      return Promise.resolve({ confirmed: false, reason: "" });
    }
    const form = dialog.querySelector("form");
    const title = dialog.querySelector("[data-execution-dialog-title]");
    const description = dialog.querySelector("[data-execution-dialog-description]");
    const reasonField = dialog.querySelector("[data-execution-dialog-reason-field]");
    const reasonInput = dialog.querySelector("[data-execution-dialog-reason]");
    const actorNode = dialog.querySelector("[data-execution-dialog-actor]");
    const versionNode = dialog.querySelector("[data-execution-dialog-version]");
    const duplicateRiskField = dialog.querySelector("[data-execution-dialog-duplicate-risk-field]");
    const duplicateRiskInput = dialog.querySelector("[data-execution-dialog-duplicate-risk]");
    const error = dialog.querySelector("[data-execution-dialog-error]");
    const cancel = dialog.querySelector("[data-execution-dialog-cancel]");
    title.textContent = options.title || "确认操作";
    description.textContent = options.description || "请确认是否继续。";
    reasonField.hidden = !options.reasonRequired;
    reasonInput.value = "";
    const actor = String(options.actor || root.dataset.operatorActor || "").trim();
    const expectedVersion = String(options.expectedVersion || "").trim();
    actorNode.textContent = actor || "无法识别已认证操作人";
    versionNode.textContent = expectedVersion || "未提供";
    duplicateRiskField.hidden = !options.duplicateRiskRequired;
    duplicateRiskInput.checked = false;
    error.hidden = true;

    return new Promise((resolve) => {
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        form.removeEventListener("submit", submitHandler);
        cancel.removeEventListener("click", cancelHandler);
        dialog.removeEventListener("cancel", cancelEventHandler);
        if (dialog.open) dialog.close();
        resolve(result);
      };
      const submitHandler = (event) => {
        event.preventDefault();
        const reason = reasonInput.value.trim();
        if (options.reasonRequired && !reason) {
          error.textContent = "请填写操作原因。";
          error.hidden = false;
          reasonInput.focus();
          return;
        }
        if (!actor) {
          error.textContent = "无法识别当前已认证操作人，请重新登录后再试。";
          error.hidden = false;
          return;
        }
        if (!expectedVersion) {
          error.textContent = "当前记录缺少版本信息，请刷新详情后再试。";
          error.hidden = false;
          return;
        }
        if (options.duplicateRiskRequired && !duplicateRiskInput.checked) {
          error.textContent = "结果未知的任务必须显式确认重复触达风险。";
          error.hidden = false;
          duplicateRiskInput.focus();
          return;
        }
        finish({
          confirmed: true,
          reason,
          actor,
          expectedVersion,
          duplicateRiskConfirmed: Boolean(duplicateRiskInput.checked),
        });
      };
      const cancelHandler = () => finish({ confirmed: false, reason: "", actor: "", expectedVersion: "" });
      const cancelEventHandler = (event) => {
        event.preventDefault();
        cancelHandler();
      };
      form.addEventListener("submit", submitHandler);
      cancel.addEventListener("click", cancelHandler);
      dialog.addEventListener("cancel", cancelEventHandler);
      dialog.showModal();
      if (options.reasonRequired) reasonInput.focus();
    });
  }

  async function requestVersionedQueueCommand(root, options) {
    const action = String(options.action || "dispatch");
    const confirmation = await requestAction(root, {
      title: action === "skip" ? "确认忽略回调" : action === "retry" ? "确认重试回调" : "确认执行回调",
      description: action === "skip"
        ? "忽略后会留下人工审计，且不会继续消费该回调。"
        : "确认后只提交版本化队列命令，不会在页面请求内执行 handler。",
      reasonRequired: true,
      actor: root.dataset.operatorActor,
      expectedVersion: options.expectedVersion,
    });
    if (!confirmation.confirmed) return null;
    return {
      actor: confirmation.actor,
      reason: confirmation.reason,
      expected_version: confirmation.expectedVersion,
    };
  }

  function bindShellActions(load, exportRows) {
    document.querySelectorAll('a[href="#refresh"]').forEach((anchor) => {
      anchor.addEventListener("click", (event) => {
        event.preventDefault();
        load();
      });
    });
    document.querySelectorAll('a[href="#export"]').forEach((anchor) => {
      anchor.addEventListener("click", (event) => {
        event.preventDefault();
        exportRows();
      });
    });
  }

  function csvCell(value) {
    const serialized = value && typeof value === "object"
      ? JSON.stringify(value)
      : String(value == null ? "" : value);
    const safe = /^[\t\r\n ]*[=+\-@]/.test(serialized) ? `'${serialized}` : serialized;
    return `"${safe.replace(/"/g, '""')}"`;
  }

  function downloadRows(filename, rows) {
    if (!rows || !rows.length || typeof window.Blob === "undefined") return;
    const headers = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    const body = [headers.map(csvCell).join(",")]
      .concat(rows.map((row) => headers.map((key) => csvCell(row[key])).join(",")))
      .join("\n");
    const url = window.URL.createObjectURL(new window.Blob([`\ufeff${body}`], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    window.URL.revokeObjectURL(url);
  }

  function bootPushList(root) {
    const form = root.querySelector("#filterForm");
    const tbody = root.querySelector("#jobsTbody");
    const initial = new URLSearchParams(window.location.search);
    const state = {
      section: initial.get("section") || "",
      status: initial.get("status") || "",
      cursorMode: true,
      cursor: initial.get("cursor") || "",
      cursorStack: [],
      nextCursor: "",
      hasMore: false,
      limit: PAGE_SIZE,
      total: 0,
      items: [],
    };
    applyQueryToForm(form, initial);

    const renderSections = (sections) => {
      const tabs = root.querySelector("#sectionTabs");
      tabs.innerHTML = [
        { key: "", label: "全部", count: state.total },
        ...(sections || []),
      ].map((section) => (
        `<button class="execution-tab${state.section === section.key ? " is-active" : ""}" type="button" data-section="${escapeHtml(section.key)}">${escapeHtml(section.label)} <span>${number(section.count)}</span></button>`
      )).join("");
    };

    const renderRows = (items) => {
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="admin-empty-cell">暂无符合条件的推送任务</td></tr>';
        return;
      }
      tbody.innerHTML = items.map((job) => {
        const id = text(job.projection_id || job.id);
        const target = [job.target_type, job.target_id || job.external_userid].filter(Boolean).join(":");
        const business = [job.business_type, job.business_id].filter(Boolean).join(":");
        const error = [job.last_error_code, job.last_error_message].filter(Boolean).join(" · ");
        return `<tr>
          <td><a class="execution-table-link execution-mono" href="/admin/push-center/jobs/${encodeURIComponent(id)}">${escapeHtml(job.display_id || id)}</a><div class="execution-muted">${escapeHtml(job.effect_type || job.record_type || "-")}</div></td>
          <td>${escapeHtml(job.section_label || job.section || "-")}</td>
          <td>${statusBadge(job.effective_status || job.status, job.effective_status_label || job.status_label)}<div class="execution-queue-state">${QueueStateBadge(job.queue_state, job.wait_reason)}<span class="execution-muted">${escapeHtml(deliveryStateLabel(job.delivery_state))}</span></div></td>
          <td class="execution-mono">${escapeHtml(text(target))}</td>
          <td class="execution-mono">${escapeHtml(text(business))}</td>
          <td>${escapeHtml(text(error))}</td>
          <td>${escapeHtml(formatTime(job.created_at || job.scheduled_at))}</td>
        </tr>`;
      }).join("");
    };

    const load = () => {
      clearError(root);
      tbody.innerHTML = '<tr><td colspan="7" class="admin-empty-cell">加载中...</td></tr>';
      const params = formParams(form, {
        section: state.section,
        status: state.status,
        limit: state.limit,
        cursor: state.cursor,
      });
      return api().requestJson(`/api/admin/push-center/jobs?${params.toString()}`).then((payload) => {
        state.items = payload.items || [];
        state.total = number(payload.total);
        state.nextCursor = payload.next_cursor || "";
        state.hasMore = Boolean(payload.has_more);
        const counts = payload.counts || {};
        root.querySelectorAll("[data-stat]").forEach((node) => {
          node.textContent = number(counts[node.dataset.stat]);
        });
        root.querySelector("#totalText").textContent = `共 ${state.total} 条`;
        populateOptions(form.querySelector("[data-status-filter]"), payload.status_definitions || [], state.status);
        renderSections(payload.sections || []);
        renderRows(state.items);
        renderPagination(root, state, load);
        replaceBrowserQuery(params);
      }).catch((error) => {
        showError(root, error);
        tbody.innerHTML = '<tr><td colspan="7" class="admin-empty-cell">读取失败</td></tr>';
      });
    };

    root.querySelector("#sectionTabs").addEventListener("click", (event) => {
      const button = event.target.closest("[data-section]");
      if (!button) return;
      state.section = button.dataset.section || "";
      state.status = form.querySelector("[data-status-filter]").value;
      state.cursor = "";
      state.cursorStack = [];
      load();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      state.status = form.querySelector("[data-status-filter]").value;
      state.cursor = "";
      state.cursorStack = [];
      load();
    });
    form.addEventListener("reset", (event) => {
      event.preventDefault();
      clearForm(form);
      state.section = "";
      state.status = "";
      state.cursor = "";
      state.cursorStack = [];
      load();
    });
    bindShellActions(load, () => downloadRows("push-center.csv", state.items));
    loadCapacitySummary(root, ["wecom_interactive", "wecom_bulk", "wecom_media", "outbound_webhook"]);
    load();
  }

  function pushActionId(detailId) {
    const value = String(detailId || "");
    if (value.startsWith("external_effect_job:")) return value.split(":", 2)[1] || "";
    return /^\d+$/.test(value) ? value : "";
  }

  function bootPushDetail(root) {
    const detailId = root.dataset.detailId || "";
    const loading = root.querySelector("[data-detail-loading]");
    const content = root.querySelector("[data-detail-content]");
    let currentJob = {};

    const render = (payload) => {
      const job = payload.job || {};
      currentJob = job;
      const attempts = payload.attempts || [];
      const linkedCounts = job.linked_record_counts || {};
      const actionId = pushActionId(detailId);
      const rawStatuses = Object.values(job.raw_statuses || {}).flat().concat(job.raw_status || []);
      const canRetry = actionId && rawStatuses.some((status) => [
        "failed_retryable",
        "failed_terminal",
        "blocked",
        "unknown_after_dispatch",
      ].includes(status));
      const canCancel = actionId && rawStatuses.some((status) => ["planned", "approved", "queued", "failed_retryable", "dispatching"].includes(status));
      const broadcastId = String(detailId).startsWith("broadcast_job:") ? String(detailId).split(":", 2)[1] : "";
      content.innerHTML = `
        <article class="admin-card execution-detail-section">
          <div class="execution-item-head"><h2>当前状态</h2>${statusBadge(job.effective_status || job.status, job.effective_status_label || job.status_label)}</div>
          ${definitionList([
            ["任务", job.display_id || job.projection_id || job.id],
            ["原始状态", rawStatuses.join(", ")],
            ["板块", job.section_label || job.section],
            ["动作类型", job.effect_type],
            ["排队状态", queueStateLabel(job.queue_state)],
            ["交付状态", deliveryStateLabel(job.delivery_state)],
            ["所属 lane", job.lane],
            ["execution_id", job.execution_id],
            ["parent_execution_id", job.parent_execution_id],
            ["等待原因", job.wait_reason],
            ["已等待", `${number(job.queue_wait_seconds)} 秒`],
            ["本 lane 前方", `${number(job.lane_ahead_count)} 条（动态快照）`],
            ["本 lane 产能", `${number(job.lane_in_flight)} / ${number(job.lane_capacity)}`],
            ["policy version", job.policy_version],
            ["可执行时间", formatTime(job.available_at)],
            ["创建时间", formatTime(job.created_at)],
            ["更新时间", formatTime(job.updated_at)],
          ])}
          <div class="execution-detail-actions">
            ${canRetry ? '<button class="admin-button admin-button--primary" type="button" data-push-action="retry">重试</button>' : ""}
            ${canCancel ? '<button class="admin-button admin-button--secondary" type="button" data-push-action="cancel">请求取消</button>' : ""}
            ${broadcastId ? `<a class="admin-button admin-button--secondary" href="/admin/broadcast-jobs/${encodeURIComponent(broadcastId)}">查看群发任务</a>` : ""}
          </div>
        </article>
        <article class="admin-card execution-detail-section">
          <h2>业务上下文</h2>
          ${definitionList([
            ["业务类型", job.business_type],
            ["业务 ID", job.business_id],
            ["目标类型", job.target_type],
            ["目标", job.target_id || job.external_userid],
            ["负责人", job.owner_userid],
            ["来源", job.source_module || job.source_route],
            ["trace_id", job.trace_id],
          ])}
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>执行尝试</h2>
          <div class="execution-attempt-list">${attempts.length ? attempts.map((attempt) => `
            <div class="execution-attempt">
              <div class="execution-item-head"><strong>${escapeHtml(attempt.attempt_id || `#${attempt.id || "-"}`)}</strong>${statusBadge(attempt.effective_status || attempt.status, attempt.status_label || attempt.status)}</div>
              ${definitionList([
                ["适配器", attempt.adapter_name || attempt.operation],
                ["开始", formatTime(attempt.started_at)],
                ["完成", formatTime(attempt.completed_at)],
                ["错误", [attempt.error_code, attempt.error_message].filter(Boolean).join(" · ")],
              ])}
            </div>`).join("") : '<div class="admin-state">暂无执行尝试</div>'}</div>
        </article>
        <article class="admin-card execution-detail-section">
          <h2>关联记录</h2>
          ${definitionList(Object.entries(linkedCounts).map(([key, value]) => [key, value]))}
        </article>
        <article class="admin-card execution-detail-section">
          <h2>安全摘要</h2>
          ${jsonDetails("Payload 摘要", job.payload_summary_json || job.payload_summary)}
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>Lane 产能</h2>
          <div data-capacity-summary></div>
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>统一执行时间线</h2>
          <div data-execution-timeline></div>
        </article>`;
      content.hidden = false;
      loading.hidden = true;
      loadCapacitySummary(root, job.lane ? [job.lane] : []);
      loadExecutionTimeline(root, job.root_execution_id || job.parent_execution_id || job.execution_id);
    };

    const load = () => {
      clearError(root);
      loading.hidden = false;
      content.hidden = true;
      return api().requestJson(`/api/admin/push-center/jobs/${encodeURIComponent(detailId)}`).then(render).catch((error) => {
        loading.hidden = true;
        showError(root, error);
      });
    };

    content.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-push-action]");
      if (!button) return;
      const action = button.dataset.pushAction;
      const currentRawStatuses = Object.values(currentJob.raw_statuses || {}).flat().concat(currentJob.raw_status || []);
      const duplicateRiskRequired = action === "retry" && currentRawStatuses.includes("unknown_after_dispatch");
      const confirmation = await requestAction(root, {
        title: action === "retry" ? "确认重试任务" : "确认取消任务",
        description: action === "retry"
          ? "系统会重新进入现有执行队列。"
          : currentRawStatuses.includes("dispatching")
            ? "任务可能已跨过服务商调用边界；取消已请求，但消息仍可能已经送达。"
            : "取消后该任务不会继续执行。",
        reasonRequired: true,
        actor: root.dataset.operatorActor,
        expectedVersion: currentJob.row_version,
        duplicateRiskRequired,
      });
      if (!confirmation.confirmed) return;
      button.disabled = true;
      api().requestJson(`/api/admin/push-center/jobs/${encodeURIComponent(pushActionId(detailId))}/${action}`, {
        method: "POST",
        body: {
          actor: confirmation.actor,
          reason: confirmation.reason,
          expected_version: confirmation.expectedVersion,
          duplicate_risk_confirmed: confirmation.duplicateRiskConfirmed,
        },
      }).then(load).catch((error) => showError(root, error)).finally(() => {
        button.disabled = false;
      });
    });
    load();
  }

  function bootInternalList(root) {
    const form = root.querySelector("#filterForm");
    const tbody = root.querySelector("[data-events-body]");
    const initial = new URLSearchParams(window.location.search);
    const state = {
      section: initial.get("event_section") || "",
      consumerStatus: initial.get("consumer_status") || "",
      offset: Math.max(0, number(initial.get("offset"))),
      limit: PAGE_SIZE,
      total: 0,
      items: [],
    };
    applyQueryToForm(form, initial);

    const renderFilterOptions = (options) => {
      const sections = [{ key: "", label: "全部" }, ...((options && options.event_sections) || [])];
      root.querySelector("#sectionTabs").innerHTML = sections.map((section) => (
        `<button class="execution-tab${state.section === section.key ? " is-active" : ""}" type="button" data-section="${escapeHtml(section.key)}">${escapeHtml(section.label)}</button>`
      )).join("");
      populateOptions(
        form.querySelector("[data-consumer-status-filter]"),
        (options && options.consumer_statuses) || [],
        state.consumerStatus,
      );
      const fillList = (selector, values) => {
        const node = root.querySelector(selector);
        if (node) node.innerHTML = (values || []).map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
      };
      fillList("#eventTypeOptions", options && options.event_types);
      fillList("#consumerOptions", options && options.consumers);
    };

    const renderRows = (items) => {
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="admin-empty-cell">暂无符合条件的事件</td></tr>';
        return;
      }
      tbody.innerHTML = items.map((item) => {
        const failed = number(item.failed_count) + number(item.blocked_count);
        const reconciliation = item.reconciliation_summary || {};
        return `<tr>
          <td><a class="execution-table-link execution-mono" href="/admin/internal-events/${encodeURIComponent(item.event_id)}">${escapeHtml(item.event_type)}</a><div class="execution-muted">${escapeHtml(item.event_id)}</div></td>
          <td class="execution-mono">${escapeHtml(item.aggregate || "-")}</td>
          <td>${number(item.consumer_total)}</td>
          <td>${number(item.succeeded_count)}</td>
          <td>${failed}</td>
          <td>${number(item.pending_count)}</td>
          <td>${statusBadge(item.derived_status || (reconciliation.unresolved_consumer_count ? "pending" : "succeeded"), item.derived_status || `未决 ${number(reconciliation.unresolved_consumer_count)}`)}</td>
          <td>${escapeHtml(formatTime(item.occurred_at || item.created_at))}</td>
        </tr>`;
      }).join("");
    };

    const load = () => {
      clearError(root);
      tbody.innerHTML = '<tr><td colspan="8" class="admin-empty-cell">加载中...</td></tr>';
      const params = formParams(form, {
        event_section: state.section,
        consumer_status: state.consumerStatus,
        limit: state.limit,
        offset: state.offset,
      });
      return api().requestJson(`/api/admin/internal-events?${params.toString()}`).then((payload) => {
        state.items = payload.items || [];
        state.total = number(payload.total);
        const counts = payload.counts || {};
        root.querySelectorAll("[data-stat]").forEach((node) => {
          node.textContent = number(counts[node.dataset.stat]);
        });
        root.querySelector("#totalText").textContent = `共 ${state.total} 条`;
        const semantics = root.querySelector("[data-count-semantics]");
        if (semantics && payload.count_semantics) semantics.textContent = payload.count_semantics;
        renderFilterOptions(payload.filter_options || {});
        renderRows(state.items);
        renderPagination(root, state, load);
        replaceBrowserQuery(params);
      }).catch((error) => {
        showError(root, error);
        tbody.innerHTML = '<tr><td colspan="8" class="admin-empty-cell">读取失败</td></tr>';
      });
    };

    root.querySelector("#sectionTabs").addEventListener("click", (event) => {
      const button = event.target.closest("[data-section]");
      if (!button) return;
      state.section = button.dataset.section || "";
      state.consumerStatus = form.querySelector("[data-consumer-status-filter]").value;
      state.offset = 0;
      load();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      state.consumerStatus = form.querySelector("[data-consumer-status-filter]").value;
      state.offset = 0;
      load();
    });
    form.addEventListener("reset", (event) => {
      event.preventDefault();
      clearForm(form);
      state.section = "";
      state.consumerStatus = "";
      state.offset = 0;
      load();
    });
    bindShellActions(load, () => downloadRows("internal-events.csv", state.items));
    loadCapacitySummary(root, ["internal_general", "internal_financial"]);
    load();
  }

  function bootInternalDetail(root) {
    const eventId = root.dataset.detailId || "";
    const loading = root.querySelector("[data-detail-loading]");
    const content = root.querySelector("[data-detail-content]");
    let currentRuns = [];

    const render = (payload) => {
      const event = payload.event || {};
      const runs = payload.consumer_runs || [];
      currentRuns = runs;
      const attempts = payload.attempts || [];
      const reconciliation = payload.reconciliation_summary || {};
      const externalEffects = (payload.reconciliation && payload.reconciliation.external_effects) || [];
      content.innerHTML = `
        <article class="admin-card execution-detail-section">
          <div class="execution-item-head"><h2>业务事实</h2>${statusBadge(payload.derived_status || "pending", payload.derived_status || "消费者执行中")}</div>
          ${definitionList([
            ["事件 ID", event.event_id],
            ["事件类型", event.event_type],
            ["业务对象", event.aggregate],
            ["来源模块", event.source_module],
            ["execution_id", event.execution_id],
            ["parent_execution_id", event.parent_execution_id],
            ["排队状态", queueStateLabel(event.queue_state)],
            ["所属 lane", (event.lanes || []).join(", ")],
            ["发生时间", formatTime(event.occurred_at)],
            ["trace_id", event.trace_id],
          ])}
          ${jsonDetails("Payload 摘要", payload.payload_summary_json)}
        </article>
        <article class="admin-card execution-detail-section">
          <h2>消费者口径</h2>
          ${definitionList([
            ["消费者总数", event.consumer_total],
            ["成功", event.succeeded_count],
            ["失败", event.failed_count],
            ["待处理", event.pending_count],
            ["已跳过", event.skipped_count],
            ["未决消费者", reconciliation.unresolved_consumer_count],
            ["灰度未放行", reconciliation.rollout_gated_consumer_count],
          ])}
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>消费者执行</h2>
          <div class="execution-consumer-list">${runs.length ? runs.map((run) => `
            <div class="execution-consumer">
              <div class="execution-item-head"><strong>${escapeHtml(run.consumer_name)}</strong>${statusBadge(run.status, run.status)}</div>
              ${definitionList([
                ["类型", run.consumer_type],
                ["尝试次数", `${number(run.attempt_count)} / ${number(run.max_attempts)}`],
                ["下次可执行", formatTime(run.next_retry_at)],
                ["Lane", run.lane],
                ["排队状态", queueStateLabel(run.queue_state)],
                ["等待原因", run.wait_reason],
                ["policy version", run.policy_version],
                ["最近错误", [run.last_error_code, run.last_error_message].filter(Boolean).join(" · ")],
              ])}
              <div class="execution-detail-actions">
                ${run.retryable ? `<button class="admin-button admin-button--primary" type="button" data-consumer-action="retry" data-consumer-name="${escapeHtml(run.consumer_name)}">重试</button>` : ""}
                ${run.skippable ? `<button class="admin-button admin-button--secondary" type="button" data-consumer-action="skip" data-consumer-name="${escapeHtml(run.consumer_name)}">跳过</button>` : ""}
              </div>
            </div>`).join("") : '<div class="admin-state">暂无消费者执行记录</div>'}</div>
        </article>
        <article class="admin-card execution-detail-section">
          <h2>执行尝试</h2>
          <div class="execution-attempt-list">${attempts.length ? attempts.map((attempt) => `
            <div class="execution-attempt"><div class="execution-item-head"><strong>${escapeHtml(attempt.consumer_name || attempt.attempt_id)}</strong>${statusBadge(attempt.status, attempt.status)}</div><div class="execution-muted">${escapeHtml(text(attempt.error))} · ${escapeHtml(formatTime(attempt.completed_at || attempt.started_at))}</div></div>
          `).join("") : '<div class="admin-state">暂无执行尝试</div>'}</div>
        </article>
        <article class="admin-card execution-detail-section">
          <h2>业务效果核对</h2>
          ${definitionList([
            ["External Effects", reconciliation.external_effect_count],
            ["状态", (reconciliation.external_effect_statuses || []).join(", ")],
            ["Placeholder Consumers (not actionable)", reconciliation.placeholder_consumer_count],
          ])}
          <div class="execution-detail-actions">${externalEffects.map((effect) => {
            const id = effect.job_id || effect.id;
            return id ? `<a class="admin-button admin-button--secondary" href="/admin/push-center/jobs/${encodeURIComponent(`external_effect_job:${id}`)}">推送任务 #${escapeHtml(id)}</a>` : "";
          }).join("")}</div>
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>Lane 产能</h2>
          <div data-capacity-summary></div>
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>统一执行时间线</h2>
          <div data-execution-timeline></div>
        </article>`;
      content.hidden = false;
      loading.hidden = true;
      loadCapacitySummary(root, (event.lanes || []).length ? event.lanes : ["internal_general", "internal_financial"]);
      loadExecutionTimeline(root, event.execution_id);
    };

    const load = () => {
      clearError(root);
      loading.hidden = false;
      content.hidden = true;
      return api().requestJson(`/api/admin/internal-events/${encodeURIComponent(eventId)}`).then(render).catch((error) => {
        loading.hidden = true;
        showError(root, error);
      });
    };

    content.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-consumer-action]");
      if (!button) return;
      const action = button.dataset.consumerAction;
      const consumerName = button.dataset.consumerName;
      const run = currentRuns.find((item) => item.consumer_name === consumerName) || {};
      const confirmation = await requestAction(root, {
        title: action === "retry" ? "重试消费者" : "跳过消费者",
        description: action === "retry" ? "该消费者会重新进入可执行状态。" : "跳过后会留下人工操作审计。",
        reasonRequired: true,
        actor: root.dataset.operatorActor,
        expectedVersion: run.row_version,
      });
      if (!confirmation.confirmed) return;
      button.disabled = true;
      api().requestJson(`/api/admin/internal-events/${encodeURIComponent(eventId)}/consumers/${encodeURIComponent(consumerName)}/${action}`, {
        method: "POST",
        body: {
          actor: confirmation.actor,
          reason: confirmation.reason,
          expected_version: confirmation.expectedVersion,
        },
      }).then(load).catch((error) => showError(root, error)).finally(() => {
        button.disabled = false;
      });
    });
    load();
  }

  function feishuStatusLabel(status) {
    return ({ unconfigured: "未配置", unverified: "未验证", valid: "验证成功", invalid: "验证失败" })[status] || text(status);
  }

  function renderFeishuSetting(root, setting) {
    root.querySelector("[data-feishu-enabled]").textContent = setting.enabled ? "已启用" : "已停用";
    root.querySelector("[data-feishu-validation-status]").textContent = feishuStatusLabel(setting.validationStatus);
    root.querySelector("[data-feishu-validated-at]").textContent = formatTime(setting.validatedAt);
    root.querySelector("[data-feishu-last-error]").textContent = text(setting.lastValidationError);
    root.querySelector("[data-feishu-enabled-input]").checked = Boolean(setting.enabled);
    root.querySelector("[data-feishu-masked]").value = setting.webhookMasked || "未配置";
  }

  function showFeishuMessage(root, message, ok) {
    const node = root.querySelector("[data-feishu-message]");
    node.textContent = message;
    node.className = `admin-alert ${ok ? "admin-alert--success" : "admin-alert--error"}`;
    node.hidden = false;
  }

  function feishuPayload(root) {
    return {
      enabled: root.querySelector("[data-feishu-enabled-input]").checked,
      webhookUrl: root.querySelector("[data-feishu-webhook-input]").value.trim(),
    };
  }

  function bindFeishu(root) {
    const settings = root.querySelector("[data-feishu-monitor-settings]");
    if (!settings) return;
    const overlay = root.querySelector("[data-feishu-overlay]");
    const read = () => api().requestJson("/api/admin/broadcast-jobs/notification-settings/feishu").then((payload) => renderFeishuSetting(settings, payload));
    root.querySelector("[data-feishu-open]").addEventListener("click", () => {
      overlay.hidden = false;
      read().catch((error) => showFeishuMessage(settings, error.message || "读取飞书配置失败", false));
    });
    root.querySelector("[data-feishu-close]").addEventListener("click", () => { overlay.hidden = true; });
    overlay.addEventListener("click", (event) => { if (event.target === overlay) overlay.hidden = true; });
    root.querySelector("[data-feishu-save]").addEventListener("click", () => {
      const payload = feishuPayload(settings);
      if (!payload.webhookUrl) {
        showFeishuMessage(settings, "请填写飞书机器人地址。", false);
        return;
      }
      api().requestJson("/api/admin/broadcast-jobs/notification-settings/feishu", { method: "PUT", body: payload })
        .then((result) => {
          renderFeishuSetting(settings, result);
          settings.querySelector("[data-feishu-webhook-input]").value = "";
          showFeishuMessage(settings, "飞书机器人地址已保存，验证前不会用于监控推送。", true);
        })
        .catch((error) => showFeishuMessage(settings, error.message || "保存失败", false));
    });
    root.querySelector("[data-feishu-validate]").addEventListener("click", () => {
      const payload = feishuPayload(settings);
      if (!payload.webhookUrl) {
        showFeishuMessage(settings, "请填写飞书机器人地址后再验证。", false);
        return;
      }
      api().requestJson("/api/admin/broadcast-jobs/notification-settings/feishu/validate", { method: "POST", body: payload })
        .then((result) => {
          renderFeishuSetting(settings, {
            enabled: payload.enabled,
            webhookMasked: result.webhookMasked,
            validationStatus: result.validationStatus || "unverified",
            validatedAt: null,
            lastValidationError: null,
          });
          settings.querySelector("[data-feishu-webhook-input]").value = "";
          showFeishuMessage(settings, `验证任务已排队${result.externalEffectJobId ? `（#${result.externalEffectJobId}）` : ""}；服务商回执确认前不会显示为验证成功。`, true);
        })
        .catch(() => showFeishuMessage(settings, "验证失败，请检查飞书机器人地址或机器人配置。", false));
    });
  }

  async function runBroadcastAction(root, button, reload) {
    const action = button.dataset.broadcastAction;
    const jobId = button.dataset.jobId;
    const confirmation = await requestAction(root, {
      title: action === "approve" ? "审批通过群发任务" : "取消群发任务",
      description: action === "approve" ? "审批后任务会进入正常排队。" : "取消后任务不会继续发送。",
      reasonRequired: true,
      actor: root.dataset.operatorActor,
      expectedVersion: button.dataset.rowVersion,
    });
    if (!confirmation.confirmed) return;
    button.disabled = true;
    api().requestJson(`/api/admin/broadcast-jobs/${encodeURIComponent(jobId)}/${action}`, {
      method: "POST",
      body: {
        actor: confirmation.actor,
        reason: confirmation.reason,
        expected_version: confirmation.expectedVersion,
      },
    }).then(reload).catch((error) => showError(root, error)).finally(() => {
      button.disabled = false;
    });
  }

  function bootBroadcastList(root) {
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-broadcast-action]");
      if (button) runBroadcastAction(root, button, () => window.location.reload());
    });
    bindFeishu(root);
  }

  function bootBroadcastDetail(root) {
    const jobId = root.dataset.detailId || "";
    const loading = root.querySelector("[data-detail-loading]");
    const content = root.querySelector("[data-detail-content]");

    const render = (payload) => {
      const job = payload.job || {};
      const batch = payload.batch || {};
      const events = payload.events || [];
      content.innerHTML = `
        <article class="admin-card execution-detail-section">
          <div class="execution-item-head"><h2>当前状态</h2>${statusBadge(job.status, job.status_label)}</div>
          ${definitionList([
            ["任务", `#${job.id || jobId}`],
            ["来源", `${job.source_type_label || job.source_type} · ${job.source_detail_label || ""}`],
            ["业务归类", job.business_domain_label || job.business_domain],
            ["渠道", job.channel_label || job.channel],
            ["执行所有者", job.execution_owner],
            ["execution_id", job.execution_id],
            ["External Effect", job.external_effect_job_id ? `#${job.external_effect_job_id}` : "-"],
            ["计划时间", job.scheduled_for_label || formatTime(job.scheduled_for)],
            ["更新时间", formatTime(job.updated_at)],
          ])}
          <div class="execution-detail-actions">
            ${job.can_approve ? `<button class="admin-button admin-button--primary" type="button" data-broadcast-action="approve" data-job-id="${escapeHtml(job.id)}" data-row-version="${Math.max(1, number(job.row_version))}">审批通过</button>` : ""}
            ${job.can_cancel ? `<button class="admin-button admin-button--secondary" type="button" data-broadcast-action="cancel" data-job-id="${escapeHtml(job.id)}" data-row-version="${Math.max(1, number(job.row_version))}">取消</button>` : ""}
            <a class="admin-button admin-button--secondary" href="${escapeHtml(payload.push_center_url || `/admin/push-center?business_id=${job.id}`)}">查看推送中心证据</a>
          </div>
        </article>
        <article class="admin-card execution-detail-section">
          <h2>任务摘要</h2>
          ${definitionList([
            ["目标类型", job.target_kind_label || job.target_kind],
            ["目标数量", job.target_count],
            ["目标摘要", job.target_summary_label || job.target_summary],
            ["内容类型", job.content_type],
            ["内容摘要", job.content_summary_label || job.content_summary],
            ["尝试次数", job.attempt_count],
            ["发送结果", `成功 ${number(job.sent_count)} / 失败 ${number(job.failed_count)}`],
            ["最近错误", job.last_error],
          ])}
        </article>
        <article class="admin-card execution-detail-section">
          <h2>批次</h2>
          ${definitionList([
            ["批次键", batch.batch_key],
            ["批次状态", batch.status],
            ["消息数", batch.message_count],
            ["窗口开始", formatTime(batch.window_start)],
            ["窗口结束", formatTime(batch.window_end)],
            ["确认时间", formatTime(batch.acked_at)],
          ])}
        </article>
        <article class="admin-card execution-detail-section">
          <h2>关联记录</h2>
          ${definitionList(Object.entries(payload.linked_record_counts || {}).map(([key, value]) => [key, value]))}
        </article>
        <article class="admin-card execution-detail-section execution-detail-section--wide">
          <h2>生命周期事件</h2>
          <div class="execution-event-list">${events.length ? events.map((item) => `
            <div class="execution-event"><div class="execution-item-head"><strong>${escapeHtml(text(item.event_type))}</strong><span>${escapeHtml(formatTime(item.occurred_at))}</span></div><div class="execution-muted">${escapeHtml(text(item.chat_type))} · ${item.has_external_target ? "含已脱敏目标" : "无目标明细"} · ${item.has_content ? "含已脱敏内容" : "无内容"}</div></div>
          `).join("") : '<div class="admin-state">暂无生命周期事件</div>'}</div>
        </article>`;
      content.hidden = false;
      loading.hidden = true;
    };

    const load = () => {
      clearError(root);
      loading.hidden = false;
      content.hidden = true;
      return api().requestJson(`/api/admin/broadcast-jobs/${encodeURIComponent(jobId)}`).then(render).catch((error) => {
        loading.hidden = true;
        showError(root, error);
      });
    };
    content.addEventListener("click", (event) => {
      const button = event.target.closest("[data-broadcast-action]");
      if (button) runBroadcastAction(root, button, load);
    });
    load();
  }

  function boot() {
    const root = document.querySelector("[data-execution-page]");
    if (!root || !api()) return;
    const page = root.dataset.executionPage;
    if (page === "push-list") bootPushList(root);
    else if (page === "push-detail") bootPushDetail(root);
    else if (page === "internal-list") bootInternalList(root);
    else if (page === "internal-detail") bootInternalDetail(root);
    else if (page === "broadcast-list") bootBroadcastList(root);
    else if (page === "broadcast-detail") bootBroadcastDetail(root);
  }

  window.AdminExecutionUI = {
    escapeHtml,
    readJson,
    formParams,
    csvCell,
    statusTone,
    statusBadge,
    QueueStateBadge,
    CapacitySummary,
    ExecutionTimeline,
    requestAction,
    requestVersionedQueueCommand,
    boot,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window, document);
