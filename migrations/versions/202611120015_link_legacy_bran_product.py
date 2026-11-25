"""Link legacy Bran stock to its existing product ID despite a misspelling."""
from alembic import op
import sqlalchemy as sa


revision = "202611120015"
down_revision = "202611120014"
branch_labels = None
depends_on = None


_LEGACY_MATCH = """
SELECT s.item_name AS current_name, MIN(m.id) AS product_id, MIN(m.name) AS old_name
FROM stocks s
JOIN master_items m ON m.group_name='product' AND SOUNDEX(m.name)=SOUNDEX(s.item_name)
WHERE s.stock_type='bran'
GROUP BY s.item_name
HAVING COUNT(DISTINCT m.id)=1
"""


def upgrade():
    bind = op.get_bind()
    matches = bind.execute(sa.text(_LEGACY_MATCH)).mappings().all()
    # Correct only the unique active product spelling supported by existing
    # Bran stock. Its master ID remains stable; invoice text is untouched.
    for row in matches:
        prices = bind.execute(
            sa.text("SELECT id,price_type FROM price_settings WHERE item_name=:old_name"),
            {"old_name": row["old_name"]},
        ).all()
        for price_id, price_type in prices:
            conflict = bind.execute(
                sa.text("SELECT id FROM price_settings WHERE item_name=:new_name AND price_type=:price_type AND id<>:id LIMIT 1"),
                {"new_name": row["current_name"], "price_type": price_type, "id": price_id},
            ).first()
            if not conflict:
                bind.execute(
                    sa.text("UPDATE price_settings SET item_name=:name WHERE id=:id"),
                    {"name": row["current_name"], "id": price_id},
                )
        duplicate = bind.execute(
            sa.text("SELECT id FROM master_items WHERE group_name='product' AND name=:name AND id<>:id LIMIT 1"),
            {"name": row["current_name"], "id": row["product_id"]},
        ).first()
        if not duplicate:
            bind.execute(
                sa.text("UPDATE master_items SET name=:name WHERE id=:id"),
                {"name": row["current_name"], "id": row["product_id"]},
            )
    bind.execute(sa.text(
        "UPDATE stocks s JOIN master_items m ON m.group_name='product' AND m.name=s.item_name "
        "SET s.product_id=m.id WHERE s.stock_type IN ('bran','other') AND s.product_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE sale_items si JOIN stocks s ON s.stock_type='bran' AND s.item_name=si.product_name "
        "AND s.variety=COALESCE(si.variety,'') JOIN master_items m ON m.group_name='product' "
        "AND SOUNDEX(m.name)=SOUNDEX(s.item_name) SET si.product_id=m.id "
        "WHERE si.category='bran' AND si.product_id IS NULL"
    ))


def downgrade():
    # Historical master names and newly populated identity links are retained.
    raise RuntimeError("Legacy Bran product identity corrections are non-destructive and cannot be downgraded.")
