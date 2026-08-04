"""Add per-owner lead QR title and subtitle.

Revision ID: 0168_lead_qr_copy_config
Revises: 0167_product_wecom_tagging_config
"""
from __future__ import annotations

from alembic import op


revision = "0168_lead_qr_copy_config"
down_revision = "0167_product_wecom_tagging_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("wechat_pay_products", "questionnaires"):
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS lead_qr_title TEXT NOT NULL DEFAULT ''
            """
        )
        op.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS lead_qr_subtitle TEXT NOT NULL DEFAULT ''
            """
        )


def downgrade() -> None:
    for table_name in ("questionnaires", "wechat_pay_products"):
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS lead_qr_subtitle")
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS lead_qr_title")
