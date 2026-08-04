(function () {
  "use strict";

  function notice(message, isError) {
    const node = document.querySelector("[data-operation-cycle-action-notice]");
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
    node.classList.toggle("is-error", Boolean(isError));
  }

  function idempotencyKey(button) {
    return [
      "operation-action",
      button.dataset.strategyKey || "",
      button.dataset.actionKey || "",
      button.dataset.runKey || "",
      button.dataset.parentRequestId || "first",
    ].join(":").slice(0, 200);
  }

  function bindActions() {
    const requestJson = window.AdminApi && window.AdminApi.requestJson;
    if (typeof requestJson !== "function") return;
    document.querySelectorAll("[data-operation-action-start]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (button.disabled) return;
        const strategyKey = String(button.dataset.strategyKey || "");
        const actionKey = String(button.dataset.actionKey || "");
        const runKey = String(button.dataset.runKey || "");
        const parentRequestId = String(button.dataset.parentRequestId || "");
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "正在提交…";
        try {
          await requestJson(
            `/api/admin/operation-cycles/strategies/${encodeURIComponent(strategyKey)}/actions/${encodeURIComponent(actionKey)}/start`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Idempotency-Key": idempotencyKey(button),
              },
              body: JSON.stringify({
                schema_version: "operation_cycle_action_start.v1",
                run_key: runKey,
                parent_request_id: parentRequestId,
              }),
            },
          );
          button.textContent = "已创建本地任务";
          notice("已在本地 Codex 创建任务，请到 Codex 侧边栏继续处理。", false);
        } catch (error) {
          button.disabled = false;
          button.textContent = original;
          const message = error && error.message ? error.message : "任务启动失败，请刷新后重试。";
          notice(message, true);
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindActions, { once: true });
  } else {
    bindActions();
  }
})();
