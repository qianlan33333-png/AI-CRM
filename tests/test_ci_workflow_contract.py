from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_FAST_WORKFLOW = ROOT / ".github" / "workflows" / "ci-fast.yml"
FULL_REGRESSION_WORKFLOW = ROOT / ".github" / "workflows" / "full-regression.yml"
DURATION_BASELINE_REFRESH_WORKFLOW = ROOT / ".github" / "workflows" / "refresh-pytest-duration-baseline.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
PROMOTE_PRODUCTION_WORKFLOW = ROOT / ".github" / "workflows" / "promote-production.yml"
SIYUAN_PROMOTE_PRODUCTION_WORKFLOW = ROOT / ".github" / "workflows" / "siyuan-promote-production.yml"
SIYUAN_PRODUCTION_SCHEMA_DIAGNOSTICS_WORKFLOW = (
    ROOT / ".github" / "workflows" / "siyuan-production-schema-diagnostics.yml"
)
QUEUE_PRODUCTION_CUTOVER_WORKFLOW = ROOT / ".github" / "workflows" / "queue-production-cutover.yml"
LEGACY_CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ci_fast_uses_selector_and_single_required_result() -> None:
    source = _source(CI_FAST_WORKFLOW)

    assert "pull_request:" in source
    assert "push:" in source
    assert "scripts/ci/select_test_scope.py --github-output" in source
    assert "scripts/ci/select_test_scope_v2.py" in source
    assert "Compare convention selector in shadow mode" in source
    assert "continue-on-error: true" in source
    assert "test-scope-v2-shadow.json" in source
    assert "python -m pytest tests/ -n auto" not in source
    assert "ci-fast-result:" in source
    assert "toJson(needs)" not in source
    assert "SELECT_RESULT: ${{ needs.select.result }}" in source
    assert "ARCHITECTURE_GATES_RESULT: ${{ needs.architecture-gates.result }}" in source
    assert "FAST_PYTHON_NO_PG_RESULT: ${{ needs.fast-python-no-pg.result }}" in source
    assert "FAST_PYTHON_PG_RESULT: ${{ needs.fast-python-pg.result }}" in source
    assert "FAST_FRONTEND_RESULT: ${{ needs.fast-frontend.result }}" in source
    assert "DEPENDENCY_AUDIT_RESULT: ${{ needs.dependency-audit.result }}" in source
    assert "FULL_REGRESSION_RESULT: ${{ needs.full-regression.result }}" in source
    assert "if result not in {\"success\", \"skipped\"}" in source
    assert "needs.select.outputs.python_tests != ''" in source
    assert "needs.select.outputs.needs_postgres == 'true'" in source
    assert "needs.select.outputs.needs_postgres != 'true'" in source
    assert "needs.select.outputs.frontend_tests != ''" in source
    assert "needs_frontend_build" not in source
    assert "needs_dependency_audit: ${{ steps.scope.outputs.needs_dependency_audit }}" in source
    assert "bash scripts/ci/run_architecture_gates.sh --mode" in source
    assert "dependency-audit:" in source
    assert "github.event_name != 'pull_request' || needs.select.outputs.needs_dependency_audit == 'true'" in source
    assert "python -m pip_audit -r requirements.lock --require-hashes --progress-spinner=off" in source
    assert "npm audit --audit-level=high" in source
    assert source.count("actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9") == 4
    assert source.count("key: venv-v1-${{ runner.os }}-${{ runner.arch }}-py3.10.5-${{ hashFiles('requirements.lock') }}") == 4
    assert source.count("if: steps.python-venv-cache.outputs.cache-hit != 'true'") == 4
    assert source.count("python -m venv .venv") == 4
    assert source.count(".venv/bin/python -m pip install --require-hashes -r requirements.lock") == 4
    assert source.count('echo "$GITHUB_WORKSPACE/.venv/bin" >> "$GITHUB_PATH"') == 4
    assert "restore-keys:" not in source
    assert "cache: pip" not in source
    assert "cache-dependency-path: requirements.lock" not in source
    assert "python -m pytest ${PYTHON_TESTS} -n auto --dist=loadfile -q --tb=short" in source
    assert "python -m pytest ${PYTHON_TESTS} -n auto --dist=loadfile -q --tb=short --timeout=120" in source
    assert "timeout-minutes: 8" in source
    assert "force_full != 'true'" not in source
    assert "full-regression:" in source
    assert "uses: ./.github/workflows/full-regression.yml" in source
    assert "needs.select.outputs.needs_full_ci == 'true'" in source
    assert source.count("needs.select.outputs.needs_full_ci != 'true'") == 2
    assert "- full-regression" in source
    assert "- dependency-audit" in source
    assert "full-regression={results['full-regression']}_but_required" in source
    assert not LEGACY_CI_WORKFLOW.exists()


def test_full_regression_owns_full_pytest_and_full_frontend() -> None:
    source = _source(FULL_REGRESSION_WORKFLOW)

    assert "name: Full Regression" in source
    assert "workflow_dispatch:" in source
    assert "workflow_call:" in source
    assert 'cron: "0 18 * * *"' in source
    assert "full-python-shard:" in source
    assert "fail-fast: false" in source
    assert "max-parallel: 8" in source
    assert source.count("shard_index:") == 8
    for shard_index in range(8):
        assert f"shard_index: {shard_index}" in source
        assert f"shard_label: {shard_index + 1}-of-8" in source
    assert "python scripts/ci/select_pytest_shard.py" in source
    assert "--shard-total 8" in source
    assert "--duration-baseline docs/ci/pytest_duration_baseline.json" in source
    assert "set -o pipefail" in source
    assert "pytest_files=()" in source
    assert "while IFS= read -r test_file; do" in source
    assert 'pytest_files+=("$test_file")' in source
    assert 'done < "$RUNNER_TEMP/pytest-shard-files.txt"' in source
    assert "mapfile" not in source
    assert 'python -m pytest "${pytest_files[@]}" -n auto --dist=loadfile -q' in source
    assert "--junitxml=" in source
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in source
    assert "timeout-minutes: 25" in source
    assert "bash scripts/ci/run_architecture_gates.sh --mode full" in source
    assert "python scripts/ci/check_dependency_security.py" in source
    assert "python -m pip_audit -r requirements.lock --require-hashes --progress-spinner=off" in source
    assert source.count("actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9") == 3
    assert source.count("key: venv-v1-${{ runner.os }}-${{ runner.arch }}-py3.10.5-${{ hashFiles('requirements.lock') }}") == 3
    assert source.count("if: steps.python-venv-cache.outputs.cache-hit != 'true'") == 3
    assert source.count("python -m venv .venv") == 3
    assert source.count(".venv/bin/python -m pip install --require-hashes -r requirements.lock") == 3
    assert source.count('echo "$GITHUB_WORKSPACE/.venv/bin" >> "$GITHUB_PATH"') == 3
    assert "restore-keys:" not in source
    assert "cache: pip" not in source
    assert "cache-dependency-path: requirements.lock" not in source
    assert "performance-regression:" in source
    assert "python scripts/ops/bootstrap_database.py" in source
    assert "python tools/check_critical_read_performance.py" in source
    assert "critical-read-performance.json" in source
    assert "npm audit --audit-level=high" in source
    assert "npm run typecheck" not in source
    assert "npm run build:frontend" not in source
    assert "npm run test:frontend:all" in source


def test_duration_baseline_refresh_is_isolated_to_trusted_successful_main_runs() -> None:
    source = _source(DURATION_BASELINE_REFRESH_WORKFLOW)
    trigger = source[source.index("on:") : source.index("permissions:")]

    assert "name: Refresh Pytest Duration Baseline" in source
    assert "workflow_run:" in trigger
    assert 'workflows: ["Full Regression"]' in trigger
    assert "types: [completed]" in trigger
    assert "pull_request:" not in trigger
    assert "push:" not in trigger
    assert "actions: read" in source
    assert "contents: write" in source
    assert "pull-requests: write" in source
    assert "github.event.workflow_run.conclusion == 'success'" in source
    assert "github.event.workflow_run.head_repository.full_name == 'qianlan333/AI-CRM'" in source
    assert "github.event.workflow_run.head_branch == 'main'" in source
    assert "github.event.workflow_run.event == 'schedule'" in source
    assert "github.event.workflow_run.event == 'workflow_dispatch'" in source
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in source
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in source
    assert "run-id: ${{ github.event.workflow_run.id }}" in source
    assert "python scripts/ci/build_pytest_duration_baseline.py" in source
    assert 'branch="automation/pytest-duration-baseline"' in source
    assert "gh pr create" in source
    assert "--draft" in source


def test_full_regression_runs_governance_once_and_ci_fast_does_not_duplicate_it() -> None:
    full_source = _source(FULL_REGRESSION_WORKFLOW)
    ci_fast_source = _source(CI_FAST_WORKFLOW)

    assert full_source.count("run_governance:") == 2
    assert "type: boolean" in full_source
    assert "default: true" in full_source
    assert "full-governance:" in full_source
    assert "github.event_name == 'schedule' || inputs.run_governance == true" in full_source
    assert "github.event_name != 'workflow_call'" not in full_source
    assert full_source.count("python scripts/ci/check_dependency_security.py") == 1
    assert full_source.count("bash scripts/ci/run_architecture_gates.sh --mode full") == 1
    assert "uses: ./.github/workflows/full-regression.yml\n    with:\n      run_governance: false" in ci_fast_source


def test_ai_crm_deploy_is_reusable_production_only_and_has_no_id_validation_access() -> None:
    source = _source(DEPLOY_WORKFLOW)
    trigger = source[source.index("on:") : source.index("permissions:")]

    assert "name: Deploy Production Release" in source
    assert "workflow_call:" in trigger
    assert "workflow_run:" not in trigger
    assert "push:" not in trigger
    assert "schedule:" not in trigger
    assert "TEST_DEPLOY" not in source
    assert "id-dev.youcangogogo.com" not in source
    assert "environment: production" in source
    assert "DEPLOY_TARGET: production" in source
    assert "host: ${{ secrets.DEPLOY_HOST }}" in source
    assert "username: ${{ secrets.DEPLOY_USER }}" in source
    assert "key: ${{ secrets.DEPLOY_SSH_KEY }}" in source
    assert "ref: ${{ inputs.release_sha }}" in source
    assert "AI-CRM deploy workflow is production-only" in source
    assert "AI-CRM remote deploy target must be production" in source
    assert "PUBLIC_HEALTH_URL: ${{ vars.PUBLIC_HEALTH_URL }}" in source
    assert "set -o pipefail" in source
    session_issue_index = source.index("python3 scripts/ops/create_deploy_smoke_session.py issue")
    admin_smoke_index = source.index("python scripts/ops/check_admin_read_pages_smoke.py", session_issue_index)
    session_revoke_index = source.index(
        "python3 scripts/ops/create_deploy_smoke_session.py revoke",
        admin_smoke_index,
    )
    assert session_issue_index < admin_smoke_index < session_revoke_index
    assert '--admin-cookie-file "$deploy_smoke_session_file"' in source
    assert 'admin_smoke_sidebar_args=(--include-all-sidebar --require-all-data-health-green)' in source
    assert "tee /tmp/aicrm-admin-read-pages-smoke.json" in source


def test_production_promotion_is_manual_cross_repo_verified_and_environment_approved() -> None:
    source = _source(PROMOTE_PRODUCTION_WORKFLOW)
    deploy_source = _source(DEPLOY_WORKFLOW)

    assert "name: Promote to Production (Manual)" in source
    assert "workflow_dispatch:" in source
    assert "release_sha:" in source
    assert "validated_id_sha:" in source
    assert "confirmation:" in source
    assert "workflow_run:" not in source
    assert "push:" not in source
    assert "schedule:" not in source
    assert "target_environment:" not in source
    assert "uses: ./.github/workflows/deploy.yml" in source
    assert "needs: validate" in source
    assert "secrets: inherit" in source
    assert "environment: production" in deploy_source
    assert "DEPLOY_TARGET: production" in deploy_source
    assert "DEPLOY 150.158.82.186" in source
    assert "https://id-dev.youcangogogo.com/health" in source
    assert "qianlan333/AI-CRM-ID-refactor.git" in source
    assert "docs/releases/production_promotion.json" in source
    assert "scripts/ops/validate_production_promotion.py" in source
    assert "aicrm_source_ci_run.json" in source
    assert "aicrm_source_deploy_run.json" in source
    assert "aicrm_target_ci_runs.json" in source
    assert "id-dev health must expose exactly one full release SHA" in source
    assert 'if [ "$(git rev-parse FETCH_HEAD)" != "$requested_sha" ]; then' in source
    assert "secrets.DEPLOY_HOST" in deploy_source
    assert "inputs.release_sha != ''" in deploy_source
    assert "TEST_DEPLOY" not in deploy_source
    assert "workflow_run:" not in deploy_source
    assert "scripts/ops/ensure_production_public_release_route.py --execute" in deploy_source
    assert '--public-health-url "${{ env.PUBLIC_HEALTH_URL }}"' in deploy_source


def test_siyuan_production_promotion_is_manual_main_ci_verified_and_environment_approved() -> None:
    source = _source(SIYUAN_PROMOTE_PRODUCTION_WORKFLOW)
    deploy_source = _source(DEPLOY_WORKFLOW)

    assert "name: Promote Siyuan to Production (Manual)" in source
    assert "workflow_dispatch:" in source
    assert "release_sha:" in source
    assert "confirmation:" in source
    assert "workflow_run:" not in source
    assert "push:" not in source
    assert "schedule:" not in source
    assert "actions: read" in source
    assert "contents: read" in source
    assert "uses: ./.github/workflows/deploy.yml" in source
    assert "needs: validate" in source
    assert "secrets: inherit" in source
    assert "environment: production" in deploy_source
    assert "DEPLOY_TARGET: production" in deploy_source
    assert "DEPLOY SIYUAN PRODUCTION" in source
    assert 'if [ "$GITHUB_REPOSITORY" != "qianlan333/siyuan-crm" ]; then' in source
    assert "qianlan333/AI-CRM-ID-refactor" not in source
    assert "id-dev.youcangogogo.com" not in source
    assert "validated_id_sha" not in source
    assert "production_promotion.json" not in source
    assert "actions/workflows/ci-fast.yml/runs?head_sha=$requested_sha&event=push&status=completed" in source
    assert "current siyuan-crm main has no successful completed CI Fast push run" in source
    assert "ref: ${{ inputs.release_sha }}" in source
    assert "fetch-depth: 0" in source
    assert 'if [ "$(git rev-parse FETCH_HEAD)" != "$requested_sha" ]; then' in source
    assert "secrets.DEPLOY_HOST" in deploy_source
    assert "inputs.release_sha != ''" in deploy_source


def test_siyuan_production_schema_diagnostics_is_read_only_and_release_pinned() -> None:
    source = _source(SIYUAN_PRODUCTION_SCHEMA_DIAGNOSTICS_WORKFLOW)

    assert "workflow_dispatch:" in source
    assert "DIAGNOSE SIYUAN PRODUCTION SCHEMA READ ONLY" in source
    assert "https://www.xinliushangye.com/health" in source
    assert 'test "$public_sha" = "$expected_release_sha"' in source
    assert 'test "$(git rev-parse HEAD)" = "$expected_release_sha"' in source
    assert 'test "$(tr -d \'\\r\\n\' < .release-sha)" = "$expected_release_sha"' in source
    assert "SELECT DISTINCT table_name" in source
    assert "FROM information_schema.columns" in source
    assert "migration_revisions" in source
    assert "contains_column_values" in source
    assert "INSERT " not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source
    assert "DROP " not in source


def test_architecture_gate_script_has_fast_db_and_full_modes() -> None:
    script = _source(ROOT / "scripts" / "ci" / "run_architecture_gates.sh")

    assert "MODE=\"full\"" in script
    assert "run_fast()" in script
    assert "run_db()" in script
    assert "run_full_only()" in script
    assert "tools/check_route_ownership_manifest.py" in script
    assert "scripts/ci/update_route_policy_manifest.py --check" in script
    assert "tools/check_repository_ownership.py" in script
    assert "tools/check_runtime_configuration_contract.py" in script
    assert "tools/check_admin_route_auth.py" in script
    assert "tools/check_db_access_boundary.py" in script
    assert "tools/check_sql_static_guard.py" in script
    assert "tests/test_alembic_revision_chain.py" in script
    assert "tools/check_background_job_contract.py" in script
    assert "scripts/ci/check_dependency_security.py" in script
    assert "Unknown architecture gate mode" in script


def test_queue_production_cutover_is_manual_exact_release_and_all_scope_only() -> None:
    source = _source(QUEUE_PRODUCTION_CUTOVER_WORKFLOW)
    trigger = source[source.index("on:") : source.index("permissions:")]

    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger
    assert "schedule:" not in trigger
    assert "environment: production" in source
    assert "docs/releases/queue_all_scope_cutover.json" in source
    assert "validate_queue_all_scope_cutover.py" in source
    assert "0139_queue_terminal_acknowledgement_scope" in source
    assert "0137_queue_production_scope_cutover'" not in source
    assert "git merge-base --is-ancestor" in source
    assert "https://www.youcangogogo.com/health" in source
    assert "AICRM_QUEUE_ALL_SCOPE_AUTHORIZED=1" in source
    assert "--target-scope all" in source
    assert "--expected-scope all" in source
    assert "--duration-hours 72" in source
    assert "--owner-inventory pr3" in source
    assert "aicrm-queue-soak-snapshot.timer" in source


def test_frontend_scripts_are_split_for_scoped_ci() -> None:
    package_json = _source(ROOT / "package.json")

    assert "\"test:frontend\": \"npm run test:frontend:all\"" in package_json
    assert "\"test:frontend:security\"" in package_json
    assert "\"test:frontend:coupons\"" in package_json
    assert "\"test:frontend:sidebar\"" in package_json
    assert "\"test:frontend:service-period\"" in package_json
    assert "\"test:frontend:service-period-sharing\"" in package_json
    assert "build:frontend" not in package_json
    assert "typescript" not in package_json
    assert "test:frontend:push-center" not in package_json
