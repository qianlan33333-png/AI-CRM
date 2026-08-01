# Standard Components Registry

## SendContentComposer

Frontend asset: `aicrm_next/app/admin_console/static/admin_console/send_content_composer.js`

Global API:

```js
window.AICRMSendContentComposer.open({
  title,
  textEnabled,
  value,
  limits,
  onConfirm,
  onCancel
})

window.AICRMSendContentComposer.mount(container, {
  title,
  textEnabled,
  value,
  limits,
  maxTotal,
  onChange
})
```

`open()` remains the standard modal API. `mount()` is the backwards-compatible inline mode for an already independent configuration page: it renders the full auto-growing copy textarea, standard material actions, selected items and live preview in the provided container. Inline edits call `onChange(contentPackage)` but do not save automatically; the outer page remains responsible for submission.

The composer only configures `SendContentPackage`: `content_text`, `image_library_ids`, `miniprogram_library_ids`, `attachment_library_ids`, and `group_invite_library_ids`. Defaults are image 3, miniprogram 1, attachment 9, group invite 1, and total materials 9. A caller may reduce type limits but must not bypass the total-material boundary.

It does not own operation mode, profile template selection, behavior rule selection, agent selection, audience preview, send constraints, or backend route selection. Those decisions belong to the outer page.

When `textEnabled=false`, the composer hides the manual copy textarea and the customer-name insertion control. Agent mode uses this form and only configures local material IDs.

Use `mount()` only when the page itself is already the focused content-configuration surface. List pages and mixed workflows should continue to use `open()` so the composer does not displace the page's primary task.

## MaterialPicker

Frontend asset: `aicrm_next/app/admin_console/static/admin_console/material_picker.js`

Global API:

```js
window.AICRMMaterialPicker.open({
  type,
  selectedIds,
  limit,
  onConfirm,
  onCancel
})
```

The picker only reads the Next-native material picker API:

- `GET /api/admin/material-picker/items?type=image`
- `GET /api/admin/material-picker/items?type=miniprogram`
- `GET /api/admin/material-picker/items?type=attachment`

Business pages must not directly fetch image, miniprogram, or attachment library APIs to render their own private material grids.

## AutomationCapabilitySelector

Frontend assets:

- `aicrm_next/app/admin_console/static/admin_console/automation_capability_selector.js`
- `aicrm_next/app/admin_console/static/admin_console/automation_capability_selector.css`

Global API:

```js
const selector = window.AutomationCapabilitySelector.mount(container, {
  items,
  value,
  currentPackageId,
  onChange
})
```

The inline selector owns the `Agent 机器人` / `固定话术` type tabs and the one-to-one availability presentation. An active unbound automation, or an active automation already bound to the current package, is selectable. Paused automations and automations bound to another package remain visible but disabled with the reason shown. The outer page owns fetching, PUT/DELETE persistence, warning messages, and the explicit unbind action.

## Automation Operation Page

The automation operation page owns four outer modes:

- unified content
- profile-layered content
- behavior-layered content
- agent personalized content

`profile_layered` requires the outer page to select a profile segment template first. `behavior_layered` requires the outer page to select the behavior rule. `agent` requires the outer page to select `agent_code` and opens the composer with `textEnabled=false`.

All new development stays under `aicrm_next`; do not double-write old Flask templates or static files.
