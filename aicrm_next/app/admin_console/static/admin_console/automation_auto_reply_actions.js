(function () {
  "use strict";

  const AutomationAutoReply = window.AutomationAutoReply || {};
  window.AutomationAutoReply = AutomationAutoReply;

  const elements = AutomationAutoReply.elements;
  const getAdminActionToken = AutomationAutoReply.getAdminActionToken;
  const root = AutomationAutoReply.root;

  async function runAction(button) {
    const rootNode = root();
    const { buttons, statusEl } = elements();
    const url = button.getAttribute("data-reply-action-url");
    const formData = new FormData();
    formData.append("admin_action_token", getAdminActionToken(rootNode));
    if (button.hasAttribute("data-reply-toggle-enabled")) {
      formData.append("enabled", button.getAttribute("data-reply-toggle-enabled") || "0");
    }
    statusEl.textContent = "执行中，请稍候...";
    buttons.forEach((item) => {
      item.disabled = true;
    });
    try {
      const response = await fetch(url, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || payload.message || "执行失败");
      }
      statusEl.textContent = payload.message || "执行完成";
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) {
      statusEl.textContent = (error && error.message) ? error.message : "执行失败";
    } finally {
      buttons.forEach((item) => {
        item.disabled = false;
      });
    }
  }

  function bindReplyActions() {
    const { buttons, statusEl } = elements();
    if (!statusEl) return;
    buttons.forEach((button) => {
      button.addEventListener("click", () => runAction(button));
    });
  }

  AutomationAutoReply.runAction = runAction;
  AutomationAutoReply.bindReplyActions = bindReplyActions;
})();
