"""Encrypted TOTP configurations and durable exclusive email fetch jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.add_column(
        "settings",
        sa.Column(
            "email_code_timeout_minutes",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.create_check_constraint(
        "settings_email_code_timeout_range",
        "settings",
        "email_code_timeout_minutes BETWEEN 1 AND 30",
    )
    op.create_table(
        "two_factors",
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True
        ),
        sa.Column("kind", sa.String(10), primary_key=True),
        sa.Column("uri_encrypted", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('service','mail')"),
    )
    op.create_table(
        "email_code_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("code_encrypted", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("message_id", sa.Text()),
        sa.Column("error", sa.String(50)),
        sa.CheckConstraint(
            "status IN ('pending','reading','found','timed_out','cancelled','failed')"
        ),
    )
    op.create_index("ix_email_code_runs_account_id", "email_code_runs", ["account_id"])
    op.create_index(
        "uq_running_email_code_account",
        "email_code_runs",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','reading')"),
    )


def downgrade():
    op.drop_table("email_code_runs")
    op.drop_table("two_factors")
    op.drop_constraint("settings_email_code_timeout_range", "settings", type_="check")
    op.drop_column("settings", "email_code_timeout_minutes")
