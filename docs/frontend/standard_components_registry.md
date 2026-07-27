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
```

The composer only configures `SendContentPackage`: `content_text`, `image_library_ids`, `miniprogram_library_ids`, and `attachment_library_ids`.

It does not own operation mode, profile template selection, behavior rule selection, agent selection, audience preview, send constraints, or backend route selection. Those decisions belong to the outer page.

When `textEnabled=false`, the composer hides the manual copy textarea and the customer-name insertion control. Agent mode uses this form and only configures local material IDs.

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

## Automation Operation Page

The automation operation page owns four outer modes:

- unified content
- profile-layered content
- behavior-layered content
- agent personalized content

`profile_layered` requires the outer page to select a profile segment template first. `behavior_layered` requires the outer page to select the behavior rule. `agent` requires the outer page to select `agent_code` and opens the composer with `textEnabled=false`.

All new development stays under `aicrm_next`; do not double-write old Flask templates or static files.
