(function () {
  "use strict";

  const AdminApi = window.AdminApi || {};
  const CustomerProfile = window.CustomerProfile || {};
  window.CustomerProfile = CustomerProfile;

  function root() {
    return document.querySelector("[data-customer-profile-root]");
  }

  const safeJsonParse = AdminApi.safeJsonParse || function safeJsonParse(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  };

  const escapeHtml = AdminApi.escapeHtml || function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  const requestJson = AdminApi.requestJson || function adminApiRequestJsonUnavailable() {
    return Promise.reject(new Error("AdminApi.requestJson unavailable"));
  };

  const isPermissionError = AdminApi.isPermissionError || function isPermissionError(error) {
    const message = String((error && error.message) || "");
    return Boolean(error) && (error.status === 401 || error.status === 403 || message.includes("令牌无效"));
  };

  function customerPulseAccessHeaders(rootNode) {
    const headers = {};
    if (!rootNode || !rootNode.dataset) return headers;
    if (rootNode.dataset.customerPulseTenantKey) {
      headers["X-Tenant-Key"] = rootNode.dataset.customerPulseTenantKey;
    }
    if (rootNode.dataset.customerPulseActorUserid) {
      headers["X-Admin-Userid"] = rootNode.dataset.customerPulseActorUserid;
    }
    if (rootNode.dataset.customerPulseActorRole) {
      headers["X-Admin-Role"] = rootNode.dataset.customerPulseActorRole;
    }
    return headers;
  }

  function requestCustomerPulseJson(rootNode, url, options = {}) {
    return requestJson(url, {
      ...options,
      headers: {
        ...customerPulseAccessHeaders(rootNode),
        ...(options.headers || {}),
      },
    });
  }

  function toDateTimeLocalValue(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return text.replace(" ", "T").slice(0, 16);
  }

  function showSectionError(stateNode, message) {
    if (!stateNode) return;
    stateNode.hidden = false;
    stateNode.classList.remove("admin-state--loading");
    stateNode.classList.add("admin-state--error");
    stateNode.innerHTML = ["<strong>当前无法加载</strong>", `<span>${escapeHtml(message)}</span>`].join("");
  }

  function showSectionEmpty(stateNode, title, body) {
    if (!stateNode) return;
    stateNode.hidden = false;
    stateNode.classList.remove("admin-state--loading", "admin-state--error");
    stateNode.innerHTML = [`<strong>${escapeHtml(title)}</strong>`, `<span>${escapeHtml(body)}</span>`].join("");
  }

  function scrollToInitialSection(rootNode) {
    const sectionId = rootNode.dataset.initialSection;
    if (!sectionId) return;
    const section = document.getElementById(sectionId);
    if (!section) return;
    window.setTimeout(() => {
      section.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 120);
  }

  const state = CustomerProfile.state || {
    currentCustomerPulsePayload: null,
    currentCustomerPulsePreview: null,
    currentCustomerPulsePreviewError: "",
    currentCustomerPulseEvidencePayload: null,
    currentCustomerPulseEvidenceError: "",
  };

  CustomerProfile.root = root;
  CustomerProfile.safeJsonParse = safeJsonParse;
  CustomerProfile.escapeHtml = escapeHtml;
  CustomerProfile.requestJson = requestJson;
  CustomerProfile.isPermissionError = isPermissionError;
  CustomerProfile.customerPulseAccessHeaders = customerPulseAccessHeaders;
  CustomerProfile.requestCustomerPulseJson = requestCustomerPulseJson;
  CustomerProfile.toDateTimeLocalValue = toDateTimeLocalValue;
  CustomerProfile.showSectionError = showSectionError;
  CustomerProfile.showSectionEmpty = showSectionEmpty;
  CustomerProfile.scrollToInitialSection = scrollToInitialSection;
  CustomerProfile.state = state;
})();
