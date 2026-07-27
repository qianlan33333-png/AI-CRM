import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const share = readFileSync(resolve(root, "aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid_share.js"), "utf8");
const grid = readFileSync(resolve(root, "aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid.js"), "utf8");
const internalTemplate = readFileSync(resolve(root, "aicrm_next/extensions/commerce/service_period/templates/service_period_member_grid.html"), "utf8");
const publicTemplate = readFileSync(resolve(root, "aicrm_next/extensions/commerce/service_period/templates/service_period_member_grid_public.html"), "utf8");

test("share dialog is super-admin gated and uses the cached WeCom directory", () => {
  assert.match(share, /access\.can_manage_share/);
  assert.match(share, /scope: "wecom_directory"/);
  assert.match(share, /allowRefresh: false/);
  assert.match(share, /includeInactive: false/);
  assert.match(share, /disabledUserIds/);
  assert.match(share, /\/collaborators/);
  assert.match(share, /permission: "read"/);
  assert.match(share, /body: \{permission, version: collaborator\.version\}/);
  assert.match(share, /body: \{version: collaborator\.version\}/);
  assert.match(share, /\/external-share/);
  assert.match(share, /body: \{enabled, version: state\.externalShare\.version\}/);
  assert.match(internalTemplate, /id="spShareButton"/);
  assert.match(internalTemplate, /id="spShareDialog"/);
});

test("public grid reads the token from the fragment and submits only saved-view paging", () => {
  assert.match(grid, /window\.location\.hash/);
  assert.match(grid, /"X-AICRM-Grid-Share-Token": shareToken/);
  assert.match(grid, /credentials: "omit"/);
  assert.match(grid, /cache: "no-store"/);
  assert.match(grid, /referrerPolicy: "no-referrer"/);
  assert.match(grid, /\{view_id: state\.activeViewId, cursor, limit: 100\}/);
  assert.doesNotMatch(grid, /publicMode\s*\?\s*\{[^}]*config:/s);
  assert.match(publicTemplate, /meta name="referrer" content="no-referrer"/);
  assert.match(publicTemplate, /只读分享/);
  assert.match(publicTemplate, /id="spInternalToolbar" hidden/);
  assert.doesNotMatch(publicTemplate, /member_grid_share\.js/);
});

test("public mode disables editing and view management in the shared renderer", () => {
  assert.match(grid, /elements\.addView\.hidden = publicMode \|\| !state\.canManageViews/);
  assert.match(grid, /elements\.viewMenuButton\.hidden = publicMode/);
  assert.match(grid, /editableFields: new Set\(\)/);
  assert.match(grid, /if \(publicMode\) \{[\s\S]*?state\.access = \{can_edit: false, can_manage_share: false\};[\s\S]*?\} else \{/);
  assert.match(grid, /if \(publicMode\) return;/);
});
