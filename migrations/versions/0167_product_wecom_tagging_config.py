"""product configurable WeCom tagging.

Revision ID: 0167_product_wecom_tagging_config
Revises: 0166_auth_api_client_credential_hint
"""
from __future__ import annotations

from alembic import op


revision = "0167_product_wecom_tagging_config"
down_revision = "0166_auth_api_client_credential_hint"
branch_labels = None
depends_on = None


_LEGACY_PRODUCT_CODES = (
    "prd_20260707050545_291025",
    "prd_20260713083438_75670b",
)
_LEGACY_TAG_ID = "etbNXyCwAAUZm79s_QWeVnr3fktQn0mg"
_LEGACY_OWNER_USERID = "HuangYouCan"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE wechat_pay_products
        ADD COLUMN IF NOT EXISTS wecom_tagging_json JSONB NOT NULL DEFAULT '{"enabled": false, "tag_ids": [], "owner_userid": ""}'::jsonb
        """
    )
    op.execute(
        f"""
        UPDATE wechat_pay_products
        SET wecom_tagging_json = jsonb_build_object(
                'enabled', true,
                'tag_ids', jsonb_build_array('{_LEGACY_TAG_ID}'),
                'owner_userid', '{_LEGACY_OWNER_USERID}'
            ),
            updated_at = CURRENT_TIMESTAMP
        WHERE product_code IN ({', '.join(repr(code) for code in _LEGACY_PRODUCT_CODES)})
          AND COALESCE(wecom_tagging_json, '{{}}'::jsonb) = '{{"enabled": false, "tag_ids": [], "owner_userid": ""}}'::jsonb
        """
    )
    op.execute(
        """
        ALTER TABLE wechat_pay_products
        DROP CONSTRAINT IF EXISTS ck_wechat_pay_products_wecom_tagging_object
        """
    )
    op.execute(
        """
        ALTER TABLE wechat_pay_products
        ADD CONSTRAINT ck_wechat_pay_products_wecom_tagging_object
        CHECK (jsonb_typeof(wecom_tagging_json) = 'object')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE wechat_pay_products
        DROP CONSTRAINT IF EXISTS ck_wechat_pay_products_wecom_tagging_object
        """
    )
    op.execute("ALTER TABLE wechat_pay_products DROP COLUMN IF EXISTS wecom_tagging_json")
