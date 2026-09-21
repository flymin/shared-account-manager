"""Exercise the recurring quota reset schema migration."""

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import engine

migration = importlib.import_module("migrations.versions.0007_quota_reset_interval")


def test_quota_reset_interval_upgrade_and_downgrade_roundtrip(admin, make_account):
    make_account()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            for table, constraint in (
                ("accounts", "accounts_quota_reset_interval_range"),
                ("settings", "settings_quota_reset_interval_range"),
            ):
                if any(
                    item["name"] == constraint
                    for item in sa.inspect(connection).get_check_constraints(table)
                ):
                    operations.drop_constraint(constraint, table, type_="check")
            operations.drop_column("accounts", "quota_reset_interval_days")
            operations.drop_column("settings", "quota_reset_interval_days")

            def snapshot(table):
                return [
                    dict(row)
                    for row in connection.execute(
                        sa.text(f"SELECT * FROM {table} ORDER BY id")
                    ).mappings()
                ]

            old_accounts, old_settings = snapshot("accounts"), snapshot("settings")
            with Operations.context(operations.migration_context):
                migration.upgrade()
            assert snapshot("accounts") == [
                dict(row, quota_reset_interval_days=None) for row in old_accounts
            ]
            assert snapshot("settings") == [
                dict(row, quota_reset_interval_days=7) for row in old_settings
            ]
            settings_column = next(
                column
                for column in sa.inspect(connection).get_columns("settings")
                if column["name"] == "quota_reset_interval_days"
            )
            assert settings_column["default"] is None
            assert (
                connection.execute(
                    sa.text("SELECT quota_reset_interval_days FROM settings WHERE id=1")
                ).scalar_one()
                == 7
            )
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert snapshot("accounts") == old_accounts
            assert snapshot("settings") == old_settings
            assert not any(
                column["name"] == "quota_reset_interval_days"
                for column in sa.inspect(connection).get_columns("settings")
            )
        finally:
            transaction.rollback()
