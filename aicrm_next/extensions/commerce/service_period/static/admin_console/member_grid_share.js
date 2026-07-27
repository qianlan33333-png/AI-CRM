(function (window, document) {
  "use strict";

  const AdminApi = window.AdminApi;
  const root = document.getElementById("spMemberGrid");
  if (!root || !AdminApi || typeof AdminApi.requestJson !== "function") return;

  const serviceProductId = String(root.dataset.serviceProductId || "");
  const apiBase = `/api/admin/service-period-products/${encodeURIComponent(serviceProductId)}/member-grid`;
  const elements = {
    button: document.getElementById("spShareButton"),
    dialog: document.getElementById("spShareDialog"),
    close: document.getElementById("spShareDialogClose"),
    invite: document.getElementById("spInviteCollaborator"),
    collaborators: document.getElementById("spCollaboratorList"),
    toggle: document.getElementById("spExternalShareToggle"),
    linkRow: document.getElementById("spExternalLinkRow"),
    link: document.getElementById("spExternalShareUrl"),
    copy: document.getElementById("spCopyExternalShareUrl"),
    status: document.getElementById("spExternalShareStatus"),
  };
  if (!elements.button || !elements.dialog) return;

  const state = {
    collaborators: [],
    externalShare: null,
    loading: false,
    canManage: false,
    toast: null,
  };

  function escapeHtml(value) {
    if (typeof AdminApi.escapeHtml === "function") return AdminApi.escapeHtml(String(value ?? ""));
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  }

  function notify(message, tone) {
    if (typeof state.toast === "function") state.toast(message, tone);
  }

  function errorMessage(error, fallback) {
    const detail = error && error.payload && error.payload.detail;
    return String((typeof detail === "string" && detail) || (error && error.message) || fallback || "操作失败");
  }

  function request(path, options) {
    return AdminApi.requestJson(path, options || {});
  }

  function avatar(member) {
    const avatarUrl = String(member.avatar_url || "").trim();
    if (avatarUrl) return `<img class="sp-collaborator-avatar" src="${escapeHtml(avatarUrl)}" alt="">`;
    const initial = String(member.display_name || member.wecom_userid || "协").trim().slice(0, 1);
    return `<span class="sp-collaborator-avatar is-fallback" aria-hidden="true">${escapeHtml(initial)}</span>`;
  }

  function renderCollaborators() {
    if (state.loading) {
      elements.collaborators.innerHTML = '<div class="sp-share-loading">正在读取协作者…</div>';
      return;
    }
    if (!state.collaborators.length) {
      elements.collaborators.innerHTML = '<div class="sp-share-empty">尚未邀请协作者</div>';
      return;
    }
    elements.collaborators.innerHTML = state.collaborators.map((member) => {
      const implicit = Boolean(member.implicit);
      const permission = String(member.permission || "read");
      return `
        <div class="sp-collaborator-row" data-collaborator-id="${escapeHtml(member.id)}">
          <div class="sp-collaborator-identity">
            ${avatar(member)}
            <div>
              <strong>${escapeHtml(member.display_name || member.wecom_userid)}</strong>
              <span>${escapeHtml(implicit ? `${member.wecom_userid || ""} · 超级管理员` : member.wecom_userid || "")}</span>
            </div>
          </div>
          <div class="sp-collaborator-actions">
            <select data-collaborator-permission ${implicit ? "disabled" : ""} aria-label="协作者权限">
              <option value="read" ${permission === "read" ? "selected" : ""}>可查看</option>
              <option value="edit" ${permission === "edit" ? "selected" : ""}>可编辑</option>
            </select>
            ${implicit ? '<span class="sp-collaborator-locked">始终拥有</span>' : '<button type="button" data-remove-collaborator>移除</button>'}
          </div>
        </div>`;
    }).join("");
  }

  function renderExternalShare() {
    const share = state.externalShare || {enabled: false, version: 1, url: ""};
    elements.toggle.checked = Boolean(share.enabled);
    elements.toggle.disabled = state.loading;
    elements.linkRow.hidden = !share.enabled;
    elements.link.value = String(share.url || "");
    elements.status.textContent = share.enabled
      ? "链接已开启。关闭后立即失效，再次开启会生成新链接。"
      : "外部分享未开启，当前没有可访问的公开链接。";
  }

  async function load() {
    state.loading = true;
    renderCollaborators();
    renderExternalShare();
    try {
      const payload = await request(`${apiBase}/share-settings`);
      state.collaborators = Array.isArray(payload.collaborators) ? payload.collaborators : [];
      state.externalShare = payload.external_share || {enabled: false, version: 1, url: ""};
    } catch (error) {
      notify(errorMessage(error, "分享设置加载失败"), "error");
      if (Number(error && error.status) === 403) elements.dialog.close();
    } finally {
      state.loading = false;
      renderCollaborators();
      renderExternalShare();
    }
  }

  async function open() {
    if (!state.canManage) return;
    elements.dialog.showModal();
    const surface = elements.dialog.querySelector(".sp-share-dialog__surface");
    if (surface) surface.focus({preventScroll: true});
    await load();
  }

  async function invite() {
    if (!window.OperationMemberPicker || typeof window.OperationMemberPicker.open !== "function") {
      notify("企微员工选择器暂不可用", "error");
      return;
    }
    const disabledUserIds = state.collaborators.map((item) => String(item.wecom_userid || "")).filter(Boolean);
    window.OperationMemberPicker.open({
      title: "邀请企微员工协作",
      scope: "wecom_directory",
      pageSize: 100,
      includeInactive: false,
      allowRefresh: false,
      disabledUserIds,
      onSelect: async (member) => {
        try {
          await request(`${apiBase}/collaborators`, {
            method: "POST",
            body: {wecom_userid: member.user_id, permission: "read"},
          });
          notify("已邀请为可查看协作者", "success");
          await load();
        } catch (error) {
          notify(errorMessage(error, "邀请协作者失败"), "error");
          if (Number(error && error.status) === 409) await load();
        }
      },
    });
  }

  async function updatePermission(row, permission) {
    const id = String(row.dataset.collaboratorId || "");
    const collaborator = state.collaborators.find((item) => String(item.id) === id);
    if (!collaborator || collaborator.implicit) return;
    try {
      await request(`${apiBase}/collaborators/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: {permission, version: collaborator.version},
      });
      notify(permission === "edit" ? "已改为可编辑" : "已改为可查看", "success");
      await load();
    } catch (error) {
      notify(errorMessage(error, "权限更新失败"), "error");
      await load();
    }
  }

  async function removeCollaborator(row) {
    const id = String(row.dataset.collaboratorId || "");
    const collaborator = state.collaborators.find((item) => String(item.id) === id);
    if (!collaborator || collaborator.implicit) return;
    try {
      await request(`${apiBase}/collaborators/${encodeURIComponent(id)}`, {
        method: "DELETE",
        body: {version: collaborator.version},
      });
      notify("已移除协作者", "success");
      await load();
    } catch (error) {
      notify(errorMessage(error, "移除协作者失败"), "error");
      await load();
    }
  }

  async function toggleExternalShare() {
    if (!state.externalShare || state.loading) return;
    const enabled = Boolean(elements.toggle.checked);
    state.loading = true;
    renderExternalShare();
    try {
      const payload = await request(`${apiBase}/external-share`, {
        method: "PUT",
        body: {enabled, version: state.externalShare.version},
      });
      state.externalShare = payload.external_share;
      notify(enabled ? "外部只读链接已开启" : "外部分享已关闭，旧链接立即失效", "success");
    } catch (error) {
      notify(errorMessage(error, "外部分享更新失败"), "error");
      await load();
    } finally {
      state.loading = false;
      renderExternalShare();
    }
  }

  async function copyLink() {
    const value = String(elements.link.value || "");
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch (_error) {
      elements.link.focus();
      elements.link.select();
      document.execCommand("copy");
    }
    notify("外部分享链接已复制", "success");
  }

  elements.button.addEventListener("click", open);
  elements.close.addEventListener("click", () => elements.dialog.close());
  elements.invite.addEventListener("click", invite);
  elements.toggle.addEventListener("change", toggleExternalShare);
  elements.copy.addEventListener("click", copyLink);
  elements.dialog.addEventListener("click", (event) => {
    if (event.target === elements.dialog) elements.dialog.close();
  });
  elements.collaborators.addEventListener("change", (event) => {
    const select = event.target.closest("[data-collaborator-permission]");
    const row = select && select.closest("[data-collaborator-id]");
    if (row) updatePermission(row, select.value);
  });
  elements.collaborators.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-collaborator]");
    const row = button && button.closest("[data-collaborator-id]");
    if (row) removeCollaborator(row);
  });

  window.ServicePeriodMemberGridShare = {
    initialize(access, toast) {
      state.canManage = Boolean(access && access.can_manage_share);
      state.toast = toast;
      elements.button.hidden = !state.canManage;
    },
  };
})(window, document);
