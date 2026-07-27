# SendContentPackage Contract

`SendContentPackage` is the only backend contract for the standard send content component in AI-CRM Next.

This capability is Next-native only. New backend behavior must be implemented under `aicrm_next/engagement/send_content` or the existing `aicrm_next/automation/automation_engine` layers. Do not add new `wecom_ability_service/http/*`, `wecom_ability_service/domains/*`, `production_compat`, or legacy facade implementations for this surface.

## Payload

```json
{
  "content_text": "",
  "image_library_ids": [],
  "miniprogram_library_ids": [],
  "attachment_library_ids": [],
  "group_invite_library_ids": []
}
```

Normalization rules:

- `content_text` is trimmed and limited to 4000 characters.
- `image_library_ids` accepts at most 3 positive integer IDs.
- `miniprogram_library_ids` accepts at most 1 positive integer ID.
- `attachment_library_ids` accepts at most 9 positive integer IDs.
- `group_invite_library_ids` accepts at most 1 positive integer compatibility ID. In operator-facing pages this is selected by customer-group name, then resolves to the group's bound WeCom `work.weixin.qq.com/gm/...` link card.
- IDs are deduplicated while preserving input order.
- Missing fields normalize to an empty string or empty arrays.
- `text_enabled=false` forces `content_text=""`.
- `require_body=true` requires at least one of text, image IDs, miniprogram IDs, attachment IDs, or group invite IDs.
- `require_body=false` allows an empty package for draft saves.

The component outputs only `content_text` plus the four compatibility ID arrays. `group_invite_library_ids` is retained to avoid breaking saved content packages; it is not presented as a material or media-id resource. Outer business pages own their audience, scheduling, sender, and delivery-mode fields.

## Customer group selection

- Operators select a synced customer group rather than creating or selecting a group-invite material.
- A group owner, administrator, or authorized AI operation binds one native WeCom `work.weixin.qq.com/gm/...` join link to each `chat_id` through `/api/admin/group-invite-library`.
- The settings page generates the link-card title and description from the group name. It does not expose media ID, media refresh, cover, config ID, state, or card-copy fields.
- The persisted `group_invite_library` row and `group_invite_library_ids` field are compatibility storage only. Sending continues to resolve the row into a WeCom `link` attachment, which has no media-ID lifecycle.

Frontend usage follows the same boundary. `AICRMSendContentComposer` only edits this package. The retired automation operation-task page no longer owns unified/profile-layered/behavior-layered/agent mode selection, profile template selection, behavior rule selection, or agent selection; new automation targeting belongs to AI Audience packages and agent copywriting flows.

In agent mode the composer must be opened with `textEnabled=false`, so no manual copy is returned and only material IDs are saved.

## HXC Broadcast Usage

HXC / funnel dashboard broadcast also uses `SendContentPackage`. The HXC page supplies audience and sender context, while `AICRMSendContentComposer` supplies only the text and four material ID arrays. HXC broadcast must call the Next-native `/api/admin/hxc-dashboard/broadcast-tasks` API and must not call the old Flask `/api/admin/hxc-dashboard/broadcast` route.

The HXC API normalizes with `require_body=true`, so empty packages are rejected. It does not upload materials to WeCom or resolve `media_id` in this phase.
