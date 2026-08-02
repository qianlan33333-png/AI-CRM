"""Persist a safe masked identifier for API client credentials.

Revision ID: 0166_auth_api_client_credential_hint
Revises: 0165_ai_audience_template_registry
"""

from __future__ import annotations

from alembic import op


revision = "0166_auth_api_client_credential_hint"
down_revision = "0165_ai_audience_template_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE auth_api_clients "
        "ADD COLUMN IF NOT EXISTS credential_hint TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    # The hint is non-secret, additive metadata. Keep it so compiled releases and
    # audit-visible status pages remain readable during a release rollback.
    pass
