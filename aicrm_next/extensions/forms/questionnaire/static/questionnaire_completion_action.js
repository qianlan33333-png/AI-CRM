(function (window, document) {
  "use strict";

  const publicErrorMessages = {
    identity_conflict: "该手机号已绑定其他账号，不能提交",
    unionid_oauth_required: "请先完成微信授权后再提交",
    wechat_browser_required: "请在微信中打开问卷后再提交",
    answers_required: "请先填写问卷内容再提交",
    already_submitted: "你已经提交过这份问卷",
    questionnaire_not_found: "问卷不存在或已停止填写",
  };

  function errorMessage(error, fallback) {
    if (!error || typeof error === "string") {
      const message = String(error || "").trim();
      if (!message || message === "[object Object]") return fallback;
      if (publicErrorMessages[message]) return publicErrorMessages[message];
      if (/^missing required answer:/i.test(message)) return "请完成所有必填项后再提交";
      if (/^question .*?(selected option not found|only allows|other_text)/i.test(message)) return "填写内容有误，请检查后重试";
      if (/^[a-z][a-z0-9]*_[a-z0-9_]+$/i.test(message)) return fallback;
      if (!/[一-龥]/.test(message) && /[a-z]/i.test(message)) return fallback;
      return message;
    }
    if (Array.isArray(error)) return error.map((item) => errorMessage(item, "")).filter(Boolean).join("；") || fallback;
    if (error.loc && (error.msg || error.message)) return `${(Array.isArray(error.loc) ? error.loc : [error.loc]).filter((item) => item !== "body").join(".") || "提交内容"}：${errorMessage(error.msg || error.message, fallback)}`;
    for (const key of ["detail", "message", "error", "reason", "errors", "msg"]) { const message = Object.prototype.hasOwnProperty.call(error, key) && errorMessage(error[key], ""); if (message) return message; }
    return fallback;
  }

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

  window.AICRMQuestionnaireCompletionAction = { create, errorMessage };
})(window, document);
