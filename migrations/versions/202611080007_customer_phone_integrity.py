"""Normalize customer phone keys and enforce uniqueness among active accounts.

Historical customer phone display values are retained. No sales, payments,
allocations, or customer rows are deleted or merged.
"""

import re
from collections import Counter

from alembic import op
import sqlalchemy as sa


revision = "202611080007"
down_revision = "202611080006"
branch_labels = None
depends_on = None


def _normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return ""
    if digits.startswith("94") and len(digits) == 11:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+94" + digits[1:]
    return digits


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, phone, is_active FROM customers")).all()
    normalized = [(row.id, _normalize_phone(row.phone), bool(row.is_active)) for row in rows]
    if any(not phone or len(phone) > 30 for _, phone, _ in normalized):
        raise RuntimeError("Customer phone normalization found an invalid existing value; migration stopped before schema changes.")
    active_counts = Counter(phone for _, phone, active in normalized if active)
    duplicates = [phone for phone, count in active_counts.items() if count > 1]
    if duplicates:
        raise RuntimeError("Existing active customers have duplicate normalized phone numbers; migration stopped before schema changes.")

    op.add_column("customers", sa.Column("phone_normalized", sa.String(length=30), nullable=True))
    for customer_id, phone, _ in normalized:
        bind.execute(
            sa.text("UPDATE customers SET phone_normalized=:phone WHERE id=:customer_id"),
            {"phone": phone, "customer_id": customer_id},
        )
    op.alter_column(
        "customers",
        "phone_normalized",
        existing_type=sa.String(length=30),
        nullable=False,
    )
    op.add_column(
        "customers",
        sa.Column(
            "active_phone",
            sa.String(length=30),
            sa.Computed("CASE WHEN `is_active` = 1 THEN `phone_normalized` ELSE NULL END", persisted=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_customers_active_phone", "customers", ["active_phone"])


def downgrade():
    raise RuntimeError("Customer phone integrity is retained; this migration cannot be downgraded safely.")
