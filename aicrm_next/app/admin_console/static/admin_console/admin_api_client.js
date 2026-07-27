(function (window) {
  "use strict";

  function safeJsonParse(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  }

  const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

  function cookieValue(name) {
    const prefix = `${encodeURIComponent(name)}=`;
    return document.cookie
      .split(";")
      .map((item) => item.trim())
      .filter((item) => item.startsWith(prefix))
      .map((item) => decodeURIComponent(item.slice(prefix.length)))[0] || "";
  }

  function adminActionTokens() {
    const node = document.getElementById("aicrmAdminActionGrants");
    const payload = safeJsonParse((node && node.textContent) || "{}");
    return payload && typeof payload === "object" ? payload : {};
  }

  function routeTemplateMatches(template, pathname) {
    const expected = String(template || "").split("/").filter(Boolean);
    const actual = String(pathname || "").split("/").filter(Boolean);
    if (expected.length !== actual.length) return false;
    return expected.every((segment, index) => {
      if (segment.startsWith("{") && segment.endsWith("}")) return Boolean(actual[index]);
      return segment === actual[index];
    });
  }

  function actionToken(method, url) {
    const normalizedMethod = String(method || "GET").toUpperCase();
    let parsed;
    try {
      parsed = new URL(String(url || window.location.href), window.location.href);
    } catch (_error) {
      return "";
    }
    if (parsed.origin !== window.location.origin) return "";
    const tokens = adminActionTokens();
    const exactKey = `${normalizedMethod} ${parsed.pathname}`;
    if (tokens[exactKey]) return String(tokens[exactKey]);
    const prefix = `${normalizedMethod} `;
    const matched = Object.keys(tokens).find((key) =>
      key.startsWith(prefix) && routeTemplateMatches(key.slice(prefix.length), parsed.pathname),
    );
    return matched ? String(tokens[matched]) : "";
  }

  function sameOrigin(url) {
    try {
      return new URL(String(url || window.location.href), window.location.href).origin === window.location.origin;
    } catch (_error) {
      return false;
    }
  }

  function prepareUnsafeHeaders(headers, method, url) {
    const normalizedMethod = String(method || "GET").toUpperCase();
    if (SAFE_METHODS.has(normalizedMethod) || !sameOrigin(url)) return headers;
    const csrfToken = cookieValue("aicrm_next_csrf");
    if (csrfToken && !hasHeader(headers, "X-CSRF-Token")) {
      headers["X-CSRF-Token"] = csrfToken;
    }
    const token = actionToken(normalizedMethod, url);
    if (token) {
      setHeader(headers, "X-Admin-Action-Token", token);
    }
    return headers;
  }

  function prepareUnsafeForm(form) {
    if (!form) return;
    const method = String(form.method || "GET").toUpperCase();
    if (SAFE_METHODS.has(method) || !sameOrigin(form.action || window.location.href)) return;
    const csrfToken = cookieValue("aicrm_next_csrf");
    if (csrfToken) {
      let csrfInput = form.querySelector('input[name="csrf_token"]');
      if (!csrfInput) {
        csrfInput = document.createElement("input");
        csrfInput.type = "hidden";
        csrfInput.name = "csrf_token";
        form.appendChild(csrfInput);
      }
      csrfInput.value = csrfToken;
    }
    const token = actionToken(method, form.action || window.location.href);
    if (token) {
      let tokenInput = form.querySelector('input[name="admin_action_token"]');
      if (!tokenInput) {
        tokenInput = document.createElement("input");
        tokenInput.type = "hidden";
        tokenInput.name = "admin_action_token";
        form.appendChild(tokenInput);
      }
      tokenInput.value = token;
    }
  }

  function installRequestSecurity() {
    if (window.__aicrmRequestSecurityInstalled) return;
    window.__aicrmRequestSecurityInstalled = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function securedFetch(input, options = {}) {
      const url = typeof input === "string" || input instanceof URL ? String(input) : String(input && input.url || "");
      const method = String(options.method || (input && input.method) || "GET").toUpperCase();
      const finalOptions = { ...options };
      finalOptions.headers = prepareUnsafeHeaders(headersToObject(options.headers || (input && input.headers)), method, url);
      return nativeFetch(input, finalOptions);
    };
    document.addEventListener("submit", (event) => prepareUnsafeForm(event.target), true);
    if (typeof HTMLFormElement !== "undefined") {
      const nativeSubmit = HTMLFormElement.prototype.submit;
      HTMLFormElement.prototype.submit = function securedSubmit() {
        prepareUnsafeForm(this);
        return nativeSubmit.call(this);
      };
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function headersToObject(headers) {
    const result = {};
    if (!headers) return result;
    if (typeof Headers !== "undefined") {
      try {
        const normalizedHeaders = new Headers(headers);
        normalizedHeaders.forEach((value, key) => {
          result[key] = value;
        });
        return result;
      } catch (_error) {
        return { ...headers };
      }
    }
    return { ...headers };
  }

  function hasHeader(headers, name) {
    const normalizedName = String(name || "").toLowerCase();
    return Object.keys(headers).some((key) => key.toLowerCase() === normalizedName);
  }

  function setHeader(headers, name, value) {
    const normalizedName = String(name || "").toLowerCase();
    Object.keys(headers).forEach((key) => {
      if (key.toLowerCase() === normalizedName) {
        delete headers[key];
      }
    });
    headers[name] = value;
  }

  function hasBody(options) {
    return Object.prototype.hasOwnProperty.call(options, "body") && options.body !== undefined && options.body !== null;
  }

  function isFormData(value) {
    return typeof FormData !== "undefined" && value instanceof FormData;
  }

  function isUrlSearchParams(value) {
    return typeof URLSearchParams !== "undefined" && value instanceof URLSearchParams;
  }

  function isJsonBody(value) {
    return Array.isArray(value) || Object.prototype.toString.call(value) === "[object Object]";
  }

  function buildRequestOptions(options) {
    const finalOptions = { ...options };
    const headers = headersToObject(options.headers);
    const body = options.body;

    if (!hasHeader(headers, "Accept")) {
      headers.Accept = "application/json";
    }

    finalOptions.method = String(options.method || "GET").toUpperCase();
    finalOptions.credentials = options.credentials || "same-origin";

    if (!hasBody(options)) {
      delete finalOptions.body;
    } else if (isFormData(body) || isUrlSearchParams(body) || typeof body === "string") {
      finalOptions.body = body;
    } else if (isJsonBody(body)) {
      if (!hasHeader(headers, "Content-Type")) {
        headers["Content-Type"] = "application/json";
      }
      finalOptions.body = JSON.stringify(body);
    } else {
      finalOptions.body = body;
    }

    finalOptions.headers = headers;
    return finalOptions;
  }

  function responseErrorMessage(response, payload) {
    if (payload && payload.error) return String(payload.error);
    if (payload && payload.message) return String(payload.message);
    if (response && response.statusText) return response.statusText;
    if (response && response.status) return `request failed (${response.status})`;
    return "request failed";
  }

  function normalizeRequestError(error, context = {}) {
    if (!error.status && context.response) {
      error.status = context.response.status;
    } else if (!error.status && context.status) {
      error.status = context.status;
    }
    if (!Object.prototype.hasOwnProperty.call(error, "payload")) {
      error.payload = context.payload || null;
    }
    if (!error.response && context.response) {
      error.response = context.response;
    }
    if (!error.url && context.url) {
      error.url = context.url;
    }
    if (!error.method && context.method) {
      error.method = context.method;
    }
    return error;
  }

  function buildRequestError(response, payload, context) {
    return normalizeRequestError(new Error(responseErrorMessage(response, payload)), {
      ...context,
      response,
      payload,
      status: response.status,
    });
  }

  function requestJson(url, options = {}) {
    const finalOptions = buildRequestOptions(options);
    const method = finalOptions.method || "GET";
    return fetch(url, finalOptions)
      .then((response) =>
        response.text().then((text) => ({
          response,
          payload: safeJsonParse(text),
        })),
      )
      .then(({ response, payload }) => {
        if (!response.ok || (payload && payload.ok === false)) {
          throw buildRequestError(response, payload, { url, method });
        }
        return payload || { ok: true };
      });
  }

  function isPermissionError(error) {
    const message = String((error && error.message) || "");
    const loweredMessage = message.toLowerCase();
    return Boolean(error) && (
      error.status === 401 ||
      error.status === 403 ||
      message.includes("令牌无效") ||
      loweredMessage.includes("permission") ||
      message.includes("权限")
    );
  }

  window.AdminApi = {
    ...(window.AdminApi || {}),
    safeJsonParse,
    escapeHtml,
    requestJson,
    isPermissionError,
    normalizeRequestError,
    actionToken,
    csrfToken: () => cookieValue("aicrm_next_csrf"),
    prepareUnsafeForm,
  };

  installRequestSecurity();
})(window);
