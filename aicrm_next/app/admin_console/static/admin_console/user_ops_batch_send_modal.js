(function () {
  const DEFAULT_PREVIEW_URL = "/api/admin/user-ops/batch-send/preview";
  const DEFAULT_EXECUTE_URL = "/api/admin/user-ops/batch-send/execute";
  const EMPTY_PACKAGE = {
    content_text: "",
    image_library_ids: [],
    miniprogram_library_ids: [],
    attachment_library_ids: [],
    group_invite_library_ids: [],
  };

  let state = {
    targetSource: "",
    targetSourceId: null,
    targetLabel: "",
    previewUrl: DEFAULT_PREVIEW_URL,
    executeUrl: DEFAULT_EXECUTE_URL,
    operator: "admin",
  };

  function normalizeIds(value) {
    const ids = [];
    (Array.isArray(value) ? value : []).forEach((raw) => {
      const id = Number(raw);
      if (Number.isInteger(id) && id > 0 && !ids.includes(id)) ids.push(id);
    });
    return ids;
  }

  function normalizeContentPackage(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
      content_text: String(source.content_text || "").trim(),
      image_library_ids: normalizeIds(source.image_library_ids),
      miniprogram_library_ids: normalizeIds(source.miniprogram_library_ids),
      attachment_library_ids: normalizeIds(source.attachment_library_ids),
      group_invite_library_ids: normalizeIds(source.group_invite_library_ids),
    };
  }

  function packageHasBody(contentPackage) {
    return Boolean(
      contentPackage.content_text
      || contentPackage.image_library_ids.length
      || contentPackage.miniprogram_library_ids.length
      || contentPackage.attachment_library_ids.length
      || contentPackage.group_invite_library_ids.length
    );
  }

  function requestImages(contentPackage) {
    return contentPackage.image_library_ids.map((id) => ({ library_id: id }));
  }

  function requestAttachments(contentPackage) {
    return [
      ...contentPackage.miniprogram_library_ids.map((id) => ({
        msgtype: "miniprogram",
        miniprogram: { library_id: id },
      })),
      ...contentPackage.attachment_library_ids.map((id) => ({
        msgtype: "file",
        file: { library_id: id },
      })),
      ...contentPackage.group_invite_library_ids.map((id) => ({
        msgtype: "link",
        link: { library_id: id },
      })),
    ];
  }

  function buildRequest(contentPackage, confirm) {
    return {
      target_source: state.targetSource,
      target_source_id: state.targetSourceId,
      selection_mode: "all_filtered",
      filters: {},
      selected_ids: [],
      excluded_ids: [],
      content: contentPackage.content_text,
      images: requestImages(contentPackage),
      attachments: requestAttachments(contentPackage),
      include_do_not_disturb: false,
      confirm: Boolean(confirm),
      operator: state.operator || "admin",
    };
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.detail || `HTTP ${response.status}`);
    }
    return data;
  }

  async function previewAndExecute(contentPackage) {
    if (!packageHasBody(contentPackage)) {
      window.alert("请先填写话术或选择素材");
      return;
    }
    const preview = await postJson(state.previewUrl, buildRequest(contentPackage, false));
    const eligibleCount = Number(preview.eligible_count || 0);
    if (eligibleCount <= 0) {
      window.alert("当前没有可发送目标");
      return;
    }
    const result = await postJson(state.executeUrl, buildRequest(contentPackage, true));
    window.alert(`群发任务已创建，记录 #${result.record_id || "-"}`);
  }

  function open(options) {
    if (!window.AICRMSendContentComposer || typeof window.AICRMSendContentComposer.open !== "function") {
      window.alert("标准发送内容组件加载失败，请刷新页面后重试");
      return;
    }
    state = {
      targetSource: options.targetSource || "",
      targetSourceId: options.targetSourceId,
      targetLabel: options.targetLabel || "AI 人群包",
      previewUrl: options.previewUrl || DEFAULT_PREVIEW_URL,
      executeUrl: options.executeUrl || DEFAULT_EXECUTE_URL,
      operator: options.operator || "admin",
    };
    window.AICRMSendContentComposer.open({
      title: "配置欢迎语和素材",
      textEnabled: true,
      value: normalizeContentPackage(options.value || EMPTY_PACKAGE),
      limits: {
        image: 3,
        miniprogram: 1,
        attachment: 9,
        group_invite: 1,
      },
      onConfirm(contentPackage) {
        previewAndExecute(normalizeContentPackage(contentPackage)).catch((error) => {
          window.alert(error.message || "群发任务创建失败");
        });
      },
    });
  }

  window.UserOpsBatchSendModal = { open };
})();
