(function (window) {
  "use strict";

  function text(value) {
    return String(value == null ? "" : value).trim();
  }

  function defaultTarget(url = "") {
    const h5Url = text(url);
    return {
      enabled: Boolean(h5Url),
      target_type: "h5",
      open_strategy: "h5_redirect",
      h5_url: h5Url,
      fallback_url: "",
      mini_program: { appid: "", username: "", path: "", query: "", env_version: "release" },
      url_link: {
        enabled: false,
        url: "",
        source_url: "",
        response_url_key: "url_link",
        expire_type: 0,
        expire_interval: 30,
      },
    };
  }

  function normalize(value, fallbackUrl = "") {
    const base = defaultTarget(fallbackUrl);
    const target = { ...base, ...(value || {}) };
    target.mini_program = { ...base.mini_program, ...(target.mini_program || {}) };
    target.url_link = { ...base.url_link, ...(target.url_link || {}) };
    if (!["h5", "url_link"].includes(target.target_type)) target.target_type = "h5";
    target.open_strategy = target.target_type === "url_link" ? "url_link" : "h5_redirect";
    target.url_link.enabled = target.target_type === "url_link" && Boolean(target.url_link.url || target.url_link.source_url);
    return target;
  }

  function mount(root, initialValue, options = {}) {
    if (!root) return null;
    const enabledEl = root.querySelector("[data-target-enabled]");
    const bodyEl = root.querySelector("[data-target-body]");
    const warningEl = root.querySelector("[data-validation-warning]");
    const typeEl = root.querySelector('[name="completion_target_type"]');
    const h5UrlEl = root.querySelector('[name="completion_h5_url"]');
    const sourceUrlEl = root.querySelector('[name="completion_url_link_source_url"]');
    const responseKeyEl = root.querySelector('[name="completion_url_link_response_key"]');
    if (!enabledEl || !bodyEl || !warningEl || !typeEl || !h5UrlEl) return null;

    function syncVisibility() {
      bodyEl.hidden = !enabledEl.checked;
      const isUrlLink = typeEl.value === "url_link";
      root.querySelectorAll("[data-h5-url-fields]").forEach((element) => { element.hidden = isUrlLink; });
      root.querySelectorAll("[data-url-link-fields]").forEach((element) => { element.hidden = !isUrlLink; });
    }

    function collect(collectOptions = {}) {
      const validate = collectOptions.validate !== false;
      const enabled = Boolean(enabledEl.checked);
      const targetType = typeEl.value === "url_link" ? "url_link" : "h5";
      const h5Url = targetType === "h5" ? text(h5UrlEl.value) : "";
      const sourceUrl = sourceUrlEl ? text(sourceUrlEl.value) : "";
      const responseUrlKey = responseKeyEl ? text(responseKeyEl.value) || "url_link" : "url_link";
      const payload = {
        ...defaultTarget(),
        enabled,
        target_type: targetType,
        open_strategy: targetType === "url_link" ? "url_link" : "h5_redirect",
        h5_url: h5Url,
        url_link: {
          ...defaultTarget().url_link,
          enabled: targetType === "url_link" && Boolean(sourceUrl),
          source_url: sourceUrl,
          response_url_key: responseUrlKey,
        },
      };
      warningEl.hidden = true;
      warningEl.textContent = "";
      if (validate && enabled && targetType === "h5" && !h5Url) {
        warningEl.textContent = options.h5RequiredMessage || "请填写 H5 跳转地址。";
      } else if (validate && enabled && targetType === "url_link" && !sourceUrl) {
        warningEl.textContent = options.urlLinkRequiredMessage || "请填写动态 URL Link 接口。";
      }
      if (warningEl.textContent) {
        warningEl.hidden = false;
        throw new Error(warningEl.textContent);
      }
      return payload;
    }

    function notify() {
      const target = collect({ validate: false });
      if (typeof options.onChange === "function") options.onChange(target);
    }

    function setValue(value) {
      const next = normalize(value, options.fallbackUrl || "");
      enabledEl.checked = Boolean(next.enabled);
      typeEl.value = next.target_type === "url_link" ? "url_link" : "h5";
      h5UrlEl.value = next.h5_url || next.fallback_url || text(options.fallbackUrl);
      if (sourceUrlEl) sourceUrlEl.value = next.url_link.source_url || "";
      if (responseKeyEl) responseKeyEl.value = next.url_link.response_url_key || "url_link";
      syncVisibility();
    }

    setValue(initialValue);

    root.collectCompletionTarget = collect;
    root.setCompletionTarget = setValue;
    root.addEventListener("input", notify);
    root.addEventListener("change", () => {
      syncVisibility();
      notify();
    });
    return { collect, setValue, syncVisibility };
  }

  window.AICRMCompletionTargetConfig = { defaultTarget, normalize, mount };
})(window);
