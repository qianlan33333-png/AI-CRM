# ADR: terminal effect and plan revision closure

Date: 2026-07-20

## Status

Accepted.

## Context

The success-only `external_effect.completed` contract correctly protected provider
truth, but a blocked, cancelled, terminal failure, simulated result, or uncertain
dispatch could leave its business parent indefinitely pending or running. Group Ops
also materialized future work from plan state whose idempotency key did not include all
mutable business inputs, so later edits could leave an old pre-provider graph eligible.

## Decision

1. Introduce payload-minimal `external_effect.settled` for every new runtime terminal
   job transition. The job, terminal attempt when one exists, and the outbox envelope
   commit together. `external_effect.completed` remains success-only.
2. Register independent settlement projections for Identity, Group Ops, Welcome,
   Broadcast, and External Push. They reload canonical state, do not access restricted
   provider results, and close only the parent records they own.
3. Treat cancellation without a new provider attempt as attemptless. An older retryable
   attempt remains immutable audit evidence and is never relabelled as the cancellation.
4. Include plan, node, content, owner, schedule, target set, and lifecycle timestamps in
   the Group Ops revision fingerprint. Any mutation first invalidates the old
   pre-provider graph under the plan advisory lock, then materializes the active new
   revision when applicable.
5. Never cancel or replay work that crossed the provider boundary. Preserve its
   canonical result and surface the boundary in graph invalidation evidence.
6. Keep pre-cutover history-freeze rows held and non-replayed; the new settlement
   contract applies to new runtime transitions, not to manufacturing historical work.

## Consequences

- Every active parent has an explicit terminal projection path instead of inferring
  completion from a missing retry.
- Success provider-result consumers and non-success parent closure remain separate.
- Plan edits cannot silently reuse an effect graph built from stale content or targets.
- Raw graph cancellation must use `UPDATE ... RETURNING` and enqueue one settlement for
  every cancelled child in the same transaction.
