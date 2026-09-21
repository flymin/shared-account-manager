import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import engine
from app.email_worker import lease_next, save_candidate
from app.mail import Candidate
from app.models import now
from test_email_worker import start

migration = importlib.import_module("migrations.versions.0006_mail_backends")


def test_legacy_accounts_jobs_backfill_and_downgrade_clears_candidates(
    admin, make_account
):
    account = make_account()
    identity = start(admin, account)
    assert save_candidate(*lease_next(), Candidate("fixture-message", "123456", now()))
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            operations.drop_column("accounts", "mail_backend")
            operations.drop_column("accounts", "mail_config_version")
            operations.drop_column("email_code_runs", "mail_backend")

            def snapshot(table):
                return [
                    dict(row)
                    for row in connection.execute(
                        sa.text(f"SELECT * FROM {table} ORDER BY id")
                    ).mappings()
                ]

            old_accounts, old_jobs = snapshot("accounts"), snapshot("email_code_runs")
            with Operations.context(operations.migration_context):
                migration.upgrade()
            assert snapshot("accounts") == [
                dict(row, mail_backend="mailcom", mail_config_version=0)
                for row in old_accounts
            ]
            assert snapshot("email_code_runs") == [
                dict(row, mail_backend="mailcom") for row in old_jobs
            ]
            for table in ("accounts", "email_code_runs"):
                column = next(
                    c
                    for c in sa.inspect(connection).get_columns(table)
                    if c["name"] == "mail_backend"
                )
                assert column["default"] is None
            connection.execute(
                sa.text("UPDATE accounts SET mail_backend = NULL WHERE id=:id"),
                {"id": account["id"]},
            )
            assert snapshot("accounts")[0]["mail_backend"] is None
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert snapshot("accounts") == old_accounts
            job = snapshot("email_code_runs")[0]
            assert job["id"] == identity and job["status"] == "cancelled"
            assert job["code_encrypted"] is None and job["received_at"] is None
            assert job["lease_token"] is None and job["lease_until"] is None
        finally:
            transaction.rollback()
