from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.db import Session, engine
from app.main import app
from app.models import Account, Event, Settings, now
from test_business import P


def seed(make_account, occurred, **options):
    account = make_account(**options)
    with Session.begin() as db:
        stored = db.get(Account, account["id"])
        stored.quota = 20
        stored.quota_updated_at = occurred - timedelta(days=1)
        stored.reset_at = occurred + timedelta(days=1)
    return account


def payload(occurred, **filters):
    return {
        "occurred_at": occurred.isoformat(),
        "filters": filters,
        "request_id": str(uuid4()),
    }


def preview(admin, body):
    return admin.post(
        P + "/account-quota-resets/preview",
        json={k: v for k, v in body.items() if k != "request_id"},
    )


def execute(admin, body):
    return admin.post(P + "/account-quota-resets", json=body)


def test_strict_cutoff_audit_and_absent_values(admin, make_account):
    d = now() - timedelta(hours=2)
    accounts = [
        seed(make_account, d, email=f"account{i}@example.test") for i in range(9)
    ]
    with Session.begin() as db:
        db.get(Account, accounts[1]["id"]).quota_updated_at = d
        db.get(Account, accounts[2]["id"]).quota_updated_at = d + timedelta(minutes=1)
        db.get(Account, accounts[3]["id"]).quota_updated_at = None
        db.get(Account, accounts[4]["id"]).reset_at = None
        db.get(Account, accounts[5]["id"]).deleted = True
        db.get(Account, accounts[6]["id"]).disabled = True
        db.get(Account, accounts[7]["id"]).expires_at = d - timedelta(days=1)
        db.get(Account, accounts[8]["id"]).health = "abnormal"
    body = payload(d)
    assert preview(admin, body).json() == {
        "matched_count": 8,
        "eligible_count": 4,
        "skipped_count": 4,
    }
    before = now()
    result = execute(admin, body)
    assert result.status_code == 201, result.text
    assert result.json()["reset_count"] == 4
    for index, account in enumerate(accounts):
        with Session() as db:
            stored = db.get(Account, account["id"])
            if index not in (0, 6, 7, 8):
                assert stored.quota == 20
                continue
            assert stored.quota == 100 and stored.quota_updated_at == d
            assert stored.reset_at == d + timedelta(days=7)
            assert stored.quota_source == "bulk_reset"
            events = list(
                db.scalars(
                    select(Event).where(
                        Event.account_id == stored.id, Event.kind == "quota_updated"
                    )
                )
            )
            assert len(events) == 1
            assert events[0].created_at >= before and events[0].actor_id
            assert datetime.fromisoformat(events[0].details["occurred_at"]) == d
        current = admin.get(P + f"/accounts/{account['id']}").json()
        assert current["recent_updates"][0]["actor"] == "一键重置"
        history = admin.get(P + f"/accounts/{account['id']}/events").json()
        assert (
            next(e for e in history if e["kind"] == "quota_updated")["actor"]
            == "一键重置"
        )
    assert (
        admin.get(P + f"/accounts/{accounts[8]['id']}").json()["health"] == "abnormal"
    )
    assert execute(admin, body).json() == result.json()
    changed_body = {**body, "occurred_at": (d - timedelta(minutes=1)).isoformat()}
    assert execute(admin, changed_body).status_code == 409
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "quota_bulk_reset")
            )
            == 1
        )


def test_global_override_and_overdue_cycle_anchor(admin, make_account):
    d = now() - timedelta(days=22)
    inherited = seed(make_account, d, email="inherited@example.test")
    override = seed(
        make_account, d, email="overridden@example.test", quota_reset_interval_days=3
    )
    with Session.begin() as db:
        db.get(Settings, 1).quota_reset_interval_days = 14
    assert execute(admin, payload(d)).status_code == 201
    with Session() as db:
        assert db.get(Account, inherited["id"]).reset_at == d + timedelta(days=28)
        assert db.get(Account, override["id"]).reset_at == d + timedelta(days=24)
        assert db.get(Account, override["id"]).quota_reset_interval_days == 3


def test_filters_apply_beyond_one_page_and_keep_empty_selection(admin, make_account):
    d = now() - timedelta(hours=1)
    group = admin.post(P + "/groups", json={"name": "Test selection"}).json()
    ids = [
        seed(make_account, d, email=f"selected{i}@example.test", groups=[group["id"]])[
            "id"
        ]
        for i in range(22)
    ]
    other = seed(make_account, d, email="other@example.test")
    body = payload(d, q="selected", tier="5x", group_id=group["id"], statuses="normal")
    page = admin.get(
        P
        + f"/accounts?q=selected&tier=5x&group_id={group['id']}&statuses=normal&offset=20&limit=20"
    )
    assert len(page.json()) == 2
    assert preview(admin, body).json()["eligible_count"] == 22
    assert execute(admin, body).json()["reset_count"] == 22
    with Session() as db:
        assert all(
            a.quota == 100
            for a in db.scalars(select(Account).where(Account.id.in_(ids)))
        )
        assert db.get(Account, other["id"]).quota == 20
    assert execute(admin, payload(d, statuses="")).json()["matched_count"] == 0
    assert preview(admin, payload(d, statuses="invalid")).status_code == 422


def test_rechecks_updated_rows_after_preview(admin, make_account):
    d = now() - timedelta(hours=1)
    account = seed(make_account, d)
    body = payload(d)
    assert preview(admin, body).json()["eligible_count"] == 1
    assert (
        admin.post(
            P + f"/accounts/{account['id']}/quota-reports", json={"quota": 32}
        ).status_code
        == 201
    )
    result = execute(admin, body).json()
    assert result["reset_count"] == 0 and result["skipped_count"] == 1
    assert admin.get(P + f"/accounts/{account['id']}").json()["quota"] == 32


def test_permissions_time_validation_and_pagination_rejection(
    admin, make_user, make_account
):
    d = now() - timedelta(hours=1)
    seed(make_account, d)
    _, user = make_user()
    body = payload(d)
    for suffix in ("", "/preview"):
        data = (
            body if not suffix else {k: v for k, v in body.items() if k != "request_id"}
        )
        path = P + "/account-quota-resets" + suffix
        assert TestClient(app).post(path, json=data).status_code == 401
        assert user.post(path, json=data).status_code == 403
        assert (
            admin.post(path, json=data, headers={"X-CSRF-Token": "wrong"}).status_code
            == 403
        )
    for bad in (now() + timedelta(days=1), d.replace(tzinfo=None)):
        assert execute(admin, payload(bad)).status_code == 422
    for key in ("offset", "limit", "sort_by"):
        assert execute(admin, payload(d, **{key: 1})).status_code == 422
    assert preview(admin, body).json()["eligible_count"] == 1


def test_idempotent_parallel_submission_and_transactional_failure(
    admin, make_account, monkeypatch
):
    d = now() - timedelta(hours=1)
    account = seed(make_account, d)
    body = payload(d)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(admin, body), range(2)))
    assert all(r.status_code == 201 for r in results)
    assert results[0].json() == results[1].json()
    with Session() as db:
        assert db.get(Account, account["id"]).quota_version == 1
    # Failing an audit insertion must also roll back the account update.
    d2 = now() - timedelta(minutes=1)

    def reject_audit(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO events"):
            raise RuntimeError("Synthetic audit failure")

    event.listen(engine, "before_cursor_execute", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="Synthetic audit failure"):
            execute(admin, payload(d2))
    finally:
        event.remove(engine, "before_cursor_execute", reject_audit)
    with Session() as db:
        assert db.get(Account, account["id"]).quota_updated_at == d


@pytest.mark.parametrize("sort_key", ["quota_updated_at", "expires_at"])
def test_new_date_sorts_are_stable_and_nulls_last(admin, make_account, sort_key):
    stamp = now()
    rows = [
        make_account(email=email)
        for email in (
            "item10@example.test",
            "item2@example.test",
            "unknown@example.test",
            "early@example.test",
        )
    ]
    with Session.begin() as db:
        for i, row in enumerate(rows):
            setattr(
                db.get(Account, row["id"]),
                sort_key,
                None if i == 2 else stamp - timedelta(days=1 if i == 3 else 0),
            )
    for order, names in (
        ("asc", ["early", "item2", "item10", "unknown"]),
        ("desc", ["item2", "item10", "early", "unknown"]),
    ):
        emails = []
        for offset in (0, 2):
            response = admin.get(
                P
                + f"/accounts?sort_by={sort_key}&order={order}&offset={offset}&limit=2"
            )
            assert response.headers["X-Total-Count"] == "4"
            emails.extend(a["email"].split("@")[0] for a in response.json())
        assert emails == names


def test_worker_reset_after_preview_is_preserved(admin, make_account):
    from test_business import run_clock

    d = now() - timedelta(hours=1)
    account = seed(make_account, d)
    with Session.begin() as db:
        db.get(Account, account["id"]).reset_at = now() - timedelta(minutes=1)
    body = payload(d)
    assert preview(admin, body).json()["eligible_count"] == 1
    run_clock(now())
    before = admin.get(P + f"/accounts/{account['id']}").json()
    assert execute(admin, body).json()["reset_count"] == 0
    after = admin.get(P + f"/accounts/{account['id']}").json()
    assert after == before


def test_exact_cycle_boundary_uses_utc_and_fixed_query_budget(
    admin, make_account, monkeypatch
):
    from sqlalchemy import text
    from app import quota_resets

    d = now() - timedelta(days=7)
    ids = [
        seed(make_account, d, email=f"budget{i}@example.test")["id"] for i in range(25)
    ]
    monkeypatch.setattr(quota_resets, "now", lambda: d + timedelta(days=7))
    original = quota_resets.selection

    def selection_in_other_zone(db, *args):
        db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        return original(db, *args)

    monkeypatch.setattr(quota_resets, "selection", selection_in_other_zone)
    statements = []

    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = execute(admin, payload(d))
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 201, response.text
    assert response.json()["reset_count"] == 25
    assert len(statements) < 20
    with Session() as db:
        assert all(
            a.reset_at == d + timedelta(days=14)
            for a in db.scalars(select(Account).where(Account.id.in_(ids)))
        )
