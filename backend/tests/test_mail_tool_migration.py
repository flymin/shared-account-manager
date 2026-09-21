"""Legacy selections survive upgrade; uncertain in-flight codes never resume."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import Session, engine
from app.email_worker import lease_next, save_candidate
from app.mail import Candidate
from app.models import EmailCodeRun, now
from app.verification import finish
from test_email_worker import start

migration = importlib.import_module("migrations.versions.0008_mail_tools")


@pytest.mark.parametrize("status", ["pending", "reading", "found", "failed"])
def test_upgrade_preserves_choices_and_credentials_and_clears_unbound_jobs(
    admin, make_account, status
):
    enabled = make_account()
    make_account(email="disabled@example.test", mail_tool=None)
    identity = start(admin, enabled)
    assert save_candidate(*lease_next(), Candidate("fixture-message", "123456", now()))
    with Session.begin() as db:
        run = db.get(EmailCodeRun, identity)
        if status == "failed":
            finish(run, status)
        else:
            run.status = status
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            operations.alter_column(
                "accounts", "mail_tool", new_column_name="mail_backend"
            )
            operations.drop_column("email_code_runs", "mail_tool")
            operations.drop_column("email_code_runs", "tool_config_hash")

            def rows(table):
                return [
                    dict(row)
                    for row in connection.execute(
                        sa.text(f"SELECT * FROM {table} ORDER BY id")
                    ).mappings()
                ]

            accounts, jobs = rows("accounts"), rows("email_code_runs")
            with Operations.context(operations.migration_context):
                migration.upgrade()
            expected_accounts = []
            for row in accounts:
                expected = dict(row)
                expected["mail_tool"] = expected.pop("mail_backend")
                expected_accounts.append(expected)
            assert rows("accounts") == expected_accounts
            expected_job = dict(
                jobs[0], mail_tool=jobs[0]["mail_backend"], tool_config_hash=""
            )
            if status != "failed":
                expected_job.update(
                    status="cancelled",
                    code_encrypted=None,
                    received_at=None,
                    lease_token=None,
                    lease_until=None,
                )
            assert rows("email_code_runs") == [expected_job]
            # Downgrade cannot interpret a tool ID as a provider ID.
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert rows("accounts") == [
                dict(
                    row,
                    mail_backend=None,
                    mail_config_version=row["mail_config_version"] + 1,
                )
                for row in accounts
            ]
            assert rows("email_code_runs")[0]["message_id"] == "fixture-message"
        finally:
            transaction.rollback()
