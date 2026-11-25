"""Allow one immutable final closeout document after partial settlements."""

from alembic import op
import sqlalchemy as sa

revision = "202611080004"
down_revision = "202611080003"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "combined_transaction_documents",
        "settlement_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "combined_transaction_documents",
        sa.Column("final_return_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_combined_documents_final_return",
        "combined_transaction_documents",
        ["final_return_id"],
    )
    op.create_foreign_key(
        "fk_combined_documents_final_return",
        "combined_transaction_documents",
        "sales_returns",
        ["final_return_id"],
        ["id"],
    )


def downgrade():
    raise RuntimeError("Final return documents are retained; this additive migration cannot be downgraded safely.")
