# Full Repo Detail Optimization Report

- Baseline branch source: `origin/main`
- Baseline commit: `d31bafb27d20db434fc9dc98056f12d2fcd269ff`
- Work branch: `cleanup/full-repo-detail-optimization`
- Scope: full-repo detail scan and no-new-feature cleanup only.

## Tracked File Inventory

The baseline inventory was generated with `git ls-files` and saved to
`.codex_artifacts/full_repo_file_inventory.txt`.

- Baseline tracked files: 1689
- Final branch delta: removes 2 orphan root-level generated JS bundles and adds
  this cleanup report, so the branch would have 1688 tracked files after merge.

Top-level counts from the baseline inventory:

| Root | Count |
| --- | ---: |
| `.codex` | 5 |
| `.github` | 7 |
| `.gitignore` | 1 |
| `AGENTS.md` | 1 |
| `CLAUDE.md` | 1 |
| `MP_verify_QqaW4cYDK8GxBbuG.txt` | 1 |
| `MP_verify_TksoQfSDwRIhbdcb.txt` | 1 |
| `Makefile` | 1 |
| `README.md` | 1 |
| `aicrm_next` | 635 |
| `alembic.ini` | 1 |
| `app.py` | 1 |
| `deploy` | 32 |
| `docs` | 302 |
| `experiments` | 1 |
| `frontend` | 37 |
| `local-environments-settings-page-W-Oe_iWM.js` | 1 |
| `migrations` | 98 |
| `package-lock.json` | 1 |
| `package.json` | 1 |
| `pyproject.toml` | 1 |
| `requirements-dev.txt` | 1 |
| `requirements.txt` | 1 |
| `scripts` | 55 |
| `skills` | 10 |
| `test_wecom_sdk_decrypt.py` | 1 |
| `test_wecom_sdk_minimal.py` | 1 |
| `tests` | 457 |
| `tools` | 31 |
| `tsconfig.frontend.json` | 1 |
| `worktree-C0NyLtpP.js` | 1 |

## Scan Method

- Read the required task preflight files before implementation:
  `README.md`, `AGENTS.md`, `CLAUDE.md`,
  `docs/development/ai_crm_next_architecture_skill.md`,
  `docs/development/codex_task_template.md`,
  `docs/skills/frontend-development-skill.md`,
  `docs/development/module_boundaries.yml`, `docs/deploy_runbook.md`,
  `docs/cleanup/repo_hygiene_report.md`, `tools/audit_repo_hygiene.py`,
  `tools/check_architecture_boundaries.py`, `scripts/run_lint.py`,
  `package.json`, and `tsconfig.frontend.json`.
- Used a clean sibling worktree from latest `origin/main` because the original
  checkout had substantial unrelated local changes.
- Classified every baseline tracked file from the `git ls-files` inventory by
  role: runtime, frontend, generated frontend, tests, docs, archive, migrations,
  scripts/tools, ops config, agent entry, and root config/other.
- Ran machine scans for hygiene, architecture boundaries, Python lint/typecheck,
  frontend build/typecheck/tests, and root pytest.
- Ran keyword sweeps across tracked text files for retired legacy markers,
  fallback/compat/shim wording, debug markers, local fixture/demo wording,
  production ops detail patterns, generated artifact candidates, and real
  external-call claims.
- Reviewed the noisy keyword hits by category and only changed issues that were
  behavior-equivalent, report-only, doc-only, or generated-artifact cleanup.

## Machine Scan Summary

Baseline:

- `python3 tools/audit_repo_hygiene.py ...`: reported 1 generated artifact
  candidate, `.codex_artifacts/full_repo_file_inventory.txt`. Triage showed this
  was a false positive caused by the audit fallback scanning ignored untracked
  files inside a git worktree.
- `python3 tools/check_architecture_boundaries.py`: initial system Python run
  failed because `yaml` was not installed. After the project venv was created,
  the checker passed.
- `python3 scripts/run_lint.py`: failed with 8 small lint findings: one unused
  local variable, six stale direct reset imports in `aicrm_next/main.py`, and
  one unused import in a smoke tool.
- `python3 scripts/run_typecheck.py`: failed before dev tooling was installed
  because `mypy` was missing; after `requirements-dev.txt`, it found the
  `scripts/script_runtime.py` duplicate module-name issue.
- `npm install`: passed with the existing lockfile.
- `npm run build:frontend`: passed.
- `npm run typecheck`: passed.
- `npm run test:frontend`: passed.
- `scripts/run_tests.sh`: 2397 passed, 88 skipped, 28 failed. The failures are
  existing broad route-owner/contract drift and payment/customer projection
  tests on `origin/main`, not introduced by this cleanup.

After cleanup:

- Hygiene audit reports 0 issues.
- Architecture boundary checker passes.
- Python lint passes.
- Python mypy wrapper passes.
- Focused hygiene audit tests pass.
- Full pytest remains at the same 28 baseline failures; pass count increases to
  2398 because this branch adds one hygiene regression test.

## Manual Coverage

All 1689 baseline tracked paths were covered through inventory classification.
The manual review was batched by directory and risk:

- Root and agent entry docs: read directly and checked for canonical preflight,
  retired runtime wording, and production ops details.
- `aicrm_next/`: scanned for lint findings, retired legacy markers, direct
  cross-context imports, debug markers, and fixture/local contract wording.
- `frontend/admin/` and `aicrm_next/frontend_compat/`: built and typechecked;
  no frontend source edits were needed.
- Tests: checked for existing audit coverage and added one focused regression
  for the artifact scanner.
- Tools/scripts: reviewed lint/typecheck wrappers and hygiene scanner behavior.
- Docs: reviewed active docs versus archive docs; only active deploy runbook
  wording was changed.
- Migrations and deploy configs: scanned and intentionally not modified except
  for active documentation wording; no schema migration or deploy config change.
- Archive: scanned as historical/archive-only material; no archive content was
  deleted or reconnected to live runtime.
- Generated/static outputs: identified two root-level orphan hashed JS bundles
  and removed them; preserved committed frontend build outputs generated from
  `frontend/admin`.

## Fixes

### Runtime

- Removed an unused normalized `corp_id` local in
  `aicrm_next/channels/channel_entry/identity_bridge_repo.py`.
- Made the fixture-reset names exported from `aicrm_next/main.py` via `__all__`
  so the production-hardening checker contract remains explicit while lint no
  longer treats them as accidental unused imports. Runtime reset orchestration
  remains owned by `aicrm_next/fixture_reset_registry.py`.

### Frontend

- No `frontend/admin` or `aicrm_next/frontend_compat` source was changed.
- Frontend generated output was rebuilt during verification and remained in sync.

### Tests

- Added a focused regression in `tests/test_repo_hygiene_audit.py` proving that
  ignored, untracked `.codex_artifacts` files inside a git worktree are not
  reported as tracked artifacts.

### Docs

- Rewrote `docs/deploy_runbook.md` to keep AI-CRM Next deployment and approval
  semantics while removing public production host/path/env/log/systemd/SQL
  cookbook details.
- Replaced a machine-specific README worktree path example with neutral
  placeholders.

### Scripts

- Updated `scripts/run_typecheck.py` to use mypy `--explicit-package-bases`,
  avoiding duplicate module names for `scripts/script_runtime.py`.
- Added narrow `no-redef` ignores to the direct-execution fallback imports used
  by `scripts/run_lint.py` and `scripts/run_typecheck.py`.

### Tools

- Fixed `tools/audit_repo_hygiene.py` so artifact-directory fallback scanning is
  only used outside git worktrees. In a git worktree, artifact findings now come
  from `git ls-files`, which matches the intended tracked-file policy.
- Removed an unused import from `tools/smoke_questionnaire_real_wecom_tag.py`.

### Archive

- No archive files changed. Archive references remain historical and are not
  treated as live runtime.

### Generated

- Removed two orphan root-level hashed JS bundles:
  `local-environments-settings-page-W-Oe_iWM.js` and `worktree-C0NyLtpP.js`.
  They referenced missing bundled chunks and were not referenced by repo docs,
  tests, runtime, or frontend source.
- Added ignore rules for `node_modules/` and the same root-level bundle naming
  pattern to prevent recurrence.

## High-Risk Items Not Changed

- `app.py`: scanned only; no runtime entry behavior changed.
- `deploy/`, `.github/workflows/deploy.yml`, nginx/systemd templates: scanned
  only; no deploy config or production workflow behavior changed.
- `migrations/`: scanned only; no schema migration was added or changed.
- Route registration and route ownership: not changed. Existing full-suite
  route-owner failures require a separate approved remediation.
- Payment, OAuth, OpenClaw, MCP, Webhook, callback workers, external-effect
  execution gates: not enabled or changed.
- WeCom External Effect boundary remains limited to the previously approved
  scope; this cleanup did not add any real external call path.

## Follow-Up Items Requiring Approval

- Root pytest still has 28 baseline failures on latest `origin/main`. They are
  broader contract/runtime drift and should be handled in a separate remediation
  PR, not folded into this hygiene cleanup.
- `.github/workflows/deploy.yml` still contains real deployment command details.
  It was intentionally not modified because that is live deploy configuration,
  not a safe documentation-only cleanup.
- Existing route inventory and active documentation still contain many
  historical references by design. They should only be changed when a specific
  ownership/inventory cleanup approves the exact slice.

## Verification

Commands run so far:

- `git fetch origin main`: passed.
- `git pull --ff-only origin main` in the clean worktree: already up to date.
- `git status --short` in the clean worktree before edits: clean.
- `git ls-files > .codex_artifacts/full_repo_file_inventory.txt`: passed.
- `python3 tools/audit_repo_hygiene.py --json-output .codex_artifacts/repo_hygiene_full_scan.json --summary-output .codex_artifacts/repo_hygiene_full_scan.md`: baseline produced the artifact false positive described above.
- `.venv/bin/python tools/audit_repo_hygiene.py --json-output .codex_artifacts/repo_hygiene_after_patch.json --summary-output .codex_artifacts/repo_hygiene_after_patch.md`: passed, 0 issues.
- `.venv/bin/python tools/check_architecture_boundaries.py`: passed.
- `.venv/bin/python scripts/run_lint.py`: passed.
- `.venv/bin/python scripts/run_typecheck.py`: passed.
- `npm install`: passed.
- `npm run build:frontend`: passed.
- `npm run typecheck`: passed.
- `npm run test:frontend`: passed.
- `.venv/bin/python -m pytest tests/test_repo_hygiene_audit.py -q`: passed,
  16 tests.
- `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_repository_provider_hardening.py -q`: passed, 6 tests.
- `scripts/run_tests.sh`: final run failed with the same 28 existing failures;
  final count was 2398 passed, 88 skipped, 28 failed.
- `git diff --check`: passed.
- `git diff --stat`: reviewed; diff is net-negative and includes the cleanup
  report plus generated bundle deletions.
- `git diff --name-only`: reviewed; changed files are limited to docs,
  ignore rules, hygiene tooling/scripts, one runtime lint/export contract, one
  focused test file, and generated bundle deletion.

## Frontend Skill Checklist

Frontend Skill Checklist: 不适用
