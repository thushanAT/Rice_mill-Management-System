"""Add nullable idempotency metadata to payment collection records.

Existing payment history and allocations are preserved. Historical payments
retain NULL idempotency keys and are not rewritten.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611080008"
down_revision = "202611080007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.add_column("payments", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_payments_idempotency_key", "payments", ["idempotency_key"])


def downgrade():
    raise RuntimeError("Payment idempotency metadata is retained; this migration cannot be downgraded safely.")
