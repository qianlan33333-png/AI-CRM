from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import (
    inspect_automation_bindings,
)
from aicrm_next.platform.shared.db_session import get_session_factory
from scripts.ops.remediate_ai_audience_binding_history import (
    RemediationError,
    issue_fingerprint,
    issue_kind_counts,
    load_manifest,
    run_remediation,
)


PRODUCTION_SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def _manifest(report, automation_id: int) -> dict:
    return {
        "schema_version": 1,
        "operation_id": "pytest_binding_history",
        "expected_production_sha": PRODUCTION_SHA,
        "automation_id": automation_id,
        "expected_issue_count": len(report.issues),
        "expected_issue_kind_counts": issue_kind_counts(report.issues),
        "expected_issue_fingerprint": issue_fingerprint(report.issues),
        "execute_confirmation": "EXECUTE_PYTEST_BINDING_HISTORY",
    }


def _seed_history_anomaly() -> tuple[int, int, str]:
    session_factory = get_session_factory()
    target_key = "remediation_target_pkg"
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package (package_key, name, status, created_at, updated_at)
                VALUES (:package_key, '目标包', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"package_key": target_key},
        )
        archived_package_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package (package_key, name, status, created_at, updated_at)
                    VALUES ('remediation_archived_pkg', '归档包', 'archived', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        automation_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO automation_agent_runtime_config (
                        agent_code, agent_name, automation_type, bound_package_key, status,
                        draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                        draft_version, published_version, fixed_content_package_json, send_webhook_url,
                        created_at, updated_at
                    ) VALUES (
                        'remediation_agent', '修复 Agent', 'agent', 'missing_package_key', 'active',
                        '', '', '', '', 1, 1, '{}'::jsonb,
                        '/api/ai/audience/packages/remediation_target_pkg/webhook',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        subscription_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_outbound_subscription (package_id, webhook_url)
                    VALUES (:package_id, '/api/ai/agents/remediation_agent/audience-webhook')
                    RETURNING id
                    """
                ),
                {"package_id": archived_package_id},
            ).scalar_one()
        )
        session.commit()
    return automation_id, subscription_id, target_key


def _seed_retired_binding_anomaly() -> tuple[int, str, str]:
    session_factory = get_session_factory()
    agent_code = "new_agent_1784189809857"
    package_key = "new_package_key"
    with session_factory() as session:
        automation_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO automation_agent_runtime_config (
                        agent_code, agent_name, automation_type, bound_package_key, status,
                        draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                        draft_version, published_version, fixed_content_package_json, send_webhook_url,
                        created_at, updated_at
                    ) VALUES (
                        :agent_code, '新建 Agent', 'agent', :package_key, 'active',
                        'role', 'task', 'role', 'task', 1, 1, '{}'::jsonb,
                        :send_webhook_url, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING id
                    """
                ),
                {
                    "agent_code": agent_code,
                    "package_key": package_key,
                    "send_webhook_url": f"/api/ai/audience/packages/{package_key}/webhook",
                },
            ).scalar_one()
        )
        session.commit()
    return automation_id, agent_code, package_key


def _seed_e2e_subscription_residue() -> tuple[list[int], list[dict[str, object]]]:
    session_factory = get_session_factory()
    subscription_ids: list[int] = []
    packages: list[dict[str, object]] = []
    with session_factory() as session:
        for index in range(3):
            package_key = f"prod_e2e_residue_{index}"
            package_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package (package_key, name, status, created_at, updated_at)
                        VALUES (:package_key, 'E2E residue', 'archived', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        RETURNING id
                        """
                    ),
                    {"package_key": package_key},
                ).scalar_one()
            )
            subscription_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_outbound_subscription (package_id, status, webhook_url)
                        VALUES (
                            :package_id,
                            'active',
                            'https://www.youcangogogo.com/api/ai/audience/test-agent/webhook'
                        )
                        RETURNING id
                        """
                    ),
                    {"package_id": package_id},
                ).scalar_one()
            )
            subscription_ids.append(subscription_id)
            packages.append({"id": package_id, "subscription_id": subscription_id, "package_key": package_key})
        session.commit()
    return subscription_ids, packages


def test_binding_history_remediation_previews_snapshots_applies_and_is_idempotent(next_pg_schema, tmp_path) -> None:
    del next_pg_schema
    session_factory = get_session_factory()
    automation_id, subscription_id, target_key = _seed_history_anomaly()
    with session_factory() as session:
        report = inspect_automation_bindings(session.connection())
    assert issue_kind_counts(report.issues) == {
        "agent_package_mismatch": 1,
        "orphan_agent_binding": 1,
        "orphan_subscription_package": 1,
    }
    manifest = _manifest(report, automation_id)
    backup_dir = tmp_path / "backups"

    preview = run_remediation(
        manifest,
        mode="preview",
        confirmation="",
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert preview["status"] == "ready"
    assert preview["database_write_executed"] is False
    assert not backup_dir.exists()

    with pytest.raises(RemediationError, match="execute_confirmation_invalid"):
        run_remediation(
            manifest,
            mode="apply",
            confirmation="WRONG_CONFIRMATION",
            backup_dir=backup_dir,
            current_release_sha=PRODUCTION_SHA,
            session_factory=session_factory,
        )

    applied = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert applied["status"] == "applied"
    assert applied["database_write_executed"] is True
    backup_path = backup_dir / str(applied["backup_path"]).split("/")[-1]
    assert backup_path.exists()
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["automation_row"]["id"] == automation_id
    assert [item["id"] for item in backup["subscription_rows"]] == [subscription_id]

    with session_factory() as session:
        bound_key = session.execute(
            text("SELECT bound_package_key FROM automation_agent_runtime_config WHERE id = :automation_id"),
            {"automation_id": automation_id},
        ).scalar_one()
        subscription_count = int(
            session.execute(
                text("SELECT COUNT(*) FROM ai_audience_outbound_subscription WHERE id = :subscription_id"),
                {"subscription_id": subscription_id},
            ).scalar_one()
        )
        post_report = inspect_automation_bindings(session.connection())
    assert bound_key == target_key
    assert subscription_count == 0
    assert post_report.ok is True

    repeated = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert repeated["status"] == "already_applied"
    assert repeated["database_write_executed"] is False


def test_e2e_subscription_residue_remediation_archives_exact_rows_and_is_idempotent(
    next_pg_schema,
    tmp_path,
) -> None:
    del next_pg_schema
    session_factory = get_session_factory()
    subscription_ids, packages = _seed_e2e_subscription_residue()
    with session_factory() as session:
        report = inspect_automation_bindings(session.connection())
    assert issue_kind_counts(report.issues) == {"orphan_subscription_package": 3}
    manifest = {
        "schema_version": 1,
        "operation_id": "pytest_e2e_subscription_residue",
        "remediation_action": "archive_e2e_subscription_residue",
        "expected_production_sha": PRODUCTION_SHA,
        "expected_issue_count": 3,
        "expected_issue_kind_counts": {"orphan_subscription_package": 3},
        "expected_issue_fingerprint": issue_fingerprint(report.issues),
        "expected_subscription_ids": subscription_ids,
        "expected_packages": packages,
        "expected_webhook_url": "https://www.youcangogogo.com/api/ai/audience/test-agent/webhook",
        "execute_confirmation": "EXECUTE_PYTEST_E2E_RESIDUE",
    }

    preview = run_remediation(
        manifest,
        mode="preview",
        confirmation="",
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert preview["status"] == "ready"
    assert preview["database_write_executed"] is False

    applied = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert applied["status"] == "applied"
    assert applied["database_write_executed"] is True
    assert applied["backup_created"] is True

    with session_factory() as session:
        statuses = (
            session.execute(
                text(
                    """
                SELECT status
                FROM ai_audience_outbound_subscription
                WHERE id = ANY(:subscription_ids)
                ORDER BY id
                """
                ),
                {"subscription_ids": subscription_ids},
            )
            .scalars()
            .all()
        )
        post_report = inspect_automation_bindings(session.connection())
    assert statuses == ["archived", "archived", "archived"]
    assert post_report.ok is True

    repeated = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=tmp_path,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert repeated["status"] == "already_applied"
    assert repeated["database_write_executed"] is False


def test_binding_history_remediation_rejects_release_or_issue_drift(next_pg_schema, tmp_path) -> None:
    del next_pg_schema
    session_factory = get_session_factory()
    automation_id, _subscription_id, _target_key = _seed_history_anomaly()
    with session_factory() as session:
        report = inspect_automation_bindings(session.connection())
    manifest = _manifest(report, automation_id)

    with pytest.raises(RemediationError, match="production_release_sha_changed"):
        run_remediation(
            manifest,
            mode="preview",
            confirmation="",
            backup_dir=tmp_path,
            current_release_sha="b" * 40,
            session_factory=session_factory,
        )

    manifest["expected_issue_fingerprint"] = "0" * 64
    with pytest.raises(RemediationError, match="unexpected_issue_envelope"):
        run_remediation(
            manifest,
            mode="preview",
            confirmation="",
            backup_dir=tmp_path,
            current_release_sha=PRODUCTION_SHA,
            session_factory=session_factory,
        )


def test_retired_binding_remediation_clears_only_legacy_binding_and_is_idempotent(next_pg_schema, tmp_path) -> None:
    del next_pg_schema
    session_factory = get_session_factory()
    automation_id, agent_code, package_key = _seed_retired_binding_anomaly()
    with session_factory() as session:
        report = inspect_automation_bindings(session.connection())
    assert issue_kind_counts(report.issues) == {
        "orphan_agent_binding": 1,
        "orphan_send_url": 1,
    }
    manifest = {
        **_manifest(report, automation_id),
        "remediation_action": "clear_retired_webhook_binding",
        "expected_agent_code": agent_code,
        "expected_package_key": package_key,
        "execute_confirmation": "EXECUTE_RETIRED_BINDING_PYTEST",
    }
    backup_dir = tmp_path / "retired-binding-backups"

    preview = run_remediation(
        manifest,
        mode="preview",
        confirmation="",
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert preview["status"] == "ready"
    assert preview["database_write_executed"] is False
    assert not backup_dir.exists()

    applied = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert applied["status"] == "applied"
    assert applied["database_write_executed"] is True
    assert applied["real_external_call_executed"] is False
    backup_path = backup_dir / Path(applied["backup_path"]).name
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup["automation_row"]["bound_package_key"] == package_key
    assert backup["automation_row"]["send_webhook_url"].endswith(f"/{package_key}/webhook")

    with session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                    SELECT agent_code, status, bound_package_key, send_webhook_url,
                           draft_role_prompt, draft_task_prompt
                    FROM automation_agent_runtime_config
                    WHERE id = :automation_id
                    """
                ),
                {"automation_id": automation_id},
            )
            .mappings()
            .one()
        )
        post_report = inspect_automation_bindings(session.connection())
    assert dict(row) == {
        "agent_code": agent_code,
        "status": "active",
        "bound_package_key": "",
        "send_webhook_url": "",
        "draft_role_prompt": "role",
        "draft_task_prompt": "task",
    }
    assert post_report.ok is True

    repeated = run_remediation(
        manifest,
        mode="apply",
        confirmation=manifest["execute_confirmation"],
        backup_dir=backup_dir,
        current_release_sha=PRODUCTION_SHA,
        session_factory=session_factory,
    )
    assert repeated["status"] == "already_applied"
    assert repeated["database_write_executed"] is False


def test_retired_binding_manifest_matches_observed_production_envelope() -> None:
    manifest = load_manifest(ROOT / "deploy/retired_ai_audience_binding_remediation.json")
    assert manifest["expected_production_sha"] == "fd03cc53b094044d9ae798aa214606155b925f22"
    assert manifest["expected_agent_code"] == "new_agent_1784189809857"
    assert manifest["expected_package_key"] == "new_package_key"
    assert manifest["expected_issue_kind_counts"] == {
        "orphan_agent_binding": 1,
        "orphan_send_url": 1,
    }
    assert manifest["expected_issue_fingerprint"] == ("32d134df09ee872d9a6a5bd21e56d4fdb3d18af376f790f3b63ed56cfbe6747e")


def test_e2e_residue_manifest_matches_observed_production_envelope() -> None:
    manifest = load_manifest(ROOT / "deploy/ai_audience_e2e_subscription_residue_20260804.json")
    assert manifest["expected_production_sha"] == "b290bada598c76a1e12bb2ade8c020dcfd8849e5"
    assert manifest["expected_issue_kind_counts"] == {"orphan_subscription_package": 3}
    assert manifest["expected_issue_fingerprint"] == ("695f12cfd9b7e82313d51fc5ab0443dae8337e1cbd45893b08ab54176a56de30")


def test_production_deploy_reconciles_exact_history_before_binding_precheck_and_runtime_stop() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    history_manifest = json.loads((ROOT / "deploy/ai_audience_binding_history_remediation.json").read_text(encoding="utf-8"))
    retired_manifest = json.loads((ROOT / "deploy/retired_ai_audience_binding_remediation.json").read_text(encoding="utf-8"))

    remediation_index = workflow.index("- name: Reconcile authorized AI Audience binding history")
    preview_index = workflow.index("--mode preview", remediation_index)
    apply_index = workflow.index("--mode apply", preview_index)
    binding_precheck_index = workflow.index(
        "python3 -m scripts.ops.validate_production_deployment_profile --binding-check",
        apply_index,
    )
    identity_preflight_index = workflow.index(
        "# Identity preflight must fail before any runtime unit is stopped.",
        binding_precheck_index,
    )
    runtime_stop_index = workflow.index("--phase stop-for-migration --execute", identity_preflight_index)

    assert remediation_index < preview_index < apply_index < binding_precheck_index
    assert binding_precheck_index < identity_preflight_index < runtime_stop_index
    assert "3b045574ef1b5b322716ee7a62aa86fd3507df43" in workflow[remediation_index:preview_index]
    assert "fd03cc53b094044d9ae798aa214606155b925f22" in workflow[remediation_index:preview_index]
    assert '--current-release-sha "$remediation_base_sha"' in workflow[remediation_index:apply_index]
    assert "EXECUTE_AI_AUDIENCE_BINDING_HISTORY_20260801" in workflow[remediation_index:binding_precheck_index]
    assert "EXECUTE_RETIRED_AI_AUDIENCE_BINDING_20260802" in workflow[remediation_index:binding_precheck_index]
    assert "b290bada598c76a1e12bb2ade8c020dcfd8849e5" in workflow[remediation_index:preview_index]
    assert "EXECUTE_AI_AUDIENCE_E2E_RESIDUE_20260804" in workflow[remediation_index:binding_precheck_index]
    assert history_manifest["expected_production_sha"] == "3b045574ef1b5b322716ee7a62aa86fd3507df43"
    assert history_manifest["expected_issue_fingerprint"] == ("c6458353624dd7a2f193ecbe6799de1e518d6cdad270c1867f0cf38f3e2ed57b")
    assert retired_manifest["expected_production_sha"] == "fd03cc53b094044d9ae798aa214606155b925f22"
