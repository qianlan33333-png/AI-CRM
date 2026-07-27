(function () {
  function findToken(root) {
    return (root && root.dataset.adminActionToken) || "";
  }

  function showAlert(root, message, tone) {
    const alert = root && root.querySelector("[data-config-alert]");
    if (!alert) {
      return;
    }
    alert.textContent = message || "";
    alert.hidden = !message;
    alert.classList.toggle("is-error", tone === "error");
    alert.classList.toggle("is-success", tone === "success");
  }

  function statusText(enabled) {
    return enabled ? "已生效" : "未生效";
  }

  function updateStatus(rowOrRoot, enabled) {
    const status = rowOrRoot && rowOrRoot.querySelector("[data-category-status]");
    if (!status) {
      return;
    }
    status.textContent = statusText(enabled);
    status.classList.toggle("is-on", enabled);
  }

  async function requestJson(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "操作失败");
    }
    return payload;
  }

  function bindEnabledSwitch(root, checkbox) {
    checkbox.addEventListener("change", async function () {
      const categoryKey = checkbox.dataset.categoryKey;
      const nextValue = checkbox.checked;
      const row = checkbox.closest("[data-category-row]") || root;
      checkbox.disabled = true;
      showAlert(root, "", "");
      try {
        await requestJson(`/api/admin/config/categories/${encodeURIComponent(categoryKey)}/enabled`, {
          method: "PUT",
          body: JSON.stringify({
            admin_action_token: findToken(root),
            enabled: nextValue,
          }),
        });
        updateStatus(row, nextValue);
        showAlert(root, "保存成功", "success");
      } catch (error) {
        checkbox.checked = !nextValue;
        updateStatus(row, !nextValue);
        showAlert(root, error.message || "保存失败", "error");
      } finally {
        checkbox.disabled = false;
      }
    });
  }

  function collectSettings(form) {
    const settings = {};
    form.querySelectorAll("[data-config-field]").forEach((field) => {
      if (field.disabled || field.readOnly) {
        return;
      }
      const key = field.dataset.fieldKey;
      if (!key) {
        return;
      }
      if (field.type === "password" && !field.value.trim()) {
        return;
      }
      if (field.dataset.fieldType === "boolean") {
        settings[key] = field.checked;
        return;
      }
      settings[key] = field.value;
    });
    return settings;
  }

  function bindDetail(root) {
    const categoryKey = root.dataset.categoryKey;
    const form = root.querySelector("[data-config-settings-form]");
    const checkButton = root.querySelector("[data-config-check]");
    if (form) {
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const button = form.querySelector('button[type="submit"]');
        if (button) {
          button.disabled = true;
        }
        showAlert(root, "", "");
        try {
          await requestJson(`/api/admin/config/categories/${encodeURIComponent(categoryKey)}/settings`, {
            method: "PUT",
            body: JSON.stringify({
              admin_action_token: findToken(root),
              settings: collectSettings(form),
            }),
          });
          showAlert(root, "保存成功", "success");
        } catch (error) {
          showAlert(root, error.message || "保存失败", "error");
        } finally {
          if (button) {
            button.disabled = false;
          }
        }
      });
    }
    if (checkButton) {
      checkButton.addEventListener("click", async function () {
        checkButton.disabled = true;
        showAlert(root, "", "");
        try {
          const payload = await requestJson(`/api/admin/config/categories/${encodeURIComponent(categoryKey)}/check`, {
            method: "POST",
            body: JSON.stringify({}),
          });
          const failed = payload.summary ? payload.summary.failed : 0;
          showAlert(root, failed ? "检查未通过" : "检查通过", failed ? "error" : "success");
        } catch (error) {
          showAlert(root, error.message || "检查失败", "error");
        } finally {
          checkButton.disabled = false;
        }
      });
    }
  }

  function memberLabel(member) {
    if (window.OperationMemberPicker && typeof window.OperationMemberPicker.memberLabel === "function") {
      return window.OperationMemberPicker.memberLabel(member);
    }
    const userId = String((member || {}).user_id || "");
    const displayName = String((member || {}).display_name || "");
    return displayName && displayName !== userId ? `${displayName} / ${userId}` : userId;
  }

  function bindAdminAccessMemberPicker(root) {
    const form = root.querySelector("[data-admin-access-form]");
    const pickButton = root.querySelector("[data-admin-access-member-picker]");
    const input = root.querySelector("[data-admin-access-wecom-userid]");
    const label = root.querySelector("[data-admin-access-member-current]");
    const feedback = root.querySelector("[data-admin-access-member-feedback]");
    const displayNameInput = root.querySelector("[data-admin-access-display-name]");
    const loginEnabled = root.querySelector("[data-admin-access-login-enabled]");
    const superAdminToggle = root.querySelector("[data-admin-access-super-admin]");
    const adminLevelInput = root.querySelector("[data-admin-access-level]");
    const transferConfirmInput = root.querySelector("[data-admin-access-transfer-confirm]");
    if (!form || !pickButton || !input || !label) {
      return;
    }

    function applySuperAdminState() {
      const isSuperAdmin = Boolean(superAdminToggle && superAdminToggle.checked);
      if (adminLevelInput) {
        adminLevelInput.value = isSuperAdmin ? "super_admin" : "admin";
      }
      if (transferConfirmInput) {
        transferConfirmInput.value = isSuperAdmin ? "1" : "";
      }
      if (isSuperAdmin && loginEnabled) {
        loginEnabled.checked = true;
      }
    }

    if (superAdminToggle) {
      superAdminToggle.addEventListener("change", applySuperAdminState);
      applySuperAdminState();
    }

    pickButton.addEventListener("click", function () {
      if (!window.OperationMemberPicker || typeof window.OperationMemberPicker.open !== "function") {
        showAlert(root, "人员选择器加载失败，请稍后重试", "error");
        return;
      }
      const previousText = pickButton.textContent;
      pickButton.textContent = "正在打开...";
      pickButton.disabled = true;
      showAlert(root, "", "");
      window.OperationMemberPicker.open({
        value: input.value,
        selectedLabel: label.textContent.trim(),
        selectedMember: input.value ? { user_id: input.value, display_name: displayNameInput ? displayNameInput.value : label.textContent.trim() } : null,
        title: "选择可访问后台的企微成员",
        scope: "common",
        page_size: 100,
        onSelect: function (member) {
          input.value = member.user_id || "";
          const nextLabel = memberLabel(member) || "未选择企微成员";
          label.textContent = nextLabel;
          label.classList.toggle("is-selected", Boolean(member.user_id));
          pickButton.textContent = "更换企微成员";
          if (feedback) {
            feedback.textContent = `已选择 ${nextLabel}`;
          }
          if (displayNameInput) {
            displayNameInput.value = member.display_name || member.name || member.user_id || "";
          }
          showAlert(root, `已选择 ${nextLabel}`, "success");
        },
      }).catch(function () {
        showAlert(root, "人员选择器加载失败，请稍后重试", "error");
      }).finally(function () {
        pickButton.disabled = false;
        if (!input.value.trim()) {
          pickButton.textContent = previousText;
        }
      });
    });

    form.addEventListener("submit", function (event) {
      applySuperAdminState();
      if (input.value.trim()) {
        return;
      }
      event.preventDefault();
      showAlert(root, "请选择可访问后台的企微成员", "error");
      pickButton.focus();
    });
  }

  document.querySelectorAll("[data-config-center], [data-config-detail]").forEach((root) => {
    root.querySelectorAll("[data-category-enabled]").forEach((checkbox) => bindEnabledSwitch(root, checkbox));
    if (root.matches("[data-config-detail]")) {
      bindDetail(root);
    }
    if (root.matches("[data-admin-access-detail]")) {
      bindAdminAccessMemberPicker(root);
    }
  });
})();
