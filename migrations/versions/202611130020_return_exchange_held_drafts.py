"""Use Held Bills records for Return Exchange drafts and link settlements."""
from alembic import op
import sqlalchemy as sa

revision = "202611130020"
down_revision = "202611130019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "held_sales",
        sa.Column("draft_type", sa.String(24), nullable=False, server_default="sales_bill"),
    )
    op.add_column("held_sales", sa.Column("return_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_held_sales_return_item",
        "held_sales",
        "return_items",
        ["return_item_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_held_sales_return_item", "held_sales", ["return_item_id"])
    op.alter_column("held_sales", "draft_type", existing_type=sa.String(24), server_default=None)
    op.add_column("return_settlements", sa.Column("exchange_ref", sa.String(40), nullable=True))


def downgrade():
    raise RuntimeError("Return Exchange drafts and linked settlement history are retained.")
