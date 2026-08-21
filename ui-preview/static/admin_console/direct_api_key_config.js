(function (global) {
  "use strict";

  const ERROR_MESSAGES = {
    direct_api_key_already_configured: "API Key 已存在，请使用重新生成功能。",
    direct_api_key_definition_conflict: "现有 API Key 配置与系统契约冲突，请联系管理员处理。",
    direct_api_key_not_configured: "尚未配置 API Key。",
    manage_config_required: "只有配置管理员或超级管理员可以执行此操作。",
    operation_confirmation_required: "请确认本次操作。",
    unknown_fields: "提交中包含不受支持的字段。",
  };

  const ACTIONS = {
    generate: {
      url: "/api/admin/config/api-key/generate",
      method: "POST",
      title: "生成 API Key",
      copy: "生成后会立即启用。完整 Key 只显示一次，关闭后页面仅保留可核对的脱敏标识。",
      confirm: "确认生成",
      busy: "生成中…",
      secretTitle: "API Key 已创建",
      toast: "API Key 已创建并启用",
    },
    rotate: {
      url: "/api/admin/config/api-key/rotate",
      method: "POST",
      title: "重新生成 API Key",
      copy: "重新生成后，当前 Key 会立即失效。新 Key 只显示一次，请确认已有安全的保存位置。",
      confirm: "确认重新生成",
      busy: "重新生成中…",
      secretTitle: "API Key 已重新生成",
      toast: "新 API Key 已启用",
    },
    disable: {
      url: "/api/admin/config/api-key/enabled",
      method: "PUT",
      title: "停用当前 API Key",
      copy: "停用后，所有使用当前 Key 的开放接口请求都会立即失败。需要恢复时必须重新生成。",
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

  function setAlert(root, message) {
    const node = root.querySelector("[data-api-key-alert]");
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
    if (!value) return Promise.reject(new Error("API Key 不可用"));
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(value);
    input.select();
    return document.execCommand("copy") ? Promise.resolve() : Promise.reject(new Error("复制失败"));
  }

  function formatTime(value) {
    if (!value) return "暂无记录";
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
    const modal = root.querySelector("[data-api-key-modal]");
    const confirmView = root.querySelector("[data-api-key-confirm-view]");
    const secretView = root.querySelector("[data-api-key-secret-view]");
    const confirmButton = root.querySelector("[data-modal-confirm]");
    const copyButton = root.querySelector("[data-copy-api-key]");
    const secretInput = root.querySelector("[data-api-key-value]");
    const toastNode = root.querySelector("[data-api-key-toast]");
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

    function closeModal() {
      if (secretInput) secretInput.value = "";
      setModalOpen(false);
      pendingAction = null;
    }

    function openConfirmation(actionName, trigger) {
      const action = ACTIONS[actionName];
      if (!action || !modal) return;
      pendingAction = { name: actionName, ...action };
      returnFocus = trigger || null;
      confirmView.hidden = false;
      secretView.hidden = true;
      root.querySelector("[data-confirm-title]").textContent = action.title;
      root.querySelector("[data-confirm-copy]").textContent = action.copy;
      confirmButton.textContent = action.confirm;
      confirmButton.dataset.originalText = action.confirm;
      confirmButton.classList.toggle("admin-button--danger", Boolean(action.destructive));
      confirmButton.classList.toggle("admin-button--primary", !action.destructive);
      setModalOpen(true);
      confirmButton.focus();
    }

    function updateStatus(status) {
      if (!status) return;
      root.dataset.configured = String(Boolean(status.configured));
      root.dataset.enabled = String(Boolean(status.enabled));
      const current = root.querySelector("[data-api-key-current]");
      const empty = root.querySelector("[data-api-key-empty]");
      if (current) current.hidden = !status.configured;
      if (empty) empty.hidden = Boolean(status.configured);
      const label = root.querySelector("[data-status-label]");
      if (label) label.textContent = status.enabled ? "正在使用" : "已停用";
      const badge = root.querySelector("[data-status-badge]");
      if (badge) badge.classList.toggle("is-enabled", Boolean(status.enabled));
      const version = root.querySelector("[data-auth-version]");
      if (version) version.textContent = String(status.auth_version || 0);
      const hint = root.querySelector("[data-credential-hint]");
      if (hint) hint.textContent = String(status.credential_hint || "aics_••••••••••••••••••");
      const hintNote = root.querySelector("[data-hint-note]");
      if (hintNote) hintNote.hidden = Boolean(status.credential_hint_available);
      const time = root.querySelector("[data-key-time]");
      if (time) time.textContent = formatTime(status.last_rotated_at || status.created_at);
      const rotate = root.querySelector("[data-rotate-api-key]");
      if (rotate) rotate.hidden = !status.configured;
      const disable = root.querySelector("[data-disable-api-key]");
      if (disable) disable.hidden = !status.enabled;
    }

    function showCredential(payload, action) {
      updateStatus(payload.api_key_status);
      secretInput.value = String(payload.api_key || "");
      confirmView.hidden = true;
      secretView.hidden = false;
      root.querySelector("[data-secret-title]").textContent = action.secretTitle;
      toast(action.toast);
      secretInput.scrollLeft = 0;
      copyButton?.focus?.();
    }

    async function executeAction() {
      const action = pendingAction;
      if (!action) return;
      setBusy(confirmButton, true, action.busy);
      setAlert(root, "");
      try {
        const body = action.name === "disable" ? { enabled: false, confirm: true } : { confirm: true };
        const payload = await global.AdminApi.requestJson(action.url, { method: action.method, body });
        if (action.name === "disable") {
          updateStatus(payload.api_key_status);
          closeModal();
          toast("当前 API Key 已停用");
        } else {
          showCredential(payload, action);
        }
      } catch (error) {
        setAlert(root, errorMessage(error, "API Key 操作失败"));
        setBusy(confirmButton, false, "");
      }
    }

    root.querySelector("[data-generate-api-key]")?.addEventListener("click", (event) => {
      openConfirmation("generate", event.currentTarget);
    });
    root.querySelector("[data-rotate-api-key]")?.addEventListener("click", (event) => {
      openConfirmation("rotate", event.currentTarget);
    });
    root.querySelector("[data-disable-api-key]")?.addEventListener("click", (event) => {
      openConfirmation("disable", event.currentTarget);
    });
    root.querySelectorAll("[data-modal-cancel]").forEach((button) => {
      button.addEventListener("click", closeModal);
    });
    root.querySelectorAll("[data-secret-close]").forEach((button) => {
      button.addEventListener("click", closeModal);
    });
    confirmButton?.addEventListener("click", executeAction);

    copyButton?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        await copyText(secretInput);
        button.textContent = "已复制";
        toast("API Key 已复制");
      } catch (error) {
        setAlert(root, errorMessage(error, "复制失败，请手动复制"));
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && modal && !modal.hidden) closeModal();
    });
  }

  global.DirectApiKeyConfig = { copyText, formatTime, init };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-direct-api-key-page]");
    if (root) init(root);
  });
})(window);
