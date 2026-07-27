(function () {
  "use strict";

  const AutomationAgentConfig = window.AutomationAgentConfig || {};
  window.AutomationAgentConfig = AutomationAgentConfig;

  const state = AutomationAgentConfig.state || {};

  function renderAgentRow(item) {
    const escapeHtml = AutomationAgentConfig.escapeHtml;
    const statusBadgeClass = AutomationAgentConfig.statusBadgeClass;
    const statusLabel = AutomationAgentConfig.statusLabel;
    const isActive = item.agent_code === state.selectedAgentCode;
    return `
      <tr data-agent-code="${escapeHtml(item.agent_code)}"${isActive ? ' class="is-active"' : ""}>
        <td>
          <strong>${escapeHtml(item.agent_name || item.agent_code || "-")}</strong>
          <div class="ac-config-code">${escapeHtml(item.agent_code || "")}</div>
        </td>
        <td>
          <span class="${statusBadgeClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
        </td>
        <td>
          <div class="ac-config-row-actions">
            <button class="admin-button admin-button--secondary" type="button" data-agent-edit="${escapeHtml(item.agent_code)}">编辑</button>
            <button class="admin-button admin-button--ghost" type="button" data-agent-delete="${escapeHtml(item.agent_code)}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }

  function renderAgentTable() {
    const { agentTableBody, agentEmpty } = AutomationAgentConfig.elements();
    if (agentTableBody) {
      agentTableBody.innerHTML = state.agents.map(renderAgentRow).join("");
    }
    if (agentEmpty) {
      agentEmpty.hidden = state.agents.length > 0;
    }
  }

  function renderAgentDiffSummary(detail) {
    const { agentDiffSummary } = AutomationAgentConfig.elements();
    if (!agentDiffSummary) return;
    const escapeHtml = AutomationAgentConfig.escapeHtml;
    const diffSummary = Array.isArray((detail || {}).diff_summary) ? detail.diff_summary : [];
    agentDiffSummary.innerHTML = diffSummary.length
      ? diffSummary.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
      : "<li>当前草稿与已发布版本一致。</li>";
  }

  function renderPublishedPreview(detail) {
    const elements = AutomationAgentConfig.elements();
    const published = ((detail || {}).published) || {};
    if (elements.agentPublishedRolePrompt) {
      elements.agentPublishedRolePrompt.textContent = published.role_prompt || "-";
    }
    if (elements.agentPublishedTaskPrompt) {
      elements.agentPublishedTaskPrompt.textContent = published.task_prompt || "-";
    }
    if (elements.agentPublishedContextSources) {
      elements.agentPublishedContextSources.textContent = AutomationAgentConfig.formatContextSourcesFromPrompt(
        published.role_prompt || "",
        published.task_prompt || "",
      );
    }
    renderAgentDiffSummary(detail);
  }

  function resetAgentForm() {
    const elements = AutomationAgentConfig.elements();
    const agentFields = AutomationAgentConfig.agentFields();
    if (elements.agentForm) elements.agentForm.reset();
    if (agentFields.formMode) agentFields.formMode.value = "create";
    if (agentFields.expectedDraftVersion) agentFields.expectedDraftVersion.value = "1";
    if (agentFields.agentCode) {
      agentFields.agentCode.value = "";
      agentFields.agentCode.readOnly = false;
    }
    if (agentFields.displayName) agentFields.displayName.value = "";
    if (agentFields.enabled) agentFields.enabled.checked = true;
    if (agentFields.changeSummary) agentFields.changeSummary.value = "创建新智能体草稿";
    if (agentFields.rolePrompt) agentFields.rolePrompt.value = "";
    if (agentFields.taskPrompt) agentFields.taskPrompt.value = "";
    if (elements.agentMetaStatus) elements.agentMetaStatus.textContent = "新建态";
    if (elements.agentMetaDraftVersion) elements.agentMetaDraftVersion.textContent = "1";
    if (elements.agentMetaPublishedVersion) elements.agentMetaPublishedVersion.textContent = "0";
    if (elements.agentMetaChangeSummary) elements.agentMetaChangeSummary.textContent = "保存后生成首版草稿";
    if (elements.agentPublishButton) elements.agentPublishButton.hidden = true;
    state.selectedAgentDetail = null;
    renderPublishedPreview(null);
    AutomationAgentConfig.showAgentFormFeedback("", "");
  }

  function populateAgentForm(detail) {
    const elements = AutomationAgentConfig.elements();
    const agentFields = AutomationAgentConfig.agentFields();
    const statusLabel = AutomationAgentConfig.statusLabel;
    const item = detail || {};
    const draft = item.draft || {};
    const publishedVersion = Number(item.published_version || 0);
    const hasUnpublishedChanges = !!item.has_unpublished_changes;
    const derivedStatus = !item.enabled ? "disabled" : publishedVersion > 0 && !hasUnpublishedChanges ? "published" : "draft";

    state.agentFormMode = "edit";
    state.selectedAgentCode = item.agent_code || state.selectedAgentCode;
    state.selectedAgentDetail = item;
    renderAgentTable();

    if (agentFields.formMode) agentFields.formMode.value = "edit";
    if (agentFields.agentCode) agentFields.agentCode.value = item.agent_code || "";
    if (agentFields.agentCode) agentFields.agentCode.readOnly = true;
    if (agentFields.displayName) agentFields.displayName.value = item.display_name || "";
    if (agentFields.enabled) agentFields.enabled.checked = !!item.enabled;
    if (agentFields.changeSummary) agentFields.changeSummary.value = item.last_change_summary || "更新智能体草稿";
    if (agentFields.rolePrompt) agentFields.rolePrompt.value = draft.role_prompt || "";
    if (agentFields.taskPrompt) agentFields.taskPrompt.value = draft.task_prompt || "";
    if (agentFields.expectedDraftVersion) agentFields.expectedDraftVersion.value = String(item.draft_version || 1);
    if (elements.agentFormTitle) elements.agentFormTitle.textContent = `编辑智能体 · ${item.display_name || item.agent_code || ""}`;
    if (elements.agentPublishButton) elements.agentPublishButton.hidden = false;

    if (elements.agentMetaStatus) {
      elements.agentMetaStatus.textContent = item.enabled
        ? (hasUnpublishedChanges && publishedVersion > 0 ? "已发布，当前有未发布草稿" : statusLabel(derivedStatus))
        : "停用";
    }
    if (elements.agentMetaDraftVersion) elements.agentMetaDraftVersion.textContent = String(item.draft_version || 1);
    if (elements.agentMetaPublishedVersion) elements.agentMetaPublishedVersion.textContent = String(item.published_version || 0);
    if (elements.agentMetaChangeSummary) elements.agentMetaChangeSummary.textContent = item.last_change_summary || "暂无变更摘要";
    renderPublishedPreview(item);
  }

  async function loadAgents() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const result = await AutomationAgentConfig.requestJson(apiUrls.agents_options, { credentials: "same-origin" });
    state.agents = (result.items || []).map(AutomationAgentConfig.normalizeAgentItem);
    if (state.selectedAgentCode && !state.agents.some((item) => item.agent_code === state.selectedAgentCode)) {
      state.selectedAgentCode = null;
    }
    AutomationAgentConfig.updateSummaryCounters();
    renderAgentTable();
  }

  async function loadAgentDetail(agentCode) {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const normalizedCode = String(agentCode || "").trim();
    if (!normalizedCode) {
      throw new Error("请选择智能体");
    }
    const result = await AutomationAgentConfig.requestJson(AutomationAgentConfig.withCode(apiUrls.agent_detail_base, normalizedCode), {
      credentials: "same-origin",
    });
    state.agentDetails[normalizedCode] = result.item || {};
    populateAgentForm(state.agentDetails[normalizedCode]);
    return state.agentDetails[normalizedCode];
  }

  async function openAgentForm(mode, agentCode) {
    const elements = AutomationAgentConfig.elements();
    const agentFields = AutomationAgentConfig.agentFields();
    const normalizedMode = mode === "create" ? "create" : "edit";
    if (!elements.agentFormPanel) return;
    elements.agentFormPanel.hidden = false;
    if (normalizedMode === "create") {
      state.agentFormMode = "create";
      state.selectedAgentCode = null;
      renderAgentTable();
      resetAgentForm();
      if (elements.agentFormTitle) elements.agentFormTitle.textContent = "新增智能体";
      elements.agentFormPanel.scrollIntoView({ behavior: "smooth", block: "start" });
      if (agentFields.agentCode) requestAnimationFrame(() => agentFields.agentCode.focus());
      return;
    }
    const normalizedCode = String(agentCode || "").trim();
    if (!normalizedCode) {
      throw new Error("请选择智能体");
    }
    AutomationAgentConfig.showAgentFormFeedback("", "");
    await loadAgentDetail(normalizedCode);
    elements.agentFormPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    if (agentFields.displayName) requestAnimationFrame(() => agentFields.displayName.focus());
  }

  function closeAgentForm() {
    const { agentFormPanel } = AutomationAgentConfig.elements();
    if (agentFormPanel) agentFormPanel.hidden = true;
    AutomationAgentConfig.showAgentFormFeedback("", "");
  }

  function collectAgentPayload() {
    const agentFields = AutomationAgentConfig.agentFields();
    const agentCode = String((agentFields.agentCode || {}).value || "").trim();
    const displayName = String((agentFields.displayName || {}).value || "").trim();
    const rolePrompt = String((agentFields.rolePrompt || {}).value || "").trim();
    const taskPrompt = String((agentFields.taskPrompt || {}).value || "").trim();
    const changeSummary = String((agentFields.changeSummary || {}).value || "").trim();
    if (!agentCode) throw new Error("智能体内部编码不能为空");
    if (!/^[a-z][a-z0-9_]*$/.test(agentCode)) throw new Error("智能体内部编码只能使用小写字母、数字和下划线，且必须以字母开头");
    if (!displayName) throw new Error("显示名称不能为空");
    if (!rolePrompt) throw new Error("角色提示词不能为空");
    if (!taskPrompt) throw new Error("任务提示词不能为空");
    return {
      agent_code: agentCode,
      admin_action_token: AutomationAgentConfig.getAdminActionToken(),
      display_name: displayName,
      enabled: !!((agentFields.enabled || {}).checked),
      change_summary: changeSummary || (state.agentFormMode === "create" ? "创建新智能体草稿" : "更新智能体草稿"),
      role_prompt: rolePrompt,
      task_prompt: taskPrompt,
      expected_draft_version: Number((agentFields.expectedDraftVersion || {}).value || 1) || 1,
    };
  }

  async function saveAgentDraft() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const payload = collectAgentPayload();
    const isCreateMode = state.agentFormMode === "create";
    const agentCode = String(payload.agent_code || "").trim();
    const result = await AutomationAgentConfig.requestJson(
      isCreateMode ? apiUrls.agent_create : AutomationAgentConfig.withCode(apiUrls.agent_draft_base, agentCode),
      {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    AutomationAgentConfig.showAgentFormFeedback(isCreateMode ? "智能体已创建" : "智能体草稿已保存", "success");
    await loadAgents();
    await openAgentForm("edit", ((result || {}).agent || {}).agent_code || agentCode);
    AutomationAgentConfig.showFeedback(isCreateMode ? "智能体已创建" : "智能体草稿已保存", "success");
  }

  async function publishAgent() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const agentFields = AutomationAgentConfig.agentFields();
    const agentCode = String((agentFields.agentCode || {}).value || "").trim();
    if (!agentCode) throw new Error("请选择智能体");
    const result = await AutomationAgentConfig.requestJson(AutomationAgentConfig.withCode(apiUrls.agent_publish_base, agentCode), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_action_token: AutomationAgentConfig.getAdminActionToken() }),
    });
    AutomationAgentConfig.showAgentFormFeedback("智能体已发布", "success");
    await loadAgents();
    await loadAgentDetail(((result || {}).agent || {}).agent_code || agentCode);
    AutomationAgentConfig.showFeedback("智能体已发布", "success");
  }

  async function deleteAgent(agentCode) {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const normalizedCode = String(agentCode || "").trim();
    if (!normalizedCode) {
      throw new Error("请选择智能体");
    }
    const selected = state.agents.find((item) => item.agent_code === normalizedCode) || {};
    const agentLabel = selected.agent_name || normalizedCode;
    if (!window.confirm(`确认删除智能体「${agentLabel}」吗？删除后会从可用智能体列表中移除。`)) {
      return;
    }
    const result = await AutomationAgentConfig.requestJson(AutomationAgentConfig.withCode(apiUrls.agent_delete_base, normalizedCode), {
      method: "DELETE",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_action_token: AutomationAgentConfig.getAdminActionToken() }),
    });
    if (state.selectedAgentCode === normalizedCode) {
      state.selectedAgentCode = null;
      state.selectedAgentDetail = null;
      closeAgentForm();
      resetAgentForm();
    }
    await loadAgents();
    AutomationAgentConfig.showFeedback((result || {}).message || "智能体已删除", "success");
  }

  function loadPublishedIntoDraft() {
    const agentFields = AutomationAgentConfig.agentFields();
    const detail = state.selectedAgentDetail || {};
    const published = detail.published || {};
    if (!detail.agent_code) {
      AutomationAgentConfig.showAgentFormFeedback("请先选择一个已有智能体，再使用已发布版本覆盖草稿。", "error");
      return;
    }
    if (agentFields.rolePrompt) agentFields.rolePrompt.value = published.role_prompt || "";
    if (agentFields.taskPrompt) agentFields.taskPrompt.value = published.task_prompt || "";
    if (agentFields.changeSummary && !String(agentFields.changeSummary.value || "").trim()) {
      agentFields.changeSummary.value = "用已发布版本覆盖草稿";
    }
    AutomationAgentConfig.showAgentFormFeedback("已将已发布版本回填到草稿编辑区，保存后会生成新的草稿版本。", "success");
  }

  function bindAgentInteractions() {
    const elements = AutomationAgentConfig.elements();
    if (elements.agentCreateButton) {
      elements.agentCreateButton.addEventListener("click", function () {
        openAgentForm("create").catch((error) => {
          AutomationAgentConfig.showFeedback(error.message || "打开智能体编辑器失败", "error");
        });
      });
    }

    if (elements.agentTableBody) {
      elements.agentTableBody.addEventListener("click", function (event) {
        const editButton = event.target.closest("button[data-agent-edit]");
        if (editButton) {
          openAgentForm("edit", editButton.dataset.agentEdit || "").catch((error) => {
            AutomationAgentConfig.showFeedback(error.message || "打开智能体编辑器失败", "error");
          });
          return;
        }
        const deleteButton = event.target.closest("button[data-agent-delete]");
        if (!deleteButton) return;
        deleteAgent(deleteButton.dataset.agentDelete || "").catch((error) => {
          AutomationAgentConfig.showFeedback(error.message || "删除智能体失败", "error");
        });
      });
    }

    if (elements.agentFormCancel) {
      elements.agentFormCancel.addEventListener("click", function () {
        closeAgentForm();
      });
    }

    if (elements.agentForm) {
      elements.agentForm.addEventListener("submit", function (event) {
        event.preventDefault();
        saveAgentDraft().catch((error) => {
          AutomationAgentConfig.showAgentFormFeedback(error.message || "保存智能体草稿失败", "error");
        });
      });
    }

    if (elements.agentPublishButton) {
      elements.agentPublishButton.addEventListener("click", function () {
        publishAgent().catch((error) => {
          AutomationAgentConfig.showAgentFormFeedback(error.message || "发布智能体失败", "error");
        });
      });
    }

    if (elements.agentLoadPublishedButton) {
      elements.agentLoadPublishedButton.addEventListener("click", function () {
        loadPublishedIntoDraft();
      });
    }
  }

  AutomationAgentConfig.renderAgentTable = renderAgentTable;
  AutomationAgentConfig.renderAgentRow = renderAgentRow;
  AutomationAgentConfig.openAgentForm = openAgentForm;
  AutomationAgentConfig.closeAgentForm = closeAgentForm;
  AutomationAgentConfig.loadAgentDetail = loadAgentDetail;
  AutomationAgentConfig.populateAgentForm = populateAgentForm;
  AutomationAgentConfig.collectAgentPayload = collectAgentPayload;
  AutomationAgentConfig.saveAgentDraft = saveAgentDraft;
  AutomationAgentConfig.publishAgent = publishAgent;
  AutomationAgentConfig.deleteAgent = deleteAgent;
  AutomationAgentConfig.loadPublishedIntoDraft = loadPublishedIntoDraft;
  AutomationAgentConfig.renderPublishedPreview = renderPublishedPreview;
  AutomationAgentConfig.renderAgentDiffSummary = renderAgentDiffSummary;
  AutomationAgentConfig.bindAgentInteractions = bindAgentInteractions;
  AutomationAgentConfig.loadAgents = loadAgents;
})();
