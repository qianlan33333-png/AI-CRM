"""Allow audited production all-scope queue cutover and soak evidence.

Revision ID: 0137_queue_production_scope_cutover
Revises: 0136_queue_runtime_validation_soak
"""

from __future__ import annotations

from alembic import op


revision = "0137_queue_production_scope_cutover"
down_revision = "0136_queue_runtime_validation_soak"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE queue_runtime_scope_transition_audit
        DROP CONSTRAINT IF EXISTS ck_queue_scope_transition_direction
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_scope_transition_audit
        ADD CONSTRAINT ck_queue_scope_transition_direction CHECK (
            from_scope IN ('test_loopback', 'allowlisted', 'all')
            AND to_scope IN ('test_loopback', 'allowlisted', 'all')
            AND from_scope <> to_scope
        )
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_soak_run
        DROP CONSTRAINT IF EXISTS queue_runtime_soak_run_external_claim_scope_check
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_soak_run
        ADD CONSTRAINT queue_runtime_soak_run_external_claim_scope_check
        CHECK (external_claim_scope IN ('allowlisted', 'all'))
        """
    )


def downgrade() -> None:
    # Production rollback retains append-only all-scope transition and soak evidence.
    pass
