from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from scripts.ops.check_ai_audience_send_records_release import (
    AUTOMATION_INDEX,
    EXPECTED_MIGRATION_HEAD,
    MANUAL_INDEX,
    _database_url,
    collect,
    redact_report,
)


ROOT = Path(__file__).resolve().parents[1]


def test_post_migration_release_check_is_read_only_and_uses_both_indexes(next_pg_schema) -> None:
    del next_pg_schema
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = collect(conn, phase="post-migration", release_sha="a" * 40)

    assert report["ok"] is True
    assert report["migration_heads"] == [EXPECTED_MIGRATION_HEAD]
    assert report["database_write_executed"] is False
    assert report["real_external_call_executed"] is False
    assert report["pii_included"] is False
    assert report["indexes"] == {
        AUTOMATION_INDEX: {"valid": True, "ready": True},
        MANUAL_INDEX: {"valid": True, "ready": True},
    }
    assert MANUAL_INDEX in report["query_plans"]["manual"]["index_names"]
    assert AUTOMATION_INDEX in report["query_plans"]["automation"]["index_names"]
    assert set(report["counts"]) == {
        "automation_traceable_business_record_count",
        "legacy_manual_record_without_package_ownership_count",
        "traceable_manual_business_record_count",
    }


def test_release_report_whitelists_only_count_and_plan_evidence() -> None:
    rendered = redact_report(
        {
            "ok": False,
            "phase": "preflight",
            "release_sha": "b" * 40,
            "error_type": "OperationalError",
            "counts": {"safe_count": 3},
            "query_plans": {},
            "nickname": "不应输出的联系人",
            "external_userid": "wm_secret",
            "content_text": "不应输出的话术",
        }
    )
    payload = json.loads(rendered)

    assert payload["counts"] == {"safe_count": 3}
    assert payload["error_type"] == "OperationalError"
    assert payload["pii_included"] is False
    assert "不应输出" not in rendered
    assert "wm_secret" not in rendered


def test_production_deploy_runs_send_record_checks_around_migration() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    script_call = "python3 scripts/ops/check_ai_audience_send_records_release.py"
    migration = "python3 -m alembic upgrade head"
    preflight_position = workflow.index(script_call)
    post_migration_position = workflow.index(script_call, preflight_position + 1)

    assert "--phase preflight" in workflow[preflight_position:post_migration_position]
    assert "--phase post-migration" in workflow[post_migration_position:]
    assert preflight_position < workflow.index("runtime_mutation_started=1")
    assert workflow.index(migration) < post_migration_position
    assert "SET TRANSACTION READ ONLY" in (
        ROOT / "scripts/ops/check_ai_audience_send_records_release.py"
    ).read_text(encoding="utf-8")
