"""Persist account mailbox choices and scope durable jobs to their backend."""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"


def upgrade():
    op.add_column(
        "accounts",
        sa.Column(
            "mail_config_version", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    # Existing accounts/jobs all used this provider. Explicit null is meaningful
    # for new accounts, so only the import API (not the DB) supplies a default.
    op.add_column(
        "accounts",
        sa.Column(
            "mail_backend", sa.String(64), nullable=True, server_default="mailcom"
        ),
    )
    op.add_column(
        "email_code_runs",
        sa.Column(
            "mail_backend", sa.String(64), nullable=False, server_default="mailcom"
        ),
    )
    op.alter_column("accounts", "mail_backend", server_default=None)
    op.alter_column("email_code_runs", "mail_backend", server_default=None)


def downgrade():
    # An older app has no backend selection and could resume a different provider's
    # job. Invalidate results before dropping its identity.
    op.execute(
        "UPDATE email_code_runs SET status='cancelled', code_encrypted=NULL, received_at=NULL, lease_token=NULL, lease_until=NULL WHERE status IN ('pending','reading','found')"
    )
    op.drop_column("email_code_runs", "mail_backend")
    op.drop_column("accounts", "mail_backend")
    op.drop_column("accounts", "mail_config_version")
