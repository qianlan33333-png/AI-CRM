(function (window, document) {
  "use strict";

  const root = document.getElementById("spMemberGrid");
  if (!root) return;

  if (String(root.dataset.mode || "internal") === "internal") {
    document.body.classList.add("sp-grid-admin-shell");
  }

  const GridState = window.ServicePeriodMemberGridState;
  const AdminApi = window.AdminApi;
  const publicMode = String(root.dataset.mode || "internal") === "public";
  if (!GridState || (!publicMode && (!AdminApi || typeof AdminApi.requestJson !== "function"))) return;

  let serviceProductId = String(root.dataset.serviceProductId || "");
  let apiBase = serviceProductId ? `/api/admin/service-period-products/${encodeURIComponent(serviceProductId)}` : "";
  const shareToken = publicMode ? String(window.location.hash || "").replace(/^#/, "").trim() : "";
  const $ = (id) => document.getElementById(id);
  const elements = {
    viewTabs: $("spViewTabs"),
    addView: $("spAddView"),
    viewMenuButton: $("spViewMenuButton"),
    viewMenu: $("spViewMenu"),
    filterButton: $("spFilterButton"),
    groupButton: $("spGroupButton"),
    sortButton: $("spSortButton"),
    filterCount: $("spFilterCount"),
    groupCount: $("spGroupCount"),
    sortCount: $("spSortCount"),
    saveView: $("spSaveView"),
    saveAsView: $("spSaveAsView"),
    draftStatus: $("spDraftStatus"),
    readonlyNotice: $("spReadonlyNotice"),
    configPanel: $("spConfigPanel"),
    resultSummary: $("spResultSummary"),
    gridShell: $("spGridShell"),
    gridScroll: $("spGridScroll"),
    gridHead: $("spGridHead"),
    gridBody: $("spGridBody"),
    gridSentinel: $("spGridSentinel"),
    gridState: $("spGridState"),
    toast: $("spGridToast"),
    nameDialog: $("spNameDialog"),
    nameDialogForm: $("spNameDialogForm"),
    nameDialogTitle: $("spNameDialogTitle"),
    nameDialogInput: $("spViewNameInput"),
    nameDialogHint: $("spNameDialogHint"),
    nameDialogSubmit: $("spNameDialogSubmit"),
    unsavedDialog: $("spUnsavedDialog"),
    confirmDialog: $("spConfirmDialog"),
    confirmTitle: $("spConfirmTitle"),
    confirmCopy: $("spConfirmCopy"),
    conflictDialog: $("spConflictDialog"),
  };

  const state = {
    schema: null,
    fields: [],
    fieldMap: new Map(),
    views: [],
    activeViewId: "",
    savedConfig: GridState.emptyConfig(),
    draftConfig: GridState.emptyConfig(),
    rows: [],
    nextCursor: "",
    total: null,
    loading: false,
    querySequence: 0,
    queryTimer: 0,
    observer: null,
    panelKind: "",
    collapsed: new Set(),
    canManageViews: false,
    editableFields: new Set(),
    toastTimer: 0,
    access: null,
  };

  const operatorLabels = new Map();
  const triStateLabels = {yes: "是", no: "否", unmatched: "未匹配"};
  const progressLabels = {
    unmatched: "—",
    no_plan: "无",
    not_started: "未开始",
    in_progress: "进行中",
    complete: "已完成",
  };

  function activeView() {
    return state.views.find((view) => String(view.id) === String(state.activeViewId)) || null;
  }

  function isDirty() {
    return GridState.isDirty(state.draftConfig, state.savedConfig);
  }

  function escapeHtml(value) {
    if (AdminApi && typeof AdminApi.escapeHtml === "function") return AdminApi.escapeHtml(String(value ?? ""));
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function errorMessage(error, fallback) {
    const detail = error && error.payload && error.payload.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") return String(detail.message || detail.error || fallback);
    return String((error && error.message) || fallback || "操作失败");
  }

  function toast(message, tone) {
    window.clearTimeout(state.toastTimer);
    elements.toast.textContent = String(message || "");
    elements.toast.className = `sp-toast${tone ? ` is-${tone}` : ""}`;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      elements.toast.hidden = true;
    }, tone === "error" ? 5200 : 2600);
  }

  function setGridState(message, kind) {
    if (!message) {
      elements.gridState.hidden = true;
      elements.gridState.className = "sp-grid-state";
      return;
    }
    elements.gridState.className = `sp-grid-state${kind ? ` is-${kind}` : ""}`;
    elements.gridState.innerHTML = kind === "loading"
      ? `<span class="sp-spinner" aria-hidden="true"></span><span>${escapeHtml(message)}</span>`
      : `<span>${escapeHtml(message)}</span>`;
    elements.gridState.hidden = false;
  }

  async function request(path, options) {
    if (!publicMode) return AdminApi.requestJson(path, options || {});
    const settings = options || {};
    const method = String(settings.method || "GET").toUpperCase();
    const response = await window.fetch(path, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-AICRM-Grid-Share-Token": shareToken,
      },
      body: method === "GET" ? undefined : JSON.stringify(settings.body || {}),
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(errorMessage({payload}, "共享数据加载失败"));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function fieldIconUrl(icon) {
    const names = {
      text: "bars-3-bottom-left",
      number: "hashtag",
      person: "user",
      check: "check-circle",
      progress: "chart-bar",
      datetime: "calendar-days",
    };
    return `/static/service-period/icons/${names[String(icon || "")] || "bars-3-bottom-left"}.svg`;
  }

  function renderHeader() {
    const cells = ["<th scope=\"col\" aria-label=\"行号\">#</th>"];
    state.fields.forEach((field) => {
      cells.push(
        `<th scope="col" class="sp-col-${escapeHtml(field.id)}">` +
          `<span class="sp-field-head"><img class="sp-field-icon" src="${fieldIconUrl(field.icon)}" alt="">` +
          `<span>${escapeHtml(field.label)}</span></span></th>`,
      );
    });
    elements.gridHead.innerHTML = `<tr>${cells.join("")}</tr>`;
  }

  function renderViews() {
    elements.viewTabs.innerHTML = state.views.map((view) => {
      const active = String(view.id) === String(state.activeViewId);
      return `<button class="sp-view-tab${active ? " is-active" : ""}" type="button" role="tab" ` +
        `aria-selected="${active ? "true" : "false"}" data-view-id="${escapeHtml(view.id)}" title="${escapeHtml(view.name)}">` +
        `<img class="sp-view-tab__icon" src="/static/service-period/icons/table-cells.svg" alt="">` +
        `<span class="sp-view-tab__name">${escapeHtml(view.name)}</span>` +
        `${active && isDirty() ? '<span class="sp-view-tab__dirty" aria-label="有未保存修改"></span>' : ""}` +
        `</button>`;
    }).join("");
    if (elements.addView) elements.addView.hidden = publicMode || !state.canManageViews;
    if (elements.viewMenuButton) elements.viewMenuButton.hidden = publicMode || !state.canManageViews || !activeView();
    const selected = elements.viewTabs.querySelector(".is-active");
    if (selected) selected.scrollIntoView({block: "nearest", inline: "nearest"});
  }

  function setCount(button, badge, value) {
    const count = Number(value || 0);
    badge.textContent = String(count);
    badge.hidden = count < 1;
    button.classList.toggle("has-value", count > 0);
  }

  function renderToolbar() {
    if (publicMode) return;
    const conditions = state.draftConfig.filter.conditions || [];
    setCount(elements.filterButton, elements.filterCount, conditions.length);
    setCount(elements.groupButton, elements.groupCount, state.draftConfig.groups.length);
    setCount(elements.sortButton, elements.sortCount, state.draftConfig.sorts.length);
    const dirty = isDirty();
    elements.saveView.hidden = !state.canManageViews;
    elements.saveAsView.hidden = !state.canManageViews;
    elements.saveView.disabled = !dirty;
    elements.draftStatus.className = `sp-draft-status ${dirty ? "is-dirty" : "is-saved"}`;
    elements.draftStatus.textContent = dirty ? "草稿已应用，尚未保存" : "视图配置已保存";
    elements.readonlyNotice.hidden = state.canManageViews;
  }

  function renderResultSummary() {
    const loaded = state.rows.length;
    const total = Number.isFinite(Number(state.total)) ? Number(state.total) : null;
    if (total === null) {
      elements.resultSummary.textContent = `已加载 ${loaded} 行`;
    } else if (state.nextCursor) {
      elements.resultSummary.textContent = `共 ${total} 行，已加载 ${loaded} 行`;
    } else {
      elements.resultSummary.textContent = `共 ${total} 行`;
    }
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date).replaceAll("/", "-");
  }

  function progressText(progress) {
    const value = progress && typeof progress === "object" ? progress : {};
    if (value.state === "unmatched") return "—";
    if (value.state === "no_plan") return "无";
    if (value.current !== null && value.current !== undefined && value.total !== null && value.total !== undefined) {
      return `${value.current}/${value.total}`;
    }
    return progressLabels[value.state] || "—";
  }

  function groupStorageKey() {
    return `service-period-member-grid:${serviceProductId}:${state.activeViewId}:collapsed`;
  }

  function loadCollapsedState() {
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(groupStorageKey()) || "[]");
      state.collapsed = new Set(Array.isArray(stored) ? stored : []);
    } catch (_error) {
      state.collapsed = new Set();
    }
  }

  function saveCollapsedState() {
    try {
      window.sessionStorage.setItem(groupStorageKey(), JSON.stringify(Array.from(state.collapsed)));
    } catch (_error) {
      // Session-only UI state must not block the shared data view.
    }
  }

  function renderGroupRow(path, level) {
    const key = GridState.pathKey(path.slice(0, level + 1));
    const item = path[level];
    const expanded = !state.collapsed.has(key);
    return `<tr class="sp-group-row" data-group-key="${escapeHtml(key)}">` +
      `<td colspan="${state.fields.length + 1}">` +
      `<button class="sp-group-toggle" type="button" aria-expanded="${expanded ? "true" : "false"}" ` +
      `style="padding-left:${14 + level * 22}px">` +
      `<img class="sp-group-toggle__chevron" src="/static/service-period/icons/chevron-down.svg" alt="">` +
      `<span>${escapeHtml(item.label || "空值")}</span>` +
      `<span class="sp-group-toggle__count">${Number(item.count || 0)} 条</span>` +
      `</button></td></tr>`;
  }

  function renderDataRow(row, rowIndex) {
    const values = row.values || {};
    const member = values.member || {};
    const progress = values.learning_plan_progress || {};
    const formal = values.formally_logged_in || "unmatched";
    const token = values.token_usage || "unmatched";
    const editableTextCell = (fieldId) => {
      const value = String(values[fieldId] || "");
      const editable = state.editableFields.has(fieldId);
      return `<td class="sp-col-${escapeHtml(fieldId)} sp-editable-text-cell${editable ? " is-editable" : ""}" ` +
        `data-row-index="${rowIndex}" data-field-id="${escapeHtml(fieldId)}"${editable ? ' tabindex="0"' : ""} ` +
        `title="${escapeHtml(value)}">${escapeHtml(value || "—")}</td>`;
    };
    const cells = [
      `<td>${rowIndex + 1}</td>`,
      `<td><div class="sp-member-cell"><div class="sp-member-primary" title="${escapeHtml(member.primary || "")}">${escapeHtml(member.primary || "—")}</div>` +
        `<div class="sp-member-secondary" title="${escapeHtml(member.secondary || row.unionid || "")}">${escapeHtml(member.secondary || row.unionid || "")}</div></div></td>`,
      `<td class="sp-col-remaining_days">${Number(values.remaining_days || 0)} 天</td>`,
      `<td class="sp-col-formally_logged_in"><span class="sp-state-text${formal === "unmatched" ? " is-unmatched" : ""}">${triStateLabels[formal] || "未匹配"}</span></td>`,
      `<td class="sp-col-token_usage"><span class="sp-state-text${token === "unmatched" ? " is-unmatched" : ""}">${triStateLabels[token] || "未匹配"}</span></td>`,
      `<td class="sp-col-learning_plan_progress"><span class="sp-progress is-${escapeHtml(progress.state || "unmatched")}" title="${escapeHtml(progressLabels[progress.state] || "")}">${escapeHtml(progressText(progress))}</span></td>`,
      `<td class="sp-col-open_count_7d">${values.open_count_7d === null || values.open_count_7d === undefined ? "—" : Number(values.open_count_7d)}</td>`,
      `<td class="sp-col-last_open_at" title="${escapeHtml(formatDateTime(values.last_open_at))}">${escapeHtml(formatDateTime(values.last_open_at))}</td>`,
      `<td class="sp-col-renewal_count">${Math.max(0, Number(values.renewal_count || 0))}</td>`,
      editableTextCell("remark"),
      editableTextCell("alliance"),
    ];
    return `<tr data-record-id="${escapeHtml(row.record_id)}">${cells.join("")}</tr>`;
  }

  function renderRows() {
    const output = [];
    let previousPath = [];
    state.rows.forEach((row, rowIndex) => {
      const path = Array.isArray(row.group_path) ? row.group_path : [];
      GridState.visibleGroupHeaders(previousPath, path).forEach((level) => {
        const ancestorCollapsed = Array.from({length: level}).some((_item, ancestorLevel) =>
          state.collapsed.has(GridState.pathKey(path.slice(0, ancestorLevel + 1))),
        );
        if (!ancestorCollapsed) output.push(renderGroupRow(path, level));
      });
      if (!GridState.rowHiddenByCollapsedPath(path, state.collapsed)) {
        output.push(renderDataRow(row, rowIndex));
      }
      previousPath = path;
    });
    elements.gridBody.innerHTML = output.join("");
    renderResultSummary();
  }

  async function queryGrid(options) {
    const reset = Boolean(options && options.reset);
    if (state.loading && !reset) return;
    const sequence = ++state.querySequence;
    const cursor = reset ? "" : state.nextCursor;
    if (!reset && !cursor) return;
    if (reset) {
      state.rows = [];
      state.nextCursor = "";
      state.total = null;
      elements.gridScroll.scrollTop = 0;
      renderRows();
      setGridState(publicMode ? "正在读取共享数据…" : "正在读取真实会员数据…", "loading");
    }
    state.loading = true;
    elements.gridShell.setAttribute("aria-busy", "true");
    if (!reset) setGridState("正在加载更多数据…", "loading");
    try {
      const payload = await request(publicMode ? "/api/public/service-period-member-grid/query" : `${apiBase}/member-grid/query`, {
        method: "POST",
        body: publicMode
          ? {view_id: state.activeViewId, cursor, limit: 100}
          : {config: state.draftConfig, cursor, limit: 100},
      });
      if (sequence !== state.querySequence) return;
      const pageRows = Array.isArray(payload.rows) ? payload.rows : [];
      state.rows = reset ? pageRows : state.rows.concat(pageRows);
      state.nextCursor = String(payload.next_cursor || "");
      if (payload.total !== null && payload.total !== undefined) state.total = Number(payload.total);
      renderRows();
      setGridState(
        state.rows.length ? "" : "当前视图没有符合条件的会员",
        state.rows.length ? "" : "empty",
      );
    } catch (error) {
      if (sequence !== state.querySequence) return;
      const fallback = Number(error && error.status) === 410 ? "分享链接已关闭或已更新" : "会员数据加载失败";
      setGridState(errorMessage(error, fallback), "error");
      if (!reset) toast(errorMessage(error, "更多数据加载失败"), "error");
    } finally {
      if (sequence === state.querySequence) {
        state.loading = false;
        elements.gridShell.setAttribute("aria-busy", "false");
      }
    }
  }

  function scheduleQuery() {
    window.clearTimeout(state.queryTimer);
    state.queryTimer = window.setTimeout(() => queryGrid({reset: true}), 180);
  }

  function applyDraft(nextConfig) {
    state.draftConfig = GridState.normalizeConfig(nextConfig);
    renderViews();
    renderToolbar();
    scheduleQuery();
  }

  function selectViewDirect(viewId) {
    const view = state.views.find((item) => String(item.id) === String(viewId));
    if (!view) return;
    state.activeViewId = String(view.id);
    state.savedConfig = GridState.normalizeConfig(view.config);
    state.draftConfig = GridState.normalizeConfig(view.config);
    try {
      window.sessionStorage.setItem(`service-period-member-grid:${serviceProductId}:active-view`, state.activeViewId);
    } catch (_error) {
      // Session preference is optional.
    }
    loadCollapsedState();
    closePanels();
    renderViews();
    renderToolbar();
    queryGrid({reset: true});
  }

  function dialogChoice(dialog) {
    return new Promise((resolve) => {
      const onClose = () => {
        dialog.removeEventListener("close", onClose);
        resolve(dialog.returnValue || "cancel");
      };
      dialog.addEventListener("close", onClose);
      dialog.returnValue = "";
      dialog.showModal();
    });
  }

  async function guardUnsaved(action) {
    if (!isDirty()) {
      await action();
      return true;
    }
    const saveButton = elements.unsavedDialog.querySelector('[value="save"]');
    saveButton.hidden = !state.canManageViews;
    const choice = await dialogChoice(elements.unsavedDialog);
    if (choice === "cancel") return false;
    if (choice === "save") {
      const saved = await saveActiveView();
      if (!saved) return false;
    } else if (choice === "discard") {
      state.draftConfig = GridState.discardDraft(state.savedConfig);
      renderViews();
      renderToolbar();
      scheduleQuery();
    }
    await action();
    return true;
  }

  function openNameDialog(options) {
    const settings = options || {};
    elements.nameDialogTitle.textContent = settings.title || "新建视图";
    elements.nameDialogSubmit.textContent = settings.submitLabel || "确认";
    elements.nameDialogInput.value = settings.value || "";
    elements.nameDialogHint.textContent = "";
    return new Promise((resolve) => {
      const onClose = () => {
        elements.nameDialog.removeEventListener("close", onClose);
        const name = elements.nameDialogInput.value.trim();
        resolve(elements.nameDialog.returnValue === "default" && name ? name : "");
      };
      elements.nameDialog.addEventListener("close", onClose);
      elements.nameDialog.returnValue = "";
      elements.nameDialog.showModal();
      window.setTimeout(() => {
        elements.nameDialogInput.focus();
        elements.nameDialogInput.select();
      }, 0);
    });
  }

  async function createViewWithConfig(config, options) {
    const settings = options || {};
    const name = await openNameDialog({
      title: settings.title || "新建视图",
      submitLabel: settings.submitLabel || "创建",
      value: settings.value || "",
    });
    if (!name) return null;
    try {
      const payload = await request(`${apiBase}/member-views`, {
        method: "POST",
        body: {name, config},
      });
      state.views.push(payload.view);
      state.views.sort((left, right) => Number(left.position || 0) - Number(right.position || 0));
      selectViewDirect(payload.view.id);
      toast("视图已创建", "success");
      return payload.view;
    } catch (error) {
      toast(errorMessage(error, "创建视图失败"), "error");
      return null;
    }
  }

  async function saveActiveView() {
    const view = activeView();
    if (!view || !state.canManageViews || !isDirty()) return Boolean(view);
    elements.saveView.disabled = true;
    elements.draftStatus.textContent = "正在保存视图…";
    try {
      const payload = await request(`${apiBase}/member-views/${encodeURIComponent(view.id)}`, {
        method: "PUT",
        body: {name: view.name, config: state.draftConfig, version: view.version},
      });
      const index = state.views.findIndex((item) => String(item.id) === String(view.id));
      state.views[index] = payload.view;
      state.savedConfig = GridState.normalizeConfig(payload.view.config);
      state.draftConfig = GridState.normalizeConfig(payload.view.config);
      renderViews();
      renderToolbar();
      toast("视图已保存", "success");
      return true;
    } catch (error) {
      if (Number(error.status) === 409) {
        await resolveViewConflict();
      } else {
        toast(errorMessage(error, "保存视图失败"), "error");
      }
      renderToolbar();
      return false;
    }
  }

  async function refreshViews(options) {
    const activeId = state.activeViewId;
    const payload = await request(`${apiBase}/member-views`);
    state.views = Array.isArray(payload.items) ? payload.items : [];
    const selected = state.views.find((view) => String(view.id) === String(activeId)) ||
      state.views.find((view) => view.is_default) || state.views[0];
    if (selected && (!options || options.resetDraft !== false)) selectViewDirect(selected.id);
    else renderViews();
  }

  async function resolveViewConflict() {
    const choice = await dialogChoice(elements.conflictDialog);
    if (choice === "reload") {
      try {
        await refreshViews({resetDraft: true});
        toast("已加载共享视图的最新版本", "success");
      } catch (error) {
        toast(errorMessage(error, "加载最新版失败"), "error");
      }
    } else if (choice === "save-as") {
      await createViewWithConfig(state.draftConfig, {title: "另存为新视图", submitLabel: "保存"});
    }
  }

  async function renameActiveView() {
    const view = activeView();
    if (!view) return;
    const name = await openNameDialog({title: "重命名视图", submitLabel: "保存", value: view.name});
    if (!name || name === view.name) return;
    try {
      const payload = await request(`${apiBase}/member-views/${encodeURIComponent(view.id)}`, {
        method: "PUT",
        body: {name, config: state.savedConfig, version: view.version},
      });
      const index = state.views.findIndex((item) => String(item.id) === String(view.id));
      state.views[index] = payload.view;
      state.savedConfig = GridState.normalizeConfig(payload.view.config);
      renderViews();
      renderToolbar();
      toast("视图已重命名", "success");
    } catch (error) {
      if (Number(error.status) === 409) await resolveViewConflict();
      else toast(errorMessage(error, "重命名失败"), "error");
    }
  }

  async function deleteActiveView() {
    const view = activeView();
    if (!view || view.is_default) return;
    elements.confirmTitle.textContent = "删除视图";
    elements.confirmCopy.textContent = `确认删除共享视图“${view.name}”？其他管理员也将无法再使用它。`;
    const choice = await dialogChoice(elements.confirmDialog);
    if (choice !== "confirm") return;
    try {
      await request(`${apiBase}/member-views/${encodeURIComponent(view.id)}`, {
        method: "DELETE",
        body: {version: view.version},
      });
      state.views = state.views.filter((item) => String(item.id) !== String(view.id));
      const next = state.views.find((item) => item.is_default) || state.views[0];
      if (next) selectViewDirect(next.id);
      toast("视图已删除", "success");
    } catch (error) {
      if (Number(error.status) === 409) await resolveViewConflict();
      else toast(errorMessage(error, "删除视图失败"), "error");
    }
  }

  function panelPosition(panel, anchor) {
    const rect = anchor.getBoundingClientRect();
    panel.hidden = false;
    panel.style.visibility = "hidden";
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    const below = rect.bottom + 6;
    const top = below + height <= window.innerHeight - 12
      ? below
      : Math.max(12, rect.top - height - 6);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.visibility = "visible";
  }

  function closePanels() {
    elements.configPanel.hidden = true;
    elements.viewMenu.hidden = true;
    state.panelKind = "";
    [elements.filterButton, elements.groupButton, elements.sortButton, elements.viewMenuButton]
      .forEach((button) => button.setAttribute("aria-expanded", "false"));
  }

  function fieldOptions(selectedField, kind) {
    const used = new Set();
    if (kind === "sorts" || kind === "groups") {
      state.draftConfig.sorts.forEach((item) => used.add(item.field));
      state.draftConfig.groups.forEach((item) => used.add(item.field));
    }
    return state.fields.map((field) => {
      const disabled = used.has(field.id) && field.id !== selectedField;
      return `<option value="${escapeHtml(field.id)}"${field.id === selectedField ? " selected" : ""}${disabled ? " disabled" : ""}>${escapeHtml(field.label)}</option>`;
    }).join("");
  }

  function defaultCondition(field) {
    const operator = field.filter_operators[0];
    return {field: field.id, operator: operator.id, value: defaultOperatorValue(field, operator)};
  }

  function defaultOperatorValue(field, operator) {
    if (operator.value_kind === "none") return undefined;
    if (operator.value_kind === "multi_select") return [String((field.options[0] || {}).value || "")].filter(Boolean);
    if (operator.value_kind === "range") {
      if (field.type === "datetime") {
        const now = new Date().toISOString();
        return [now, now];
      }
      return [0, 0];
    }
    if (field.type === "datetime") return new Date().toISOString();
    if (field.type === "number" || field.type === "progress") return 0;
    return "";
  }

  function operatorFor(field, operatorId) {
    return (field.filter_operators || []).find((operator) => operator.id === operatorId) || field.filter_operators[0];
  }

  function localDateTimeValue(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function renderValueControl(condition, field, operator, index) {
    if (!operator || operator.value_kind === "none") return '<span class="sp-panel-copy">无需填写</span>';
    if (operator.value_kind === "multi_select") {
      const selected = new Set(Array.isArray(condition.value) ? condition.value : []);
      return `<div class="sp-multi-options">${(field.options || []).map((option) =>
        `<label class="sp-check-pill"><input type="checkbox" data-filter-value data-index="${index}" value="${escapeHtml(option.value)}"${selected.has(option.value) ? " checked" : ""}>${escapeHtml(option.label)}</label>`,
      ).join("")}</div>`;
    }
    if (operator.value_kind === "range") {
      const values = Array.isArray(condition.value) ? condition.value : defaultOperatorValue(field, operator);
      const type = field.type === "datetime" ? "datetime-local" : "number";
      const first = type === "datetime-local" ? localDateTimeValue(values[0]) : values[0];
      const second = type === "datetime-local" ? localDateTimeValue(values[1]) : values[1];
      return `<div class="sp-multi-options"><input class="sp-panel-input" type="${type}" data-filter-range="0" data-index="${index}" value="${escapeHtml(first)}">` +
        `<span>至</span><input class="sp-panel-input" type="${type}" data-filter-range="1" data-index="${index}" value="${escapeHtml(second)}"></div>`;
    }
    const inputType = field.type === "datetime" ? "datetime-local" :
      (field.type === "number" || field.type === "progress" ? "number" : "text");
    const value = inputType === "datetime-local" ? localDateTimeValue(condition.value) : (condition.value ?? "");
    const bounds = field.type === "progress" ? ' min="0" max="100" step="0.01"' : "";
    return `<input class="sp-panel-input" type="${inputType}" data-filter-scalar data-index="${index}" value="${escapeHtml(value)}"${bounds}>`;
  }

  function renderFilterPanel() {
    const conditions = state.draftConfig.filter.conditions || [];
    elements.configPanel.innerHTML = `
      <div class="sp-panel-head"><strong>筛选条件</strong><button class="sp-icon-button" type="button" data-close-panel aria-label="关闭"><img src="/static/service-period/icons/x-mark.svg" alt=""></button></div>
      <p class="sp-panel-copy">最多 20 条条件。修改后立即查询当前草稿，保存视图后才会共享给其他管理员。</p>
      <div class="sp-panel-list">${conditions.map((condition, index) => {
        const field = state.fieldMap.get(condition.field) || state.fields[0];
        const operator = operatorFor(field, condition.operator);
        return `<div class="sp-condition-row" data-condition-index="${index}">` +
          `<select class="sp-select" data-filter-field data-index="${index}" aria-label="筛选字段">${fieldOptions(field.id, "filter")}</select>` +
          `<select class="sp-select" data-filter-operator data-index="${index}" aria-label="筛选操作符">${field.filter_operators.map((item) =>
            `<option value="${escapeHtml(item.id)}"${item.id === operator.id ? " selected" : ""}>${escapeHtml(item.label)}</option>`,
          ).join("")}</select>` +
          `${renderValueControl(condition, field, operator, index)}` +
          `<button class="sp-icon-button sp-row-remove" type="button" data-remove-filter="${index}" aria-label="删除条件"><img src="/static/service-period/icons/x-mark.svg" alt=""></button></div>`;
      }).join("") || '<p class="sp-panel-copy">尚未添加筛选条件。</p>'}</div>
      <div class="sp-panel-footer">
        <button class="sp-panel-add" type="button" data-add-filter${conditions.length >= 20 ? " disabled" : ""}><img src="/static/service-period/icons/plus.svg" alt="">添加条件</button>
        <label class="sp-panel-logic">满足
          <select class="sp-select" data-filter-logic><option value="and"${state.draftConfig.filter.logic === "and" ? " selected" : ""}>全部条件</option><option value="or"${state.draftConfig.filter.logic === "or" ? " selected" : ""}>任一条件</option></select>
        </label>
      </div>`;
  }

  function renderOrderPanel(kind) {
    const isGroup = kind === "groups";
    const items = state.draftConfig[kind];
    const maximum = isGroup ? 2 : 8;
    elements.configPanel.innerHTML = `
      <div class="sp-panel-head"><strong>${isGroup ? "分组" : "排序"}</strong><button class="sp-icon-button" type="button" data-close-panel aria-label="关闭"><img src="/static/service-period/icons/x-mark.svg" alt=""></button></div>
      <p class="sp-panel-copy">${isGroup ? "最多两层分组；组折叠状态仅保存在当前浏览器会话。" : "最多八个唯一排序字段；分组字段会自动优先排序。"}</p>
      <div class="sp-panel-list">${items.map((item, index) =>
        `<div class="sp-order-row"><span class="sp-order-index">${index + 1}</span>` +
        `<select class="sp-select" data-order-field data-kind="${kind}" data-index="${index}">${fieldOptions(item.field, kind)}</select>` +
        `<select class="sp-select" data-order-direction data-kind="${kind}" data-index="${index}">` +
        `<option value="asc"${item.direction === "asc" ? " selected" : ""}>升序</option>` +
        `<option value="desc"${item.direction === "desc" ? " selected" : ""}>降序</option></select>` +
        `<button class="sp-icon-button sp-row-remove" type="button" data-remove-order data-kind="${kind}" data-index="${index}" aria-label="删除"><img src="/static/service-period/icons/x-mark.svg" alt=""></button></div>`,
      ).join("") || `<p class="sp-panel-copy">尚未添加${isGroup ? "分组" : "排序"}字段。</p>`}</div>
      <div class="sp-panel-footer"><button class="sp-panel-add" type="button" data-add-order data-kind="${kind}"${items.length >= maximum ? " disabled" : ""}><img src="/static/service-period/icons/plus.svg" alt="">添加${isGroup ? "分组" : "排序"}</button><span class="sp-panel-copy">${items.length}/${maximum}</span></div>`;
  }

  function openConfigPanel(kind, anchor) {
    closePanels();
    state.panelKind = kind;
    if (kind === "filter") renderFilterPanel();
    else renderOrderPanel(kind);
    anchor.setAttribute("aria-expanded", "true");
    panelPosition(elements.configPanel, anchor);
  }

  function rerenderOpenPanel() {
    if (!state.panelKind) return;
    const anchor = state.panelKind === "filter" ? elements.filterButton :
      (state.panelKind === "groups" ? elements.groupButton : elements.sortButton);
    if (state.panelKind === "filter") renderFilterPanel();
    else renderOrderPanel(state.panelKind);
    panelPosition(elements.configPanel, anchor);
  }

  function firstAvailableOrderField(kind) {
    const used = new Set([
      ...state.draftConfig.sorts.map((item) => item.field),
      ...state.draftConfig.groups.map((item) => item.field),
    ]);
    return state.fields.find((field) => !used.has(field.id) && (kind === "groups" ? field.groupable : field.sortable));
  }

  function updateFilterValue(target) {
    const index = Number(target.dataset.index);
    const config = GridState.normalizeConfig(state.draftConfig);
    const condition = config.filter.conditions[index];
    if (!condition) return;
    const field = state.fieldMap.get(condition.field);
    const operator = operatorFor(field, condition.operator);
    if (target.matches("[data-filter-value]")) {
      const checks = elements.configPanel.querySelectorAll(`[data-filter-value][data-index="${index}"]`);
      condition.value = Array.from(checks).filter((input) => input.checked).map((input) => input.value);
      if (!condition.value.length) {
        target.checked = true;
        condition.value = [target.value];
      }
    } else if (target.matches("[data-filter-range]")) {
      const inputs = elements.configPanel.querySelectorAll(`[data-filter-range][data-index="${index}"]`);
      condition.value = Array.from(inputs).map((input) => field.type === "datetime"
        ? new Date(input.value).toISOString()
        : Number(input.value || 0));
    } else if (target.matches("[data-filter-scalar]")) {
      condition.value = field.type === "datetime"
        ? new Date(target.value).toISOString()
        : (field.type === "number" || field.type === "progress" ? Number(target.value || 0) : target.value);
    }
    if (operator.value_kind === "none") delete condition.value;
    applyDraft(config);
  }

  function openViewMenu() {
    const view = activeView();
    if (!view || !state.canManageViews) return;
    closePanels();
    elements.viewMenu.innerHTML = `
      <button class="sp-menu-item" type="button" role="menuitem" data-view-action="rename">重命名</button>
      <button class="sp-menu-item" type="button" role="menuitem" data-view-action="duplicate">复制视图</button>
      <div class="sp-menu-divider"></div>
      <button class="sp-menu-item is-danger" type="button" role="menuitem" data-view-action="delete"${view.is_default ? " disabled" : ""}>删除视图${view.is_default ? "（默认视图不可删除）" : ""}</button>`;
    elements.viewMenuButton.setAttribute("aria-expanded", "true");
    panelPosition(elements.viewMenu, elements.viewMenuButton);
  }

  function startTextEdit(cell) {
    const fieldId = String(cell.dataset.fieldId || "");
    if (!state.editableFields.has(fieldId) || cell.querySelector("textarea")) return;
    const rowIndex = Number(cell.dataset.rowIndex);
    const row = state.rows[rowIndex];
    if (!row) return;
    const field = state.fieldMap.get(fieldId);
    if (!field || !field.editable) return;
    const label = String(field.label || fieldId);
    const original = String((row.values || {})[fieldId] || "");
    const editor = document.createElement("textarea");
    editor.className = "sp-text-cell-editor";
    editor.value = original;
    editor.maxLength = 500;
    cell.textContent = "";
    cell.appendChild(editor);
    let finished = false;
    const cancel = () => {
      if (finished) return;
      finished = true;
      renderRows();
    };
    const save = async () => {
      if (finished) return;
      finished = true;
      const next = editor.value.slice(0, 500);
      row.values[fieldId] = next;
      renderRows();
      if (next === original) return;
      try {
        await request(`${apiBase}/members/${encodeURIComponent(row.unionid)}/${encodeURIComponent(fieldId)}`, {
          method: "PUT",
          body: {[fieldId]: next},
        });
        toast(`${label}已保存`, "success");
        scheduleQuery();
      } catch (error) {
        row.values[fieldId] = original;
        renderRows();
        toast(errorMessage(error, `${label}保存失败，已恢复原值`), "error");
      }
    };
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      } else if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        save();
      }
    });
    editor.addEventListener("blur", save, {once: true});
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
  }

  function installEvents() {
    if (publicMode) {
      elements.viewTabs.addEventListener("click", (event) => {
        const button = event.target.closest("[data-view-id]");
        if (!button || String(button.dataset.viewId) === String(state.activeViewId)) return;
        selectViewDirect(button.dataset.viewId);
      });
      elements.gridBody.addEventListener("click", (event) => {
        const group = event.target.closest("[data-group-key]");
        if (!group) return;
        const key = group.dataset.groupKey;
        if (state.collapsed.has(key)) state.collapsed.delete(key);
        else state.collapsed.add(key);
        saveCollapsedState();
        renderRows();
      });
      return;
    }
    elements.viewTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-view-id]");
      if (!button || String(button.dataset.viewId) === String(state.activeViewId)) return;
      guardUnsaved(async () => selectViewDirect(button.dataset.viewId));
    });
    elements.addView.addEventListener("click", () => {
      guardUnsaved(async () => createViewWithConfig(GridState.emptyConfig(), {title: "新建视图", submitLabel: "创建"}));
    });
    elements.saveView.addEventListener("click", () => saveActiveView());
    elements.saveAsView.addEventListener("click", () => createViewWithConfig(state.draftConfig, {title: "另存为新视图", submitLabel: "保存"}));
    elements.filterButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.panelKind === "filter") closePanels();
      else openConfigPanel("filter", elements.filterButton);
    });
    elements.groupButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.panelKind === "groups") closePanels();
      else openConfigPanel("groups", elements.groupButton);
    });
    elements.sortButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.panelKind === "sorts") closePanels();
      else openConfigPanel("sorts", elements.sortButton);
    });
    elements.viewMenuButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!elements.viewMenu.hidden) closePanels();
      else openViewMenu();
    });
    elements.configPanel.addEventListener("click", (event) => {
      event.stopPropagation();
      if (event.target.closest("[data-close-panel]")) closePanels();
      const addFilter = event.target.closest("[data-add-filter]");
      if (addFilter) {
        const config = GridState.normalizeConfig(state.draftConfig);
        if (config.filter.conditions.length < 20) config.filter.conditions.push(defaultCondition(state.fields[0]));
        applyDraft(config);
        rerenderOpenPanel();
      }
      const removeFilter = event.target.closest("[data-remove-filter]");
      if (removeFilter) {
        const config = GridState.normalizeConfig(state.draftConfig);
        config.filter.conditions.splice(Number(removeFilter.dataset.removeFilter), 1);
        applyDraft(config);
        rerenderOpenPanel();
      }
      const addOrder = event.target.closest("[data-add-order]");
      if (addOrder) {
        const kind = addOrder.dataset.kind;
        const field = firstAvailableOrderField(kind);
        if (!field) return;
        try {
          applyDraft(GridState.addOrder(state.draftConfig, kind, field.id, "asc", {sorts: 8, groups: 2}));
        } catch (error) {
          toast(error.message || "无法添加字段", "error");
        }
        rerenderOpenPanel();
      }
      const removeOrder = event.target.closest("[data-remove-order]");
      if (removeOrder) {
        applyDraft(GridState.removeOrder(state.draftConfig, removeOrder.dataset.kind, Number(removeOrder.dataset.index)));
        rerenderOpenPanel();
      }
    });
    elements.configPanel.addEventListener("change", (event) => {
      const target = event.target;
      if (target.matches("[data-filter-logic]")) {
        const config = GridState.normalizeConfig(state.draftConfig);
        config.filter.logic = target.value === "or" ? "or" : "and";
        applyDraft(config);
      } else if (target.matches("[data-filter-field]")) {
        const config = GridState.normalizeConfig(state.draftConfig);
        const index = Number(target.dataset.index);
        const field = state.fieldMap.get(target.value);
        config.filter.conditions[index] = defaultCondition(field);
        applyDraft(config);
        rerenderOpenPanel();
      } else if (target.matches("[data-filter-operator]")) {
        const config = GridState.normalizeConfig(state.draftConfig);
        const index = Number(target.dataset.index);
        const condition = config.filter.conditions[index];
        const field = state.fieldMap.get(condition.field);
        const operator = operatorFor(field, target.value);
        condition.operator = operator.id;
        const value = defaultOperatorValue(field, operator);
        if (value === undefined) delete condition.value;
        else condition.value = value;
        applyDraft(config);
        rerenderOpenPanel();
      } else if (target.matches("[data-filter-value], [data-filter-range], [data-filter-scalar]")) {
        try {
          updateFilterValue(target);
        } catch (_error) {
          toast("请输入有效的筛选值", "error");
        }
      } else if (target.matches("[data-order-field]")) {
        applyDraft(GridState.updateOrder(state.draftConfig, target.dataset.kind, Number(target.dataset.index), {field: target.value}));
        rerenderOpenPanel();
      } else if (target.matches("[data-order-direction]")) {
        applyDraft(GridState.updateOrder(state.draftConfig, target.dataset.kind, Number(target.dataset.index), {direction: target.value}));
      }
    });
    elements.configPanel.addEventListener("input", (event) => {
      const target = event.target;
      if (target.matches("[data-filter-scalar], [data-filter-range]")) {
        window.clearTimeout(target.__spInputTimer);
        target.__spInputTimer = window.setTimeout(() => {
          try { updateFilterValue(target); } catch (_error) { /* wait for a valid value */ }
        }, 240);
      }
    });
    elements.viewMenu.addEventListener("click", async (event) => {
      event.stopPropagation();
      const button = event.target.closest("[data-view-action]");
      if (!button || button.disabled) return;
      const action = button.dataset.viewAction;
      closePanels();
      if (action === "rename") await renameActiveView();
      if (action === "duplicate") {
        const view = activeView();
        await createViewWithConfig(state.savedConfig, {title: "复制视图", submitLabel: "复制", value: `${view.name} 副本`});
      }
      if (action === "delete") await guardUnsaved(deleteActiveView);
    });
    elements.gridBody.addEventListener("click", (event) => {
      const group = event.target.closest("[data-group-key]");
      if (group) {
        const key = group.dataset.groupKey;
        if (state.collapsed.has(key)) state.collapsed.delete(key);
        else state.collapsed.add(key);
        saveCollapsedState();
        renderRows();
      }
    });
    elements.gridBody.addEventListener("dblclick", (event) => {
      const cell = event.target.closest(".sp-editable-text-cell");
      if (cell) startTextEdit(cell);
    });
    elements.gridBody.addEventListener("keydown", (event) => {
      const cell = event.target.closest(".sp-editable-text-cell");
      if (cell && event.key === "Enter" && !cell.querySelector("textarea")) {
        event.preventDefault();
        startTextEdit(cell);
      }
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#spConfigPanel, #spViewMenu")) closePanels();
    });
    document.addEventListener("click", (event) => {
      const link = event.target.closest("a[href]");
      if (!link || !isDirty() || link.target || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const destination = new URL(link.href, window.location.href);
      if (destination.href === window.location.href) return;
      event.preventDefault();
      guardUnsaved(async () => { window.location.href = destination.href; });
    }, true);
    window.addEventListener("beforeunload", (event) => {
      if (!isDirty()) return;
      event.preventDefault();
      event.returnValue = "";
    });
    window.addEventListener("resize", closePanels);
  }

  function installInfiniteScroll() {
    if (!("IntersectionObserver" in window)) {
      elements.gridScroll.addEventListener("scroll", () => {
        if (elements.gridScroll.scrollTop + elements.gridScroll.clientHeight >= elements.gridScroll.scrollHeight - 120) {
          queryGrid({reset: false});
        }
      });
      return;
    }
    state.observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && state.nextCursor && !state.loading) {
        queryGrid({reset: false});
      }
    }, {root: elements.gridScroll, rootMargin: "220px 0px", threshold: 0});
    state.observer.observe(elements.gridSentinel);
  }

  async function initialize() {
    if (!publicMode && !serviceProductId) {
      setGridState("周期商品不存在", "error");
      return;
    }
    installEvents();
    installInfiniteScroll();
    try {
      let schemaPayload;
      let viewsPayload;
      if (publicMode) {
        const bootstrap = await request("/api/public/service-period-member-grid/bootstrap");
        serviceProductId = String(bootstrap.service_product_id || "");
        root.dataset.serviceProductId = serviceProductId;
        schemaPayload = {schema: bootstrap.schema || {}};
        viewsPayload = {items: bootstrap.views || []};
        const title = document.getElementById("spPublicProductTitle");
        if (title) title.textContent = String((bootstrap.product || {}).title || "周期商品数据");
        document.title = `${String((bootstrap.product || {}).title || "周期商品数据")} · 只读分享`;
        state.access = {can_edit: false, can_manage_share: false};
      } else {
        const [accessPayload, internalSchemaPayload, internalViewsPayload] = await Promise.all([
          request(`${apiBase}/member-grid/access`),
          request(`${apiBase}/member-grid/schema`),
          request(`${apiBase}/member-views`),
        ]);
        state.access = accessPayload.access || {};
        schemaPayload = internalSchemaPayload;
        viewsPayload = internalViewsPayload;
        state.canManageViews = Boolean(state.access.can_manage_views);
        state.editableFields = new Set(state.access.can_edit_cells ? ["remark", "alliance"] : []);
        if (window.ServicePeriodMemberGridShare && typeof window.ServicePeriodMemberGridShare.initialize === "function") {
          window.ServicePeriodMemberGridShare.initialize(state.access, toast);
        }
      }
      state.schema = schemaPayload.schema || {};
      state.fields = Array.isArray(state.schema.fields) ? state.schema.fields : [];
      state.fieldMap = new Map(state.fields.map((field) => [field.id, field]));
      state.fields.forEach((field) => (field.filter_operators || []).forEach((operator) => operatorLabels.set(operator.id, operator.label)));
      state.views = Array.isArray(viewsPayload.items) ? viewsPayload.items : [];
      renderHeader();
      let preferredId = "";
      try {
        preferredId = window.sessionStorage.getItem(`service-period-member-grid:${serviceProductId}:active-view`) || "";
      } catch (_error) {
        preferredId = "";
      }
      const preferred = state.views.find((view) => String(view.id) === String(preferredId));
      const selected = preferred || state.views.find((view) => view.is_default) || state.views[0];
      if (!selected) throw new Error("该商品尚未初始化默认视图");
      selectViewDirect(selected.id);
    } catch (error) {
      elements.gridShell.setAttribute("aria-busy", "false");
      const status = Number(error && error.status);
      const fallback = publicMode && status === 410
        ? "分享链接已关闭或已更新"
        : publicMode && status === 401
          ? "分享链接无效"
          : "数据工作区初始化失败";
      setGridState(errorMessage(error, fallback), "error");
      elements.resultSummary.textContent = publicMode ? "无法访问共享数据" : "加载失败";
    }
  }

  initialize();
})(window, document);
