(function (window, document) {
  "use strict";

  const api = window.AdminApi || {};
  const escapeHtml = api.escapeHtml || ((value) => String(value || ""));
  const requestJson = api.requestJson || ((url, options) => fetch(url, options).then((response) => response.json()));
  const app = document.getElementById("group-ops-app");
  if (!app) return;

  const state = {
    mode: app.dataset.pageMode || "list",
    planId: Number(app.dataset.planId || 0),
    plans: [],
    groups: [],
    plan: null,
    planGroups: [],
    groupSummary: null,
    nodes: [],
    webhook: null,
    ownerOptions: [],
    createOwner: null,
    groupFilterOwner: null,
    refreshingOwnerGroups: false,
    notice: "",
    showCreate: false,
    createNotice: "",
    showGroupPicker: false,
    groupPickerSearch: "",
    groupPickerNotice: "",
    bindingGroups: false,
    showNodeModal: false,
    editingNodeId: 0,
    activeDetailPanel: "basic",
  };

  const routes = {
    list: "/admin/automation-conversion/group-ops/ui",
    groups: "/admin/automation-conversion/group-ops/groups/ui",
    plan: (id) => `/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}`,
    apiPlans: "/api/admin/automation-conversion/group-ops/plans",
    apiPlan: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}`,
    apiPlanEnable: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/enable`,
    apiPlanDisable: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/disable`,
    apiPlanGroups: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/groups`,
    apiPlanGroup: (id, chatId) =>
      `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/groups/${encodeURIComponent(chatId)}`,
    apiPlanNodes: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/nodes`,
    apiPlanNode: (id, nodeId) =>
      `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/nodes/${encodeURIComponent(nodeId)}`,
    apiWebhook: (id) => `/api/admin/automation-conversion/group-ops/plans/${encodeURIComponent(id)}/webhook`,
    apiGroups: "/api/admin/automation-conversion/group-ops/groups",
    apiGroupsSync: "/api/admin/automation-conversion/group-ops/groups/sync",
    apiMembers: "/api/admin/common/operation-members?scope=group_ops&page_size=100",
  };

  const GROUP_OPS_SCHEDULED_TIME_OPTIONS = Object.freeze([
    "08:00",
    "08:30",
    "09:00",
    "09:30",
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "12:00",
    "12:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "16:00",
    "16:30",
    "17:00",
    "17:30",
    "18:00",
    "18:30",
    "19:00",
    "19:30",
    "20:00",
    "20:30",
    "21:00",
    "21:30",
    "22:00",
    "22:30",
    "23:00",
    "23:30",
  ]);

  function normalizeItems(payload) {
    if (!payload || !Array.isArray(payload.items)) return [];
    return payload.items;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  }

  function statusText(status) {
    const map = { active: "启用", draft: "草稿", disabled: "停用" };
    return map[status] || status || "-";
  }

  function typeText(type) {
    return type === "webhook" ? "Webhook" : "标准编排";
  }

  function attachmentLabel(attachments) {
    const items = Array.isArray(attachments) ? attachments : [];
    if (!items.length) return "-";
    return items
      .map((item) => item && (item.msgtype || item.type || item.name || "素材"))
      .filter(Boolean)
      .join("、");
  }

  function materialTypeLabels(node) {
    const labels = [];
    const contentPackage = normalizeContentPackage((node || {}).content_package_json || {});
    if (contentPackage.image_library_ids.length) labels.push("图片");
    if (contentPackage.miniprogram_library_ids.length) labels.push("小程序");
    if (contentPackage.attachment_library_ids.length) labels.push("附件");
    if (contentPackage.group_invite_library_ids.length) labels.push("群邀请");
    legacyAttachmentsForNode((node || {}).attachments).forEach((item) => {
      const msgtype = String((item && item.msgtype) || "").toLowerCase();
      if (msgtype === "image" && labels.indexOf("图片") === -1) labels.push("图片");
      if (msgtype === "miniprogram" && labels.indexOf("小程序") === -1) labels.push("小程序");
      if (msgtype && !["image", "miniprogram"].includes(msgtype) && labels.indexOf("附件") === -1) labels.push("附件");
    });
    return labels;
  }

  function materialChips(node) {
    const labels = materialTypeLabels(node);
    if (!labels.length) return "-";
    return labels.map((label) => `<span class="group-ops__chip group-ops__chip--neutral">${escapeHtml(label)}</span>`).join("");
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

  function contentPackageIsEmpty(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    return !normalized.content_text
      && !normalized.image_library_ids.length
      && !normalized.miniprogram_library_ids.length
      && !normalized.attachment_library_ids.length
      && !normalized.group_invite_library_ids.length;
  }

  function addUniqueId(target, value) {
    const id = parseInt(String(value || "").trim(), 10);
    if (id > 0 && target.indexOf(id) === -1) target.push(id);
  }

  function mergeRecognizedLegacyAttachmentIds(contentPackage, attachments) {
    const merged = normalizeContentPackage(contentPackage);
    legacyAttachmentsForNode(attachments).forEach((item) => {
      const msgtype = String((item && item.msgtype) || "").toLowerCase();
      const image = item && item.image && typeof item.image === "object" ? item.image : {};
      const mini = item && item.miniprogram && typeof item.miniprogram === "object" ? item.miniprogram : {};
      const file = item && item.file && typeof item.file === "object" ? item.file : {};
      const link = item && item.link && typeof item.link === "object" ? item.link : {};
      if (msgtype === "image") addUniqueId(merged.image_library_ids, image.library_id || item.library_id);
      if (msgtype === "miniprogram") addUniqueId(merged.miniprogram_library_ids, mini.library_id || item.library_id);
      if (msgtype === "link") addUniqueId(merged.group_invite_library_ids, link.library_id || item.library_id);
      if (msgtype && !["image", "miniprogram", "link"].includes(msgtype)) {
        addUniqueId(merged.attachment_library_ids, file.library_id || item.library_id);
      }
    });
    return merged;
  }

  function nodeToContentPackage(node) {
    const current = node || {};
    if (current.content_package_json && typeof current.content_package_json === "object") {
      const normalized = normalizeContentPackage(current.content_package_json);
      if (contentPackageIsEmpty(normalized) && current.text_content) {
        normalized.content_text = String(current.text_content || "").trim();
      }
      return mergeRecognizedLegacyAttachmentIds(normalized, current.attachments);
    }
    return mergeRecognizedLegacyAttachmentIds({ content_text: current.text_content || "" }, current.attachments);
  }

  function contentPackageToNodePayload(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    return {
      text_content: normalized.content_text,
      content_package_json: normalized,
    };
  }

  function contentPackageFromForm() {
    const raw = currentFormValue("node_content_package_json");
    if (!raw) return normalizeContentPackage({});
    try {
      return normalizeContentPackage(JSON.parse(raw));
    } catch (error) {
      return normalizeContentPackage({});
    }
  }

  function contentPackageSummary(contentPackage) {
    const normalized = normalizeContentPackage(contentPackage);
    const text = normalized.content_text || "";
    return {
      text: text ? (text.length > 60 ? `${text.slice(0, 60)}...` : text) : "未配置话术",
      imageCount: normalized.image_library_ids.length,
      miniprogramCount: normalized.miniprogram_library_ids.length,
      attachmentCount: normalized.attachment_library_ids.length,
      groupInviteCount: normalized.group_invite_library_ids.length,
    };
  }

  if (typeof window !== "undefined") {
    window.AICRMGroupOpsContentAdapter = {
      normalizeContentPackage,
      nodeToContentPackage,
      contentPackageToNodePayload,
      scheduledTimeOptions: () => GROUP_OPS_SCHEDULED_TIME_OPTIONS.slice(),
    };
  }

  function textSummary(value) {
    const text = String(value || "").trim();
    if (!text) return "-";
    return text.length > 34 ? `${text.slice(0, 34)}...` : text;
  }

  function requestErrorMessage(error, fallback) {
    const payload = (error && error.payload) || {};
    const detail = payload && typeof payload.detail === "object" ? payload.detail : {};
    const candidates = [
      payload.error_message,
      payload.message,
      detail.detail,
      detail.error_message,
      detail.error_code,
      error && error.message,
      fallback,
    ];
    return String(candidates.find((item) => String(item || "").trim()) || fallback || "请求失败");
  }

  function renderShell(content) {
    app.innerHTML = content;
    bindSharedEvents();
  }

  function renderLoading() {
    renderShell('<section class="group-ops__card"><div class="group-ops__empty">加载中</div></section>');
  }

  function renderError(message) {
    renderShell(`<section class="group-ops__card"><div class="group-ops__empty">${escapeHtml(message || "加载失败")}</div></section>`);
  }

  function pageButton(label, href, variant) {
    return `<a class="group-ops__button${variant === "primary" ? " group-ops__button--primary" : ""}" href="${escapeHtml(href)}">${escapeHtml(label)}</a>`;
  }

  function actionButton(label, action, extraClass) {
    return `<button class="group-ops__button${extraClass ? ` ${extraClass}` : ""}" type="button" data-action="${escapeHtml(action)}">${escapeHtml(label)}</button>`;
  }

  function metricCard(label, value) {
    return `<article class="group-ops__metric"><div class="group-ops__metric-label">${escapeHtml(label)}</div><div class="group-ops__metric-value">${escapeHtml(value)}</div></article>`;
  }

  function statCard(label, value) {
    return `<article class="group-ops__stat"><div class="group-ops__stat-label">${escapeHtml(label)}</div><div class="group-ops__stat-value">${escapeHtml(value)}</div></article>`;
  }

  function bindSharedEvents() {
    app.querySelectorAll("[data-action]").forEach((element) => {
      element.addEventListener("click", onAction);
    });
    app.querySelectorAll("[data-filter]").forEach((element) => {
      element.addEventListener("change", onFilterChange);
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter") onFilterChange();
      });
    });
    app.querySelectorAll("[data-group-picker-search]").forEach((element) => {
      element.addEventListener("input", (event) => {
        state.groupPickerSearch = event.currentTarget.value || "";
        renderDetail();
      });
    });
  }

  function onFilterChange() {
    if (state.mode === "groups") loadGroupsPage();
  }

  function currentFormValue(name) {
    const element = app.querySelector(`[name="${name}"]`);
    return element ? element.value : "";
  }

  function memberLabel(member) {
    if (window.OperationMemberPicker && typeof window.OperationMemberPicker.memberLabel === "function") {
      return window.OperationMemberPicker.memberLabel(member);
    }
    const userId = member.user_id || member.userid || "";
    const displayName = member.display_name || member.name || "";
    return displayName && displayName !== userId ? `${displayName} / ${userId}` : userId;
  }

  function normalizeOwners(payload, plan) {
    const owners = new Map();
    normalizeItems(payload).forEach((member) => {
      const userId = member.user_id || member.userid;
      if (userId) owners.set(userId, { user_id: userId, display_name: member.display_name || member.name || userId });
    });
    if (plan && plan.owner_userid && !owners.has(plan.owner_userid)) {
      owners.set(plan.owner_userid, { user_id: plan.owner_userid, display_name: plan.owner_name || plan.owner_userid });
    }
    return Array.from(owners.values());
  }

  function currentMemberFor(userId) {
    const normalized = String(userId || "");
    if (!normalized) return null;
    return state.ownerOptions.find((member) => member.user_id === normalized) || { user_id: normalized, display_name: normalized };
  }

  function renderMemberField(name, currentUserId, action, label) {
    const selected = currentMemberFor(currentUserId);
    return `
      <div class="group-ops__member-field" data-member-field="${escapeHtml(name)}">
        <input type="hidden" name="${escapeHtml(name)}" value="${escapeHtml((selected || {}).user_id || "")}">
        <div class="group-ops__member-current" data-member-current="${escapeHtml(name)}">${escapeHtml(selected ? memberLabel(selected) : "未选择")}</div>
        ${actionButton(label || (selected ? "更换" : "选择"), action)}
      </div>
    `;
  }

  function setMemberField(name, member) {
    const input = app.querySelector(`[name="${name}"]`);
    const current = app.querySelector(`[data-member-current="${name}"]`);
    if (input) input.value = member.user_id || "";
    if (current) current.textContent = memberLabel(member);
  }

  function openMemberPicker({ fieldName, title, value, onPicked }) {
    if (!window.OperationMemberPicker) {
      state.notice = "人员加载失败，请稍后重试";
      if (state.mode === "detail") renderDetail();
      else if (state.mode === "groups") renderGroups();
      else renderList(state.lastTotal || state.plans.length, state.queueCount || 0);
      return;
    }
    window.OperationMemberPicker.open({
      value,
      title: title || "选择运营人员",
      scope: "group_ops",
      page_size: 100,
      onSelect: (member) => {
        setMemberField(fieldName, member);
        if (typeof onPicked === "function") onPicked(member);
      },
    });
  }

  function onAction(event) {
    const action = event.currentTarget.dataset.action;
    if (action === "create-plan") return createPlan();
    if (action === "show-create-plan") return showCreatePlan();
    if (action === "cancel-create-plan") return cancelCreatePlan();
    if (action === "save-plan") return savePlan();
    if (action === "save-active-detail-panel") return saveActiveDetailPanel();
    if (action === "switch-detail-panel") {
      state.activeDetailPanel = event.currentTarget.dataset.panel || "basic";
      return renderDetail();
    }
    if (action === "refresh-owner-groups") return refreshOwnerGroups();
    if (action === "enable-plan") return enablePlan(event.currentTarget.dataset.planId);
    if (action === "disable-plan") return disablePlan(event.currentTarget.dataset.planId);
    if (action === "delete-plan") return deletePlan(event.currentTarget.dataset.planId);
    if (action === "bind-group") return bindGroup(event.currentTarget.dataset.chatId);
    if (action === "open-group-picker") return openGroupPicker();
    if (action === "close-group-picker") return closeGroupPicker();
    if (action === "confirm-group-picker") return confirmGroupPicker();
    if (action === "remove-group") return removeGroup(event.currentTarget.dataset.chatId);
    if (action === "open-node-modal") return openNodeModal();
    if (action === "edit-node") return openNodeModal(event.currentTarget.dataset.nodeId);
    if (action === "configure-node-content") return openNodeContentComposer();
    if (action === "save-node") return saveNode();
    if (action === "cancel-node") return closeNodeModal();
    if (action === "delete-node") return deleteNode(event.currentTarget.dataset.nodeId);
    if (action === "copy-webhook") return copyWebhook();
    if (action === "pick-create-owner") return openMemberPicker({
      fieldName: "create_owner_userid",
      title: "选择运营人员",
      value: currentFormValue("create_owner_userid"),
      onPicked: (member) => {
        state.createOwner = member;
      },
    });
    if (action === "pick-plan-owner") return openMemberPicker({
      fieldName: "owner_userid",
      title: "选择运营人员",
      value: currentFormValue("owner_userid") || (state.plan || {}).owner_userid,
      onPicked: (member) => {
        if (state.plan) {
          state.plan.owner_userid = member.user_id || "";
          state.plan.owner_name = member.display_name || member.name || member.user_id || "";
        }
        loadOwnerGroups(member.user_id || "").catch((error) => {
          state.notice = error.message || "加载群聊失败";
          renderDetail();
        });
      },
    });
    if (action === "pick-group-filter-owner") return openMemberPicker({
      fieldName: "owner_userid",
      title: "选择群主/管理员",
      value: currentFormValue("owner_userid"),
      onPicked: (member) => {
        state.groupFilterOwner = member;
        loadGroupsPage();
      },
    });
    if (action === "clear-group-filter-owner") {
      state.groupFilterOwner = null;
      setMemberField("owner_userid", { user_id: "", display_name: "" });
      return loadGroupsPage();
    }
    return undefined;
  }

  function showCreatePlan() {
    state.showCreate = true;
    state.createNotice = "";
    renderList(state.lastTotal || state.plans.length, state.queueCount || 0);
  }

  function cancelCreatePlan() {
    state.showCreate = false;
    state.createNotice = "";
    renderList(state.lastTotal || state.plans.length, state.queueCount || 0);
  }

  async function createPlan() {
    const owner = currentFormValue("create_owner_userid");
    if (!owner) {
      state.showCreate = true;
      state.createNotice = "请选择运营成员";
      state.notice = "";
      renderList(state.lastTotal || state.plans.length, state.queueCount || 0);
      return;
    }
    try {
      const created = await requestJson(routes.apiPlans, {
        method: "POST",
        body: {
          plan_name: currentFormValue("create_plan_name") || "新建群运营计划",
          plan_type: currentFormValue("create_plan_type") || "standard",
          owner_userid: owner,
          status: "draft",
        },
      });
      const item = created.item || created;
      if (item.id) window.location.assign(routes.plan(item.id));
    } catch (error) {
      const message = requestErrorMessage(error, "创建失败");
      state.showCreate = true;
      state.createNotice = message.includes("创建失败") ? message : `创建失败：${message}`;
      state.notice = "";
      renderList(state.lastTotal || state.plans.length, state.queueCount || 0);
    }
  }

  async function disablePlan(planId) {
    if (!planId) return;
    await requestJson(routes.apiPlanDisable(planId), { method: "POST" });
    loadListPage();
  }

  async function enablePlan(planId) {
    if (!planId) return;
    await requestJson(routes.apiPlanEnable(planId), { method: "POST" });
    loadListPage();
  }

  async function deletePlan(planId) {
    if (!planId) return;
    const current = state.plans.find((item) => Number(item.id) === Number(planId));
    const label = current && current.plan_name ? `「${current.plan_name}」` : "该计划";
    if (!window.confirm(`确认删除${label}？删除后列表将不再显示。`)) return;
    await requestJson(routes.apiPlan(planId), { method: "DELETE" });
    loadListPage();
  }

  async function savePlan() {
    if (!state.plan || !state.plan.id) return;
    await requestJson(routes.apiPlan(state.plan.id), {
      method: "PUT",
      body: {
        plan_name: currentFormValue("plan_name") || state.plan.plan_name,
        plan_code: state.plan.plan_code,
        plan_type: currentFormValue("plan_type") || state.plan.plan_type,
        owner_userid: currentFormValue("owner_userid") || state.plan.owner_userid,
        status: currentFormValue("status") || state.plan.status,
      },
    });
    state.notice = "已保存";
    loadDetailPage(state.plan.id);
  }

  function saveCurrentDimensionDisabled() {
    return state.activeDetailPanel !== "basic";
  }

  function saveActiveDetailPanel() {
    if (state.activeDetailPanel === "basic") return savePlan();
    return undefined;
  }

  async function bindGroup(chatId) {
    if (!state.plan || !chatId) return;
    await requestJson(routes.apiPlanGroups(state.plan.id), { method: "POST", body: { chat_id: chatId, operator: "admin_ui" } });
    state.notice = "已添加";
    loadDetailPage(state.plan.id);
  }

  function openGroupPicker() {
    state.showGroupPicker = true;
    state.groupPickerSearch = "";
    state.groupPickerNotice = "";
    renderDetail();
  }

  function closeGroupPicker() {
    if (state.bindingGroups) return;
    state.showGroupPicker = false;
    state.groupPickerSearch = "";
    state.groupPickerNotice = "";
    renderDetail();
  }

  async function confirmGroupPicker() {
    if (!state.plan || !state.plan.id || state.bindingGroups) return;
    const selected = Array.from(app.querySelectorAll("[data-group-choice]:checked")).map((item) => item.value).filter(Boolean);
    if (!selected.length) {
      state.groupPickerNotice = "请选择群";
      renderDetail();
      return;
    }
    state.bindingGroups = true;
    state.groupPickerNotice = "绑定中";
    renderDetail();
    try {
      for (const chatId of selected) {
        await requestJson(routes.apiPlanGroups(state.plan.id), { method: "POST", body: { chat_id: chatId, operator: "admin_ui" } });
      }
      state.notice = `已添加 ${formatNumber(selected.length)} 个群`;
      state.showGroupPicker = false;
      state.groupPickerSearch = "";
      state.groupPickerNotice = "";
      loadDetailPage(state.plan.id);
    } catch (error) {
      state.groupPickerNotice = requestErrorMessage(error, "绑定失败");
      state.notice = "";
      renderDetail();
    } finally {
      state.bindingGroups = false;
    }
  }

  async function removeGroup(chatId) {
    if (!state.plan || !chatId) return;
    await requestJson(routes.apiPlanGroup(state.plan.id, chatId), { method: "DELETE" });
    state.notice = "已移除";
    loadDetailPage(state.plan.id);
  }

  async function loadOwnerGroups(ownerUserId) {
    const owner = String(ownerUserId || "").trim();
    if (!owner) {
      state.groups = [];
      renderDetail();
      return;
    }
    const payload = await requestJson(`${routes.apiGroups}?owner_userid=${encodeURIComponent(owner)}`);
    state.groups = normalizeItems(payload);
    renderDetail();
  }

  async function refreshOwnerGroups() {
    if (!state.plan) return;
    const owner = currentFormValue("owner_userid") || state.plan.owner_userid || "";
    if (!owner) {
      state.notice = "请选择运营成员";
      renderDetail();
      return;
    }
    state.refreshingOwnerGroups = true;
    state.notice = "刷新中";
    renderDetail();
    try {
      const synced = await requestJson(routes.apiGroupsSync, {
        method: "POST",
        body: {
          owner_userid: owner,
          limit: 100,
          operator: "admin_ui",
        },
      });
      const payload = await requestJson(`${routes.apiGroups}?owner_userid=${encodeURIComponent(owner)}`);
      state.groups = normalizeItems(payload);
      state.notice = `已刷新：新增 ${formatNumber(synced.new_count || 0)} 个，更新 ${formatNumber(synced.updated_count || 0)} 个`;
    } catch (error) {
      state.notice = requestErrorMessage(error, "刷新失败");
    } finally {
      state.refreshingOwnerGroups = false;
      renderDetail();
    }
  }

  function openNodeModal(nodeId) {
    state.editingNodeId = Number(nodeId || 0);
    state.showNodeModal = true;
    renderDetail();
  }

  function closeNodeModal() {
    state.editingNodeId = 0;
    state.showNodeModal = false;
    renderDetail();
  }

  function editingNode() {
    return state.nodes.find((node) => Number(node.id) === Number(state.editingNodeId)) || null;
  }

  function legacyAttachmentsForNode(value) {
    return Array.isArray(value) ? value : [];
  }

  async function saveNode() {
    if (!state.plan || !state.plan.id) return;
    const nodeId = Number(state.editingNodeId || 0);
    const existing = editingNode() || {};
    const contentPayload = contentPackageToNodePayload(contentPackageFromForm());
    const payload = {
      day_index: Number(currentFormValue("node_day_index") || 1),
      scheduled_time: currentFormValue("node_scheduled_time") || "20:00",
      action_title: currentFormValue("node_action_title"),
      text_content: contentPayload.text_content,
      content_package_json: contentPayload.content_package_json,
      attachments: legacyAttachmentsForNode(existing.attachments),
      sort_order: Number(currentFormValue("node_sort_order") || 0),
      status: currentFormValue("node_status") || "active",
    };
    await requestJson(nodeId ? routes.apiPlanNode(state.plan.id, nodeId) : routes.apiPlanNodes(state.plan.id), {
      method: nodeId ? "PUT" : "POST",
      body: payload,
    });
    state.notice = nodeId ? "已更新动作" : "已添加动作";
    state.editingNodeId = 0;
    state.showNodeModal = false;
    loadDetailPage(state.plan.id);
  }

  async function deleteNode(nodeId) {
    if (!state.plan || !nodeId) return;
    await requestJson(routes.apiPlanNode(state.plan.id, nodeId), { method: "DELETE" });
    state.notice = "已删除动作";
    loadDetailPage(state.plan.id);
  }

  async function copyWebhook() {
    const url = state.webhook && state.webhook.webhook_url;
    if (!url) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(url);
    }
    state.notice = "已复制";
    renderDetail();
  }

  async function loadListPage() {
    renderLoading();
    try {
      const [payload, ownersPayload] = await Promise.all([requestJson(routes.apiPlans), requestJson(routes.apiMembers)]);
      state.plans = normalizeItems(payload);
      state.ownerOptions = normalizeOwners(ownersPayload, null);
      state.lastTotal = payload.total || state.plans.length;
      state.queueCount = payload.queue_count || 0;
      renderList(state.lastTotal, state.queueCount);
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderCreatePanel() {
    if (!state.showCreate) return "";
    const ownerField = renderMemberField("create_owner_userid", (state.createOwner || {}).user_id, "pick-create-owner", state.createOwner ? "更换运营成员" : "选择运营成员");
    return `
      <section class="group-ops__card">
        <div class="group-ops__filters">
          <label class="group-ops__field group-ops__field--wide"><span>计划名称</span><input name="create_plan_name" value="新建群运营计划"></label>
          <label class="group-ops__field"><span>计划类型</span><select name="create_plan_type"><option value="standard">标准编排计划</option><option value="webhook">Webhook 接收计划</option></select></label>
          <label class="group-ops__field"><span>运营成员</span>${ownerField}</label>
          <div class="group-ops__modal-notice" ${state.createNotice ? "" : "hidden"}>${escapeHtml(state.createNotice)}</div>
          <div class="group-ops__row-actions">${actionButton("保存计划", "create-plan", "group-ops__button--primary")}${actionButton("取消", "cancel-create-plan")}</div>
        </div>
      </section>
    `;
  }

  function renderList(total, queueCount) {
    const boundCount = state.plans.reduce((sum, plan) => sum + Number(plan.bound_group_count || 0), 0);
    const reach = state.plans.reduce((sum, plan) => sum + Number(plan.today_estimated_reach || 0), 0);
    const rows = state.plans
      .map(
        (plan) => `
        <tr>
          <td><strong>${escapeHtml(plan.plan_name)}</strong></td>
          <td>${escapeHtml(typeText(plan.plan_type))}</td>
          <td>${escapeHtml(plan.owner_name || plan.owner_userid || "-")}</td>
          <td>${formatNumber(plan.bound_group_count)}</td>
          <td>${formatNumber(plan.today_estimated_reach)}</td>
          <td><span class="group-ops__chip${plan.status === "active" ? " group-ops__chip--ok" : " group-ops__chip--neutral"}">${escapeHtml(statusText(plan.status))}</span></td>
          <td>
            <div class="group-ops__row-actions">
              <a class="group-ops__button group-ops__button--primary" href="${escapeHtml(routes.plan(plan.id))}">编辑</a>
              ${
                plan.status === "active"
                  ? `<button class="group-ops__button" type="button" data-action="disable-plan" data-plan-id="${escapeHtml(plan.id)}">停用</button>`
                  : `<button class="group-ops__button" type="button" data-action="enable-plan" data-plan-id="${escapeHtml(plan.id)}">启用</button>`
              }
              <button class="group-ops__button group-ops__button--danger" type="button" data-action="delete-plan" data-plan-id="${escapeHtml(plan.id)}">删除</button>
            </div>
          </td>
        </tr>`,
      )
      .join("");
    renderShell(`
      <div class="group-ops__bar">
        ${pageButton("查看所有群", routes.groups)}
        ${actionButton("创建计划", "show-create-plan", "group-ops__button--primary")}
      </div>
      <div class="group-ops__notice" ${state.notice ? "" : "hidden"}>${escapeHtml(state.notice)}</div>
      <section class="group-ops__metric-grid">
        ${metricCard("运营计划", formatNumber(total))}
        ${metricCard("已绑定群", formatNumber(boundCount))}
        ${metricCard("今日预估", formatNumber(reach))}
        ${metricCard("通知排队队列", formatNumber(queueCount))}
      </section>
      ${renderCreatePanel()}
      <section class="group-ops__card">
        <div class="group-ops__table-wrap">
          <table class="group-ops__table">
            <thead>
              <tr><th>计划名称</th><th>类型</th><th>运营成员</th><th>绑定群</th><th>今日预估</th><th>状态</th><th>操作</th></tr>
            </thead>
            <tbody>${rows || '<tr><td colspan="7" class="group-ops__empty">暂无数据</td></tr>'}</tbody>
          </table>
        </div>
      </section>
    `);
    state.notice = "";
    state.createNotice = "";
  }

  async function loadDetailPage(planId) {
    renderLoading();
    try {
      const [planPayload, groupPayload, ownersPayload] = await Promise.all([
        requestJson(routes.apiPlan(planId)),
        requestJson(routes.apiPlanGroups(planId)),
        requestJson(routes.apiMembers),
      ]);
      state.plan = planPayload.item || planPayload.plan || planPayload;
      const isWebhook = state.plan.plan_type === "webhook";
      const [allGroupsPayload, typePayload] = await Promise.all([
        requestJson(`${routes.apiGroups}?owner_userid=${encodeURIComponent(state.plan.owner_userid || "")}`),
        requestJson(isWebhook ? routes.apiWebhook(planId) : routes.apiPlanNodes(planId)),
      ]);
      state.planGroups = normalizeItems(groupPayload);
      state.groupSummary = groupPayload.summary || null;
      state.groups = normalizeItems(allGroupsPayload);
      state.ownerOptions = normalizeOwners(ownersPayload, state.plan);
      if (isWebhook) {
        state.nodes = [];
        state.webhook = typePayload;
      } else {
        state.nodes = normalizeItems(typePayload);
        state.webhook = null;
      }
      renderDetail();
    } catch (error) {
      renderError(error.message);
    }
  }

  function groupName(row) {
    return row.group_name || row.group_name_snapshot || row.chat_id || "-";
  }

  function groupOwner(row) {
    return row.owner_name || row.owner_userid || row.owner_userid_snapshot || "-";
  }

  function groupAdminUserids(row) {
    if (Array.isArray(row.admin_userids)) return row.admin_userids.map((item) => String(item || "").trim()).filter(Boolean);
    try {
      const parsed = JSON.parse(row.admin_userids || "[]");
      return Array.isArray(parsed) ? parsed.map((item) => String(item || "").trim()).filter(Boolean) : [];
    } catch (error) {
      return [];
    }
  }

  function groupManageableBy(group, userid) {
    const member = String(userid || "").trim();
    if (!member) return false;
    return group.owner_userid === member || groupAdminUserids(group).includes(member);
  }

  function renderBoundGroups() {
    if (!state.planGroups.length) return '<div class="group-ops__empty">暂无绑定群</div>';
    return state.planGroups
      .map(
        (group) => `
        <div class="group-ops__group-item">
          <div>
            <div class="group-ops__group-name"><strong>${escapeHtml(groupName(group))}</strong></div>
            <div class="group-ops__group-meta">${escapeHtml(group.chat_id || "")}</div>
          </div>
          ${actionButton("移除", "remove-group", "") .replace(">", ` data-chat-id="${escapeHtml(group.chat_id)}">`)}
        </div>`,
      )
      .join("");
  }

  function availableGroupsForCurrentOwner() {
    const selectedOwner = currentFormValue("owner_userid") || (state.plan && state.plan.owner_userid) || "";
    const bound = new Set(state.planGroups.map((group) => group.chat_id));
    return state.groups.filter((group) => groupManageableBy(group, selectedOwner) && !bound.has(group.chat_id));
  }

  function renderGroupPickerOptions() {
    const keyword = String(state.groupPickerSearch || "").trim().toLowerCase();
    const rows = availableGroupsForCurrentOwner().filter((group) => {
      if (!keyword) return true;
      return `${group.group_name || ""} ${group.chat_id || ""}`.toLowerCase().includes(keyword);
    });
    if (!rows.length) return '<div class="group-ops__empty">暂无可选群</div>';
    return rows
      .map(
        (group) => `
        <label class="group-ops__group-item group-ops__group-choice">
          <input type="checkbox" data-group-choice value="${escapeHtml(group.chat_id)}">
          <div>
            <div class="group-ops__group-name"><strong>${escapeHtml(group.group_name)}</strong></div>
            <div class="group-ops__group-meta">${escapeHtml(group.chat_id)}</div>
          </div>
        </label>`,
      )
      .join("");
  }

  function renderGroupPickerModal() {
    if (!state.showGroupPicker) return "";
    return `
      <div class="group-ops__modal-mask" role="dialog" aria-modal="true">
        <div class="group-ops__modal group-ops__modal--groups">
          <div class="group-ops__modal-head">
            <h3>选择群</h3>
            ${actionButton("关闭", "close-group-picker")}
          </div>
          <label class="group-ops__field">
            <span>群名 / 群 ID</span>
            <input data-group-picker-search name="group_picker_keyword" value="${escapeHtml(state.groupPickerSearch)}">
          </label>
          <div class="group-ops__modal-notice" ${state.groupPickerNotice ? "" : "hidden"}>${escapeHtml(state.groupPickerNotice)}</div>
          <div class="group-ops__group-picker-list">${renderGroupPickerOptions()}</div>
          <div class="group-ops__modal-footer">
            ${actionButton("取消", "close-group-picker")}
            <button class="group-ops__button group-ops__button--primary" type="button" data-action="confirm-group-picker"${
              state.bindingGroups ? " disabled" : ""
            }>${state.bindingGroups ? "绑定中" : "确认选择"}</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderRefreshOwnerGroupsButton() {
    const owner = currentFormValue("owner_userid") || (state.plan && state.plan.owner_userid) || "";
    const disabled = !owner || state.refreshingOwnerGroups;
    return `<button class="group-ops__button" type="button" data-action="refresh-owner-groups"${disabled ? " disabled" : ""}>${
      state.refreshingOwnerGroups ? "刷新中" : "刷新名下群聊"
    }</button>`;
  }

  function refreshNodeContentSummary(contentPackage) {
    const target = app.querySelector("[data-node-content-summary]");
    if (!target) return;
    const summary = contentPackageSummary(contentPackage);
    target.innerHTML =
      `<strong>话术：</strong><span>${escapeHtml(summary.text)}</span>` +
      `<strong>内容：</strong><span>图片 ${summary.imageCount} / 小程序 ${summary.miniprogramCount} / 附件 ${summary.attachmentCount} / 客户群 ${summary.groupInviteCount}</span>`;
  }

  function renderLegacyAttachmentNotice(node) {
    if (!legacyAttachmentsForNode((node || {}).attachments).length) return "";
    return `
      <div class="group-ops__modal-notice">
        历史素材已保留，保存新素材不会自动删除历史素材
      </div>
    `;
  }

  function openNodeContentComposer() {
    const hidden = app.querySelector('[name="node_content_package_json"]');
    if (!hidden) return;
    if (!window.AICRMSendContentComposer || typeof window.AICRMSendContentComposer.open !== "function") {
      state.notice = "标准发送内容组件加载失败，请刷新页面后重试";
      renderDetail();
      return;
    }
    window.AICRMSendContentComposer.open({
      title: "配置群运营动作内容",
      textEnabled: true,
      value: contentPackageFromForm(),
      limits: {
        image: 3,
        miniprogram: 1,
        attachment: 9,
        group_invite: 1,
      },
      onConfirm(contentPackage) {
        const normalized = normalizeContentPackage(contentPackage);
        hidden.value = JSON.stringify(normalized);
        refreshNodeContentSummary(normalized);
      },
    });
  }

  function renderStats(summary) {
    return `
      ${statCard("运营成员", state.plan.owner_name || state.plan.owner_userid || "-")}
      ${statCard("绑定群", formatNumber(summary.bound_group_count))}
      ${statCard("外部联系人", formatNumber(summary.external_member_count))}
      ${statCard("状态", statusText(state.plan.status))}
    `;
  }

  function renderScheduledTimeOptions(value) {
    const current = GROUP_OPS_SCHEDULED_TIME_OPTIONS.includes(value) ? value : "20:00";
    return GROUP_OPS_SCHEDULED_TIME_OPTIONS.map(
      (option) => `<option value="${escapeHtml(option)}"${option === current ? " selected" : ""}>${escapeHtml(option)}</option>`,
    ).join("");
  }

  function renderNodes() {
    const current = editingNode() || {
      day_index: 1,
      scheduled_time: "20:00",
      action_title: "",
      text_content: "",
      attachments: [],
      content_package_json: {},
      sort_order: 10,
      status: "active",
    };
    const currentContentPackage = nodeToContentPackage(current);
    const currentContentSummary = contentPackageSummary(currentContentPackage);
    const modal = state.showNodeModal
      ? `
        <div class="group-ops__modal-mask" role="dialog" aria-modal="true">
          <div class="group-ops__modal group-ops__modal--action">
            <div class="group-ops__modal-head">
              <h3>${state.editingNodeId ? "编辑动作" : "添加动作"}</h3>
              ${actionButton("关闭", "cancel-node")}
            </div>
            <div class="group-ops__action-modal-grid">
              <div class="group-ops__schedule-fields">
                <label class="group-ops__field"><span>第几天</span><input name="node_day_index" type="number" min="1" value="${escapeHtml(current.day_index)}"></label>
                <label class="group-ops__field"><span>发送时间</span><select name="node_scheduled_time">${renderScheduledTimeOptions(current.scheduled_time)}</select></label>
                <label class="group-ops__field group-ops__field--wide"><span>动作标题</span><input name="node_action_title" value="${escapeHtml(current.action_title || "")}"></label>
                <label class="group-ops__field"><span>排序</span><input name="node_sort_order" type="number" value="${escapeHtml(current.sort_order || 0)}"></label>
                <label class="group-ops__field"><span>状态</span><select name="node_status"><option value="active"${current.status === "active" ? " selected" : ""}>启用</option><option value="draft"${current.status === "draft" ? " selected" : ""}>草稿</option><option value="disabled"${current.status === "disabled" ? " selected" : ""}>停用</option></select></label>
              </div>
              <aside class="group-ops__content-box">
                <div class="group-ops__content-summary" data-node-content-summary>
                  <strong>话术摘要</strong><span>${escapeHtml(currentContentSummary.text)}</span>
                  <strong>内容数量</strong><span>图片 ${currentContentSummary.imageCount} / 小程序 ${currentContentSummary.miniprogramCount} / 附件 ${currentContentSummary.attachmentCount} / 客户群 ${currentContentSummary.groupInviteCount}</span>
                </div>
                <button class="group-ops__button" type="button" data-action="configure-node-content">配置话术和素材</button>
                ${renderLegacyAttachmentNotice(current)}
                <input type="hidden" name="node_content_package_json" value="${escapeHtml(JSON.stringify(currentContentPackage))}">
              </aside>
            </div>
            <div class="group-ops__modal-footer">
              ${actionButton("取消", "cancel-node")}
              ${actionButton("保存动作", "save-node", "group-ops__button--primary")}
            </div>
          </div>
        </div>`
      : "";
    const rows = state.nodes
      .map(
        (node) => `
        <tr>
          <td>第 ${escapeHtml(node.day_index)} 天</td>
          <td>${escapeHtml(node.scheduled_time || "-")}</td>
          <td>${escapeHtml(node.action_title || "-")}</td>
          <td><span class="group-ops__summary">${escapeHtml(textSummary(nodeToContentPackage(node).content_text || node.text_content))}</span></td>
          <td><div class="group-ops__chip-row">${materialChips(node)}</div></td>
          <td><div class="group-ops__row-actions">
            ${actionButton("编辑", "edit-node", "").replace(">", ` data-node-id="${escapeHtml(node.id)}">`)}
            ${actionButton("删除", "delete-node", "group-ops__button--danger").replace(">", ` data-node-id="${escapeHtml(node.id)}">`)}
          </div></td>
        </tr>`,
      )
      .join("");
    const isStandard = state.plan.plan_type === "standard";
    return `
      <section class="group-ops__panel${state.activeDetailPanel === "nodes" ? " is-active" : ""}" id="panel-nodes">
        <div class="group-ops__panel-title-row">
          <h3>标准编排</h3>
          ${isStandard ? actionButton("添加动作", "open-node-modal", "group-ops__button--primary") : ""}
        </div>
        <div class="group-ops__table-wrap">
          <table class="group-ops__table">
            <thead><tr><th>第几天</th><th>发送时间</th><th>动作标题</th><th>标准话术摘要</th><th>素材标签</th><th class="group-ops__table-actions-head">操作</th></tr></thead>
            <tbody>${
              isStandard
                ? rows || '<tr><td colspan="6" class="group-ops__empty">暂无节点</td></tr>'
                : '<tr><td colspan="6" class="group-ops__empty">Webhook 接收计划无需配置标准编排</td></tr>'
            }</tbody>
          </table>
        </div>
      </section>
      ${isStandard ? modal : ""}
    `;
  }

  function renderWebhook() {
    const config = state.webhook || {};
    if (state.plan.plan_type !== "webhook") {
      return `
        <section class="group-ops__panel${state.activeDetailPanel === "webhook" ? " is-active" : ""}" id="panel-webhook">
          <div class="group-ops__panel-title-row">
            <h3>Webhook</h3>
          </div>
          <div class="group-ops__empty">标准编排计划无需配置 Webhook</div>
        </section>
      `;
    }
    return `
      <section class="group-ops__panel${state.activeDetailPanel === "webhook" ? " is-active" : ""}" id="panel-webhook">
        <div class="group-ops__panel-title-row">
          <h3>Webhook</h3>
          <span class="group-ops__pill">Webhook 接收计划</span>
        </div>
        <div class="group-ops__webhook-panel">
          <div class="group-ops__webhook-line">
            <span class="group-ops__chip">POST</span>
            <div class="group-ops__url">${escapeHtml(config.webhook_url || "")}</div>
            ${actionButton("复制地址", "copy-webhook", "group-ops__button--primary")}
          </div>
          <div class="group-ops__webhook-line">
            <strong>认证方式</strong>
            <span class="group-ops__chip group-ops__chip--ok">HTTP Message Signatures</span>
          </div>
        </div>
      </section>
    `;
  }

  function detailPanelButton(key, index, label) {
    const active = state.activeDetailPanel === key;
    return `
      <button class="${active ? "is-active" : ""}" type="button" data-action="switch-detail-panel" data-panel="${escapeHtml(key)}">
        <span class="group-ops__detail-index">${escapeHtml(index)}</span>
        <span class="group-ops__detail-nav-label">${escapeHtml(label)}</span>
      </button>
    `;
  }

  function renderDetailNav() {
    return `
      <nav class="group-ops__side-nav" aria-label="群运营计划配置维度">
        ${detailPanelButton("basic", "1", "基础配置")}
        ${detailPanelButton("groups", "2", "绑定群")}
        ${detailPanelButton("webhook", "3", "Webhook")}
        ${detailPanelButton("nodes", "4", "标准编排")}
      </nav>
    `;
  }

  function renderBasicPanel() {
    return `
      <section class="group-ops__panel${state.activeDetailPanel === "basic" ? " is-active" : ""}" id="panel-basic">
        <div class="group-ops__panel-title-row">
          <h3>基础配置</h3>
          <span class="group-ops__pill">可保存</span>
        </div>
        <div class="group-ops__form-grid">
          <div class="group-ops__field group-ops__field--full">
            <span>运营成员</span>
            ${renderMemberField("owner_userid", state.plan.owner_userid, "pick-plan-owner", "更换运营成员")}
          </div>
          <label class="group-ops__field">
            <span>状态</span>
            <select name="status">
              <option value="draft"${state.plan.status === "draft" ? " selected" : ""}>草稿</option>
              <option value="active"${state.plan.status === "active" ? " selected" : ""}>启用</option>
              <option value="disabled"${state.plan.status === "disabled" ? " selected" : ""}>停用</option>
            </select>
          </label>
          <label class="group-ops__field">
            <span>计划名称</span>
            <input name="plan_name" value="${escapeHtml(state.plan.plan_name || "")}">
          </label>
          <label class="group-ops__field">
            <span>计划类型</span>
            <select name="plan_type">
              <option value="standard"${state.plan.plan_type === "standard" ? " selected" : ""}>标准编排计划</option>
              <option value="webhook"${state.plan.plan_type === "webhook" ? " selected" : ""}>Webhook 接收计划</option>
            </select>
          </label>
        </div>
        <div class="group-ops__panel-actions">
          ${renderRefreshOwnerGroupsButton()}
          ${actionButton("保存基础配置", "save-plan", "group-ops__button--primary")}
        </div>
      </section>
    `;
  }

  function renderGroupsPanel() {
    return `
      <section class="group-ops__panel${state.activeDetailPanel === "groups" ? " is-active" : ""}" id="panel-groups">
        <div class="group-ops__panel-title-row">
          <h3>绑定群</h3>
          ${actionButton("选择群", "open-group-picker", "group-ops__button--primary")}
        </div>
        <div class="group-ops__group-list">${renderBoundGroups()}</div>
        <div class="group-ops__panel-actions">
          ${renderRefreshOwnerGroupsButton()}
        </div>
      </section>
    `;
  }

  function renderDetailPanels() {
    return `
      <div class="group-ops__panel-card">
        ${renderBasicPanel()}
        ${renderGroupsPanel()}
        ${renderWebhook()}
        ${renderNodes()}
      </div>
    `;
  }

  function renderDetailShell(summary) {
    return `
      <div class="group-ops__notice" ${state.notice ? "" : "hidden"}>${escapeHtml(state.notice)}</div>
      <section class="group-ops__detail-shell">
        <section class="group-ops__summary-card">
          <div class="group-ops__summary-head">
            <h2>${escapeHtml(state.plan.plan_name || "群运营计划")}</h2>
            <div class="group-ops__summary-actions">
              ${pageButton("返回列表", routes.list)}
              <button class="group-ops__button group-ops__button--primary" type="button" data-action="save-active-detail-panel"${
                saveCurrentDimensionDisabled() ? " disabled" : ""
              }>保存当前维度</button>
            </div>
          </div>
          <div class="group-ops__summary-grid">${renderStats(summary)}</div>
        </section>
        <section class="group-ops__workspace">
          ${renderDetailNav()}
          ${renderDetailPanels()}
        </section>
      </section>
      ${renderGroupPickerModal()}
    `;
  }

  function renderDetail() {
    const summary = state.groupSummary || state.plan.groups_summary || {
      bound_group_count: state.planGroups.length,
      internal_member_count: state.planGroups.reduce((sum, item) => sum + Number(item.internal_member_count_snapshot || 0), 0),
      external_member_count: state.planGroups.reduce((sum, item) => sum + Number(item.external_member_count_snapshot || 0), 0),
      estimated_reach: state.planGroups.reduce((sum, item) => sum + Number(item.external_member_count_snapshot || 0), 0),
    };
    renderShell(renderDetailShell(summary));
    state.notice = "";
  }

  function groupsQueryParams() {
    const params = new URLSearchParams();
    const keyword = currentFormValue("keyword");
    const owner = currentFormValue("owner_userid");
    const plan = currentFormValue("plan_id");
    const bind = currentFormValue("bind_status");
    if (keyword) params.set("keyword", keyword);
    if (owner) params.set("owner_userid", owner);
    if (plan) params.set("plan_id", plan);
    if (bind) params.set("bind_status", bind);
    return params.toString();
  }

  async function loadGroupsPage() {
    try {
      const query = groupsQueryParams();
      const [groupPayload, planPayload, ownersPayload] = await Promise.all([
        requestJson(query ? `${routes.apiGroups}?${query}` : routes.apiGroups),
        state.plans.length ? Promise.resolve({ items: state.plans }) : requestJson(routes.apiPlans),
        requestJson(routes.apiMembers),
      ]);
      state.groups = normalizeItems(groupPayload);
      state.plans = normalizeItems(planPayload);
      state.ownerOptions = normalizeOwners(ownersPayload, null);
      renderGroups();
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderPlanFilter() {
    return state.plans
      .map((plan) => `<option value="${escapeHtml(plan.id)}">${escapeHtml(plan.plan_name)}</option>`)
      .join("");
  }

  function renderGroups() {
    const rows = state.groups
      .map(
        (group) => `
        <tr>
          <td><strong>${escapeHtml(groupName(group))}</strong></td>
          <td>${escapeHtml(group.chat_id || "-")}</td>
          <td>${escapeHtml(groupOwner(group))}</td>
          <td>${escapeHtml(group.plan_name || "-")}</td>
          <td><span class="group-ops__chip${group.bind_status === "bound" ? " group-ops__chip--ok" : " group-ops__chip--neutral"}">${escapeHtml(group.bind_status === "bound" ? "已绑定" : "未绑定")}</span></td>
        </tr>`,
      )
      .join("");
    renderShell(`
      <div class="group-ops__bar">${pageButton("返回列表", routes.list)}</div>
      <section class="group-ops__card">
        <div class="group-ops__filters">
          <label class="group-ops__field group-ops__field--wide"><span>群名 / 群 ID</span><input name="keyword" data-filter></label>
          <label class="group-ops__field"><span>群主/管理员</span>${renderMemberField("owner_userid", (state.groupFilterOwner || {}).user_id, "pick-group-filter-owner", state.groupFilterOwner ? "更换成员" : "选择成员")}</label>
          <div class="group-ops__row-actions">${actionButton("清除成员", "clear-group-filter-owner")}</div>
          <label class="group-ops__field"><span>所属计划</span><select name="plan_id" data-filter><option value="">全部</option>${renderPlanFilter()}</select></label>
          <label class="group-ops__field"><span>已绑定 / 未绑定</span><select name="bind_status" data-filter><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label>
        </div>
      </section>
      <section class="group-ops__card">
        <div class="group-ops__table-wrap">
          <table class="group-ops__table">
            <thead><tr><th>群名</th><th>群 ID</th><th>群主</th><th>所属计划</th><th>状态</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="5" class="group-ops__empty">暂无数据</td></tr>'}</tbody>
          </table>
        </div>
      </section>
    `);
  }

  if (state.mode === "detail" && state.planId) {
    loadDetailPage(state.planId);
  } else if (state.mode === "groups") {
    renderLoading();
    loadGroupsPage();
  } else {
    loadListPage();
  }
})(window, document);
