(function (global) {
  "use strict";

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const typeLabel = (value) => value === "fixed_script" ? "固定话术" : "Agent 机器人";
  const statusLabel = (value) => value === "active" ? "启用中" : "已停止";

  function mount(container, options) {
    if (!container) throw new Error("AutomationCapabilitySelector container is required");
    const initialItems = Array.isArray(options && options.items) ? options.items : [];
    const initialValue = Number(options && options.value || 0) || null;
    const selectedItem = initialItems.find((item) => Number(item.id || 0) === initialValue);
    const state = {
      items: initialItems,
      value: initialValue,
      currentPackageId: Number(options && options.currentPackageId || 0),
      type: selectedItem && selectedItem.automation_type === "fixed_script" ? "fixed_script" : "agent",
      onChange: options && typeof options.onChange === "function" ? options.onChange : function () {}
    };

    const availability = (item) => {
      const boundPackageId = Number(item.bound_package_id || 0);
      if (boundPackageId && boundPackageId !== state.currentPackageId) {
        const stopped = item.status !== "active" ? "已停止 · " : "";
        return { disabled: true, reason: `${stopped}已绑定「${item.bound_package_name || "其他人群包"}」` };
      }
      if (item.status !== "active") {
        return { disabled: true, reason: boundPackageId ? "已停止 · 已绑定当前人群包" : "已停止，暂不可选择" };
      }
      return { disabled: false, reason: boundPackageId ? "已绑定当前人群包" : "可绑定" };
    };

    const render = () => {
      const visible = state.items.filter((item) => (item.automation_type || "agent") === state.type);
      container.innerHTML = `
        <div class="automation-capability-tabs" role="tablist" aria-label="自动化能力类型">
          <button type="button" role="tab" data-capability-type="agent" aria-selected="${state.type === "agent"}">Agent 机器人</button>
          <button type="button" role="tab" data-capability-type="fixed_script" aria-selected="${state.type === "fixed_script"}">固定话术</button>
        </div>
        <div class="automation-capability-list" role="radiogroup" aria-label="选择自动化名称">
          ${visible.length ? visible.map((item) => {
            const itemId = Number(item.id || 0);
            const selected = itemId === state.value;
            const rule = availability(item);
            return `
              <label class="automation-capability-item${selected ? " is-selected" : ""}${rule.disabled ? " is-disabled" : ""}">
                <input type="radio" name="automation-capability" value="${itemId}" ${selected ? "checked" : ""} ${rule.disabled ? "disabled" : ""}>
                <span class="automation-capability-copy">
                  <strong>${escapeHtml(item.agent_name || item.agent_code || "未命名自动化")}</strong>
                  <span>${escapeHtml(typeLabel(item.automation_type))} · ${escapeHtml(statusLabel(item.status))}</span>
                </span>
                <span class="automation-capability-state">${escapeHtml(rule.reason)}</span>
              </label>
            `;
          }).join("") : '<div class="automation-capability-empty">当前类型暂无自动化话术</div>'}
        </div>
      `;
      container.querySelectorAll("[data-capability-type]").forEach((button) => {
        button.addEventListener("click", () => {
          state.type = button.dataset.capabilityType;
          render();
        });
      });
      container.querySelectorAll('input[name="automation-capability"]').forEach((input) => {
        input.addEventListener("change", () => {
          state.value = Number(input.value || 0) || null;
          state.onChange(state.value);
          render();
        });
      });
    };

    render();
    return {
      getValue: () => state.value,
      setValue: (value) => { state.value = Number(value || 0) || null; render(); },
      setItems: (items) => { state.items = Array.isArray(items) ? items : []; render(); },
      destroy: () => { container.innerHTML = ""; }
    };
  }

  function createBindingController(container, options) {
    if (!options || typeof options.fetchJson !== "function") {
      throw new Error("AutomationCapabilitySelector fetchJson is required");
    }
    const state = { binding: null, selectedAutomationId: null, selector: null };
    const setStatus = (message, tone = "") => {
      if (typeof options.setStatus === "function") options.setStatus(message, tone);
    };
    const render = (items) => {
      if (state.selector) state.selector.destroy();
      state.selector = mount(container, {
        items,
        value: state.selectedAutomationId,
        currentPackageId: options.currentPackageId,
        onChange: (value) => { state.selectedAutomationId = value; }
      });
      if (options.unbindButton) options.unbindButton.disabled = !state.binding;
      if (state.binding && state.binding.warning === "automation_paused") {
        setStatus("当前绑定能力已停止，关系保留但不会继续接收新增成员。", "error");
      } else if (state.binding) {
        setStatus(`当前绑定：${state.binding.automation_name || state.binding.agent_code}`, "success");
      } else {
        setStatus("当前未绑定自动化话术能力");
      }
    };
    const load = async () => {
      const [agentsPayload, bindingPayload] = await Promise.all([
        options.fetchJson(`${options.automationAgentsApiUrl}?_=${Date.now()}`),
        options.fetchJson(`${options.bindingApiUrl}?_=${Date.now()}`)
      ]);
      state.binding = bindingPayload.binding || null;
      state.selectedAutomationId = state.binding ? Number(state.binding.automation_id) : null;
      render(agentsPayload.items || []);
      return state.binding;
    };
    const save = async () => {
      if (!state.selectedAutomationId) throw new Error("请先选择自动化名称");
      setStatus("保存中...");
      await options.fetchJson(options.bindingApiUrl, {
        method: "PUT",
        body: { automation_id: state.selectedAutomationId }
      });
      await load();
      setStatus("自动化话术能力已绑定", "success");
      return state.binding;
    };
    const unbind = async () => {
      if (!state.binding) return null;
      const confirmAction = typeof options.confirm === "function" ? options.confirm : global.confirm;
      if (!confirmAction(`确认解除与「${state.binding.automation_name || state.binding.agent_code}」的绑定？`)) return state.binding;
      setStatus("解除中...");
      await options.fetchJson(options.bindingApiUrl, { method: "DELETE" });
      await load();
      setStatus("绑定已解除", "success");
      return state.binding;
    };
    return { load, save, unbind, getBinding: () => state.binding };
  }

  global.AutomationCapabilitySelector = { mount, createBindingController };
})(window);
