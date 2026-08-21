(function () {
  "use strict";

  const AutomationAutoReply = window.AutomationAutoReply || {};
  window.AutomationAutoReply = AutomationAutoReply;

  const copyReplyText = AutomationAutoReply.copyReplyText;
  const elements = AutomationAutoReply.elements;
  const escapeHtml = AutomationAutoReply.escapeHtml;
  const getAdminActionToken = AutomationAutoReply.getAdminActionToken;
  const getApiUrls = AutomationAutoReply.getApiUrls;
  const requestJson = AutomationAutoReply.requestJson;
  const state = AutomationAutoReply.state;
  const withWebhookOutputId = AutomationAutoReply.withWebhookOutputId;
  const withWecomOutputId = AutomationAutoReply.withWecomOutputId;

  function renderOutputs() {
    const { outputEmptyEl, outputListEl, reviewStatusEl } = elements();
    if (!outputEmptyEl || !outputListEl || !reviewStatusEl) return;
    const rows = Array.isArray(state.outputs) ? state.outputs : [];
    outputEmptyEl.hidden = rows.length > 0;
    outputListEl.hidden = rows.length === 0;
    if (!rows.length) {
      outputListEl.innerHTML = "";
      reviewStatusEl.textContent = "当前没有可评审话术";
      return;
    }
    reviewStatusEl.textContent = `最近 ${rows.length} 条老黄 AI 话术`;
    outputListEl.innerHTML = rows.map((item) => `
      <article class="reply-output-item" data-output-id="${escapeHtml(item.output_id)}">
        <div class="reply-output-head">
          <div>
            <div class="reply-output-title">${escapeHtml(item.external_contact_id || "匿名客户")} · 老黄 AI</div>
            <div class="reply-output-meta">
              <span>${escapeHtml(item.created_at || "-")}</span>
              <span>${escapeHtml(item.external_message_id || item.request_id || "-")}</span>
              <span>${escapeHtml(item.outcome_status_label || "未闭环")}</span>
              ${item.send_record_id ? `<span>send_record_id: ${escapeHtml(item.send_record_id)}</span>` : ""}
            </div>
          </div>
          <div class="reply-output-actions">
            <button class="admin-button" type="button" data-review-action="webhook">一键 webhook</button>
            <button class="admin-button admin-button--secondary" type="button" data-review-action="copy">复制话术</button>
            <button class="admin-button admin-button--ghost" type="button" data-review-action="wecom_send">一键推企微群发</button>
          </div>
        </div>
        <div class="reply-output-body">${escapeHtml(item.rendered_output_text || item.reason || "-")}</div>
        ${item.review_note ? `<div class="reply-output-meta"><span>最近评审备注：${escapeHtml(item.review_note)}</span></div>` : ""}
      </article>
    `).join("");
  }

  async function loadOutputs(root) {
    const apiUrls = getApiUrls(root);
    const { reviewStatusEl } = elements();
    if (!apiUrls.review_outputs || !reviewStatusEl) return;
    reviewStatusEl.textContent = "正在加载最近话术...";
    try {
      const payload = await requestJson(apiUrls.review_outputs);
      state.outputs = payload.rows || [];
      renderOutputs();
    } catch (error) {
      reviewStatusEl.textContent = error.message || "加载最近话术失败";
    }
  }

  function bindOutputList(root) {
    const { outputListEl, reviewStatusEl } = elements();
    if (!outputListEl || !reviewStatusEl) return;
    outputListEl.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-review-action]");
      const itemEl = event.target.closest("[data-output-id]");
      if (!button || !itemEl) return;
      const outputId = itemEl.getAttribute("data-output-id") || "";
      const action = button.getAttribute("data-review-action");
      if (!outputId || !action) return;
      const output = state.outputs.find((item) => String(item.output_id || "") === outputId) || null;
      if (action === "rejected") {
        AutomationAutoReply.openRejectModal(outputId);
        return;
      }
      if (action === "copy") {
        const defaultLabel = button.textContent;
        try {
          await copyReplyText((output && (output.rendered_output_text || output.reason)) || "");
          button.textContent = "已复制";
          reviewStatusEl.textContent = "话术已复制到剪贴板";
        } catch (error) {
          reviewStatusEl.textContent = error.message || "复制失败";
        } finally {
          window.setTimeout(() => {
            button.textContent = defaultLabel;
            renderOutputs();
          }, 1200);
        }
        return;
      }
      if (action === "webhook") {
        if (!window.confirm("确认把这条话术推送到 webhook？")) {
          return;
        }
        const defaultLabel = button.textContent;
        button.disabled = true;
        button.textContent = "推送中...";
        reviewStatusEl.textContent = "正在提交 webhook...";
        try {
          await requestJson(withWebhookOutputId(outputId), {
            method: "POST",
            body: {
              admin_action_token: getAdminActionToken(root),
              operator: "crm_console",
            },
          });
          reviewStatusEl.textContent = "webhook 已推送";
        } catch (error) {
          reviewStatusEl.textContent = error.message || "webhook 推送失败";
        } finally {
          button.disabled = false;
          button.textContent = defaultLabel;
          await loadOutputs(root);
        }
      }
      if (action === "wecom_send") {
        if (!window.confirm("确认一键推企微群发？")) {
          return;
        }
        const defaultLabel = button.textContent;
        button.disabled = true;
        button.textContent = "推送中...";
        reviewStatusEl.textContent = "正在推企微群发...";
        try {
          await requestJson(withWecomOutputId(outputId), {
            method: "POST",
            body: {
              admin_action_token: getAdminActionToken(root),
              operator: "crm_console",
            },
          });
          reviewStatusEl.textContent = "企微群发已受理";
        } catch (error) {
          reviewStatusEl.textContent = error.message || "企微群发失败";
        } finally {
          button.disabled = false;
          button.textContent = defaultLabel;
          await loadOutputs(root);
        }
      }
    });
  }

  AutomationAutoReply.renderOutputs = renderOutputs;
  AutomationAutoReply.loadOutputs = loadOutputs;
  AutomationAutoReply.bindOutputList = bindOutputList;
})();
