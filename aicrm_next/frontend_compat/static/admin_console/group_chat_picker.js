(function (window, document) {
  "use strict";

  const pickerApi = "/api/admin/automation-conversion/group-ops/group-picker";
  const pickerSyncApi = "/api/admin/automation-conversion/group-ops/group-picker/sync";
  const ensureApi = "/api/admin/group-invite-bindings/ensure";

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);

  async function fetchJson(url, options, errorMessage) {
    if (window.AdminApi && typeof window.AdminApi.requestJson === "function") {
      return window.AdminApi.requestJson(url, options || {}).catch((error) => {
        throw new Error(error.message || errorMessage || "请求失败");
      });
    }
    const response = await fetch(url, options || {});
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail && typeof payload.detail === "object" ? payload.detail.detail : payload.detail;
    if (!response.ok || payload.ok === false) throw new Error(payload.error || detail || errorMessage || "请求失败");
    return payload;
  }

  function normalizeGroup(raw) {
    return {
      chat_id: String(raw.chat_id || "").trim(),
      group_name: String(raw.group_name || raw.chat_id || "未命名群聊").trim(),
      owner_userid: String(raw.owner_userid || "").trim(),
      owner_name: String(raw.owner_name || raw.owner_userid || "未识别群主").trim(),
      member_count: Number(raw.member_count || 0),
      binding_id: Number(raw.binding_id || 0),
      binding_status: String(raw.binding_status || "pending").trim(),
    };
  }

  function bindingLabel(status) {
    if (status === "ready") return "可直接使用";
    if (status === "invalid") return "群邀请已失效";
    return "选择后自动生成";
  }

  function open(options) {
    options = options || {};
    const ownerUserId = String(options.ownerUserId || options.owner_userid || "").trim();
    const selectedIds = new Set((options.selectedIds || []).map(Number).filter((value) => value > 0));
    const onConfirm = typeof options.onConfirm === "function" ? options.onConfirm : function () {};
    const onCancel = typeof options.onCancel === "function" ? options.onCancel : function () {};
    let groups = [];
    let query = "";
    let closed = false;
    let loading = false;

    const mask = document.createElement("div");
    mask.className = "aicrm-group-chat-picker-mask is-open";
    mask.innerHTML = `
      <section class="aicrm-group-chat-picker" role="dialog" aria-modal="true" aria-labelledby="aicrm-group-chat-picker-title">
        <header class="aicrm-group-chat-picker__head">
          <h2 id="aicrm-group-chat-picker-title">选择群聊</h2>
          <button class="aicrm-group-chat-picker__cancel" type="button" data-group-picker-close>取消</button>
        </header>
        <div class="aicrm-group-chat-picker__search-wrap">
          <input class="aicrm-group-chat-picker__search" type="search" placeholder="搜索群聊名称" aria-label="搜索群聊名称" data-group-picker-search>
        </div>
        <div class="aicrm-group-chat-picker__body">
          <div class="aicrm-group-chat-picker__empty" data-group-picker-empty>正在加载群聊…</div>
          <div class="aicrm-group-chat-picker__list" data-group-picker-list></div>
        </div>
      </section>`;
    document.body.appendChild(mask);

    const search = mask.querySelector("[data-group-picker-search]");
    const list = mask.querySelector("[data-group-picker-list]");
    const empty = mask.querySelector("[data-group-picker-empty]");

    function close(cancelled) {
      if (closed) return;
      closed = true;
      mask.remove();
      if (cancelled) onCancel();
    }

    function visibleGroups() {
      const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
      if (!normalizedQuery) return groups;
      return groups.filter((group) => group.group_name.toLocaleLowerCase("zh-CN").includes(normalizedQuery));
    }

    function render(message) {
      const rows = visibleGroups();
      if (loading || message || !rows.length) {
        empty.hidden = false;
        empty.textContent = message || (loading ? "正在加载群聊…" : "没有匹配的群聊");
      } else {
        empty.hidden = true;
      }
      list.innerHTML = rows.map((group) => {
        const selected = selectedIds.has(group.binding_id);
        const invalid = group.binding_status === "invalid";
        return `<button class="aicrm-group-chat-picker__row${selected ? " is-selected" : ""}${invalid ? " is-disabled" : ""}" type="button" data-group-chat-id="${escapeHtml(group.chat_id)}" ${invalid ? "disabled" : ""}>
          <span class="aicrm-group-chat-picker__main">
            <strong class="aicrm-group-chat-picker__name">${escapeHtml(group.group_name)}</strong>
            <span class="aicrm-group-chat-picker__meta">群主：${escapeHtml(group.owner_name)} · ${group.member_count} 人</span>
          </span>
          <span class="aicrm-group-chat-picker__state is-${escapeHtml(group.binding_status)}">${escapeHtml(bindingLabel(group.binding_status))}</span>
        </button>`;
      }).join("");
    }

    async function loadCache() {
      loading = true;
      render();
      const params = new URLSearchParams({ limit: "200" });
      if (ownerUserId) params.set("owner_userid", ownerUserId);
      const payload = await fetchJson(`${pickerApi}?${params.toString()}`, null, "群聊列表加载失败");
      groups = (payload.items || []).map(normalizeGroup);
      loading = false;
      render();
      if (payload.needs_sync) await syncGroups().catch(() => {});
    }

    async function syncGroups() {
      const payload = await fetchJson(pickerSyncApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner_userid: ownerUserId, limit: 200 }),
      }, "群聊同步失败");
      groups = (payload.items || []).map(normalizeGroup);
      render();
    }

    async function selectGroup(group) {
      loading = true;
      render("正在自动生成群邀请…");
      try {
        const payload = await fetchJson(ensureApi, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(group),
        }, "群邀请绑定创建失败");
        const binding = payload.item || {};
        const bindingId = Number(payload.binding_id || binding.id || 0);
        if (!bindingId) throw new Error("群邀请绑定 ID 无效");
        const status = String(payload.binding_status || binding.binding_status || "");
        if (status !== "ready" || !String(binding.join_url || "").trim()) {
          throw new Error("系统未返回可用的群邀请，请重试");
        }
        onConfirm({
          type: "group_invite",
          library_id: bindingId,
          title: group.group_name,
          subtitle: bindingLabel(status),
          enabled: true,
          selectable: true,
          metadata: {
            chat_id: group.chat_id,
            group_name: group.group_name,
            owner_userid: group.owner_userid,
            owner_name: group.owner_name,
            member_count: group.member_count,
            binding_status: status,
          },
        });
        close(false);
      } catch (error) {
        loading = false;
        render(error.message || "群邀请绑定创建失败");
      }
    }

    mask.addEventListener("click", (event) => {
      if (event.target === mask || event.target.closest("[data-group-picker-close]")) return close(true);
      const row = event.target.closest("[data-group-chat-id]");
      if (!row || loading) return;
      const group = groups.find((item) => item.chat_id === String(row.dataset.groupChatId || ""));
      if (group) selectGroup(group);
    });
    search.addEventListener("input", () => {
      query = search.value || "";
      render();
    });
    loadCache().catch((error) => {
      loading = false;
      render(error.message || "群聊列表加载失败");
    });
  }

  window.AICRMGroupChatPicker = { open };
})(window, document);
