"""Configure recurring quota reset intervals."""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    op.add_column(
        "settings",
        sa.Column(
            "quota_reset_interval_days",
            sa.Integer(),
            nullable=False,
            server_default="7",
        ),
    )
    op.create_check_constraint(
        "settings_quota_reset_interval_range",
        "settings",
        "quota_reset_interval_days BETWEEN 1 AND 365",
    )
    op.add_column(
        "accounts",
        sa.Column("quota_reset_interval_days", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "accounts_quota_reset_interval_range",
        "accounts",
        "quota_reset_interval_days IS NULL OR quota_reset_interval_days BETWEEN 1 AND 365",
    )
    op.alter_column("settings", "quota_reset_interval_days", server_default=None)


def downgrade():
    op.drop_constraint("accounts_quota_reset_interval_range", "accounts", type_="check")
    op.drop_column("accounts", "quota_reset_interval_days")
    op.drop_constraint("settings_quota_reset_interval_range", "settings", type_="check")
    op.drop_column("settings", "quota_reset_interval_days")
