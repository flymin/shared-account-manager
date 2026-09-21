"""Select composed mailbox tools and freeze their configuration for each run."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def cancel_runs():
    op.execute(
        "UPDATE email_code_runs SET status='cancelled', code_encrypted=NULL, "
        "received_at=NULL, lease_token=NULL, lease_until=NULL "
        "WHERE status IN ('pending','reading','found')"
    )


def upgrade():
    # Keep existing IDs and explicit null. The bundled tool uses the old ID.
    op.alter_column("accounts", "mail_backend", new_column_name="mail_tool")
    op.add_column("email_code_runs", sa.Column("mail_tool", sa.String(64)))
    op.add_column("email_code_runs", sa.Column("tool_config_hash", sa.String(64)))
    op.execute("UPDATE email_code_runs SET mail_tool=mail_backend, tool_config_hash=''")
    op.alter_column("email_code_runs", "mail_tool", nullable=False)
    op.alter_column("email_code_runs", "tool_config_hash", nullable=False)
    # Old jobs did not record a template configuration; never resume their codes
    # using the newly selected tool. Historical message IDs remain deduplicated.
    cancel_runs()


def downgrade():
    cancel_runs()
    op.drop_column("email_code_runs", "tool_config_hash")
    op.drop_column("email_code_runs", "mail_tool")
    op.alter_column("accounts", "mail_tool", new_column_name="mail_backend")
    # Tool IDs can mean a different backend after a configuration change. A
    # downgraded app must require the administrator to select a backend again.
    op.execute(
        "UPDATE accounts SET mail_backend=NULL, mail_config_version=mail_config_version+1"
    )
