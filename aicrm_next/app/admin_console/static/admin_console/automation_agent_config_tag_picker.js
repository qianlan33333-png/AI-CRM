(function () {
  "use strict";

  const AutomationAgentConfig = window.AutomationAgentConfig || {};
  window.AutomationAgentConfig = AutomationAgentConfig;

  const state = AutomationAgentConfig.state || {};

  function normalizeTagId(value) {
    return String(value || "").trim();
  }

  function buildUnknownTag(tagId, tagName, groupName) {
    const normalizedTagId = normalizeTagId(tagId);
    return {
      tag_id: normalizedTagId,
      tag_name: String(tagName || "").trim() || "未匹配标签",
      group_name: String(groupName || "").trim(),
    };
  }

  function ensureTagKnown(tagId, fallbackTag) {
    const normalizedTagId = normalizeTagId(tagId);
    if (!normalizedTagId) return null;
    return state.availableTagMap.get(normalizedTagId) || fallbackTag || buildUnknownTag(normalizedTagId);
  }

  function formatTagGroupName(tag) {
    return String(((tag || {}).group_name) || "").trim();
  }

  function formatTagLabel(tag) {
    if (!tag) return "";
    const groupName = formatTagGroupName(tag);
    const tagName = String(tag.tag_name || "未命名标签").trim();
    return `${groupName ? `${groupName} / ` : ""}${tagName}`;
  }

  function buildTagBadge(tag) {
    const escapeHtml = AutomationAgentConfig.escapeHtml;
    if (!tag || !normalizeTagId(tag.tag_id)) {
      return '<span class="ac-config-tag-picker-note">未配置扫码自动打标签</span>';
    }
    const normalizedTagId = normalizeTagId(tag.tag_id);
    const isUnknown = !state.availableTagMap.has(normalizedTagId);
    return `<span class="ac-config-tag-badge${isUnknown ? " is-unknown" : ""}">${escapeHtml(formatTagLabel(tag))}</span>`;
  }

  function renderSelectedTags() {
    const elements = AutomationAgentConfig.elements();
    const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
    if (!elements.defaultChannelEntryTagDisplay) return;
    elements.defaultChannelEntryTagDisplay.innerHTML = buildTagBadge(state.defaultChannelSelectedTag);
    if (defaultChannelFields.entryTagId) {
      defaultChannelFields.entryTagId.value = normalizeTagId((state.defaultChannelSelectedTag || {}).tag_id);
    }
  }

  function setDefaultChannelSelectedTag(tag) {
    const normalizedTagId = normalizeTagId((tag || {}).tag_id);
    state.defaultChannelSelectedTag = normalizedTagId ? ensureTagKnown(normalizedTagId, tag) : null;
    renderSelectedTags();
  }

  function currentTagModalSelection() {
    return normalizeTagId((state.defaultChannelSelectedTag || {}).tag_id);
  }

  function selectTag(tagId) {
    setDefaultChannelSelectedTag(ensureTagKnown(tagId, buildUnknownTag(tagId)));
  }

  function unselectTag(tagId) {
    if (!tagId || normalizeTagId((state.defaultChannelSelectedTag || {}).tag_id) === normalizeTagId(tagId)) {
      setDefaultChannelSelectedTag(null);
    }
  }

  function renderTagGroups() {
    renderSelectedTags();
  }

  function openTagPicker() {
    if (!window.AICRMWeComTagPicker) {
      AutomationAgentConfig.showDefaultChannelFeedback?.("标签选择器加载失败，请刷新后重试", "error");
      return;
    }
    const currentTagId = normalizeTagId((state.defaultChannelSelectedTag || {}).tag_id);
    state.tagModal.open = true;
    window.AICRMWeComTagPicker.open({
      title: "选择默认入池标签",
      mode: "single",
      value: currentTagId
        ? (state.defaultChannelSelectedTag || buildUnknownTag(currentTagId))
        : null,
      catalog: { items: state.availableTags },
      allowManual: !state.availableTags.length || (currentTagId && !state.availableTagMap.has(currentTagId)),
      onConfirm: (tag) => {
        const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
        if (defaultChannelFields.entryTagIdManual) defaultChannelFields.entryTagIdManual.value = "";
        setDefaultChannelSelectedTag(tag);
        state.tagModal.open = false;
      },
      onClear: () => {
        const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
        if (defaultChannelFields.entryTagIdManual) defaultChannelFields.entryTagIdManual.value = "";
        setDefaultChannelSelectedTag(null);
      },
    });
  }

  function closeTagPicker() {
    state.tagModal.open = false;
    window.AICRMWeComTagPicker?.close();
  }

  function confirmTagSelection() {
    closeTagPicker();
  }

  async function loadWeComTags() {
    const apiUrls = AutomationAgentConfig.getApiUrls();
    const elements = AutomationAgentConfig.elements();
    if (!apiUrls.wecom_tags) return;
    try {
      const result = await AutomationAgentConfig.requestJson(apiUrls.wecom_tags, { credentials: "same-origin" });
      state.availableTags = Array.isArray(result.items) ? result.items : [];
      state.availableTagMap = new Map(state.availableTags.map((item) => [normalizeTagId(item.tag_id), item]));
      if (elements.defaultChannelEntryTagHelp) {
        elements.defaultChannelEntryTagHelp.textContent = state.availableTags.length
          ? "从已有企微标签和标签组中选择。"
          : "当前未获取到企微标签，可稍后重试。";
      }
    } catch (_error) {
      state.availableTags = [];
      state.availableTagMap = new Map();
      if (elements.defaultChannelEntryTagHelp) {
        elements.defaultChannelEntryTagHelp.textContent = "企微标签列表加载失败，可稍后重试。";
      }
    }
    if (state.defaultChannelSelectedTag) {
      setDefaultChannelSelectedTag(state.defaultChannelSelectedTag);
    }
    if (state.tagModal.open) {
      renderTagGroups();
    }
  }

  function bindTagPickerInteractions() {
    const elements = AutomationAgentConfig.elements();
    const defaultChannelFields = AutomationAgentConfig.defaultChannelFields();
    if (elements.defaultChannelEntryTagPickButton) {
      elements.defaultChannelEntryTagPickButton.addEventListener("click", function () {
        openTagPicker();
      });
    }

    if (elements.defaultChannelEntryTagClearButton) {
      elements.defaultChannelEntryTagClearButton.addEventListener("click", function () {
        if (defaultChannelFields.entryTagIdManual) defaultChannelFields.entryTagIdManual.value = "";
        setDefaultChannelSelectedTag(null);
      });
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && state.tagModal.open) {
        closeTagPicker();
      }
    });
  }

  AutomationAgentConfig.normalizeTagId = normalizeTagId;
  AutomationAgentConfig.buildUnknownTag = buildUnknownTag;
  AutomationAgentConfig.ensureTagKnown = ensureTagKnown;
  AutomationAgentConfig.formatTagGroupName = formatTagGroupName;
  AutomationAgentConfig.formatTagLabel = formatTagLabel;
  AutomationAgentConfig.buildTagBadge = buildTagBadge;
  AutomationAgentConfig.renderSelectedTags = renderSelectedTags;
  AutomationAgentConfig.setDefaultChannelSelectedTag = setDefaultChannelSelectedTag;
  AutomationAgentConfig.currentTagModalSelection = currentTagModalSelection;
  AutomationAgentConfig.filterTags = function filterTags(query) {
    const keyword = String(query || "").trim().toLowerCase();
    return state.availableTags.filter((tag) => {
      if (!keyword) return true;
      return `${tag.group_name || ""} ${tag.tag_name || ""}`.toLowerCase().includes(keyword);
    });
  };
  AutomationAgentConfig.renderTagGroups = renderTagGroups;
  AutomationAgentConfig.selectTag = selectTag;
  AutomationAgentConfig.unselectTag = unselectTag;
  AutomationAgentConfig.openTagPicker = openTagPicker;
  AutomationAgentConfig.closeTagPicker = closeTagPicker;
  AutomationAgentConfig.confirmTagSelection = confirmTagSelection;
  AutomationAgentConfig.loadWeComTags = loadWeComTags;
  AutomationAgentConfig.bindTagPickerInteractions = bindTagPickerInteractions;
})();
