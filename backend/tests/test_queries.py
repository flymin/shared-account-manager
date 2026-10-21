"""Query budgets and permission-scoped paging on realistic, fictional data."""

from contextlib import contextmanager
from datetime import timedelta
import json

import pytest
from sqlalchemy import event, literal, select, text
from sqlalchemy.dialects import postgresql

from app import queries
from app.config import cipher
from app.db import Session, engine
from app.models import (
    Account,
    AccountGroup,
    AccountNote,
    AccountUser,
    Claim,
    Event,
    Group,
    User,
    UserGroup,
    now,
)


@contextmanager
def selects():
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def seed(count, *, claims=False):
    encrypted = cipher().encrypt(b"Fictional-Account-Credential").decode()
    with Session.begin() as db:
        actor = db.scalar(select(User).where(User.username == "admin"))
        groups = [Group(name=f"Fixture group {i}") for i in range(2)]
        db.add_all(groups)
        accounts = [
            Account(
                email=f"account{i}@example.test",
                tier="5x",
                mail_tool="mailcom",
                password_encrypted=encrypted,
                auth_password_encrypted=encrypted,
                quota=i % 101,
                capacity=2,
            )
            for i in range(count)
        ]
        db.add_all(accounts)
        db.flush()
        ids = [a.id for a in accounts]
        for a in accounts:
            db.add_all(AccountGroup(account_id=a.id, group_id=g.id) for g in groups)
            db.add(AccountUser(account_id=a.id, user_id=actor.id))
            db.add(
                AccountNote(
                    account_id=a.id, author_id=actor.id, content="Fictional note"
                )
            )
            for i in range(5):
                db.add(
                    Event(
                        kind="quota_updated",
                        account_id=a.id,
                        actor_id=actor.id,
                        details={"quota": i},
                    )
                )
            if claims:
                db.add(Claim(account_id=a.id, user_id=actor.id))
        return ids


@pytest.mark.parametrize("count", [1, 58, 500, 1000])
def test_accounts_have_fixed_query_budget_and_bounded_recent_events(admin, count):
    ids = seed(count)
    with selects() as sql:
        response = admin.get(
            "/api/v1/accounts?sort_by=quota&order=desc&offset=0&limit=20"
        )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == str(count)
    assert len(response.json()) == min(20, count)
    assert len(sql) <= 14
    assert all("password_encrypted" not in statement for statement in sql)
    for item in response.json():
        assert item["note_count"] == 1
        assert [e["details"]["quota"] for e in item["recent_updates"]] == [4, 3, 2]
    with selects() as sql:
        legacy = admin.get("/api/v1/accounts")
    assert len(sql) <= 14
    assert {item["id"] for item in legacy.json()} == set(ids)


def test_current_and_historical_claims_use_batches(admin):
    ids = seed(30, claims=True)
    with selects() as sql:
        response = admin.get("/api/v1/claims?scope=all&limit=50")
    assert response.status_code == 200
    assert len(response.json()) == 30
    assert len(sql) <= 16
    with Session.begin() as db:
        for claim in db.scalars(select(Claim)):
            claim.returned_at = now()
    with selects() as sql:
        response = admin.get("/api/v1/claims?scope=all&history=true&limit=20")
    assert response.headers["X-Total-Count"] == "30"
    assert len(response.json()) == 20
    assert {item["account"]["id"] for item in response.json()} <= set(ids)
    assert len(sql) <= 6


def test_recent_quota_reads_remain_bounded_with_large_mixed_history(admin):
    ids = seed(2)
    with Session.begin() as db:
        # Newer non-quota history must not force a scan past thousands of rows.
        db.execute(
            text(
                "INSERT INTO events (account_id,kind,details,created_at) "
                "SELECT a.id, CASE WHEN n <= 20000 THEN 'quota_updated' "
                "ELSE 'health_reported' END, jsonb_build_object('sequence',n), now() "
                "FROM accounts a CROSS JOIN generate_series(1,40000) n "
                "WHERE a.id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        db.execute(text("ANALYZE events"))
        query = queries.recent_quota_events(ids)
        rows = list(db.scalars(query))
        assert len(rows) == 6
        for aid in ids:
            assert [e.details["sequence"] for e in rows if e.account_id == aid] == [
                20000,
                19999,
                19998,
            ]
        # Exercise PostgreSQL's generic prepared plan too: the fixed quota
        # predicate must still select the partial index and stop at three rows.
        dialect = postgresql.dialect(paramstyle="numeric_dollar")
        compiled = query.compile(dialect=dialect)
        arguments = ", ".join(
            str(
                literal(compiled.params[name], type_=compiled.binds[name].type).compile(
                    dialect=dialect, compile_kwargs={"literal_binds": True}
                )
            )
            for name in compiled.positiontup
        )
        db.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        db.execute(text("PREPARE recent_quota_probe AS " + str(compiled)))
        try:
            plan = db.scalar(
                text(
                    "EXPLAIN (ANALYZE, FORMAT JSON) EXECUTE recent_quota_probe("
                    + arguments
                    + ")"
                )
            )
        finally:
            db.execute(text("DEALLOCATE recent_quota_probe"))
        if isinstance(plan, str):
            plan = json.loads(plan)

        def nodes(node):
            yield node
            for child in node.get("Plans", []):
                yield from nodes(child)

        reads = [
            n for n in nodes(plan[0]["Plan"]) if n.get("Relation Name") == "events"
        ]
        assert len(reads) == 1
        assert reads[0]["Index Name"] == "ix_events_quota_account_id_id"
        assert reads[0]["Actual Rows"] == 3
        assert reads[0]["Actual Loops"] == 2
        assert reads[0].get("Rows Removed by Filter", 0) == 0


def test_user_group_audit_settings_and_overview_budgets(admin):
    seed(25)
    with Session.begin() as db:
        original = db.scalar(select(User).where(User.username == "admin"))
        users = [
            User(
                username=f"fixture{i}",
                display_name=f"Fixture {i}",
                password_hash=original.password_hash,
                role="user",
                must_change_password=False,
            )
            for i in range(30)
        ]
        db.add_all(users)
        db.flush()
        groups = list(db.scalars(select(Group)))
        db.add_all(
            UserGroup(user_id=u.id, group_id=g.id) for u in users for g in groups
        )
    for path, budget in [
        ("users?limit=10", 7),
        ("groups?limit=10", 5),
        ("audit-events?limit=50", 4),
        ("settings", 3),
        ("overview", 5),
        ("users?view=options", 2),
        ("groups?view=options", 2),
    ]:
        with selects() as sql:
            response = admin.get("/api/v1/" + path)
        assert response.status_code == 200, path
        assert len(sql) <= budget, (path, len(sql))
    assert set(admin.get("/api/v1/users?view=options").json()[0]) == {
        "id",
        "username",
        "display_name",
    }
    assert set(admin.get("/api/v1/groups?view=options").json()[0]) == {"id", "name"}


def test_filters_count_only_authorized_unique_accounts(admin, make_user):
    ids = seed(12)
    user, client = make_user()
    with Session.begin() as db:
        groups = list(db.scalars(select(Group)))
        db.add_all(UserGroup(user_id=user["id"], group_id=g.id) for g in groups)
        db.add(AccountUser(account_id=ids[0], user_id=user["id"]))
        db.get(Account, ids[1]).disabled = True
        db.get(Account, ids[2]).expires_at = now() - timedelta(days=1)
        foreign = Account(
            email="foreign@example.test",
            tier="20x",
            password_encrypted="unread",
            auth_password_encrypted="unread",
        )
        db.add(foreign)
    response = client.get("/api/v1/accounts?scope=hall&limit=2&offset=50")
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "10"
    assert len(client.get("/api/v1/accounts").json()) == 12
    assert client.get("/api/v1/accounts?statuses=").json() == []
    assert (
        client.get("/api/v1/accounts?q=foreign&limit=20").headers["X-Total-Count"]
        == "0"
    )
    assert len(admin.get("/api/v1/accounts?statuses=disabled&limit=20").json()) == 1
    assert client.get("/api/v1/users?view=options").status_code == 403
    assert client.get("/api/v1/groups?view=options").status_code == 403


def test_server_sort_preserves_natural_and_null_ordering(admin):
    ids = seed(12)
    with Session.begin() as db:
        for a in db.scalars(select(Account)):
            a.quota = 50
        db.get(Account, ids[2]).quota = None
        db.get(Account, ids[3]).reset_at = now() + timedelta(days=2)
        db.get(Account, ids[4]).reset_at = now() + timedelta(days=1)
    names = [
        a["email"] for a in admin.get("/api/v1/accounts?sort_by=email&order=asc").json()
    ]
    assert names.index("account2@example.test") < names.index("account10@example.test")
    for order in ("asc", "desc"):
        items = admin.get(f"/api/v1/accounts?sort_by=quota&order={order}").json()
        assert items[-1]["quota"] is None
    ascending = admin.get("/api/v1/accounts?sort_by=reset_at&order=asc").json()
    descending = admin.get("/api/v1/accounts?sort_by=reset_at&order=desc").json()
    assert ascending[0]["reset_at"] is None
    assert descending[-1]["reset_at"] is None
    assert [a["id"] for a in ascending[-2:]] == [ids[4], ids[3]]


def test_status_filters_match_display_priority_without_duplicate_rows(admin):
    ids = seed(6)
    with Session.begin() as db:
        for a in db.scalars(select(Account)):
            a.quota = 50
        db.get(Account, ids[0]).disabled = True
        db.get(Account, ids[0]).health = "abnormal"
        db.get(Account, ids[1]).health = "abnormal"
        db.get(Account, ids[1]).quota = 0
        db.get(Account, ids[2]).health = "possibly_recovered"
        db.get(Account, ids[2]).quota = 0
        db.get(Account, ids[3]).quota = 0
        db.get(Account, ids[4]).capacity = 1
        actor = db.scalar(select(User).where(User.username == "admin"))
        db.add(Claim(account_id=ids[4], user_id=actor.id))
    for state, identity in zip(
        ["disabled", "abnormal", "possibly_recovered", "empty", "full", "normal"], ids
    ):
        response = admin.get(f"/api/v1/accounts?statuses={state}&limit=20")
        assert [a["id"] for a in response.json()] == [identity]
        assert response.headers["X-Total-Count"] == "1"
    response = admin.get("/api/v1/accounts?statuses=full,normal,normal&limit=20")
    assert {a["id"] for a in response.json()} == set(ids[4:])
    assert response.headers["X-Total-Count"] == "2"


@pytest.mark.parametrize(
    "query",
    [
        "statuses=unknown",
        "sort_by=unsafe",
        "order=unsafe",
        "limit=0",
        "limit=1001",
        "offset=-1",
    ],
)
def test_invalid_list_queries_are_rejected(admin, query):
    assert admin.get("/api/v1/accounts?" + query).status_code == 422


def test_import_queries_are_bounded_and_duplicates_include_soft_deleted(admin):
    seed(1)
    with Session.begin() as db:
        db.scalar(select(Account)).deleted = True
    payload = {
        "tier": "5x",
        "text": "\n".join(
            f"batch{i}@example.test----Fictional-Password" for i in range(100)
        ),
    }
    with selects() as sql:
        response = admin.post("/api/v1/account-imports/preview", json=payload)
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 100
    assert len(sql) <= 7
    with selects() as sql:
        response = admin.post("/api/v1/account-imports", json=payload)
    assert response.status_code == 201
    assert response.json()["count"] == 100
    assert len(sql) <= 7
    payload["text"] = "account0@example.test----Fictional-Password"
    assert (
        admin.post("/api/v1/account-imports/preview", json=payload).json()["errors"][0][
            "message"
        ]
        == "邮箱重复（包含已删除账号）"
    )
