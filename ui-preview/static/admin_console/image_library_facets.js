(function (root) {
  "use strict";

  var TAG_DIMENSION_ORDER = ["主题", "特征", "用途", "行业"];

  function splitTagDimension(tag) {
    var value = String(tag || "").trim();
    var matched = value.match(/^([^:：]{1,12})[:：]\s*(.+)$/);
    if (!matched) return { key: "其他", label: "其他标签", value: value, option: value };
    return {
      key: matched[1].trim(),
      label: matched[1].trim(),
      value: value,
      option: matched[2].trim(),
    };
  }

  function buildTagDimensions(tags) {
    var grouped = {};
    (tags || []).forEach(function (tag) {
      var item = splitTagDimension(tag);
      if (!item.value || !item.option) return;
      if (!grouped[item.key]) grouped[item.key] = { key: item.key, label: item.label, options: [] };
      grouped[item.key].options.push({ value: item.value, label: item.option });
    });
    return Object.keys(grouped).sort(function (a, b) {
      var ai = TAG_DIMENSION_ORDER.indexOf(a);
      var bi = TAG_DIMENSION_ORDER.indexOf(b);
      if (ai < 0) ai = TAG_DIMENSION_ORDER.length + (a === "其他" ? 100 : 0);
      if (bi < 0) bi = TAG_DIMENSION_ORDER.length + (b === "其他" ? 100 : 0);
      if (ai !== bi) return ai - bi;
      return a.localeCompare(b, "zh-CN");
    }).map(function (key) { return grouped[key]; });
  }

  function facetRow(key, label, options, selected, kind, escapeHtml) {
    if (!options.length) return "";
    var unlimitedActive = selected.length ? "" : " active";
    var buttons = ['<button type="button" class="il-facet-option' + unlimitedActive + '" data-filter-kind="' + kind + '" data-dimension="' + escapeHtml(key) + '" data-clear="true" aria-pressed="' + String(!selected.length) + '">不限</button>'];
    options.forEach(function (option) {
      var active = selected.indexOf(option.value) >= 0;
      buttons.push('<button type="button" class="il-facet-option' + (active ? " active" : "") + '" data-filter-kind="' + kind + '" data-dimension="' + escapeHtml(key) + '" data-value="' + escapeHtml(option.value) + '" aria-pressed="' + String(active) + '">' + escapeHtml(option.label) + "</button>");
    });
    return '<div class="il-facet-row" data-facet-dimension="' + escapeHtml(key) + '"><div class="il-facet-label">' + escapeHtml(label) + '</div><div class="il-facet-options">' + buttons.join("") + "</div></div>";
  }

  root.ImageLibraryFacets = Object.freeze({
    buildTagDimensions: buildTagDimensions,
    facetRow: facetRow,
    splitTagDimension: splitTagDimension,
  });
})(window);
