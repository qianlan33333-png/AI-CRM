(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", () => {
    const AutomationAgentConfig = window.AutomationAgentConfig || {};
    if (typeof AutomationAgentConfig.boot === "function") {
      AutomationAgentConfig.boot();
    }
  });
})();
