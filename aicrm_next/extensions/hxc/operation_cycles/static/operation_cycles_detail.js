(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#2f6feb", "#20a36a", "#f59e0b", "#8b5cf6", "#ef4444", "#0891b2", "#64748b", "#ec4899"];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function svgElement(tag, attributes) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes || {}).forEach(([name, value]) => node.setAttribute(name, String(value)));
    return node;
  }

  function decodePayload(encoded) {
    const binary = window.atob(String(encoded || ""));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return JSON.parse(new TextDecoder("utf-8").decode(bytes));
  }

  function formatNumber(value, unit) {
    const numeric = Number(value);
    const formatted = Number.isInteger(numeric)
      ? new Intl.NumberFormat("zh-CN").format(numeric)
      : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(numeric);
    return `${formatted}${unit || ""}`;
  }

  function chartHeader(spec) {
    const fragment = document.createDocumentFragment();
    if (spec.title) fragment.appendChild(element("h3", "operation-cycle-chart__title", spec.title));
    if (spec.series.length > 1) {
      const legend = element("div", "operation-cycle-chart__legend");
      spec.series.forEach((series, index) => {
        const item = element("span", "operation-cycle-chart__legend-item");
        const swatch = element("i", "operation-cycle-chart__legend-swatch");
        swatch.style.background = COLORS[index % COLORS.length];
        item.append(swatch, document.createTextNode(series.name));
        legend.appendChild(item);
      });
      fragment.appendChild(legend);
    }
    return fragment;
  }

  function renderBar(container, spec) {
    const values = spec.series.flatMap((series) => series.data.map((value) => Math.abs(Number(value))));
    const maximum = Math.max(...values, 1);
    const body = element("div", "operation-cycle-chart__bars");
    spec.labels.forEach((label, labelIndex) => {
      const row = element("div", "operation-cycle-chart__bar-row");
      row.appendChild(element("span", "operation-cycle-chart__axis-label", label));
      const group = element("div", "operation-cycle-chart__bar-group");
      spec.series.forEach((series, seriesIndex) => {
        const item = element("div", "operation-cycle-chart__bar-item");
        const track = element("div", "operation-cycle-chart__bar-track");
        const fill = element("i", "operation-cycle-chart__bar-fill");
        const numeric = Number(series.data[labelIndex]);
        fill.style.width = `${Math.max(1, Math.abs(numeric) * 100 / maximum)}%`;
        fill.style.background = COLORS[seriesIndex % COLORS.length];
        track.appendChild(fill);
        item.append(track, element("strong", "operation-cycle-chart__value", formatNumber(numeric, spec.unit)));
        group.appendChild(item);
      });
      row.appendChild(group);
      body.appendChild(row);
    });
    container.appendChild(body);
  }

  function renderLine(container, spec) {
    const width = 760;
    const height = 300;
    const padding = { top: 22, right: 24, bottom: 52, left: 58 };
    const values = spec.series.flatMap((series) => series.data.map(Number));
    let minimum = Math.min(...values, 0);
    let maximum = Math.max(...values, 0);
    if (minimum === maximum) maximum = minimum + 1;
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const x = (index) => padding.left + (spec.labels.length === 1 ? plotWidth / 2 : index * plotWidth / (spec.labels.length - 1));
    const y = (value) => padding.top + (maximum - value) * plotHeight / (maximum - minimum);
    const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": spec.title || "折线图" });

    for (let index = 0; index <= 4; index += 1) {
      const value = maximum - index * (maximum - minimum) / 4;
      const position = padding.top + index * plotHeight / 4;
      svg.appendChild(svgElement("line", { x1: padding.left, y1: position, x2: width - padding.right, y2: position, class: "operation-cycle-chart__grid-line" }));
      const label = svgElement("text", { x: padding.left - 10, y: position + 4, class: "operation-cycle-chart__svg-label", "text-anchor": "end" });
      label.textContent = formatNumber(value, spec.unit);
      svg.appendChild(label);
    }

    spec.labels.forEach((labelText, index) => {
      const label = svgElement("text", { x: x(index), y: height - 18, class: "operation-cycle-chart__svg-label", "text-anchor": "middle" });
      label.textContent = labelText;
      svg.appendChild(label);
    });

    spec.series.forEach((series, seriesIndex) => {
      const color = COLORS[seriesIndex % COLORS.length];
      const points = series.data.map((value, index) => `${x(index)},${y(Number(value))}`).join(" ");
      svg.appendChild(svgElement("polyline", { points, fill: "none", stroke: color, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      series.data.forEach((value, index) => {
        const point = svgElement("circle", { cx: x(index), cy: y(Number(value)), r: 4, fill: "#fff", stroke: color, "stroke-width": 3 });
        const title = svgElement("title");
        title.textContent = `${spec.labels[index]} · ${series.name}：${formatNumber(value, spec.unit)}`;
        point.appendChild(title);
        svg.appendChild(point);
      });
    });
    const wrap = element("div", "operation-cycle-chart__line");
    wrap.appendChild(svg);
    container.appendChild(wrap);
  }

  function renderPie(container, spec) {
    const values = spec.series[0].data.map((value) => Math.max(0, Number(value)));
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!total) {
      container.appendChild(element("div", "operation-cycle-chart__empty", "暂无可绘制数据"));
      return;
    }
    let offset = 0;
    const segments = values.map((value, index) => {
      const start = offset;
      offset += value * 100 / total;
      return `${COLORS[index % COLORS.length]} ${start}% ${offset}%`;
    });
    const body = element("div", "operation-cycle-chart__pie-layout");
    const pie = element("div", "operation-cycle-chart__pie");
    pie.style.background = `conic-gradient(${segments.join(",")})`;
    pie.setAttribute("role", "img");
    pie.setAttribute("aria-label", spec.title || "饼图");
    const legend = element("div", "operation-cycle-chart__pie-legend");
    spec.labels.forEach((label, index) => {
      const item = element("div", "operation-cycle-chart__pie-item");
      const swatch = element("i", "operation-cycle-chart__legend-swatch");
      swatch.style.background = COLORS[index % COLORS.length];
      item.append(swatch, element("span", "", label), element("strong", "", formatNumber(values[index], spec.unit)));
      legend.appendChild(item);
    });
    body.append(pie, legend);
    container.appendChild(body);
  }

  function renderFunnel(container, spec) {
    const values = spec.series[0].data.map((value) => Math.max(0, Number(value)));
    const maximum = Math.max(...values, 1);
    const body = element("div", "operation-cycle-chart__funnel");
    spec.labels.forEach((label, index) => {
      const row = element("div", "operation-cycle-chart__funnel-row");
      row.style.width = `${Math.max(22, values[index] * 100 / maximum)}%`;
      row.style.background = COLORS[index % COLORS.length];
      row.append(element("span", "", label), element("strong", "", formatNumber(values[index], spec.unit)));
      body.appendChild(row);
    });
    container.appendChild(body);
  }

  function renderCharts() {
    document.querySelectorAll("[data-operation-cycle-chart]").forEach((container) => {
      try {
        const spec = decodePayload(container.dataset.operationCycleChart);
        container.replaceChildren(chartHeader(spec));
        if (spec.type === "bar") renderBar(container, spec);
        if (spec.type === "line") renderLine(container, spec);
        if (spec.type === "pie") renderPie(container, spec);
        if (spec.type === "funnel") renderFunnel(container, spec);
      } catch (_error) {
        container.replaceChildren(element("div", "operation-cycle-chart-error", "图表加载失败"));
      }
    });
  }

  function parseFlowNode(raw) {
    const value = String(raw || "").trim();
    const match = value.match(/^([A-Za-z0-9_.:-]+)\s*(?:\[\[(.*?)\]\]|\(\((.*?)\)\)|\[(.*?)\]|\{(.*?)\}|\((.*?)\))?$/);
    if (!match) return { id: value, label: value, shape: "rect" };
    const label = match.slice(2).find((item) => item !== undefined) || match[1];
    const shape = match[5] !== undefined ? "decision" : match[3] !== undefined ? "circle" : "rect";
    return { id: match[1], label, shape };
  }

  function renderFlowDiagram(container, source) {
    const lines = source.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("%%"));
    const header = lines.shift() || "";
    const direction = /\b(?:TD|TB|BT)\b/i.test(header) ? "vertical" : "horizontal";
    const edges = [];
    lines.forEach((line) => {
      const match = line.match(/^(.+?)\s*(?:-->|==>|-\.->)\s*(?:\|([^|]+)\|\s*)?(.+)$/);
      if (!match) return;
      edges.push({ from: parseFlowNode(match[1]), label: String(match[2] || "").trim(), to: parseFlowNode(match[3]) });
    });
    if (!edges.length) throw new Error("unsupported flow diagram");
    const body = element("div", `operation-cycle-diagram__flow operation-cycle-diagram__flow--${direction}`);
    edges.forEach((edge) => {
      const row = element("div", "operation-cycle-diagram__edge");
      const from = element("div", `operation-cycle-diagram__node operation-cycle-diagram__node--${edge.from.shape}`, edge.from.label);
      const arrow = element("div", "operation-cycle-diagram__arrow", direction === "vertical" ? "↓" : "→");
      if (edge.label) arrow.appendChild(element("small", "", edge.label));
      const to = element("div", `operation-cycle-diagram__node operation-cycle-diagram__node--${edge.to.shape}`, edge.to.label);
      row.append(from, arrow, to);
      body.appendChild(row);
    });
    container.appendChild(body);
  }

  function renderSequenceDiagram(container, source) {
    const lines = source.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("%%"));
    lines.shift();
    const participantLabels = new Map();
    const messages = [];
    lines.forEach((line) => {
      const participant = line.match(/^participant\s+([A-Za-z0-9_.:-]+)(?:\s+as\s+(.+))?$/i);
      if (participant) {
        participantLabels.set(participant[1], String(participant[2] || participant[1]).trim());
        return;
      }
      const message = line.match(/^([A-Za-z0-9_.:-]+)\s*(?:-->>|->>|-->|->)\s*([A-Za-z0-9_.:-]+)\s*:\s*(.+)$/);
      if (!message) return;
      participantLabels.set(message[1], participantLabels.get(message[1]) || message[1]);
      participantLabels.set(message[2], participantLabels.get(message[2]) || message[2]);
      messages.push({ from: message[1], to: message[2], text: message[3] });
    });
    if (!messages.length) throw new Error("unsupported sequence diagram");
    const body = element("div", "operation-cycle-diagram__sequence");
    messages.forEach((message, index) => {
      const row = element("div", "operation-cycle-diagram__message");
      row.append(
        element("span", "operation-cycle-diagram__actor", participantLabels.get(message.from)),
        element("span", "operation-cycle-diagram__message-arrow", `${index + 1}  →`),
        element("span", "operation-cycle-diagram__actor", participantLabels.get(message.to)),
        element("strong", "operation-cycle-diagram__message-text", message.text),
      );
      body.appendChild(row);
    });
    container.appendChild(body);
  }

  function renderDiagrams() {
    document.querySelectorAll("[data-operation-cycle-diagram]").forEach((container) => {
      try {
        const payload = decodePayload(container.dataset.operationCycleDiagram);
        const source = String(payload.source || "").trim();
        container.replaceChildren();
        if (/^(?:flowchart|graph)\b/i.test(source)) {
          renderFlowDiagram(container, source);
        } else if (/^sequenceDiagram\b/i.test(source)) {
          renderSequenceDiagram(container, source);
        } else {
          throw new Error("unsupported diagram");
        }
      } catch (_error) {
        container.replaceChildren(element("div", "operation-cycle-chart-error", "流程图语法暂不支持"));
      }
    });
  }

  function statusLabel(value) {
    const labels = {
      pending_review: "待审批",
      approved: "已批准",
      rejected: "已拒绝",
      active: "执行中",
      draft: "草稿",
      completed: "已完成",
      failed: "失败",
      pending: "待处理",
    };
    return labels[String(value || "")] || String(value || "未知");
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }

  function planCell(text, className) {
    return element("td", className || "", text);
  }

  function planRow(record) {
    const row = element("tr", "operation-cycle-plan-row");
    const name = planCell("");
    const title = element("strong", "", record.plan.display_name || record.reference.label || record.planId);
    name.append(title, element("small", "", record.planId));
    const detail = planCell("");
    const link = element("a", "operation-cycle-plan-link", "查看详情");
    link.href = `/admin/cloud-orchestrator/plans/${encodeURIComponent(record.planId)}`;
    detail.appendChild(link);
    if (record.error) {
      row.classList.add("is-unavailable");
      row.append(name, planCell("—"), planCell("—"), planCell("记录暂不可用"), planCell("—"), detail);
      return row;
    }
    row.append(
      name,
      planCell(formatDate(record.plan.updated_at)),
      planCell(new Intl.NumberFormat("zh-CN").format(Number(record.plan.target_count || 0))),
      planCell(statusLabel(record.plan.review_status)),
      planCell(statusLabel(record.plan.run_status)),
      detail,
    );
    return row;
  }

  async function renderPlanHistory() {
    const root = document.querySelector("[data-operation-cycle-plan-history]");
    const payloadNode = document.getElementById("operationCycleAssistantPlans");
    if (!root || !payloadNode) return;
    let references = [];
    try {
      references = JSON.parse(payloadNode.textContent || "[]");
    } catch (_error) {
      references = [];
    }
    if (!references.length) {
      root.replaceChildren(element("div", "operation-cycle-document-empty", "还没有与当前任务绑定的 AI 助手记录"));
      return;
    }
    const requestJson = window.AdminApi && window.AdminApi.requestJson;
    if (typeof requestJson !== "function") {
      root.replaceChildren(element("div", "operation-cycle-document-empty", "AI 助手记录暂时无法加载"));
      return;
    }
    const records = await Promise.all(references.map(async (reference) => {
      const planId = String(reference.source_id || "");
      try {
        const payload = await requestJson(`/api/admin/cloud-orchestrator/plans/${encodeURIComponent(planId)}`);
        return { reference, planId, plan: payload.plan || {}, stats: payload.stats || {}, error: false };
      } catch (_error) {
        return { reference, planId, plan: {}, stats: {}, error: true };
      }
    }));
    const tableWrap = element("div", "operation-cycle-plan-table-wrap");
    const table = element("table", "operation-cycle-plan-table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["计划名称", "更新时间", "目标人数", "审核状态", "执行状态", "明细"].forEach((label) => headRow.appendChild(element("th", "", label)));
    head.appendChild(headRow);
    const body = document.createElement("tbody");
    records.forEach((record) => body.appendChild(planRow(record)));
    table.append(head, body);
    tableWrap.appendChild(table);
    root.replaceChildren(tableWrap);
  }

  function init() {
    renderCharts();
    renderDiagrams();
    renderPlanHistory();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
