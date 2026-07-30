"""Repair the canonical WeCom contacts audience read view.

Revision ID: 0160_ai_audience_wecom_contacts_view_repair
Revises: 0159_ai_automation_lane_rollout_audit
"""

from __future__ import annotations

from importlib import import_module


revision = "0160_ai_audience_wecom_contacts_view_repair"
down_revision = "0159_ai_automation_lane_rollout_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reuse the canonical, schema-tolerant definition instead of introducing a
    # second view contract. This also repairs cutover/restored databases whose
    # Alembic revision was preserved while non-materialized views were omitted.
    audience_migration = import_module(
        "migrations.versions.0057_huangyoucan_unregistered_ai_audience"
    )
    audience_migration._refresh_wecom_contacts_view()


def downgrade() -> None:
    # The view predates this repair migration. A release rollback must not
    # remove a pre-existing shared read contract.
    pass
