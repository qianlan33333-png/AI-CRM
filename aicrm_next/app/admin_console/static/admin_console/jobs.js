function bootJobsForms() {
  document.querySelectorAll("[data-jobs-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const confirmMessage = form.getAttribute("data-confirm-message");
      if (confirmMessage && !window.confirm(confirmMessage)) {
        event.preventDefault();
        return;
      }
      const button = form.querySelector("[data-jobs-submit]");
      if (!button) {
        return;
      }
      const loadingLabel = button.getAttribute("data-loading-label") || "执行中...";
      button.dataset.originalText = button.textContent;
      button.textContent = loadingLabel;
      button.disabled = true;
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bootJobsForms();
});
