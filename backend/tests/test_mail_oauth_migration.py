import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import engine

migration = importlib.import_module("migrations.versions.0012_mail_oauth")


def test_oauth_migration_preserves_passwords_and_refuses_credential_loss(
    admin, make_account
):
    account = make_account()
    actor = admin.get("/api/v1/auth/me").json()["user"]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            operations.drop_table("mail_oauth_states")
            operations.drop_table("mail_oauth_credentials")
            before = connection.execute(
                sa.text("SELECT to_jsonb(a) FROM accounts a ORDER BY id")
            ).scalars().all()
            with Operations.context(operations.migration_context):
                migration.upgrade()
            after = connection.execute(
                sa.text("SELECT to_jsonb(a) FROM accounts a ORDER BY id")
            ).scalars().all()
            assert after == before
            connection.execute(
                sa.text(
                    """INSERT INTO mail_oauth_credentials
                    (account_id, backend_id, client_id_encrypted, tenant,
                     refresh_token_encrypted, authorized_email, updated_by,
                     created_at, updated_at)
                    VALUES (:account, 'outlook', 'fictional-encrypted-client',
                            'consumers', 'fictional-encrypted-token', :email,
                            :actor, now(), now())"""
                ),
                {"account": account["id"], "email": account["email"], "actor": actor["id"]},
            )
            with Operations.context(operations.migration_context):
                with pytest.raises(RuntimeError, match="OAuth credentials exist"):
                    migration.downgrade()
            assert connection.scalar(
                sa.text("SELECT count(*) FROM mail_oauth_credentials")
            ) == 1
            connection.execute(sa.text("DELETE FROM mail_oauth_credentials"))
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert "mail_oauth_credentials" not in sa.inspect(connection).get_table_names()
        finally:
            transaction.rollback()
