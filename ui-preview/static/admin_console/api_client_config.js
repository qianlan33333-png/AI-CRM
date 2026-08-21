(function (global) {
  "use strict";

  const ERROR_MESSAGES = {
    active_client_update_requires_disable: "客户端正在启用中，请先停用后再修改。",
    activation_requires_secret_self_check: "启用前必须使用 Client Secret 完成自检。",
    client_id_already_exists: "Client ID 已存在，请更换后重试。",
    client_secret_self_check_failed: "Client Secret 自检失败，请检查后重试。",
    invalid_allowed_cidr: "CIDR 白名单格式不正确。",
    invalid_client_id: "Client ID 格式不正确。",
    invalid_token_ttl: "Token 有效期只支持 15、30 或 60 分钟。",
    manage_api_clients_required: "只有超级管理员可以执行此操作。",
    secret_copy_confirmation_required: "请先确认已经复制并保存 Secret。",
    system_managed_client_readonly: "系统预置客户端只允许查看。",
    unknown_fields: "提交中包含不受支持的字段。",
  };

  const ACTIONS = {
    rotate: {
      title: "轮换 Client Secret",
      copy: "轮换后客户端会立即停用，旧 Secret 和已签发的 Access Token 都会失效。新 Secret 只显示一次。",
      confirm: "确认轮换",
      busy: "轮换中…",
      destructive: false,
    },
    disable: {
      title: "停用当前客户端",
      copy: "停用后，当前客户端的所有 Access Token 都会立即失效。需要恢复时必须使用已保存的 Secret 完成自检。",
      confirm: "确认停用",
      busy: "停用中…",
      destructive: true,
    },
  };

  function errorMessage(error, fallback) {
    const code = String(error && error.payload && error.payload.error || "");
    const prefix = code.split(":", 1)[0];
    return ERROR_MESSAGES[code] || ERROR_MESSAGES[prefix] || global.AdminApi.errorMessage(error, fallback);
  }

  function splitCidrs(value) {
    return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
  }

  function selectedTemplate(select) {
    const option = select && select.options[select.selectedIndex];
    return option ? option.dataset : {};
  }

  function updateTemplateValues(root) {
    const select = root.querySelector("[data-client-type]");
    if (!select) return;
    const values = selectedTemplate(select);
    ["baseUrl", "tokenUrl", "resourceUrl", "audience", "scopes", "capabilities"].forEach((key) => {
      const field = root.querySelector(`[data-value="${key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)}"]`);
      if (field) field.textContent = values[key] || "-";
    });
  }

  function setAlert(root, message) {
    const node = root.querySelector("[data-api-client-alert]");
    if (!node) return;
    node.textContent = message || "";
    node.hidden = !message;
  }

  function setBusy(button, busy, busyText) {
    if (!button) return;
    if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
    button.disabled = Boolean(busy);
    button.textContent = busy ? busyText : button.dataset.originalText;
  }

  function copyText(input) {
    const value = String(input && input.value || "");
    if (!value) return Promise.reject(new Error("Secret 不可用"));
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(value);
    input.select();
    return document.execCommand("copy") ? Promise.resolve() : Promise.reject(new Error("复制失败"));
  }

  function formatTime(value) {
    if (!value) return "尚未轮换";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed).replaceAll("/", "-");
  }

  function init(root) {
    const mode = root.dataset.mode;
    const canManage = root.dataset.canManage === "true";
    const state = {
      clientId: root.dataset.clientId || "",
      secret: "",
      reloadOnClose: false,
    };
    const form = root.querySelector("[data-api-client-form]");
    const typeSelect = root.querySelector("[data-client-type]");
    const modal = root.querySelector("[data-client-secret-modal]");
    const confirmView = root.querySelector("[data-client-confirm-view]");
    const secretView = root.querySelector("[data-client-secret-view]");
    const confirmButton = root.querySelector("[data-client-modal-confirm]");
    const secretInput = root.querySelector("[data-secret-value]");
    const copyButton = root.querySelector("[data-copy-secret]");
    const copiedCheck = root.querySelector("[data-secret-copied]");
    const activateSecretButton = root.querySelector("[data-activate-secret]");
    const toastNode = root.querySelector("[data-client-secret-toast]");
    let pendingAction = null;
    let returnFocus = null;
    let toastTimer = null;

    function toast(message) {
      if (!toastNode) return;
      global.clearTimeout(toastTimer);
      toastNode.textContent = message;
      toastNode.classList.add("is-visible");
      toastTimer = global.setTimeout(() => toastNode.classList.remove("is-visible"), 2600);
    }

    function setModalOpen(open) {
      if (!modal) return;
      modal.hidden = !open;
      if (document.body) document.body.classList.toggle("has-api-key-modal", open);
      if (!open && returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
    }

    function clearSecret() {
      state.secret = "";
      if (secretInput) secretInput.value = "";
      if (copiedCheck) copiedCheck.checked = false;
      if (activateSecretButton) activateSecretButton.disabled = true;
    }

    function closeModal(options) {
      const shouldReload = state.reloadOnClose && (!options || options.reload !== false);
      clearSecret();
      state.reloadOnClose = false;
      pendingAction = null;
      setModalOpen(false);
      if (shouldReload && state.clientId) {
        global.location.assign(`/admin/config/api-clients/${encodeURIComponent(state.clientId)}`);
      }
    }

    function updateClientStatus(client) {
      if (!client) return;
      root.dataset.enabled = String(Boolean(client.enabled));
      const label = root.querySelector("[data-client-status-label]");
      if (label) label.textContent = client.enabled ? "正在使用" : "已停用";
      const badge = root.querySelector("[data-client-status-badge]");
      if (badge) badge.classList.toggle("is-enabled", Boolean(client.enabled));
      const hint = root.querySelector("[data-client-credential-hint]");
      if (hint) hint.textContent = String(client.credential_hint || "aics_••••••••••••••••••");
      const note = root.querySelector("[data-client-hint-note]");
      if (note) note.hidden = Boolean(client.credential_hint_available);
      const version = root.querySelector("[data-client-auth-version]");
      if (version) version.textContent = String(client.auth_version || 1);
      const rotatedAt = root.querySelector("[data-client-rotated-at]");
      if (rotatedAt) rotatedAt.textContent = formatTime(client.last_rotated_at || client.created_at);
      const disable = root.querySelector("[data-disable-client]");
      if (disable) disable.hidden = !client.enabled;
      const reactivate = root.querySelector("[data-reactivate-panel]");
      if (reactivate) reactivate.hidden = Boolean(client.enabled);
    }

    function openConfirmation(actionName, trigger) {
      const action = ACTIONS[actionName];
      if (!action || !modal) return;
      pendingAction = { name: actionName, ...action };
      returnFocus = trigger || null;
      confirmView.hidden = false;
      secretView.hidden = true;
      root.querySelector("[data-client-confirm-title]").textContent = action.title;
      root.querySelector("[data-client-confirm-copy]").textContent = action.copy;
      confirmButton.textContent = action.confirm;
      confirmButton.dataset.originalText = action.confirm;
      confirmButton.classList.toggle("admin-button--danger", Boolean(action.destructive));
      confirmButton.classList.toggle("admin-button--primary", !action.destructive);
      setModalOpen(true);
      confirmButton.focus();
    }

    function showCredential(payload, secretTitle) {
      state.clientId = payload.client.client_id;
      state.secret = String(payload.client_secret || "");
      state.reloadOnClose = true;
      updateClientStatus(payload.client);
      secretInput.value = state.secret;
      secretInput.scrollLeft = 0;
      copiedCheck.checked = false;
      activateSecretButton.disabled = true;
      if (copyButton) {
        copyButton.textContent = "复制 Secret";
        copyButton.dataset.originalText = "复制 Secret";
      }
      confirmView.hidden = true;
      secretView.hidden = false;
      root.querySelector("[data-client-secret-title]").textContent = secretTitle;
      setModalOpen(true);
      copyButton?.focus?.();
    }

    async function activate(secret, copiedConfirmed, button) {
      if (!secret || !copiedConfirmed) {
        setAlert(root, "请输入 Secret，并确认已经安全保存。");
        return;
      }
      setBusy(button, true, "自检中…");
      setAlert(root, "");
      try {
        const payload = await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/activate`, {
          method: "POST",
          body: { client_secret: secret, copied_confirmed: true, confirm: true },
        });
        updateClientStatus(payload.client);
        state.reloadOnClose = false;
        clearSecret();
        setModalOpen(false);
        toast("Client Secret 自检通过，客户端已启用");
        if (mode === "create") {
          global.location.assign(`/admin/config/api-clients/${encodeURIComponent(state.clientId)}`);
        }
      } catch (error) {
        setAlert(root, errorMessage(error, "自检启用失败"));
      } finally {
        setBusy(button, false, "");
      }
    }

    async function executeAction() {
      const action = pendingAction;
      if (!action) return;
      setBusy(confirmButton, true, action.busy);
      setAlert(root, "");
      try {
        if (action.name === "rotate") {
          const payload = await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/rotate-secret`, {
            method: "POST",
            body: { confirm: true },
          });
          showCredential(payload, "Client Secret 已轮换");
          toast("新 Client Secret 已生成，客户端等待自检启用");
        } else {
          const payload = await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/enabled`, {
            method: "PUT",
            body: { enabled: false, confirm: true },
          });
          updateClientStatus(payload.client);
          closeModal({ reload: false });
          toast("客户端已停用");
        }
      } catch (error) {
        setAlert(root, errorMessage(error, action.name === "rotate" ? "轮换失败" : "停用失败"));
      } finally {
        setBusy(confirmButton, false, "");
      }
    }

    if (typeSelect) {
      typeSelect.addEventListener("change", () => updateTemplateValues(root));
      updateTemplateValues(root);
    }

    if (form && canManage) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        const submit = form.querySelector('button[type="submit"]');
        const data = new FormData(form);
        const body = {
          display_name: data.get("display_name"),
          token_ttl_minutes: Number(data.get("token_ttl_minutes")),
          allowed_cidrs: splitCidrs(data.get("allowed_cidrs")),
          confirm: data.get("confirm") === "1",
        };
        const url = mode === "create"
          ? "/api/admin/config/api-clients"
          : `/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}`;
        if (mode === "create") {
          body.client_id = data.get("client_id");
          body.client_type = data.get("client_type");
        }
        setBusy(submit, true, mode === "create" ? "创建中…" : "保存中…");
        setAlert(root, "");
        try {
          const payload = await global.AdminApi.requestJson(url, { method: mode === "create" ? "POST" : "PUT", body });
          if (mode === "create") {
            returnFocus = submit;
            showCredential(payload, "Client Secret 已创建");
            Array.from(form.elements).forEach((element) => { element.disabled = true; });
            submit.textContent = "已创建";
          } else {
            global.location.reload();
          }
        } catch (error) {
          setAlert(root, errorMessage(error, mode === "create" ? "创建客户端失败" : "保存配置失败"));
        } finally {
          if (!(mode === "create" && state.secret)) setBusy(submit, false, "");
        }
      });
    }

    root.querySelector("[data-rotate-secret]")?.addEventListener("click", (event) => {
      openConfirmation("rotate", event.currentTarget);
    });
    root.querySelector("[data-disable-client]")?.addEventListener("click", (event) => {
      openConfirmation("disable", event.currentTarget);
    });
    root.querySelectorAll("[data-client-modal-cancel]").forEach((button) => {
      button.addEventListener("click", () => closeModal({ reload: false }));
    });
    root.querySelectorAll("[data-client-secret-close]").forEach((button) => {
      button.addEventListener("click", () => closeModal());
    });
    confirmButton?.addEventListener("click", executeAction);

    copyButton?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        await copyText(secretInput);
        button.textContent = "已复制";
        toast("Client Secret 已复制");
      } catch (error) {
        setAlert(root, errorMessage(error, "复制失败，请手动复制"));
      }
    });

    copiedCheck?.addEventListener("change", () => {
      activateSecretButton.disabled = !copiedCheck.checked;
    });
    activateSecretButton?.addEventListener("click", () => {
      activate(state.secret, Boolean(copiedCheck && copiedCheck.checked), activateSecretButton);
    });

    root.querySelector("[data-activate-existing]")?.addEventListener("click", async (event) => {
      const input = root.querySelector("[data-existing-secret]");
      const confirmation = root.querySelector("[data-existing-secret-confirm]");
      const secret = String(input && input.value || "");
      try {
        await activate(secret, Boolean(confirmation && confirmation.checked), event.currentTarget);
      } finally {
        if (input) input.value = "";
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && modal && !modal.hidden) {
        if (secretView && !secretView.hidden) closeModal();
        else closeModal({ reload: false });
      }
    });
  }

  global.ApiClientConfig = { splitCidrs, selectedTemplate, formatTime, init };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-api-client-page]");
    if (root) init(root);
  });
})(window);
