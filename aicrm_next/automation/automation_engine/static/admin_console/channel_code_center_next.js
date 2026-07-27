(function () {
  const root = document.querySelector('[data-channel-admission-page="channel-center"]');
  if (!root) return;

  const list = root.querySelector("[data-channel-list]");
  const search = root.querySelector("[data-channel-search]");
  const drawer = root.querySelector("[data-channel-drawer]");
  const drawerBody = root.querySelector("[data-channel-drawer-body]");
  const apiUrl = root.dataset.apiChannels || "/api/admin/channels?limit=300";
  if (!list) return;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function isLink(channel) {
    return channel.carrier_type === "link" || channel.channel_type === "wecom_customer_acquisition";
  }

  function statusLabel(value) {
    return {
      active: "启用",
      inactive: "停用",
      disabled: "禁用",
      paused: "暂停",
      archived: "归档",
    }[value] || value || "-";
  }

  function metric(name, value) {
    const node = root.querySelector(`[data-channel-metric="${name}"]`);
    if (node) node.textContent = String(value || 0);
  }

  function updateMetrics(channels) {
    metric("total", channels.length);
    metric("standalone", channels.length);
    metric("link", channels.filter(isLink).length);
    metric("entered", channels.reduce((total, channel) => total + Number(channel.channel_contact_count || 0), 0));
  }

  function channelLinkText(channel) {
    return channel.copy_text || channel.share_url || channel.final_url || channel.link_url || "";
  }

  function toast(message) {
    root.dataset.lastToast = message;
    if (window.AdminConsole && typeof window.AdminConsole.showToast === "function") {
      window.AdminConsole.showToast(message);
    }
  }

  function fallbackCopy(text) {
    const input = document.createElement("textarea");
    input.value = String(text || "");
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

  function copyText(text) {
    const value = String(text || "").trim();
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

  function shareText(text) {
    const value = String(text || "").trim();
    if (!value) return copyText(value);
    if (navigator.share) {
      return navigator.share({ title: "企微获客助手链接", url: value }).catch(() => copyText(value));
    }
    return copyText(value);
  }

  function urlFromBase(base, id) {
    return String(base || "").replace(/\/0($|[/?#])/, "/" + id + "$1");
  }

  function apiJson(url) {
    return fetch(url, { credentials: "same-origin" }).then((response) => response.json().then((data) => ({ response, data })));
  }

  function apiErrorMessage(data, fallback) {
    const detail = data && data.detail;
    if (data && typeof data.reason === "string" && data.reason) return data.reason;
    if (data && typeof data.error === "string" && data.error) return data.error;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      return detail.reason || detail.error || detail.error_code || detail.message || fallback;
    }
    return fallback;
  }

  function postJson(url, payload, options) {
    const timeoutMs = Number((options || {}).timeoutMs || 0);
    const controller = timeoutMs > 0 && window.AbortController ? new AbortController() : null;
    const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
    const request = fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
      signal: controller ? controller.signal : undefined,
    }).then((response) => response.json().catch(() => ({})).then((data) => ({ response, data })));
    return timer ? request.finally(() => window.clearTimeout(timer)) : request;
  }

  function patchJson(url, payload) {
    return fetch(url, {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    }).then((response) => response.json().then((data) => ({ response, data })));
  }

  function qrcodeReady(channel) {
    return Boolean(channel.qrcode_asset_id) && ["active", "generated"].includes(String(channel.qrcode_status || ""));
  }

  function bootstrapUrls() {
    const node = root.querySelector("[data-channel-bootstrap]");
    if (!node) return {};
    try {
      return (JSON.parse(node.textContent || "{}").api_urls) || {};
    } catch (error) {
      return {};
    }
  }

  function truncateChannelName(name, maxLength = 20) {
    const value = String(name || "-");
    return value.length > maxLength ? `${value.slice(0, maxLength)}····` : value;
  }

  function statusClass(value) {
    const normalized = String(value || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
    return normalized ? `is-status-${normalized}` : "is-status-unknown";
  }

  function statusActionButtons(channel) {
    const status = String(channel.status || "");
    const deleteButton = '<button class="admin-button admin-button--ghost" type="button" data-channel-status-action data-next-status="archived" data-action-label="删除">删除</button>';
    if (status === "active") {
      return `<button class="admin-button admin-button--ghost" type="button" data-channel-status-action data-next-status="inactive" data-action-label="下架">下架</button>${deleteButton}`;
    }
    if (status === "inactive") {
      return `<button class="admin-button admin-button--ghost" type="button" data-channel-status-action data-next-status="active" data-action-label="启用">启用</button>${deleteButton}`;
    }
    return "";
  }

  function statusSuccessMessage(nextStatus) {
    return {
      active: "渠道已启用",
      inactive: "渠道已下架",
      archived: "渠道已删除",
    }[nextStatus] || "渠道状态已更新";
  }

  function confirmStatusAction(nextStatus) {
    if (nextStatus !== "archived") return true;
    return window.confirm("删除后会归档渠道并保留历史用户、二维码、配置和入渠记录。确认删除？");
  }

  function renderRow(channel) {
    const link = isLink(channel);
    const channelName = String(channel.channel_name || "-");
    const displayChannelName = truncateChannelName(channelName);
    const searchText = String(channel.channel_name || "").toLowerCase();
    const typeText = link ? "企微获客助手链接" : "普通二维码";
    const downloadUrl = channel.qr_download_url || `/api/admin/channels/${encodeURIComponent(channel.id)}/qrcode/download`;
    const copyText = channelLinkText(channel);
    const action = link
      ? `<button class="admin-button admin-button--secondary" type="button" data-copy-channel-link data-copy-text="${escapeHtml(copyText)}">复制链接</button>
         <button class="admin-button admin-button--secondary" type="button" data-share-channel-link data-copy-text="${escapeHtml(copyText)}">分享链接</button>`
      : qrcodeReady(channel)
        ? `<a class="admin-button admin-button--secondary" href="${escapeHtml(downloadUrl)}">下载二维码</a>`
        : `<button class="admin-button admin-button--secondary" type="button" data-generate-channel-qrcode>生成二维码</button>`;
    return `
      <tr data-channel-row data-channel-id="${escapeHtml(channel.id)}" data-search-text="${escapeHtml(searchText)}">
        <td>
          <strong class="channel-name" title="${escapeHtml(channelName)}">${escapeHtml(displayChannelName)}</strong>
        </td>
        <td><span class="channel-pill ${link ? "is-link" : "is-qrcode"}">${typeText}</span></td>
        <td><span class="channel-pill is-status ${statusClass(channel.status)}">${escapeHtml(statusLabel(channel.status))}</span></td>
        <td>
          <span class="channel-pill ${channel.welcome_message_configured ? "is-ok" : ""}">${channel.welcome_message_configured ? "欢迎语" : "无欢迎语"}</span>
          <span class="channel-pill">${escapeHtml(channel.welcome_attachment_count || 0)} 素材</span>
          <span class="channel-pill ${channel.entry_tag_configured ? "is-ok" : ""}">${channel.entry_tag_configured ? "标签" : "无标签"}</span>
        </td>
        <td>${escapeHtml(channel.channel_contact_count || 0)}</td>
        <td class="channel-action-cell">
          <div class="channel-row-actions">
            ${action}
            <button class="admin-button admin-button--ghost" type="button" data-open-channel-drawer>查看</button>
            <a class="admin-button admin-button--ghost" href="/admin/channels/${encodeURIComponent(channel.id)}/edit">编辑</a>
            ${statusActionButtons(channel)}
          </div>
        </td>
      </tr>`;
  }

  fetch(apiUrl, { credentials: "same-origin" })
    .then((response) => response.json().then((data) => ({ response, data })))
    .then(({ response, data }) => {
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || data.reason || "channels_load_failed");
      }
      const channels = Array.isArray(data.channels) ? data.channels : [];
      updateMetrics(channels);
      list.innerHTML = channels.length ? channels.map(renderRow).join("") : '<tr><td colspan="6">暂无渠道。</td></tr>';
    })
    .catch(() => {
      list.innerHTML = '<tr><td colspan="6">渠道加载失败，请稍后重试。</td></tr>';
    });

  search?.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    Array.from(list.querySelectorAll("[data-channel-row]")).forEach((row) => {
      const text = row.dataset.searchText || "";
      row.hidden = Boolean(query) && !text.includes(query);
    });
  });

  list.addEventListener("click", (event) => {
    const copyButton = event.target.closest("[data-copy-channel-link]");
    if (copyButton) {
      copyText(copyButton.dataset.copyText);
      return;
    }
    const shareButton = event.target.closest("[data-share-channel-link]");
    if (shareButton) {
      shareText(shareButton.dataset.copyText);
      return;
    }
    const generateButton = event.target.closest("[data-generate-channel-qrcode]");
    if (generateButton) {
      const row = generateButton.closest("[data-channel-row]");
      const channelId = row ? row.dataset.channelId : "";
      if (!channelId) return;
      generateButton.disabled = true;
      generateButton.textContent = "生成中";
      postJson(`/api/admin/channels/${encodeURIComponent(channelId)}/qrcode/generate`, {}, { timeoutMs: 30000 }).then(({ response, data }) => {
        if (!response.ok || data.ok === false) {
          throw new Error(apiErrorMessage(data, "二维码生成失败"));
        }
        toast("二维码已生成");
        window.location.reload();
      }).catch((error) => {
        generateButton.disabled = false;
        generateButton.textContent = "生成二维码";
        toast(error.name === "AbortError" ? "二维码生成超时，请稍后刷新确认或重试" : (error.message || "二维码生成失败"));
      });
      return;
    }
    const statusButton = event.target.closest("[data-channel-status-action]");
    if (statusButton) {
      const row = statusButton.closest("[data-channel-row]");
      const channelId = row ? row.dataset.channelId : "";
      const nextStatus = statusButton.dataset.nextStatus || "";
      if (!channelId || !nextStatus || !confirmStatusAction(nextStatus)) return;
      statusButton.disabled = true;
      const originalText = statusButton.textContent;
      statusButton.textContent = "处理中";
      patchJson(`/api/admin/channels/${encodeURIComponent(channelId)}`, { status: nextStatus }).then(({ response, data }) => {
        if (!response.ok || data.ok === false) {
          throw new Error(data.detail || data.error || data.reason || "channel_status_update_failed");
        }
        if (row && nextStatus === "archived") {
          row.remove();
        } else if (row && data.channel) {
          row.outerHTML = renderRow(data.channel);
        }
        toast(statusSuccessMessage(nextStatus));
      }).catch((error) => {
        statusButton.disabled = false;
        statusButton.textContent = originalText || statusButton.dataset.actionLabel || "操作";
        toast(error.message || "渠道状态更新失败");
      });
      return;
    }
    const detailButton = event.target.closest("[data-open-channel-drawer]");
    if (!detailButton || !drawer || !drawerBody) return;
    const row = detailButton.closest("[data-channel-row]");
    const channelId = row ? row.dataset.channelId : "";
    drawer.hidden = false;
    drawerBody.innerHTML = row
      ? `<p><strong>${escapeHtml(row.querySelector("strong")?.textContent || "")}</strong></p><p class="channel-muted">正在加载渠道用户列表...</p>`
      : "<p>暂无详情。</p>";
    if (!channelId) return;
    const urls = bootstrapUrls();
    Promise.all([
      apiJson(urlFromBase(urls.contacts_base, channelId) + "?limit=20").catch(() => ({ data: { contacts: [] } })),
    ]).then(([contactsResult]) => {
      const contacts = (contactsResult.data || {}).contacts || [];
      const contactRows = contacts.length
        ? contacts.map((item) => `<tr><td>${escapeHtml(item.display_name || item.name || item.external_contact_id || "-")}</td><td>${escapeHtml(item.enter_count || 0)}</td></tr>`).join("")
        : '<tr><td colspan="2">暂无渠道用户。</td></tr>';
      drawerBody.innerHTML = `
        <p><strong>${escapeHtml(row.querySelector("strong")?.textContent || "")}</strong></p>
        <h3>渠道用户列表</h3>
        <table class="admin-table channel-table"><thead><tr><th>客户</th><th>进入次数</th></tr></thead><tbody>${contactRows}</tbody></table>`;
    });
  });

  root.querySelector("[data-close-channel-drawer]")?.addEventListener("click", () => {
    if (drawer) drawer.hidden = true;
  });
})();
