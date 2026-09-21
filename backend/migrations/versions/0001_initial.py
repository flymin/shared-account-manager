"""Initial, immutable schema snapshot."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute(
        "CREATE TABLE accounts (\n\tid VARCHAR(36) NOT NULL, \n\temail VARCHAR(254) NOT NULL, \n\tpassword_encrypted TEXT NOT NULL, \n\tauth_password_encrypted TEXT NOT NULL, \n\ttier VARCHAR(3) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE, \n\tcapacity INTEGER, \n\tdeleted BOOLEAN NOT NULL, \n\tquota INTEGER, \n\treset_at TIMESTAMP WITH TIME ZONE, \n\tquota_updated_at TIMESTAMP WITH TIME ZONE, \n\tquota_source VARCHAR(20), \n\tquota_version INTEGER NOT NULL, \n\thealth VARCHAR(30) NOT NULL, \n\thealth_categories JSONB NOT NULL, \n\thealth_note TEXT NOT NULL, \n\tanomaly_since TIMESTAMP WITH TIME ZONE, \n\thealth_version INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (tier IN ('5x','20x')), \n\tCHECK (capacity IS NULL OR capacity > 0), \n\tCHECK (quota IS NULL OR quota BETWEEN 0 AND 100), \n\tCHECK (health IN ('normal','abnormal','possibly_recovered')), \n\tUNIQUE (email)\n)"
    )
    op.execute(
        "CREATE TABLE groups (\n\tid VARCHAR(36) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (name)\n)"
    )
    op.execute(
        "CREATE TABLE login_attempts (\n\tkey VARCHAR(64) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tsince TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (key)\n)"
    )
    op.execute(
        "CREATE TABLE settings (\n\tid INTEGER NOT NULL, \n\tuser_claim_limit INTEGER NOT NULL, \n\taccount_capacity INTEGER NOT NULL, \n\tobservation_hours INTEGER NOT NULL, \n\tcooldown_hours INTEGER NOT NULL, \n\tsession_days INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)"
    )
    op.execute(
        "CREATE TABLE users (\n\tid VARCHAR(36) NOT NULL, \n\tusername VARCHAR(80) NOT NULL, \n\tdisplay_name VARCHAR(100) NOT NULL, \n\tpassword_hash TEXT NOT NULL, \n\trole VARCHAR(10) NOT NULL, \n\tmust_change_password BOOLEAN NOT NULL, \n\tclaim_limit INTEGER, \n\tdeleted BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (role IN ('admin','user')), \n\tCHECK (claim_limit IS NULL OR claim_limit > 0), \n\tUNIQUE (username)\n)"
    )
    op.execute(
        "CREATE TABLE account_groups (\n\taccount_id VARCHAR(36) NOT NULL, \n\tgroup_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (account_id, group_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(group_id) REFERENCES groups (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE account_users (\n\taccount_id VARCHAR(36) NOT NULL, \n\tuser_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (account_id, user_id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)"
    )
    op.execute(
        "CREATE TABLE claims (\n\tid VARCHAR(36) NOT NULL, \n\taccount_id VARCHAR(36) NOT NULL, \n\tuser_id VARCHAR(36) NOT NULL, \n\tclaimed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tobserved_health_version INTEGER, \n\tinvalidated_at TIMESTAMP WITH TIME ZONE, \n\tinvalidation_kind VARCHAR(20), \n\tinvalidation_reason TEXT, \n\treturned_at TIMESTAMP WITH TIME ZONE, \n\treturn_kind VARCHAR(30), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)"
    )
    op.execute("CREATE INDEX ix_claims_account_id ON claims (account_id)")
    op.execute("CREATE INDEX ix_claims_user_id ON claims (user_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_open_claim ON claims (user_id, account_id) WHERE returned_at IS NULL"
    )
    op.execute(
        "CREATE TABLE events (\n\tid BIGSERIAL NOT NULL, \n\taccount_id VARCHAR(36), \n\tactor_id VARCHAR(36), \n\ttarget_user_id VARCHAR(36), \n\tkind VARCHAR(50) NOT NULL, \n\tdetails JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tautomation_key VARCHAR(120), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(actor_id) REFERENCES users (id), \n\tFOREIGN KEY(target_user_id) REFERENCES users (id), \n\tUNIQUE (automation_key)\n)"
    )
    op.execute("CREATE INDEX ix_events_account_id ON events (account_id)")
    op.execute(
        "CREATE TABLE login_sessions (\n\ttoken_hash VARCHAR(64) NOT NULL, \n\tuser_id VARCHAR(36) NOT NULL, \n\tcsrf_token VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (token_hash), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)"
    )
    op.execute("CREATE INDEX ix_login_sessions_user_id ON login_sessions (user_id)")
    op.execute(
        "CREATE TABLE user_groups (\n\tuser_id VARCHAR(36) NOT NULL, \n\tgroup_id VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (user_id, group_id), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(group_id) REFERENCES groups (id) ON DELETE CASCADE\n)"
    )


def downgrade():
    op.drop_table("user_groups")
    op.drop_table("login_sessions")
    op.drop_table("events")
    op.drop_table("claims")
    op.drop_table("account_users")
    op.drop_table("account_groups")
    op.drop_table("users")
    op.drop_table("settings")
    op.drop_table("login_attempts")
    op.drop_table("groups")
    op.drop_table("accounts")
