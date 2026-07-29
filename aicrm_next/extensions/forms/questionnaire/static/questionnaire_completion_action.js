(function (window, document) {
  "use strict";

  function create(options) {
    const panel = document.getElementById("lead-qr-panel");
    const title = document.getElementById("lead-qr-title");
    const image = document.getElementById("lead-qr-image");

    function renderLeadQr(leadQr) {
      const qrUrl = String((leadQr && leadQr.qr_url) || "").trim();
      if (!/^https:\/\/[^\s\\]+$/i.test(qrUrl)) {
        window.location.href = options.submittedUrl;
        return;
      }
      if (options.formEl) options.formEl.hidden = true;
      options.weappLaunchPanel.hidden = true;
      title.textContent = String(leadQr.channel_name || "扫码继续");
      image.onerror = () => {
        image.removeAttribute("src");
        panel.hidden = true;
        options.setState("提交成功");
      };
      image.src = qrUrl;
      panel.hidden = false;
      options.setState("提交成功");
      panel.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    return function handleCompletionResponse(result) {
      const action = (result && result.completion_action) || {};
      if (action.type === "lead_qr") {
        renderLeadQr((result && result.lead_qr) || action.lead_qr || {});
        return;
      }
      options.handleCompletionTarget(
        result && result.completion_target,
        (result && result.redirect_url) || options.submittedUrl,
      );
    };
  }

  window.AICRMQuestionnaireCompletionAction = { create };
})(window, document);
