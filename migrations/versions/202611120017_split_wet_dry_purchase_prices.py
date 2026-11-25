"""Store Wet and Dry purchase defaults by rice-variety identity."""
from alembic import op
import sqlalchemy as sa


revision = "202611120017"
down_revision = "202611120016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_price_settings",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("variety_id", sa.Integer(), nullable=False),
        sa.Column("paddy_type", sa.String(length=30), nullable=False),
        sa.Column("default_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["variety_id"],
            ["master_items.id"],
            name="fk_purchase_price_variety_id_master_items",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variety_id", "paddy_type", name="uq_purchase_price_variety_type"),
        mysql_engine="InnoDB",
    )
    op.alter_column("purchase_price_settings", "is_locked", server_default=None)
    op.alter_column("purchase_price_settings", "is_active", server_default=None)
    op.alter_column("purchase_price_settings", "created_at", server_default=None)
    op.alter_column("purchase_price_settings", "updated_at", server_default=None)
    op.add_column("purchases", sa.Column("variety_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_purchases_variety_id_master_items",
        "purchases",
        "master_items",
        ["variety_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("fk_purchases_variety_id_master_items", "purchases", type_="foreignkey")
    op.drop_column("purchases", "variety_id")
    op.drop_table("purchase_price_settings")
