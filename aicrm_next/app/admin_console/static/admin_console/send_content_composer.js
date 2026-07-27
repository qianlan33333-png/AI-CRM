(function () {
  const TYPE_FIELDS = {
    image: "image_library_ids",
    miniprogram: "miniprogram_library_ids",
    attachment: "attachment_library_ids",
    group_invite: "group_invite_library_ids",
  };
  const TYPE_LABELS = {
    image: "图片",
    miniprogram: "小程序",
    attachment: "PDF/附件",
    group_invite: "客户群",
  };
  const DEFAULT_LIMITS = { image: 3, miniprogram: 1, attachment: 9, group_invite: 1 };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function normalizeIds(value) {
    const ids = [];
    (Array.isArray(value) ? value : []).forEach((raw) => {
      const id = Number(raw);
      if (Number.isInteger(id) && id > 0 && !ids.includes(id)) ids.push(id);
    });
    return ids;
  }

  function normalizePackage(value, textEnabled) {
    const source = value && typeof value === "object" ? value : {};
    return {
      content_text: textEnabled ? String(source.content_text || "").trim() : "",
      image_library_ids: normalizeIds(source.image_library_ids),
      miniprogram_library_ids: normalizeIds(source.miniprogram_library_ids),
      attachment_library_ids: normalizeIds(source.attachment_library_ids),
      group_invite_library_ids: normalizeIds(source.group_invite_library_ids),
    };
  }

  function countMaterials(value) {
    return normalizeIds(value.image_library_ids).length
      + normalizeIds(value.miniprogram_library_ids).length
      + normalizeIds(value.attachment_library_ids).length
      + normalizeIds(value.group_invite_library_ids).length;
  }

  async function previewPackage(contentPackage, textEnabled) {
    const response = await fetch("/api/admin/send-content/preview", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        content_package: contentPackage,
        text_enabled: textEnabled,
        require_body: false,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || data.detail || "预览失败");
    return data.preview || {};
  }

  async function validatePackage(contentPackage, textEnabled) {
    const response = await fetch("/api/admin/send-content/validate", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        content_package: contentPackage,
        text_enabled: textEnabled,
        require_body: false,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || data.detail || "内容格式不正确");
    return data.content_package || contentPackage;
  }

  function open(options) {
    options = options || {};
    const textEnabled = options.textEnabled !== false;
    const limits = { ...DEFAULT_LIMITS, ...(options.limits || {}) };
    const onConfirm = typeof options.onConfirm === "function" ? options.onConfirm : function () {};
    const onCancel = typeof options.onCancel === "function" ? options.onCancel : function () {};
    const state = {
      value: normalizePackage(options.value || {}, textEnabled),
      details: new Map(),
      closed: false,
    };

    const mask = document.createElement("div");
    mask.className = "aicrm-send-composer-mask is-open";
    mask.innerHTML = `
      <div class="aicrm-send-composer" role="dialog" aria-modal="true">
        <header class="aicrm-send-composer__head">
          <h3>${escapeHtml(options.title || "配置发送内容")}</h3>
          <div class="aicrm-send-composer__summary" data-composer-summary></div>
        </header>
        <div class="aicrm-send-composer__body">
          <div class="aicrm-send-composer__layout">
            <div class="aicrm-send-composer__main">
              ${textEnabled ? `
                <label class="aicrm-send-composer__field">
                  <span>话术</span>
                  <textarea class="aicrm-send-composer__textarea" data-composer-text maxlength="4000">${escapeHtml(state.value.content_text)}</textarea>
                </label>
                <div class="aicrm-send-composer__quick">
                  <button class="aicrm-send-composer__button is-soft" type="button" data-insert-customer-name>插入客户名</button>
                </div>
              ` : `
                <div class="aicrm-send-composer__agent-note">Agent 将为每个客户生成个性化话术</div>
              `}
              <div>
                <div class="aicrm-send-composer__section-title">素材与进群</div>
                <div class="aicrm-send-composer__material-actions">
                  <button class="aicrm-send-composer__button is-soft" type="button" data-add-material="image">+图片</button>
                  <button class="aicrm-send-composer__button is-soft" type="button" data-add-material="miniprogram">+小程序</button>
                  <button class="aicrm-send-composer__button is-soft" type="button" data-add-material="attachment">+附件</button>
                  <button class="aicrm-send-composer__button is-soft" type="button" data-add-material="group_invite">+选择群聊</button>
                </div>
              </div>
              <div>
                <div class="aicrm-send-composer__section-title">已选内容</div>
                <div class="aicrm-send-composer__selected" data-selected-list></div>
              </div>
            </div>
            <aside class="aicrm-send-composer__preview">
              <div class="aicrm-send-composer__preview-card">
                <div class="aicrm-send-composer__section-title">发送任务预览</div>
                <div class="aicrm-send-composer__bubble" data-preview-text></div>
                <div class="aicrm-send-composer__tokens" data-preview-materials></div>
              </div>
            </aside>
          </div>
        </div>
        <footer class="aicrm-send-composer__foot">
          <span class="aicrm-send-composer__summary" data-composer-error></span>
          <div class="aicrm-send-composer__actions">
            <button class="aicrm-send-composer__button" type="button" data-composer-cancel>取消</button>
            <button class="aicrm-send-composer__button is-soft" type="button" data-composer-save>保存内容</button>
            <button class="aicrm-send-composer__button is-primary" type="button" data-composer-confirm>确认</button>
          </div>
        </footer>
      </div>
    `;
    document.body.appendChild(mask);

    const textField = mask.querySelector("[data-composer-text]");
    const selectedList = mask.querySelector("[data-selected-list]");
    const summary = mask.querySelector("[data-composer-summary]");
    const error = mask.querySelector("[data-composer-error]");
    const previewText = mask.querySelector("[data-preview-text]");
    const previewMaterials = mask.querySelector("[data-preview-materials]");

    function close(cancelled) {
      if (state.closed) return;
      state.closed = true;
      mask.remove();
      if (cancelled) onCancel();
    }

    function currentPackage() {
      return normalizePackage({
        ...state.value,
        content_text: textEnabled ? (textField ? textField.value : state.value.content_text) : "",
      }, textEnabled);
    }

    function materialTitle(type, id) {
      const detail = state.details.get(`${type}:${id}`);
      return detail ? detail.title : `${TYPE_LABELS[type]} ${id}`;
    }

    function materialPreviewCard(item) {
      const type = item.type || "attachment";
      const title = item.title || `${TYPE_LABELS[type] || "素材"} ${item.library_id || ""}`;
      const subtitle = item.subtitle || TYPE_LABELS[type] || "";
      const thumb = item.thumbnail_url
        ? `<img src="${escapeHtml(item.thumbnail_url)}" alt="${escapeHtml(title)}">`
        : `<span>${escapeHtml(TYPE_LABELS[type] || "素材")}</span>`;
      if (type === "image" || type === "miniprogram" || type === "group_invite") {
        return `<article class="aicrm-send-composer__preview-material is-${escapeHtml(type)}">
          <div class="aicrm-send-composer__preview-thumb">${thumb}</div>
          <div class="aicrm-send-composer__preview-meta">
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(subtitle)}</span>
          </div>
        </article>`;
      }
      return `<article class="aicrm-send-composer__preview-material is-attachment">
        <div class="aicrm-send-composer__preview-file">PDF</div>
        <div class="aicrm-send-composer__preview-meta">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(subtitle)}</span>
        </div>
      </article>`;
    }

    function renderSelected() {
      const rows = [];
      Object.entries(TYPE_FIELDS).forEach(([type, field]) => {
        state.value[field].forEach((id) => {
          rows.push(`<div class="aicrm-send-composer__selected-item">
            <strong>${escapeHtml(materialTitle(type, id))}</strong>
            <button class="aicrm-send-composer__remove" type="button" data-remove-type="${type}" data-remove-id="${id}">移除</button>
          </div>`);
        });
      });
      selectedList.innerHTML = rows.length ? rows.join("") : `<div class="aicrm-send-composer__empty">还没有选择内容</div>`;
      summary.textContent = `已选 ${state.value.image_library_ids.length} 图片 / ${state.value.miniprogram_library_ids.length} 小程序 / ${state.value.attachment_library_ids.length} PDF/附件 / ${state.value.group_invite_library_ids.length} 客户群`;
    }

    async function renderPreview() {
      const contentPackage = currentPackage();
      state.value = contentPackage;
      renderSelected();
      if (!textEnabled) {
        previewText.textContent = "Agent 将为每个客户生成个性化话术";
      } else {
        previewText.textContent = contentPackage.content_text || "未填写话术";
      }
      previewMaterials.innerHTML = "";
      try {
        const preview = await previewPackage(contentPackage, textEnabled);
        const materials = preview.materials || [];
        previewMaterials.innerHTML = materials.length
          ? materials.map(materialPreviewCard).join("")
          : countMaterials(contentPackage)
            ? `<span class="aicrm-send-composer__token">已选择 ${countMaterials(contentPackage)} 个素材</span>`
            : "";
      } catch (_error) {
        if (countMaterials(contentPackage)) {
          previewMaterials.innerHTML = `<span class="aicrm-send-composer__token">已选择 ${countMaterials(contentPackage)} 个素材</span>`;
        }
      }
    }

    function addMaterial(item) {
      const type = item.type;
      const field = TYPE_FIELDS[type];
      if (!field) return;
      const id = Number(item.library_id || 0);
      if (!id || state.value[field].includes(id)) return;
      if (state.value[field].length >= limits[type]) {
        window.alert(`${TYPE_LABELS[type]}最多选择 ${limits[type]} 个`);
        return;
      }
      state.value[field] = state.value[field].concat(id);
      state.details.set(`${type}:${id}`, item);
      renderPreview();
    }

    async function submit() {
      error.textContent = "";
      try {
        const normalized = await validatePackage(currentPackage(), textEnabled);
        onConfirm({ ...normalized, content_text: textEnabled ? normalized.content_text : "" });
        close(false);
      } catch (submitError) {
        error.textContent = submitError.message || "保存失败";
      }
    }

    mask.addEventListener("click", (event) => {
      if (event.target.closest("[data-composer-cancel]")) {
        close(true);
        return;
      }
      if (event.target.closest("[data-insert-customer-name]") && textField) {
        const start = textField.selectionStart || textField.value.length;
        const end = textField.selectionEnd || start;
        textField.value = `${textField.value.slice(0, start)}{{客户名}}${textField.value.slice(end)}`;
        textField.focus();
        renderPreview();
        return;
      }
      const remove = event.target.closest("[data-remove-type]");
      if (remove) {
        const type = remove.dataset.removeType;
        const id = Number(remove.dataset.removeId || 0);
        const field = TYPE_FIELDS[type];
        state.value[field] = state.value[field].filter((item) => Number(item) !== id);
        renderPreview();
        return;
      }
      const addButton = event.target.closest("[data-add-material]");
      if (addButton) {
        error.textContent = "";
        const type = addButton.dataset.addMaterial || "image";
        const field = TYPE_FIELDS[type];
        if (!field) return;
        if (state.value[field].length >= limits[type]) {
          window.alert(`${TYPE_LABELS[type]}最多选择 ${limits[type]} 个`);
          return;
        }
        if (!window.AICRMMaterialPicker || typeof window.AICRMMaterialPicker.open !== "function") {
          error.textContent = "内容选择器未加载，请刷新页面后重试";
          return;
        }
        window.AICRMMaterialPicker.open({
          type,
          title: `选择${TYPE_LABELS[type]}`,
          selectedIds: state.value[field],
          limit: limits[type],
          onConfirm: addMaterial,
        });
        return;
      }
      if (event.target.closest("[data-composer-save]") || event.target.closest("[data-composer-confirm]")) {
        submit();
      }
    });
    if (textField) textField.addEventListener("input", renderPreview);

    renderPreview();
  }

  window.AICRMSendContentComposer = { open };
})();
