# AI-CRM Capability Registry

`aicrm_next.capability_registry` is the static ownership source for the
low-operations modular monolith. It groups the current 40 physical contexts
under seven stable core capabilities and opt-in extension packs without
changing existing HTTP, database, event, or external-effect behavior.

## Core capabilities

- `core.app`
- `core.platform`
- `core.crm`
- `core.channels`
- `core.engagement`
- `core.automation`
- `core.insights`

Extensions remain disabled by default in the core-only development profile.
Switching a reviewed profile to `enforce` removes disabled routes, event
consumers, external effects, provider adapters, static assets, and jobs
together. Hiding a navigation entry alone is never considered capability
deactivation.

`deploy/deployment_profiles/production-current.json` is the separately
versioned production profile. It lists every capability currently deployed on
the baseline instance and runs in `enforce` mode. The deployment workflow
persists its absolute path through the one startup reference before any runtime
is restarted. Because every currently deployed extension is explicitly
enabled, enforcement is behavior-equivalent: no route, consumer, effect
adapter, job, static mount, or historical extension is removed by this cutover.
The core-only `wecom-core.json` remains the safe default when no production
profile is selected.

## Ownership rules

- Each current context, route group, config section, table domain, internal
  event type, and external effect type resolves to exactly one capability.
- Dependencies are explicit and acyclic.
- Router composition fails closed when a route group is not registered.
- Business code must not use dynamic imports to discover capability modules.
- `tools/check_capability_registry.py` is part of the fast architecture gate
  and rejects ownership gaps or accidental opt-in extensions.

The current registry resolves 40 contexts, 63 route groups, 11 configuration
sections, 54 table domains, 31 internal event types, 26 external effect types,
and 11 job types to exactly one logical owner.

The registry is deliberately code-backed and static. It is not a runtime
plugin marketplace and does not permit uploading or executing customer code.

## Migration and rollback

Physical directories remain unchanged in the first stage. This avoids a large
rename-only diff while dependency ownership is still being corrected. Rollback
is a previous-release rollback; no legacy runtime or dual-execution fallback is
introduced.

The detailed implementation and cutover status is recorded in
`docs/architecture/scrm_low_ops_foundation.md`.
