"""Separate financial closeout from physical return inspection."""
from alembic import op
import sqlalchemy as sa

revision = "202611130021"
down_revision = "202611130020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sales_returns",
        sa.Column(
            "financial_status",
            sa.String(32),
            nullable=False,
            server_default="pending_settlement",
        ),
    )
    op.execute(sa.text("""
        UPDATE sales_returns r
        SET financial_status = CASE
            WHEN r.approval_status='rejected' THEN 'rejected'
            WHEN r.approval_status='cancelled' THEN 'cancelled'
            WHEN NOT EXISTS (SELECT 1 FROM return_items i WHERE i.return_id=r.id AND i.settlement_status<>'settled')
                AND EXISTS (SELECT 1 FROM return_items i WHERE i.return_id=r.id)
                THEN 'settled'
            WHEN EXISTS (SELECT 1 FROM return_items i WHERE i.return_id=r.id AND i.settlement_status='settled')
                THEN 'partially_settled'
            ELSE 'pending_settlement'
        END
    """))
    op.alter_column(
        "sales_returns",
        "financial_status",
        existing_type=sa.String(32),
        server_default=None,
    )


def downgrade():
    raise RuntimeError("Financial closeout history is retained; this additive migration cannot be downgraded safely.")
