"""Adding admission state must not clear existing lockouts or sessions."""

import importlib
from datetime import timedelta

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import Session, engine
from app.models import LoginAttempt, now

migration = importlib.import_module("migrations.versions.0009_login_limits")


def test_login_limits_upgrade_and_downgrade_preserve_existing_data(admin):
    stamp = now()
    with Session.begin() as db:
        db.add_all(
            [
                LoginAttempt(key="fictional-live-key", attempts=10, since=stamp),
                LoginAttempt(
                    key="fictional-expired-key",
                    attempts=8,
                    since=stamp - timedelta(days=1),
                ),
            ]
        )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            with Operations.context(operations.migration_context):
                migration.downgrade()

            def existing():
                return {
                    table: list(
                        connection.scalars(
                            sa.text(
                                f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY to_jsonb(t)::text'
                            )
                        )
                    )
                    for table in ("users", "login_sessions", "login_attempts")
                }

            before = existing()
            with Operations.context(operations.migration_context):
                migration.upgrade()
            assert existing() == before
            assert connection.scalar(sa.text("SELECT count(*) FROM login_budget")) == 0
            assert any(
                index["name"] == "ix_login_attempts_since"
                for index in sa.inspect(connection).get_indexes("login_attempts")
            )
            assert any(
                constraint["name"] == "login_budget_singleton"
                for constraint in sa.inspect(connection).get_check_constraints(
                    "login_budget"
                )
            )
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert existing() == before
            assert "login_budget" not in sa.inspect(connection).get_table_names()
            assert not any(
                index["name"] == "ix_login_attempts_since"
                for index in sa.inspect(connection).get_indexes("login_attempts")
            )
        finally:
            transaction.rollback()
