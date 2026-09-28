"""Store mailbox OAuth authorization separately from mailbox passwords."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"


def upgrade():
    op.create_table(
        "mail_oauth_credentials",
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("backend_id", sa.String(64), nullable=False),
        sa.Column("client_id_encrypted", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("tenant", sa.String(128), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("authorized_email", sa.String(254), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("account_id", "backend_id"),
        sa.CheckConstraint(
            "status IN ('active','reauthorize_required')",
            name="mail_oauth_credentials_status",
        ),
    )
    op.create_table(
        "mail_oauth_states",
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("backend_id", sa.String(64), nullable=False),
        sa.Column("admin_id", sa.String(36), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("client_id_encrypted", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("tenant", sa.String(128), nullable=False),
        sa.Column("code_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index(
        "ix_mail_oauth_states_expires_at", "mail_oauth_states", ["expires_at"]
    )


def downgrade():
    # Credentials are encrypted but still valuable; refuse to silently destroy
    # them during a rollback.
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM mail_oauth_credentials)")):
        raise RuntimeError("OAuth credentials exist; retain this schema when rolling back")
    op.drop_index("ix_mail_oauth_states_expires_at", table_name="mail_oauth_states")
    op.drop_table("mail_oauth_states")
    op.drop_table("mail_oauth_credentials")
