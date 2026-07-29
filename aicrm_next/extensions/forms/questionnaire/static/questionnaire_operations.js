(function (window, document) {
  "use strict";

  function initialize() {
  const root = document.querySelector(".qo-page");
  const configNode = document.getElementById("questionnaire-operations-config");
  if (!root || !configNode) return;

  const api = window.AdminApi;
  const targetController = window.AICRMCompletionTargetConfig;
  const questionnaireId = Number(root.dataset.questionnaireId || 0);
  const state = {
    payload: JSON.parse(configNode.textContent || "{}"),
    activePanel: "completion",
    completionMode: "lead_qr",
    channels: [],
  };
  const $ = (id) => document.getElementById(id);

  function text(value) { return String(value == null ? "" : value).trim(); }
  function setToast(message, isError = false) {
    const element = $("qo-toast");
    element.textContent = message || "";
    element.classList.toggle("is-error", Boolean(isError));
  }
  function request(url, options = {}) {
    return api.requestJson(url, options);
  }
  function numberOrNull(id) {
    const value = text($(id).value);
    return value ? Number(value) : null;
  }
  function switchPanel(name) {
    state.activePanel = name === "external-push" ? "external-push" : "completion";
    document.querySelectorAll("[data-qo-panel]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.qoPanel === state.activePanel);
    });
    document.querySelectorAll("[data-qo-panel-content]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.qoPanelContent === state.activePanel);
    });
  }
  function completionEnabled() { return Boolean($("qo-completion-enabled").checked); }
  function syncCompletionVisibility() {
    const enabled = completionEnabled();
    $("qo-completion-enabled-text").textContent = enabled ? "已启用" : "未启用";
    $("qo-completion-body").hidden = !enabled;
    $("qo-lead-fields").hidden = !enabled || state.completionMode !== "lead_qr";
    $("qo-redirect-fields").hidden = !enabled || state.completionMode !== "redirect";
    const targetToggle = document.querySelector("[data-completion-target-config] [data-target-enabled]");
    if (targetToggle) {
      targetToggle.checked = enabled && state.completionMode === "redirect";
      targetToggle.dispatchEvent(new Event("change", { bubbles: true }));
    }
    document.querySelectorAll("[data-qo-completion-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.qoCompletionMode === state.completionMode);
    });
    updateSummary();
  }
  function updateSummary() {
    const completion = state.payload.completion || {};
    const completionLabel = !completionEnabled()
      ? "未启用"
      : (state.completionMode === "lead_qr" ? "渠道二维码" : "直接跳转");
    $("qo-summary-completion").textContent = completionLabel;
    $("qo-summary-push").textContent = $("qo-push-enabled").checked ? "已启用" : "未启用";
    completion.enabled = completionEnabled();
  }
  function selectableChannel(channel) {
    const status = text(channel.status) || "active";
    const carrier = text(channel.carrier_type) || (channel.channel_type === "wecom_customer_acquisition" ? "link" : "qrcode");
    const qrUrl = text(channel.active_qrcode_asset_url || channel.qr_url);
    const assetId = Number(channel.qrcode_asset_id || channel.active_qrcode_asset_id || 0);
    const qrStatus = text(channel.qrcode_status) || (qrUrl ? "legacy_untracked" : "not_generated");
    return status === "active"
      && carrier === "qrcode"
      && assetId > 0
      && ["active", "generated", "legacy_untracked"].includes(qrStatus)
      && qrUrl.startsWith("https://");
  }
  function normalizedChannel(channel) {
    return {
      channel_id: Number(channel.channel_id || channel.id || 0),
      channel_name: text(channel.channel_name) || `渠道 ${channel.channel_id || channel.id}`,
      qr_url: text(channel.active_qrcode_asset_url || channel.qr_url),
      selectable: selectableChannel(channel),
    };
  }
  function renderChannels() {
    const selectedId = Number((state.payload.completion || {}).lead_channel_id || 0);
    const options = state.channels.map((channel) => {
      const suffix = channel.selectable ? "" : " · 暂不可用";
      return `<option value="${channel.channel_id}"${channel.selectable ? "" : " disabled"}>${api.escapeHtml(channel.channel_name + suffix)}</option>`;
    });
    $("qo-lead-channel").innerHTML = options.length ? `<option value="">请选择渠道码</option>${options.join("")}` : '<option value="">暂无可用二维码渠道</option>';
    if (selectedId) $("qo-lead-channel").value = String(selectedId);
    renderChannelPreview();
  }
  function renderChannelPreview() {
    const channelId = Number($("qo-lead-channel").value || 0);
    const channel = state.channels.find((item) => item.channel_id === channelId && item.selectable);
    $("qo-channel-preview").hidden = !channel;
    if (!channel) return;
    $("qo-channel-preview-image").src = channel.qr_url;
    $("qo-channel-preview-name").textContent = channel.channel_name;
  }
  function renderParams(params) {
    const items = Array.isArray(params) ? params : [];
    $("qo-param-list").innerHTML = (items.length ? items : [{ name: "", value: "" }]).map((item) => `
      <div class="qo-param-row">
        <input class="qo-param-name" placeholder="参数名" value="${api.escapeHtml(item.name || "")}">
        <input class="qo-param-value" placeholder="参数值" value="${api.escapeHtml(item.value || "")}">
        <button class="qo-button qo-button--secondary" data-qo-remove-param type="button">删除</button>
      </div>
    `).join("");
  }
  function readParams() {
    return Array.from(document.querySelectorAll(".qo-param-row")).map((row) => ({
      name: text(row.querySelector(".qo-param-name").value),
      value: row.querySelector(".qo-param-value").value,
    })).filter((item) => item.name);
  }
  function renderCapability() {
    const capability = state.payload.push_capability || {};
    const ok = Boolean(capability.enabled) && !capability.readonly;
    const message = ok
      ? "外推能力已启用；测试操作仅排队，不会在当前请求中外呼。"
      : `外推能力当前不可测试：${capability.reason || "能力未启用"}`;
    $("qo-push-capability").classList.toggle("is-ok", ok);
    $("qo-push-capability").textContent = message;
    $("qo-test-push").disabled = !ok;
  }
  function fill(payload) {
    state.payload = { ...state.payload, ...payload };
    payload = state.payload;
    const completion = payload.completion || {};
    const push = payload.external_push || {};
    const legacyMiniProgram = (completion.completion_target && completion.completion_target.mini_program) || {};
    state.completionMode = completion.mode === "redirect" ? "redirect" : "lead_qr";
    $("qo-completion-enabled").checked = Boolean(completion.enabled);
    $("qo-legacy-target").hidden = !completion.legacy_target_readonly;
    $("qo-legacy-appid").textContent = text(legacyMiniProgram.appid) || "未记录";
    $("qo-legacy-username").textContent = text(legacyMiniProgram.username) || "未记录";
    $("qo-legacy-path").textContent = text(legacyMiniProgram.path) || "未记录";
    $("qo-legacy-env").textContent = text(legacyMiniProgram.env_version) || "release";
    $("qo-legacy-query").textContent = text(legacyMiniProgram.query) || "未记录";
    const targetRoot = document.querySelector("[data-completion-target-config]");
    if (targetRoot && typeof targetRoot.setCompletionTarget === "function") {
      targetRoot.setCompletionTarget(completion.completion_target || targetController.defaultTarget());
    }
    $("qo-push-enabled").checked = Boolean(push.enabled);
    $("qo-push-enabled-text").textContent = push.enabled ? "已启用" : "未启用";
    $("qo-push-url").value = push.webhook_url || "";
    $("qo-push-type").value = push.type || "";
    $("qo-push-expires-at").value = push.expires_at_ts == null ? "" : push.expires_at_ts;
    $("qo-push-day").value = push.day == null ? "" : push.day;
    $("qo-push-frequency").value = push.frequency == null ? "" : push.frequency;
    $("qo-push-remark").value = push.remark || "";
    renderParams(push.custom_params || []);
    renderCapability();
    syncCompletionVisibility();
    renderChannels();
  }
  function completionBody() {
    if (!completionEnabled()) return { enabled: false };
    if (state.completionMode === "lead_qr") {
      const channelId = Number($("qo-lead-channel").value || 0);
      if (!channelId) throw new Error("请选择一个可用渠道码");
      return { enabled: true, action_type: "lead_qr", lead_channel_id: channelId };
    }
    const targetRoot = document.querySelector("[data-completion-target-config]");
    return { enabled: true, action_type: "redirect", completion_target: targetRoot.collectCompletionTarget() };
  }
  function pushBody() {
    return {
      enabled: Boolean($("qo-push-enabled").checked),
      webhook_url: text($("qo-push-url").value),
      type: $("qo-push-type").value,
      expires_at_ts: numberOrNull("qo-push-expires-at"),
      day: numberOrNull("qo-push-day"),
      frequency: numberOrNull("qo-push-frequency"),
      remark: text($("qo-push-remark").value),
      custom_params: readParams(),
    };
  }
  async function saveCompletion() {
    setToast("正在保存提交后动作…");
    const payload = await request(`/api/admin/questionnaires/${questionnaireId}/operations/completion`, {
      method: "PUT",
      body: completionBody(),
    });
    fill(payload);
    setToast("提交后动作已保存");
  }
  async function savePush() {
    setToast("正在保存外部推送…");
    const payload = await request(`/api/admin/questionnaires/${questionnaireId}/operations/external-push`, {
      method: "PUT",
      body: pushBody(),
    });
    fill(payload);
    setToast("外部推送已保存");
    return payload;
  }
  async function testPush() {
    await savePush();
    setToast("测试推送正在排队…");
    const payload = await request(`/api/admin/questionnaires/${questionnaireId}/operations/external-push/test`, { method: "POST" });
    setToast(`测试推送已排队 · ${payload.test_run_id}`);
  }
  async function loadChannels() {
    const payload = await request("/api/admin/channels?limit=300&status=active");
    state.channels = (payload.channels || []).map(normalizedChannel);
    const current = state.payload.completion && state.payload.completion.lead_channel;
    if (current && !state.channels.some((item) => item.channel_id === Number(current.channel_id))) {
      state.channels.push(normalizedChannel({
        id: current.channel_id,
        channel_name: current.channel_name,
        qr_url: current.qr_url,
        qrcode_asset_id: current.qrcode_asset_id,
        qrcode_status: current.qrcode_status,
        carrier_type: current.carrier_type,
        status: current.status,
      }));
    }
    renderChannels();
  }

  const targetRoot = document.querySelector("[data-completion-target-config]");
  targetController.mount(targetRoot, (state.payload.completion || {}).completion_target || targetController.defaultTarget(), {
    onChange() {},
  });
  fill(state.payload);
  loadChannels().catch((error) => setToast(error.message || "渠道列表加载失败", true));

  document.querySelectorAll("[data-qo-panel]").forEach((button) => button.addEventListener("click", () => switchPanel(button.dataset.qoPanel)));
  document.querySelectorAll("[data-qo-completion-mode]").forEach((button) => button.addEventListener("click", () => {
    state.completionMode = button.dataset.qoCompletionMode === "redirect" ? "redirect" : "lead_qr";
    syncCompletionVisibility();
  }));
  $("qo-completion-enabled").addEventListener("change", syncCompletionVisibility);
  $("qo-push-enabled").addEventListener("change", () => {
    $("qo-push-enabled-text").textContent = $("qo-push-enabled").checked ? "已启用" : "未启用";
    updateSummary();
  });
  $("qo-lead-channel").addEventListener("change", renderChannelPreview);
  $("qo-add-param").addEventListener("click", () => {
    $("qo-param-list").insertAdjacentHTML("beforeend", '<div class="qo-param-row"><input class="qo-param-name" placeholder="参数名"><input class="qo-param-value" placeholder="参数值"><button class="qo-button qo-button--secondary" data-qo-remove-param type="button">删除</button></div>');
  });
  $("qo-param-list").addEventListener("click", (event) => event.target.closest("[data-qo-remove-param]")?.closest(".qo-param-row")?.remove());
  $("qo-save-completion").addEventListener("click", () => saveCompletion().catch((error) => setToast(error.message || "保存失败", true)));
  $("qo-save-push").addEventListener("click", () => savePush().catch((error) => setToast(error.message || "保存失败", true)));
  $("qo-test-push").addEventListener("click", () => testPush().catch((error) => setToast(error.message || "测试失败", true)));
  $("qo-save-current").addEventListener("click", () => {
    const action = state.activePanel === "external-push" ? savePush : saveCompletion;
    action().catch((error) => setToast(error.message || "保存失败", true));
  });
  $("qo-copy-public-url").addEventListener("click", async () => {
    const url = new URL(state.payload.questionnaire.public_path, window.location.origin).toString();
    try {
      await navigator.clipboard.writeText(url);
      setToast("公开地址已复制");
    } catch (_error) {
      window.prompt("请复制公开地址", url);
    }
  });
  }

  if (document.readyState === "loading" || !window.AdminApi) {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})(window, document);
