(function () {
  "use strict";

  const AutomationAgentConfig = window.AutomationAgentConfig || {};
  window.AutomationAgentConfig = AutomationAgentConfig;

  const state = AutomationAgentConfig.state || {};

  function showDefaultChannelFeedback(message, tone) {
    const { defaultChannelFeedback } = AutomationAgentConfig.elements();
    if (!defaultChannelFeedback) return;
    if (!message) {
      defaultChannelFeedback.hidden = true;
      defaultChannelFeedback.textContent = "";
      defaultChannelFeedback.className = "ac-config-feedback";
      return;
    }
    defaultChannelFeedback.hidden = false;
    defaultChannelFeedback.textContent = message;
    defaultChannelFeedback.className = "ac-config-feedback" + (tone === "error" ? " is-error" : tone === "success" ? " is-success" : "");
  }

  function showModelSettingsFeedback(message, tone) {
    const { modelSettingsFeedback } = AutomationAgentConfig.elements();
    if (!modelSettingsFeedback) return;
    if (!message) {
      modelSettingsFeedback.hidden = true;
      modelSettingsFeedback.textContent = "";
      modelSettingsFeedback.className = "ac-config-feedback";
      return;
    }
    modelSettingsFeedback.hidden = false;
    modelSettingsFeedback.textContent = message;
    modelSettingsFeedback.className = "ac-config-feedback" + (tone === "error" ? " is-error" : tone === "success" ? " is-success" : "");
  }

  function channelFieldStatusLabel(status) {
    const normalized = String(status || "").trim();
    if (normalized === "applied") return "已生效";
    if (normalized === "pending") return "待重新生成二维码后生效";
    if (normalized === "unsupported") return "当前 provider 不支持";
    if (normalized === "not_set") return "未配置";
    return normalized || "-";
  }

  function applyTagSelectionToDefaultChannel(tag) {
    if (typeof AutomationAgentConfig.setDefaultChannelSelectedTag === "function") {
      AutomationAgentConfig.setDefaultChannelSelectedTag(tag);
    }
  }

  function renderDefaultChannelQr(payload) {
    const elements = AutomationAgentConfig.elements();
    const escapeHtml = AutomationAgentConfig.escapeHtml;
    const statusBadgeClass = AutomationAgentConfig.statusBadgeClass;
    const channel = payload || state.defaultChannel || {};
    const qrUrl = String(channel.qr_url || "").trim();

    if (elements.defaultChannelName) elements.defaultChannelName.textContent = channel.channel_name || "-";
    if (elements.defaultChannelStatus) elements.defaultChannelStatus.textContent = channel.status || "-";
    if (elements.defaultChannelProvider) elements.defaultChannelProvider.textContent = state.providerAvailable ? "已接入" : "未接入";
    if (elements.defaultChannelOwner) elements.defaultChannelOwner.textContent = channel.owner_staff_id || "-";

    if (qrUrl) {
      if (elements.defaultChannelQrImage) {
        elements.defaultChannelQrImage.hidden = false;
        elements.defaultChannelQrImage.src = qrUrl;
      }
      if (elements.defaultChannelQrEmpty) elements.defaultChannelQrEmpty.hidden = true;
      if (elements.defaultChannelQrLink) {
        elements.defaultChannelQrLink.innerHTML = `<a href="${escapeHtml(qrUrl)}" target="_blank" rel="noreferrer">打开当前二维码链接</a>`;
      }
    } else {
      if (elements.defaultChannelQrImage) {
        elements.defaultChannelQrImage.hidden = true;
        elements.defaultChannelQrImage.removeAttribute("src");
      }
      if (elements.defaultChannelQrEmpty) elements.defaultChannelQrEmpty.hidden = false;
      if (elements.defaultChannelQrLink) elements.defaultChannelQrLink.textContent = "";
    }

    const fieldStatuses = channel.field_statuses || {};
    if (elements.defaultChannelFieldStatuses) {
      elements.defaultChannelFieldStatuses.innerHTML = [
        { key: "welcome_message", label: "欢迎语" },
        { key: "auto_accept_friend", label: "自动通过" },
        { key: "entry_tag", label: "扫码自动打标签" },
      ].map((item) => {
        const statusItem = fieldStatuses[item.key] || {};
        return `
          <div class="ac-config-field-status-item">
            <strong>${escapeHtml(item.label)}</strong>
            <span class="${statusBadgeClass(statusItem.status === "applied" ? "published" : statusItem.status === "unsupported" ? "disabled" : "draft")}">${escapeHtml(channelFieldStatusLabel(statusItem.status))}</span>
            <p>${escapeHtml(statusItem.detail || "暂无说明")}</p>
          </div>
        `;
      }).join("");
    }
  }

  function populateDefaultChannelForm(payload) {
    const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
    const normalizeTagId = AutomationAgentConfig.normalizeTagId || function normalizeTagId(value) {
      return String(value || "").trim();
    };
    const channel = payload || state.defaultChannel || {};

    if (defaultChannelFields.channelName) defaultChannelFields.channelName.value = channel.channel_name || "";
    if (defaultChannelFields.autoAcceptFriend) defaultChannelFields.autoAcceptFriend.value = channel.auto_accept_friend ? "1" : "0";
    if (defaultChannelFields.entryTagIdManual) defaultChannelFields.entryTagIdManual.value = "";
    if (defaultChannelFields.welcomeMessage) defaultChannelFields.welcomeMessage.value = channel.welcome_message || "";
    applyTagSelectionToDefaultChannel(
      normalizeTagId(channel.entry_tag_id)
        ? {
            tag_id: channel.entry_tag_id,
            tag_name: channel.entry_tag_name,
            group_name: channel.entry_tag_group_name,
          }
        : null
    );
  }

  function renderDefaultChannel(payload) {
    const channel = payload || state.defaultChannel || {};
    populateDefaultChannelForm(channel);
    renderDefaultChannelQr(channel);
  }

  function collectDefaultChannelPayload() {
    const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
    const normalizeTagId = AutomationAgentConfig.normalizeTagId || function normalizeTagId(value) {
      return String(value || "").trim();
    };
    const root = AutomationAgentConfig.root();
    const manualTagId = normalizeTagId((defaultChannelFields.entryTagIdManual || {}).value || "");
    const selectedTagId = manualTagId || normalizeTagId((defaultChannelFields.entryTagId || {}).value || "");
    return {
      admin_action_token: AutomationAgentConfig.getAdminActionToken(root),
      channel_name: String((defaultChannelFields.channelName || {}).value || "").trim(),
      auto_accept_friend: String((defaultChannelFields.autoAcceptFriend || {}).value || "0") === "1",
      entry_tag_id: selectedTagId,
      welcome_message: String((defaultChannelFields.welcomeMessage || {}).value || "").trim(),
    };
  }

  async function loadDefaultChannelSettings() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const result = await AutomationAgentConfig.requestJson(apiUrls.default_channel_settings, { credentials: "same-origin" });
    state.defaultChannel = result.default_channel || {};
    state.providerAvailable = !!result.provider_available;
    renderDefaultChannel(state.defaultChannel);
    return result;
  }

  async function saveDefaultChannelSettings() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const result = await AutomationAgentConfig.requestJson(apiUrls.default_channel_settings, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectDefaultChannelPayload()),
    });
    state.defaultChannel = result.default_channel || {};
    state.providerAvailable = !!result.provider_available;
    renderDefaultChannel(state.defaultChannel);
    showDefaultChannelFeedback("渠道配置已保存", "success");
    AutomationAgentConfig.showFeedback("渠道配置已保存", "success");
    return result;
  }

  async function generateDefaultChannelQr() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const root = AutomationAgentConfig.root();
    const result = await AutomationAgentConfig.requestJson(apiUrls.default_channel_generate_qr, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_action_token: AutomationAgentConfig.getAdminActionToken(root) }),
    });
    state.defaultChannel = result.channel || state.defaultChannel || {};
    if (result.field_statuses && state.defaultChannel) {
      state.defaultChannel.field_statuses = result.field_statuses;
    }
    renderDefaultChannel(state.defaultChannel);
    showDefaultChannelFeedback("默认二维码已重新生成", "success");
    AutomationAgentConfig.showFeedback("默认二维码已重新生成", "success");
    return result;
  }

  function renderModelFieldStatus(payload) {
    const elements = AutomationAgentConfig.elements();
    const deepseek = ((payload || state.modelSettings || {}).deepseek) || {};
    if (elements.modelSettingsEnabledLabel) elements.modelSettingsEnabledLabel.textContent = deepseek.enabled ? "开启中" : "已关闭";
    if (elements.modelSettingsApiKeyMask) {
      elements.modelSettingsApiKeyMask.textContent = deepseek.api_key_masked || (deepseek.api_key_configured ? "已配置" : "未配置");
    }
    if (elements.modelSettingsUpdatedAt) elements.modelSettingsUpdatedAt.textContent = deepseek.updated_at || "-";
  }

  function populateModelSettingsForm(payload) {
    const modelSettingsFields = AutomationAgentConfig.modelSettingsFields();
    const deepseek = ((payload || state.modelSettings || {}).deepseek) || {};
    if (modelSettingsFields.enabled) modelSettingsFields.enabled.value = deepseek.enabled ? "true" : "false";
    if (modelSettingsFields.apiKey) modelSettingsFields.apiKey.value = "";
    if (modelSettingsFields.baseUrl) modelSettingsFields.baseUrl.value = deepseek.base_url || "";
    if (modelSettingsFields.timeoutSeconds) modelSettingsFields.timeoutSeconds.value = String(deepseek.timeout_seconds || "");
    if (modelSettingsFields.routerModel) modelSettingsFields.routerModel.value = deepseek.router_model || "";
    if (modelSettingsFields.executionModel) modelSettingsFields.executionModel.value = deepseek.execution_model || "";
    if (modelSettingsFields.reasonerModel) modelSettingsFields.reasonerModel.value = deepseek.reasoner_model || "";
  }

  function renderModelSettings(payload) {
    const nextPayload = payload || state.modelSettings || {};
    renderModelFieldStatus(nextPayload);
    populateModelSettingsForm(nextPayload);
  }

  function collectModelSettingsPayload() {
    const modelSettingsFields = AutomationAgentConfig.modelSettingsFields();
    const root = AutomationAgentConfig.root();
    return {
      admin_action_token: AutomationAgentConfig.getAdminActionToken(root),
      enabled: String((modelSettingsFields.enabled || {}).value || "false") === "true",
      api_key: String((modelSettingsFields.apiKey || {}).value || "").trim(),
      base_url: String((modelSettingsFields.baseUrl || {}).value || "").trim(),
      timeout_seconds: Number((modelSettingsFields.timeoutSeconds || {}).value || 0) || "",
      router_model: String((modelSettingsFields.routerModel || {}).value || "").trim(),
      execution_model: String((modelSettingsFields.executionModel || {}).value || "").trim(),
      reasoner_model: String((modelSettingsFields.reasonerModel || {}).value || "").trim(),
    };
  }

  async function loadModelSettings() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const result = await AutomationAgentConfig.requestJson(apiUrls.model_settings, { credentials: "same-origin" });
    state.modelSettings = result || {};
    renderModelSettings(state.modelSettings);
    return result;
  }

  async function saveModelSettings() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const result = await AutomationAgentConfig.requestJson(apiUrls.model_settings, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectModelSettingsPayload()),
    });
    state.modelSettings = result || {};
    renderModelSettings(state.modelSettings);
    showModelSettingsFeedback("模型配置已保存", "success");
    AutomationAgentConfig.showFeedback("模型配置已保存", "success");
    return result;
  }

  async function testModelSettings() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const root = AutomationAgentConfig.root();
    const elements = AutomationAgentConfig.elements();
    const result = await AutomationAgentConfig.requestJson(apiUrls.model_settings_test, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_action_token: AutomationAgentConfig.getAdminActionToken(root) }),
    });
    if (elements.modelSettingsTestResult) {
      elements.modelSettingsTestResult.textContent = `成功 · ${result.model_name || "-"} · ${result.latency_ms || 0}ms`;
    }
    showModelSettingsFeedback("模型连通性测试成功", "success");
    return result;
  }

  function bindChannelModelInteractions() {
    const elements = AutomationAgentConfig.elements();

    if (elements.defaultChannelForm) {
      elements.defaultChannelForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        try {
          await saveDefaultChannelSettings();
        } catch (error) {
          showDefaultChannelFeedback(error.message || "保存渠道配置失败", "error");
        }
      });
    }

    if (elements.defaultChannelGenerateButton) {
      elements.defaultChannelGenerateButton.addEventListener("click", async function () {
        try {
          await generateDefaultChannelQr();
        } catch (error) {
          showDefaultChannelFeedback(error.message || "生成二维码失败", "error");
        }
      });
    }

    if (elements.modelSettingsForm) {
      elements.modelSettingsForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        try {
          await saveModelSettings();
        } catch (error) {
          showModelSettingsFeedback(error.message || "保存模型配置失败", "error");
        }
      });
    }

    if (elements.modelSettingsTestButton) {
      elements.modelSettingsTestButton.addEventListener("click", async function () {
        try {
          await testModelSettings();
        } catch (error) {
          if (elements.modelSettingsTestResult) elements.modelSettingsTestResult.textContent = "失败";
          showModelSettingsFeedback(error.message || "模型连通性测试失败", "error");
        }
      });
    }
  }

  async function loadChannelModelState() {
    const elements = AutomationAgentConfig.elements();
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const tasks = [];
    if ((elements.defaultChannelForm || elements.defaultChannelGenerateButton) && apiUrls.default_channel_settings) {
      tasks.push(loadDefaultChannelSettings());
      if (AutomationAgentConfig.loadWeComTags && apiUrls.wecom_tags) {
        tasks.push(AutomationAgentConfig.loadWeComTags());
      }
    }
    if (elements.modelSettingsForm && apiUrls.model_settings) {
      tasks.push(loadModelSettings());
    }
    await Promise.all(tasks);
  }

  AutomationAgentConfig.showDefaultChannelFeedback = showDefaultChannelFeedback;
  AutomationAgentConfig.showModelSettingsFeedback = showModelSettingsFeedback;
  AutomationAgentConfig.channelFieldStatusLabel = channelFieldStatusLabel;
  AutomationAgentConfig.applyTagSelectionToDefaultChannel = applyTagSelectionToDefaultChannel;
  AutomationAgentConfig.renderDefaultChannelQr = renderDefaultChannelQr;
  AutomationAgentConfig.populateDefaultChannelForm = populateDefaultChannelForm;
  AutomationAgentConfig.collectDefaultChannelPayload = collectDefaultChannelPayload;
  AutomationAgentConfig.loadDefaultChannelSettings = loadDefaultChannelSettings;
  AutomationAgentConfig.saveDefaultChannelSettings = saveDefaultChannelSettings;
  AutomationAgentConfig.generateDefaultChannelQr = generateDefaultChannelQr;
  AutomationAgentConfig.renderModelFieldStatus = renderModelFieldStatus;
  AutomationAgentConfig.populateModelSettingsForm = populateModelSettingsForm;
  AutomationAgentConfig.collectModelSettingsPayload = collectModelSettingsPayload;
  AutomationAgentConfig.loadModelSettings = loadModelSettings;
  AutomationAgentConfig.saveModelSettings = saveModelSettings;
  AutomationAgentConfig.testModelSettings = testModelSettings;
  AutomationAgentConfig.bindChannelModelInteractions = bindChannelModelInteractions;
  AutomationAgentConfig.loadChannelModelState = loadChannelModelState;
})();
