(function () {
  "use strict";

  const AutomationAutoReply = window.AutomationAutoReply || {};
  window.AutomationAutoReply = AutomationAutoReply;

  function boot() {
    const root = AutomationAutoReply.root();
    if (!root) return;
    AutomationAutoReply.bindReplyActions(root);
    AutomationAutoReply.bindOutputList(root);
    AutomationAutoReply.bindRejectModal(root);
    AutomationAutoReply.loadOutputs(root);
  }

  AutomationAutoReply.boot = boot;

  document.addEventListener("DOMContentLoaded", () => {
    if (typeof AutomationAutoReply.boot === "function") {
      AutomationAutoReply.boot();
    }
  });
})();
