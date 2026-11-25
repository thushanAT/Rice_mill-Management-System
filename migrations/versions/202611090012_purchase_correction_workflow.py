"""Add append-only purchase correction requests and effective version pointer.

Purchase values remain unchanged in the original purchases row. Approved
snapshots are stored in purchase_corrections and selected by the nullable
effective_correction_id pointer. Existing purchase records are untouched.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611090012"
down_revision = "202611090011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("before_values", sa.JSON(), nullable=False),
        sa.Column("proposed_values", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("decision_notes", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["purchases.id"],
            name="fk_purchase_corrections_purchase_id_purchases",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_purchase_corrections_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_purchase_corrections_reviewed_by_users",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_purchase_corrections_idempotency_key"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_purchase_corrections_purchase_status",
        "purchase_corrections",
        ["purchase_id", "status", "id"],
    )
    op.add_column("purchases", sa.Column("effective_correction_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_purchases_effective_correction_id",
        "purchases",
        "purchase_corrections",
        ["effective_correction_id"],
        ["id"],
    )


def downgrade():
    raise RuntimeError("Purchase correction history is append-only and cannot be downgraded safely.")
