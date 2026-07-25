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
  content. Startup-only infrastructure values are isolated in a 32-key catalog;
  `AICRM_DEPLOYMENT_PROFILE_PATH` selects a checked-in profile through that
  boundary.
- Enforced profiles remove disabled extension routes, static assets, event
  consumers, external-effect continuations, provider adapters, effects, and
  jobs. The external worker also blocks residual queued effects before the
  provider boundary.
- `ConfigDefinition` contributes 348 schema and app-setting definitions,
  including 38 sensitive values that accept only `secretref:` references. Configuration releases use
  draft, validation, checksum-protected atomic publish, audit history, shadow
  comparison, and rollback-as-a-new-release.
- The configuration-cutover catalog now covers 237 unique managed settings. The
  initial platform/auth slice, provider-boundary slice, AI/commerce slice, and
  final domain slice assign an explicit capability owner and definition to every
  business runtime key. All business and application modules under
  `aicrm_next` are direct-environment-free: the generated inventory reports zero
  direct `os.getenv` / `os.environ` reads outside shared startup/runtime
  boundaries. Direct access is restricted by the architecture gate to six
  reviewed shared files; four currently contain 19 direct references. Every
  registered cutover key is resolved centrally, including callers that pass keys
  through a dynamic helper. The same gate requires every runtime key to have a
  `ConfigDefinition` and rejects new business-domain environment access.
- Migrated settings use an expand/contract cutover. Before activation, an
  existing environment value remains authoritative while the published value
  is shadow-compared. `AICRM_RUNTIME_CONFIG_CUTOVER_KEYS` can activate only a
  previously staged key whose semantic value matches the current environment;
  unknown keys, wildcard activation, same-release stage-and-switch, invalid
  secret references, and mismatches fail validation.
- Publish rechecks every affected setting with compare-and-swap preconditions
  inside the publish transaction. Cutover activation also guards the staged
  values that are not themselves changed by the activation release. After a
  key is activated, legacy admin-setting writes fail closed and only Config
  Release may update it or the cutover catalog.
- The admin console and API expose capability, deployment profile,
  configuration-definition, release, validation, publish, rollback, and
  redacted shadow-comparison views.
- The canonical SCRM interface layer defines `CustomerRelationship`,
  `AudienceSpec`, `ContentBundle`, `CampaignExecution`, and `ProviderReceipt`,
  plus their public ports. It does not introduce an arbitrary object model or
  executable audience DSL.
- `crm_user_identity` and `crm_user_identity_conflicts` now have one logical
  writer, `aicrm_next.identity_contact`. Channel-entry identity ingestion is
  classified under that owner, and sidebar mobile/profile/material mutations
  use the injected `IdentityWritePort` with the caller's existing transaction.
  The ownership guard scans all runtime Python SQL for those canonical tables,
  so a new cross-capability direct writer fails CI.
- Unresolved identity ingress from AI Audience, questionnaire submissions,
  archived messages, and channel contact tags now uses one versioned
  `IdentityResolutionQueuePort`. Source-specific idempotency keys, the caller's
  transaction, effect lineage, and fail-closed missing-unionid behavior remain
  intact; their former repository write-owner exceptions are removed. Queue
  claims, backfill outcomes, terminal settlement, completion receipts, and the
  authorized pre-provider cutover reopen also use the same owner port, leaving
  no cross-capability direct queue writer.
- The versioned Job Catalog binds work to `web`, `callback`, `internal_worker`,
  `external_worker`, and `scheduler`. Only `external_worker` may call a real
  provider. The internal worker entrypoint combines inbox, internal-event, and
  outbox lanes in one process while preserving the legacy queue-kind arguments.
  Each claimed job executes against one request-scoped runtime-settings
  snapshot so a multi-key configuration release cannot split a single job.
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

- Managed values remain in observation mode. Per-instance staging, redacted
  shadow comparison, and cutover activation are operational release steps and
  are not inferred from repository tests. No
  `AICRM_RUNTIME_CONFIG_CUTOVER_KEYS` value is activated by this change.
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

1. Deploy this release in observation mode, stage the 237 managed settings, and
   collect redacted configuration shadow comparisons per instance.
2. Activate only matching keys through the cutover catalog; migrate the next
   capability slice with the same expand/contract rule and add public
   command/query ports before changing package paths.
3. Run successor parity for each candidate job, then switch one runtime unit in
   an independent release with the legacy unit still recoverable.
4. Enable extension enforcement on a baseline instance, verify no residual
   route, consumer, effect, or job, and then upgrade customer instances one at
   a time.
5. Start the 30-day zero-read/write clock for each legacy table only after its
   successor owner is authoritative.
