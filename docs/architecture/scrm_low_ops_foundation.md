# AI-CRM low-operations SCRM foundation

This change establishes the control plane for the seven-domain modular
monolith. It intentionally does not perform a production runtime cutover,
physical package move, legacy table deletion, or real provider call.

## Implemented and active

- `CapabilitySpec` is the single static owner registry for the current 40
  contexts, 63 route groups, 11 configuration sections, 54 table domains, 31
  internal event types, 26 external effect types, and 11 job types.
- `DeploymentProfile` validates the core version, capability dependency closure,
  five runtime roles, configuration schema version, and secret-free profile
  content. `AICRM_DEPLOYMENT_PROFILE_PATH` is the only startup reference needed
  to select a checked-in profile.
- Enforced profiles remove disabled extension routes, static assets, event
  consumers, external-effect continuations, provider adapters, effects, and
  jobs. The external worker also blocks residual queued effects before the
  provider boundary.
- `ConfigDefinition` contributes all 130 schema and app-setting definitions,
  including 23 sensitive values that accept only `secretref:` references. Configuration releases use
  draft, validation, checksum-protected atomic publish, audit history, shadow
  comparison, and rollback-as-a-new-release.
- The admin console and API expose capability, deployment profile,
  configuration-definition, release, validation, publish, rollback, and
  redacted shadow-comparison views.
- The canonical SCRM interface layer defines `CustomerRelationship`,
  `AudienceSpec`, `ContentBundle`, `CampaignExecution`, and `ProviderReceipt`,
  plus their public ports. It does not introduce an arbitrary object model or
  executable audience DSL.
- The versioned Job Catalog binds work to `web`, `callback`, `internal_worker`,
  `external_worker`, and `scheduler`. Only `external_worker` may call a real
  provider. The internal worker entrypoint combines inbox, internal-event, and
  outbox lanes in one process while preserving the legacy queue-kind arguments.
- Architecture gates forbid premature physical moves and legacy table drops
  without 30 days of zero-read/write evidence, verified export, rollback
  rehearsal, successor ownership, and approval.

## Compatibility mode

`deploy/deployment_profiles/wecom-core.json` and
`deploy/runtime_role_catalog.json` are checked in with observation/cutover
disabled. In this state all existing HTTP paths, auth rules, event names,
effect types, database behavior, runtime units, and extension behavior remain
unchanged. This is the safe first release for shadow comparison.

An `enforce` profile is a separate reviewed release decision. It must not be
enabled until the target instance has route, event, effect, job, configuration,
and historical-data parity evidence.

## Deliberately not completed in this release

- Business-domain environment reads have not yet reached zero. The runtime
  contract inventory continues to expose every reference while settings are
  migrated owner by owner through shadow comparison.
- The historical cross-context import baseline has not yet been reduced to the
  target of 120. Physical directory moves remain disabled until public ports
  and unique table-write ownership are proven.
- Existing service and timer units remain authoritative. The new scheduler is
  catalog-only and fails closed on `--execute`; successor parity is required
  before any unit retirement.
- The five canonical SCRM contracts are stable integration seams; existing
  identity, audience, content, campaign, and receipt tables are not bulk-moved
  or dual-written by this change.
- No legacy/retired table is deleted. Each future drop must be released
  independently from runtime cutover and directory movement.
- The 1,200 callbacks/minute performance acceptance test and sequential
  customer-instance rollout require deployment infrastructure and production-
  representative load; they are not claimed by repository-only tests.

## Next safe sequence

1. Deploy this release in observation mode and collect configuration shadow
   comparisons per instance.
2. Migrate direct business configuration reads one capability at a time and
   add public command/query ports before changing package paths.
3. Run successor parity for each candidate job, then switch one runtime unit in
   an independent release with the legacy unit still recoverable.
4. Enable extension enforcement on a baseline instance, verify no residual
   route, consumer, effect, or job, and then upgrade customer instances one at
   a time.
5. Start the 30-day zero-read/write clock for each legacy table only after its
   successor owner is authoritative.
