# Questionnaire and Radar R09 Reconciliation

Deployment and routine diagnostics use count-only mode:

```bash
python scripts/ops/reconcile_questionnaire_radar.py
```

The command reports only aggregate counts for missing outbox/event/effect lineage, duplicate effects, effect/attempt/planner inconsistencies, successful tag effects missing local projection, and retired retry residue. It returns no questionnaire answers, webhook payloads, mobile, openid, unionid, or external_userid. It never relays an outbox, runs a consumer, dispatches a provider, or updates Radar events.

Actionable submission continuity is scoped to the production auto-execute cutover
(`2026-07-13 16:20:00 UTC`). Before that instant questionnaire events were
deliberately shadow-only; migration `0109_questionnaire_auto_execute` records a
terminal audit skip for their pending consumers so historical webhooks and WeCom
mutations cannot be replayed. The report retains pre-cutover missing-effect counts
under `historical_counts`, but only `counts` controls `has_anomalies`.

A retained canonical `questionnaire.submitted` Internal Event is sufficient evidence
after its transactional outbox envelope has been relayed and later removed. Planner
consistency applies only to effects linked to that canonical event type; legacy effects
and completed legacy retry logs are not actionable R09 gaps.

Confirm these flags on every run:

```text
mode=count_only
database_mutation_performed=false
consumer_executed=false
provider_executed=false
real_external_call_executed=false
pii_in_output=false
```

## Continuation-only repair

Repair requires an explicit actor and reason:

```bash
python scripts/ops/reconcile_questionnaire_radar.py \
  --repair \
  --actor "$OPERATOR" \
  --reason "approved questionnaire continuation recovery" \
  --limit 100
```

Repair may only add a missing, idempotent `questionnaire.submitted` outbox envelope for an already committed submission. Actor and reason hashes are persisted in the outbox summary. It does not create an External Effect directly, retry a retired questionnaire push log, execute an Internal Event consumer, repair contact-tag projection, or call WeCom/webhooks.

After repair, run count-only mode again and let the canonical Internal Event and External Effect workers process the durable continuation under their normal gates. Duplicate effects, unknown-after-dispatch attempts, missing post-provider projection, and legacy retry residue require manual investigation; never restart a retired external-push worker or restore the old H5/provider path.

## Radar retention and identity

`radar_click_events` is append-only under the sole `aicrm_next.extensions.radar.radar_links` write owner. Raw identity may enter only from a validated OAuth result or a verified signed viewer session. Events are retained with the owning Radar link and deleted through the foreign-key cascade when that link is deleted. Query parameters and plain cookies are not identity sources.
