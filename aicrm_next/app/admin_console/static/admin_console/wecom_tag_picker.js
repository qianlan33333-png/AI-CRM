(function () {
  "use strict";

  const DEFAULT_TITLE = "选择标签";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeId(value) {
    return String(value || "").trim();
  }

  function tagKey(tag) {
    return normalizeId((tag || {}).tag_id);
  }

  function normalizeTag(raw, fallbackGroup) {
    const id = normalizeId(raw && (raw.tag_id || raw.id || raw.value));
    if (!id) return null;
    const name = String((raw && (raw.tag_name || raw.name || raw.label)) || "").trim() || id;
    const groupName = String((raw && (raw.group_name || raw.group || raw.groupName)) || fallbackGroup || "").trim() || "未分组";
    return {
      tag_id: id,
      tag_name: name,
      group_name: groupName,
      group_id: normalizeId(raw && (raw.group_id || raw.groupId)),
    };
  }

  function normalizeCatalog(catalog) {
    const groups = [];
    const seenGroups = new Set();
    const seenTags = new Set();
    const rawGroups = Array.isArray((catalog || {}).groups) ? catalog.groups : [];
    const rawItems = Array.isArray((catalog || {}).items) ? catalog.items : [];

    rawGroups.forEach((group, index) => {
      const groupName = String((group || {}).group_name || (group || {}).name || "").trim() || "未分组";
      const groupId = normalizeId((group || {}).group_id || (group || {}).id) || `group-${index}`;
      const tags = (Array.isArray((group || {}).tags) ? group.tags : Array.isArray((group || {}).tag) ? group.tag : [])
        .map((item) => normalizeTag({ ...item, group_name: groupName, group_id: groupId }, groupName))
        .filter(Boolean)
        .filter((tag) => {
          const key = tagKey(tag);
          if (seenTags.has(key)) return false;
          seenTags.add(key);
          return true;
        });
      const groupKey = groupId || groupName;
      if (!seenGroups.has(groupKey)) {
        seenGroups.add(groupKey);
        groups.push({ group_id: groupId, group_name: groupName, tags });
      }
    });

    rawItems.forEach((item) => {
      const tag = normalizeTag(item);
      if (!tag || seenTags.has(tagKey(tag))) return;
      seenTags.add(tagKey(tag));
      const groupKey = tag.group_id || tag.group_name;
      let group = groups.find((candidate) => (candidate.group_id || candidate.group_name) === groupKey);
      if (!group) {
        group = { group_id: tag.group_id || tag.group_name, group_name: tag.group_name, tags: [] };
        groups.push(group);
      }
      group.tags.push(tag);
    });

    return groups.filter((group) => group.group_name || group.tags.length);
  }

  function normalizeValue(value) {
    const values = Array.isArray(value) ? value : value ? [value] : [];
    return values.map((item) => {
      if (typeof item === "string") return { tag_id: normalizeId(item) };
      return normalizeTag(item) || { tag_id: normalizeId((item || {}).tag_id) };
    }).filter((item) => normalizeId(item.tag_id));
  }

  function createPicker() {
    const overlay = document.createElement("div");
    overlay.className = "aicrm-tag-picker";
    overlay.hidden = true;
    overlay.innerHTML = `
      <div class="aicrm-tag-picker__panel" role="dialog" aria-modal="true" aria-labelledby="aicrm-tag-picker-title">
        <div class="aicrm-tag-picker__head">
          <h3 class="aicrm-tag-picker__title" id="aicrm-tag-picker-title">${escapeHtml(DEFAULT_TITLE)}</h3>
          <button class="aicrm-tag-picker__close" type="button" data-action="cancel" aria-label="关闭">×</button>
        </div>
        <div class="aicrm-tag-picker__search">
          <input type="search" data-role="search" placeholder="搜索标签或标签组" autocomplete="off">
        </div>
        <div class="aicrm-tag-picker__body">
          <div class="aicrm-tag-picker__groups" data-role="groups"></div>
          <div class="aicrm-tag-picker__tags" data-role="tags"></div>
          <details class="aicrm-tag-picker__manual" data-role="manual-wrap" hidden>
            <summary>找不到标签？手动填写</summary>
            <input type="text" data-role="manual-input" autocomplete="off">
          </details>
        </div>
        <div class="aicrm-tag-picker__footer">
          <div class="aicrm-tag-picker__selected" data-role="selected">已选：无</div>
          <div class="aicrm-tag-picker__actions">
            <button class="aicrm-tag-picker__button" type="button" data-action="clear">清空</button>
            <button class="aicrm-tag-picker__button" type="button" data-action="cancel">取消</button>
            <button class="aicrm-tag-picker__button aicrm-tag-picker__button--primary" type="button" data-action="confirm">确认选择</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
  }

  let overlay = null;
  let els = null;
  let state = null;

  function ensurePicker() {
    if (!overlay) {
      overlay = createPicker();
      els = {
        title: overlay.querySelector(".aicrm-tag-picker__title"),
        search: overlay.querySelector('[data-role="search"]'),
        groups: overlay.querySelector('[data-role="groups"]'),
        tags: overlay.querySelector('[data-role="tags"]'),
        selected: overlay.querySelector('[data-role="selected"]'),
        manualWrap: overlay.querySelector('[data-role="manual-wrap"]'),
        manualInput: overlay.querySelector('[data-role="manual-input"]'),
      };
      bindPicker();
    }
    return overlay;
  }

  function selectedMap() {
    return new Map((state.selected || []).map((tag) => [tagKey(tag), tag]));
  }

  function findCatalogTag(id) {
    const normalized = normalizeId(id);
    for (const group of state.groups) {
      const tag = (group.tags || []).find((item) => tagKey(item) === normalized);
      if (tag) return tag;
    }
    return null;
  }

  function ensureSelectedTag(tag) {
    const id = tagKey(tag);
    if (!id) return null;
    return findCatalogTag(id) || tag;
  }

  function filteredGroups() {
    const keyword = String(state.search || "").trim().toLowerCase();
    if (!keyword) {
      return state.groups.map((group) => ({ ...group, visibleTags: group.tags || [] }));
    }
    return state.groups.map((group) => {
      const groupMatches = String(group.group_name || "").toLowerCase().includes(keyword);
      const matchedTags = (group.tags || []).filter((tag) => String(tag.tag_name || "").toLowerCase().includes(keyword));
      if (!groupMatches && !matchedTags.length) return null;
      return { ...group, visibleTags: groupMatches ? (group.tags || []) : matchedTags };
    }).filter(Boolean);
  }

  function groupKey(group) {
    return String((group || {}).group_id || (group || {}).group_name || "");
  }

  function selectFirstGroup(groups) {
    if (!groups.length) {
      state.activeGroupKey = "";
      return;
    }
    const hasActive = groups.some((group) => groupKey(group) === state.activeGroupKey);
    if (!hasActive) state.activeGroupKey = groupKey(groups[0]);
  }

  function selectedNames() {
    return (state.selected || []).map((tag) => {
      const known = ensureSelectedTag(tag) || tag;
      return String(known.tag_name || "").trim() || "未命名标签";
    });
  }

  function renderSelected() {
    const names = selectedNames();
    els.selected.textContent = `已选：${names.length ? names.join("、") : "无"}`;
  }

  function renderGroups(groups) {
    if (!state.groups.length) {
      els.groups.innerHTML = '<div class="aicrm-tag-picker__empty">暂无标签组</div>';
      return;
    }
    if (!groups.length) {
      els.groups.innerHTML = '<div class="aicrm-tag-picker__empty">没有匹配结果</div>';
      return;
    }
    els.groups.innerHTML = groups.map((group) => `
      <button
        class="aicrm-tag-picker__group${groupKey(group) === state.activeGroupKey ? " is-active" : ""}"
        type="button"
        data-group-key="${escapeHtml(groupKey(group))}"
      >${escapeHtml(group.group_name || "未分组")}</button>
    `).join("");
  }

  function renderTags(groups) {
    const activeGroup = groups.find((group) => groupKey(group) === state.activeGroupKey);
    const tags = activeGroup ? (activeGroup.visibleTags || []) : [];
    const selected = selectedMap();
    if (!state.groups.length) {
      els.tags.innerHTML = '<div class="aicrm-tag-picker__empty">暂无标签</div>';
      return;
    }
    if (!groups.length) {
      els.tags.innerHTML = '<div class="aicrm-tag-picker__empty">没有匹配结果</div>';
      return;
    }
    if (!tags.length) {
      els.tags.innerHTML = '<div class="aicrm-tag-picker__empty">暂无标签</div>';
      return;
    }
    els.tags.innerHTML = tags.map((tag) => {
      const id = tagKey(tag);
      return `
        <button
          class="aicrm-tag-picker__tag${selected.has(id) ? " is-active" : ""}"
          type="button"
          data-tag-key="${escapeHtml(id)}"
        >
          <span class="aicrm-tag-picker__mark" aria-hidden="true"></span>
          <span class="aicrm-tag-picker__tag-name">${escapeHtml(tag.tag_name || "未命名标签")}</span>
        </button>
      `;
    }).join("");
  }

  function renderManual() {
    const shouldShow = Boolean(state.allowManual);
    els.manualWrap.hidden = !shouldShow;
    if (!shouldShow) {
      els.manualWrap.open = false;
      els.manualInput.value = "";
    }
  }

  function render() {
    if (!state) return;
    const groups = filteredGroups();
    selectFirstGroup(groups);
    renderGroups(groups);
    renderTags(groups);
    renderSelected();
    renderManual();
  }

  function chooseTag(id) {
    const tag = findCatalogTag(id);
    if (!tag) return;
    if (state.mode === "single") {
      const alreadySelected = (state.selected || []).some((item) => tagKey(item) === tagKey(tag));
      state.selected = alreadySelected ? [] : [tag];
      render();
      return;
    }
    const selected = selectedMap();
    const idKey = tagKey(tag);
    if (selected.has(idKey)) {
      selected.delete(idKey);
    } else {
      selected.set(idKey, tag);
    }
    state.selected = [...selected.values()];
    render();
  }

  function close() {
    if (!overlay) return;
    overlay.hidden = true;
    state = null;
  }

  function confirm() {
    if (!state) return;
    const manualValue = normalizeId(els.manualInput.value);
    let selected = (state.selected || []).map(ensureSelectedTag).filter(Boolean);
    if (manualValue && state.allowManual) {
      const manualTag = findCatalogTag(manualValue) || {
        tag_id: manualValue,
        tag_name: "手动填写标签",
        group_name: "",
      };
      selected = state.mode === "single" ? [manualTag] : [...selected, manualTag];
    }
    if (typeof state.onConfirm === "function") {
      state.onConfirm(state.mode === "single" ? (selected[0] || null) : selected);
    }
    close();
  }

  function clear() {
    if (!state) return;
    state.selected = [];
    if (els.manualInput) els.manualInput.value = "";
    if (typeof state.onClear === "function") {
      state.onClear(state.mode === "single" ? null : []);
    }
    render();
  }

  function bindPicker() {
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close();
        return;
      }
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action === "cancel") close();
      if (action === "confirm") confirm();
      if (action === "clear") clear();
      const groupButton = event.target.closest("[data-group-key]");
      if (groupButton && state) {
        state.activeGroupKey = groupButton.dataset.groupKey || "";
        render();
      }
      const tagButton = event.target.closest("[data-tag-key]");
      if (tagButton && state) {
        chooseTag(tagButton.dataset.tagKey);
      }
    });
    els.search.addEventListener("input", () => {
      if (!state) return;
      state.search = els.search.value || "";
      render();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && overlay && !overlay.hidden) close();
    });
  }

  function hasUnknownValue(groups, selected) {
    const ids = new Set();
    groups.forEach((group) => (group.tags || []).forEach((tag) => ids.add(tagKey(tag))));
    return selected.some((tag) => !ids.has(tagKey(tag)));
  }

  function open(options) {
    ensurePicker();
    const groups = normalizeCatalog((options || {}).catalog || {});
    const selected = normalizeValue((options || {}).value).map((tag) => {
      const known = groups.flatMap((group) => group.tags || []).find((item) => tagKey(item) === tagKey(tag));
      return known || tag;
    });
    state = {
      title: String((options || {}).title || DEFAULT_TITLE),
      mode: (options || {}).mode === "single" ? "single" : "multiple",
      groups,
      selected,
      search: "",
      activeGroupKey: "",
      allowManual: Boolean((options || {}).allowManual) || !groups.length || hasUnknownValue(groups, selected),
      onConfirm: (options || {}).onConfirm,
      onClear: (options || {}).onClear,
    };
    els.title.textContent = state.title;
    els.search.value = "";
    els.manualInput.value = "";
    render();
    overlay.hidden = false;
    requestAnimationFrame(() => els.search.focus());
  }

  window.AICRMWeComTagPicker = {
    open,
    close,
    normalizeCatalog,
  };
})();
