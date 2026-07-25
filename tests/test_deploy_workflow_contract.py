from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ("wecom_ability" + "_service")
RUNTIME_UNITS_HELPER = "python3 scripts/ops/manage_production_runtime_units.py"
PRODUCTION_DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
PRODUCTION_PROMOTION_WORKFLOW = ROOT / ".github" / "workflows" / "promote-production.yml"
TERMINAL_ACKNOWLEDGEMENT_ORCHESTRATOR = (
    ROOT / "scripts" / "ops" / "acknowledge_release_terminal_histories.py"
)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _runtime_units_phase(phase: str) -> str:
    return f"{RUNTIME_UNITS_HELPER} --phase {phase} --execute"


def test_deploy_workflows_serialize_without_cancelling_active_release() -> None:
    deploy = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    promotion = PRODUCTION_PROMOTION_WORKFLOW.read_text(encoding="utf-8")

    assert "group: aicrm-production-deploy" in deploy
    assert "group: aicrm-production-promotion" in promotion
    assert "cancel-in-progress: false" in deploy
    assert "cancel-in-progress: false" in promotion


def test_deploy_acknowledges_only_authorized_pre_cutover_welcome_before_green_health() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    refresh_index = workflow.index("scripts/ops/run_release_customer_read_model_refresh.py")
    acknowledgement_index = workflow.index(
        "scripts/ops/acknowledge_release_terminal_histories.py",
        refresh_index,
    )
    apply_index = workflow.index("--apply", acknowledgement_index)
    admin_health_index = workflow.index("scripts/ops/check_admin_read_pages_smoke.py", apply_index)

    assert refresh_index < acknowledgement_index < apply_index < admin_health_index
    assert "AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED=1" in workflow
    acknowledgement_block = workflow[acknowledgement_index:admin_health_index]
    assert "send_welcome_msg" not in acknowledgement_block
    assert "requeue" not in acknowledgement_block.lower()


def test_production_deploy_refreshes_release_pinned_data_health_before_admin_smoke() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    web_health_index = workflow.index("cat /tmp/aicrm_health.json")
    snapshot_index = workflow.index(
        "python3 scripts/ops/refresh_data_health_snapshot.py",
        web_health_index,
    )
    expected_sha_index = workflow.index(
        '--expected-release-sha "$after_sha"',
        snapshot_index,
    )
    admin_smoke_index = workflow.index(
        "python scripts/ops/check_admin_read_pages_smoke.py",
        expected_sha_index,
    )
    runtime_start_index = _deploy_runtime_phase_index(workflow, "authorize-runtime-start")

    assert web_health_index < snapshot_index < expected_sha_index < admin_smoke_index < runtime_start_index
    snapshot_block = workflow[snapshot_index - 500 : admin_smoke_index]
    assert "DB_APPLICATION_NAME=aicrm-next-deploy-data-health" in snapshot_block
    assert "PGAPPNAME=aicrm-next-deploy-data-health" in snapshot_block
    assert "tee /tmp/aicrm-data-health-release-snapshot.json" in snapshot_block


def test_deploy_acknowledges_only_exact_authorized_production_terminal_histories_before_green_health() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    orchestrator = TERMINAL_ACKNOWLEDGEMENT_ORCHESTRATOR.read_text(encoding="utf-8")

    acknowledgement_index = workflow.index(
        "scripts/ops/acknowledge_release_terminal_histories.py",
    )
    apply_index = workflow.index("--apply", acknowledgement_index)
    authorization_unset_index = workflow.index(
        "unset AICRM_QUEUE_TERMINAL_ACK_AUTHORIZED",
        apply_index,
    )
    admin_health_index = workflow.index(
        "scripts/ops/check_admin_read_pages_smoke.py",
        authorization_unset_index,
    )

    assert acknowledgement_index < apply_index < authorization_unset_index < admin_health_index
    acknowledgement_block = workflow[acknowledgement_index:admin_health_index]
    assert "queue_all_scope_cutover.json" in orchestrator
    assert "production_terminal_history_acknowledgements.json" in orchestrator
    assert "production_welcome_timeout_acknowledgement.json" in orchestrator
    assert "acknowledge_pre_cutover_welcome_terminal" in orchestrator
    assert "acknowledge_production_terminal_history" in orchestrator
    assert "acknowledge_production_welcome_timeout" in orchestrator
    assert "operator-authorized production terminal histories; no replay" in orchestrator
    assert "operator-authorized welcome job 2157 timeout history; no replay" in orchestrator
    assert "requeue" not in acknowledgement_block.lower()
    assert "requeue" not in orchestrator.lower()
    assert "refund_request(" not in orchestrator
    assert "send_private_message(" not in orchestrator
    assert "send_welcome_msg(" not in orchestrator


def test_remote_deploy_holds_target_specific_server_lock_before_sha_checks() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    target_index = workflow.index("deploy_target=\"${{ env.DEPLOY_TARGET }}\"", workflow.index("Deploy via SSH"))
    lock_file_index = workflow.index('deploy_lock_file="/tmp/aicrm-deploy-${deploy_target}.lock"', target_index)
    lock_fd_index = workflow.index('exec 9>"$deploy_lock_file"', lock_file_index)
    flock_index = workflow.index("if ! flock -n 9; then", lock_fd_index)
    before_sha_index = workflow.index('before_sha="$(git rev-parse HEAD)"', flock_index)
    migration_index = workflow.index("python3 -m alembic upgrade head", before_sha_index)

    assert target_index < lock_file_index < lock_fd_index < flock_index < before_sha_index < migration_index
    assert 'echo "another $deploy_target deployment holds $deploy_lock_file"' in workflow


def test_failed_uncommitted_deploy_restores_previous_exact_sha_and_dependencies() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    switched_init = workflow.index("release_switched=0")
    committed_init = workflow.index("release_committed=0", switched_init)
    cleanup_index = workflow.index("cleanup_deploy()", committed_init)
    transaction_guard = workflow.index('[ "${runtime_mutation_started:-0}" = "1" ]', cleanup_index)
    stop_index = workflow.index("--phase stop-for-migration-recovery --execute", transaction_guard)
    rollback_guard = workflow.index('[ "${release_switched:-0}" = "1" ]', stop_index)
    reset_index = workflow.index('git reset --hard "$before_sha"', rollback_guard)
    release_file_index = workflow.index("printf '%s\\n' \"$before_sha\" > .release-sha", reset_index)
    dependency_guard = workflow.index('git diff --quiet "$before_sha" "$verified_sha" -- requirements.lock', release_file_index)
    dependency_restore = workflow.index("--require-hashes -r requirements.lock", dependency_guard)
    exact_health = workflow.index('grep -i "x-aicrm-release-sha: $restore_expected_sha"', dependency_restore)
    restore_units = workflow.index("--phase install-enable-after-web-health --execute", exact_health)

    assert switched_init < committed_init < cleanup_index < transaction_guard < stop_index < rollback_guard
    assert rollback_guard < reset_index < release_file_index
    assert release_file_index < dependency_guard < dependency_restore < exact_health < restore_units
    assert 'restore_expected_sha="$before_sha"' in workflow
    assert "alembic downgrade" not in workflow


def test_failed_deploy_drain_restores_timers_without_killing_active_oneshots() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    partial_init = workflow.index("runtime_transaction_partial=0")
    mutation_index = workflow.index("runtime_mutation_started=1", partial_init)
    partial_start = workflow.index("runtime_transaction_partial=1", mutation_index)
    stop_index = workflow.index("--phase stop-for-migration --execute", partial_start)
    stopped_index = workflow.index("runtime_units_stopped=1", stop_index)
    partial_clear = workflow.index("runtime_transaction_partial=0", stopped_index)
    cleanup_index = workflow.index("cleanup_deploy()")
    preserve_index = workflow.index("preserving active one-shot workers during rollback", cleanup_index)
    conditional_web_stop = workflow.index('if [ "${runtime_units_stopped:-0}" = "1" ]; then', preserve_index)
    rollback_index = workflow.index('git reset --hard "$before_sha"', conditional_web_stop)
    restore_index = workflow.index("--phase authorize-runtime-restore --execute", rollback_index)
    install_index = workflow.index("--phase install-enable-after-web-health --execute", restore_index)
    release_index = workflow.index("--phase release-runtime-guard --execute", install_index)

    assert partial_init < mutation_index < partial_start < stop_index < stopped_index < partial_clear
    assert cleanup_index < preserve_index < conditional_web_stop < rollback_index < restore_index < install_index < release_index


def test_deploy_recovers_only_exact_identity_worker_self_deadlock_under_transaction_guard() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    mutation_index = workflow.index("runtime_mutation_started=1")
    begin_index = workflow.index("--phase begin-transaction --execute", mutation_index)
    recovery_index = workflow.index("scripts/ops/recover_identity_resolution_worker_deadlock.py", begin_index)
    execute_index = workflow.index("--execute", recovery_index)
    evidence_index = workflow.index("/tmp/aicrm-identity-worker-deadlock-recovery.json", execute_index)
    stop_index = workflow.index("--phase stop-for-migration --execute", evidence_index)

    assert mutation_index < begin_index < recovery_index < execute_index < evidence_index < stop_index


def test_success_marks_release_committed_only_after_public_exact_sha_verification() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    switch_index = workflow.index("release_switched=1")
    local_exact_sha_index = workflow.index('grep -i "x-aicrm-release-sha: $after_sha"', switch_index)
    public_route_index = workflow.index("scripts/ops/ensure_production_public_release_route.py --execute")
    public_exact_sha_index = workflow.index('--expected-sha "$after_sha"', public_route_index)
    committed_index = workflow.index("release_committed=1", public_exact_sha_index)

    assert switch_index < local_exact_sha_index < public_route_index < public_exact_sha_index < committed_index


def test_deploy_requires_runtime_units_and_application_readiness_before_commit() -> None:
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    verify_units_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    readiness_index = workflow.index(
        "curl -sSf http://127.0.0.1:5001/api/system/health",
        verify_units_index,
    )
    restored_flag_index = workflow.index("runtime_units_stopped=0", readiness_index)
    committed_index = workflow.index("release_committed=1", restored_flag_index)

    assert verify_units_index < readiness_index < restored_flag_index < committed_index
    assert "tee /tmp/aicrm-runtime-readiness.json" in workflow


def _deploy_runtime_phase_index(workflow: str, phase: str) -> int:
    if phase == "stop-for-migration":
        mutation_index = workflow.index("runtime_mutation_started=1")
        return workflow.index(f"--phase {phase} --execute", mutation_index)
    reset_index = workflow.index('git reset --hard "$verified_sha"')
    return workflow.index(_runtime_units_phase(phase), reset_index)


def test_production_deploy_loads_postgres_env_before_alembic_upgrade():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    env_source_index = workflow.index("source /home/ubuntu/.openclaw-wecom-pg.env")
    database_url_guard_index = workflow.index('test -n "${DATABASE_URL:-}"')
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")

    assert env_source_index < database_url_guard_index < alembic_upgrade_index
    assert "python3 app.py init-db" not in workflow
    assert "python app.py init-db" not in workflow
    assert "init-db-legacy" not in workflow
    assert "alembic stamp head" not in workflow
    assert "legacy_flask_app" not in workflow


def test_production_deploy_stashes_dirty_worktree_before_remote_update():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    stash_index = workflow.index("git stash push --include-untracked")
    before_sha_index = workflow.index('before_sha="$(git rev-parse HEAD)"')
    verified_sha_index = workflow.index('verified_sha="${{ inputs.release_sha }}"', before_sha_index)
    fetch_index = workflow.index('git fetch --no-tags "$release_bundle" "refs/deploy/release:refs/remotes/deploy/main"')
    reset_index = workflow.index('git reset --hard "$verified_sha"')
    stop_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")

    assert before_sha_index < verified_sha_index < stash_index < fetch_index < reset_index < stop_index
    assert 'release_bundle="/tmp/aicrm-release-$verified_sha/aicrm-release.bundle"' in workflow
    assert "git@github.com" not in workflow
    assert "GIT_SSH_COMMAND" not in workflow


def test_production_deploy_retires_callback_hotfix_overlay_before_migration_and_restart():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    reset_index = workflow.index('git reset --hard "$verified_sha"')
    marker_index = workflow.index("printf '%s\\n' \"$after_sha\" > .release-sha")
    retire_index = workflow.index(_runtime_units_phase("retire-legacy-overlays"))
    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    web_start_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then")
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))

    assert reset_index < marker_index < retire_index < stop_runtime_units_index < alembic_upgrade_index < web_start_index < install_index


def test_production_deploy_installs_dependencies_only_when_hashed_lock_changes():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    fetch_index = workflow.index('git fetch --no-tags "$release_bundle" "refs/deploy/release:refs/remotes/deploy/main"')
    reset_index = workflow.index('git reset --hard "$verified_sha"')
    after_sha_index = workflow.index('after_sha="$(git rev-parse HEAD)"')
    requirements_guard_index = workflow.index('git diff --quiet "$before_sha" "$after_sha" -- requirements.lock')
    pip_install_index = workflow.index("python -m pip install --require-hashes -r requirements.lock")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")

    assert fetch_index < reset_index < after_sha_index < requirements_guard_index < pip_install_index < alembic_upgrade_index
    assert "requirements.lock unchanged; skipping pip install" in workflow


def test_production_deploy_fails_closed_unless_checkout_matches_verified_workflow_sha():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    remote_deploy_index = workflow.index("uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2")
    verified_sha_index = workflow.index('verified_sha="${{ inputs.release_sha }}"', remote_deploy_index)
    release_head_index = workflow.index('release_head_sha="$(git rev-parse refs/remotes/deploy/main)"')
    head_guard_index = workflow.index('if [ "$release_head_sha" != "$verified_sha" ]; then')
    reset_index = workflow.index('git reset --hard "$verified_sha"')
    after_sha_index = workflow.index('after_sha="$(git rev-parse HEAD)"')
    checkout_guard_index = workflow.index('if [ "$after_sha" != "$verified_sha" ]; then')
    stop_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")

    assert verified_sha_index < release_head_index < head_guard_index < reset_index < after_sha_index < checkout_guard_index < stop_index
    assert "invalid verified workflow sha" in workflow
    assert "requested production release is not the current repository main" in workflow
    assert "deployed checkout does not match verified workflow sha" in workflow


def test_production_deploy_verifies_local_bundle_before_fetch_and_stopping_services():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    bundle_index = workflow.index('release_bundle="/tmp/aicrm-release-$verified_sha/aicrm-release.bundle"')
    checksum_index = workflow.index("sha256sum -c aicrm-release.bundle.sha256")
    verify_index = workflow.index('git bundle verify "$release_bundle"')
    head_guard_index = workflow.index('git bundle list-heads "$release_bundle"')
    fetch_index = workflow.index('git fetch --no-tags "$release_bundle" "refs/deploy/release:refs/remotes/deploy/main"')
    stop_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")

    assert bundle_index < checksum_index < verify_index < head_guard_index < fetch_index < stop_index
    assert "release bundle does not advertise the verified workflow sha" in workflow
    assert "git@github.com" not in workflow
    assert "GIT_SSH_COMMAND" not in workflow


def test_production_deploy_builds_and_transfers_incremental_exact_sha_bundle_before_remote_deploy():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    checkout_index = workflow.index("uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0")
    discover_index = workflow.index('public_health_url="$PUBLIC_HEALTH_URL"')
    build_index = workflow.index("git bundle create release/aicrm-release.bundle refs/deploy/release ^refs/deploy/base")
    transfer_index = workflow.index("uses: appleboy/scp-action@ff85246acaad7bdce478db94a363cd2bf7c90345")
    remote_deploy_index = workflow.index("uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2")

    assert checkout_index < discover_index < build_index < transfer_index < remote_deploy_index
    assert "permissions:\n  contents: read" in workflow
    assert "ref: ${{ inputs.release_sha }}" in workflow
    assert "fetch-depth: 0" in workflow
    assert 'verified_sha="${{ inputs.release_sha }}"' in workflow
    assert "git fetch --no-tags origin main" in workflow
    assert 'if [ "$(git rev-parse FETCH_HEAD)" != "$verified_sha" ]; then' in workflow
    assert 'public_health_url="$PUBLIC_HEALTH_URL"' in workflow
    assert 'base_sha="$(awk \'tolower($1) == "x-aicrm-release-sha:" {print $2}\'' in workflow
    assert 'git merge-base --is-ancestor "$base_sha" "$verified_sha"' in workflow
    assert 'git update-ref refs/deploy/release "$verified_sha"' in workflow
    assert 'git update-ref refs/deploy/base "$base_sha"' in workflow
    assert 'echo "base_sha=$base_sha" >> "$GITHUB_OUTPUT"' in workflow
    assert "sha256sum aicrm-release.bundle" in workflow
    assert "git bundle verify release/aicrm-release.bundle" in workflow
    assert "git bundle create release/aicrm-release.bundle HEAD" not in workflow
    assert "target: /tmp/aicrm-release-${{ inputs.release_sha }}" in workflow
    assert "strip_components: 1" in workflow
    assert "overwrite: true" in workflow


def test_production_release_fails_closed_when_live_base_is_missing_or_diverged():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    missing_base_index = workflow.index('if ! git cat-file -e "$base_sha^{commit}"; then')
    missing_base_error_index = workflow.index(
        "production release is not present in repository history",
        missing_base_index,
    )
    ancestor_guard_index = workflow.index(
        'git merge-base --is-ancestor "$base_sha" "$verified_sha"',
        missing_base_error_index,
    )
    ancestor_error_index = workflow.index(
        "production release is not an ancestor of the requested release",
        ancestor_guard_index,
    )
    incremental_mode_index = workflow.index('echo "bundle_mode=incremental" >> "$GITHUB_OUTPUT"')
    remote_mode_index = workflow.index('bundle_mode="${{ steps.release.outputs.bundle_mode }}"')
    remote_mode_guard_index = workflow.index(
        'if [ "$bundle_mode" != "incremental" ]; then',
        remote_mode_index,
    )
    remote_base_guard_index = workflow.index('if [ "$before_sha" != "$base_sha" ]; then', remote_mode_guard_index)

    assert missing_base_index < missing_base_error_index < ancestor_guard_index < ancestor_error_index
    assert ancestor_error_index < incremental_mode_index < remote_mode_index < remote_mode_guard_index < remote_base_guard_index
    assert 'bundle_mode="full"' not in workflow
    assert "test environment release is orphaned" not in workflow


def test_production_deploy_requires_remote_head_to_match_bundle_prerequisite_before_fetch():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    before_sha_index = workflow.index('before_sha="$(git rev-parse HEAD)"')
    base_sha_index = workflow.index('base_sha="${{ steps.release.outputs.base_sha }}"')
    base_guard_index = workflow.index('if [ "$before_sha" != "$base_sha" ]; then')
    bundle_verify_index = workflow.index('git bundle verify "$release_bundle"')
    stash_index = workflow.index("git stash push --include-untracked")
    fetch_index = workflow.index('git fetch --no-tags "$release_bundle" "refs/deploy/release:refs/remotes/deploy/main"')
    stop_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")

    assert before_sha_index < base_sha_index < base_guard_index < bundle_verify_index < stash_index < fetch_index < stop_index
    assert "target checkout moved after the release bundle was built" in workflow


def test_incremental_release_bundle_requires_live_base_and_fetches_exact_merge_sha(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "AI CRM CI")
    _git(source, "config", "user.email", "ci@example.invalid")

    (source / "root.txt").write_text("root\n", encoding="utf-8")
    _git(source, "add", "root.txt")
    _git(source, "commit", "-m", "root")
    root_sha = _git(source, "rev-parse", "HEAD").stdout.strip()
    _git(source, "branch", "feature")

    (source / "main.txt").write_text("main\n", encoding="utf-8")
    _git(source, "add", "main.txt")
    _git(source, "commit", "-m", "main")
    base_sha = _git(source, "rev-parse", "HEAD").stdout.strip()

    _git(source, "checkout", "feature")
    (source / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(source, "add", "feature.txt")
    _git(source, "commit", "-m", "feature")
    _git(source, "checkout", "main")
    _git(source, "merge", "--no-ff", "feature", "-m", "merge")
    verified_sha = _git(source, "rev-parse", "HEAD").stdout.strip()
    _git(source, "update-ref", "refs/deploy/release", verified_sha)
    _git(source, "update-ref", "refs/deploy/base", base_sha)
    _git(source, "branch", "release-root", root_sha)
    _git(source, "branch", "release-base", base_sha)

    bundle = tmp_path / "aicrm-release.bundle"
    _git(source, "bundle", "create", str(bundle), "refs/deploy/release", "^refs/deploy/base")

    missing_base = tmp_path / "missing-base"
    missing_base.mkdir()
    _git(missing_base, "init")
    _git(missing_base, "fetch", str(source), "release-root:refs/heads/release-root")
    missing_verify = _git(missing_base, "bundle", "verify", str(bundle), check=False)
    assert missing_verify.returncode != 0

    receiver = tmp_path / "receiver"
    receiver.mkdir()
    _git(receiver, "init")
    _git(receiver, "fetch", str(source), "release-base:refs/heads/release-base")
    _git(receiver, "bundle", "verify", str(bundle))
    _git(
        receiver,
        "fetch",
        "--no-tags",
        str(bundle),
        "refs/deploy/release:refs/remotes/aicrm-release/main",
    )
    release_sha = _git(receiver, "rev-parse", "refs/remotes/aicrm-release/main").stdout.strip()
    assert release_sha == verified_sha


def test_full_release_bundle_recovers_receiver_with_orphaned_head(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "AI CRM CI")
    _git(source, "config", "user.email", "ci@example.invalid")
    (source / "release.txt").write_text("verified\n", encoding="utf-8")
    _git(source, "add", "release.txt")
    _git(source, "commit", "-m", "verified release")
    verified_sha = _git(source, "rev-parse", "HEAD").stdout.strip()
    _git(source, "update-ref", "refs/deploy/release", verified_sha)

    bundle = tmp_path / "aicrm-full-release.bundle"
    _git(source, "bundle", "create", str(bundle), "refs/deploy/release")

    receiver = tmp_path / "receiver"
    receiver.mkdir()
    _git(receiver, "init", "-b", "main")
    _git(receiver, "config", "user.name", "AI CRM Test")
    _git(receiver, "config", "user.email", "test@example.invalid")
    (receiver / "orphan.txt").write_text("orphaned test release\n", encoding="utf-8")
    _git(receiver, "add", "orphan.txt")
    _git(receiver, "commit", "-m", "orphaned release")
    orphaned_sha = _git(receiver, "rev-parse", "HEAD").stdout.strip()

    _git(receiver, "bundle", "verify", str(bundle))
    _git(
        receiver,
        "fetch",
        "--no-tags",
        str(bundle),
        "refs/deploy/release:refs/remotes/aicrm-release/main",
    )
    release_sha = _git(receiver, "rev-parse", "refs/remotes/aicrm-release/main").stdout.strip()

    assert orphaned_sha != verified_sha
    assert release_sha == verified_sha


def test_production_deploy_refreshes_release_marker_before_restart_and_checks_health_header():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    after_sha_index = workflow.index('after_sha="$(git rev-parse HEAD)"')
    marker_index = workflow.index("printf '%s\\n' \"$after_sha\" > .release-sha")
    start_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then")
    header_curl_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health")
    header_grep_index = workflow.index('grep -i "x-aicrm-release-sha: $after_sha" /tmp/aicrm_health_headers.txt')
    ready_guard_index = workflow.index('if [ "$release_ready" != "1" ]; then')

    assert after_sha_index < marker_index < start_index < header_curl_index < header_grep_index < ready_guard_index


def test_production_deploy_quiesces_web_before_alembic_and_service_restart():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    env_source_index = workflow.index("source /home/ubuntu/.openclaw-wecom-pg.env")
    database_url_guard_index = workflow.index('test -n "${DATABASE_URL:-}"')
    begin_transaction_index = workflow.index("--phase begin-transaction --execute", workflow.index("runtime_mutation_started=1"))
    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    stash_index = workflow.index("git stash push --include-untracked")
    reset_index = workflow.index('git reset --hard "$verified_sha"', stash_index)
    pip_install_index = workflow.index("python -m pip install --require-hashes -r requirements.lock", reset_index)
    preflight_index = workflow.index("--phase preflight", reset_index)
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    stale_listener_index = workflow.index("if sudo fuser -s 5001/tcp; then", stop_runtime_units_index)
    term_kill_index = workflow.index("sudo fuser -k -TERM 5001/tcp || true")
    force_kill_index = workflow.index("sudo fuser -k -KILL 5001/tcp || true")
    wait_for_free_index = workflow.index('echo "waiting for stale 5001 listener to exit"')
    reset_failed_index = workflow.index("sudo systemctl reset-failed openclaw-wecom-postgres.service || true", alembic_upgrade_index)
    start_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then")
    alembic_table = "alembic_" + "version"

    assert env_source_index < database_url_guard_index < alembic_upgrade_index
    assert (
        stash_index
        < reset_index
        < pip_install_index
        < preflight_index
        < begin_transaction_index
        < stop_runtime_units_index
        < stale_listener_index
        < term_kill_index
        < force_kill_index
        < wait_for_free_index
        < alembic_upgrade_index
        < reset_failed_index
        < start_index
    )
    assert "sudo fuser -TERM 5001/tcp" not in workflow
    assert "sudo fuser -KILL 5001/tcp" not in workflow
    assert "python3 app.py init-db" not in workflow
    assert "python app.py init-db" not in workflow
    assert "alembic stamp head" not in workflow
    assert "systemctl mask --runtime" not in workflow
    assert "systemctl unmask --runtime" not in workflow
    assert f"ALTER TABLE IF EXISTS {alembic_table}" not in workflow
    assert f"ALTER TABLE {alembic_table}" not in workflow


def test_production_deploy_migrates_and_reconciles_secret_references_before_web_restart():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    migration_index = workflow.index("python3 scripts/ops/migrate_app_setting_secrets.py --execute")
    auth_bootstrap_index = workflow.index("python3 scripts/ops/bootstrap_auth_clients.py", migration_index)
    refreshed_env_index = workflow.index("source /home/ubuntu/.openclaw-wecom-pg.env", migration_index)
    auth_readiness_index = workflow.index("python3 scripts/ops/check_auth_readiness.py", refreshed_env_index)
    reconciliation_index = workflow.index("python3 scripts/ops/check_secret_reference_cutover.py")
    stop_web_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    start_web_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then")

    assert (
        stop_web_index
        < alembic_upgrade_index
        < migration_index
        < auth_bootstrap_index
        < refreshed_env_index
        < auth_readiness_index
        < reconciliation_index
        < start_web_index
    )
    assert '--secret-store-dir "$AICRM_SECRET_STORE_DIR"' in workflow
    assert "--environment-file /home/ubuntu/.openclaw-wecom-pg.env" in workflow
    assert "tee /tmp/aicrm-secret-migration.json" in workflow
    assert "--apply" in workflow[auth_bootstrap_index:refreshed_env_index]
    assert '--issuer "$auth_issuer"' in workflow[auth_bootstrap_index:reconciliation_index]
    assert 'auth_issuer="${{ env.PUBLIC_BASE_URL }}"' in workflow
    assert "tee /tmp/aicrm-auth-client-bootstrap.json" in workflow
    assert "tee /tmp/aicrm-auth-readiness.json" in workflow
    assert "tee /tmp/aicrm-secret-reconciliation.json" in workflow
    assert "set -x" not in workflow


def test_production_deploy_serializes_workflow_and_host_transactions():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert "group: aicrm-production-deploy" in workflow
    assert "cancel-in-progress: false" in workflow
    remote_deploy_index = workflow.index("uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2")
    lock_path_index = workflow.index('deploy_lock_file="/tmp/aicrm-deploy-${deploy_target}.lock"', remote_deploy_index)
    lock_fd_index = workflow.index('exec 9>"$deploy_lock_file"', lock_path_index)
    flock_index = workflow.index("if ! flock -n 9; then", lock_fd_index)
    checkout_mutation_index = workflow.index("git stash push --include-untracked", flock_index)

    assert remote_deploy_index < lock_path_index < lock_fd_index < flock_index < checkout_mutation_index


def test_production_deploy_failure_after_quiesce_is_fail_closed():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    cleanup_index = workflow.index("cleanup_deploy()")
    trap_index = workflow.index("trap cleanup_deploy EXIT", cleanup_index)
    mutation_flag_index = workflow.index("runtime_mutation_started=1", trap_index)
    begin_runtime_index = workflow.index("--phase begin-transaction --execute", mutation_flag_index)
    stop_runtime_index = workflow.index("--phase stop-for-migration --execute", begin_runtime_index)
    cleanup_guard_index = workflow.index('[ "${runtime_mutation_started:-0}" = "1" ]', cleanup_index)
    cleanup_begin_index = workflow.index(
        "--phase begin-transaction --execute",
        cleanup_guard_index,
    )
    cleanup_stop_runtime_index = workflow.index("--phase stop-for-migration-recovery --execute", cleanup_begin_index)
    cleanup_stop_web_index = workflow.index("sudo systemctl stop openclaw-wecom-postgres.service || true", cleanup_guard_index)
    deploy_stop_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    reset_index = workflow.index('git reset --hard "$verified_sha"')
    install_web_index = workflow.index(_runtime_units_phase("install-primary-web"), reset_index)
    authorize_web_index = workflow.index(_runtime_units_phase("authorize-web-start"), install_web_index)
    start_web_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then", authorize_web_index)
    readiness_index = workflow.index("python scripts/ops/check_runtime_secret_readiness.py", start_web_index)
    authorize_runtime_index = workflow.index(_runtime_units_phase("authorize-runtime-start"), readiness_index)
    staged_verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"), authorize_runtime_index)
    public_route_index = workflow.index("scripts/ops/ensure_production_public_release_route.py --execute")
    release_guard_index = workflow.index(_runtime_units_phase("release-runtime-guard"), public_route_index)
    commit_index = workflow.index("runtime_committed=1", public_route_index)

    assert cleanup_index < trap_index < mutation_flag_index < begin_runtime_index < stop_runtime_index
    assert cleanup_guard_index < cleanup_begin_index < cleanup_stop_runtime_index < cleanup_stop_web_index
    assert reset_index < deploy_stop_index < install_web_index < authorize_web_index < start_web_index
    assert start_web_index < readiness_index < authorize_runtime_index < staged_verify_index < public_route_index
    assert public_route_index < release_guard_index < commit_index
    assert release_guard_index < workflow.index("runtime_committed=1", release_guard_index)
    assert "systemctl mask --runtime" not in workflow


def test_production_deploy_installs_managed_web_and_checks_runtime_secret_readiness_before_workers():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    reconciliation_index = workflow.index("python3 scripts/ops/check_secret_reference_cutover.py")
    install_web_index = workflow.index(_runtime_units_phase("install-primary-web"), reconciliation_index)
    start_web_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then", install_web_index)
    readiness_index = workflow.index("python scripts/ops/check_runtime_secret_readiness.py", start_web_index)
    authorize_runtime_index = workflow.index(_runtime_units_phase("authorize-runtime-start"), readiness_index)
    worker_install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"), readiness_index)
    staged_verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"), worker_install_index)
    public_route_index = workflow.index("scripts/ops/ensure_production_public_release_route.py --execute", staged_verify_index)
    release_guard_index = workflow.index(_runtime_units_phase("release-runtime-guard"), public_route_index)

    assert reconciliation_index < install_web_index < start_web_index < readiness_index < authorize_runtime_index
    assert authorize_runtime_index < worker_install_index < staged_verify_index < public_route_index < release_guard_index
    assert '--expected-sha "$after_sha"' in workflow[readiness_index:worker_install_index]
    assert '--expected-callback-url "$auth_issuer/auth/wecom/callback"' in workflow[readiness_index:worker_install_index]
    assert "tee /tmp/aicrm-runtime-secret-readiness.json" in workflow[readiness_index:worker_install_index]


def test_production_deploy_repairs_only_approved_legacy_nginx_web_route_and_requires_public_exact_sha():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    local_health_index = workflow.index('grep -i "x-aicrm-release-sha: $after_sha"')
    runtime_verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    public_route_index = workflow.index("scripts/ops/ensure_production_public_release_route.py --execute")
    public_sha_index = workflow.index('--expected-sha "$after_sha"', public_route_index)

    assert local_health_index < runtime_verify_index < public_route_index < public_sha_index
    assert '--server-name "${{ env.PUBLIC_SERVER_NAME }}"' in workflow
    assert '--public-health-url "${{ env.PUBLIC_HEALTH_URL }}"' in workflow
    assert '--nginx-config "${{ env.NGINX_CONFIG_PATH }}"' in workflow
    assert "tee /tmp/aicrm-public-release-route.json" in workflow


def test_deploy_workflow_is_production_only_and_uses_only_production_credentials():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    runtime_verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    public_route_index = workflow.index("ensure_production_public_release_route.py", runtime_verify_index)
    public_health_index = workflow.index('"${{ env.PUBLIC_HEALTH_URL }}"', public_route_index)

    assert runtime_verify_index < public_route_index < public_health_index
    assert "environment: production" in workflow
    assert "DEPLOY_TARGET: production" in workflow
    assert "secrets.DEPLOY_HOST" in workflow
    assert "secrets.DEPLOY_USER" in workflow
    assert "secrets.DEPLOY_SSH_KEY" in workflow
    assert "TEST_DEPLOY_" not in workflow
    assert "target_environment" not in workflow
    assert "workflow_run" not in workflow
    assert 'if [ "$deploy_target" != "production" ]; then' in workflow
    assert 'grep -i "x-aicrm-release-sha: $after_sha"' in workflow


def test_production_deploy_polls_health_after_restart_instead_of_fixed_sleep():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    start_index = workflow.index("if ! sudo systemctl start openclaw-wecom-postgres.service; then")
    poll_index = workflow.index("for _ in $(seq 1 60); do", start_index)
    health_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health", poll_index)
    header_index = workflow.index('grep -i "x-aicrm-release-sha: $after_sha" /tmp/aicrm_health_headers.txt', health_index)
    ready_guard_index = workflow.index('if [ "$release_ready" != "1" ]; then', header_index)
    status_index = workflow.index("sudo systemctl status openclaw-wecom-postgres.service --no-pager || true", ready_guard_index)

    assert start_index < poll_index < health_index < header_index < status_index
    assert "sleep 3" not in workflow


def test_deploy_admin_smoke_uses_short_lived_server_session_without_logging_cookie():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    issue_index = workflow.index("python3 scripts/ops/create_deploy_smoke_session.py issue")
    smoke_index = workflow.index("python scripts/ops/check_admin_read_pages_smoke.py", issue_index)
    revoke_index = workflow.index("python3 scripts/ops/create_deploy_smoke_session.py revoke", smoke_index)
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert issue_index < smoke_index < revoke_index < install_index < verify_index
    assert 'deploy_smoke_session_file="$(mktemp /tmp/aicrm-deploy-smoke-session.XXXXXX)"' in workflow
    assert '--output-file "$deploy_smoke_session_file"' in workflow
    assert "--ttl-seconds 300" in workflow
    assert '--admin-cookie-file "$deploy_smoke_session_file"' in workflow
    assert 'admin_smoke_sidebar_args=(--include-all-sidebar --require-all-data-health-green)' in workflow
    assert '--cookie-file "$deploy_smoke_session_file"' in workflow
    assert "aicrm_next_admin_session=" not in workflow
    assert 'cat "$deploy_smoke_session_file"' not in workflow
    assert 'echo "$deploy_smoke_session_file"' not in workflow


def test_deploy_exit_trap_revokes_smoke_session_and_restores_runtime_units():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    cleanup_index = workflow.index("cleanup_deploy() {")
    stop_index = workflow.index("--phase stop-for-migration-recovery --execute", cleanup_index)
    verify_index = workflow.index("--phase verify-staged-runtime --execute", stop_index)
    trap_index = workflow.index("trap cleanup_deploy EXIT", verify_index)
    restored_flag_index = workflow.index("runtime_units_stopped=0", trap_index)

    assert cleanup_index < stop_index < verify_index < trap_index < restored_flag_index
    cleanup = workflow[cleanup_index:trap_index]
    assert "create_deploy_smoke_session.py revoke" in cleanup
    assert 'if [ "${runtime_units_stopped:-0}" = "1" ]; then' in cleanup
    assert 'echo "restoring runtime units for $restore_expected_sha"' in cleanup
    assert 'git reset --hard "$before_sha"' in cleanup
    assert 'grep -i "x-aicrm-release-sha: $restore_expected_sha"' in cleanup
    assert "--phase install-enable-after-web-health --execute" in cleanup
    assert "--phase verify-staged-runtime --execute" in cleanup
    assert "restored_web_ready" in cleanup


def test_production_deploy_refreshes_customer_projection_through_the_scoped_internal_consumer_before_smoke():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    readiness_index = workflow.index("python scripts/ops/check_runtime_secret_readiness.py")
    authorization_index = workflow.index(
        "export AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED=1",
        readiness_index,
    )
    consumer_index = workflow.index(
        "python3 scripts/ops/run_release_customer_read_model_refresh.py",
        authorization_index,
    )
    release_sha_index = workflow.index('--release-sha "$after_sha"', consumer_index)
    unset_index = workflow.index(
        "unset AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED",
        release_sha_index,
    )
    smoke_index = workflow.index("python scripts/ops/check_admin_read_pages_smoke.py", unset_index)

    assert readiness_index < authorization_index < consumer_index < release_sha_index
    assert release_sha_index < unset_index < smoke_index
    assert "AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED=1" in workflow[readiness_index:smoke_index]
    assert '--release-sha "$after_sha"' in workflow[consumer_index:smoke_index]
    refresh_runner = (ROOT / "scripts" / "ops" / "run_release_customer_read_model_refresh.py").read_text(
        encoding="utf-8"
    )
    assert "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES" in refresh_runner
    assert "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS" in refresh_runner
    assert '"candidate_count": 0 if already_completed else 1' in refresh_runner
    assert '"succeeded_count": 0 if already_completed else 1' in refresh_runner
    assert "release_refresh_completion_mismatch" in refresh_runner
    assert '"real_external_call_executed": False' in refresh_runner


def test_queue_production_diagnostics_is_count_only_and_requires_exact_public_release():
    workflow = (ROOT / ".github" / "workflows" / "queue-production-diagnostics.yml").read_text(encoding="utf-8")

    assert "DIAGNOSE AI-CRM QUEUE PRODUCTION READ ONLY" in workflow
    assert 'test "$public_sha" = "$expected_release_sha"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$expected_release_sha"' in workflow
    source_index = workflow.index("source /home/ubuntu/.openclaw-wecom-pg.env")
    assert workflow.rindex("set -a", 0, source_index) < source_index
    assert source_index < workflow.index("set +a", source_index)
    assert "_external_effect_failed_retryable_backlog" in workflow
    assert "preprovider_identity_history" in workflow
    assert "exact_gate_attempt_count" in workflow
    assert "contact_absence_terminal_history" in workflow
    assert "error_message_class" in workflow
    assert "invalid_external_userid" in workflow
    assert "private_target_rejected" in workflow
    assert "_customer_360_freshness_guard" in workflow
    assert "COUNT(*) AS row_count" in workflow
    assert "succeeded_same_business" in workflow
    assert "succeeded_same_target_after_terminal" in workflow
    assert "deferred_identity_projection" in workflow
    assert "provider_boundary_attempt_count" in workflow
    assert "welcome_graph_terminal" in workflow
    assert "payment_refund_terminal_history" in workflow
    assert "merchant_balance_insufficient" in workflow
    assert "merchant_refund_permission_denied" in workflow
    assert "provider_error_code_present" in workflow
    assert '"target_id"' not in workflow
    assert "job.payload_json" not in workflow
    assert "request_payload_json" not in workflow
    assert "response_payload_json AS" not in workflow
    assert "provider_error_message" not in workflow
    assert "UPDATE external_effect_job" not in workflow
    assert "DELETE FROM external_effect_job" not in workflow
    assert "INSERT INTO external_effect_job" not in workflow


def test_production_deploy_retires_legacy_external_push_worker():
    manifest = json.loads((ROOT / "deploy" / "production_runtime_units.json").read_text(encoding="utf-8"))
    active_timers = {item["timer"] for item in manifest["active_autostart"]}
    retired = set(manifest["retired_forbidden"])

    assert "openclaw-external-push-worker.timer" not in active_timers
    assert "openclaw-external-push-worker.timer" in retired
    assert "openclaw-external-push-worker.service" in retired


def test_production_deploy_installs_external_effect_queue_worker_timer_without_manual_execute():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    health_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health", workflow.index("for _ in $(seq 1 60); do"))
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert stop_runtime_units_index < alembic_upgrade_index
    assert health_index < install_index < verify_index
    assert "sudo systemctl start openclaw-external-effect-worker.service" not in workflow


def test_production_deploy_installs_and_runs_broadcast_queue_worker_timer():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    health_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health", workflow.index("for _ in $(seq 1 60); do"))
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    assert health_index < install_index < verify_index


def test_production_deploy_installs_and_runs_internal_event_worker_timer():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    health_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health", workflow.index("for _ in $(seq 1 60); do"))
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    reconciliation_index = workflow.index("python scripts/ops/reconcile_internal_event_outbox.py")

    assert health_index < install_index < verify_index < reconciliation_index
    assert "python scripts/ops/reconcile_internal_event_outbox.py --repair" not in workflow


def test_production_deploy_runs_commerce_fulfillment_reconciliation_count_only():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    internal_event_index = workflow.index("python scripts/ops/reconcile_internal_event_outbox.py")
    commerce_index = workflow.index("python scripts/ops/reconcile_commerce_fulfillment.py")

    assert verify_index < internal_event_index < commerce_index
    assert "python scripts/ops/reconcile_commerce_fulfillment.py --repair" not in workflow


def test_production_deploy_runs_r09_and_r10_reconciliation_count_only():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))
    commerce_index = workflow.index("python scripts/ops/reconcile_commerce_fulfillment.py")
    questionnaire_index = workflow.index("python scripts/ops/reconcile_questionnaire_radar.py")
    group_ops_index = workflow.index("python scripts/ops/reconcile_group_ops_broadcast.py")

    assert verify_index < commerce_index < questionnaire_index < group_ops_index
    assert "python scripts/ops/reconcile_questionnaire_radar.py --repair" not in workflow
    assert "python scripts/ops/reconcile_group_ops_broadcast.py --repair" not in workflow
    assert "tee /tmp/aicrm-questionnaire-radar-reconciliation.json" in workflow
    assert "tee /tmp/aicrm-group-ops-broadcast-reconciliation.json" in workflow


def test_external_push_worker_systemd_units_are_not_deployable():
    assert not (ROOT / "deploy" / "openclaw-external-push-worker.service").exists()
    assert not (ROOT / "deploy" / "openclaw-external-push-worker.timer").exists()


def test_external_effect_queue_worker_systemd_units_are_deployable():
    service = (ROOT / "deploy" / "openclaw-external-effect-worker.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "openclaw-external-effect-worker.timer").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "python scripts/run_external_effect_queue_worker.py --execute" in service
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=openclaw-external-effect-worker.service" in timer


def test_production_deploy_installs_payment_reconciliation_and_identity_workers():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert stop_runtime_units_index < alembic_upgrade_index < install_index < verify_index


def test_payment_reconciliation_and_identity_worker_units_are_deployable():
    payment_service = (ROOT / "deploy" / "openclaw-wechat-pay-order-reconciliation-worker.service").read_text(encoding="utf-8")
    payment_timer = (ROOT / "deploy" / "openclaw-wechat-pay-order-reconciliation-worker.timer").read_text(encoding="utf-8")
    identity_service = (ROOT / "deploy" / "openclaw-identity-resolution-worker.service").read_text(encoding="utf-8")
    identity_timer = (ROOT / "deploy" / "openclaw-identity-resolution-worker.timer").read_text(encoding="utf-8")

    for service in (payment_service, identity_service):
        assert "After=network.target openclaw-wecom-postgres.service" in service
        assert "Requires=openclaw-wecom-postgres.service" in service
        assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
        assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
        assert "wecom_ability_service" not in service
        assert "legacy_flask_app" not in service
        assert "run-legacy" not in service

    assert "python scripts/run_wechat_pay_order_reconciliation_worker.py --execute" in payment_service
    assert "OnCalendar=*-*-* *:0/10:45" in payment_timer
    assert "Persistent=true" in payment_timer
    assert "Unit=openclaw-wechat-pay-order-reconciliation-worker.service" in payment_timer

    assert "python scripts/run_identity_resolution_backfill_worker.py --execute --limit 20" in identity_service
    assert "OnCalendar=*-*-* *:0/2:20" in identity_timer
    assert "Persistent=true" in identity_timer
    assert "Unit=openclaw-identity-resolution-worker.service" in identity_timer


def test_internal_event_worker_systemd_units_are_deployable():
    service = (ROOT / "deploy" / "openclaw-internal-event-worker.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "openclaw-internal-event-worker.timer").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_ENABLED=1" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED=1" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED=1" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_SHADOW_ONLY=1" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_AUTO_EXECUTE=1" in service
    assert "Environment=AICRM_INTERNAL_EVENT_RELAY_ROLE=owner" in service
    assert "Environment=AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE=50" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE=50" in service
    assert "Environment=AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE=50" in service
    assert "payment.succeeded:service_period_entitlement_consumer" in service
    assert "payment.succeeded:webhook_order_paid_consumer" in service
    assert "questionnaire.submitted:questionnaire_projection_consumer" in service
    assert "questionnaire.submitted:questionnaire_webhook_consumer" in service
    assert "questionnaire.submitted:questionnaire_tag_consumer" in service
    assert "questionnaire.submitted:automation_questionnaire_consumer" in service
    assert "questionnaire.submitted:customer_summary_consumer" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "ExecStart=/usr/bin/env" in service
    assert "/home/ubuntu/venvs/openclaw/bin/python scripts/run_internal_event_worker.py --execute --limit 50" in service
    assert "/bin/bash -lc" not in service
    assert "wecom_ability_service" not in service
    assert "legacy_flask_app" not in service
    assert "run-legacy" not in service
    assert "OnCalendar=*-*-* *:*:40" in timer
    assert "Persistent=true" in timer
    assert "Unit=openclaw-internal-event-worker.service" in timer


def test_production_deploy_installs_callback_ingress_and_worker_isolated_runtime():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    health_index = workflow.index("curl -sSf -D /tmp/aicrm_health_headers.txt http://127.0.0.1:5001/health", workflow.index("for _ in $(seq 1 60); do"))
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    retry_loop_index = workflow.index("for callback_smoke_attempt in 1 2; do")
    smoke_index = workflow.index("python scripts/ops/check_wecom_callback_deploy_smoke.py", retry_loop_index)
    smoke_evidence_index = workflow.index("tee /tmp/wecom-callback-deploy-smoke.json")
    retry_message_index = workflow.index("callback deploy smoke failed during runtime warm-up; retrying once")
    failure_guard_index = workflow.index('if [ "$callback_smoke_ready" != "1" ]; then')
    ingress_status_index = workflow.index(
        "systemctl status openclaw-wecom-callback-ingress.service",
        failure_guard_index,
    )
    ingress_journal_index = workflow.index(
        "journalctl -u openclaw-wecom-callback-ingress.service -n 120",
        ingress_status_index,
    )
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert stop_runtime_units_index < alembic_upgrade_index
    assert health_index < install_index < retry_loop_index < smoke_index < smoke_evidence_index
    assert smoke_evidence_index < retry_message_index < failure_guard_index
    assert failure_guard_index < ingress_status_index < ingress_journal_index < verify_index
    assert "callback_smoke_ready=0" in workflow
    assert 'if [ "$callback_smoke_attempt" -lt 2 ]; then' in workflow
    assert "sleep 2" in workflow[retry_message_index:failure_guard_index]
    assert "nginx-wecom-callback-ingress.conf.example /etc" not in workflow


def test_wecom_callback_ingress_systemd_unit_is_deployable():
    service = (ROOT / "deploy" / "openclaw-wecom-callback-ingress.service").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "Environment=WECOM_CALLBACK_INGRESS_HOST=127.0.0.1" in service
    assert "Environment=WECOM_CALLBACK_INGRESS_PORT=5002" in service
    assert "Environment=APP_PORT=5002" not in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "python scripts/run_wecom_callback_ingress.py" in service
    assert "Restart=always" in service
    assert "wecom_ability_service" not in service
    assert "legacy_flask_app" not in service
    assert "run-legacy" not in service


def test_wecom_callback_inbox_worker_systemd_units_are_deployable():
    service = (ROOT / "deploy" / "openclaw-wecom-callback-inbox-worker.service").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "Environment=AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1" in service
    assert "Environment=AICRM_WECOM_CALLBACK_INBOX_WORKER_BATCH_SIZE=20" in service
    assert "Environment=AICRM_WECOM_CALLBACK_INBOX_WORKER_MAX_EXECUTE_BATCH_SIZE=20" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "Type=simple" in service
    assert "python scripts/run_wecom_callback_inbox_worker.py --execute --loop" in service
    assert "AICRM_WECOM_CALLBACK_INBOX_WORKER_POLL_INTERVAL_SECONDS=0.25" in service
    assert "Restart=always" in service
    assert "WantedBy=multi-user.target" in service
    assert "wecom_ability_service" not in service
    assert "legacy_flask_app" not in service
    assert "run-legacy" not in service
    assert not (ROOT / "deploy" / "openclaw-wecom-callback-inbox-worker.timer").exists()


def test_aicrm_canonical_runtime_isolation_systemd_units_are_deployable():
    web = (ROOT / "deploy" / "openclaw-wecom-postgres.service").read_text(encoding="utf-8")
    ingress = (ROOT / "deploy" / "aicrm-wecom-ingress.service").read_text(encoding="utf-8")
    callback_worker = (ROOT / "deploy" / "aicrm-wecom-callback-worker.service").read_text(encoding="utf-8")
    internal_worker = (ROOT / "deploy" / "aicrm-internal-event-worker.service").read_text(encoding="utf-8")
    external_worker = (ROOT / "deploy" / "aicrm-external-effect-worker.service").read_text(encoding="utf-8")

    for service in (ingress, callback_worker, internal_worker, external_worker):
        assert "After=network.target openclaw-wecom-postgres.service" in service
        assert "Requires=openclaw-wecom-postgres.service" in service
        assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
        assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
        assert "wecom_ability_service" not in service
        assert "legacy_flask_app" not in service
        assert "run-legacy" not in service

    assert not (ROOT / "deploy" / "aicrm-web.service").exists()
    assert "After=network.target postgresql.service" in web
    assert "User=ubuntu" in web
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in web
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in web
    assert "python app.py run" in web
    assert "Environment=APP_PORT=5002" in ingress
    assert "python scripts/run_wecom_callback_ingress.py" in ingress
    assert "Environment=AICRM_WECOM_CALLBACK_INBOX_WORKER_BATCH_SIZE=20" in callback_worker
    assert "Environment=AICRM_WECOM_CALLBACK_INBOX_WORKER_MAX_EXECUTE_BATCH_SIZE=20" in callback_worker
    assert "python scripts/run_wecom_callback_inbox_worker.py --execute --loop" in callback_worker
    assert "Restart=always" in callback_worker
    assert "python scripts/run_internal_event_worker.py --execute" in internal_worker
    assert "python scripts/run_external_effect_queue_worker.py --execute" in external_worker


def test_wechat_shop_order_sync_systemd_units_are_deployable():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "aicrm-wechat-shop-order-sync.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "aicrm-wechat-shop-order-sync.timer").read_text(encoding="utf-8")

    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert install_index < verify_index
    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "python -m scripts.run_wechat_shop_order_sync --mode incremental" in service
    assert "OnCalendar=*-*-* *:0/10:30" in timer
    assert "Persistent=true" in timer
    assert "Unit=aicrm-wechat-shop-order-sync.service" in timer


def test_broadcast_queue_worker_systemd_units_are_deployable():
    service = (ROOT / "deploy" / "openclaw-broadcast-queue-worker.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "openclaw-broadcast-queue-worker.timer").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "Environment=AICRM_GROUP_OPS_MATERIAL_UPLOAD_MODE=real" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "python scripts/run_broadcast_queue_worker.py" in service
    assert "wecom_ability_service" not in service
    assert "legacy_flask_app" not in service
    assert "run-legacy" not in service
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=openclaw-broadcast-queue-worker.service" in timer


def test_archive_sync_systemd_units_are_deployable():
    service = (ROOT / "deploy" / "aicrm-archive-sync.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "aicrm-archive-sync.timer").read_text(encoding="utf-8")

    assert "After=network.target openclaw-wecom-postgres.service" in service
    assert "Requires=openclaw-wecom-postgres.service" in service
    assert "EnvironmentFile=/home/ubuntu/.openclaw-wecom-pg.env" in service
    assert "Environment=APP_HOST=127.0.0.1" in service
    assert "Environment=APP_PORT=5001" in service
    assert "WorkingDirectory=/home/ubuntu/极简 crm" in service
    assert "python -m scripts.run_incremental_archive_sync" in service
    assert "wecom_ability_service" not in service
    assert "legacy_flask_app" not in service
    assert "run-legacy" not in service
    assert "OnCalendar=*-*-* *:00/5:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=aicrm-archive-sync.service" in timer


def test_pg_only_ops_tools_do_not_expose_sqlite_entrypoints():
    assert not (ROOT / "scripts" / "backup_sqlite.sh").exists()
    retired_seed_demo = ROOT / "scripts" / ("seed_" + "automation_conversion_demo.py")
    assert not retired_seed_demo.exists()
    assert not (ROOT / ("wecom_ability" + "_service") / "http").exists()

    broadcast_worker = (ROOT / "scripts" / "run_broadcast_queue_worker.py").read_text(encoding="utf-8")
    alembic_env = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "DATABASE_PATH`` / ``DATABASE_URL" not in broadcast_worker
    assert "DATABASE_PATH" not in alembic_env
    assert "data.sqlite3" not in alembic_env
    assert "sqlite:///" not in alembic_env


def test_makefile_check_uses_existing_quality_gate_targets():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "check: lint typecheck build" in makefile
    assert "customer-pulse-quality" not in makefile
    assert "scripts/run_customer_pulse_quality_gates.py" not in makefile
    assert "tests/test_customer_pulse_inbox.py" not in makefile
    assert "tests/test_customer_pulse_quality_gates.py" not in makefile


def test_postgres_backup_restore_share_database_url_guard():
    helper = (ROOT / "scripts" / "_postgres_env.sh").read_text(encoding="utf-8")
    backup = (ROOT / "scripts" / "backup_postgres.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_postgres.sh").read_text(encoding="utf-8")

    assert "require_database_url()" in helper
    assert 'echo "DATABASE_URL is required" >&2' in helper
    assert 'source "${SCRIPT_DIR}/_postgres_env.sh"' in backup
    assert 'source "${SCRIPT_DIR}/_postgres_env.sh"' in restore
    assert "require_database_url" in backup
    assert "require_database_url" in restore


def test_batch_scripts_share_int_env_reader():
    runtime = (ROOT / "scripts" / "script_runtime.py").read_text(encoding="utf-8")
    broadcast_worker = (ROOT / "scripts" / "run_broadcast_queue_worker.py").read_text(encoding="utf-8")

    assert "def read_int_env" in runtime
    assert 'read_int_env("BROADCAST_QUEUE_BATCH_SIZE", 50)' in broadcast_worker
    assert "int(os.environ.get" not in broadcast_worker


def test_due_runner_scripts_share_int_env_reader():
    external_push_worker = (ROOT / "scripts" / "run_external_push_worker.py").read_text(encoding="utf-8")
    internal_event_worker = (ROOT / "scripts" / "run_internal_event_worker.py").read_text(encoding="utf-8")
    ai_audience_scheduler = (ROOT / "scripts" / "run_ai_audience_scheduler.py").read_text(encoding="utf-8")
    ai_audience_scheduler_runtime = (ROOT / "aicrm_next" / "ai_audience_ops" / "scheduler.py").read_text(encoding="utf-8")

    assert not (ROOT / "scripts" / "run_automation_sop.py").exists()
    assert 'read_int_env("EXTERNAL_PUSH_WORKER_BATCH_SIZE", DEFAULT_BATCH_SIZE)' in external_push_worker
    assert "CommerceFulfillmentReconciliationService().diagnose()" in external_push_worker
    assert "run_due_external_push_events" not in external_push_worker
    assert "run_due_external_push_retries" not in external_push_worker
    assert 'read_int_env("AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE", DEFAULT_WORKER_BATCH_SIZE)' in internal_event_worker
    assert "build_internal_event_consumer_registry" in internal_event_worker
    assert 'read_int_env("AICRM_AI_AUDIENCE_SCHEDULER_BATCH_SIZE", 20)' in ai_audience_scheduler
    assert "--execute" in internal_event_worker
    assert 'relay_role="owner"' in internal_event_worker
    assert 'relay_role="consumer_only"' in ai_audience_scheduler_runtime
    assert "int(os.environ.get" not in external_push_worker
    assert "int(os.environ.get" not in internal_event_worker
    assert "int(os.environ.get" not in ai_audience_scheduler


def test_ai_audience_scheduler_runs_through_internal_event_queue_only():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    scheduler = (ROOT / "scripts" / "run_ai_audience_scheduler.py").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "openclaw-ai-audience-scheduler.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "openclaw-ai-audience-scheduler.timer").read_text(encoding="utf-8")
    stop_runtime_units_index = _deploy_runtime_phase_index(workflow, "stop-for-migration")
    alembic_upgrade_index = workflow.index("python3 -m alembic upgrade head")
    install_index = workflow.index(_runtime_units_phase("install-enable-after-web-health"))
    verify_index = workflow.index(_runtime_units_phase("verify-staged-runtime"))

    assert stop_runtime_units_index < alembic_upgrade_index < install_index < verify_index
    assert "register_ai_audience_event_consumers()" in scheduler
    assert 'read_int_env("AICRM_AI_AUDIENCE_SCHEDULER_BATCH_SIZE", 20)' in scheduler
    assert "run_due_ai_audience_consumers" in scheduler
    assert "--run-consumers --execute" in service
    assert "Environment=AICRM_INTERNAL_EVENT_RELAY_ROLE=consumer_only" in service
    assert "ExecStart=/bin/bash -c" in service
    assert "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES=" in service
    assert "ai_audience.refresh.incremental_tick,ai_audience.refresh.daily_tick" in service
    assert "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS=" in service
    assert "ai_audience.refresh.incremental_tick:ai_audience_incremental_refresh_consumer" in service
    assert "ai_audience.refresh.daily_tick:ai_audience_daily_refresh_consumer" in service
    assert "ai_audience.run.refreshed:ai_audience_outbound_effect_planner" in service
    assert "ai_audience.member.updated:ai_audience_outbound_effect_planner" not in service
    assert "ai_audience.member.exited:ai_audience_outbound_effect_planner" not in service
    assert "ExternalEffectWorker" not in service
    assert "run_external_effect_queue_worker.py" not in service
    assert "OnCalendar=*-*-* *:0/3:00" in timer


def test_queue_v2_ai_audience_replacement_preserves_three_minute_refresh_contract():
    service = (ROOT / "deploy" / "aicrm-ai-audience-daily-intent.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "aicrm-ai-audience-daily-intent.timer").read_text(encoding="utf-8")

    assert "--refresh-intent-clock --daily-at 02:00" in service
    assert "--run-consumers" not in service
    assert "--execute" not in service
    assert "run_external_effect_queue_worker.py" not in service
    assert "OnCalendar=*-*-* *:0/3:00" in timer
    assert "02:00:00 Asia/Shanghai" not in timer


def test_production_runtime_declares_exactly_one_internal_event_relay_owner():
    manifest = json.loads((ROOT / "deploy" / "production_runtime_units.json").read_text(encoding="utf-8"))
    legacy = {
        item["service"]
        for item in manifest["cutover_managed_legacy"]["timers"]
    }
    successors = {
        item["capability"]: item["successor_unit"]
        for item in manifest["cutover_successor_matrix"]["owners"]
    }

    assert "openclaw-ai-audience-scheduler.service" in legacy
    assert "openclaw-internal-event-worker.service" in legacy
    assert successors["internal_event_dispatch"] == "aicrm-internal-queue-runtime.service"
    assert successors["ai_audience_refresh_intent_clock"] == "aicrm-ai-audience-daily-intent.timer"

    owner_sources = [
        path.relative_to(ROOT).as_posix()
        for root in (ROOT / "aicrm_next", ROOT / "scripts")
        for path in root.rglob("*.py")
        if 'relay_role="owner"' in path.read_text(encoding="utf-8")
    ]
    assert owner_sources == ["scripts/run_internal_event_worker.py"]


def _calls_utcnow(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "utcnow":
            return True
        if isinstance(node.func, ast.Name) and node.func.id == "utcnow":
            return True
    return False


def test_runtime_code_does_not_use_deprecated_utcnow():
    offenders = sorted(path.relative_to(ROOT).as_posix() for path in RUNTIME_DIR.rglob("*.py") if "__pycache__" not in path.parts and _calls_utcnow(path))

    assert not offenders, f"Runtime code must use explicit timezone-aware UTC helpers instead of datetime.utcnow(). Offenders: {offenders}"


def test_alembic_0002_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0002_perf_indexes_and_trace.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "TIMESTAMPTZ" in migration


def test_alembic_0003_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0003_member_segment_columns.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "information_schema.columns" in migration
    assert "DROP COLUMN IF EXISTS" in migration


def test_alembic_0004_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0004_cloud_orchestrator.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "datetime('now'" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "workflow_id BIGINT NOT NULL" in migration
    assert "JSONB NOT NULL DEFAULT '[]'::jsonb" in migration
    assert "BOOLEAN NOT NULL DEFAULT TRUE" in migration
    assert "TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in migration
    assert "next_node_id BIGINT" in migration
    assert "DROP COLUMN IF EXISTS" in migration


def test_alembic_0005_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0005_segments_and_campaigns.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "sql_dialect TEXT NOT NULL DEFAULT 'postgres'" in migration
    assert "JSONB NOT NULL DEFAULT '[]'::jsonb" in migration
    assert "BOOLEAN NOT NULL DEFAULT TRUE" in migration
    assert "ADD COLUMN IF NOT EXISTS segment_id BIGINT" in migration


def test_alembic_0006_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0006_miniprogram_library.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "BOOLEAN NOT NULL DEFAULT TRUE" in migration
    assert "JSONB NOT NULL DEFAULT '[]'::jsonb" in migration


def test_alembic_0007_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0007_image_library.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "thumb_image_id BIGINT" in migration
    assert "TIMESTAMPTZ" in migration


def test_alembic_0008_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0008_broadcast_jobs.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "AUTOINCREMENT" not in migration
    assert "BIGSERIAL PRIMARY KEY" in migration
    assert "BOOLEAN NOT NULL DEFAULT FALSE" in migration
    assert "JSONB NOT NULL DEFAULT '[]'::jsonb" in migration
    assert "WHERE source_id <> ''" in migration


def test_alembic_0009_is_pg_only():
    migration = (ROOT / "migrations" / "versions" / "0009_image_library_semantic.py").read_text(encoding="utf-8")

    assert "_is_postgres" not in migration
    assert "PRAGMA" not in migration
    assert "TEXT NOT NULL DEFAULT '[]'" not in migration
    assert "JSONB NOT NULL DEFAULT '[]'::jsonb" in migration
    assert "USING GIN (tags)" in migration


def test_deploy_runs_runtime_environment_as_repository_module():
    workflow = PRODUCTION_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "python3 -m scripts.ops.ensure_runtime_environment" in workflow
    assert "python3 scripts/ops/ensure_runtime_environment.py" not in workflow
    persistence_index = workflow.index("python3 -m scripts.ops.ensure_runtime_environment")
    runtime_start_index = workflow.index("sudo systemctl start openclaw-wecom-postgres.service", persistence_index)
    flag_index = workflow.index("runtime_environment_args+=(--allow-missing-wechat-shop-callback-token)")

    assert flag_index < persistence_index < runtime_start_index
    assert '"${runtime_environment_args[@]}"' in workflow[persistence_index:runtime_start_index]
