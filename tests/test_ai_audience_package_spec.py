from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.package_spec import parse_markdown_spec, validate_spec
from aicrm_next.shared.db_session import get_session_factory
from scripts.ai_audience_apply_package_spec import apply_spec


VALID_SPEC = """---
package_key: spec_q101
name: Spec 问卷包
status: paused
query_mode: incremental_event
identity_policy: external_userid
refresh_mode: incremental_3m
natural_language_definition: 提交问卷且已加微。
parameters:
  questionnaire_id: 101
senders:
  - sender_userid: HuangYouCan
    display_name: HuangYouCan
    priority: 1
    status: active
---

# 业务说明

测试 spec。

# Incremental SQL

```sql
SELECT
  'external_userid' AS identity_type,
  qs.external_userid AS identity_value,
  'questionnaire_submission:' || qs.submission_id::text AS event_source_key,
  jsonb_build_object('questionnaire_id', qs.questionnaire_id) AS payload_json,
  qs.external_userid,
  qs.submitted_at AS event_at
FROM audience_read.questionnaire_submissions_v1 qs
JOIN audience_read.wecom_contacts_v1 wc ON wc.external_userid = qs.external_userid
WHERE qs.questionnaire_id = :questionnaire_id
  AND qs.submitted_at >= :last_watermark_at
  AND qs.submitted_at < :refresh_started_at
```
"""


def _write_spec(tmp_path: Path, text_value: str, name: str = "package.md") -> Path:
    path = tmp_path / name
    path.write_text(text_value, encoding="utf-8")
    return path


def test_ai_audience_package_spec_parse_valid_example() -> None:
    spec = parse_markdown_spec("docs/ai_audience/examples/questionnaire_submitted_added_wecom.md")

    errors, warnings = validate_spec(spec)

    assert spec.package_key == "q101_submitted_added_wecom"
    assert "audience_read.questionnaire_submissions_v1" in spec.incremental_sql
    assert errors == []
    assert warnings == []


def test_ai_audience_package_spec_parse_group_chat_members_manual_example() -> None:
    spec = parse_markdown_spec("docs/ai_audience/examples/group_chat_members_manual.md")

    errors, warnings = validate_spec(spec)

    assert spec.package_key == "group_chat_members_manual"
    assert spec.frontmatter["refresh_mode"] == "manual"
    assert "audience_read.group_chat_members_v1" in spec.snapshot_sql
    assert errors == []
    assert warnings == []


def test_ai_audience_package_spec_invalid_refresh_mode_fails(tmp_path) -> None:
    spec = parse_markdown_spec(_write_spec(tmp_path, VALID_SPEC.replace("refresh_mode: incremental_3m", "refresh_mode: incremental_5m")))

    errors, _warnings = validate_spec(spec)

    assert "invalid_refresh_mode" in errors


def test_ai_audience_package_spec_missing_required_sql_fails(tmp_path) -> None:
    spec = parse_markdown_spec(_write_spec(tmp_path, VALID_SPEC.replace("refresh_mode: incremental_3m", "refresh_mode: daily_0200")))

    errors, _warnings = validate_spec(spec)

    assert "snapshot_sql_required" in errors


def test_ai_audience_package_spec_invalid_sql_fails(tmp_path) -> None:
    broken = VALID_SPEC.replace("FROM audience_read.questionnaire_submissions_v1 qs", "FROM public.users qs").replace(
        "SELECT\n  'external_userid' AS identity_type,",
        "SELECT\n  *,\n  'external_userid' AS identity_type,",
    )
    spec = parse_markdown_spec(_write_spec(tmp_path, broken))

    errors, _warnings = validate_spec(spec)

    assert "incremental:select_star_forbidden" in errors
    assert "incremental:dependency_not_allowed:public.users" in errors


def test_ai_audience_package_spec_allows_system_params_but_requires_business_params(tmp_path) -> None:
    system_only = VALID_SPEC.replace("  AND qs.submitted_at < :refresh_started_at", "  AND qs.submitted_at < :refresh_started_at\n  AND :lookback_seconds >= 0\n  AND :package_id >= 0")
    spec = parse_markdown_spec(_write_spec(tmp_path, system_only))

    errors, _warnings = validate_spec(spec)

    assert not any("last_watermark_at" in item or "refresh_started_at" in item or "lookback_seconds" in item or "package_id" in item for item in errors)

    missing_business_param = system_only.replace("parameters:\n  questionnaire_id: 101", "parameters: {}")
    spec = parse_markdown_spec(_write_spec(tmp_path, missing_business_param, name="missing_business_param.md"))

    errors, _warnings = validate_spec(spec)

    assert "incremental:parameter_not_declared:questionnaire_id" in errors


def test_ai_audience_package_spec_dry_run_does_not_write_db(tmp_path, next_pg_schema) -> None:
    del next_pg_schema
    spec = parse_markdown_spec(_write_spec(tmp_path, VALID_SPEC))

    report = apply_spec(spec, apply=False)

    assert report["ok"] is True
    assert report["package_id"] is None
    with get_session_factory()() as session:
        count = session.execute(text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = 'spec_q101'")).scalar_one()
    assert count == 0


def test_ai_audience_package_spec_apply_update_and_publish(tmp_path, next_pg_schema) -> None:
    del next_pg_schema
    spec = parse_markdown_spec(_write_spec(tmp_path, VALID_SPEC))

    created = apply_spec(spec, apply=True)

    assert created["ok"] is True
    assert created["created"] is True
    assert created["package_id"]
    assert created["version_id"]
    with get_session_factory()() as session:
        package = session.execute(text("SELECT status, incremental_enabled, incremental_interval_seconds FROM ai_audience_package WHERE package_key = 'spec_q101'")).mappings().one()
        version_rows = session.execute(text("SELECT version_number, parameters_json FROM ai_audience_package_version WHERE package_id = :package_id ORDER BY version_number"), {"package_id": created["package_id"]}).mappings().all()
    assert package["status"] == "paused"
    assert package["incremental_enabled"] is True
    assert package["incremental_interval_seconds"] == 180
    assert version_rows[0]["parameters_json"] == {"questionnaire_id": 101}

    updated_spec = parse_markdown_spec(_write_spec(tmp_path, VALID_SPEC.replace("questionnaire_id: 101", "questionnaire_id: 202"), name="package_v2.md"))
    updated = apply_spec(updated_spec, apply=True, publish=True)

    assert updated["ok"] is True
    assert updated["updated"] is True
    assert updated["published"] is True
    with get_session_factory()() as session:
        versions = session.execute(text("SELECT version_number, status, parameters_json FROM ai_audience_package_version WHERE package_id = :package_id ORDER BY version_number"), {"package_id": created["package_id"]}).mappings().all()
        package_status = session.execute(text("SELECT status, current_version_id FROM ai_audience_package WHERE id = :package_id"), {"package_id": created["package_id"]}).mappings().one()
    assert [row["version_number"] for row in versions] == [1, 2]
    assert versions[-1]["status"] == "published"
    assert versions[-1]["parameters_json"] == {"questionnaire_id": 202}
    assert package_status["status"] == "active"
