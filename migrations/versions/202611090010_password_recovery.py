"""Add hashed, expiring password recovery tokens and session versioning.

The migration is additive. Existing users retain their password hashes and
receive auth_version 0; no existing business records are touched.
"""

from alembic import op
import sqlalchemy as sa


revision = "202611090010"
down_revision = "202611090009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "password_recovery_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_password_recovery_tokens_user_id_users"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("otp_hash", sa.String(length=64), nullable=True),
        sa.Column("otp_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reset_token_hash", sa.String(length=64), nullable=True),
        sa.Column("reset_expires_at", sa.DateTime(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("reset_token_hash", name="uq_password_recovery_reset_hash"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_password_recovery_attempts_nonnegative"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_password_recovery_user_purpose_created",
        "password_recovery_tokens",
        ["user_id", "purpose", "created_at"],
    )


def downgrade():
    raise RuntimeError("Password recovery history is retained; this migration cannot be downgraded safely.")
