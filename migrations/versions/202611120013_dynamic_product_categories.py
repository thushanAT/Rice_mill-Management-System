"""Connect existing product IDs to sellable main categories and inventory.

The migration only adds nullable product references and category links. It
does not change balances, movement rows, invoices, or historical amounts.
"""
from alembic import op
import sqlalchemy as sa


revision = "202611120013"
down_revision = "202611090012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "main_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("inventory_section", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("code", name="uq_main_categories_code"),
        sa.UniqueConstraint("name", name="uq_main_categories_name"),
        sa.UniqueConstraint("inventory_section", name="uq_main_categories_inventory_section"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "master_item_main_categories",
        sa.Column("master_item_id", sa.Integer(), nullable=False),
        sa.Column("main_category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["master_item_id"],
            ["master_items.id"],
            name="fk_master_item_main_categories_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["main_category_id"],
            ["main_categories.id"],
            name="fk_master_item_main_categories_category",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("master_item_id", "main_category_id"),
        mysql_engine="InnoDB",
    )
    op.add_column("stocks", sa.Column("product_id", sa.Integer(), nullable=True))
    op.create_index("ix_stocks_product_id", "stocks", ["product_id"])
    op.create_foreign_key(
        "fk_stocks_product_id_master_items",
        "stocks",
        "master_items",
        ["product_id"],
        ["id"],
    )
    op.add_column("sale_items", sa.Column("product_id", sa.Integer(), nullable=True))
    op.create_index("ix_sale_items_product_id", "sale_items", ["product_id"])
    op.create_foreign_key(
        "fk_sale_items_product_id_master_items",
        "sale_items",
        "master_items",
        ["product_id"],
        ["id"],
    )
    op.add_column("sale_items", sa.Column("stock_type", sa.String(30), nullable=True))

    bind = op.get_bind()
    categories = sa.table(
        "main_categories",
        sa.column("id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("inventory_section", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = sa.func.now()
    for code, name, section in (
        ("bulk", "Bulk Rice", "rice"),
        ("packed", "Packed Rice", "packed"),
        ("other", "Other Sellable Products", "other"),
    ):
        bind.execute(
            sa.text("INSERT INTO main_categories (code,name,inventory_section,is_active,created_at,updated_at) " "VALUES (:code,:name,:section,1,NOW(),NOW()) " "ON DUPLICATE KEY UPDATE name=VALUES(name), inventory_section=VALUES(inventory_section)"),
            {"code": code, "name": name, "section": section},
        )

    link = sa.table(
        "master_item_main_categories",
        sa.column("master_item_id", sa.Integer),
        sa.column("main_category_id", sa.Integer),
    )
    for group, category_code in (
        ("rice_variety", "bulk"),
        ("rice_variety", "packed"),
        ("product", "other"),
    ):
        bind.execute(
            sa.text("INSERT IGNORE INTO master_item_main_categories (master_item_id,main_category_id) " "SELECT m.id,c.id FROM master_items m JOIN main_categories c ON c.code=:code " "WHERE m.group_name=:group_name"),
            {"code": category_code, "group_name": group},
        )

    bind.execute(sa.text(
        "UPDATE stocks s JOIN master_items m ON "
        "((s.stock_type IN ('rice','packed') AND m.group_name='rice_variety' AND m.name=s.variety) OR "
        "(s.stock_type IN ('wet_paddy','dry_paddy') AND m.group_name IN ('rice_variety','paddy_type') AND m.name=s.variety) OR "
        "(s.stock_type IN ('bran','other') AND m.group_name='product' AND m.name=s.item_name)) "
        "SET s.product_id=m.id WHERE s.product_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE sale_items si JOIN master_items m ON "
        "((si.category IN ('bulk','packed') AND m.group_name='rice_variety' AND m.name=si.variety) OR "
        "(si.category IN ('bran','other') AND m.group_name='product' AND m.name=si.product_name)) "
        "SET si.product_id=m.id WHERE si.product_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE sale_items SET stock_type=CASE "
        "WHEN category='bulk' THEN 'rice' WHEN category='packed' THEN 'packed' "
        "WHEN category='bran' THEN 'bran' WHEN category='other' THEN 'other' "
        "WHEN category='rice' THEN 'rice' ELSE category END WHERE stock_type IS NULL"
    ))


def downgrade():
    op.drop_column("sale_items", "stock_type")
    op.drop_constraint("fk_sale_items_product_id_master_items", "sale_items", type_="foreignkey")
    op.drop_index("ix_sale_items_product_id", table_name="sale_items")
    op.drop_column("sale_items", "product_id")
    op.drop_constraint("fk_stocks_product_id_master_items", "stocks", type_="foreignkey")
    op.drop_index("ix_stocks_product_id", table_name="stocks")
    op.drop_column("stocks", "product_id")
    op.drop_table("master_item_main_categories")
    op.drop_table("main_categories")
