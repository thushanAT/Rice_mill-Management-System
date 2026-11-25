"""Add append-only payment reversals and customer balance adjustments.

The new tables reference original financial records and do not alter or remove
existing invoices, payments, allocations, or return settlement data.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611090009"
down_revision = "202611080008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_reversals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reversal_no", sa.String(60), nullable=False),
        sa.Column(
            "original_payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("original_payment_id", name="uq_payment_reversals_original_payment"),
        sa.UniqueConstraint("reversal_no", name="uq_payment_reversals_reversal_no"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_reversals_idempotency_key"),
        sa.CheckConstraint("amount > 0", name="ck_payment_reversals_positive_amount"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "payment_reversal_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reversal_id",
            sa.Integer(),
            sa.ForeignKey("payment_reversals.id"),
            nullable=False,
        ),
        sa.Column(
            "original_allocation_id",
            sa.Integer(),
            sa.ForeignKey("payment_allocations.id"),
            nullable=False,
        ),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.UniqueConstraint(
            "original_allocation_id",
            name="uq_payment_reversal_allocations_original",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_reversal_allocations_positive_amount"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "customer_balance_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_customer_balance_adjustments_idempotency_key",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_customer_balance_adjustments_nonzero_amount"),
        mysql_engine="InnoDB",
    )


def downgrade():
    raise RuntimeError("Financial correction history is append-only; this migration cannot be downgraded.")
