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
    input.type = "text";
    input.select();
    const copied = document.execCommand("copy");
    input.type = "password";
    return copied ? Promise.resolve() : Promise.reject(new Error("复制失败"));
  }

  function init(root) {
    const mode = root.dataset.mode;
    const canManage = root.dataset.canManage === "true";
    const state = { clientId: root.dataset.clientId || "", secret: "" };
    const form = root.querySelector("[data-api-client-form]");
    const typeSelect = root.querySelector("[data-client-type]");
    const secretPanel = root.querySelector("[data-secret-panel]");
    const secretInput = root.querySelector("[data-secret-value]");
    const copiedCheck = root.querySelector("[data-secret-copied]");
    const activateSecretButton = root.querySelector("[data-activate-secret]");

    function showCredential(clientId, secret) {
      state.clientId = clientId;
      state.secret = secret;
      secretInput.value = secret;
      secretPanel.hidden = false;
      copiedCheck.checked = false;
      activateSecretButton.disabled = true;
      secretPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    async function activate(secret, copiedConfirmed, button) {
      if (!secret || !copiedConfirmed) {
        setAlert(root, "请输入 Secret，并确认已经安全保存。 ");
        return;
      }
      setBusy(button, true, "自检中…");
      setAlert(root, "");
      try {
        await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/activate`, {
          method: "POST",
          body: { client_secret: secret, copied_confirmed: true, confirm: true },
        });
        state.secret = "";
        if (secretInput) secretInput.value = "";
        global.location.assign(`/admin/config/api-clients/${encodeURIComponent(state.clientId)}`);
      } catch (error) {
        setAlert(root, errorMessage(error, "自检启用失败"));
      } finally {
        setBusy(button, false, "");
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
            showCredential(payload.client.client_id, payload.client_secret);
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

    root.querySelector("[data-copy-secret]")?.addEventListener("click", async (event) => {
      try {
        await copyText(secretInput);
        event.currentTarget.textContent = "已复制";
      } catch (error) {
        setAlert(root, errorMessage(error, "复制失败，请手动复制"));
      }
    });

    root.querySelector("[data-toggle-secret]")?.addEventListener("click", (event) => {
      const visible = secretInput.type === "text";
      secretInput.type = visible ? "password" : "text";
      event.currentTarget.textContent = visible ? "显示 Secret" : "隐藏 Secret";
    });

    copiedCheck?.addEventListener("change", () => {
      activateSecretButton.disabled = !copiedCheck.checked;
    });

    activateSecretButton?.addEventListener("click", () => activate(state.secret, copiedCheck.checked, activateSecretButton));

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

    root.querySelector("[data-disable-client]")?.addEventListener("click", async (event) => {
      if (!global.confirm("确认停用此客户端？已有 Access Token 将立即失效。")) return;
      setBusy(event.currentTarget, true, "停用中…");
      setAlert(root, "");
      try {
        await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/enabled`, {
          method: "PUT",
          body: { enabled: false, confirm: true },
        });
        global.location.reload();
      } catch (error) {
        setAlert(root, errorMessage(error, "停用失败"));
        setBusy(event.currentTarget, false, "");
      }
    });

    root.querySelector("[data-rotate-secret]")?.addEventListener("click", async (event) => {
      if (!global.confirm("确认轮换 Secret？客户端会立即停用，旧 Secret 和已有 Access Token 都会失效。")) return;
      setBusy(event.currentTarget, true, "轮换中…");
      setAlert(root, "");
      try {
        const payload = await global.AdminApi.requestJson(`/api/admin/config/api-clients/${encodeURIComponent(state.clientId)}/rotate-secret`, {
          method: "POST",
          body: { confirm: true },
        });
        showCredential(payload.client.client_id, payload.client_secret);
      } catch (error) {
        setAlert(root, errorMessage(error, "轮换失败"));
      } finally {
        setBusy(event.currentTarget, false, "");
      }
    });
  }

  global.ApiClientConfig = { splitCidrs, selectedTemplate, init };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-api-client-page]");
    if (root) init(root);
  });
})(window);
