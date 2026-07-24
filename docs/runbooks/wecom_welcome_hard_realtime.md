# WeCom welcome hard-real-time runbook

## Contract

- Start the 20-second window at the original callback `received_at`.
- Stop starting provider calls at `received_at + 18s`.
- Use `wecom_welcome_ingress` for the callback and `wecom_welcome` for every
  media upload and final welcome send.
- Allow one attempt only. Never replay a WelcomeCode from repair, history, or a
  delayed queue row.
- Keep one canonical provider boundary: `WeComWelcomeMessageAdapter`.

## Release checks

1. Confirm Alembic head is `0141_production_welcome_timeout_ack_scope`.
2. Confirm both reserved lane policies are enabled with `max_in_flight = 2`.
   Confirm the ordinary-work admission budget is
   `global_max_in_flight - 4`; this is the actual reservation, not only a larger
   global number.
3. Confirm the queue runtime has one callback worker for
   `wecom_welcome_ingress` and one external-effect worker for `wecom_welcome`.
4. Confirm the runtime generation and rollout mode match the production
   cutover contract.
5. Use a newly generated WeCom scan/add event. Never use an old WelcomeCode as
   a release probe.

## Acceptance evidence

For one fresh scan, retain the callback/event/effect identifiers and prove:

- callback lane is `wecom_welcome_ingress`;
- every welcome graph job lane is `wecom_welcome`;
- final `provider_call_started_at - callback.received_at < 18s`;
- the final send has exactly one attempt and the provider returns `errcode = 0`;
- the internal job and graph both finish successfully.

If the deadline is missed, the valid result is `expired` with
`attempt_count = 0`, blank `provider_call_started_at`, and no provider request.
Do not retry it.

## Rollback

Roll back the application to the previous release and stop accepting new
welcome real-time work before changing queue policy. Wait at least 20 seconds
for all current WelcomeCodes to become unusable. Do not downgrade migration
0140 while either reserved lane contains history; its downgrade fails closed.
Never replay rows created by the rolled-back release.
