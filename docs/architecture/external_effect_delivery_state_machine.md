# External effect delivery state machine

`external_effect_job` is the canonical queue for real external side effects. Scheduler,
realtime callback, and manual execution all enter the same durable claim boundary.

## Claim and lease

- Scheduler claims only due `queued` or `failed_retryable` jobs.
- Scheduler and explicit `dispatch_one(job_id)` claims both honor `scheduled_at`,
  retry backoff, and `attempt_count < max_attempts`.
- A claim atomically changes the job to `dispatching` and assigns a unique
  `lease_token` plus `lease_expires_at`.
- Result persistence compares both `status = dispatching` and the lease token.
  A worker that lost its lease cannot mutate the open attempt or job result; the
  already durable `dispatching` attempt remains for stale-lease quarantine.
- Immediately before entering an adapter, the worker commits one
  `external_effect_attempt(status = dispatching)` and links it through
  `last_attempt_id`. A provider result may only complete that existing attempt;
  a success without this durable boundary is rejected.
- Expired `dispatching` jobs are quarantined as `unknown_after_dispatch`. They are
  never automatically requeued because the former worker may have reached the provider.

## Truthful terminal states

| State | Required evidence | Automatic retry |
| --- | --- | --- |
| `succeeded` | A real side effect executed and an accepted provider response or receipt was persisted | No |
| `simulated` | A fake/fixture adapter completed without a real external call | No |
| `blocked` | Policy, allowlist, kill switch, or validation prevented the provider call | No |
| `failed_retryable` | The provider returned a definite response that is safe to retry | Yes, when due |
| `failed_terminal` | A definite non-retryable provider or payload failure | No |
| `unknown_after_dispatch` | Dispatch started but the provider outcome or result persistence is uncertain | Never |

An adapter may not turn `side_effect_executed = false` into `succeeded`. Fake success
is `simulated`; a claimed success without real execution is `blocked`. A real call
without provider evidence is `unknown_after_dispatch`.

The existing attempt row, final job transition, and terminal internal-event outbox
envelopes are committed in one transaction. Every new runtime transition into
`succeeded`, `simulated`, `unknown_after_dispatch`, `failed_terminal`, `blocked`, or
`cancelled` emits `external_effect.settled`. A successful transition also emits the
existing `external_effect.completed`; that event remains success-only and preserves
all provider-result access controls. A cancellation that did not create a new provider
attempt emits an attemptless settlement, even when the job retains an older retryable
attempt for audit.

A successful provider call followed by an unpersisted result or outbox hand-off is
quarantined as unknown, not retried as if no call occurred. Post-success and terminal
projections execute from durable internal events and cannot rewrite provider truth.

`external_effect.completed` has an authoritative per-continuation fan-out. Identity,
Group Ops, Broadcast, Questionnaire, External Push, Automation, and any separately
registered media dependency each own a distinct `internal_event_consumer_run`, attempt
history, and retry budget. A predicate miss is a successful no-op; one continuation
failure cannot block a sibling or change a succeeded provider job/attempt. The former
`external_effect_completion_continuation_consumer` is only a handler alias for held
historical runs and is excluded from every new fan-out manifest. Restricted provider
payload is loaded only after an explicitly allowlisted continuation predicate matches;
job, terminal attempt, and tenant linkage must all match before that read.

`external_effect.settled` has a separate, payload-minimal fan-out. Identity, Group Ops,
Welcome, Broadcast, and External Push each own an independent projection run. These
consumers reload the canonical job/attempt, cannot request restricted provider result,
and close their parent queue, graph, recipient, message, or delivery state for every
non-success terminal outcome. A success is a no-op in this fan-out because its business
continuations remain owned by `external_effect.completed`.

## Cancellation

- Every mutable job exposes a monotonic `row_version`; a supplied
  `expected_version` must match before a cancellation request is accepted.
- A queued/planned/retryable job is cancelled with a status CAS.
- Graph and plan cancellation paths use `UPDATE ... RETURNING` and enqueue one
  attemptless settlement per cancelled child in the same transaction; direct table
  updates without this hand-off are not an allowed runtime path.
- A leased job records `cancel_requested_at/by/reason` without clearing the lease.
- The worker may settle the request only before the durable provider attempt exists.
  Once that boundary exists, the provider result wins and the request remains audit
  evidence rather than overwriting the result.

## Manual recovery

`unknown_after_dispatch` requires provider-side reconciliation. Manual retry requires
an actor, a reason, and an explicit `confirm_duplicate_risk = true` acknowledgement.
The authorization is appended to `external_effect_attempt` before the job is queued.

History-freeze quarantine remains deliberately held and non-replayed. It is not a new
runtime dispatch transition and must not manufacture settlement work for the frozen
pre-cutover population.

## Broadcast fake adapters

Fake WeCom private/group broadcast responses use `simulated` in `broadcast_jobs`,
`outbound_tasks`, and cloud-plan recipient/message projections. They never set `sent`,
increment `sent_count`, or populate `sent_at`.

## Broadcast delivery boundary

Private broadcast jobs use an equivalent but separate R10 state machine because
`broadcast_jobs` is also the durable scheduling ledger:

1. Claim only `queued`, expired `claimed`, or due `failed_retryable` rows with a
   claim token.
2. Commit `claimed -> dispatching` and align the current cloud recipient/message
   before calling WeCom.
3. Let the dispatcher return redacted request/response evidence without writing
   any delivery table.
4. In one transaction, lock by `id + dispatching + claim_token`, upsert the
   one-to-one `outbound_tasks` evidence, align recipient/message projections,
   append a `broadcast_job_events` row, update the terminal job state, and only
   then clear the token.

`dispatching` and `unknown_after_dispatch` are excluded from automatic reclaim.
If provider execution may have occurred and terminal persistence fails, the
worker changes every available projection to `unknown_after_dispatch` and sets
`reconciliation_required=true`. It never resends that job automatically.

Migration `0103_broadcast_delivery_state_machine` adds the state/evidence
columns and the one-to-one outbound-task link. The count-only command
`scripts/ops/reconcile_group_ops_broadcast.py` reports gaps but cannot repair or
call a provider.
