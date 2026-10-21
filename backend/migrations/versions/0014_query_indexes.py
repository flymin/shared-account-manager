"""Support bounded list reads, natural email ordering and reverse ACL lookups."""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"

INDEXES = (
    ("ix_events_account_id_id", "events", ["account_id", sa.text("id DESC")]),
    ("ix_events_actor_id_id", "events", ["actor_id", sa.text("id DESC")]),
    ("ix_events_target_user_id_id", "events", ["target_user_id", sa.text("id DESC")]),
    (
        "ix_claims_user_claimed_id",
        "claims",
        ["user_id", sa.text("claimed_at DESC"), sa.text("id DESC")],
    ),
    (
        "ix_claims_claimed_id",
        "claims",
        [sa.text("claimed_at DESC"), sa.text("id DESC")],
    ),
    ("ix_account_groups_group_account", "account_groups", ["group_id", "account_id"]),
    ("ix_account_users_user_account", "account_users", ["user_id", "account_id"]),
    ("ix_user_groups_group_user", "user_groups", ["group_id", "user_id"]),
)


def upgrade():
    op.execute(
        "CREATE COLLATION account_email_natural (provider = icu, locale = 'en-u-kn-true-ks-level1', deterministic = false)"
    )
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    op.create_index(
        "ix_events_quota_account_id_id",
        "events",
        ["account_id", sa.text("id DESC")],
        postgresql_where=sa.text("kind IN ('quota_updated', 'quota_reset')"),
    )
    op.create_index(
        "ix_claims_active_account_user",
        "claims",
        ["account_id", "user_id"],
        postgresql_where=sa.text("returned_at IS NULL AND invalidated_at IS NULL"),
    )
    op.drop_index("ix_events_account_id", table_name="events")
    op.drop_index("ix_claims_user_id", table_name="claims")


def downgrade():
    op.create_index("ix_events_account_id", "events", ["account_id"])
    op.create_index("ix_claims_user_id", "claims", ["user_id"])
    op.drop_index("ix_claims_active_account_user", table_name="claims")
    op.drop_index("ix_events_quota_account_id_id", table_name="events")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.execute("DROP COLLATION account_email_natural")
