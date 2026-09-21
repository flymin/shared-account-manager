from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import Session
from app.models import Account, Event, now
from test_business import P, run_clock


def test_global_interval_override_and_changes_preserve_anchor(admin, make_account):
    inherited = make_account(email="inherited@example.test")
    overridden = make_account(
        email="override@example.test", quota_reset_interval_days=3
    )
    deadline = now() + timedelta(hours=1)
    for account in (inherited, overridden):
        assert (
            admin.post(
                P + f"/accounts/{account['id']}/quota-reports",
                json={"quota": 0, "reset_at": deadline.isoformat()},
            ).status_code
            == 201
        )
    cfg = admin.get(P + "/settings").json()
    cfg["quota_reset_interval_days"] = 14
    assert admin.put(P + "/settings", json=cfg).status_code == 200
    # An older client that omits the interval cannot restore the default.
    del cfg["quota_reset_interval_days"]
    assert (
        admin.put(P + "/settings", json=cfg).json()["quota_reset_interval_days"] == 14
    )
    for account in (inherited, overridden):
        current = admin.get(P + f"/accounts/{account['id']}").json()
        assert datetime.fromisoformat(current["reset_at"]) == deadline
    run_clock(deadline)
    for account, days in ((inherited, 14), (overridden, 3)):
        current = admin.get(P + f"/accounts/{account['id']}").json()
        assert current["quota"] == 100
        assert datetime.fromisoformat(current["reset_at"]) == deadline + timedelta(
            days=days
        )
    run_clock(deadline + timedelta(days=3))
    current = admin.get(P + f"/accounts/{overridden['id']}").json()
    assert datetime.fromisoformat(current["reset_at"]) == deadline + timedelta(days=6)
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "quota_reset")
            )
            == 3
        )


@pytest.mark.parametrize("value", [0, 366, True, 3.5])
def test_account_interval_validation_is_atomic(admin, make_account, value):
    account = make_account(quota_reset_interval_days=3)
    assert (
        admin.patch(
            P + f"/accounts/{account['id']}",
            json={"quota_reset_interval_days": value, "tier": "20x"},
        ).status_code
        == 422
    )
    current = admin.get(P + f"/accounts/{account['id']}").json()
    assert current["quota_reset_interval_days"] == 3 and current["tier"] == "5x"
    payload = {
        "tier": "5x",
        "text": "invalid@example.test----p----a",
        "quota_reset_interval_days": value,
    }
    for endpoint in ("/account-imports/preview", "/account-imports"):
        assert admin.post(P + endpoint, json=payload).status_code == 422
    assert len(admin.get(P + "/accounts").json()) == 1


def test_user_cannot_change_account_interval(admin, make_user, make_account):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    assert (
        client.patch(
            P + f"/accounts/{account['id']}", json={"quota_reset_interval_days": 1}
        ).status_code
        == 403
    )
    assert (
        admin.get(P + f"/accounts/{account['id']}").json()["quota_reset_interval_days"]
        is None
    )


def test_manual_reset_replacement_and_clearing_after_overdue_reset(admin, make_account):
    account = make_account()
    for replacement in (now() + timedelta(days=2), None):
        with Session.begin() as db:
            stored = db.get(Account, account["id"])
            stored.reset_at = now() - timedelta(minutes=1)
            stored.quota = 0
        assert (
            admin.post(
                P + f"/accounts/{account['id']}/quota-reports",
                json={
                    "quota": 25,
                    "reset_at": replacement.isoformat() if replacement else None,
                },
            ).status_code
            == 201
        )
        run_clock(now())
        current = admin.get(P + f"/accounts/{account['id']}").json()
        assert current["quota"] == 25 and current["quota_source"] == "user"
        actual = (
            datetime.fromisoformat(current["reset_at"]) if current["reset_at"] else None
        )
        assert actual == replacement
