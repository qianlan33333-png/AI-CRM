# ADR: Every retired queue owner has one declared Next successor

Date: 2026-07-20

## Decision

The PR-3 runtime inventory is fail closed at the capability level. Every timer
or persistent service in `cutover_managed_legacy` must have exactly one row in
`cutover_successor_matrix`. A row declares the capability, successor unit,
health contract, and backlog contract. Missing, duplicate, legacy, or inactive
successors make the production runtime manifest invalid.

Broadcast delegation and Group Ops planning remain separate bounded-context
owners. They use the reviewed Next-native entrypoints through
`aicrm-next-broadcast-delegation.timer` and
`aicrm-next-group-ops-planning.timer`. Both produce durable External Effects;
neither calls WeCom. The PostgreSQL External Effect runtime remains the single
provider owner.

## Safety

- The old `openclaw-*` timers remain retired and disabled.
- Replacement timers activate only after a committed positive generation.
- Before cutover they are installed but disabled, so there is no dual owner.
- Existing held/history rows remain held; no migration or automatic replay is
  introduced.
- Rollback is the previous exact release, never restoration of a legacy timer.

## Verification

The runtime-unit manager and queue cutover checker both compare the complete
reviewed successor matrix. Deployment copies, enables, restarts, and verifies
each replacement timer only when the cutover marker is committed. Runtime
contract inventory exposes the replacement units for read-only diagnostics.
