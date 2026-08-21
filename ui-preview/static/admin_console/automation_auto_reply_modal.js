(function () {
  "use strict";

  const AutomationAutoReply = window.AutomationAutoReply || {};
  window.AutomationAutoReply = AutomationAutoReply;

  const copyClipboardText = AutomationAutoReply.copyClipboardText;
  const elements = AutomationAutoReply.elements;
  const requestJson = AutomationAutoReply.requestJson;
  const state = AutomationAutoReply.state;
  const withOutputId = AutomationAutoReply.withOutputId;

  function setModalFeedback(message) {
    const { modalFeedbackEl } = elements();
    if (!modalFeedbackEl) return;
    modalFeedbackEl.textContent = message || "";
  }

  function closeRejectModal() {
    const {
      clipboardTextEl,
      clipboardWrapEl,
      modalCopyEl,
      modalEl,
      reviewNoteEl,
    } = elements();
    state.pendingRejectOutputId = "";
    state.clipboardText = "";
    if (modalEl) modalEl.hidden = true;
    if (reviewNoteEl) reviewNoteEl.value = "";
    if (clipboardTextEl) clipboardTextEl.value = "";
    if (clipboardWrapEl) clipboardWrapEl.hidden = true;
    if (modalCopyEl) modalCopyEl.hidden = true;
    setModalFeedback("");
  }

  function openRejectModal(outputId) {
    const {
      clipboardTextEl,
      clipboardWrapEl,
      modalCopyEl,
      modalEl,
      reviewNoteEl,
    } = elements();
    state.pendingRejectOutputId = String(outputId || "");
    state.clipboardText = "";
    if (reviewNoteEl) reviewNoteEl.value = "";
    if (clipboardTextEl) clipboardTextEl.value = "";
    if (clipboardWrapEl) clipboardWrapEl.hidden = true;
    if (modalCopyEl) modalCopyEl.hidden = true;
    setModalFeedback("");
    if (modalEl) modalEl.hidden = false;
    if (reviewNoteEl) reviewNoteEl.focus();
  }

  function bindRejectModal(root) {
    const {
      clipboardTextEl,
      clipboardWrapEl,
      modalCancelEl,
      modalCopyEl,
      modalEl,
      modalSubmitEl,
      reviewNoteEl,
    } = elements();
    if (!modalEl || !modalSubmitEl || !modalCopyEl || !modalCancelEl || !reviewNoteEl || !clipboardTextEl || !clipboardWrapEl) {
      return;
    }
    modalSubmitEl.addEventListener("click", async () => {
      const outputId = state.pendingRejectOutputId;
      const reviewNote = String(reviewNoteEl.value || "").trim();
      if (!reviewNote) {
        setModalFeedback("未采用原因必填");
        reviewNoteEl.focus();
        return;
      }
      modalSubmitEl.disabled = true;
      modalCopyEl.hidden = true;
      clipboardWrapEl.hidden = true;
      setModalFeedback("正在提交未采用结果...");
      try {
        const payload = await requestJson(withOutputId(outputId), {
          method: "POST",
          body: {
            decision: "rejected",
            review_note: reviewNote,
          },
        });
        state.clipboardText = String(payload.clipboard_text || "");
        const copied = await copyClipboardText(state.clipboardText);
        if (copied) {
          setModalFeedback("未采用已提交，JSON 已自动复制到剪切板");
          window.setTimeout(() => {
            closeRejectModal();
            AutomationAutoReply.loadOutputs(root);
          }, 500);
          return;
        }
        clipboardTextEl.value = state.clipboardText;
        clipboardWrapEl.hidden = false;
        modalCopyEl.hidden = false;
        setModalFeedback("未采用已提交，但自动复制失败，请手动复制下面的 JSON");
        await AutomationAutoReply.loadOutputs(root);
      } catch (error) {
        setModalFeedback(error.message || "未采用提交失败");
      } finally {
        modalSubmitEl.disabled = false;
      }
    });

    modalCopyEl.addEventListener("click", async () => {
      const copied = await copyClipboardText(state.clipboardText);
      if (copied) {
        setModalFeedback("JSON 已复制到剪切板");
        return;
      }
      clipboardTextEl.focus();
      clipboardTextEl.select();
      setModalFeedback("浏览器仍然拒绝复制，请手动复制 textarea 中的 JSON");
    });

    modalCancelEl.addEventListener("click", closeRejectModal);
    modalEl.addEventListener("click", (event) => {
      if (event.target === modalEl) {
        closeRejectModal();
      }
    });
  }

  AutomationAutoReply.setModalFeedback = setModalFeedback;
  AutomationAutoReply.openRejectModal = openRejectModal;
  AutomationAutoReply.closeRejectModal = closeRejectModal;
  AutomationAutoReply.bindRejectModal = bindRejectModal;
})();
