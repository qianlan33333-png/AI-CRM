# P1 Group Ops Workspace Production Validation Remediation - 2026-06-25

## Summary

This report documents the remediation for the post-#1416 production validation
blockers. The scope is observability and release metadata only.

Result of this PR: `PRODUCTION_VALIDATION_REMEDIATION_READY`.

Still true:

- `P1_GROUP_OPS_WORKSPACE_READY_FOR_INTERNAL_GRAY` remains subject to production re-validation.
- `EXECUTION_NOT_IN_SCOPE`
- `EXTERNAL_EFFECT_EXECUTION_NOT_IN_SCOPE`
- `PASS_90_PLUS_NOT_CLAIMED`

## Why Production Validation Remediation Is Not Execution

This PR does not add product behavior. It does not save a new draft, request
governance, approve governance steps, bridge a production review, create a
Push Center execution, or send anything.

No external effect execution is introduced. No WeCom send, webhook send, message
send, broadcast job creation, internal_event execution, or external_effect_job
creation is introduced.

## Release Sha Mismatch Root Cause

Production validation after final closeout found that production git `HEAD`
matched the #1416 merge commit, but `/health` and page response header
`x-aicrm-release-sha` still reported a stale value.

Root cause:

- FastAPI middleware read `AICRM_NEXT_RELEASE_SHA` / `RELEASE_SHA` directly.
- The production deploy workflow reset the git worktree but did not refresh a
  release marker after `after_sha="$(git rev-parse HEAD)"`.
- A stale environment value could therefore outlive a successful deploy.

## Release Sha Fix

The release sha source is now centralized through `aicrm_next.platform.shared.release`.
Resolution order:

1. `.release-sha` deploy marker
2. current git `HEAD`
3. `AICRM_NEXT_RELEASE_SHA` / `RELEASE_SHA` / `GIT_SHA`
4. `unknown`

The deploy workflow writes `.release-sha` from `after_sha` before service
restart, then checks `/health` response headers after restart:

- `x-aicrm-release-sha` must equal `$after_sha`
- empty release sha remains fail-safe as `unknown`, not a stale env value
- admin read model and runtime route map now use the same release source

This avoids hard-coding #1416 or any specific commit.

## Private Ops Diagnostic Boundary

Production connection details and command cookbooks are not published in the
main repo. Any production diagnostic wrapper must live in the private ops
handoff for the current environment and must not expose host aliases, SSH
dispatchers, arbitrary SQL bridges, shell passthrough, tokens, secrets, raw
receiver data, raw external user ids, phone numbers, target lists, message
bodies, or callback bodies.

Expected diagnostic output includes:

- `dry_run_read_only=true`
- `SKIPPED_WRITE_VALIDATION_SAFE_MODE`
- route registration / route ownership
- auth fail-closed
- static asset status
- no-execution flags
- `real_external_call_executed=false`
- `can_claim_pass_90_plus=false`

## External Effect Aggregate Evidence / Safe Fallback

`scripts/diagnose_p1_group_ops_workspace_bridge_acceptance.py` now emits a
read-only aggregate evidence section for the recent bridge validation window.

It may report counts for:

- `external_effect_job`
- `broadcast_jobs`
- `internal_event`

The output is aggregate-only: count/status/window. It does not print raw
payloads, receiver lists, message bodies, or identifiers that could directly
enable sending.

If production credentials cannot read `external_effect_job`, the diagnostic must
say:

```text
EXTERNAL_EFFECT_JOB_READ_SKIPPED_PERMISSION_LIMITED
```

That status is not a success claim. It means production validation still needs
operator-level aggregate evidence or a permission-safe read path before upgrading
the conclusion.

## Security / Sensitive-data Boundary

The remediation keeps the existing sensitive-data boundary.

Forbidden in reports, diagnostics, and metadata:

- raw receiver
- raw `external_userid`
- phone / mobile
- raw chat/member id
- openid / unionid
- token / secret / `Authorization`
- raw target list
- raw message body
- raw callback body
- original target list that can directly enable sending

Allowed:

- sanitized summary
- internal reference id
- hash
- count
- guardrail summary
- approval summary
- gray-window summary
- aggregate status

## Tests

Added/updated contract coverage:

- release sha prefers `.release-sha` over stale env
- release sha falls back to current git `HEAD` before stale env
- `/health` header uses the centralized release helper
- deploy workflow writes `.release-sha` and verifies the health release header
- `scripts/prod.sh` is a safe stub and does not publish production connection details
- bridge diagnostic reports dry-run / no-execution flags
- permission-limited `external_effect_job` aggregate reads return
  `EXTERNAL_EFFECT_JOB_READ_SKIPPED_PERMISSION_LIMITED`
- production validation remediation report contains no PASS_90_PLUS claim

## Verification

Required verification:

```bash
npm run build:frontend
npm run typecheck
npm run test:frontend
.venv/bin/python -m pytest tests/test_group_ops_frontend_contract.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_frontend_contract.py -q
.venv/bin/python -m pytest tests/test_group_ops_workspace_draft_migration.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_draft_api.py -q
.venv/bin/python -m pytest tests/test_group_ops_workspace_governance_migration.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_governance_api.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_bridge_hardening.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_final_closeout.py -q
.venv/bin/python -m pytest tests/test_p1_group_ops_workspace_production_validation_remediation.py -q
.venv/bin/python scripts/diagnose_p1_group_ops_workspace_bridge_acceptance.py
.venv/bin/python scripts/diagnose_business_closure_acceptance.py --scenario all
bash scripts/ci/run_architecture_gates.sh
git diff --check
```

After deploy, run the production diagnostic from the private ops handoff only.

## Production Re-validation Steps

After merge/deploy:

1. Confirm production git `HEAD`.
2. Confirm #1416 is contained in production `HEAD`.
3. Confirm `/health` returns `x-aicrm-release-sha` equal to production `HEAD`.
4. Confirm `/admin/p1/group-ops-workspace` is reachable under normal admin auth.
5. Confirm legacy Group Ops route remains unaffected.
6. Confirm draft/governance/bridge APIs fail closed without cookies.
7. Run the private ops diagnostic for the P1 bridge.
8. Run business closure acceptance and confirm `can_claim_pass_90_plus=false`.
9. Collect aggregate no-execution evidence or record explicit permission-limited
   safe fallback.

Target upgrade after successful re-validation: C -> A. This still does not
authorize real sending, external effect execution, or PASS_90_PLUS.

## Risk / Rollback

Rollback is low-risk:

- Reverting the release helper returns headers to env-only behavior.
- Reverting the deploy workflow change stops writing `.release-sha`.
- Reverting the prod wrapper subcommand removes the local diagnostic shortcut.
- Reverting diagnostic aggregate output removes the added observability only.

No external effect rollback is needed because this PR performs no outbound
message, no Push Center execution, no broadcast job creation, and no
external_effect_job creation.

## Next Action

Re-run production validation after this PR is merged and deployed.

Expected pass conditions:

- release header matches production git `HEAD`
- private ops diagnostic runs without exposing command details in this repo
- no-execution flags remain false
- business closure still reports `can_claim_pass_90_plus=false`
- no new execution jobs are observed or any permission-limited gaps are explicitly
  documented

External effect execution remains out of scope. PASS_90_PLUS remains out of
scope.

## Frontend Skill Checklist

- 已读取 `frontend-development-skill.md`
- 参考的已有页面: P1 Group Ops Workspace 和 legacy Group Ops 仅作为边界参考
- 参考的已有组件: 未改前端组件
- 复用的 hooks / services / types: 未新增前端集成
- 是否新增组件: 否
- 新增组件原因: 不适用
- 一级 / 二级页面职责划分: 不变
- 是否存在重复标题和说明: 否
- 是否存在重复造轮子风险: 否
- 自检结论: 本 PR 为生产验收 remediation / observability，不改变 P1 workspace 或 legacy Group Ops 页面行为
