"""Exercise upgrade/downgrade against fictional legacy and fresh type labels."""

import importlib
import json

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import cipher
from app.db import engine

migration = importlib.import_module("migrations.versions.0005_service_verification")


@pytest.mark.parametrize("previous", ["legacy", "service"])
def test_type_migration_preserves_configs_versions_and_audit_roundtrip(
    admin, make_account, previous
):
    active = make_account(email="active@example.test")
    cancelled = make_account(email="cancelled@example.test")
    actor = admin.get("/api/v1/auth/me").json()["user"]["id"]
    encrypted = cipher().encrypt(b"fictional-persisted-uri").decode()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            operations = Operations(MigrationContext.configure(connection))
            for constraint in sa.inspect(connection).get_check_constraints(
                "two_factors"
            ):
                operations.drop_constraint(
                    constraint["name"], "two_factors", type_="check"
                )
            # Fixed synthetic values only; this models the original enum constraint.
            operations.create_check_constraint(
                "two_factors_kind_check",
                "two_factors",
                f"kind IN ('{previous}','mail')",
            )
            for account, kind, value, version in (
                (active["id"], previous, encrypted, 7),
                (cancelled["id"], previous, "", 9),
                (active["id"], "mail", encrypted, 4),
            ):
                connection.execute(
                    sa.text(
                        "INSERT INTO two_factors (account_id,kind,uri_encrypted,version,updated_by,updated_at) "
                        "VALUES (:account,:kind,:value,:version,:actor,now())"
                    ),
                    dict(
                        account=account,
                        kind=kind,
                        value=value,
                        version=version,
                        actor=actor,
                    ),
                )
            for kind, target in (
                ("two_factor_updated", previous),
                ("two_factor_removed", previous),
                ("two_factor_updated", "mail"),
                ("fixture_other", previous),
            ):
                connection.execute(
                    sa.text(
                        "INSERT INTO events (kind,details,created_at) VALUES (:kind,CAST(:details AS jsonb),now())"
                    ),
                    dict(
                        kind=kind,
                        details=json.dumps({"target": target, "fixture": "preserve"}),
                    ),
                )

            def snapshot():
                configs = [
                    dict(row)
                    for row in connection.execute(
                        sa.text("SELECT * FROM two_factors ORDER BY account_id,kind")
                    ).mappings()
                ]
                events = [
                    dict(row)
                    for row in connection.execute(
                        sa.text("SELECT * FROM events ORDER BY id")
                    ).mappings()
                ]
                return configs, events

            before_configs, before_events = snapshot()
            with Operations.context(operations.migration_context):
                migration.upgrade()
            configs, events = snapshot()
            expected_configs = [
                dict(row, kind="service" if row["kind"] == previous else row["kind"])
                for row in before_configs
            ]
            assert sorted(
                configs, key=lambda row: (row["account_id"], row["kind"])
            ) == sorted(
                expected_configs, key=lambda row: (row["account_id"], row["kind"])
            )
            for before, after in zip(before_events, events, strict=True):
                if (
                    before["kind"] in {"two_factor_updated", "two_factor_removed"}
                    and before["details"].get("target") == previous
                ):
                    before = dict(
                        before, details={**before["details"], "target": "service"}
                    )
                assert before == after
            assert (
                cipher().decrypt(
                    next(
                        row["uri_encrypted"]
                        for row in configs
                        if row["kind"] == "service" and row["uri_encrypted"]
                    ).encode()
                )
                == b"fictional-persisted-uri"
            )
            with Operations.context(operations.migration_context):
                migration.downgrade()
            assert snapshot() == (before_configs, before_events)
            with Operations.context(operations.migration_context):
                migration.upgrade()
            assert snapshot() == (configs, events)
        finally:
            transaction.rollback()
