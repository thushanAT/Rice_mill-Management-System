"""Associate canonical sellable categories with existing Administration rows."""
from alembic import op
import sqlalchemy as sa


revision = "202611120014"
down_revision = "202611120013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("main_categories", sa.Column("master_category_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_main_categories_master_category_id",
        "main_categories",
        ["master_category_id"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_main_categories_master_category",
        "main_categories",
        "master_items",
        ["master_category_id"],
        ["id"],
    )
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE master_items SET name='Other Sellable Products',is_active=1 "
        "WHERE group_name='category' AND name='Other' "
        "AND NOT EXISTS (SELECT 1 FROM (SELECT id FROM master_items WHERE group_name='category' "
        "AND name='Other Sellable Products') existing_category)"
    ))
    bind.execute(sa.text(
        "UPDATE master_items SET is_active=0 WHERE group_name='category' AND name='Other' "
        "AND EXISTS (SELECT 1 FROM (SELECT id FROM master_items WHERE group_name='category' "
        "AND name='Other Sellable Products') existing_category)"
    ))
    for category_name in ("Bulk Rice", "Packed Rice"):
        bind.execute(
            sa.text("INSERT INTO master_items (group_name,name,unit,is_active,created_at,updated_at) " "SELECT 'category',:name,'kg',1,NOW(),NOW() WHERE NOT EXISTS " "(SELECT 1 FROM master_items WHERE group_name='category' AND name=:name)"),
            {"name": category_name},
        )
    bind.execute(sa.text(
        "INSERT INTO master_items (group_name,name,unit,is_active,created_at,updated_at) "
        "SELECT 'category','Other Sellable Products','kg',1,NOW(),NOW() "
        "WHERE NOT EXISTS (SELECT 1 FROM master_items WHERE group_name='category' AND name='Other Sellable Products')"
    ))
    # Keep the old Bran category record but stop presenting it as a selectable category.
    bind.execute(sa.text("UPDATE master_items SET is_active=0 WHERE group_name='category' AND name='Bran'"))
    bind.execute(sa.text(
        "UPDATE main_categories c JOIN master_items m ON m.group_name='category' AND "
        "m.name=CASE c.code WHEN 'other' THEN 'Other Sellable Products' ELSE c.name END "
        "SET c.master_category_id=m.id"
    ))


def downgrade():
    op.drop_constraint("fk_main_categories_master_category", "main_categories", type_="foreignkey")
    op.drop_index("ix_main_categories_master_category_id", table_name="main_categories")
    op.drop_column("main_categories", "master_category_id")
