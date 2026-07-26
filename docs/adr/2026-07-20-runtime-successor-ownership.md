# ADR: Every retired queue owner has one declared Next successor

Date: 2026-07-20

## Decision

The PR-3 runtime inventory is fail closed at the capability level. Every timer
or persistent service in `cutover_managed_legacy` must have exactly one row in
`cutover_successor_matrix`. A row declares the capability, successor unit,
health contract, and backlog contract. Missing, duplicate, legacy, or inactive
successors make the production runtime manifest invalid.

Broadcast delegation and Group Ops planning remain separate bounded-context
handlers, but share the reviewed clock owner
`aicrm-job-catalog-scheduler.timer`. The versioned job catalog invokes each
handler independently and also owns the AI Audience clock and record-only
reconciliation schedule. All produce durable internal facts or External
Effects; none calls WeCom. The PostgreSQL External Effect runtime remains the
single provider owner. Payment reconciliation is explicitly `observe_only` and
retains its separate timer.

## Safety

- The old `openclaw-*` timers remain retired and disabled.
- The consolidated scheduler activates only after its predecessor timer/service
  pairs are disabled and removed in the deployment transaction.
- The execute path requires an environment gate and exact fixed confirmation,
  so rollback or partial installation fails closed.
- Existing held/history rows remain held; no migration or automatic replay is
  introduced.
- Rollback is the previous exact release, never restoration of a legacy timer.

## Verification

The runtime-unit manager and job catalog checker both compare the complete
reviewed successor matrix. Deployment retires predecessor unit files before it
installs and restarts the single scheduler timer. Runtime contract inventory
and the read-only production diagnostic expose the consolidated owner, the
retired predecessors, the unchanged payment timer, and the last successful
post-release scheduler exit.
