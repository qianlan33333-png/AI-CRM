from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, MetaData, String, Table, Text
from sqlalchemy.types import JSON

from aicrm_next.platform.shared.database import Base

metadata: MetaData = Base.metadata


user_ops_pool_current_next = Table(
    "user_ops_pool_current_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("unionid", String(128), nullable=False),
    Column("customer_name_snapshot", String(255), nullable=False, default=""),
    Column("owner_userid", String(128), nullable=True),
    Column("owner_display_name", String(255), nullable=False, default=""),
    Column("class_term_no", String(80), nullable=True),
    Column("class_term_label", String(255), nullable=False, default=""),
    Column("source_type", String(80), nullable=False, default="lead_pool"),
    Column("activation_bucket", String(40), nullable=False, default="pending_input"),
    Column("activation_bucket_label", String(80), nullable=False, default="激活待录入"),
    Column("auto_do_not_disturb_reasons_json", JSON, nullable=False, default=list),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_user_ops_pool_current_next_unionid", "unionid"),
    Index("ix_user_ops_pool_current_next_owner_userid", "owner_userid"),
    Index("ix_user_ops_pool_current_next_class_term_no", "class_term_no"),
    Index("ix_user_ops_pool_current_next_activation_bucket", "activation_bucket"),
)

user_ops_do_not_disturb_next = Table(
    "user_ops_do_not_disturb_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("unionid", String(128), nullable=False),
    Column("source_type", String(40), nullable=False, default="manual"),
    Column("reason_code", String(80), nullable=False, default="manual_set"),
    Column("reason_text", Text, nullable=False, default="运营设置"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_by", String(128), nullable=False, default="fixture-admin"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_user_ops_dnd_next_unionid", "unionid"),
    Index("ix_user_ops_dnd_next_active_reason", "is_active", "reason_code"),
)

user_ops_send_records_next = Table(
    "user_ops_send_records_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("record_key", String(80), nullable=False, unique=True),
    Column("idempotency_key", Text, nullable=True),
    Column("task_type", String(80), nullable=False, default="user_ops_batch_send"),
    Column("execution_backend", String(40), nullable=False, default="legacy_fake"),
    Column("target_unionids_json", JSON, nullable=False, default=list),
    Column("outbound_task_ids_json", JSON, nullable=False, default=list),
    Column("task_results_json", JSON, nullable=False, default=list),
    Column("external_effect_job_ids_json", JSON, nullable=False, default=list),
    Column("external_effect_status_summary_json", JSON, nullable=False, default=dict),
    Column("selected_count", Integer, nullable=False, default=0),
    Column("eligible_count", Integer, nullable=False, default=0),
    Column("sent_count", Integer, nullable=False, default=0),
    Column("skipped_count", Integer, nullable=False, default=0),
    Column("planned_count", Integer, nullable=False, default=0),
    Column("queued_count", Integer, nullable=False, default=0),
    Column("dispatching_count", Integer, nullable=False, default=0),
    Column("succeeded_count", Integer, nullable=False, default=0),
    Column("failed_count", Integer, nullable=False, default=0),
    Column("blocked_count", Integer, nullable=False, default=0),
    Column("cancelled_count", Integer, nullable=False, default=0),
    Column("skipped_reasons_json", JSON, nullable=False, default=dict),
    Column("include_do_not_disturb", Boolean, nullable=False, default=False),
    Column("content_preview", Text, nullable=False, default=""),
    Column("image_count", Integer, nullable=False, default=0),
    Column("sender_userids_json", JSON, nullable=False, default=list),
    Column("filter_snapshot_json", JSON, nullable=False, default=dict),
    Column("operator", String(128), nullable=False, default="fixture-admin"),
    Column("status", String(40), nullable=False, default="created"),
    Column("status_label", String(80), nullable=False, default="已创建任务"),
    Column("last_status_sync_at", DateTime(timezone=True), nullable=True),
    Column("last_refreshed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_user_ops_send_records_next_created_at", "created_at"),
    Index("ix_user_ops_send_records_next_status", "status"),
    Index("ix_user_ops_send_records_next_idempotency_key", "idempotency_key"),
)
