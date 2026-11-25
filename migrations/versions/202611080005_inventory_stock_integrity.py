"""Guarantee normalized stock keys and idempotent inventory movements.

Generated key columns close MySQL's multiple-NULL unique-index gap without
changing or merging any existing stock or movement rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611080005"
down_revision = "202611080004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stocks",
        sa.Column(
            "variety_key",
            sa.String(length=80),
            sa.Computed("COALESCE(`variety`, '')", persisted=True),
            nullable=True,
        ),
    )
    op.add_column(
        "stocks",
        sa.Column(
            "package_size_key",
            sa.Integer(),
            sa.Computed("COALESCE(`package_size_kg`, 0)", persisted=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_stocks_logical_key",
        "stocks",
        ["item_name", "stock_type", "variety_key", "package_size_key"],
    )
    op.add_column(
        "stock_movements",
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
    )
    op.create_unique_constraint(
        "uq_stock_movements_idempotency_key",
        "stock_movements",
        ["idempotency_key"],
    )


def downgrade():
    raise RuntimeError("Inventory integrity constraints are retained; this migration cannot be downgraded safely.")
