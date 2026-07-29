(function () {
  "use strict";

  const CustomerProfile = window.CustomerProfile || {};
  window.CustomerProfile = CustomerProfile;

  const escapeHtml = CustomerProfile.escapeHtml;
  const requestJson = CustomerProfile.requestJson;
  const showSectionError = CustomerProfile.showSectionError;
  const showSectionEmpty = CustomerProfile.showSectionEmpty;

  function liveTagName(tag) {
    let value = "";
    if (tag && typeof tag === "object") {
      value = tag.tag_name || tag.name || tag.label || tag.value || tag.text || tag.tag_id || tag.id || "";
    } else {
      value = tag;
    }
    const normalized = String(value || "").trim();
    if (!normalized || normalized.toLowerCase() === "undefined" || normalized.toLowerCase() === "null") {
      return "";
    }
    return normalized;
  }

  function renderLiveTags(payload) {
    const stateNode = document.querySelector("[data-profile-tags-state]");
    const listNode = document.querySelector("[data-profile-tags]");
    if (!stateNode || !listNode) return;
    const tags = (payload && Array.isArray(payload.tags) ? payload.tags : [])
      .map(liveTagName)
      .filter(Boolean);
    if (!tags.length) {
      listNode.hidden = true;
      showSectionEmpty(stateNode, "当前没有实时标签", "暂未读取到企微标签。");
      return;
    }
    stateNode.hidden = true;
    listNode.hidden = false;
    listNode.innerHTML = tags
      .map((tag) => `<span class="admin-profile-tag">${escapeHtml(tag)}</span>`)
      .join("");
  }

  function renderQuestionnaireAnswers(payload) {
    const stateNode = document.querySelector("[data-profile-questionnaire-state]");
    const wrapNode = document.querySelector("[data-profile-questionnaire-wrap]");
    const bodyNode = document.querySelector("[data-profile-questionnaire-body]");
    if (!stateNode || !wrapNode || !bodyNode) return;
    const answers = payload && Array.isArray(payload.answers) ? payload.answers : [];
    const latestAssessment = payload && payload.latest_assessment_result ? payload.latest_assessment_result : null;
    if (!answers.length && !latestAssessment) {
      wrapNode.hidden = true;
      showSectionEmpty(stateNode, "当前没有问卷记录", "暂未找到可展示的问卷问答。");
      return;
    }
    const assessmentRows = latestAssessment ? `
          <tr>
            <td>测评结果</td>
            <td>${escapeHtml([
              latestAssessment.overall_level_title || "未分层",
              latestAssessment.total_score !== undefined ? `总分 ${latestAssessment.total_score}` : "",
              latestAssessment.weaknesses && latestAssessment.weaknesses.length ? `短板 ${latestAssessment.weaknesses.join("/")}` : "",
            ].filter(Boolean).join(" / "))}</td>
          </tr>
        ` : "";
    bodyNode.innerHTML = assessmentRows + answers
      .map(
        (item) => `
          <tr>
            <td>${escapeHtml(item.question || "未命名问题")}</td>
            <td>${escapeHtml(item.answer || "未填写")}</td>
          </tr>
        `,
      )
      .join("");
    stateNode.hidden = true;
    wrapNode.hidden = false;
  }

  function renderMessages(payload) {
    const stateNode = document.querySelector("[data-profile-messages-state]");
    const listNode = document.querySelector("[data-profile-messages]");
    if (!stateNode || !listNode) return;
    const messages = payload && Array.isArray(payload.messages) ? payload.messages : [];
    if (!messages.length) {
      listNode.hidden = true;
      showSectionEmpty(stateNode, "当前没有聊天记录", "暂未找到聊天内容。");
      return;
    }
    listNode.innerHTML = messages
      .map(
        (item) => `
          <article class="admin-profile-message">
            <div class="admin-profile-message-meta">
              <span>${escapeHtml(item.send_time || "未知时间")}</span>
              <span>${escapeHtml(item.speaker || "未知发送方")}</span>
            </div>
            <div class="admin-profile-message-content">${escapeHtml(item.content || "无内容")}</div>
          </article>
        `,
      )
      .join("");
    stateNode.hidden = true;
    listNode.hidden = false;
  }

  function loadLiveTags(root) {
    return requestJson(root.dataset.tagsUrl)
      .then((payload) => {
        renderLiveTags(payload);
        return payload;
      })
      .catch((error) => {
        showSectionError(document.querySelector("[data-profile-tags-state]"), error.message || "当前无法加载实时标签");
        return null;
      });
  }

  function loadQuestionnaireAnswers(root) {
    return requestJson(root.dataset.questionnaireUrl)
      .then((payload) => {
        renderQuestionnaireAnswers(payload);
        return payload;
      })
      .catch((error) => {
        showSectionError(document.querySelector("[data-profile-questionnaire-state]"), error.message || "当前无法加载问卷记录");
        return null;
      });
  }

  function loadMessages(root, fetchAll) {
    const url = new URL(root.dataset.messagesUrl, window.location.origin);
    if (fetchAll) {
      url.searchParams.set("fetch_all", "1");
    }
    return requestJson(url.toString())
      .then((payload) => {
        renderMessages(payload);
        return payload;
      })
      .catch((error) => {
        showSectionError(document.querySelector("[data-profile-messages-state]"), error.message || "当前无法加载聊天记录");
        return null;
      });
  }

  function wireFetchAllButton(root) {
    const button = document.querySelector("[data-profile-fetch-all-messages]");
    if (!button) return;
    button.addEventListener("click", () => {
      button.disabled = true;
      button.textContent = "正在加载全部聊天记录";
      loadMessages(root, true).finally(() => {
        button.disabled = false;
        button.textContent = "获取全部聊天记录";
      });
    });
  }

  function bootBasicSections(root) {
    loadLiveTags(root);
    loadQuestionnaireAnswers(root);
    loadMessages(root, false);
    wireFetchAllButton(root);
  }

  CustomerProfile.renderLiveTags = renderLiveTags;
  CustomerProfile.renderQuestionnaireAnswers = renderQuestionnaireAnswers;
  CustomerProfile.renderMessages = renderMessages;
  CustomerProfile.loadLiveTags = loadLiveTags;
  CustomerProfile.loadQuestionnaireAnswers = loadQuestionnaireAnswers;
  CustomerProfile.loadMessages = loadMessages;
  CustomerProfile.bootBasicSections = bootBasicSections;
})();
