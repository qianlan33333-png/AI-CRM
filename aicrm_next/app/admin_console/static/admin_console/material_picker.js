(function () {
  const TYPE_LABELS = {
    image: "图片",
    miniprogram: "小程序",
    attachment: "PDF/附件",
    group_invite: "客户群",
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function normalizeItem(raw) {
    const metadata = raw.metadata && typeof raw.metadata === "object" ? raw.metadata : {};
    return {
      type: String(raw.type || ""),
      library_id: Number(raw.library_id || 0),
      title: String(raw.title || ""),
      subtitle: String(raw.subtitle || ""),
      thumbnail_url: String(raw.thumbnail_url || ""),
      enabled: raw.enabled !== false,
      selectable: raw.selectable !== false && raw.enabled !== false,
      mime_type: String(raw.mime_type || metadata.mime_type || ""),
      file_name: String(raw.file_name || metadata.file_name || raw.title || ""),
      file_size: Number(raw.file_size || metadata.file_size || 0),
      metadata,
    };
  }

  async function fetchItems(type, q) {
    const params = new URLSearchParams({ type, q: q || "", enabled_only: "true", limit: "50", offset: "0" });
    const response = await fetch(`/api/admin/material-picker/items?${params.toString()}`, {
      headers: { Accept: "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.detail || "素材列表加载失败");
    }
    return (data.items || []).map(normalizeItem);
  }

  function open(options) {
    options = options || {};
    const type = String(options.type || "image");
    if (!TYPE_LABELS[type]) throw new Error("未知素材类型");
    if (type === "group_invite") {
      if (!window.AICRMGroupChatPicker) throw new Error("群聊选择器未加载");
      window.AICRMGroupChatPicker.open(options);
      return;
    }
    const selectedIds = new Set((options.selectedIds || []).map((id) => Number(id)));
    const limit = Number(options.limit || 1);
    const allowedMimeTypes = new Set((options.allowedMimeTypes || []).map((value) => String(value || "").trim()).filter(Boolean));
    const onConfirm = typeof options.onConfirm === "function" ? options.onConfirm : function () {};
    const onCancel = typeof options.onCancel === "function" ? options.onCancel : function () {};

    let items = [];
    let query = "";
    let closed = false;

    const mask = document.createElement("div");
    mask.className = "aicrm-material-picker-mask is-open";
    mask.innerHTML = `
      <div class="aicrm-material-picker" role="dialog" aria-modal="true">
        <header class="aicrm-material-picker__head">
          <div>
            <h3>${escapeHtml(options.title || `选择${TYPE_LABELS[type]}`)}</h3>
            <p>最多 ${limit} 个，已选项会高亮显示。</p>
          </div>
          <button class="aicrm-material-picker__button" type="button" data-picker-close>取消</button>
        </header>
        <div class="aicrm-material-picker__tools">
          <input class="aicrm-material-picker__search" data-picker-search placeholder="搜索${TYPE_LABELS[type]}" aria-label="搜索${TYPE_LABELS[type]}">
          <button class="aicrm-material-picker__button is-primary" type="button" data-picker-refresh>搜索</button>
        </div>
        <div class="aicrm-material-picker__body">
          <div class="aicrm-material-picker__empty" data-picker-empty>正在加载${escapeHtml(TYPE_LABELS[type])}...</div>
          <div class="aicrm-material-picker__grid" data-picker-grid></div>
        </div>
      </div>
    `;
    document.body.appendChild(mask);

    const grid = mask.querySelector("[data-picker-grid]");
    const empty = mask.querySelector("[data-picker-empty]");
    const search = mask.querySelector("[data-picker-search]");

    function close(cancelled) {
      if (closed) return;
      closed = true;
      mask.remove();
      if (cancelled) onCancel();
    }

    function render() {
      if (!items.length) {
        empty.hidden = false;
        empty.textContent = "没有可选素材";
        grid.innerHTML = "";
        return;
      }
      empty.hidden = true;
      grid.innerHTML = items.map((item) => {
        const selected = selectedIds.has(item.library_id);
        const thumb = item.thumbnail_url
          ? `<img src="${escapeHtml(item.thumbnail_url)}" alt="">`
          : `<span>${escapeHtml(TYPE_LABELS[item.type] || "素材")}</span>`;
        const disabled = !item.selectable || !item.library_id;
        return `<button class="aicrm-material-picker__item${selected ? " is-selected" : ""}${disabled ? " is-disabled" : ""}" type="button" ${disabled ? "disabled" : `data-picker-id="${item.library_id}"`}>
          <span class="aicrm-material-picker__thumb">${thumb}</span>
          <span class="aicrm-material-picker__title">${escapeHtml(item.title || `${TYPE_LABELS[type]} ${item.library_id}`)}</span>
          <span class="aicrm-material-picker__subtitle">${escapeHtml(item.subtitle || "")}</span>
        </button>`;
      }).join("");
    }

    async function load() {
      empty.hidden = false;
      empty.textContent = `正在加载${TYPE_LABELS[type]}...`;
      grid.innerHTML = "";
      try {
        items = await fetchItems(type, query);
        if (allowedMimeTypes.size) {
          items = items.filter((item) => allowedMimeTypes.has(String(item.mime_type || item.metadata?.mime_type || "")));
        }
        render();
      } catch (error) {
        items = [];
        empty.hidden = false;
        empty.textContent = error.message || "素材列表加载失败";
        grid.innerHTML = "";
      }
    }

    mask.addEventListener("click", (event) => {
      if (event.target === mask || event.target.closest("[data-picker-close]")) {
        close(true);
        return;
      }
      const itemButton = event.target.closest("[data-picker-id]");
      if (!itemButton) return;
      const id = Number(itemButton.dataset.pickerId || 0);
      const item = items.find((entry) => Number(entry.library_id) === id);
      if (!item) return;
      if (!selectedIds.has(id) && selectedIds.size >= limit) {
        window.alert(`${TYPE_LABELS[type]}最多选择 ${limit} 个`);
        return;
      }
      onConfirm(item);
      close(false);
    });
    mask.querySelector("[data-picker-refresh]").addEventListener("click", () => {
      query = search.value || "";
      load();
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        query = search.value || "";
        load();
      }
    });

    load();
  }

  window.AICRMMaterialPicker = { open };
})();
