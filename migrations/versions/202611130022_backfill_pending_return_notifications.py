"""Make existing pending returns visible in the requester notification history."""
from alembic import op
import sqlalchemy as sa

revision = "202611130022"
down_revision = "202611130021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        INSERT INTO return_approval_notifications (user_id, return_id, is_read, created_at)
        SELECT r.created_by, r.id, 0, UTC_TIMESTAMP()
        FROM sales_returns r
        WHERE r.created_by IS NOT NULL
          AND (r.approval_status IS NULL OR r.approval_status='pending')
          AND NOT EXISTS (
              SELECT 1 FROM return_approval_notifications n WHERE n.return_id=r.id
          )
    """))


def downgrade():
    raise RuntimeError("Pending return notification history is retained; this additive migration cannot be downgraded safely.")
