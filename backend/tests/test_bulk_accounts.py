from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app import bulk_accounts
from app.db import Session, engine
from app.main import app
from app.models import Account, AccountNote, Event, now
from test_business import run_clock

P = "/api/v1"


def body(accounts, changes, filters=None):
    return {
        "account_ids": [a["id"] for a in accounts],
        "filters": filters or {},
        "changes": changes,
    }


def preview(admin, data):
    return admin.post(P + "/account-bulk-edits/preview", json=data)


def commit(admin, data):
    result = preview(admin, data)
    assert result.status_code == 200, result.text
    request = {
        **data,
        "revision": result.json()["revision"],
        "request_id": str(uuid4()),
    }
    return admin.post(P + "/account-bulk-edits", json=request), request


def test_selection_all_filters_and_intersection_across_pages(admin, make_account):
    group = admin.post(P + "/groups", json={"name": "Batch group"}).json()
    accounts = [
        make_account(email=f"chosen{i}@example.test", groups=[group["id"]])
        for i in range(23)
    ]
    outside = make_account(email="outside@example.test")
    with Session.begin() as db:
        db.get(Account, accounts[0]["id"]).disabled = True
        db.get(Account, accounts[1]["id"]).deleted = True
    filters = {
        "q": "chosen",
        "group_id": group["id"],
        "tier": "5x",
        "statuses": "normal",
    }
    selected = admin.post(P + "/account-selections", json={"filters": filters}).json()
    assert selected["count"] == 21
    assert set(selected["account_ids"]) == {a["id"] for a in accounts[2:]}
    retained = admin.post(
        P + "/account-selections",
        json={"filters": filters, "account_ids": [accounts[2]["id"], outside["id"]]},
    ).json()
    assert retained["account_ids"] == [accounts[2]["id"]]
    assert (
        admin.post(
            P + "/account-selections", json={"filters": {"statuses": ""}}
        ).json()["count"]
        == 0
    )
    # Actual IDs determine edits, independently of how they were selected.
    data = body(accounts[2:], {"capacity": 4}, filters)
    response, _ = commit(admin, data)
    assert response.status_code == 201 and response.json()["count"] == 21
    assert admin.get(P + f"/accounts/{outside['id']}").json()["capacity"] is None


def test_combined_metadata_quota_health_note_and_acl_edit(
    admin, make_account, make_user
):
    user, client = make_user()
    group = admin.post(P + "/groups", json={"name": "Replacement"}).json()
    accounts = [
        make_account(email=f"account{i}@example.test", users=[user["id"]])
        for i in range(2)
    ]
    claimed = client.post(P + "/claims", json={"account_id": accounts[0]["id"]}).json()
    future = now() + timedelta(days=3)
    changes = {
        "tier": "20x",
        "capacity": 1,
        "quota_reset_interval_days": 3,
        "expires_at": future.isoformat(),
        "mail_tool": None,
        "enabled": False,
        "group_ids": [group["id"]],
        "user_ids": [],
        "quota": 0,
        "reset_at": future.isoformat(),
        "health": {"action": "report", "note": "Synthetic batch anomaly"},
        "note": "Batch note",
    }
    response, request = commit(admin, body(accounts, changes))
    assert response.status_code == 201, response.text
    for a in accounts:
        current = admin.get(P + f"/accounts/{a['id']}").json()
        assert current["tier"] == "20x" and current["disabled"] is True
        assert current["capacity"] == 1 and current["quota_reset_interval_days"] == 3
        assert current["mail_tool"] is None and current["quota"] == 0
        assert (
            current["health"] == "abnormal"
            and current["health_note"] == "Synthetic batch anomaly"
        )
        assert current["group_ids"] == [group["id"]] and current["user_ids"] == []
        assert (
            current["expires_at"] == future.isoformat()
            and current["reset_at"] == future.isoformat()
        )
        assert current["note_count"] == 1
        credentials = admin.get(P + f"/accounts/{a['id']}/credentials").json()
        assert (
            credentials["password"] == "fictional-password"
            and credentials["auth_password"] == "fictional-auth"
        )
    assert client.get(P + "/claims").json()[0]["id"] == claimed["id"]
    assert client.get(P + "/claims").json()[0]["invalidated_at"] is None
    assert admin.post(P + "/account-bulk-edits", json=request).json() == response.json()
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AccountNote)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "accounts_bulk_updated")
            )
            == 1
        )


def test_clear_values_and_schedule_only_preserves_quota_feedback(admin, make_account):
    a = make_account(capacity=4, quota_reset_interval_days=3)
    future = now() + timedelta(days=4)
    admin.post(
        P + f"/accounts/{a['id']}/quota-reports",
        json={"quota": 27, "reset_at": future.isoformat()},
    )
    stamp = admin.get(P + f"/accounts/{a['id']}").json()["quota_updated_at"]
    response, _ = commit(
        admin,
        body(
            [a],
            {
                "capacity": None,
                "quota_reset_interval_days": None,
                "expires_at": None,
                "group_ids": [],
                "user_ids": [],
                "reset_at": None,
                "health": {"action": "clear"},
            },
        ),
    )
    assert response.status_code == 201, response.text
    current = admin.get(P + f"/accounts/{a['id']}").json()
    assert current["quota"] == 27 and current["quota_updated_at"] == stamp
    assert (
        current["reset_at"] is None
        and current["capacity"] is None
        and current["health"] == "normal"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"password": "forbidden"},
        {"auth_password": "forbidden"},
        {"email": "other@example.test"},
        {"two_factor": "bad"},
        {"tier": None},
        {"quota": None},
        {"group_ids": None},
        {"note": " "},
        {"capacity": 0},
        {"quota_reset_interval_days": 366},
        {"health": {"action": "report"}},
        {"user_ids": ["missing"]},
        {"reset_at": "2020-01-01T00:00:00Z"},
        {},
    ],
)
def test_validation_and_prohibited_fields_are_atomic(admin, make_account, change):
    a = make_account()
    assert preview(admin, body([a], change)).status_code == 422
    assert admin.get(P + f"/accounts/{a['id']}").json()["capacity"] is None


@pytest.mark.parametrize(
    "concurrent", ["metadata", "note", "health", "quota", "deleted", "filter", "worker"]
)
def test_preview_conflicts_do_not_apply_partial_edits(admin, make_account, concurrent):
    accounts = [make_account(email=f"account{i}@example.test") for i in range(2)]
    data = body(accounts, {"capacity": 8}, {"tier": "5x"})
    if concurrent == "worker":
        with Session.begin() as db:
            db.get(Account, accounts[0]["id"]).reset_at = now() - timedelta(minutes=1)
    prior = preview(admin, data).json()
    a = accounts[0]
    if concurrent == "metadata":
        admin.patch(P + f"/accounts/{a['id']}", json={"capacity": 3})
    elif concurrent == "note":
        admin.post(P + f"/accounts/{a['id']}/notes", json={"content": "New"})
    elif concurrent == "health":
        admin.post(
            P + f"/accounts/{a['id']}/health-reports",
            json={"action": "report", "version": 0, "note": "New"},
        )
    elif concurrent == "quota":
        admin.post(P + f"/accounts/{a['id']}/quota-reports", json={"quota": 10})
    elif concurrent == "worker":
        run_clock(now())
    else:
        with Session.begin() as db:
            row = db.get(Account, a["id"])
            if concurrent == "deleted":
                row.deleted = True
            else:
                row.tier = "20x"
    response = admin.post(
        P + "/account-bulk-edits",
        json={**data, "revision": prior["revision"], "request_id": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert admin.get(P + f"/accounts/{accounts[1]['id']}").json()["capacity"] is None


def test_parallel_retry_and_error_roll_back_whole_batch(
    admin, make_account, monkeypatch
):
    accounts = [make_account(email=f"account{i}@example.test") for i in range(2)]
    data = body(accounts, {"note": "Once", "capacity": 5})
    request = {
        **data,
        "revision": preview(admin, data).json()["revision"],
        "request_id": str(uuid4()),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: admin.post(P + "/account-bulk-edits", json=request), range(2)
            )
        )
    assert all(r.status_code == 201 for r in results)
    assert results[0].json() == results[1].json()
    request["changes"] = {"note": "Different"}
    assert admin.post(P + "/account-bulk-edits", json=request).status_code == 409
    data = body(accounts, {"note": "Rollback", "capacity": 7})
    request = {
        **data,
        "revision": preview(admin, data).json()["revision"],
        "request_id": str(uuid4()),
    }
    original = bulk_accounts.insert

    def fail(model):
        if model is AccountNote:
            raise RuntimeError("Synthetic failure")
        return original(model)

    monkeypatch.setattr(bulk_accounts, "insert", fail)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        admin.post(P + "/account-bulk-edits", json=request)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AccountNote)) == 2
        assert all(a.capacity == 5 for a in db.scalars(select(Account)))


def test_endpoints_require_admin_and_csrf(admin, make_user, make_account):
    _, client = make_user()
    a = make_account()
    data = body([a], {"capacity": 5})
    for endpoint in (
        "/account-selections",
        "/account-bulk-edits/preview",
        "/account-bulk-edits",
    ):
        payload = {"filters": {}} if endpoint == "/account-selections" else data
        if endpoint.endswith("edits"):
            payload = {**data, "revision": "a" * 64, "request_id": str(uuid4())}
        assert TestClient(app).post(P + endpoint, json=payload).status_code == 401
        assert client.post(P + endpoint, json=payload).status_code == 403
        assert (
            admin.post(
                P + endpoint, json=payload, headers={"X-CSRF-Token": "wrong"}
            ).status_code
            == 403
        )


def test_batch_read_query_budget_does_not_grow_per_account(admin, make_account):
    accounts = [make_account(email=f"budget{i}@example.test") for i in range(25)]
    data = body(
        accounts, {"capacity": 5, "health": {"action": "report", "note": "Synthetic"}}
    )
    request = {
        **data,
        "revision": preview(admin, data).json()["revision"],
        "request_id": str(uuid4()),
    }
    reads = []

    def record(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = admin.post(P + "/account-bulk-edits", json=request)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 201, response.text
    assert len(reads) < 20


def test_tool_switch_cancels_runs_and_removes_old_authorization(admin, make_account):
    from app.config import cipher
    from app.models import EmailCodeRun, MailOAuthCredential, MailOAuthState

    a = make_account()
    user_id = admin.get(P + "/auth/me").json()["user"]["id"]
    encoded = cipher().encrypt(b"Synthetic-Only").decode()
    with Session.begin() as db:
        db.add(
            EmailCodeRun(
                id=str(uuid4()),
                account_id=a["id"],
                user_id=user_id,
                session_hash="synthetic",
                mail_tool="mailcom",
                tool_config_hash="a" * 64,
                mail_backend="mailcom",
                since=now(),
                deadline=now() + timedelta(minutes=5),
                code_encrypted=encoded,
            )
        )
        db.add(
            MailOAuthCredential(
                account_id=a["id"],
                backend_id="outlook",
                client_id_encrypted=encoded,
                tenant="consumers",
                refresh_token_encrypted=encoded,
                authorized_email=a["email"],
                updated_by=user_id,
            )
        )
        db.add(
            MailOAuthState(
                state_hash="b" * 64,
                account_id=a["id"],
                backend_id="outlook",
                admin_id=user_id,
                session_hash="synthetic",
                client_id_encrypted=encoded,
                tenant="consumers",
                code_verifier_encrypted=encoded,
                expires_at=now() + timedelta(minutes=5),
            )
        )
    response, _ = commit(admin, body([a], {"mail_tool": None}))
    assert response.status_code == 201, response.text
    with Session() as db:
        run = db.scalar(select(EmailCodeRun))
        assert run.status == "cancelled" and run.code_encrypted is None
        assert db.scalar(select(func.count()).select_from(MailOAuthCredential)) == 0
        assert db.scalar(select(func.count()).select_from(MailOAuthState)) == 0
        assert db.get(Account, a["id"]).mail_config_version == 1


def test_overdue_reset_settles_before_new_period_and_quota(admin, make_account):
    a = make_account(quota_reset_interval_days=3)
    deadline = now() - timedelta(hours=1)
    with Session.begin() as db:
        row = db.get(Account, a["id"])
        row.reset_at = deadline
        row.quota = 0
    response, _ = commit(
        admin, body([a], {"quota": 31, "quota_reset_interval_days": 14})
    )
    assert response.status_code == 201, response.text
    with Session() as db:
        row = db.get(Account, a["id"])
        assert row.quota == 31 and row.reset_at == deadline + timedelta(days=3)
        assert row.quota_reset_interval_days == 14
    events = admin.get(P + f"/accounts/{a['id']}/events").json()
    assert any(e["kind"] == "quota_reset" for e in events)
    assert (
        next(e for e in events if e["kind"] == "quota_updated")["details"]["quota"]
        == 31
    )


def test_acl_replacement_is_complete_across_write_chunks(
    admin, make_account, monkeypatch
):
    accounts = [make_account(email=f"chunk{i}@example.test") for i in range(3)]
    other = make_account(email="outside@example.test")
    groups = [
        admin.post(P + "/groups", json={"name": f"Chunk group {i}"}).json()["id"]
        for i in range(2)
    ]
    monkeypatch.setattr(bulk_accounts, "LINK_BATCH_SIZE", 2)
    response, _ = commit(admin, body(accounts, {"group_ids": [*groups, groups[0]]}))
    assert response.status_code == 201, response.text
    for a in accounts:
        assert set(admin.get(P + f"/accounts/{a['id']}").json()["group_ids"]) == set(
            groups
        )
    assert admin.get(P + f"/accounts/{other['id']}").json()["group_ids"] == []
