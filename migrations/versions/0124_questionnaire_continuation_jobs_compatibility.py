"""Preserve the retired Siyuan questionnaire continuation revision marker.

The Siyuan production database was upgraded through the historical
``0124_questionnaire_continuation_jobs`` revision before that capability was
retired from the cumulative AI-CRM main line.  The table and columns created by
that historical migration are no longer owned by the current application, so
fresh databases must not recreate them.  Keeping its revision ID as a no-op
ancestor lets Alembic advance both that production database and fresh installs
through the current linear chain.

Revision ID: 0124_questionnaire_continuation_jobs
Revises: 0123_required_physical_schema_repair
"""

from __future__ import annotations


revision = "0124_questionnaire_continuation_jobs"
down_revision = "0123_required_physical_schema_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
