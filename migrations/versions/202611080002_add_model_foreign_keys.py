"""Add foreign keys declared by the current SQLAlchemy models.

This migration is additive. It does not update or delete rows.
"""

from alembic import op

revision = "202611080002"
down_revision = "202611080001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key('fk_audit_logs_user_id_users', 'audit_logs', 'users', ['user_id'], ['id'])
    op.create_foreign_key(
        'fk_purchases_created_by_users',
        'purchases',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_stock_movements_stock_id_stocks',
        'stock_movements',
        'stocks',
        ['stock_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_stock_movements_created_by_users',
        'stock_movements',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_production_records_created_by_users',
        'production_records',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_sales_customer_id_customers',
        'sales',
        'customers',
        ['customer_id'],
        ['id'],
    )
    op.create_foreign_key('fk_sales_created_by_users', 'sales', 'users', ['created_by'], ['id'])
    op.create_foreign_key(
        'fk_sale_items_sale_id_sales',
        'sale_items',
        'sales',
        ['sale_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_held_sales_created_by_users',
        'held_sales',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_sales_returns_sale_id_sales',
        'sales_returns',
        'sales',
        ['sale_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_sales_returns_customer_id_customers',
        'sales_returns',
        'customers',
        ['customer_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_sales_returns_created_by_users',
        'sales_returns',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_return_items_return_id_sales_returns',
        'return_items',
        'sales_returns',
        ['return_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_return_items_sale_item_id_sale_items',
        'return_items',
        'sale_items',
        ['sale_item_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_payments_customer_id_customers',
        'payments',
        'customers',
        ['customer_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_payments_created_by_users',
        'payments',
        'users',
        ['created_by'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_payment_allocations_payment_id_payments',
        'payment_allocations',
        'payments',
        ['payment_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_payment_allocations_sale_id_sales',
        'payment_allocations',
        'sales',
        ['sale_id'],
        ['id'],
    )


def downgrade():
    op.drop_constraint(
        'fk_payment_allocations_sale_id_sales',
        'payment_allocations',
        type_="foreignkey",
    )
    op.drop_constraint(
        'fk_payment_allocations_payment_id_payments',
        'payment_allocations',
        type_="foreignkey",
    )
    op.drop_constraint('fk_payments_created_by_users', 'payments', type_="foreignkey")
    op.drop_constraint('fk_payments_customer_id_customers', 'payments', type_="foreignkey")
    op.drop_constraint(
        'fk_return_items_sale_item_id_sale_items',
        'return_items',
        type_="foreignkey",
    )
    op.drop_constraint(
        'fk_return_items_return_id_sales_returns',
        'return_items',
        type_="foreignkey",
    )
    op.drop_constraint('fk_sales_returns_created_by_users', 'sales_returns', type_="foreignkey")
    op.drop_constraint(
        'fk_sales_returns_customer_id_customers',
        'sales_returns',
        type_="foreignkey",
    )
    op.drop_constraint('fk_sales_returns_sale_id_sales', 'sales_returns', type_="foreignkey")
    op.drop_constraint('fk_held_sales_created_by_users', 'held_sales', type_="foreignkey")
    op.drop_constraint('fk_sale_items_sale_id_sales', 'sale_items', type_="foreignkey")
    op.drop_constraint('fk_sales_created_by_users', 'sales', type_="foreignkey")
    op.drop_constraint('fk_sales_customer_id_customers', 'sales', type_="foreignkey")
    op.drop_constraint(
        'fk_production_records_created_by_users',
        'production_records',
        type_="foreignkey",
    )
    op.drop_constraint('fk_stock_movements_created_by_users', 'stock_movements', type_="foreignkey")
    op.drop_constraint('fk_stock_movements_stock_id_stocks', 'stock_movements', type_="foreignkey")
    op.drop_constraint('fk_purchases_created_by_users', 'purchases', type_="foreignkey")
    op.drop_constraint('fk_audit_logs_user_id_users', 'audit_logs', type_="foreignkey")
