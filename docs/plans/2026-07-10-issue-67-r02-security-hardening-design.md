# R02 Secrets, PII, SSRF and Dependency Security Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove plaintext application secrets, prevent secret/PII disclosure, close outbound-webhook redirect and DNS-rebinding paths, and make dependency installation reproducible without adding product capabilities.

**Architecture:** Keep the modular monolith and existing admin/config routes. Store versioned secret bytes in an atomic host filesystem store owned by the existing `ubuntu` systemd user, while PostgreSQL stores only opaque `secretref:file:<key>:<version>` references. Enforce redaction and PII auditing through neutral shared contracts assembled at the composition root, and route webhook delivery through a resolver-injected, redirect-disabled HTTPS transport that connects only to a validated public IP.

**Tech Stack:** Python 3.10, FastAPI, SQLAlchemy/PostgreSQL, `requests`/`urllib3`, pytest/xdist, GitHub Actions, pip-tools/pip-audit, npm audit.

---

## Architecture preflight

- Capability owners: `admin_config` for secret configuration, `external_push` and `platform_foundation.external_effects` for webhook safety, and neutral `shared` contracts for redaction/secret resolution/PII audit.
- Routes: existing `/admin/config/app-settings`, `/api/admin/config/app-settings`, existing customer/message/questionnaire/radar/order export/read routes, and existing external-effect worker execution. No new product route or page.
- Runtime owner: AI-CRM Next only; legacy and `production_compat` remain retired.
- Real external calls: tests use injected transports only. This work does not enable blocked Webhook, Payment, OAuth, OpenClaw, or MCP execution. When an already-approved webhook gate is enabled, the same call becomes safer.
- Production data: `app_settings` and `admin_operation_logs` need read-only inventory plus a controlled secret-reference migration. Test outputs must never contain values.
- Fixture risk: many tests insert raw `app_settings` values. Fixture bootstrap may use a temporary secret directory, but production cutover checks must reject raw sensitive rows.
- New checkers: secret-reference reconciliation, PII/log scanner, dependency audit policy, and webhook SSRF fault tests.
- Rollback: previous release plus versioned secret references; do not restore plaintext audit payloads or automatic redirect behavior.
- Frontend Skill Checklist: not applicable. Existing markup and interaction are unchanged; only response values become fixed masks/presence metadata.

## Design decision

Three approaches were evaluated:

1. **Versioned filesystem provider (selected).** Fits the single-host, single-tenant deployment; preserves existing admin save behavior; requires no new business table or external infrastructure; supports atomic `0600` writes and immutable version rollback.
2. **Environment-only secrets.** Smallest runtime surface, but it removes the existing admin save capability and forces manual restarts for every rotation.
3. **Envelope-encrypted database values.** Preserves database transactions, but keeps secret material coupled to the database and creates a second key-management problem; it also does not meet the requirement that the database stores references/versions only.

R02 uses an expand/migrate/contract sequence in one work package. The expand release can resolve old plaintext and references. The migration writes immutable files first, changes database rows in one transaction, reconciles every key, and sets a cutover sentinel. Once the sentinel is true, production reads and writes reject plaintext sensitive values. The compatibility branch has an owner and deletion target in R15; it cannot be re-enabled implicitly.

### Task 1: Freeze the sensitive-data and outbound-call inventory

**Files:**
- Create: `docs/architecture/r02_sensitive_data_inventory.yml`
- Create: `tests/test_r02_sensitive_data_inventory.py`
- Modify: `docs/ci/test_scope_manifest.yml`

**Steps:**

1. Write a failing inventory test requiring every `SENSITIVE_KEYS` item to declare runtime consumers, current source, target provider, rotation owner, and rollback decision.
2. Add every PII route with `pii_level != none`, plus export/decrypt/identity-repair classification and required audit purpose.
3. Add every outbound webhook caller and declare its single dispatch transport.
4. Run `pytest -q tests/test_r02_sensitive_data_inventory.py`; expect pass with no unclassified key/route/caller.
5. Commit `docs: 冻结 R02 敏感数据与外呼清单`.

### Task 2: Add defensive redaction and fixed masking

**Files:**
- Create: `aicrm_next/platform/shared/sensitive_data.py`
- Create: `tests/test_sensitive_data_redaction.py`
- Modify: `aicrm_next/platform/admin_config/settings.py`
- Modify: `aicrm_next/platform/admin_config/repository.py`
- Modify: `aicrm_next/platform/external_push/service.py`

**Steps:**

1. Write table-driven failures for nested dict/list/tuple payloads containing secret/token/authorization/cookie/private-key fields and unionid/external_userid/mobile/answer/message content.
2. Implement `redact_sensitive_data`, stable HMAC identifiers for correlation, fixed secret masks, and masked PII helpers. Never retain secret prefixes/suffixes.
3. Apply repository-level defense before `admin_operation_logs` serialization so callers cannot bypass redaction.
4. Replace duplicate external-push redaction with the shared contract.
5. Capture logs/exceptions in tests and assert sentinel values never appear.
6. Run focused tests and Ruff; commit `security: 统一 secret 与 PII 脱敏边界`.

### Task 3: Implement the versioned file secret provider

**Files:**
- Create: `aicrm_next/platform/shared/secret_store.py`
- Create: `tests/test_secret_store.py`
- Modify: `aicrm_next/platform/shared/runtime_settings.py`
- Modify: `aicrm_next/platform/admin_config/application.py`
- Modify: `aicrm_next/platform/admin_config/settings.py`
- Modify: `aicrm_next/message_archive/repo.py`
- Modify: `aicrm_next/questionnaire/repo.py`

**Steps:**

1. Write failures for path traversal, symlink escape, wrong directory/file modes, partial writes, unreadable refs, reference tampering, rotation, version rollback, and constant-time value comparison.
2. Implement `FileSecretStore` under `AICRM_SECRET_STORE_DIR`; create root `0700`, immutable version files `0600`, use `O_NOFOLLOW`/exclusive temp files, `fsync`, atomic rename, and strict reference parsing.
3. Make `runtime_setting` resolve references generically. Keep explicit expand-mode plaintext reads only while the cutover sentinel is false.
4. Make sensitive admin writes store the secret first and persist only its reference. Repeated identical writes must remain idempotent.
5. Update direct `app_settings` secret consumers to use `runtime_setting`.
6. Change admin responses to expose only `configured`, `version`, `updated_at`, and a fixed mask; never expose raw values or reference paths.
7. Run secret round-trip and existing admin-config tests; commit `security: 将敏感配置迁移到版本化 secret store`.

### Task 4: Migrate, reconcile, and contract plaintext rows

**Files:**
- Create: `scripts/ops/migrate_app_setting_secrets.py`
- Create: `scripts/ops/check_secret_reference_cutover.py`
- Create: `tests/test_app_setting_secret_migration.py`
- Create: `docs/runbooks/app_setting_secret_cutover_zh.md`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_deploy_workflow_contract.py`

**Steps:**

1. Write dry-run tests proving reports contain only key/source/version/presence/status and no values.
2. Write failure-injection tests for filesystem failure before DB transaction, DB failure after file write, duplicate execution, mixed raw/ref rows, and rollback to a previous version.
3. Implement migration: inventory sensitive rows, write immutable versions, replace rows in one DB transaction, verify every reference, then set `AICRM_SECRET_REFERENCE_CUTOVER=true` in non-sensitive config.
4. Once cutover is true, reject raw sensitive DB reads and all plaintext sensitive writes. Missing/unreadable refs fail closed without logging a value.
5. Add deploy sequence after the new runtime is available and before final readiness; execute migration/checker without echoing environment or values.
6. Reconciliation targets: `plaintext_sensitive_rows=0`, `unresolved_refs=0`, `unsafe_audit_hits=0`, root/file permission errors `=0`.
7. Commit `ops: 增加 secret reference 迁移与收口门禁`.

### Task 5: Add PII access auditing without response-body capture

**Files:**
- Create: `aicrm_next/platform/shared/pii_audit.py`
- Create: `aicrm_next/platform/admin_config/pii_audit_repository.py`
- Create: `tests/test_pii_audit_contract.py`
- Modify: `aicrm_next/main.py`
- Modify: `aicrm_next/platform/admin_auth/route_policy.py`
- Modify: existing export endpoints in `aicrm_next/questionnaire/api.py`, `aicrm_next/radar_links/api.py`, `aicrm_next/commerce/api.py`, `aicrm_next/class_user_management/api.py`, and `aicrm_next/ops_enrollment/api.py`

**Steps:**

1. Write five-principal tests for sensitive reads/exports, including rejected requests, successful counts, and no raw identifiers in audit payloads.
2. Define a neutral audit protocol and composition-root injection. Do not import `admin_config` from shared or business modules.
3. Record actor, server-declared purpose, policy scope, result count, route name, status, request id, and HMAC resource fingerprint. Never capture response bodies or raw path/query identifiers.
4. Add result-count metadata at existing export/query boundaries where the middleware cannot infer it.
5. Verify audit repository failure does not disclose PII; for high-risk export/decrypt/repair, fail closed if durable audit cannot be written.
6. Commit `security: 为 PII 查询与导出增加用途审计`.

### Task 6: Pin DNS and disable webhook redirects

**Files:**
- Modify: `aicrm_next/platform/external_push/security.py`
- Create: `aicrm_next/platform/external_push/https_transport.py`
- Modify: `aicrm_next/platform/platform_foundation/external_effects/adapters.py`
- Create: `tests/test_external_push_ssrf_transport.py`
- Modify: `tests/test_external_effects_mvp.py`

**Steps:**

1. Write fault tests for 30x to localhost/RFC1918/CGNAT/link-local/IPv6 local, mixed public/private A+AAAA records, credentials/fragments, alternate ports, and resolver result changes.
2. Return an immutable validated target containing normalized HTTPS URL, original hostname, port, and public IP set.
3. Implement an injected HTTPS transport that connects to one validated IP while preserving Host, SNI, and certificate hostname verification. It must set `redirect=False` and never call DNS again inside dispatch.
4. Validate again at dispatch even if configuration validation already ran. Reject any 30x as terminal `redirect_blocked` without following Location.
5. Preserve existing adapter status/retry semantics and injected fake transport tests; do not enable real webhook execution.
6. Commit `security: 封闭 webhook redirect 与 DNS rebinding`.

### Task 7: Add a PII/log source and runtime gate

**Files:**
- Create: `scripts/ci/check_pii_logging.py`
- Create: `tests/test_pii_log_scanner.py`
- Modify: `scripts/ci/run_architecture_gates.sh`
- Modify: `docs/architecture/high_risk_contract_inventory.yml`

**Steps:**

1. Create AST checks for logger/print/exception calls that pass sensitive variables, raw request data, payloads, or formatted PII without an approved redaction helper.
2. Add runtime capture tests for representative admin-config, identity, questionnaire, message, worker, exception, and migration paths using unique sentinel values.
3. Maintain a narrow allowlist with owner/reason/expiry; expired entries fail.
4. Add the checker to full architecture gates and verify zero unapproved hits.
5. Commit `ci: 将 PII 与 secret 日志扫描设为门禁`.

### Task 8: Split internal credentials by purpose

**Files:**
- Modify: `aicrm_next/platform/admin_auth/route_policy.py`
- Modify: relevant worker/checker callers and deployment env inventory
- Create: `tests/test_internal_service_token_purpose.py`
- Modify: `docs/architecture/r02_sensitive_data_inventory.yml`

**Steps:**

1. Write cross-purpose replay failures for MCP, identity, archive, group broadcast, callback, and automation worker credentials.
2. Require the exact token family declared by each RoutePolicy; remove `/mcp` fallback to `AUTOMATION_INTERNAL_API_TOKEN`.
3. Keep legacy token acceptance only behind an explicit, default-off migration flag with owner and deletion date; production cutover checker requires it off.
4. Update server env inventory/checker without printing token values.
5. Commit `security: 按用途拆分内部服务凭证`.

### Task 9: Lock and audit dependencies

**Files:**
- Create: `requirements.lock`
- Create: `docs/security/dependency_risk_acceptance.yml`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Modify: `.github/workflows/ci-fast.yml`
- Modify: `.github/workflows/full-regression.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_ci_workflow_contract.py`

**Steps:**

1. Run current `pip-audit` and `npm audit`; record advisory IDs and affected direct/transitive packages, using official advisory sources.
2. Upgrade the smallest compatible package set and run focused behavior tests after each group.
3. Generate a Python 3.10 lock with hashes for all transitive dependencies. CI/deploy must install with `--require-hashes` from the lock and cache by lock digest.
4. Add high/critical audit gates. Any exception requires advisory, owner, reason, compensating control, and expiry.
5. Run two clean installs and compare lock/artifact digests.
6. Commit `build: 锁定并审计 Python 与 npm 依赖`.

### Task 10: Full verification, PR, migration, and deployment evidence

**Files:**
- Modify: `docs/plans/2026-07-10-issue-67-r02-security-hardening-design.md` with final evidence only if commands or paths changed.
- Update: Issue #72 and PR body.

**Steps:**

1. Run all focused security tests, PII scanner, secret cutover checker in fixture/dry-run mode, pip-audit, npm audit, Ruff, frontend tests/typecheck/build, and all architecture gates.
2. Run full PostgreSQL regression. CI selector must show `needs_full_ci=true`, `needs_postgres=true`, `architecture_gate=full`, `unmatched_files=[]`.
3. Open a Chinese PR with the Epic no-new-function declaration and exact rollback sequence.
4. After merge, verify deployed SHA equals merge SHA, secret migration reconciliation is all zero, no raw value appears in logs/API/audit, SSRF negative smoke is blocked before external call, and all runtime units are healthy.
5. Close Issue #72 only after migration/deploy evidence is attached.

### Local verification evidence

- Rebased the R02-only commits onto `origin/main` at the R01 merge commit before final verification.
- Python 3.10 hashed lock: two clean installs produced 87 packages and the same `pip freeze --all` SHA-256 `21ec855bc57ea79dfd1978c2fd2a4e7aefd936a57a7230cfda0b2526ec0c5064`; `requirements.lock` SHA-256 is `94357b5f349cf7cd716e963e1b3e62a12c6f52cf5aa83ade23512e511baaead4`.
- `pip-audit -r requirements.lock --require-hashes` and `npm audit --audit-level=high` both reported zero known vulnerabilities.
- Dependency/security compatibility set: 242 tests passed. Secret migration, PII logging/audit, SSRF, token-purpose, and secret-store set: 81 tests passed. Final fresh-PostgreSQL regression: 2893 tests passed.
- Full frontend typecheck, build, and all frontend test groups passed. All architecture gates and the R02-relative Ruff check passed. The repository-wide Ruff command still reports the pre-existing baseline findings outside this work package.
- CI selector for all R02 changes reports `needs_full_ci=true`, `needs_postgres=true`, `architecture_gate=full`, and `unmatched_files=[]`.
- No-new-capability comparison against `origin/main`: 708 route identities and 110 page route identities are unchanged, with zero additions/removals; admin navigation and frontend/package files are unchanged.
- Final-regression compatibility cleanup moved existing Commerce response/error helpers into `aicrm_next/commerce/api_contract.py`, kept `aicrm_next/commerce/api.py` below its frozen size budget, and updated the test-only loopback to HTTPS so the SSRF contract remains enforced.

## Acceptance evidence map

| Requirement | Authoritative evidence |
|---|---|
| DB/audit store references only | production reconciliation query + secret round-trip tests |
| API/log/exception contain no raw value | sentinel runtime capture + PII scanner + API contract tests |
| PII access audit | durable `admin_operation_logs` rows containing actor/purpose/scope/count/request id and only fingerprints |
| Redirect and DNS rebinding blocked | injected resolver/transport fault tests and provider-call count zero |
| Purpose-scoped worker tokens | cross-purpose replay matrix and production config checker |
| No high/critical dependency risk | pip/npm audit artifacts plus unexpired acceptance file |
| Reproducible install | hashed lock, two clean-install digest comparison, deploy uses lock |
| No new product capability | route/page/menu inventory diff and PR declaration |
