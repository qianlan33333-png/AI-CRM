(function () {
  "use strict";

  const AdminApi = window.AdminApi || {};
  const AutomationAutoReply = window.AutomationAutoReply || {};
  window.AutomationAutoReply = AutomationAutoReply;

  const state = {
    outputs: [],
    pendingRejectOutputId: "",
    clipboardText: "",
  };

  const safeJsonParse = AdminApi.safeJsonParse || function safeJsonParse(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  };

  const escapeHtml = AdminApi.escapeHtml || function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  const requestJson = AdminApi.requestJson || function adminApiRequestJsonUnavailable() {
    return Promise.reject(new Error("AdminApi.requestJson unavailable"));
  };

  function root() {
    return document.getElementById("automation-auto-reply-root");
  }

  function getApiUrls(rootNode) {
    const source = rootNode || root();
    if (!source) return {};
    return safeJsonParse(source.getAttribute("data-api-urls") || "{}") || {};
  }

  function getAdminActionToken(rootNode) {
    const source = rootNode || root();
    return String((source && source.dataset && source.dataset.adminActionToken) || "");
  }

  function elements() {
    return {
      buttons: Array.from(document.querySelectorAll("[data-reply-action-url]")),
      statusEl: document.getElementById("reply-action-status"),
      reviewStatusEl: document.getElementById("reply-review-status"),
      outputListEl: document.getElementById("reply-output-list"),
      outputEmptyEl: document.getElementById("reply-output-empty"),
      modalEl: document.getElementById("reply-output-modal"),
      modalFeedbackEl: document.getElementById("reply-output-modal-feedback"),
      reviewNoteEl: document.getElementById("reply-output-review-note"),
      clipboardWrapEl: document.getElementById("reply-output-clipboard-wrap"),
      clipboardTextEl: document.getElementById("reply-output-clipboard-text"),
      modalSubmitEl: document.getElementById("reply-output-modal-submit"),
      modalCopyEl: document.getElementById("reply-output-modal-copy"),
      modalCancelEl: document.getElementById("reply-output-modal-cancel"),
    };
  }

  function urlWithOutputId(template, outputId) {
    return String(template || "").replace("__OUTPUT_ID__", encodeURIComponent(String(outputId || "")));
  }

  function withOutputId(outputId) {
    const apiUrls = getApiUrls();
    return urlWithOutputId(apiUrls.review_output_base, outputId);
  }

  function withSendOutputId(outputId) {
    const apiUrls = getApiUrls();
    return urlWithOutputId(apiUrls.review_output_bazhuayu_send_base, outputId);
  }

  function withWebhookOutputId(outputId) {
    const apiUrls = getApiUrls();
    return urlWithOutputId(apiUrls.review_output_webhook_send_base || apiUrls.review_output_bazhuayu_send_base, outputId);
  }

  function withWecomOutputId(outputId) {
    const apiUrls = getApiUrls();
    return urlWithOutputId(apiUrls.review_output_wecom_send_base, outputId);
  }

  async function copyClipboardText(text) {
    if (!text) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_error) {
      return false;
    }
  }

  async function copyReplyText(text) {
    const normalized = String(text || "").trim();
    if (!normalized) {
      throw new Error("当前没有可复制的话术");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(normalized);
      return;
    }
    window.prompt("请复制以下话术", normalized);
  }

  AutomationAutoReply.root = root;
  AutomationAutoReply.escapeHtml = escapeHtml;
  AutomationAutoReply.requestJson = requestJson;
  AutomationAutoReply.getApiUrls = getApiUrls;
  AutomationAutoReply.getAdminActionToken = getAdminActionToken;
  AutomationAutoReply.state = state;
  AutomationAutoReply.elements = elements;
  AutomationAutoReply.withOutputId = withOutputId;
  AutomationAutoReply.withSendOutputId = withSendOutputId;
  AutomationAutoReply.withWebhookOutputId = withWebhookOutputId;
  AutomationAutoReply.withWecomOutputId = withWecomOutputId;
  AutomationAutoReply.copyClipboardText = copyClipboardText;
  AutomationAutoReply.copyReplyText = copyReplyText;
})();
