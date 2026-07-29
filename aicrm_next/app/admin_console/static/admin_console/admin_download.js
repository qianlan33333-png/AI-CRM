(function () {
  function decodeFilename(value) {
    try {
      return decodeURIComponent(String(value || "").replace(/\+/g, "%20"));
    } catch (_error) {
      return String(value || "");
    }
  }

  function filenameFromDisposition(disposition, fallbackFilename) {
    const value = String(disposition || "");
    const encoded = value.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    if (encoded && encoded[1]) return decodeFilename(encoded[1].trim().replace(/^"|"$/g, ""));
    const plain = value.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i);
    return String((plain && (plain[1] || plain[2])) || fallbackFilename || "download").trim();
  }

  async function responseError(response) {
    const data = await response.clone().json().catch(() => null);
    if (data && typeof data === "object") {
      const detail = data.detail;
      if (typeof data.error === "string" && data.error) return data.error;
      if (typeof data.reason === "string" && data.reason) return data.reason;
      if (typeof detail === "string" && detail) return detail;
      if (detail && typeof detail === "object") return detail.error || detail.reason || detail.message || "下载失败";
    }
    return `下载失败（HTTP ${response.status}）`;
  }

  async function download(url, options) {
    options = options || {};
    const response = await fetch(String(url || ""), {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: options.accept || "image/jpeg" },
      redirect: "error",
    });
    if (!response.ok) throw new Error(await responseError(response));
    const blob = await response.blob();
    if (!blob.size) throw new Error("下载内容为空");
    const filename = filenameFromDisposition(
      response.headers.get("Content-Disposition"),
      options.fallbackFilename || "二维码.jpg"
    );
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    try {
      anchor.click();
    } finally {
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    }
    return { filename, size: blob.size, contentType: blob.type };
  }

  window.AICRMAdminDownload = { download, filenameFromDisposition };
})();
