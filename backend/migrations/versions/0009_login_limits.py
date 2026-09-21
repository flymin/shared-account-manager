"""Bound login verification and index expiring failure counters."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"


def upgrade():
    op.create_table(
        "login_budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="login_budget_singleton"),
    )
    op.create_index("ix_login_attempts_since", "login_attempts", ["since"])
    # Existing lockouts survive the upgrade. API and worker gradually remove
    # expired rows without a large, blocking deletion during migration.


def downgrade():
    op.drop_index("ix_login_attempts_since", table_name="login_attempts")
    op.drop_table("login_budget")
