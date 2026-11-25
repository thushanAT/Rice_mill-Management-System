"""Add separate purchase and selling default-price lock settings."""
from alembic import op
import sqlalchemy as sa


revision = "202611120016"
down_revision = "202611120015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "price_settings",
        sa.Column("purchase_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "price_settings",
        sa.Column("selling_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("price_settings", "purchase_locked", server_default=None)
    op.alter_column("price_settings", "selling_locked", server_default=None)


def downgrade():
    op.drop_column("price_settings", "selling_locked")
    op.drop_column("price_settings", "purchase_locked")
