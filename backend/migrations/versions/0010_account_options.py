"""Persist editable account tiers and anomaly recovery rules."""

import json
import re

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

from app.config import default_account_options

revision = "0010"
down_revision = "0009"
CONSTRAINT = "accounts_tier_length"


def read_comment(connection, name):
    return connection.scalar(
        sa.text(
            "SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint "
            "WHERE conrelid = 'accounts'::regclass AND conname = :name"
        ),
        {"name": name},
    )


def comment(connection, name, value):
    identifier = connection.dialect.identifier_preparer.quote(name)
    literal = sa.literal(value).compile(
        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
    )
    connection.exec_driver_sql(
        f"COMMENT ON CONSTRAINT {identifier} ON accounts IS {literal}"
    )


def upgrade():
    connection = op.get_bind()
    constraints = [
        item
        for item in sa.inspect(connection).get_check_constraints("accounts")
        if re.search(r"\btier\b", item["sqltext"])
    ]
    if len(constraints) != 1:
        raise RuntimeError("Unexpected account tier constraint; migration aborted")
    previous = constraints[0]
    column = next(
        item
        for item in sa.inspect(connection).get_columns("accounts")
        if item["name"] == "tier"
    )
    metadata = {
        "revision": revision,
        "name": previous["name"],
        "sqltext": previous["sqltext"],
        "length": column["type"].length,
        "comment": read_comment(connection, previous["name"]),
    }
    options = default_account_options()
    # Preserve existing identifiers even if an operator customized the seed file.
    for kind, query in (
        ("tiers", "SELECT DISTINCT tier FROM accounts ORDER BY tier"),
        (
            "anomaly_categories",
            "SELECT DISTINCT jsonb_array_elements_text(health_categories) AS category FROM accounts ORDER BY category",
        ),
    ):
        known = {item["id"] for item in options[kind]}
        for identity in connection.scalars(sa.text(query)):
            if identity not in known:
                item = {"id": identity, "name": identity, "enabled": True}
                if kind == "anomaly_categories":
                    item["cooldown_hours"] = None
                options[kind].append(item)
    op.add_column("settings", sa.Column("account_options", JSONB, nullable=True))
    settings = sa.table("settings", sa.column("account_options", JSONB))
    connection.execute(settings.update().values(account_options=options))
    op.alter_column("settings", "account_options", nullable=False)
    op.drop_constraint(previous["name"], "accounts", type_="check")
    op.alter_column("accounts", "tier", type_=sa.String(64))
    op.create_check_constraint(CONSTRAINT, "accounts", "length(tier) BETWEEN 1 AND 64")
    comment(connection, CONSTRAINT, json.dumps(metadata))


def downgrade():
    connection = op.get_bind()
    metadata = json.loads(read_comment(connection, CONSTRAINT) or "null")
    if not metadata or metadata.get("revision") != revision:
        raise RuntimeError("Tier rollback metadata is missing; restore a backup")
    # Never truncate new identifiers or silently assign accounts to another tier.
    if connection.scalar(
        sa.text(
            f"SELECT EXISTS(SELECT 1 FROM accounts WHERE NOT ({metadata['sqltext']}) "
            "OR length(tier) > :length)"
        ),
        {"length": metadata["length"]},
    ):
        raise RuntimeError("Accounts use new tiers; reassign them before downgrading")
    op.drop_constraint(CONSTRAINT, "accounts", type_="check")
    op.alter_column("accounts", "tier", type_=sa.String(metadata["length"]))
    op.create_check_constraint(metadata["name"], "accounts", metadata["sqltext"])
    comment(connection, metadata["name"], metadata["comment"])
    op.drop_column("settings", "account_options")
