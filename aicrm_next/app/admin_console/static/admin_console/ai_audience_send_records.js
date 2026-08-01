(function (global) {
  "use strict";

  const root = document.getElementById("panel-records");
  if (!root) return;

  const apiUrl = String(root.dataset.sendRecordsApiUrl || "").trim();
  const pageSize = 20;
  const els = {
    rows: document.getElementById("sendRecordRows"),
    total: document.getElementById("sendRecordTotal"),
    pageLabel: document.getElementById("sendRecordPageLabel"),
    previous: document.getElementById("sendRecordPrevBtn"),
    next: document.getElementById("sendRecordNextBtn"),
    status: document.getElementById("sendRecordStatusLine"),
    drawerMask: document.getElementById("sendRecordDrawerMask"),
    drawer: document.getElementById("sendRecordDrawer"),
    closeDrawer: document.getElementById("closeSendRecordDrawerBtn"),
    drawerSubtitle: document.getElementById("sendRecordDrawerSubtitle"),
    drawerMeta: document.getElementById("sendRecordMeta"),
    contentDetail: document.getElementById("sendRecordContentDetail"),
  };
  let offset = 0;
  let total = 0;
  let loaded = false;
  let initialLoad = null;
  let returnFocus = null;
  let bodyOverflowBeforeDrawer = "";

  const escapeHtml = (value) => global.AICRMSendContentReadonlyDetail
    ? global.AICRMSendContentReadonlyDetail.escapeHtml(value)
    : String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  function errorMessage(error, fallback) {
    return global.AdminApi && typeof global.AdminApi.errorMessage === "function"
      ? global.AdminApi.errorMessage(error, fallback)
      : fallback;
  }

  function setStatus(text, tone = "") {
    els.status.textContent = text;
    els.status.className = tone ? `ai-status-line ${tone}` : "ai-status-line";
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(date).reduce((result, item) => {
      result[item.type] = item.value;
      return result;
    }, {});
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  }

  function tone(status) {
    return ["sent", "failed", "retrying", "sending", "unknown_after_dispatch"].includes(status)
      ? status
      : "";
  }

  function closeDrawer() {
    const wasOpen = els.drawer.getAttribute("aria-hidden") === "false";
    els.drawerMask.classList.remove("open");
    els.drawer.classList.remove("open");
    els.drawer.setAttribute("aria-hidden", "true");
    if (!wasOpen) return;
    document.body.style.overflow = bodyOverflowBeforeDrawer;
    if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
    returnFocus = null;
  }

  function openDrawer(trigger) {
    returnFocus = trigger || document.activeElement;
    bodyOverflowBeforeDrawer = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    els.drawerMask.classList.add("open");
    els.drawer.classList.add("open");
    els.drawer.setAttribute("aria-hidden", "false");
    els.closeDrawer.focus();
  }

  function metaCell(label, value, full = false) {
    return `
      <div class="ai-detail-cell${full ? " full" : ""}">
        <div class="label">${escapeHtml(label)}</div>
        <div class="value">${escapeHtml(value || "—")}</div>
      </div>
    `;
  }

  async function openDetail(recordId, trigger) {
    if (!recordId) return;
    openDrawer(trigger);
    els.drawerSubtitle.textContent = "发送明细加载中";
    els.drawerMeta.innerHTML = "";
    els.contentDetail.innerHTML = '<div class="ai-empty">加载中</div>';
    const payload = await global.AdminApi.requestJson(
      `${apiUrl}/${encodeURIComponent(recordId)}?_=${Date.now()}`,
      { cache: "no-store" },
    );
    const record = payload.record || {};
    els.drawerSubtitle.textContent = `${record.source_label || "发送"} · ${record.status_label || "—"}`;
    els.drawerMeta.innerHTML = [
      metaCell("联系人", record.nickname || "未命名客户"),
      metaCell("外部联系人 ID", record.external_userid || "—"),
      metaCell("发送来源", record.source_label || "—"),
      metaCell("发送状态", record.status_label || "—"),
      metaCell("发送时间", record.send_time ? formatTime(record.send_time) : "—"),
      metaCell("技术尝试次数", String(Number(record.technical_attempt_count || 0))),
      ...(record.failure_reason ? [metaCell("失败原因", record.failure_reason, true)] : []),
    ].join("");
    if (!global.AICRMSendContentReadonlyDetail) throw new Error("只读发送内容组件加载失败");
    els.contentDetail.innerHTML = global.AICRMSendContentReadonlyDetail.renderFull(record);
  }

  function render(payload) {
    const items = Array.isArray(payload.items) ? payload.items : [];
    total = Number(payload.total || 0);
    offset = Number(payload.offset || 0);
    const currentPage = total ? Math.floor(offset / pageSize) + 1 : 0;
    const totalPages = total ? Math.ceil(total / pageSize) : 0;
    els.total.textContent = `共 ${formatNumber(total)} 条`;
    els.pageLabel.textContent = `第 ${currentPage} / ${totalPages} 页`;
    els.previous.disabled = offset <= 0;
    els.next.disabled = offset + items.length >= total;
    if (!items.length) {
      els.rows.innerHTML = '<tr><td class="ai-empty" colspan="7">暂无可准确追溯的发送记录</td></tr>';
      return;
    }
    els.rows.innerHTML = items.map((item) => `
      <tr>
        <td><div class="ai-record-name"><strong>${escapeHtml(item.nickname || "未命名客户")}</strong></div></td>
        <td class="ai-mono ai-record-id">${escapeHtml(item.external_userid || "—")}</td>
        <td>${escapeHtml(item.source_label || "自动化话术")}</td>
        <td><span class="ai-record-status ${tone(item.status)}">${escapeHtml(item.status_label || "—")}</span></td>
        <td>${item.send_time ? escapeHtml(formatTime(item.send_time)) : "—"}</td>
        <td class="ai-record-failure" title="${escapeHtml(item.failure_reason || "")}">${escapeHtml(item.failure_reason || "—")}</td>
        <td><button class="ai-btn soft" type="button" data-send-record-detail="${escapeHtml(item.record_id)}" ${item.detail_available ? "" : "disabled"}>查看详情</button></td>
      </tr>
    `).join("");
    els.rows.querySelectorAll("[data-send-record-detail]").forEach((button) => {
      button.addEventListener("click", () => {
        openDetail(button.dataset.sendRecordDetail, button).catch((error) => {
          els.contentDetail.innerHTML = `<div class="ai-empty">${escapeHtml(errorMessage(error, "发送明细加载失败"))}</div>`;
        });
      });
    });
  }

  async function load(nextOffset = offset) {
    const safeOffset = Math.max(0, Number(nextOffset || 0));
    els.rows.innerHTML = '<tr><td class="ai-empty" colspan="7">发送记录加载中</td></tr>';
    setStatus("加载中...");
    try {
      const payload = await global.AdminApi.requestJson(
        `${apiUrl}?limit=${pageSize}&offset=${safeOffset}&_=${Date.now()}`,
        { cache: "no-store" },
      );
      loaded = true;
      render(payload);
      setStatus("");
      return payload;
    } catch (error) {
      els.rows.innerHTML = '<tr><td class="ai-empty" colspan="7">发送记录加载失败，请稍后重试</td></tr>';
      els.total.textContent = "加载失败";
      setStatus(errorMessage(error, "发送记录加载失败"), "error");
      throw error;
    }
  }

  function enter() {
    if (loaded) return Promise.resolve();
    if (!initialLoad) initialLoad = load(0).finally(() => { initialLoad = null; });
    return initialLoad.catch(() => undefined);
  }

  els.previous.addEventListener("click", () => { load(Math.max(0, offset - pageSize)).catch(() => undefined); });
  els.next.addEventListener("click", () => { load(offset + pageSize).catch(() => undefined); });
  els.closeDrawer.addEventListener("click", closeDrawer);
  els.drawerMask.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });

  global.AICRMAudienceSendRecords = Object.freeze({ enter, refresh: () => load(offset) });
})(window);
