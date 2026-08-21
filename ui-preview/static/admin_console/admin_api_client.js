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

  const ERROR_VALUE_KEYS = ["detail", "message", "error", "reason", "errors", "error_message", "msg"];
  const ERROR_META_KEYS = new Set([
    "ok", "success", "code", "error_code", "type", "loc", "location", "input", "ctx", "context", "status", "status_code",
    "request_id", "trace_id", "stack", "debug",
  ]);
  const GENERIC_ERROR_STRINGS = new Set([
    "[object object]", "request failed", "failed", "error", "internal server error", "bad request",
  ]);
  const ERROR_CODE_MESSAGES = {
    admin_auth_required: "登录状态已失效，请刷新页面后重新登录",
    authentication_required: "登录状态已失效，请刷新页面后重新登录",
    unauthorized: "登录状态已失效，请刷新页面后重新登录",
    forbidden: "当前账号没有执行此操作的权限",
    permission_denied: "当前账号没有执行此操作的权限",
    rate_limited: "操作太频繁，请稍后重试",
    too_many_requests: "操作太频繁，请稍后重试",
    payload_too_large: "提交内容过大，请压缩后重试",
    request_entity_too_large: "提交内容过大，请压缩后重试",
    not_found: "请求的数据不存在或已被删除",
    conflict: "数据已发生变化，请刷新页面后重试",
    channels_load_failed: "渠道列表加载失败，请稍后重试",
    channel_status_update_failed: "渠道状态更新失败，请稍后重试",
    sync_failed: "同步失败，请检查配置后重试",
    external_call_failed_known: "外部服务暂时调用失败，请稍后重试",
    identity_conflict: "当前客户身份存在冲突，请联系管理员处理",
    missing_unionid: "当前客户身份信息不完整，请稍后重试",
  };

  function uniqueMessages(values) {
    const seen = new Set();
    return values.filter((value) => {
      const normalized = String(value || "").trim();
      if (!normalized || seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
  }

  function humanizeErrorText(value) {
    const text = String(value == null ? "" : value).trim();
    if (!text) return "";
    const lowered = text.toLowerCase();
    if (ERROR_CODE_MESSAGES[lowered]) return ERROR_CODE_MESSAGES[lowered];
    if (GENERIC_ERROR_STRINGS.has(lowered)) return "";
    if (lowered === "field required" || lowered === "required" || lowered.includes("field required")) return "必填";
    if (lowered === "failed to fetch" || lowered.includes("networkerror") || lowered.includes("network request failed")) {
      return "网络连接异常，请检查网络后重试";
    }
    if (/^http\s+\d+$/i.test(text) || /^[a-z][a-z0-9]*_[a-z0-9_]+$/i.test(text)) return "";
    const minLength = text.match(/String should have at least (\d+) characters?/i);
    if (minLength) return `至少填写 ${minLength[1]} 个字符`;
    const maxLength = text.match(/String should have at most (\d+) characters?/i);
    if (maxLength) return `最多填写 ${maxLength[1]} 个字符`;
    if (/input should be a valid/i.test(text) || /value is not a valid/i.test(text)) return "格式不正确";
    if (/\bnot found\b|\bdoes not exist\b/i.test(text)) return "请求的数据不存在或已被删除";
    if (/\balready exists\b|\bmust be unique\b|\bduplicate\b/i.test(text)) return "相同数据已存在，请检查后重试";
    if (/\bnot configured\b|configuration .* required/i.test(text)) return "相关配置尚未完成，请联系管理员处理";
    if (/\bnot available\b|\bunavailable\b/i.test(text)) return "当前服务暂不可用，请稍后重试";
    if (!/[一-龥]/.test(text) && /\b(not allowed|cannot|unsupported|invalid|must be|required for)\b/i.test(text)) {
      return "提交内容不符合要求，请检查后重试";
    }
    if (!/[一-龥]/.test(text) && /[a-z]/i.test(text)) return "";
    return text;
  }

  function fieldPath(location) {
    const parts = Array.isArray(location) ? location : [location];
    const filtered = parts
      .map((part) => String(part == null ? "" : part).trim())
      .filter((part) => part && !["body", "query", "path", "header", "headers", "cookie"].includes(part));
    return filtered.join(".");
  }

  function fieldLabel(path, labels = {}) {
    const normalizedPath = String(path || "");
    const leaf = normalizedPath.split(".").filter(Boolean).pop() || "";
    return labels[normalizedPath] || labels[leaf] || normalizedPath || "提交内容";
  }

  function collectFieldErrors(value, options = {}, results = [], depth = 0) {
    if (depth > 8 || value == null) return results;
    if (Array.isArray(value)) {
      value.forEach((item) => collectFieldErrors(item, options, results, depth + 1));
      return results;
    }
    if (typeof value !== "object") return results;

    const location = value.loc || value.location;
    const message = humanizeErrorText(value.msg || value.message || "");
    if (location && message) {
      const path = fieldPath(location);
      results.push({
        field: path.split(".").filter(Boolean).pop() || path,
        path,
        label: fieldLabel(path, options.fieldLabels || {}),
        message,
      });
      return results;
    }

    ERROR_VALUE_KEYS.forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        collectFieldErrors(value[key], options, results, depth + 1);
      }
    });
    return results;
  }

  function formatErrorValue(value, options = {}, depth = 0) {
    if (depth > 8 || value == null) return "";
    if (value instanceof Error) return humanizeErrorText(value.message);
    if (["string", "number", "boolean"].includes(typeof value)) return humanizeErrorText(value);
    if (Array.isArray(value)) {
      return uniqueMessages(value.map((item) => formatErrorValue(item, options, depth + 1))).join("；");
    }
    if (typeof value !== "object") return "";

    const location = value.loc || value.location;
    const validationMessage = humanizeErrorText(value.msg || value.message || "");
    if (location && validationMessage) {
      return `${fieldLabel(fieldPath(location), options.fieldLabels || {})}：${validationMessage}`;
    }

    const code = humanizeErrorText(value.code || value.error_code || "");
    if (code && code !== String(value.code || value.error_code || "").trim()) return code;

    for (const key of ERROR_VALUE_KEYS) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      const message = formatErrorValue(value[key], options, depth + 1);
      if (message) return message;
    }

    const messages = Object.entries(value)
      .filter(([key]) => !ERROR_META_KEYS.has(key))
      .map(([key, item]) => {
        const message = formatErrorValue(item, options, depth + 1);
        return message ? `${fieldLabel(key, options.fieldLabels || {})}：${message}` : "";
      });
    return uniqueMessages(messages).join("；");
  }

  function statusErrorMessage(status) {
    const normalizedStatus = Number(status || 0);
    if (normalizedStatus === 400) return "提交内容有误，请检查后重试";
    if (normalizedStatus === 401) return "登录状态已失效，请刷新页面后重新登录";
    if (normalizedStatus === 403) return "当前账号没有执行此操作的权限";
    if (normalizedStatus === 404) return "请求的数据不存在或已被删除";
    if (normalizedStatus === 409) return "数据已发生变化，请刷新页面后重试";
    if (normalizedStatus === 413) return "提交内容过大，请压缩后重试";
    if (normalizedStatus === 422) return "输入内容有误，请检查标记字段";
    if (normalizedStatus === 429) return "操作太频繁，请稍后重试";
    if (normalizedStatus >= 500) return "服务暂时不可用，请稍后重试";
    return "";
  }

  function normalizeApiError(response, payload, options = {}) {
    const status = Number(options.status || (response && response.status) || 0);
    const fieldErrors = collectFieldErrors(payload, options);
    const fieldMessage = uniqueMessages(fieldErrors.map((item) => `${item.label}：${item.message}`)).join("；");
    const payloadMessage = formatErrorValue(payload, options);
    const fallback = humanizeErrorText(options.fallback || "请求失败") || "请求失败";
    const protectedStatusMessage = [401, 403, 413, 429].includes(status) || status >= 500
      ? statusErrorMessage(status)
      : "";
    const message = protectedStatusMessage || fieldMessage || payloadMessage || statusErrorMessage(status) || fallback;
    return { message, fieldErrors, status };
  }

  function responseErrorMessage(response, payload, fallback = "请求失败", options = {}) {
    return normalizeApiError(response, payload, { ...options, fallback }).message;
  }

  function errorMessage(error, fallback = "操作失败", options = {}) {
    if (error && (error.response || error.payload || error.status)) {
      return normalizeApiError(error.response, error.payload, {
        ...options,
        fallback,
        status: error.status,
      }).message;
    }
    return formatErrorValue(error, options) || humanizeErrorText(fallback) || "操作失败";
  }

  function nativeValidationMessage(input) {
    if (input.validity && input.validity.valueMissing) return "必填";
    if (input.validity && input.validity.tooShort) return `至少填写 ${input.minLength} 个字符`;
    if (input.validity && input.validity.tooLong) return `最多填写 ${input.maxLength} 个字符`;
    if (input.validity && input.validity.rangeUnderflow) return `不能小于 ${input.min}`;
    if (input.validity && input.validity.rangeOverflow) return `不能大于 ${input.max}`;
    if (input.validity && (input.validity.stepMismatch || input.validity.badInput || input.validity.typeMismatch)) return "格式不正确";
    return "输入有误";
  }

  function createFormErrorController(options = {}) {
    const form = typeof options.form === "string" ? document.getElementById(options.form) : options.form;
    const fieldLabels = options.fieldLabels || {};
    const fieldIds = options.fieldIds || {};
    const fieldEntries = Object.entries(fieldIds);
    const errorSuffix = options.errorSuffix || "Error";
    const invalidClass = options.invalidClass || "has-error";
    const containerSelector = options.fieldContainerSelector || ".field";

    function inputForField(field) {
      const inputId = fieldIds[field];
      return inputId ? document.getElementById(inputId) : null;
    }

    function fieldForInput(input) {
      const entry = fieldEntries.find(([, inputId]) => inputId === (input && input.id));
      return entry ? entry[0] : "";
    }

    function errorNode(input) {
      if (!input) return null;
      if (typeof options.errorNode === "function") return options.errorNode(input);
      return document.getElementById(`${input.id}${errorSuffix}`);
    }

    function clearField(input) {
      if (!input) return;
      input.removeAttribute("aria-invalid");
      if (typeof input.closest === "function") input.closest(containerSelector)?.classList.remove(invalidClass);
      const node = errorNode(input);
      if (node) node.textContent = "";
    }

    function clearAll() {
      Array.from(new Set(Object.values(fieldIds))).forEach((inputId) => clearField(document.getElementById(inputId)));
    }

    function setField(field, message) {
      const input = inputForField(field);
      if (!input) return null;
      input.setAttribute("aria-invalid", "true");
      if (typeof input.closest === "function") input.closest(containerSelector)?.classList.add(invalidClass);
      const node = errorNode(input);
      if (node) node.textContent = String(message || "输入有误");
      return input;
    }

    function normalize(error, fallback) {
      if (error && (error.response || error.payload || error.status)) {
        return normalizeApiError(error.response, error.payload, {
          status: error.status,
          fallback,
          fieldLabels,
        });
      }
      return { message: errorMessage(error, fallback, { fieldLabels }), fieldErrors: [] };
    }

    function focusFirst(input) {
      if (!input) return;
      if (typeof options.beforeFocus === "function") options.beforeFocus(input);
      if (typeof input.focus === "function") input.focus();
    }

    function present(error, fallback = "操作失败") {
      const normalized = normalize(error, fallback);
      clearAll();
      let firstInput = null;
      (normalized.fieldErrors || []).forEach((item) => {
        const input = setField(item.field, item.message);
        if (!firstInput && input) firstInput = input;
      });
      focusFirst(firstInput);
      if (typeof options.showMessage === "function") options.showMessage(normalized.message || fallback, "error");
      return normalized.message || fallback;
    }

    function validate() {
      if (!form) return true;
      clearAll();
      if (form.checkValidity()) return true;
      const invalidInputs = Array.from(form.elements || []).filter(
        (input) => typeof input.checkValidity === "function" && !input.checkValidity(),
      );
      const reasons = invalidInputs.map((input) => {
        const field = fieldForInput(input);
        const message = nativeValidationMessage(input);
        if (field) setField(field, message);
        const label = fieldLabels[field] || (input.labels && input.labels[0] && input.labels[0].textContent.trim()) || "表单字段";
        return `${label}：${message}`;
      });
      focusFirst(invalidInputs[0]);
      if (typeof form.reportValidity === "function") form.reportValidity();
      const prefix = options.validationPrefix || "未保存";
      if (typeof options.showMessage === "function") {
        options.showMessage(`${prefix}：${uniqueMessages(reasons).join("；")}`, "error");
      }
      return false;
    }

    return { clearField, clearAll, setField, present, validate };
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
    const normalized = normalizeApiError(response, payload);
    const error = normalizeRequestError(new Error(normalized.message), {
      ...context,
      response,
      payload,
      status: response.status,
    });
    error.fieldErrors = normalized.fieldErrors;
    return error;
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
      })
      .catch((error) => {
        if (error && (error.response || error.payload || error.status)) throw error;
        const normalized = normalizeRequestError(new Error(errorMessage(error, "网络连接异常，请检查网络后重试")), {
          url,
          method,
        });
        normalized.cause = error;
        throw normalized;
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
    formatErrorValue,
    collectFieldErrors,
    normalizeApiError,
    responseErrorMessage,
    errorMessage,
    createFormErrorController,
    requestJson,
    isPermissionError,
    normalizeRequestError,
    actionToken,
    csrfToken: () => cookieValue("aicrm_next_csrf"),
    prepareUnsafeForm,
  };

  installRequestSecurity();
})(window);
