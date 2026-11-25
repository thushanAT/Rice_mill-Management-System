"""Record tendered amount separately from invoice applied payments."""
from alembic import op
import sqlalchemy as sa

revision = "202611130018"
down_revision = "202611120017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sales", sa.Column("amount_received", sa.Numeric(14, 2), nullable=True))
    op.execute("UPDATE sales SET amount_received = amount_paid WHERE amount_received IS NULL")
    op.alter_column(
        "sales",
        "amount_received",
        existing_type=sa.Numeric(14, 2),
        nullable=False,
        server_default=sa.text("0"),
    )
    op.alter_column("sales", "amount_received", server_default=None)


def downgrade():
    op.drop_column("sales", "amount_received")
