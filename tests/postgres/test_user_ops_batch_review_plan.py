from __future__ import annotations

from uuid import uuid4

import psycopg

from aicrm_next.extensions.growth.cloud_orchestrator.repository import PostgresCloudPlanRepository
from aicrm_next.extensions.growth.cloud_orchestrator.review_plans import create_ai_assist_batch_review_plan


def test_postgres_batch_review_plan_conserves_recipients_and_creates_no_jobs(
    migrated_database_url: str,
) -> None:
    event_id = f"user_ops_batch_send:postgres:{uuid4().hex}"
    repository = PostgresCloudPlanRepository(migrated_database_url)
    payload = {
        "external_event_id": event_id,
        "package_key": "ai_audience_package:14",
        "operator": "pytest",
        "display_name": "AI 人群包群发审批 · 2 人",
        "content_package": {"content_text": "测试审批话术"},
        "recipients": [
            {"unionid": f"union_{uuid4().hex}", "owner_userid": "HuangYouCan", "customer_name": "测试 A"},
            {"unionid": f"union_{uuid4().hex}", "owner_userid": "HuangYouCan", "customer_name": "测试 B"},
        ],
    }

    created = create_ai_assist_batch_review_plan(payload, repository=repository)
    reused = create_ai_assist_batch_review_plan(payload, repository=repository)
    plan_id = created["plan_id"]
    try:
        assert created["status"] == "created"
        assert reused["status"] == "reused"
        assert created["review_status"] == "pending_review"
        assert created["run_status"] == "draft"
        assert created["recipient_count"] == 2
        assert created["message_count"] == 2
        assert created["broadcast_job_count"] == 0
        with psycopg.connect(migrated_database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT plan.review_status, plan.run_status,
                           (SELECT COUNT(*) FROM cloud_broadcast_plan_recipients WHERE plan_id = plan.plan_id),
                           (SELECT COUNT(*) FROM cloud_broadcast_plan_recipient_messages WHERE plan_id = plan.plan_id),
                           (SELECT COUNT(*) FROM broadcast_jobs WHERE source_id LIKE %s)
                    FROM cloud_broadcast_plans plan
                    WHERE plan.plan_id = %s
                    """,
                    (f"{plan_id}:%", plan_id),
                )
                assert cursor.fetchone() == ("pending_review", "draft", 2, 2, 0)
    finally:
        with psycopg.connect(migrated_database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM cloud_broadcast_plans WHERE plan_id = %s", (plan_id,))
            connection.commit()
