# Runtime Config Release operations

This runbook moves managed business settings from environment compatibility
reads to atomic Config Releases. It never changes startup topology values such
as `DATABASE_URL`, public origins, release SHA, runtime role, or the deployment
profile path.

## Safety contract

- Deploy the exact cumulative `main` SHA in `observe` mode before using this
  workflow. The public and local health endpoints, checkout, and `.release-sha`
  must all expose that same SHA.
- Production secrets must already be immutable `secretref:file:` references.
  The deploy workflow runs `migrate_app_setting_secrets.py`; a raw sensitive
  value blocks staging and is never printed or copied into Config Release.
- `inventory` and previews are read-only. Mutations require the exact
  confirmation emitted by the preview and execute one database transaction.
- Staging does not activate a key. Activation is a later release, is limited to
  at most 25 exact keys, and rejects missing, disabled, or semantically
  different staged values.
- Boolean and integer compatibility values are compared semantically, so values
  such as `1` and `true` do not create false mismatches.
- No action calls WeCom, a payment provider, an AI provider, or another external
  system. No runtime unit is restarted by the workflow.

## GitHub workflow

Use `Runtime Config Production Control` with the production SHA and one of four
actions. Every run records the profile ID, code SHA, Config Release ID, key
names, redacted status, and whether a publish occurred; it never records setting
values or secret references.

### 1. Inventory

- `action=inventory`
- `execute=false`
- `confirmation=INVENTORY_RUNTIME_CONFIG_<release_sha>`

Review `stageable_count`, `active_count`, and blockers. `capability_disabled`
means the setting remains environment-owned until that static capability is
included in the instance's reviewed Deployment Profile.

### 2. Stage

Preview all enabled, configured, inactive keys:

- `action=stage`
- `execute=false`
- `confirmation=PREVIEW_RUNTIME_CONFIG_<release_sha>`

To stage only a slice, pass comma-separated `keys`. After the preview is clean,
repeat with `execute=true` and the emitted
`STAGE_RUNTIME_CONFIG_<release_sha>` confirmation. The operation publishes the
same normalized values into `app_settings`; the environment remains
authoritative because the cutover catalog is unchanged.

### 3. Activate

Pass either explicit `keys` or one `capability_id`. Start with the lowest-risk
core settings, keep `max_keys` at 25 or lower, and preview first. The preview
emits a confirmation bound to both the production SHA and the SHA-256 digest of
the exact sorted key set. Repeat with `execute=true` and that exact confirmation.

After each batch, verify application health and normal callback/worker metrics
before starting the next batch. Provider-enabling booleans are still unchanged
semantically; this operation changes only the authoritative configuration
source.

### 4. Roll back

Rollback applies only to the currently published Config Release and creates a
new audited release. Preview with the target `release_id`, then execute with:

`ROLLBACK_RUNTIME_CONFIG_<release_sha>_<release_id>`

For an activation release, rollback restores the previous cutover catalog in
one transaction. It does not delete staged values or secret versions.

## Per-instance evidence

For every independent customer instance retain:

- code SHA and database migration head;
- Deployment Profile ID, version, activation mode, and enabled capabilities;
- staged and active Config Release IDs;
- redacted inventory, preview, publish, health, and rollback-rehearsal reports;
- the observation window before the next instance or capability batch.

Do not combine Config Release activation with a physical package move, runtime
unit retirement, or legacy table deletion.
