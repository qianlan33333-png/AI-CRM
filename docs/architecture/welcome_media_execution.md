# Welcome media execution ownership

Channel-entry callbacks persist a `channel_welcome_effect_graph`. An unresolved
image, file, or mini-program thumbnail becomes its own `wecom.media.upload`
external effect in the `wecom_media` lane. The final
`wecom.welcome_message.send` effect stays `planned` until every dependency has
completed successfully; the completion consumer then resolves the provider
payload and releases the final row with a compare-and-set update.

`WeComWelcomeMessageAdapter` accepts provider-ready attachments only. It does
not read the media library, resolve a lease, upload a file, or perform any
other provider call before `send_welcome_msg`. Therefore one claimed welcome
effect and one attempt have exactly one provider request boundary.

The former automation-ops media refresher is retired. Normal media work is
created on demand by the owning business graph. `enqueue_due_media_refreshes`
is retained only for the operator-run `scripts/backfill_wecom_media_leases.py`
repair command and requires `repair_authorized=True`; it must not be restored
to a timer or scheduler.

Cancellation is fail-closed: only jobs that have not crossed the provider
boundary are cancelled, and a cancelled graph can never release its final
welcome effect. A failed upload leaves the final effect held for explicit
repair. Repeated planning and repeated completion delivery are idempotent.
