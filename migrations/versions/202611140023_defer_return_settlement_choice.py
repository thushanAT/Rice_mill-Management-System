"""Allow new return requests to defer settlement choice until approval."""
from alembic import op
import sqlalchemy as sa

revision = "202611140023"
down_revision = "202611130022"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "sales_returns",
        "settlement_type",
        existing_type=sa.String(length=20),
        nullable=True,
    )


def downgrade():
    raise RuntimeError("Historical and pending Return Bill settlement choices are retained; downgrade is unsafe.")
