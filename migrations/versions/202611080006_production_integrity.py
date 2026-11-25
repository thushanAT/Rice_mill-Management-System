"""Add production idempotency, movement links, and packaging remainder.

All added columns are nullable or have a zero default so existing production
and movement rows remain intact and are not merged or rewritten.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611080006"
down_revision = "202611080005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "production_records",
        sa.Column("remaining_kg", sa.Numeric(16, 3), nullable=False, server_default="0"),
    )
    op.add_column("production_records", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column(
        "production_records",
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_production_records_idempotency_key",
        "production_records",
        ["idempotency_key"],
    )
    op.add_column("stock_movements", sa.Column("production_record_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_stock_movements_production_record_id",
        "stock_movements",
        ["production_record_id"],
    )
    op.create_foreign_key(
        "fk_stock_movements_production_record_id_production_records",
        "stock_movements",
        "production_records",
        ["production_record_id"],
        ["id"],
    )


def downgrade():
    raise RuntimeError("Production integrity data is retained; this migration cannot be downgraded safely.")
