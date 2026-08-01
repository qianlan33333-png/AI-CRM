(function (global) {
  "use strict";

  const splitList = (value) => String(value || "")
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);

  const defaultsFromFields = (fields) => Object.fromEntries((fields || []).map((field) => [
    field.name,
    field.default !== undefined ? structuredClone(field.default) : null
  ]));

  const datetimeLocalValue = (value) => {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16);
    const local = new Date(parsed.getTime() - (parsed.getTimezoneOffset() * 60 * 1000));
    return local.toISOString().slice(0, 16);
  };

  class TemplateParameterFormController {
    constructor(root) {
      this.root = root;
      this.fields = [];
      this.values = {};
      this.readOnly = false;
      this.inputs = new Map();
    }

    setSchema(fields, values = {}, options = {}) {
      this.fields = Array.isArray(fields) ? fields : [];
      this.values = { ...defaultsFromFields(this.fields), ...(values || {}) };
      this.readOnly = Boolean(options.readOnly);
      this.render();
    }

    render() {
      this.root.replaceChildren();
      this.inputs.clear();
      this.fields.forEach((field) => {
        const wrapper = document.createElement("div");
        wrapper.className = "ai-field template-parameter-field";
        wrapper.dataset.fieldName = field.name;
        const label = document.createElement("label");
        label.className = "ai-label";
        label.textContent = `${field.label || field.name}${field.required ? " *" : ""}`;
        wrapper.appendChild(label);
        const input = this.renderInput(field, this.values[field.name]);
        wrapper.appendChild(input);
        if (field.help) {
          const help = document.createElement("small");
          help.className = "template-parameter-help";
          help.textContent = field.help;
          wrapper.appendChild(help);
        }
        this.root.appendChild(wrapper);
        this.inputs.set(field.name, { field, input, wrapper });
      });
      this.updateVisibility();
    }

    renderInput(field, value) {
      if (field.type === "condition_list") return this.renderConditions(field, value);
      if (field.type === "boolean") {
        const label = document.createElement("label");
        label.className = "template-switch";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(value);
        input.disabled = this.readOnly;
        input.addEventListener("change", () => this.updateVisibility());
        label.append(input, document.createTextNode(" 是"));
        return label;
      }
      if (field.type === "enum") {
        const select = document.createElement("select");
        select.className = "ai-select";
        (field.enum || []).forEach((optionValue) => {
          const option = document.createElement("option");
          option.value = optionValue;
          option.textContent = (field.enum_labels || {})[optionValue] || optionValue;
          select.appendChild(option);
        });
        select.value = value == null ? "" : String(value);
        select.disabled = this.readOnly;
        select.addEventListener("change", () => this.updateVisibility());
        return select;
      }
      if (field.type === "enum_list") {
        const select = document.createElement("select");
        select.className = "ai-select template-multi-select";
        select.multiple = true;
        (field.enum || []).forEach((optionValue) => {
          const option = document.createElement("option");
          option.value = optionValue;
          option.textContent = (field.enum_labels || {})[optionValue] || optionValue;
          option.selected = Array.isArray(value) && value.includes(optionValue);
          select.appendChild(option);
        });
        select.disabled = this.readOnly;
        return select;
      }
      if (["string_list", "reference_list"].includes(field.type)) {
        const textarea = document.createElement("textarea");
        textarea.className = "ai-textarea template-list-input";
        textarea.rows = 3;
        textarea.placeholder = "每行一个，也可用逗号分隔";
        textarea.value = Array.isArray(value) ? value.join("\n") : "";
        textarea.disabled = this.readOnly;
        return textarea;
      }
      const input = document.createElement("input");
      input.className = "ai-input";
      input.disabled = this.readOnly;
      if (field.type === "integer") {
        input.type = "number";
        if (field.minimum !== undefined) input.min = String(field.minimum);
      } else if (field.type === "datetime") {
        input.type = "datetime-local";
      } else {
        input.type = "text";
        if (field.reference) input.placeholder = "稳定 ID/code 或精确中文标题";
      }
      input.value = field.type === "datetime" ? datetimeLocalValue(value) : (value == null ? "" : String(value));
      return input;
    }

    renderConditions(field, value) {
      const container = document.createElement("div");
      container.className = "template-condition-list";
      const rows = document.createElement("div");
      rows.className = "template-condition-rows";
      container.appendChild(rows);
      const add = document.createElement("button");
      add.className = "ai-btn soft";
      add.type = "button";
      add.textContent = "添加题目条件";
      add.disabled = this.readOnly;
      add.addEventListener("click", () => this.appendCondition(rows, { question: "", options: [] }));
      container.appendChild(add);
      const items = Array.isArray(value) && value.length ? value : [{ question: "", options: [] }];
      items.forEach((item) => this.appendCondition(rows, item));
      return container;
    }

    appendCondition(rows, item) {
      const row = document.createElement("div");
      row.className = "template-condition-row";
      const question = document.createElement("input");
      question.className = "ai-input";
      question.dataset.conditionQuestion = "true";
      question.placeholder = "题目 ID 或精确标题";
      question.value = item.question ?? item.question_id ?? item.question_title ?? "";
      question.disabled = this.readOnly;
      const options = document.createElement("textarea");
      options.className = "ai-textarea template-list-input";
      options.dataset.conditionOptions = "true";
      options.placeholder = "候选选项 ID 或精确文本；同题多个选项为 OR";
      options.value = (item.options || item.option_ids || item.option_texts || []).join("\n");
      options.disabled = this.readOnly;
      const remove = document.createElement("button");
      remove.className = "ai-btn danger";
      remove.type = "button";
      remove.textContent = "移除";
      remove.disabled = this.readOnly;
      remove.addEventListener("click", () => row.remove());
      row.append(question, options, remove);
      rows.appendChild(row);
    }

    updateVisibility() {
      const current = this.getValue({ includeHidden: true });
      this.inputs.forEach(({ field, wrapper }) => {
        const rule = field.visible_when || null;
        wrapper.hidden = Boolean(rule) && Object.entries(rule).some(([name, value]) => current[name] !== value);
      });
    }

    getValue(options = {}) {
      const result = {};
      this.inputs.forEach(({ field, input, wrapper }, name) => {
        if (wrapper.hidden && !options.includeHidden) return;
        if (field.type === "condition_list") {
          result[name] = Array.from(input.querySelectorAll(".template-condition-row")).map((row) => ({
            question: row.querySelector("[data-condition-question]").value.trim(),
            options: splitList(row.querySelector("[data-condition-options]").value)
          })).filter((item) => item.question || item.options.length);
        } else if (field.type === "boolean") {
          result[name] = input.querySelector("input").checked;
        } else if (field.type === "enum_list") {
          result[name] = Array.from(input.selectedOptions).map((option) => option.value);
        } else if (["string_list", "reference_list"].includes(field.type)) {
          result[name] = splitList(input.value);
        } else if (field.type === "integer") {
          result[name] = input.value === "" ? null : Number(input.value);
        } else if (field.type === "datetime") {
          result[name] = input.value ? new Date(input.value).toISOString() : null;
        } else {
          result[name] = input.value;
        }
      });
      if (result.owner_scope === "all") result.owner_userids = [];
      return result;
    }
  }

  class PackageTemplateController {
    constructor(options) {
      this.options = options;
      this.els = options.elements;
      this.templates = [];
      this.form = new TemplateParameterFormController(this.els.templateParameterForm);
    }

    templateByKey(key) {
      return this.templates.find((item) => item.template_key === key);
    }

    hasTemplates() {
      return this.templates.length > 0;
    }

    editableParameters(templateKey, parameters = {}) {
      const value = { ...parameters };
      if (templateKey === "questionnaire_choice_answers") {
        value.questionnaire = parameters.questionnaire_id || "";
        value.conditions = (parameters.conditions || []).map((item) => ({
          question: item.question_id || item.question_title || "",
          options: item.option_ids || item.option_texts || []
        }));
      } else if (templateKey === "paid_order") {
        value.products = parameters.product_codes || parameters.product_names || [];
      } else if (templateKey === "channel_entry") {
        value.channels = parameters.channel_codes || parameters.channel_names || [];
      } else if (templateKey === "radar_first_click_elapsed") {
        value.radars = parameters.radar_ids || parameters.radar_titles || [];
      }
      return value;
    }

    render(packageInfo, resetValues = false) {
      const active = packageInfo.status === "active";
      const templateKey = resetValues
        ? this.els.templateSelect.value
        : (packageInfo.template_key || this.els.templateSelect.value || "");
      this.els.templateSelect.value = templateKey;
      const template = this.templateByKey(templateKey);
      const values = resetValues ? {} : this.editableParameters(templateKey, packageInfo.template_parameters || {});
      this.form.setSchema(template ? template.fields : [], values, { readOnly: active });
      this.els.templateSelect.disabled = active;
      this.els.templatePreviewBtn.disabled = active || !template;
      this.els.templateSaveBtn.disabled = active || !template;
      this.els.templateAllowEmpty.disabled = active;
      this.els.templateVersionBadge.textContent = template
        ? `${template.label} · v${packageInfo.template_version || template.template_version}`
        : "历史配置";
      this.els.templateHistoryNote.hidden = !packageInfo.is_historical_config;
      if (active) this.options.setStatus(this.els.templateStatusLine, "运行中的人群包为只读；请先停止再修正模板。", "error");
      else this.options.setStatus(this.els.templateStatusLine, template ? "可重新预览并保存为新版本" : "请选择模板完成历史配置转换");
    }

    async loadTemplates() {
      const payload = await this.options.fetchJson(`${this.options.templatesApiUrl}?_=${Date.now()}`);
      this.templates = payload.templates || [];
      this.els.templateSelect.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "请选择模板";
      this.els.templateSelect.appendChild(placeholder);
      this.templates.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.template_key;
        option.textContent = `${item.label} · v${Number(item.template_version)}`;
        this.els.templateSelect.appendChild(option);
      });
    }

    requestBody() {
      const packageInfo = this.options.getPackage();
      const mode = this.options.getRefreshMode();
      return {
        package_key: packageInfo.package_key,
        name: this.els.packageNameInput.value.trim() || packageInfo.name,
        template_key: this.els.templateSelect.value,
        parameters: this.form.getValue(),
        refresh_mode: mode === "incremental_3m"
          ? "every_3m"
          : mode === "incremental_3m_plus_daily_0200" ? "every_3m_plus_daily_0200" : mode,
        allow_empty: this.els.templateAllowEmpty.checked
      };
    }

    showPreview(payload) {
      this.els.templatePreviewBox.replaceChildren();
      const count = document.createElement("strong");
      count.textContent = `命中：${payload.matched_count_display || `${Number(payload.matched_count || 0)} 人`}`;
      const rule = document.createElement("span");
      rule.textContent = payload.natural_language_rule || "";
      const warning = document.createElement("small");
      warning.textContent = (payload.risk_warnings || []).length
        ? `风险提示：${payload.risk_warnings.join("、")}`
        : "未发现阻断风险";
      this.els.templatePreviewBox.append(count, rule, warning);
      this.els.templatePreviewBox.hidden = false;
    }

    async preview() {
      this.options.setStatus(this.els.templateStatusLine, "正在预览，最多等待 10 秒...");
      const payload = await this.options.fetchJson(this.options.templatePreviewApiUrl, {
        method: "POST",
        body: this.requestBody()
      });
      this.showPreview(payload);
      this.options.setStatus(this.els.templateStatusLine, "预览成功；保存只会生成 paused 版本，不会启用或发送。", "success");
      return payload;
    }

    async save() {
      this.options.setStatus(this.els.templateStatusLine, "正在校验并保存新版本...");
      const payload = await this.options.fetchJson(this.options.templateConfigApiUrl, {
        method: "PUT",
        body: this.requestBody()
      });
      this.showPreview(payload);
      this.options.renderPackage(payload.package || this.options.getPackage());
      this.options.setStatus(
        this.els.templateStatusLine,
        payload.idempotent ? "配置未变化，已复用现有版本。" : "新模板版本已保存，人群包保持停止状态。",
        "success"
      );
    }

    bind() {
      this.els.templateSelect.addEventListener("change", () => {
        const packageInfo = this.options.getPackage();
        if (!packageInfo || packageInfo.status === "active") return;
        this.render(packageInfo, true);
        this.els.templatePreviewBox.hidden = true;
      });
      this.els.templatePreviewBtn.addEventListener("click", () => {
        this.preview().catch((error) => this.options.setStatus(
          this.els.templateStatusLine,
          global.AdminApi.errorMessage(error, "预览失败"),
          "error"
        ));
      });
      this.els.templateSaveBtn.addEventListener("click", () => {
        this.save().catch((error) => this.options.setStatus(
          this.els.templateStatusLine,
          global.AdminApi.errorMessage(error, "保存失败"),
          "error"
        ));
      });
    }
  }

  global.TemplateParameterForm = {
    create(root) {
      if (!root) throw new Error("TemplateParameterForm root is required");
      return new TemplateParameterFormController(root);
    },
    createPackageController(options) {
      if (!options || !options.elements) throw new Error("PackageTemplateController options are required");
      return new PackageTemplateController(options);
    }
  };
})(window);
