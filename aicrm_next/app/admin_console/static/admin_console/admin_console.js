function bootLegacyFrames() {
  document.querySelectorAll("[data-legacy-shell]").forEach((shell) => {
    const frame = shell.querySelector("[data-legacy-frame]");
    const state = shell.querySelector("[data-legacy-state]");
    if (!frame || !state) {
      return;
    }

    let loaded = false;
    const ready = () => {
      if (loaded) {
        return;
      }
      loaded = true;
      shell.querySelector(".admin-legacy-frame-wrap")?.classList.add("is-ready");
    };

    frame.addEventListener("load", ready, { once: true });
    window.setTimeout(() => {
    if (loaded) {
      return;
    }
    state.classList.remove("admin-state--loading");
    state.classList.add("admin-state--error");
    state.innerHTML = [
        "<strong>页面加载超时</strong>",
        "<span>当前页面没有按预期完成加载，请稍后重试。</span>",
    ].join("");
  }, 15000);
  });
}

function bootOutputModal() {
  const backdrop = document.querySelector("[data-output-modal-backdrop]");
  if (!backdrop) {
    return;
  }

  const closeUrl = backdrop.getAttribute("data-close-url") || "";
  const closeModal = () => {
    if (!closeUrl) {
      return;
    }
    window.location.href = closeUrl;
  };

  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) {
      closeModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeModal();
    }
  });

  backdrop.querySelectorAll("[data-output-modal-close]").forEach((node) => {
    node.addEventListener("click", (event) => {
      if (!closeUrl) {
        return;
      }
      event.preventDefault();
      closeModal();
    });
  });
}

function bootCopyButtons() {
  document.querySelectorAll("[data-copy-text]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = button.getAttribute("data-copy-text") || "";
      const defaultLabel = button.getAttribute("data-copy-label-default") || button.textContent || "复制";
      const successLabel = button.getAttribute("data-copy-label-success") || "已复制";
      const errorLabel = button.getAttribute("data-copy-label-error") || "复制失败";
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = successLabel;
      } catch (error) {
        button.textContent = errorLabel;
      }
      window.setTimeout(() => {
        button.textContent = defaultLabel;
      }, 1500);
    });
  });
}

function bootOutputReviewForms() {
  document.querySelectorAll("[data-output-review-reject-form]").forEach((form) => {
    const button = form.querySelector("[data-output-review-reject]");
    const noteInput = form.querySelector('input[name="review_note"]');
    if (!button || !noteInput) {
      return;
    }
    button.addEventListener("click", () => {
      const defaultValue = noteInput.value || "";
      const note = window.prompt("可选：填写这条话术未被采用的原因或修改建议", defaultValue);
      if (note === null) {
        return;
      }
      noteInput.value = note.trim();
      form.submit();
    });
  });
}

function formatRelativeTime(input) {
  if (input === null || input === undefined || input === "") {
    return "";
  }
  const date = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(date.getTime())) {
    return String(input);
  }
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.round(diffMs / 1000);
  const diffMin = Math.round(diffSec / 60);
  const sameDay =
    now.getFullYear() === date.getFullYear() &&
    now.getMonth() === date.getMonth() &&
    now.getDate() === date.getDate();

  if (Math.abs(diffSec) < 45) return "刚刚";
  if (Math.abs(diffMin) < 60) {
    return diffMin >= 0 ? `${diffMin} 分钟前` : `${-diffMin} 分钟后`;
  }
  if (sameDay) {
    return `今天 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (
    yesterday.getFullYear() === date.getFullYear() &&
    yesterday.getMonth() === date.getMonth() &&
    yesterday.getDate() === date.getDate()
  ) {
    return `昨天 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }
  if (now.getFullYear() === date.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

function formatLocalTime(input) {
  if (input === null || input === undefined || input === "") {
    return "";
  }
  const date = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(date.getTime())) {
    return String(input);
  }
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

window.AdminFmt = Object.assign(window.AdminFmt || {}, {
  relativeTime: formatRelativeTime,
  localTime: formatLocalTime,
});

document.addEventListener("DOMContentLoaded", () => {
  bootLegacyFrames();
  bootOutputModal();
  bootCopyButtons();
  bootOutputReviewForms();
});
