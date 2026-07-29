# WeCom welcome hard-real-time execution ownership

WeCom welcome codes have a 20-second provider window. This flow is a one-shot,
hard-real-time path: a durable row is an idempotency and audit boundary, never a
backlog that can be replayed after the deadline. The application computes one
absolute provider deadline from the original callback receipt time. It stops
dispatch at 18 seconds, reserving the final two seconds for the provider round
trip. Historical repair commands never recreate or replay a welcome send.

The path has reserved capacity end to end:

- every callback carrying a WelcomeCode enters `wecom_welcome_ingress`, even if
  it has no channel `State`, separate from the ordinary `webhook_inbox` lane;
- welcome media uploads and the final send enter `wecom_welcome`, separate from
  interactive, bulk, ordinary media, and outbound-webhook effects;
- both lanes have two in-flight slots, and the global limit is increased by the
  same four slots; the atomic claim gate subtracts active welcome capacity from
  the ordinary-work budget, so ordinary traffic cannot consume that headroom;
- notification wake-up is primary; a 250 ms sweep is only crash/lost-notify
  recovery, not the normal scheduling path.

Channel-entry callbacks persist a `channel_welcome_effect_graph`. An unresolved
image, file, or mini-program thumbnail becomes its own `wecom.media.upload`
external effect in the same `wecom_welcome` lane. The final
`wecom.welcome_message.send` effect stays `planned` until every dependency has
completed successfully. Successful media completion releases the final row
immediately after the media result is durable. The durable completion event
remains an idempotent recovery path if the process stops between those commits;
it is not the normal real-time hand-off.

`WeComWelcomeMessageAdapter` accepts provider-ready attachments only. It does
not read the media library, resolve a lease, upload a file, or perform any
other provider call before `send_welcome_msg`. Therefore one claimed welcome
effect and one attempt have exactly one provider request boundary.

Every welcome effect has `max_attempts = 1`. The repository checks the absolute
deadline atomically before recording a provider attempt, and the adapter checks
it again immediately before HTTP. An elapsed job becomes terminal `expired`,
emits its durable settlement record, and makes zero provider calls. Generic
retry, operator repair, and delayed event delivery cannot cross that boundary.

The former automation-ops media refresher is retired. Normal media work is
created on demand by the owning business graph. `enqueue_due_media_refreshes`
is retained only for the operator-run `scripts/backfill_wecom_media_leases.py`
repair command and requires `repair_authorized=True`; it must not be restored
to a timer or scheduler.

Cancellation is fail-closed: only jobs that have not crossed the provider
boundary are cancelled, and a cancelled graph can never release its final
welcome effect. A failed or expired upload leaves the final effect terminally
blocked; there is no welcome-code repair. Repeated planning and repeated
completion delivery are idempotent and never create another provider path.
