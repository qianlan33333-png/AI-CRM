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
    input.type = "text";
    input.select();
    const copied = document.execCommand("copy");
    input.type = "password";
    return copied ? Promise.resolve() : Promise.reject(new Error("复制失败"));
  }

  function init(root) {
    const secretPanel = root.querySelector("[data-api-key-secret-panel]");
    const secretInput = root.querySelector("[data-api-key-value]");

    function showCredential(payload) {
      secretInput.value = String(payload.api_key || "");
      secretPanel.hidden = false;
      root.querySelector("[data-status-label]").textContent = payload.api_key_status.status_label;
      root.querySelector("[data-auth-version]").textContent = String(payload.api_key_status.auth_version);
      secretPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    async function issue(url, button, busyText) {
      setBusy(button, true, busyText);
      setAlert(root, "");
      try {
        const payload = await global.AdminApi.requestJson(url, { method: "POST", body: { confirm: true } });
        showCredential(payload);
        button.disabled = true;
      } catch (error) {
        setAlert(root, errorMessage(error, "API Key 操作失败"));
        setBusy(button, false, "");
      }
    }

    root.querySelector("[data-generate-api-key]")?.addEventListener("click", (event) => {
      if (!global.confirm("确认生成并启用唯一的 CRM 开放 API Key？Key 只显示一次。")) return;
      issue("/api/admin/config/api-key/generate", event.currentTarget, "生成中…");
    });

    root.querySelector("[data-rotate-api-key]")?.addEventListener("click", (event) => {
      if (!global.confirm("确认重新生成 API Key？当前 Key 会立即失效，新 Key 只显示一次。")) return;
      issue("/api/admin/config/api-key/rotate", event.currentTarget, "重新生成中…");
    });

    root.querySelector("[data-disable-api-key]")?.addEventListener("click", async (event) => {
      if (!global.confirm("确认停用当前 API Key？停用后所有使用该 Key 的请求都会立即失败。")) return;
      setBusy(event.currentTarget, true, "停用中…");
      setAlert(root, "");
      try {
        await global.AdminApi.requestJson("/api/admin/config/api-key/enabled", {
          method: "PUT",
          body: { enabled: false, confirm: true },
        });
        global.location.reload();
      } catch (error) {
        setAlert(root, errorMessage(error, "停用 API Key 失败"));
        setBusy(event.currentTarget, false, "");
      }
    });

    root.querySelector("[data-copy-api-key]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      try {
        await copyText(secretInput);
        button.textContent = "已复制";
      } catch (error) {
        setAlert(root, errorMessage(error, "复制失败，请手动复制"));
      }
    });

    root.querySelector("[data-toggle-api-key]")?.addEventListener("click", (event) => {
      const visible = secretInput.type === "text";
      secretInput.type = visible ? "password" : "text";
      event.currentTarget.textContent = visible ? "显示 Key" : "隐藏 Key";
    });
  }

  global.DirectApiKeyConfig = { copyText, init };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-direct-api-key-page]");
    if (root) init(root);
  });
})(window);
