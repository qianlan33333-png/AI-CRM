(function () {
  function normalizeIdList(value) {
    const raw = Array.isArray(value) ? value : String(value || "").split(",");
    const ids = [];
    raw.forEach((item) => {
      const id = parseInt(String(item).trim(), 10);
      if (id > 0 && ids.indexOf(id) === -1) ids.push(id);
    });
    return ids;
  }

  function welcomeFieldsToContentPackage(fields) {
    const data = fields || {};
    return {
      content_text: String(data.welcome_message || "").trim(),
      image_library_ids: normalizeIdList(data.welcome_image_library_ids),
      miniprogram_library_ids: normalizeIdList(data.welcome_miniprogram_library_ids),
      attachment_library_ids: normalizeIdList(data.welcome_attachment_library_ids),
      group_invite_library_ids: normalizeIdList(data.welcome_group_invite_library_ids),
    };
  }

  function contentPackageToWelcomeFields(contentPackage) {
    const data = contentPackage || {};
    return {
      welcome_message: String(data.content_text || "").trim(),
      welcome_image_library_ids: normalizeIdList(data.image_library_ids),
      welcome_miniprogram_library_ids: normalizeIdList(data.miniprogram_library_ids),
      welcome_attachment_library_ids: normalizeIdList(data.attachment_library_ids),
      welcome_group_invite_library_ids: normalizeIdList(data.group_invite_library_ids),
    };
  }

  if (typeof window !== "undefined") {
    window.AICRMChannelWelcomeAdapter = {
      normalizeIdList,
      welcomeFieldsToContentPackage,
      contentPackageToWelcomeFields,
    };
  }

  const root = document.querySelector("[data-channel-admission-page]");
  if (!root) return;

  const bootstrapNode = root.querySelector("[data-channel-bootstrap]");
  const bootstrap = bootstrapNode ? JSON.parse(bootstrapNode.textContent || "{}") : {};
  const bootstrapChannel = bootstrap.channel || {};
  const adminToken = root.dataset.adminToken || "";
  const state = {
    activeChannelPanel: "basic",
  };

  function bySelector(selector, scope) {
    return Array.from((scope || root).querySelectorAll(selector));
  }

  function toast(message) {
    root.dataset.lastToast = message;
    if (window.AdminConsole && typeof window.AdminConsole.showToast === "function") {
      window.AdminConsole.showToast(message);
    }
  }

  function setSaveFeedback(message, tone) {
    const node = root.querySelector("[data-channel-save-feedback]");
    if (!node) return;
    const text = String(message || "").trim();
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle("is-error", tone === "error");
  }

  function safeInit(name, fn) {
    try {
      fn();
    } catch (error) {
      root.dataset[`init${name}Error`] = error.message || "init failed";
    }
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function intList(value) {
    return normalizeIdList(value);
  }

  function setIds(input, ids) {
    if (!input) return;
    input.value = normalizeIdList(ids).join(",");
  }

  function apiJson(url, options) {
    return fetch(url, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
    }).then((response) => response.json().then((data) => ({ response, data })));
  }

  function apiErrorMessage(data, fallback) {
    const detail = data && data.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      return detail.reason || detail.error || detail.message || detail.error_code || fallback;
    }
    if (data && typeof data.error === "string" && data.error) return data.error;
    if (data && typeof data.reason === "string" && data.reason) return data.reason;
    return fallback;
  }

  function urlFromBase(base, id) {
    return String(base || "").replace(/\/0($|[/?#])/, "/" + id + "$1");
  }

  function linkValueFrom(text) {
    return String(text || "").trim();
  }

  function copyText(text) {
    const value = linkValueFrom(text);
    if (!value) {
      toast("没有可复制链接");
      return Promise.resolve(false);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value).then(
        () => {
          toast("链接已复制");
          return true;
        },
        () => fallbackCopy(value)
      );
    }
    return Promise.resolve(fallbackCopy(value));
  }

  function fallbackCopy(text) {
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "readonly");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (error) {
      ok = false;
    }
    input.remove();
    toast(ok ? "链接已复制" : "请手动复制链接");
    return ok;
  }

  function shareText(text) {
    const value = linkValueFrom(text);
    if (!value) return copyText(value);
    if (navigator.share) {
      return navigator.share({ title: "企微获客助手链接", url: value }).catch(() => copyText(value));
    }
    return copyText(value);
  }

  function finalUrl(linkUrl, customerChannel) {
    const link = linkValueFrom(linkUrl);
    const channel = linkValueFrom(customerChannel);
    if (!link || !channel) return link;
    try {
      const url = new URL(link, window.location.origin);
      url.searchParams.set("customer_channel", channel);
      return url.toString();
    } catch (error) {
      return link + (link.indexOf("?") >= 0 ? "&" : "?") + "customer_channel=" + encodeURIComponent(channel);
    }
  }

  function statusLabel(value) {
    return {
      active: "启用",
      inactive: "停用",
      paused: "暂停",
      archived: "归档",
    }[value] || value || "-";
  }

  function renderImportResult(data) {
    if (!data || typeof data !== "object") return "导入任务已处理";
    const lines = [];
    if (data.dry_run === true) lines.push("导入模式：只做预估");
    if (data.dry_run === false) lines.push("导入模式：确认导入");
    if (typeof data.planned_count !== "undefined") lines.push("预计导入人数：" + data.planned_count);
    if (typeof data.imported_count !== "undefined") lines.push("已导入人数：" + data.imported_count);
    if (typeof data.created_count !== "undefined") lines.push("新建人数：" + data.created_count);
    if (typeof data.skipped_count !== "undefined") lines.push("跳过人数：" + data.skipped_count);
    if (data.reason) lines.push("处理说明：" + data.reason);
    if (Array.isArray(data.errors) && data.errors.length) lines.push("错误：" + data.errors.join("；"));
    return lines.length ? lines.join("\n") : "导入任务已处理";
  }

  function setupChannelSearch() {
    const input = root.querySelector("[data-channel-search]");
    if (!input) return;
    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      bySelector("[data-channel-row]").forEach((row) => {
        const text = row.dataset.searchText || "";
        row.hidden = query && text.indexOf(query) === -1;
      });
    });
  }

  function setupCopyShare() {
    bySelector("[data-copy-channel-link]").forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copyText));
    });
    bySelector("[data-share-channel-link]").forEach((button) => {
      button.addEventListener("click", () => shareText(button.dataset.copyText));
    });
  }

  function setupChannelDrawer() {
    const drawer = root.querySelector("[data-channel-drawer]");
    if (!drawer) return;
    const body = root.querySelector("[data-channel-drawer-body]");
    bySelector("[data-open-channel-drawer]").forEach((button) => {
      button.addEventListener("click", () => {
        const row = button.closest("[data-channel-row]");
        const channelId = row ? row.dataset.channelId : "";
        body.innerHTML = row
          ? "<p><strong>" + row.querySelector("strong").textContent + "</strong></p><p class=\"channel-muted\">正在加载渠道用户列表...</p>"
          : "<p>暂无详情。</p>";
        drawer.hidden = false;
        if (!channelId) return;
        const urls = (bootstrap.api_urls || {});
        Promise.all([
          apiJson(urlFromBase(urls.contacts_base, channelId) + "?limit=20", { method: "GET" }).catch(() => ({ data: { contacts: [] } })),
        ]).then(([contactsResult]) => {
          const contacts = (contactsResult.data || {}).contacts || [];
          const contactRows = contacts.length
            ? contacts.map((item) => "<tr><td>" + (item.display_name || item.name || item.external_contact_id || "-") + "</td><td>" + (item.enter_count || 0) + "</td><td>" + (item.last_channel_entered_at || "-") + "</td></tr>").join("")
            : "<tr><td colspan=\"3\">暂无渠道用户。</td></tr>";
          body.innerHTML =
            "<p><strong>" + row.querySelector("strong").textContent + "</strong></p>" +
            "<p class=\"channel-muted\">以下为最近渠道用户。</p>" +
            "<h3>渠道用户列表</h3>" +
            "<table class=\"admin-table channel-table\"><thead><tr><th>客户</th><th>进入次数</th><th>最近进入</th></tr></thead><tbody>" +
            contactRows +
            "</tbody></table>";
        });
      });
    });
    root.querySelector("[data-close-channel-drawer]")?.addEventListener("click", () => {
      drawer.hidden = true;
    });
  }

  function updateTypeVisibility() {
    const selected = root.querySelector('[name="channel_type"]:checked') || root.querySelector("[data-channel-type-select]");
    const value = selected ? selected.value : "qrcode";
    const isLink = value === "wecom_customer_acquisition";
    bySelector("[data-channel-type-card]").forEach((card) => {
      card.classList.toggle("active", card.dataset.channelTypeCard === value);
    });
    bySelector("[data-link-field], [data-link-section]").forEach((node) => {
      node.hidden = !isLink;
    });
    bySelector("[data-qrcode-field], [data-qrcode-section]").forEach((node) => {
      node.hidden = isLink;
    });
    const typeText = isLink ? "渠道获客链接" : "普通二维码";
    const carrierPill = root.querySelector("[data-carrier-pill]");
    const summaryType = root.querySelector("[data-summary-channel-type]");
    if (carrierPill) carrierPill.textContent = typeText;
    if (summaryType) summaryType.textContent = typeText;
    const form = root.querySelector("[data-channel-form]");
    if (form) {
      const linkUrl = form.querySelector('[name="link_url"]')?.value || "";
      const customerChannel = form.querySelector('[name="customer_channel"]')?.value || "";
      const preview = root.querySelector("[data-link-preview]");
      const finalInput = form.querySelector('[name="final_url"]');
      const computed = finalUrl(linkUrl, customerChannel);
      if (preview && "value" in preview && computed && isLink) preview.value = computed;
      if (preview && !("value" in preview)) preview.textContent = computed || "";
      if (finalInput && computed && isLink) finalInput.value = computed;
    }
  }

  function updateFormSummary() {
    const form = root.querySelector("[data-channel-form]");
    const name = form?.querySelector('[name="channel_name"]')?.value || bootstrapChannel.channel_name || "未命名渠道";
    const status = form?.querySelector('[name="status"]')?.value || bootstrapChannel.status || "active";
    const nameNode = root.querySelector("[data-summary-channel-name]");
    const statusNode = root.querySelector("[data-summary-channel-status]");
    const assigneeNode = root.querySelector("[data-summary-assignee-count]");
    if (nameNode) nameNode.textContent = name || "未命名渠道";
    if (statusNode) statusNode.textContent = statusLabel(status);
    if (assigneeNode) assigneeNode.textContent = visibleAssignees().length + " 人";
  }

  function setActiveChannelPanel(panel) {
    const nextPanel = ["basic", "carrier", "assignee", "welcome", "tag"].includes(panel) ? panel : "basic";
    state.activeChannelPanel = nextPanel;
    bySelector("[data-channel-panel]").forEach((button) => {
      button.classList.toggle("active", button.dataset.channelPanel === nextPanel);
    });
    bySelector("[data-channel-panel-content]").forEach((panelNode) => {
      panelNode.classList.toggle("active", panelNode.dataset.channelPanelContent === nextPanel);
    });
  }

  function setupChannelPanels() {
    if (!root.querySelector("[data-channel-panel]")) return;
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-channel-panel]");
      if (!button || !root.contains(button)) return;
      event.preventDefault();
      setActiveChannelPanel(button.dataset.channelPanel);
    });
    setActiveChannelPanel(state.activeChannelPanel);
  }

  function channelFormPayload() {
    const form = root.querySelector("[data-channel-form]");
    if (!form) return {};
    const data = Object.fromEntries(new FormData(form).entries());
    const isLink = data.channel_type === "wecom_customer_acquisition";
    const miniprogramIds = intList(root.querySelector("[data-miniprogram-ids]")?.value);
    const imageIds = intList(root.querySelector("[data-image-ids]")?.value);
    const attachmentIds = intList(root.querySelector("[data-attachment-ids]")?.value);
    const groupInviteIds = intList(root.querySelector("[data-group-invite-ids]")?.value);
    const entryTagId = root.querySelector("[data-entry-tag-id]")?.value || "";
    const entryTagName = root.querySelector("[data-entry-tag-name]")?.value || "";
    const entryTagGroupName = root.querySelector("[data-entry-tag-group-name]")?.value || "";
    if (miniprogramIds.length + imageIds.length + attachmentIds.length + groupInviteIds.length > 9) {
      throw new Error("欢迎语素材最多选择 9 个");
    }
    const assignment = assignmentPayload();
    const payload = {
      admin_action_token: adminToken,
      channel_type: isLink ? "wecom_customer_acquisition" : "qrcode",
      carrier_type: isLink ? "link" : "qrcode",
      channel_name: data.channel_name || "",
      channel_code: data.channel_code || "",
      link_url: isLink ? data.link_url || "" : "",
      final_url: isLink ? data.final_url || finalUrl(data.link_url, data.customer_channel) : "",
      qr_url: isLink ? "" : data.qr_url || "",
      welcome_message: root.querySelector("[data-welcome-message]")?.value || "",
      welcome_image_library_ids: imageIds,
      welcome_miniprogram_library_ids: miniprogramIds,
      welcome_attachment_library_ids: attachmentIds,
      welcome_group_invite_library_ids: groupInviteIds,
      auto_accept_friend: !isLink && !!form.querySelector('[name="auto_accept_friend"]')?.checked,
      entry_tag_id: entryTagId,
      entry_tag_name: entryTagName,
      entry_tag_group_name: entryTagGroupName,
      owner_staff_id: assignment.owner_staff_id || data.owner_staff_id || "",
      assignment_mode: assignment.assignment_mode,
      assignment_strategy: assignment.assignment_strategy,
      assignees: assignment.assignees,
      status: data.status || "active",
    };
    if (isLink) {
      payload.scene_value = data.customer_channel || data.scene_value || "";
      payload.customer_channel = data.customer_channel || data.scene_value || "";
    }
    return payload;
  }

  const assignmentState = {
    strategy: "ratio",
    assignees: [],
    errors: [],
  };

  function normalizeAssignee(raw, index) {
    const staffId = String((raw || {}).staff_id || (raw || {}).user_id || "").trim();
    const displayName = String((raw || {}).display_name || (raw || {}).display_name_snapshot || staffId).trim();
    return {
      staff_id: staffId,
      display_name: displayName || staffId,
      ratio_percent: Number((raw || {}).ratio_percent || 0) || (index === 0 ? 100 : 0),
      max_scans_24h: Number((raw || {}).max_scans_24h || 0) || 100,
      status: String((raw || {}).status || "active").trim() || "active",
    };
  }

  function visibleAssignees() {
    return assignmentState.assignees.filter((item) => item.status === "active");
  }

  function ratioTotal() {
    return visibleAssignees().reduce((sum, item) => sum + Number(item.ratio_percent || 0), 0);
  }

  function validateAssignment() {
    const errors = [];
    const assignees = visibleAssignees();
    if (!assignees.length) errors.push("至少添加 1 个客服。");
    if (assignees.length > 5) errors.push("最多只能添加 5 个客服。");
    if (assignmentState.strategy === "ratio") {
      const total = ratioTotal();
      if (total !== 100) errors.push("比例合计必须等于 100%，当前为 " + total + "%。");
      assignees.forEach((item) => {
        if (Number(item.ratio_percent || 0) <= 0) errors.push((item.display_name || item.staff_id) + " 的比例必须大于 0%。");
      });
    } else {
      assignees.forEach((item) => {
        if (Number(item.max_scans_24h || 0) <= 0) errors.push((item.display_name || item.staff_id) + " 的 24h 上限必须大于 0。");
      });
    }
    assignmentState.errors = errors;
    return errors;
  }

  function assignmentPayload() {
    const errors = validateAssignment();
    if (errors.length) throw new Error(errors[0]);
    const assignees = visibleAssignees();
    return {
      assignment_mode: "multi_staff",
      assignment_strategy: assignmentState.strategy,
      owner_staff_id: assignees[0] ? assignees[0].staff_id : "",
      assignees: assignees.map((item, index) => ({
        staff_id: item.staff_id,
        display_name: item.display_name,
        priority: index + 1,
        ratio_percent: assignmentState.strategy === "ratio" ? Number(item.ratio_percent || 0) : null,
        max_scans_24h: assignmentState.strategy === "cap_switch" ? Number(item.max_scans_24h || 0) : null,
        status: "active",
      })),
    };
  }

  function setSaveButtonAvailability() {
    bySelector("[data-save-channel]").forEach((button) => {
      button.disabled = assignmentState.errors.length > 0;
    });
  }

  function renderAssignmentStrategies() {
    bySelector("[data-strategy-card]").forEach((card) => {
      const active = card.dataset.strategyCard === assignmentState.strategy;
      card.classList.toggle("is-active", active);
      const input = card.querySelector("[data-assignment-strategy]");
      if (input) input.checked = active;
    });
    const title = root.querySelector("[data-assignee-mode-title]");
    if (title) title.textContent = assignmentState.strategy === "ratio" ? "客服比例" : "客服 24h 上限";
  }

  function renderAssignees() {
    const list = root.querySelector("[data-assignee-list]");
    const count = root.querySelector("[data-assignee-count]");
    const addButtons = bySelector("[data-add-channel-assignee]");
    const assignees = visibleAssignees();
    const ratioMode = assignmentState.strategy === "ratio";
    if (count) count.textContent = assignees.length + " / 5";
    addButtons.forEach((button) => {
      button.disabled = assignees.length >= 5;
    });
    if (!list) return;
    if (!assignees.length) {
      list.innerHTML = '<div class="assignee-empty">请添加企微客服。</div>';
      return;
    }
    list.innerHTML = assignees.map((item, index) => {
      const field = ratioMode
        ? '<label class="field"><span>分配比例</span><input type="number" min="1" max="100" value="' + escapeHtml(item.ratio_percent) + '" data-assignee-field="ratio_percent" data-index="' + index + '"></label>'
        : '<label class="field"><span>24h 上限人数</span><input type="number" min="1" max="99999" value="' + escapeHtml(item.max_scans_24h) + '" data-assignee-field="max_scans_24h" data-index="' + index + '"></label>';
      return '<div class="assignee-row">' +
        '<div class="assignee-name"><strong>' + escapeHtml(item.display_name || item.staff_id) + '</strong><small>' + escapeHtml(item.staff_id) + '</small></div>' +
        field +
        '<div class="assignee-row-actions">' +
        '<button class="btn" type="button" data-assignee-move-up="' + index + '" ' + (index === 0 ? "disabled" : "") + '>上移</button>' +
        '<button class="btn danger" type="button" data-assignee-remove="' + index + '" ' + (assignees.length <= 1 ? "disabled" : "") + '>删除</button>' +
        '</div></div>';
    }).join("");
  }

  function renderAssignmentValidation() {
    const errors = validateAssignment();
    const bar = root.querySelector("[data-assignment-validation]");
    const text = root.querySelector("[data-assignment-validation-text]");
    const pill = root.querySelector("[data-assignment-validation-pill]");
    if (bar) bar.classList.toggle("is-error", errors.length > 0);
    if (assignmentState.strategy === "ratio") {
      const total = ratioTotal();
      if (text) text.textContent = errors.length ? errors[0] : "比例合计 100%，可以保存。";
      if (pill) {
        pill.textContent = total + "%";
        pill.className = "pill " + (total === 100 ? "green" : "red");
      }
    } else {
      if (text) text.textContent = errors.length ? errors[0] : "按列表顺序承接；达到 24h 上限后切换下一个客服。";
      if (pill) {
        pill.textContent = "满额切换";
        pill.className = "pill blue";
      }
    }
    setSaveButtonAvailability();
  }

  function renderAssignment() {
    renderAssignmentStrategies();
    renderAssignees();
    renderAssignmentValidation();
    const ownerInput = root.querySelector("[data-channel-owner-staff-id]");
    const first = visibleAssignees()[0];
    if (ownerInput) ownerInput.value = first ? first.staff_id : "";
    updateFormSummary();
  }

  function setupChannelAssignees() {
    const assignmentRoot = root.querySelector("[data-channel-assignment]");
    if (!assignmentRoot) return;
    assignmentState.strategy = String(bootstrapChannel.assignment_strategy || "ratio").trim() || "ratio";
    if (!["ratio", "cap_switch"].includes(assignmentState.strategy)) assignmentState.strategy = "ratio";
    const initialAssignees = Array.isArray(bootstrapChannel.assignees) ? bootstrapChannel.assignees : [];
    assignmentState.assignees = initialAssignees
      .filter((item) => String((item || {}).status || "active") === "active")
      .map(normalizeAssignee)
      .filter((item) => item.staff_id)
      .slice(0, 5);
    const ownerStaffId = String(bootstrapChannel.owner_staff_id || root.querySelector("[data-channel-owner-staff-id]")?.value || "").trim();
    if (!assignmentState.assignees.length && ownerStaffId) {
      assignmentState.assignees = [normalizeAssignee({ staff_id: ownerStaffId, display_name: ownerStaffId, ratio_percent: 100, max_scans_24h: 100 }, 0)];
      if (window.OperationMemberPicker && typeof window.OperationMemberPicker.resolve === "function") {
        window.OperationMemberPicker.resolve(ownerStaffId, { scope: "channel_code", page_size: 100 }).then((member) => {
          if (!member || !assignmentState.assignees[0] || assignmentState.assignees[0].staff_id !== ownerStaffId) return;
          assignmentState.assignees[0].display_name = member.display_name || member.user_id || ownerStaffId;
          renderAssignment();
        }).catch(() => undefined);
      }
    }
    renderAssignment();

    assignmentRoot.addEventListener("change", (event) => {
      const strategyInput = event.target.closest("[data-assignment-strategy]");
      if (strategyInput) {
        assignmentState.strategy = strategyInput.value === "cap_switch" ? "cap_switch" : "ratio";
        renderAssignment();
        return;
      }
      const field = event.target.closest("[data-assignee-field]");
      if (field) {
        const index = Number(field.dataset.index || 0);
        const key = field.dataset.assigneeField;
        const assignees = visibleAssignees();
        if (assignees[index] && key) assignees[index][key] = Number(field.value || 0);
        renderAssignment();
      }
    });
    assignmentRoot.addEventListener("input", (event) => {
      const field = event.target.closest("[data-assignee-field]");
      if (!field) return;
      const index = Number(field.dataset.index || 0);
      const key = field.dataset.assigneeField;
      const assignees = visibleAssignees();
      if (assignees[index] && key) assignees[index][key] = Number(field.value || 0);
      renderAssignmentValidation();
    });
    assignmentRoot.addEventListener("click", (event) => {
      const strategyCard = event.target.closest("[data-strategy-card]");
      if (strategyCard) {
        assignmentState.strategy = strategyCard.dataset.strategyCard === "cap_switch" ? "cap_switch" : "ratio";
        renderAssignment();
        return;
      }
      const remove = event.target.closest("[data-assignee-remove]");
      if (remove) {
        const index = Number(remove.dataset.assigneeRemove || 0);
        if (assignmentState.assignees.length > 1) assignmentState.assignees.splice(index, 1);
        renderAssignment();
        return;
      }
      const moveUp = event.target.closest("[data-assignee-move-up]");
      if (moveUp) {
        const index = Number(moveUp.dataset.assigneeMoveUp || 0);
        if (index > 0) {
          const previous = assignmentState.assignees[index - 1];
          assignmentState.assignees[index - 1] = assignmentState.assignees[index];
          assignmentState.assignees[index] = previous;
        }
        renderAssignment();
        return;
      }
      if (event.target.closest("[data-add-channel-assignee]")) {
        if (!window.OperationMemberPicker || typeof window.OperationMemberPicker.open !== "function") {
          toast("企微客服选择器加载失败，请刷新页面后重试");
          return;
        }
        const current = visibleAssignees();
        window.OperationMemberPicker.open({
          multiple: true,
          max: Math.max(1, 5 - current.length),
          selectedMembers: [],
          disabledUserIds: current.map((item) => item.staff_id),
          title: "选择企微客服",
          scope: "channel_code",
          page_size: 100,
          onConfirm: (members) => {
            const existing = new Set(visibleAssignees().map((item) => item.staff_id));
            (Array.isArray(members) ? members : []).forEach((member) => {
              const staffId = String(member.user_id || "").trim();
              if (!staffId || existing.has(staffId) || visibleAssignees().length >= 5) return;
              existing.add(staffId);
              assignmentState.assignees.push(normalizeAssignee({
                staff_id: staffId,
                display_name: member.display_name || staffId,
                ratio_percent: assignmentState.strategy === "ratio" ? 0 : null,
                max_scans_24h: 100,
                status: "active",
              }, assignmentState.assignees.length));
            });
            renderAssignment();
          },
        });
      }
    });
  }

  function setupChannelForm() {
    bySelector("[data-channel-type-option]").forEach((input) => {
      input.addEventListener("change", updateTypeVisibility);
    });
    bySelector("[data-channel-type-card]").forEach((card) => {
      card.addEventListener("click", () => {
        const input = card.querySelector("[data-channel-type-option]");
        if (!input) return;
        input.checked = true;
        updateTypeVisibility();
      });
    });
    bySelector('[name="link_url"], [name="customer_channel"]').forEach((input) => {
      input.addEventListener("input", updateTypeVisibility);
    });
    bySelector('[name="channel_name"], [name="status"]').forEach((input) => {
      input.addEventListener("input", updateFormSummary);
      input.addEventListener("change", updateFormSummary);
    });
    updateTypeVisibility();
    root.querySelector("[data-copy-form-link]")?.addEventListener("click", () => {
      const preview = root.querySelector("[data-link-preview]");
      copyText(preview && "value" in preview ? preview.value : preview?.textContent);
    });
    root.querySelector("[data-share-form-link]")?.addEventListener("click", () => {
      const preview = root.querySelector("[data-link-preview]");
      shareText(preview && "value" in preview ? preview.value : preview?.textContent);
    });
    root.querySelector("[data-generate-form-qrcode]")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      const detailUrl = root.dataset.apiDetail || "";
      if (!detailUrl) return;
      button.disabled = true;
      button.textContent = "生成中";
      apiJson(detailUrl + "/qrcode/generate", { method: "POST", body: JSON.stringify({}) }).then(({ response, data }) => {
        if (!response.ok || data.ok === false) {
          throw new Error(apiErrorMessage(data, "二维码生成失败"));
        }
        toast("二维码已生成");
        window.location.reload();
      }).catch((error) => {
        button.disabled = false;
        button.textContent = "生成二维码";
        toast(error.message || "二维码生成失败");
      });
    });

    const saveChannel = (saveButton) => {
      const isEdit = root.dataset.isEdit === "1";
      const url = isEdit ? root.dataset.apiDetail : root.dataset.apiCreate;
      const method = isEdit ? "PATCH" : "POST";
      let payload = {};
      try {
        payload = channelFormPayload();
      } catch (error) {
        toast(error.message || "保存失败");
        setSaveFeedback(error.message || "保存失败", "error");
        return;
      }
      if (saveButton) {
        saveButton.disabled = true;
        saveButton.dataset.originalText = saveButton.dataset.originalText || saveButton.textContent || "保存当前维度";
        saveButton.textContent = "保存中...";
      }
      setSaveFeedback("正在保存渠道配置...");
      apiJson(url, { method, body: JSON.stringify(payload) }).then(({ response, data }) => {
        if (!response.ok || data.ok === false) {
          const message = apiErrorMessage(data, "保存失败");
          toast(message);
          setSaveFeedback(message, "error");
          return;
        }
        const savedAt = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        toast("渠道已保存");
        setSaveFeedback("保存成功。" + (savedAt ? " " + savedAt : ""));
        if (!isEdit && data.channel && data.channel.id) {
          window.location.href = "/admin/channels/" + data.channel.id + "/edit";
        }
      }).catch((error) => {
        toast(error.message || "保存失败");
        setSaveFeedback(error.message || "保存失败", "error");
      }).finally(() => {
        if (saveButton) {
          saveButton.textContent = saveButton.dataset.originalText || "保存当前维度";
        }
        setSaveButtonAvailability();
      });
    };
    bySelector("[data-save-channel]").forEach((button) => {
      button.addEventListener("click", () => saveChannel(button));
    });
    updateFormSummary();
  }

  function setupWelcomeComposer() {
    const messageInput = root.querySelector("[data-welcome-message]");
    const miniInput = root.querySelector("[data-miniprogram-ids]");
    const imageInput = root.querySelector("[data-image-ids]");
    const attachmentInput = root.querySelector("[data-attachment-ids]");
    const groupInviteInput = root.querySelector("[data-group-invite-ids]");
    const summary = root.querySelector("[data-welcome-content-summary]");
    const materialSummary = root.querySelector("[data-welcome-material-summary]");
    if (!root.querySelector("[data-open-welcome-composer]")) return;

    const currentPackage = () => welcomeFieldsToContentPackage({
      welcome_message: messageInput?.value || "",
      welcome_image_library_ids: intList(imageInput?.value),
      welcome_miniprogram_library_ids: intList(miniInput?.value),
      welcome_attachment_library_ids: intList(attachmentInput?.value),
      welcome_group_invite_library_ids: intList(groupInviteInput?.value),
    });

    const renderSummary = () => {
      const contentPackage = currentPackage();
      const text = String(contentPackage.content_text || "").trim();
      const textSummary = text ? (text.length > 60 ? text.slice(0, 60) + "..." : text) : "未配置话术";
      if (summary) summary.textContent = textSummary;
      if (materialSummary) {
        materialSummary.innerHTML =
          '<span class="pill">图片 ' + contentPackage.image_library_ids.length + '</span>' +
          '<span class="pill">小程序 ' + contentPackage.miniprogram_library_ids.length + '</span>' +
          '<span class="pill">附件 ' + contentPackage.attachment_library_ids.length + '</span>' +
          '<span class="pill">客户群 ' + contentPackage.group_invite_library_ids.length + '</span>';
      } else if (summary) {
        summary.innerHTML =
          '<strong>话术：</strong><span>' + escapeHtml(textSummary) + '</span>' +
          '<strong>素材：</strong><span>图片 ' + contentPackage.image_library_ids.length +
          ' / 小程序 ' + contentPackage.miniprogram_library_ids.length +
          ' / 附件 ' + contentPackage.attachment_library_ids.length +
          ' / 客户群 ' + contentPackage.group_invite_library_ids.length + '</span>';
      }
    };

    const openWelcomeComposer = () => {
      if (!window.AICRMSendContentComposer || typeof window.AICRMSendContentComposer.open !== "function") {
        const message = "标准内容编辑器未加载，请刷新页面后重试";
        toast(message);
        setSaveFeedback(message, "error");
        root.dataset.welcomeComposerError = "composer_not_loaded";
        return;
      }
      window.AICRMSendContentComposer.open({
        title: "配置欢迎语和素材",
        textEnabled: true,
        value: currentPackage(),
        limits: {
          image: 3,
          miniprogram: 1,
          attachment: 9,
          group_invite: 1,
        },
        onConfirm(contentPackage) {
          const fields = contentPackageToWelcomeFields(contentPackage);
          if (messageInput) messageInput.value = fields.welcome_message;
          setIds(imageInput, fields.welcome_image_library_ids);
          setIds(miniInput, fields.welcome_miniprogram_library_ids);
          setIds(attachmentInput, fields.welcome_attachment_library_ids);
          setIds(groupInviteInput, fields.welcome_group_invite_library_ids);
          renderSummary();
          toast("欢迎语和素材已更新");
        },
      });
    };

    root.addEventListener("click", (event) => {
      const openButton = event.target.closest("[data-open-welcome-composer]");
      if (!openButton) return;
      event.preventDefault();
      openWelcomeComposer();
    });

    renderSummary();
    root.dataset.welcomeComposerReady = "1";
  }

  function setupEntryTagPicker() {
    const openButton = root.querySelector("[data-open-tag-picker]");
    if (!openButton) return;
    const tagSelected = root.querySelector("[data-tag-selected]");
    const tagIdInput = root.querySelector("[data-entry-tag-id]");
    const tagNameInput = root.querySelector("[data-entry-tag-name]");
    const tagGroupInput = root.querySelector("[data-entry-tag-group-name]");
    const pickerState = { catalog: null, failed: false };

    const fetchTagCatalog = () => {
      if (pickerState.catalog) return Promise.resolve(pickerState.catalog);
      const apiUrl = (bootstrap.api_urls || {}).wecom_tags || "/api/admin/wecom/tags";
      return apiJson(apiUrl, { method: "GET" }).then(({ data }) => {
        pickerState.catalog = {
          groups: Array.isArray(data.groups) ? data.groups : [],
          items: Array.isArray(data.items) ? data.items : [],
        };
        pickerState.failed = false;
        return pickerState.catalog;
      }).catch(() => {
        pickerState.catalog = { groups: [], items: [] };
        pickerState.failed = true;
        return pickerState.catalog;
      });
    };

    const renderSelectedMaterials = () => {
      if (tagSelected) {
        const tagId = String(tagIdInput?.value || "").trim();
        const tagName = String(tagNameInput?.value || "").trim();
        const groupName = String(tagGroupInput?.value || "").trim();
        tagSelected.innerHTML = tagId
          ? '<button type="button" class="pill" data-remove-picked="tag">' + escapeHtml((groupName ? groupName + " / " : "") + (tagName || "已选择标签")) + ' ×</button>'
          : '暂未选择标签';
      }
    };

    const openPicker = () => {
      if (!window.AICRMWeComTagPicker) {
        toast("标签选择器加载失败，请刷新后重试");
        return;
      }
      fetchTagCatalog().then((catalog) => {
        const currentValue = String(tagIdInput?.value || "").trim()
          ? {
              tag_id: tagIdInput.value,
              tag_name: tagNameInput?.value || "",
              group_name: tagGroupInput?.value || "",
            }
          : null;
        window.AICRMWeComTagPicker.open({
          title: "选择入渠标签",
          mode: "single",
          value: currentValue,
          catalog,
          allowManual: pickerState.failed,
          onConfirm: (tag) => {
            if (tagIdInput) tagIdInput.value = tag?.tag_id || "";
            if (tagNameInput) tagNameInput.value = tag?.tag_name || "";
            if (tagGroupInput) tagGroupInput.value = tag?.group_name || "";
            renderSelectedMaterials();
          },
          onClear: () => {
            if (tagIdInput) tagIdInput.value = "";
            if (tagNameInput) tagNameInput.value = "";
            if (tagGroupInput) tagGroupInput.value = "";
            renderSelectedMaterials();
          },
        });
      });
    };

    openButton.addEventListener("click", openPicker);
    root.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-picked]");
      if (!button) return;
      const kind = button.dataset.removePicked;
      if (kind === "tag") {
        if (tagIdInput) tagIdInput.value = "";
        if (tagNameInput) tagNameInput.value = "";
        if (tagGroupInput) tagGroupInput.value = "";
      }
      renderSelectedMaterials();
    });
    renderSelectedMaterials();
  }

  function importPayload(dryRun) {
    const channelId = parseInt(root.querySelector("[data-import-channel-id]")?.value || "0", 10);
    return {
      admin_action_token: adminToken,
      channel_id: channelId,
      dry_run: dryRun !== false,
      use_historical_channel_entered_at: false,
    };
  }

  function setupImportPanel() {
    const preview = root.querySelector("[data-import-payload-preview]");
    if (!preview) return;
    const update = (dryRun) => {
      preview.textContent = "导入模式：" + (dryRun === false ? "确认导入" : "只做预估") + "\n导入结果：仅写入渠道进入事实，供 AI 人群包查询。";
    };
    update(true);
    root.querySelector("[data-import-channel-id]")?.addEventListener("change", () => update(true));
    root.querySelector("[data-run-import-dry-run]")?.addEventListener("click", () => {
      const payload = importPayload(true);
      update(true);
      apiJson(root.dataset.apiImport, { method: "POST", body: JSON.stringify(payload) }).then(({ data }) => {
        preview.textContent = renderImportResult(data);
      });
    });
    root.querySelector("[data-run-import-confirm]")?.addEventListener("click", () => {
      const payload = importPayload(false);
      update(false);
      apiJson(root.dataset.apiImport, { method: "POST", body: JSON.stringify(payload) }).then(({ data }) => {
        preview.textContent = renderImportResult(data);
      });
    });
  }

  function setupEntryActions() {
    bySelector("[data-unbind-channel]").forEach((button) => {
      button.addEventListener("click", () => {
        const bindingId = button.dataset.bindingId;
        const base = (bootstrap.api_urls || {}).binding_base || "";
        const url = base.replace(/\/0($|[/?#])/, "/" + bindingId + "$1");
        apiJson(url, { method: "DELETE", body: JSON.stringify({ admin_action_token: adminToken }) }).then(({ response, data }) => {
          if (!response.ok || data.ok === false) {
            toast(data.error || data.reason || "解绑失败");
            return;
          }
          window.location.reload();
        });
      });
    });
    bySelector("[data-open-import-panel]").forEach((button) => {
      button.addEventListener("click", () => {
        const preview = root.querySelector("[data-import-payload-preview]");
        if (preview) {
          preview.textContent = "导入模式：只做预估\n导入结果：仅写入渠道进入事实，供 AI 人群包查询。";
        }
      });
    });
    const drawer = root.querySelector("[data-attempt-drawer]");
    if (drawer) {
      bySelector("[data-open-attempt-drawer]").forEach((button) => {
        button.addEventListener("click", () => {
          const row = button.closest("[data-admission-attempt-id]");
          root.querySelector("[data-attempt-drawer-body]").innerHTML = row ? row.innerHTML : "暂无详情";
          drawer.hidden = false;
        });
      });
      root.querySelector("[data-close-attempt-drawer]")?.addEventListener("click", () => {
        drawer.hidden = true;
      });
    }
  }

  safeInit("ChannelSearch", setupChannelSearch);
  safeInit("CopyShare", setupCopyShare);
  safeInit("ChannelDrawer", setupChannelDrawer);
  safeInit("ChannelPanels", setupChannelPanels);
  safeInit("ChannelForm", setupChannelForm);
  safeInit("ChannelAssignees", setupChannelAssignees);
  safeInit("WelcomeComposer", setupWelcomeComposer);
  safeInit("EntryTagPicker", setupEntryTagPicker);
  if (typeof setupBindModal === "function") safeInit("BindModal", setupBindModal);
  safeInit("ImportPanel", setupImportPanel);
  safeInit("EntryActions", setupEntryActions);
})();
