import assert from "node:assert/strict";
import {createRequire} from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
require("../../aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid_state.js");
const GridState = globalThis.ServicePeriodMemberGridState;

test("view drafts stay explicit and do not mutate saved configuration", () => {
  const saved = GridState.emptyConfig();
  const draft = GridState.clone(saved);
  draft.filter.conditions.push({field: "member", operator: "contains", value: "浅蓝"});

  assert.equal(GridState.isDirty(draft, saved), true);
  assert.equal(saved.filter.conditions.length, 0);
  assert.equal(GridState.isDirty(GridState.clone(saved), saved), false);
});

test("discarding a draft restores an independent copy of the saved view", () => {
  const saved = GridState.emptyConfig();
  saved.groups.push({field: "formally_logged_in", direction: "asc"});
  const restored = GridState.discardDraft(saved);

  assert.equal(GridState.isDirty(restored, saved), false);
  restored.groups.push({field: "token_usage", direction: "asc"});
  assert.equal(saved.groups.length, 1);
});

test("group header calculation merges identical paths across page boundaries", () => {
  const priorPageLastPath = [
    {field: "formally_logged_in", value: "yes"},
    {field: "token_usage", value: "no"},
  ];
  const nextPageFirstPath = [
    {field: "formally_logged_in", value: "yes"},
    {field: "token_usage", value: "no"},
  ];
  const laterPath = [
    {field: "formally_logged_in", value: "yes"},
    {field: "token_usage", value: "unmatched"},
  ];

  assert.deepEqual(GridState.visibleGroupHeaders(priorPageLastPath, nextPageFirstPath), []);
  assert.deepEqual(GridState.visibleGroupHeaders(nextPageFirstPath, laterPath), [1]);
});

test("session collapse hides descendants at either grouping depth", () => {
  const path = [
    {field: "formally_logged_in", value: "yes"},
    {field: "token_usage", value: "no"},
  ];
  const parentKey = GridState.pathKey(path.slice(0, 1));
  const childKey = GridState.pathKey(path);

  assert.equal(GridState.rowHiddenByCollapsedPath(path, new Set()), false);
  assert.equal(GridState.rowHiddenByCollapsedPath(path, new Set([parentKey])), true);
  assert.equal(GridState.rowHiddenByCollapsedPath(path, new Set([childKey])), true);
});

test("sort and group fields remain unique and respect limits", () => {
  let config = GridState.emptyConfig();
  config = GridState.addOrder(config, "groups", "formally_logged_in", "asc", {groups: 2, sorts: 8});
  config = GridState.addOrder(config, "sorts", "remaining_days", "desc", {groups: 2, sorts: 8});

  assert.deepEqual(config.groups, [{field: "formally_logged_in", direction: "asc"}]);
  assert.deepEqual(config.sorts, [{field: "remaining_days", direction: "desc"}]);
  assert.throws(
    () => GridState.addOrder(config, "sorts", "formally_logged_in", "asc", {groups: 2, sorts: 8}),
    /other order/,
  );
});
