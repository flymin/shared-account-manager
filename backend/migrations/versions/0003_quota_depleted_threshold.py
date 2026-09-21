"""Persist the configurable quota depletion threshold."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column(
        "settings",
        sa.Column(
            "quota_depleted_threshold",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.create_check_constraint(
        "settings_quota_depleted_threshold_range",
        "settings",
        "quota_depleted_threshold BETWEEN 1 AND 100",
    )


def downgrade():
    op.drop_constraint(
        "settings_quota_depleted_threshold_range", "settings", type_="check"
    )
    op.drop_column("settings", "quota_depleted_threshold")
