(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ServicePeriodMemberGridState = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const EMPTY_CONFIG = Object.freeze({
    schema_version: 1,
    filter: Object.freeze({logic: "and", conditions: Object.freeze([])}),
    sorts: Object.freeze([]),
    groups: Object.freeze([]),
  });

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function emptyConfig() {
    return clone(EMPTY_CONFIG);
  }

  function normalizeConfig(value) {
    const raw = value && typeof value === "object" ? value : {};
    const filter = raw.filter && typeof raw.filter === "object" ? raw.filter : {};
    return {
      schema_version: 1,
      filter: {
        logic: filter.logic === "or" ? "or" : "and",
        conditions: Array.isArray(filter.conditions) ? clone(filter.conditions) : [],
      },
      sorts: Array.isArray(raw.sorts) ? clone(raw.sorts) : [],
      groups: Array.isArray(raw.groups) ? clone(raw.groups) : [],
    };
  }

  function canonical(value) {
    return JSON.stringify(normalizeConfig(value));
  }

  function isDirty(draft, saved) {
    return canonical(draft) !== canonical(saved);
  }

  function discardDraft(saved) {
    return normalizeConfig(saved);
  }

  function pathKey(path) {
    return (Array.isArray(path) ? path : [])
      .map((part) => `${String(part.field || "")}:${JSON.stringify(part.value === undefined ? null : part.value)}`)
      .join("/");
  }

  function visibleGroupHeaders(previousPath, nextPath) {
    const previous = Array.isArray(previousPath) ? previousPath : [];
    const next = Array.isArray(nextPath) ? nextPath : [];
    let common = 0;
    while (
      common < previous.length &&
      common < next.length &&
      previous[common].field === next[common].field &&
      JSON.stringify(previous[common].value) === JSON.stringify(next[common].value)
    ) {
      common += 1;
    }
    return next.slice(common).map((_item, offset) => common + offset);
  }

  function rowHiddenByCollapsedPath(path, collapsedKeys) {
    const collapsed = collapsedKeys instanceof Set ? collapsedKeys : new Set(collapsedKeys || []);
    const parts = Array.isArray(path) ? path : [];
    for (let index = 0; index < parts.length; index += 1) {
      if (collapsed.has(pathKey(parts.slice(0, index + 1)))) return true;
    }
    return false;
  }

  function addOrder(config, kind, field, direction, limits) {
    const next = normalizeConfig(config);
    if (!new Set(["sorts", "groups"]).has(kind)) throw new Error("invalid order kind");
    const otherKind = kind === "sorts" ? "groups" : "sorts";
    if (next[kind].some((item) => item.field === field)) throw new Error("field already selected");
    if (next[otherKind].some((item) => item.field === field)) throw new Error("field already used by other order");
    const maximum = Number((limits || {})[kind] || (kind === "groups" ? 2 : 8));
    if (next[kind].length >= maximum) throw new Error("order limit reached");
    next[kind].push({field, direction: direction === "desc" ? "desc" : "asc"});
    return next;
  }

  function updateOrder(config, kind, index, patch) {
    const next = normalizeConfig(config);
    if (!next[kind] || !next[kind][index]) return next;
    next[kind][index] = {...next[kind][index], ...(patch || {})};
    return next;
  }

  function removeOrder(config, kind, index) {
    const next = normalizeConfig(config);
    if (next[kind]) next[kind].splice(index, 1);
    return next;
  }

  return {
    EMPTY_CONFIG,
    clone,
    emptyConfig,
    normalizeConfig,
    canonical,
    isDirty,
    discardDraft,
    pathKey,
    visibleGroupHeaders,
    rowHiddenByCollapsedPath,
    addOrder,
    updateOrder,
    removeOrder,
  };
});
