# Event And External Effect Table Lineage

This document records the PR #13 boundary between internal events, business
outboxes, legacy webhook delivery ledgers, and the canonical external-effect
queue. It is intentionally a lineage view first; high-risk physical table
merges must be done in later PRs with producer/consumer evidence.

## Canonical Roles

| Table | Role | Lifecycle | Current owner | Convergence rule |
| --- | --- | --- | --- | --- |
| `internal_event_outbox` | Transactional durability boundary for existing internal business events | queue | `platform_foundation.internal_events` | Keep. Business-critical producers append through the owner boundary in the business transaction; relay creates the canonical event and runs. |
| `internal_event` | Canonical internal event ledger | event | `platform_foundation.internal_events.service` | Keep. Internal handlers must not execute real external calls directly. |
| `internal_event_consumer_run` | Internal event consumer queue state | queue | `platform_foundation.internal_events` | Keep. One row per event/consumer execution state. |
| `internal_event_consumer_attempt` | Internal consumer attempt audit | audit | `platform_foundation.internal_events` | Keep. Append-only execution evidence. |
| `external_effect_job` | Canonical external side-effect queue | queue | `platform_foundation.external_effects` | Keep. Real external calls enter through the capability-owned ports. |
| `external_effect_attempt` | External side-effect attempt audit | audit | `platform_foundation.external_effects` | Keep. Append-only execution evidence owned with the queue. |
| `domain_event_outbox` | Historical commerce business-event outbox | legacy boundary | no active payment producer | Read-only parity/reconciliation after R08; do not create new `transaction.paid` rows. |
| `external_push_delivery` | Commerce order-push projection | legacy_boundary | `payment.succeeded:webhook_order_paid_consumer` | Created atomically with one canonical `external_effect_job`; keep for existing admin read compatibility. |
| `outbound_webhook_deliveries` | Legacy outbound webhook delivery queue/ledger | queue | outbound webhook runtime/admin jobs | Candidate to converge to external effects after webhook retry parity is proven. |
| `outbound_event_outbox` | Legacy outbound event outbox | event | legacy outbound publisher | Candidate after producers/readers are audited. |

## Allowed Flow

```text
domain business action
  -> internal_event_outbox in the same business transaction
  -> idempotent relay
  -> internal_event for in-process business state fanout
  -> internal_event_consumer_run / internal_event_consumer_attempt for handler execution
  -> external_effect_job for real external side effects
  -> external_effect_attempt for external execution evidence
```

Commerce-specific R08 flow is:

```text
paid order + payment.succeeded outbox (one transaction)
  -> payment.succeeded internal event
  -> webhook_order_paid_consumer
  -> external_push_delivery + external_effect_job (one transaction)
  -> external-effect worker
```

Webhook-specific legacy flow still exists:

```text
outbound event
  -> outbound_event_outbox / outbound_webhook_deliveries
  -> future external_effect_job convergence after retry and status parity is proved
```

## PR #13 Guardrails

1. `internal_event*` remains internal only; external calls must be queued through
   `external_effect_job`.
2. `internal_event_outbox` relay creates `internal_event` and every registered
   `internal_event_consumer_run` in one local transaction. Duplicate relay reuses
   the existing event/run unique keys.
3. `external_effect_job` and `external_effect_attempt` are the canonical external
   side-effect queue and audit pair.
4. `domain_event_outbox`, `external_push_delivery`,
   `outbound_webhook_deliveries`, and `outbound_event_outbox` are not deleted in
   PR #13 because active runtime references still exist.
5. Later deletion PRs must prove producer and reader parity before marking any of
   the legacy boundary tables as retired.

## R08 closure

R08 cuts the active `domain_event_outbox` producer and retires the legacy external-push timer/service. Historical rows remain readable for parity. `external_push_delivery` remains an admin-compatible projection, but its insert/update and the corresponding External Effect job now share one caller-owned transaction. Reconciliation is count-only by default and can repair only durable internal-event continuations.
