"""Add return approval, inspection, settlement, and combined document storage.

All existing business tables and rows are retained. New nullable approval
fields leave historic returns unapproved until an Admin reviews them.
"""

from alembic import op
import sqlalchemy as sa

revision = "202611080003"
down_revision = "202611080002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sales_returns", sa.Column("approval_status", sa.String(32), nullable=True))
    op.add_column("sales_returns", sa.Column("approved_by", sa.Integer(), nullable=True))
    op.add_column("sales_returns", sa.Column("approved_at", sa.DateTime(), nullable=True))
    op.add_column("sales_returns", sa.Column("approval_notes", sa.Text(), nullable=True))
    op.add_column("sales_returns", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.create_unique_constraint(
        "uq_sales_returns_idempotency_key",
        "sales_returns",
        ["idempotency_key"],
    )
    op.create_foreign_key(
        "fk_sales_returns_approved_by_users",
        "sales_returns",
        "users",
        ["approved_by"],
        ["id"],
    )

    op.add_column("return_items", sa.Column("inspected_by", sa.Integer(), nullable=True))
    op.add_column("return_items", sa.Column("inspected_at", sa.DateTime(), nullable=True))
    op.add_column(
        "return_items",
        sa.Column("settlement_status", sa.String(24), nullable=False, server_default="unsettled"),
    )
    op.create_foreign_key(
        "fk_return_items_inspected_by_users",
        "return_items",
        "users",
        ["inspected_by"],
        ["id"],
    )

    op.add_column("stock_movements", sa.Column("return_item_id", sa.Integer(), nullable=True))
    op.create_unique_constraint(
        "uq_stock_movements_return_item_id",
        "stock_movements",
        ["return_item_id"],
    )
    op.create_foreign_key(
        "fk_stock_movements_return_item",
        "stock_movements",
        "return_items",
        ["return_item_id"],
        ["id"],
    )

    op.create_table(
        "return_approval_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("return_id", sa.Integer(), sa.ForeignKey("sales_returns.id"), nullable=False),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_return_approval_events_return_id", "return_approval_events", ["return_id"])

    op.create_table(
        "return_inspection_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("return_item_id", sa.Integer(), sa.ForeignKey("return_items.id"), nullable=False),
        sa.Column("inspector_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_return_inspection_events_item_id",
        "return_inspection_events",
        ["return_item_id"],
    )

    op.create_table(
        "return_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("return_item_id", sa.Integer(), sa.ForeignKey("return_items.id"), nullable=False),
        sa.Column("settlement_type", sa.String(20), nullable=False),
        sa.Column("refund_option", sa.String(32), nullable=True),
        sa.Column("return_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("replacement_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("balance_applied", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("cash_refund", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("amount_collected", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("difference_refund", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("return_item_id", name="uq_return_settlements_return_item"),
        mysql_engine="InnoDB",
    )

    op.create_table(
        "return_replacement_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "settlement_id",
            sa.Integer(),
            sa.ForeignKey("return_settlements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("product_name", sa.String(140), nullable=False),
        sa.Column("variety", sa.String(120), nullable=True),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("package_size_kg", sa.Integer(), nullable=True),
        sa.Column("total_kg", sa.Numeric(14, 3), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("manual_price_reason", sa.Text(), nullable=True),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_return_replacement_items_settlement_id",
        "return_replacement_items",
        ["settlement_id"],
    )

    op.create_table(
        "return_financial_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "settlement_id",
            sa.Integer(),
            sa.ForeignKey("return_settlements.id"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("method", sa.String(30), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_return_financial_entries_settlement_id",
        "return_financial_entries",
        ["settlement_id"],
    )

    op.create_table(
        "combined_transaction_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_no", sa.String(60), nullable=False, unique=True),
        sa.Column("return_id", sa.Integer(), sa.ForeignKey("sales_returns.id"), nullable=False),
        sa.Column(
            "settlement_id",
            sa.Integer(),
            sa.ForeignKey("return_settlements.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_combined_documents_return_id",
        "combined_transaction_documents",
        ["return_id"],
    )


def downgrade():
    raise RuntimeError("Return approval and settlement history is retained; this migration is intentionally non-destructive and cannot be downgraded.")
