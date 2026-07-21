from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, MetaData, String, Table, Text, UniqueConstraint
from sqlalchemy.types import JSON

from aicrm_next.shared.database import Base

metadata: MetaData = Base.metadata


customer_list_index_next = Table(
    "customer_list_index_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("unionid", String(128), nullable=False, default=""),
    Column("customer_name", String(255), nullable=False, default=""),
    Column("owner_userid", String(128), nullable=False, default=""),
    Column("owner_display_name", String(255), nullable=False, default=""),
    Column("remark", String(255), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("mobile", String(32), nullable=True),
    Column("is_bound", Boolean, nullable=False, default=False),
    Column("binding_status", String(80), nullable=False, default="unbound"),
    Column("tags_json", JSON, nullable=False, default=list),
    Column("class_user_status_json", JSON, nullable=False, default=dict),
    Column("last_message_at", DateTime(timezone=True), nullable=True),
    Column("last_touch_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_customer_list_index_next_unionid", "unionid"),
    Index("ix_customer_list_index_next_owner_userid", "owner_userid"),
    Index("ix_customer_list_index_next_mobile", "mobile"),
    Index("ix_customer_list_index_next_updated_at", "updated_at"),
)

customer_detail_snapshot_next = Table(
    "customer_detail_snapshot_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("unionid", String(128), nullable=False, default=""),
    Column("customer_json", JSON, nullable=False, default=dict),
    Column("binding_json", JSON, nullable=False, default=dict),
    Column("identity_json", JSON, nullable=False, default=dict),
    Column("follow_users_json", JSON, nullable=False, default=list),
    Column("marketing_summary_json", JSON, nullable=False, default=dict),
    Column("marketing_profile_json", JSON, nullable=False, default=dict),
    Column("contact_json", JSON, nullable=False, default=dict),
    Column("sidebar_context_json", JSON, nullable=False, default=dict),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_customer_detail_snapshot_next_unionid", "unionid"),
)

customer_timeline_event_next = Table(
    "customer_timeline_event_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(128), nullable=False),
    Column("unionid", String(128), nullable=False, default=""),
    Column("event_type", String(80), nullable=False),
    Column("event_time", DateTime(timezone=True), nullable=False),
    Column("title", String(255), nullable=False, default=""),
    Column("summary", Text, nullable=False, default=""),
    Column("source_table", String(128), nullable=False, default=""),
    Column("source_id", String(128), nullable=False, default=""),
    Column("metadata_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", name="uq_customer_timeline_event_next_event_id"),
    Index("ix_customer_timeline_event_next_unionid", "unionid"),
    Index("ix_customer_timeline_event_next_event_type", "event_type"),
    Index("ix_customer_timeline_event_next_event_time", "event_time"),
)

Index(
    "ix_customer_timeline_event_next_unionid_time_id",
    customer_timeline_event_next.c.unionid,
    customer_timeline_event_next.c.event_time.desc(),
    customer_timeline_event_next.c.id.desc(),
)

customer_recent_message_next = Table(
    "customer_recent_message_next",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("msgid", String(128), nullable=False),
    Column("unionid", String(128), nullable=False, default=""),
    Column("msgtype", String(40), nullable=False, default="text"),
    Column("content", Text, nullable=False, default=""),
    Column("send_time", DateTime(timezone=True), nullable=False),
    Column("owner_userid", String(128), nullable=True),
    Column("chat_type", String(40), nullable=False, default="single"),
    Column("metadata_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_customer_recent_message_next_unionid", "unionid"),
    Index("ix_customer_recent_message_next_send_time", "send_time"),
)
