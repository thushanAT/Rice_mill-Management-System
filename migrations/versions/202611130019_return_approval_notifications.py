"""Persist Admin decisions for the original Return Bill requester."""
from alembic import op
import sqlalchemy as sa

revision = "202611130019"
down_revision = "202611130018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "return_approval_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["return_id"], ["sales_returns.id"]),
        sa.UniqueConstraint("return_id", name="uq_return_approval_notification_return"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_return_approval_notifications_user_read",
        "return_approval_notifications",
        ["user_id", "is_read"],
    )


def downgrade():
    raise RuntimeError("Return Bill notification history is retained; this additive migration cannot be downgraded safely.")
