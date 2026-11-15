import csv
import io
from datetime import timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app import queries
from app.db import Session
from app.models import Account, now

P = "/api/v1"


def test_expired_is_an_overlapping_filter_and_preserves_disabled(
    admin, make_account, monkeypatch
):
    stamp = now()
    monkeypatch.setattr(queries, "now", lambda: stamp)
    accounts = [make_account(email=f"expiry{i}@example.test") for i in range(7)]
    with Session.begin() as db:
        for i, account in enumerate(accounts):
            row = db.get(Account, account["id"])
            row.expires_at = (
                stamp - timedelta(days=1)
                if i < 3 or i == 6
                else stamp
                if i == 3
                else stamp + timedelta(days=1)
                if i == 4
                else None
            )
            row.disabled = i == 1 or i == 4
            if i == 2:
                row.health = "abnormal"
            if i == 6:
                row.deleted = True

    def ids(statuses):
        response = admin.get(
            P + "/accounts", params={"statuses": statuses, "limit": 20}
        )
        assert response.status_code == 200
        assert len(response.json()) == int(response.headers["X-Total-Count"])
        return [a["id"] for a in response.json()]

    expired = {a["id"] for a in accounts[:4]}
    assert set(ids("expired")) == expired
    assert set(ids("disabled")) == {accounts[1]["id"], accounts[4]["id"]}
    both = ids("expired,disabled")
    assert len(both) == len(set(both)) == 5
    assert set(both) == expired | {accounts[4]["id"]}
    assert set(ids("normal")) == {
        accounts[0]["id"],
        accounts[3]["id"],
        accounts[5]["id"],
    }
    assert ids("") == []
    # The five ordinary hall states keep their original optimized list behavior;
    # an expiration filter can never bring expired accounts into the hall.
    hall = admin.get(P + "/accounts", params={"scope": "hall", "statuses": "expired"})
    assert hall.json() == []


def test_expired_selection_edit_export_and_quota_reset_share_the_same_scope(
    admin, make_account
):
    group = admin.post(P + "/groups", json={"name": "Expiration group"}).json()
    past = now() - timedelta(days=1)
    rows = [
        make_account(
            email=f"selected{i}@example.test",
            groups=[group["id"]],
            expires_at=past.isoformat(),
        )
        for i in range(22)
    ]
    outside = make_account(email="outside@example.test")
    with Session.begin() as db:
        for row in db.scalars(
            select(Account).where(Account.id.in_([a["id"] for a in rows]))
        ):
            row.quota_updated_at = past - timedelta(days=1)
            row.reset_at = now() + timedelta(days=1)
        db.get(Account, rows[0]["id"]).disabled = True
    filters = {
        "q": "selected",
        "tier": "5x",
        "group_id": group["id"],
        "statuses": "expired",
    }
    response = admin.get(P + "/accounts", params={**filters, "offset": 20, "limit": 20})
    assert len(response.json()) == 2 and response.headers["X-Total-Count"] == "22"
    selection = admin.post(P + "/account-selections", json={"filters": filters}).json()
    expected = {a["id"] for a in rows}
    assert set(selection["account_ids"]) == expected
    exported = admin.get(P + "/accounts/export.csv", params=filters)
    assert exported.status_code == 200
    assert {
        r["账号邮箱"]
        for r in csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")))
    } == {a["email"] for a in rows}
    preview = admin.post(
        P + "/account-quota-resets/preview",
        json={"filters": filters, "occurred_at": past.isoformat()},
    )
    assert preview.json()["eligible_count"] == 22
    edit = {
        "account_ids": selection["account_ids"],
        "filters": filters,
        "changes": {"capacity": 3},
    }
    version = admin.post(P + "/account-bulk-edits/preview", json=edit).json()[
        "revision"
    ]
    committed = admin.post(
        P + "/account-bulk-edits",
        json={**edit, "revision": version, "request_id": str(uuid4())},
    )
    assert committed.status_code == 201 and committed.json()["count"] == 22
    with Session() as db:
        assert all(
            a.capacity == 3
            for a in db.scalars(select(Account).where(Account.id.in_(expected)))
        )
        assert db.get(Account, outside["id"]).capacity is None


def test_expiration_uses_aware_instant_and_null_means_never(
    admin, make_account, monkeypatch
):
    stamp = now()
    monkeypatch.setattr(queries, "now", lambda: stamp)
    account = make_account(
        expires_at=stamp.astimezone(timezone(timedelta(hours=8))).isoformat()
    )
    make_account(email="never@example.test")
    assert [a["id"] for a in admin.get(P + "/accounts?statuses=expired").json()] == [
        account["id"]
    ]
    monkeypatch.setattr(queries, "now", lambda: stamp - timedelta(microseconds=1))
    assert admin.get(P + "/accounts?statuses=expired").json() == []
