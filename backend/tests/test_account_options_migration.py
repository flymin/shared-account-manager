"""Exercise upgrade preservation and safe rollback of configurable options."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import default_account_options
from app.db import engine

migration = importlib.import_module("migrations.versions.0010_account_options")


def snapshot(connection, table):
    return [
        dict(row)
        for row in connection.execute(
            sa.text(f"SELECT * FROM {table} ORDER BY id")
        ).mappings()
    ]


def test_catalog_migration_preserves_accounts_and_roundtrips(admin, make_account):
    make_account()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.downgrade()
            before_accounts = snapshot(connection, "accounts")
            before_settings = snapshot(connection, "settings")
            with Operations.context(context):
                migration.upgrade()
            assert snapshot(connection, "accounts") == before_accounts
            assert snapshot(connection, "settings") == [
                dict(row, account_options=default_account_options())
                for row in before_settings
            ]
            with Operations.context(context):
                migration.downgrade()
            assert snapshot(connection, "accounts") == before_accounts
            assert snapshot(connection, "settings") == before_settings
        finally:
            transaction.rollback()


def test_catalog_migration_keeps_legacy_ids_with_custom_seed(
    admin, make_account, monkeypatch
):
    make_account()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.downgrade()
            connection.execute(
                sa.text(
                    "UPDATE accounts SET health_categories = '[\"legacy-category\"]'::jsonb"
                )
            )
            monkeypatch.setattr(
                migration,
                "default_account_options",
                lambda: {
                    "tiers": [{"id": "custom-tier", "name": "Custom", "enabled": True}],
                    "anomaly_categories": [],
                },
            )
            with Operations.context(context):
                migration.upgrade()
            cfg = snapshot(connection, "settings")[0]["account_options"]
            assert {tier["id"] for tier in cfg["tiers"]} == {"custom-tier", "5x"}
            assert cfg["anomaly_categories"] == [
                {
                    "id": "legacy-category",
                    "name": "legacy-category",
                    "enabled": True,
                    "cooldown_hours": None,
                }
            ]
            connection.execute(sa.text("UPDATE accounts SET tier = 'custom-tier'"))
            with (
                Operations.context(context),
                pytest.raises(RuntimeError, match="new tiers"),
            ):
                migration.downgrade()
            assert snapshot(connection, "accounts")[0]["tier"] == "custom-tier"
            assert snapshot(connection, "settings")[0]["account_options"] == cfg
        finally:
            transaction.rollback()
