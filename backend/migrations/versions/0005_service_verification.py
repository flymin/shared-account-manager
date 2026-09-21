"""Normalize the single service verification type without embedding vendor names."""

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
CONSTRAINT = "two_factors_kind_check"


def read_comment(connection, name):
    return connection.scalar(
        sa.text(
            "SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint "
            "WHERE conrelid = 'two_factors'::regclass AND conname = :name"
        ),
        {"name": name},
    )


def comment(connection, name, value):
    identifier = connection.dialect.identifier_preparer.quote(name)
    literal = sa.literal(value).compile(
        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
    )
    connection.exec_driver_sql(
        f"COMMENT ON CONSTRAINT {identifier} ON two_factors IS {literal}"
    )


def rename_type(connection, previous, current):
    connection.execute(
        sa.text("UPDATE two_factors SET kind = :current WHERE kind = :previous"),
        {"previous": previous, "current": current},
    )
    connection.execute(
        sa.text(
            "UPDATE events SET details = jsonb_set(details, '{target}', "
            "to_jsonb(CAST(:current AS text))) "
            "WHERE kind IN ('two_factor_updated', 'two_factor_removed') "
            "AND details->>'target' = :previous"
        ),
        {"previous": previous, "current": current},
    )


def upgrade():
    connection = op.get_bind()
    constraints = sa.inspect(connection).get_check_constraints("two_factors")
    if len(constraints) != 1 or "kind" not in constraints[0]["sqltext"]:
        raise RuntimeError("Unexpected verification constraint; migration aborted")
    constraint = constraints[0]
    values = set(re.findall(r"'([^']+)'", constraint["sqltext"]))
    service_types = values - {"mail"}
    if "mail" not in values or len(service_types) != 1:
        raise RuntimeError("Expected one service verification type and one mail type")
    previous = service_types.pop()
    # Keep rollback metadata in the database, including for installations whose
    # previous type was vendor-specific. No key, ciphertext or version changes.
    metadata = {
        "revision": revision,
        "previous": previous,
        "name": constraint["name"],
        "sqltext": constraint["sqltext"],
        "comment": read_comment(connection, constraint["name"]),
    }
    op.drop_constraint(constraint["name"], "two_factors", type_="check")
    rename_type(connection, previous, "service")
    op.create_check_constraint(CONSTRAINT, "two_factors", "kind IN ('service','mail')")
    comment(connection, CONSTRAINT, json.dumps(metadata))


def downgrade():
    connection = op.get_bind()
    metadata = json.loads(read_comment(connection, CONSTRAINT) or "null")
    if not metadata or metadata.get("revision") != revision:
        raise RuntimeError(
            "Verification rollback metadata is missing; restore a backup"
        )
    op.drop_constraint(CONSTRAINT, "two_factors", type_="check")
    rename_type(connection, "service", metadata["previous"])
    op.create_check_constraint(metadata["name"], "two_factors", metadata["sqltext"])
    comment(connection, metadata["name"], metadata["comment"])
