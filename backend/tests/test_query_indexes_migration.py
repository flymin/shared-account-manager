"""The query-only migration must roundtrip without changing stored data."""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db import engine

migration = importlib.import_module("migrations.versions.0014_query_indexes")


def test_query_indexes_roundtrip_preserves_account_data(admin, make_account):
    make_account()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            before = (
                connection.execute(
                    text("SELECT to_jsonb(a) FROM accounts a ORDER BY id")
                )
                .scalars()
                .all()
            )
            operations = Operations(MigrationContext.configure(connection))
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='ix_events_account_id'"
                    )
                )
                == 1
            )
            with Operations.context(operations.migration_context):
                migration.upgrade()
            after = (
                connection.execute(
                    text("SELECT to_jsonb(a) FROM accounts a ORDER BY id")
                )
                .scalars()
                .all()
            )
            assert after == before
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='ix_events_quota_account_id_id'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='ix_events_account_id_id'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT 'account2' COLLATE account_email_natural < 'account10'"
                    )
                )
                is True
            )
        finally:
            transaction.rollback()
