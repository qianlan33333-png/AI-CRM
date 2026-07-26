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
- `manage_runtime_config_release.py` and the production-environment workflow
  add a release-SHA-bound operational path for redacted inventory, staging,
  bounded activation, and rollback. Staging blocks raw secrets, activation is
  limited to 25 exact keys and a digest-bound confirmation, and every action
  asserts that no real external provider call occurred.
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
- `wecom_external_contact_event_logs` now has one storage owner,
  `aicrm_next.identity_contact`. Channel callback processing keeps its existing
  transaction and compatibility functions but reaches audit and identity-sync
  state only through the versioned `IdentityEventLogPort`.
- `contact_tags` now has one logical writer, `aicrm_next.customer_tags`.
  Channel-entry snapshots resolve canonical unionid and then use the public
  `CustomerTagProjectionPort` in the existing transaction; unresolved aliases
  continue into identity recovery without creating tag rows.
- `sync_runs` now has one platform owner. Message-archive and customer-tag
  synchronization write through `JobRunLedgerPort` using their existing DBAPI
  and SQLAlchemy transactions; the port never commits on their behalf.
- `queue_rate_scope_cooldown` now has one platform owner. Runtime administration
  and provider-429 settlement use `RateScopeCooldownPort` with their existing
  DBAPI and SQLAlchemy transactions, while the monotonic deadline rule remains
  unchanged.
- Channel assignees, assignment events, and QR assets now declare their actual
  single SQL owner, `channel_entry`. Automation channel services keep their
  compatibility methods but delegate mutations to that owner.
- Canonical channel metadata also has one SQL owner. Admin save, assignment
  settings, and QR updates use `ChannelWritePort`; callers retain their current
  connection and commit boundaries.
- `admin_operation_logs` now has one platform owner. Admin config, PII access,
  job administration, AI Audience, cloud orchestration, and owner migration
  append through `AdminAuditPort`; redaction and every caller's existing commit
  boundary remain unchanged.
- `webhook_inbox` now has one platform owner. Callback ingestion, worker
  settlement, queue claim/recovery/heartbeat, and operator CAS commands all use
  the webhook-inbox public ports while preserving the execution runtime's
  policy locks, fairness cursor, wakeup, and command-audit transaction.
- `internal_event_consumer_run` now has one platform owner. Consumer creation,
  settlement, runtime claim/recovery/heartbeat, audited operator CAS, and stale
  signal quarantine all cross the internal-events public port; caller-owned
  policy locks, fairness, wakeup, and transaction boundaries remain unchanged.
- `internal_event_outbox` now has one platform owner. Business modules append
  through transaction-preserving owner functions; runtime claim/recovery,
  heartbeat, operator wake CAS, stale-signal quarantine, relay settlement, and
  reconciliation no longer write the table across capability boundaries.
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
- The catalog scheduler now has a production observer unit. It evaluates the
  versioned minute schedules and runs only redacted dry-run handlers while the
  three predecessor timers remain authoritative. Four commands are statically
  classified as provider-free (`external_effect.reconcile`, `campaign.plan`,
  `group_ops.plan`, and `ai_audience.refresh`); payment reconciliation remains
  `observe_only` until it delegates provider work through External Effect. The
  execute path additionally requires an environment gate and an exact fixed
  confirmation, and fails if any handler reports a real external call.
- Architecture gates forbid premature physical moves and legacy table drops
  without 30 days of zero-read/write evidence, verified export, rollback
  rehearsal, successor ownership, and approval.

## Compatibility mode

`deploy/deployment_profiles/wecom-core.json` remains in observation mode. The
separate `deploy/deployment_profiles/production-current.json` profile stages
all currently deployed core and extension capabilities in observation mode; it
is not selected by production in this release. This establishes a rollback-safe
file boundary before the later release that changes the staged profile to
`enforce` and selects it through the single startup reference. The
job-catalog observer timer is active, but its execute gate is closed and all
existing authoritative task timers remain unchanged. HTTP paths, auth rules,
event names, effect types, database behavior, and extension behavior therefore
remain compatible while scheduler parity evidence is collected.

An `enforce` profile is a separate reviewed release decision. It must not be
enabled until the target instance has route, event, effect, job, configuration,
and historical-data parity evidence.

## Deliberately not completed in this release

- Managed values remain in observation mode. The per-instance staging,
  redacted shadow comparison, and cutover workflow now exists, but production
  evidence must come from executing it after the observation deployment. No
  `AICRM_RUNTIME_CONFIG_CUTOVER_KEYS` value is activated by this change.
- The cross-context import baseline is reduced from 162 to 116 by injecting the
  composed FastAPI route registry, keeping the runtime-config projection in
  `admin_config`, publishing the management shell as a static app contract, and
  routing all business-provider dependencies through the versioned
  `integration_ports` surface. The provider surface reaches 114 edges; two
  explicit owner-port dependencies for cross-domain transactional tables leave
  the current graph at 116. The target of 120 remains met without introducing a
  broker or runtime plugin system. Physical directory moves remain disabled
  until public ports and unique table-write ownership are proven.
- Existing task timers remain authoritative. The scheduler observer is active
  and redacted, while `--execute` stays fail-closed without both its environment
  gate and exact confirmation. Successor parity is required before any unit
  retirement.
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
