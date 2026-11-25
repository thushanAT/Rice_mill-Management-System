"""Add nullable idempotency metadata to purchase records.

Existing purchase rows are preserved with NULL keys. New API-created purchases
use a unique key and request fingerprint so retries cannot duplicate stock.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611090011"
down_revision = "202611090010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchases", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.add_column(
        "purchases",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint("uq_purchases_idempotency_key", "purchases", ["idempotency_key"])


def downgrade():
    raise RuntimeError("Purchase idempotency metadata is retained; this migration cannot be downgraded safely.")
