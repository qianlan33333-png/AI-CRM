(function () {
  "use strict";

  const AdminApi = window.AdminApi || {};
  const AutomationAgentConfig = window.AutomationAgentConfig || {};
  window.AutomationAgentConfig = AutomationAgentConfig;

  const state = AutomationAgentConfig.state || {
    agents: [],
    agentDetails: {},
    agentFormMode: "create",
    selectedAgentCode: null,
    selectedAgentDetail: null,
    focusedPromptField: null,
    defaultChannel: null,
    defaultChannelSelectedTag: null,
    providerAvailable: false,
    availableTags: [],
    availableTagMap: new Map(),
    tagModal: {
      open: false,
      search: "",
      selected: "",
    },
    modelSettings: null,
  };

  const safeJsonParse = AdminApi.safeJsonParse || function safeJsonParse(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  };

  const adminEscapeHtml = AdminApi.escapeHtml;
  function escapeHtml(value) {
    const normalizedValue = String(value == null ? "" : value);
    if (typeof adminEscapeHtml === "function") {
      return adminEscapeHtml(normalizedValue);
    }
    return normalizedValue
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const adminRequestJson = AdminApi.requestJson || function adminApiRequestJsonUnavailable() {
    return Promise.reject(new Error("AdminApi.requestJson unavailable"));
  };

  function requestJson(url, options = {}) {
    return adminRequestJson(url, options).catch((error) => {
      const payload = (error && error.payload) || {};
      if (payload.error || payload.message) {
        throw error;
      }
      if (error && error.status) {
        throw new Error(`请求失败（HTTP ${error.status || 0}）`);
      }
      throw error;
    });
  }

  function root() {
    return document.getElementById("automation-agent-config-root");
  }

  function getApiUrls(rootNode) {
    const source = rootNode || root();
    if (!source) return {};
    return safeJsonParse(source.dataset.apiUrls || "{}") || {};
  }

  function getAdminActionToken(rootNode) {
    const source = rootNode || root();
    return String((source && source.dataset && source.dataset.adminActionToken) || "").trim();
  }

  function parseJsonScript(id, fallback) {
    const node = document.getElementById(id);
    const parsed = safeJsonParse((node && node.textContent) || "");
    return parsed == null ? fallback : parsed;
  }

  function elements() {
    return {
      feedback: document.getElementById("agent-config-feedback"),
      loading: document.getElementById("agent-config-loading"),
      summaryGrid: document.getElementById("agent-config-summary-grid"),
      agentTotal: document.getElementById("agent-total"),
      agentPublishedTotal: document.getElementById("agent-published-total"),
      agentTableBody: document.getElementById("agent-table-body"),
      agentEmpty: document.getElementById("agent-empty"),
      agentCreateButton: document.getElementById("agent-create-button"),
      agentFormPanel: document.getElementById("agent-form-panel"),
      agentFormTitle: document.getElementById("agent-form-title"),
      agentFormFeedback: document.getElementById("agent-form-feedback"),
      agentForm: document.getElementById("agent-form"),
      agentFormCancel: document.getElementById("agent-form-cancel"),
      agentPublishButton: document.getElementById("agent-publish-button"),
      agentMetaStatus: document.getElementById("agent-meta-status"),
      agentMetaDraftVersion: document.getElementById("agent-meta-draft-version"),
      agentMetaPublishedVersion: document.getElementById("agent-meta-published-version"),
      agentMetaChangeSummary: document.getElementById("agent-meta-change-summary"),
      agentLoadPublishedButton: document.getElementById("agent-load-published-button"),
      agentPublishedRolePrompt: document.getElementById("agent-published-role-prompt"),
      agentPublishedTaskPrompt: document.getElementById("agent-published-task-prompt"),
      agentPublishedContextSources: document.getElementById("agent-published-context-sources"),
      agentDiffSummary: document.getElementById("agent-diff-summary"),
      defaultChannelFeedback: document.getElementById("default-channel-feedback"),
      defaultChannelGenerateButton: document.getElementById("default-channel-generate-button"),
      defaultChannelQrImage: document.getElementById("default-channel-qr-image"),
      defaultChannelQrEmpty: document.getElementById("default-channel-qr-empty"),
      defaultChannelName: document.getElementById("default-channel-name"),
      defaultChannelStatus: document.getElementById("default-channel-status"),
      defaultChannelProvider: document.getElementById("default-channel-provider"),
      defaultChannelOwner: document.getElementById("default-channel-owner"),
      defaultChannelQrLink: document.getElementById("default-channel-qr-link"),
      defaultChannelFieldStatuses: document.getElementById("default-channel-field-statuses"),
      defaultChannelForm: document.getElementById("default-channel-form"),
      defaultChannelEntryTagDisplay: document.getElementById("default-channel-entry-tag-display"),
      defaultChannelEntryTagHelp: document.getElementById("default-channel-entry-tag-help"),
      defaultChannelEntryTagPickButton: document.getElementById("default-channel-entry-tag-pick-button"),
      defaultChannelEntryTagClearButton: document.getElementById("default-channel-entry-tag-clear-button"),
      defaultChannelTagManualInput: document.getElementById("default-channel-tag-manual-input"),
      modelSettingsFeedback: document.getElementById("model-settings-feedback"),
      modelSettingsEnabledLabel: document.getElementById("model-settings-enabled-label"),
      modelSettingsApiKeyMask: document.getElementById("model-settings-api-key-mask"),
      modelSettingsUpdatedAt: document.getElementById("model-settings-updated-at"),
      modelSettingsTestResult: document.getElementById("model-settings-test-result"),
      modelSettingsForm: document.getElementById("model-settings-form"),
      modelSettingsTestButton: document.getElementById("model-settings-test-button"),
    };
  }

  function agentFields() {
    const { agentForm } = elements();
    return {
      formMode: agentForm ? agentForm.querySelector('[name="form_mode"]') : null,
      expectedDraftVersion: agentForm ? agentForm.querySelector('[name="expected_draft_version"]') : null,
      agentCode: agentForm ? agentForm.querySelector('[name="agent_code"]') : null,
      displayName: agentForm ? agentForm.querySelector('[name="display_name"]') : null,
      enabled: agentForm ? agentForm.querySelector('[name="enabled"]') : null,
      changeSummary: agentForm ? agentForm.querySelector('[name="change_summary"]') : null,
      rolePrompt: agentForm ? agentForm.querySelector('[name="role_prompt"]') : null,
      taskPrompt: agentForm ? agentForm.querySelector('[name="task_prompt"]') : null,
    };
  }

  function defaultChannelFields() {
    const { defaultChannelForm } = elements();
    return {
      channelName: defaultChannelForm ? defaultChannelForm.querySelector('[name="channel_name"]') : null,
      autoAcceptFriend: defaultChannelForm ? defaultChannelForm.querySelector('[name="auto_accept_friend"]') : null,
      entryTagId: defaultChannelForm ? defaultChannelForm.querySelector('[name="entry_tag_id"]') : null,
      entryTagIdManual: defaultChannelForm ? defaultChannelForm.querySelector('[name="entry_tag_id_manual"]') : null,
      welcomeMessage: defaultChannelForm ? defaultChannelForm.querySelector('[name="welcome_message"]') : null,
    };
  }

  function modelSettingsFields() {
    const { modelSettingsForm } = elements();
    return {
      enabled: modelSettingsForm ? modelSettingsForm.querySelector('[name="enabled"]') : null,
      apiKey: modelSettingsForm ? modelSettingsForm.querySelector('[name="api_key"]') : null,
      baseUrl: modelSettingsForm ? modelSettingsForm.querySelector('[name="base_url"]') : null,
      timeoutSeconds: modelSettingsForm ? modelSettingsForm.querySelector('[name="timeout_seconds"]') : null,
      routerModel: modelSettingsForm ? modelSettingsForm.querySelector('[name="router_model"]') : null,
      executionModel: modelSettingsForm ? modelSettingsForm.querySelector('[name="execution_model"]') : null,
      reasonerModel: modelSettingsForm ? modelSettingsForm.querySelector('[name="reasoner_model"]') : null,
    };
  }

  function showFeedback(message, tone) {
    const { feedback } = elements();
    if (!feedback) return;
    if (!message) {
      feedback.hidden = true;
      feedback.textContent = "";
      feedback.className = "ac-config-feedback";
      return;
    }
    feedback.hidden = false;
    feedback.textContent = message;
    feedback.className = "ac-config-feedback" + (tone === "error" ? " is-error" : tone === "success" ? " is-success" : "");
  }

  function showAgentFormFeedback(message, tone) {
    const { agentFormFeedback } = elements();
    if (!agentFormFeedback) return;
    if (!message) {
      agentFormFeedback.hidden = true;
      agentFormFeedback.textContent = "";
      agentFormFeedback.className = "ac-config-feedback";
      return;
    }
    agentFormFeedback.hidden = false;
    agentFormFeedback.textContent = message;
    agentFormFeedback.className = "ac-config-feedback" + (tone === "error" ? " is-error" : tone === "success" ? " is-success" : "");
  }

  function withId(base, id) {
    return String(base || "").replace(/\/0(?=\/|$|\?)/, "/" + encodeURIComponent(String(id)));
  }

  function withCode(template, agentCode) {
    return String(template || "").replace("__AGENT_CODE__", encodeURIComponent(String(agentCode || "").trim()));
  }

  function statusLabel(status) {
    const normalized = String(status || "").trim();
    if (normalized === "published") return "已发布";
    if (normalized === "disabled") return "停用";
    if (normalized === "draft") return "草稿";
    return normalized || "-";
  }

  function statusBadgeClass(status) {
    const normalized = String(status || "").trim();
    if (normalized === "published") return "ac-config-status-badge is-published";
    if (normalized === "disabled") return "ac-config-status-badge is-disabled";
    return "ac-config-status-badge is-draft";
  }

  function normalizeAgentItem(item) {
    const status = String((item && (item.status || item.status_code)) || "").trim() || "draft";
    return {
      agent_code: String((item && item.agent_code) || "").trim(),
      agent_name: String((item && (item.agent_name || item.display_name || item.agent_code)) || "").trim(),
      status,
    };
  }

  function contextSourceSpecs() {
    return [
      { code: "questionnaire", label: "问卷信息", placeholder: "{{问卷信息}}" },
      { code: "recent_messages", label: "最近20条聊天信息", placeholder: "{{最近20条聊天信息}}" },
      { code: "user_tags", label: "用户标签", placeholder: "{{用户标签}}" },
      { code: "activation_info", label: "阶段信息", placeholder: "{{阶段信息}}" },
    ];
  }

  function detectContextSourcesFromPrompt(rolePrompt, taskPrompt) {
    const promptText = [rolePrompt, taskPrompt].map((item) => String(item || "")).join("\n");
    return contextSourceSpecs()
      .filter((item) => item.placeholder && promptText.includes(item.placeholder))
      .map((item) => item.code);
  }

  function formatContextSourcesFromPrompt(rolePrompt, taskPrompt) {
    const usedCodes = new Set(detectContextSourcesFromPrompt(rolePrompt, taskPrompt));
    const items = contextSourceSpecs()
      .filter((item) => usedCodes.has(item.code))
      .map((item) => `${item.label} <- ${item.placeholder}`);
    return items.length ? items.join("\n") : "当前未引用任何上下文占位符";
  }

  function updateSummaryCounters() {
    const nodes = elements();
    if (nodes.loading) nodes.loading.hidden = true;
    if (nodes.summaryGrid) nodes.summaryGrid.hidden = false;
    if (nodes.agentTotal) nodes.agentTotal.textContent = String(state.agents.length);
    if (nodes.agentPublishedTotal) {
      nodes.agentPublishedTotal.textContent = String(state.agents.filter((item) => item.status === "published").length);
    }
  }

  function syncInitialState() {
    state.agents = (parseJsonScript("automation-agent-config-initial-agents", []) || []).map(normalizeAgentItem);
    updateSummaryCounters();
  }

  AutomationAgentConfig.root = root;
  AutomationAgentConfig.safeJsonParse = safeJsonParse;
  AutomationAgentConfig.escapeHtml = escapeHtml;
  AutomationAgentConfig.requestJson = requestJson;
  AutomationAgentConfig.getApiUrls = getApiUrls;
  AutomationAgentConfig.getAdminActionToken = getAdminActionToken;
  AutomationAgentConfig.parseJsonScript = parseJsonScript;
  AutomationAgentConfig.state = state;
  AutomationAgentConfig.elements = elements;
  AutomationAgentConfig.agentFields = agentFields;
  AutomationAgentConfig.defaultChannelFields = defaultChannelFields;
  AutomationAgentConfig.modelSettingsFields = modelSettingsFields;
  AutomationAgentConfig.showFeedback = showFeedback;
  AutomationAgentConfig.showAgentFormFeedback = showAgentFormFeedback;
  AutomationAgentConfig.withId = withId;
  AutomationAgentConfig.withCode = withCode;
  AutomationAgentConfig.statusLabel = statusLabel;
  AutomationAgentConfig.statusBadgeClass = statusBadgeClass;
  AutomationAgentConfig.normalizeAgentItem = normalizeAgentItem;
  AutomationAgentConfig.contextSourceSpecs = contextSourceSpecs;
  AutomationAgentConfig.detectContextSourcesFromPrompt = detectContextSourcesFromPrompt;
  AutomationAgentConfig.formatContextSourcesFromPrompt = formatContextSourcesFromPrompt;
  AutomationAgentConfig.updateSummaryCounters = updateSummaryCounters;
  AutomationAgentConfig.syncInitialState = syncInitialState;
})();
