from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.db import Session, write_lock
from app.domain import reconcile
from app.main import app
from app.models import Account, Claim, Event, now
from conftest import PASSWORD, login

P = "/api/v1"


def claim(client, account, warning=False):
    return client.post(
        P + "/claims",
        json={"account_id": account["id"], "acknowledge_warning": warning},
    )


def normal_return(client, c, **extra):
    return client.put(
        P + f"/claims/{c['id']}/return",
        json={
            "kind": "normal",
            "quota": 0,
            "reset_at": (now() + timedelta(hours=4)).isoformat(),
            **extra,
        },
    )


def run_clock(stamp):
    with Session.begin() as db:
        write_lock(db)
        reconcile(db, stamp)


@pytest.mark.parametrize(
    "quota, depleted", [(None, False), (0, True), (4, True), (5, False), (6, False)]
)
def test_quota_threshold_boundaries_and_claim_views(
    admin, make_user, make_account, quota, depleted
):
    user, client = make_user()
    account = make_account(users=[user["id"]], capacity=2)
    if quota is not None:
        assert (
            admin.post(
                P + f"/accounts/{account['id']}/quota-reports", json={"quota": quota}
            ).status_code
            == 201
        )
    info = client.get(P + "/accounts?scope=hall").json()[0]
    assert info["quota"] == quota
    assert info["quota_depleted"] is depleted
    assert info["can_claim"] is True
    assert (
        client.get(P + f"/accounts/{account['id']}").json()["quota_depleted"]
        is depleted
    )
    assert admin.get(P + "/overview").json()["depleted"] == int(depleted)
    assert claim(client, account).status_code == 201
    assert client.get(P + "/claims").json()[0]["account"]["quota_depleted"] is depleted


def test_threshold_settings_apply_without_changing_quota_or_claims(
    admin, make_user, make_account
):
    user, client = make_user()
    account = make_account(users=[user["id"]], capacity=2)
    admin.post(P + f"/accounts/{account['id']}/quota-reports", json={"quota": 7})
    record = claim(client, account).json()
    cfg = admin.get(P + "/settings").json()
    assert cfg["quota_depleted_threshold"] == 5
    cfg["quota_depleted_threshold"] = 10
    assert client.put(P + "/settings", json=cfg).status_code == 403
    assert admin.put(P + "/settings", json=cfg).json() == cfg
    assert admin.get(P + "/settings").json()["quota_depleted_threshold"] == 10
    info = client.get(P + f"/accounts/{account['id']}").json()
    assert info["quota"] == 7 and info["quota_depleted"] is True
    assert info["active_count"] == 1
    assert client.get(P + "/claims").json()[0]["id"] == record["id"]
    assert admin.get(P + "/overview").json()["depleted"] == 1
    # Older clients updating unrelated settings must preserve the chosen threshold.
    del cfg["quota_depleted_threshold"]
    assert admin.put(P + "/settings", json=cfg).json()["quota_depleted_threshold"] == 10
    cfg["quota_depleted_threshold"] = 7
    assert admin.put(P + "/settings", json=cfg).status_code == 200
    assert (
        client.get(P + f"/accounts/{account['id']}").json()["quota_depleted"] is False
    )
    assert admin.get(P + "/overview").json()["depleted"] == 0


@pytest.mark.parametrize("threshold", [0, 101, 3.5, True])
def test_threshold_rejects_invalid_values(admin, threshold):
    cfg = admin.get(P + "/settings").json()
    cfg["quota_depleted_threshold"] = threshold
    assert admin.put(P + "/settings", json=cfg).status_code == 422
    assert admin.get(P + "/settings").json()["quota_depleted_threshold"] == 5


def test_quota_reset_interval_defaults_and_rejects_invalid_values(admin):
    cfg = admin.get(P + "/settings").json()
    assert cfg["quota_reset_interval_days"] == 7
    for value in [0, 366, 3.5, True]:
        changed = {**cfg, "quota_reset_interval_days": value}
        assert admin.put(P + "/settings", json=changed).status_code == 422
    assert admin.get(P + "/settings").json()["quota_reset_interval_days"] == 7


def test_quota_reset_interval_can_be_overridden_on_import_and_edit(admin, make_account):
    account = make_account(email="interval@example.test", quota_reset_interval_days=3)
    assert (
        admin.get(P + f"/accounts/{account['id']}").json()["quota_reset_interval_days"]
        == 3
    )
    assert (
        admin.patch(
            P + f"/accounts/{account['id']}",
            json={"quota_reset_interval_days": 14},
        ).status_code
        == 200
    )
    assert (
        admin.get(P + f"/accounts/{account['id']}").json()["quota_reset_interval_days"]
        == 14
    )
    assert (
        admin.patch(
            P + f"/accounts/{account['id']}",
            json={"quota_reset_interval_days": None},
        ).status_code
        == 200
    )
    assert (
        admin.get(P + f"/accounts/{account['id']}").json()["quota_reset_interval_days"]
        is None
    )


def test_claim_return_and_claim_again(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 403
    result = claim(c, a)
    assert result.status_code == 201
    record = result.json()
    assert claim(c, a).status_code == 409
    cred = c.get(P + f"/accounts/{a['id']}/credentials")
    assert cred.json()["auth_password"] == "fictional-auth"
    assert cred.headers["cache-control"] == "no-store"
    assert normal_return(c, record).status_code == 200
    assert normal_return(c, record).status_code == 200
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 0
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 403
    assert claim(c, a).status_code == 201  # depleted accounts are still claimable


def test_revoke_separates_account_and_user_quota(admin, make_user, make_account):
    u, c = make_user()
    v, other = make_user("bob")
    a = make_account(users=[u["id"], v["id"]])
    record = claim(c, a).json()
    assert (
        admin.put(
            P + f"/claims/{record['id']}/revocation", json={"reason": "测试回收"}
        ).status_code
        == 200
    )
    assert (
        admin.put(
            P + f"/claims/{record['id']}/revocation", json={"reason": "重复回收"}
        ).status_code
        == 200
    )
    assert admin.get(P + "/accounts").json()[0]["active_count"] == 0
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    pending = c.get(P + "/claims").json()[0]
    assert pending["account"] is None and "email" not in str(pending)
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 403
    assert claim(c, a).status_code == 409
    assert claim(other, a).status_code == 201
    assert (
        c.put(
            P + f"/claims/{record['id']}/return", json={"kind": "acknowledge"}
        ).status_code
        == 200
    )
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 0


def test_deleted_account_ack_and_deleted_user_auto_return(
    admin, make_user, make_account
):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    assert (
        admin.request(
            "DELETE", P + f"/accounts/{a['id']}", json={"reason": "过期清理"}
        ).status_code
        == 200
    )
    assert c.get(P + "/claims").json()[0]["invalidation_kind"] == "deleted"
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 404
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    assert (
        c.put(
            P + f"/claims/{record['id']}/return", json={"kind": "acknowledge"}
        ).status_code
        == 200
    )
    b = make_account(email="second@example.test", users=[u["id"]])
    claim(c, b)
    assert admin.delete(P + f"/users/{u['id']}").status_code == 200
    assert c.get(P + "/auth/me").status_code == 401
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Claim)
                .where(Claim.user_id == u["id"], Claim.returned_at.is_(None))
            )
            == 0
        )
    assert admin.get(P + "/accounts").json()[0]["active_count"] == 0


def test_group_union_direct_acl_and_retained_claim(admin, make_user, make_account):
    groups = [
        admin.post(P + "/groups", json={"name": n}).json() for n in ["研发", "测试"]
    ]
    u, c = make_user(groups=[g["id"] for g in groups])
    v, other = make_user("other")
    a = make_account(groups=[groups[1]["id"]])
    b = make_account(email="direct@example.test", users=[u["id"]])
    hidden = make_account(email="hidden@example.test")
    assert {a["id"], b["id"]} == {x["id"] for x in c.get(P + "/accounts").json()}
    assert other.get(P + "/accounts").json() == []
    assert other.get(P + f"/accounts/{hidden['id']}").status_code == 404
    record = claim(c, a).json()
    admin.patch(P + f"/users/{u['id']}", json={"group_ids": []})
    assert a["id"] not in [x["id"] for x in c.get(P + "/accounts").json()]
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 200
    assert (
        c.post(P + f"/accounts/{a['id']}/quota-reports", json={"quota": 25}).status_code
        == 201
    )
    assert normal_return(c, record).status_code == 200
    assert claim(c, a).status_code == 404


def test_expired_account_retains_current_user(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]], capacity=2)
    record = claim(c, a).json()
    admin.patch(
        P + f"/accounts/{a['id']}",
        json={"expires_at": (now() - timedelta(minutes=1)).isoformat()},
    )
    assert c.get(P + f"/accounts/{a['id']}/credentials").status_code == 200
    assert normal_return(c, record).status_code == 200
    response = claim(c, a)
    assert response.status_code == 409 and "已过期" in response.text


def test_abnormal_return_and_manual_recovery(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    assert (
        c.put(
            P + f"/claims/{record['id']}/return", json={"kind": "abnormal"}
        ).status_code
        == 422
    )
    assert (
        c.put(
            P + f"/claims/{record['id']}/return",
            json={
                "kind": "abnormal",
                "categories": ["at capacity", "降智"],
                "note": "输出异常",
            },
        ).status_code
        == 200
    )
    a = c.get(P + "/accounts").json()[0]
    assert a["health"] == "abnormal" and a["quota"] is None
    assert claim(c, a).status_code == 409
    record = claim(c, a, True).json()
    assert normal_return(c, record).status_code == 409
    assert (
        normal_return(
            c, record, health_version=a["health_version"], health_action="maintain"
        ).status_code
        == 200
    )
    a = c.get(P + "/accounts").json()[0]
    assert a["health_version"] == 2
    record = claim(c, a, True).json()
    assert (
        c.post(
            P + f"/accounts/{a['id']}/health-reports",
            json={"action": "clear", "version": 1},
        ).status_code
        == 409
    )
    assert (
        c.post(
            P + f"/accounts/{a['id']}/health-reports",
            json={"action": "clear", "version": 2},
        ).status_code
        == 201
    )
    assert c.get(P + "/accounts").json()[0]["health"] == "normal"
    assert c.get(P + "/accounts").json()[0]["quota"] == 0
    assert normal_return(c, record).status_code == 200


def test_observation_cooldown_and_new_anomaly_versions(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]], capacity=2)
    admin.post(
        P + f"/accounts/{a['id']}/health-reports",
        json={"action": "report", "version": 0, "categories": ["降智"]},
    )
    claim(c, a, True)
    start = now()
    run_clock(start + timedelta(hours=4, seconds=1))
    assert c.get(P + "/accounts").json()[0]["health"] == "possibly_recovered"
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    admin.post(
        P + f"/accounts/{a['id']}/health-reports",
        json={"action": "report", "version": 1, "note": "新异常"},
    )
    run_clock(now() + timedelta(hours=5))
    assert (
        c.get(P + "/accounts").json()[0]["health"] == "abnormal"
    )  # old claim cannot clear new report
    run_clock(now() + timedelta(hours=25))
    run_clock(now() + timedelta(hours=26))
    assert c.get(P + "/accounts").json()[0]["health"] == "possibly_recovered"
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "health_possible")
            )
            == 2
        )


def test_returned_or_revoked_claim_does_not_trigger_observation(
    admin, make_user, make_account
):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    admin.post(
        P + f"/accounts/{a['id']}/health-reports",
        json={"action": "report", "version": 0, "note": "测试异常"},
    )
    record = claim(c, a, True).json()
    admin.put(P + f"/claims/{record['id']}/revocation", json={"reason": "结束测试"})
    run_clock(now() + timedelta(hours=5))
    assert admin.get(P + "/accounts").json()[0]["health"] == "abnormal"


def test_quota_reset_once_independent_of_health_and_new_reports(admin, make_account):
    a = make_account()
    deadline = now() + timedelta(hours=1)
    admin.post(
        P + f"/accounts/{a['id']}/quota-reports",
        json={"quota": 0, "reset_at": deadline.isoformat()},
    )
    admin.post(
        P + f"/accounts/{a['id']}/health-reports",
        json={"action": "report", "version": 0, "note": "异常"},
    )
    run_clock(deadline + timedelta(seconds=1))
    run_clock(deadline + timedelta(seconds=2))
    a = admin.get(P + "/accounts").json()[0]
    assert (
        a["quota"] == 100
        and a["quota_source"] == "system"
        and a["health"] == "abnormal"
        and datetime.fromisoformat(a["reset_at"]) == deadline + timedelta(days=7)
    )
    admin.post(P + f"/accounts/{a['id']}/quota-reports", json={"quota": 30})
    run_clock(deadline + timedelta(seconds=3))
    assert admin.get(P + "/accounts").json()[0]["quota"] == 30
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "quota_reset")
            )
            == 1
        )


def test_quota_reset_catches_up_missed_cycles_once(admin, make_account):
    account = make_account(email="catchup@example.test", quota_reset_interval_days=7)
    deadline = now() - timedelta(days=20)
    with Session.begin() as db:
        stored = db.get(Account, account["id"])
        stored.quota, stored.reset_at = 0, deadline
    stamp = now()
    run_clock(stamp)
    result = admin.get(P + f"/accounts/{account['id']}").json()
    expected = deadline + timedelta(days=21)
    assert result["quota"] == 100
    assert datetime.fromisoformat(result["reset_at"]) == expected
    with Session() as db:
        events = db.scalars(select(Event).where(Event.kind == "quota_reset")).all()
        assert len(events) == 1
        assert events[0].details["interval_days"] == 7
        assert events[0].details["next_reset_at"] == expected.isoformat()


def test_settings_changes_apply_to_existing_anchors_and_limits(
    admin, make_user, make_account
):
    u, c = make_user(limit=2)
    a = make_account(users=[u["id"]], capacity=2)
    b = make_account(email="b@example.test", users=[u["id"]])
    assert claim(c, a).status_code == 201
    assert claim(c, b).status_code == 201
    assert (
        admin.patch(P + f"/users/{u['id']}", json={"claim_limit": 1}).status_code == 200
    )
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 2
    admin.post(
        P + f"/accounts/{a['id']}/health-reports",
        json={"action": "report", "version": 0, "note": "异常"},
    )
    cfg = admin.get(P + "/settings").json()
    cfg["cooldown_hours"] = 1
    assert admin.put(P + "/settings", json=cfg).status_code == 200
    run_clock(now() + timedelta(hours=2))
    assert (
        admin.get(P + f"/accounts/{a['id']}").json()["health"] == "possibly_recovered"
    )


def test_simultaneous_account_capacity(admin, make_user, make_account):
    users = [make_user(f"user{i}") for i in range(6)]
    a = make_account(users=[u["id"] for u, c in users])
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda pair: claim(pair[1], a), users))
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409, 409]
    assert admin.get(P + "/accounts").json()[0]["active_count"] == 1


def test_simultaneous_personal_limit_and_duplicate(admin, make_user, make_account):
    u, c = make_user()
    accounts = [
        make_account(email=f"acc{i}@example.test", users=[u["id"]]) for i in range(4)
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda a: claim(c, a), accounts))
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 1


def test_return_revoke_race_never_double_releases(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        r1 = pool.submit(normal_return, c, record)
        r2 = pool.submit(
            admin.put,
            P + f"/claims/{record['id']}/revocation",
            json={"reason": "竞态测试"},
        )
        assert r1.result().status_code == 200 and r2.result().status_code == 200
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 0
    assert admin.get(P + "/accounts").json()[0]["active_count"] == 0


def test_bulk_import_atomicity_format_validation_and_no_secret_leaks(
    admin, make_account
):
    make_account()
    payload = {
        "tier": "20x",
        "text": "new@example.test----Secret-Only-Test----Auth-Only-Test\r\n\naccount@example.test----p----a\nbroken",
    }
    preview = admin.post(P + "/account-imports/preview", json=payload)
    assert [e["line"] for e in preview.json()["errors"]] == [3, 4]
    assert "Secret-Only-Test" not in preview.text
    assert admin.post(P + "/account-imports", json=payload).status_code == 422
    assert len(admin.get(P + "/accounts").json()) == 1
    payload["text"] = "UPPER@example.test----  secret with spaces  ----认证测试\r\n\n"
    assert admin.post(P + "/account-imports", json=payload).status_code == 201
    a = next(
        a
        for a in admin.get(P + "/accounts").json()
        if a["email"] == "upper@example.test"
    )
    assert a["tier"] == "20x" and a["created_at"] and a["expires_at"] is None
    assert (
        admin.get(P + f"/accounts/{a['id']}/credentials").json()["password"]
        == "  secret with spaces  "
    )
    with Session() as db:
        stored = db.get(Account, a["id"])
        assert "secret with spaces" not in stored.password_encrypted
    assert "secret with spaces" not in admin.get(P + "/audit-events").text


def test_bad_acl_rolls_back_entire_import(admin):
    response = admin.post(
        P + "/account-imports",
        json={
            "tier": "5x",
            "text": "one@example.test----p----a\ntwo@example.test----p----a",
            "user_ids": ["missing"],
        },
    )
    assert response.status_code == 422
    assert admin.get(P + "/accounts").json() == []


def test_permissions_sessions_csrf_password_and_last_admin(
    admin, make_user, make_account
):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    other = TestClient(app)
    assert other.get(P + "/accounts").status_code == 401
    assert c.get(P + "/users").status_code == 403
    assert c.get(P + "/claims?scope=all").status_code == 403
    assert (
        c.post(
            P + "/account-imports",
            json={"tier": "5x", "text": "x@example.test----p----a"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            P + "/claims", json={"account_id": a["id"]}, headers={"X-CSRF-Token": "bad"}
        ).status_code
        == 403
    )
    assert (
        c.post(
            P + "/claims",
            json={"account_id": a["id"]},
            headers={"Origin": "https://evil.example.test"},
        ).status_code
        == 403
    )
    admin_id = admin.get(P + "/auth/me").json()["user"]["id"]
    assert admin.delete(P + f"/users/{admin_id}").status_code == 409
    assert (
        admin.patch(P + f"/users/{admin_id}", json={"role": "user"}).status_code == 409
    )
    other_session = login("alice")
    assert (
        c.put(
            P + "/auth/password",
            json={
                "current_password": PASSWORD,
                "new_password": "Changed-Password-For-Test!",
            },
        ).status_code
        == 200
    )
    assert other_session.get(P + "/auth/me").status_code == 401
    assert (
        login("alice", "Changed-Password-For-Test!").get(P + "/auth/me").status_code
        == 200
    )


def test_first_password_and_admin_reset_must_change(admin):
    r = admin.post(
        P + "/users",
        json={"username": "first", "display_name": "初次", "password": PASSWORD},
    )
    c = login("first")
    assert c.get(P + "/accounts").status_code == 403
    assert (
        c.put(
            P + "/auth/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        ).status_code
        == 422
    )
    assert (
        c.put(
            P + "/auth/password",
            json={"current_password": PASSWORD, "new_password": "New-For-First-User!"},
        ).status_code
        == 200
    )
    c = login("first", "New-For-First-User!")
    assert c.get(P + "/accounts").status_code == 200
    admin.patch(P + f"/users/{r.json()['id']}", json={"password": PASSWORD})
    assert c.get(P + "/auth/me").status_code == 401
    assert login("first").get(P + "/accounts").status_code == 403


def test_validation_redacts_submitted_secrets(admin):
    raw = "Extremely-private-test-marker"
    r = admin.post(
        P + "/users",
        json={"username": "x", "display_name": "x", "password": raw, "unexpected": raw},
    )
    assert r.status_code == 422 and raw not in r.text
    r = admin.post(P + "/account-imports", json={"tier": "bad", "text": raw})
    assert r.status_code == 422 and raw not in r.text


def test_login_failures_persist_and_rate_limit(admin):
    c = TestClient(app)
    for _ in range(10):
        assert (
            c.post(
                P + "/auth/login",
                headers={"X-Login-Request": "1"},
                json={"username": "ghost", "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        c.post(
            P + "/auth/login",
            headers={"X-Login-Request": "1"},
            json={"username": "ghost", "password": "wrong"},
        ).status_code
        == 429
    )


def test_https_reverse_proxy_origin_cookie_and_csrf(monkeypatch):
    from app import auth, config

    origin = "https://accounts.example.test"
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", origin)
    # TLS ends at the proxy: the API still receives an HTTP request.
    upstream = TestClient(app, base_url="http://accounts.example.test")
    headers = {"Origin": origin, "X-Login-Request": "1"}
    payload = {"username": "admin", "password": PASSWORD}
    response = upstream.post(P + "/auth/login", headers=headers, json=payload)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "; secure" in cookie and "; httponly" in cookie and "samesite=lax" in cookie
    for bad_origin in ["http://accounts.example.test", "https://evil.example.test"]:
        rejected = upstream.post(
            P + "/auth/login",
            headers={
                **headers,
                "Origin": bad_origin,
                "Host": "evil.example.test",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "evil.example.test",
            },
            json=payload,
        )
        assert rejected.status_code == 403
    browser = TestClient(app, base_url=origin)
    browser.cookies.set(auth.COOKIE, upstream.cookies.get(auth.COOKIE))
    assert browser.get(P + "/auth/me").status_code == 200
    assert (
        browser.post(P + "/auth/logout", headers={"Origin": origin}).status_code == 403
    )
    result = browser.post(
        P + "/auth/logout",
        headers={"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]},
    )
    assert (
        result.status_code == 200 and "; secure" in result.headers["set-cookie"].lower()
    )
    assert browser.get(P + "/auth/me").status_code == 401


def test_direct_http_login_remains_supported(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "")
    client = TestClient(app)
    response = client.post(
        P + "/auth/login",
        headers={"Origin": "http://testserver", "X-Login-Request": "1"},
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert "; secure" not in response.headers["set-cookie"].lower()
    assert client.get(P + "/auth/me").status_code == 200


def test_bad_return_does_not_consume_or_release_any_state(
    admin, make_user, make_account
):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    for data in [
        {"kind": "normal", "quota": 0},
        {
            "kind": "normal",
            "quota": 0,
            "reset_at": (now() - timedelta(hours=1)).isoformat(),
        },
        {"kind": "acknowledge"},
    ]:
        assert c.put(P + f"/claims/{record['id']}/return", json=data).status_code == 422
    assert c.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    assert admin.get(P + "/accounts").json()[0]["quota"] is None


def test_sessions_and_timers_use_database_not_process_memory(
    admin, make_user, make_account
):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    with TestClient(app) as restarted:
        restarted.cookies.update(c.cookies)
        restarted.headers.update({"X-CSRF-Token": c.headers["X-CSRF-Token"]})
        assert restarted.get(P + "/auth/me").json()["user"]["claims_used"] == 1
        assert normal_return(restarted, record).status_code == 200
    with Session.begin() as db:
        db.get(Account, a["id"]).reset_at = now() - timedelta(hours=2)
    run_clock(now())
    assert admin.get(P + "/accounts").json()[0]["quota"] == 100


def test_commit_conflicts_are_reported_before_success_response(admin):
    admin.post(P + "/groups", json={"name": "first"}).json()
    second = admin.post(P + "/groups", json={"name": "second"}).json()
    result = admin.patch(P + f"/groups/{second['id']}", json={"name": "first"})
    assert result.status_code == 409
    groups = admin.get(P + "/groups").json()
    assert {g["name"] for g in groups} == {"first", "second"}


def test_overview_and_filtered_claim_pagination(admin, make_user, make_account):
    u, c = make_user()
    a = make_account(users=[u["id"]])
    record = claim(c, a).json()
    assert admin.get(P + "/overview").json()["active_claims"] == 1
    assert c.get(P + "/overview").status_code == 403
    assert (
        len(admin.get(P + "/claims?scope=all&q=alice&state=active&tier=5x").json()) == 1
    )
    assert admin.get(P + "/claims?scope=all&q=missing").json() == []
    admin.put(P + f"/claims/{record['id']}/revocation", json={"reason": "test"})
    counts = admin.get(P + "/overview").json()
    assert (
        counts["active_claims"] == 0
        and counts["pending_claims"] == 1
        and counts["available"] == 1
    )
    assert len(admin.get(P + "/claims?scope=all&state=pending&limit=1").json()) == 1
    assert (
        admin.get(P + "/claims?scope=all&state=pending&limit=1&offset=1").json() == []
    )


def test_overdue_reset_settles_before_new_feedback(admin, make_account):
    a = make_account()
    deadline = now() - timedelta(minutes=1)
    with Session.begin() as db:
        account = db.get(Account, a["id"])
        account.quota, account.reset_at = 0, deadline
    assert (
        admin.post(
            P + f"/accounts/{a['id']}/quota-reports", json={"quota": 37}
        ).status_code
        == 201
    )
    run_clock(now())
    result = admin.get(P + f"/accounts/{a['id']}").json()
    assert result["quota"] == 37 and result["quota_source"] == "user"
    assert datetime.fromisoformat(result["reset_at"]) == deadline + timedelta(days=7)
    with Session() as db:
        account = db.get(Account, a["id"])
        assert account.quota_updated_at > deadline
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "quota_reset")
            )
            == 1
        )
    future = now() + timedelta(hours=2)
    admin.post(
        P + f"/accounts/{a['id']}/quota-reports",
        json={"quota": 20, "reset_at": future.isoformat()},
    )
    admin.post(P + f"/accounts/{a['id']}/quota-reports", json={"quota": 10})
    run_clock(future + timedelta(seconds=1))
    assert admin.get(P + f"/accounts/{a['id']}").json()["quota"] == 100


def test_deleted_user_acl_edit_and_identifiable_audit(admin, make_user, make_account):
    from app.models import AccountUser, UserGroup

    group = admin.post(P + "/groups", json={"name": "audit-group"}).json()
    removed, client = make_user(groups=[group["id"]])
    retained, other = make_user("bob")
    a = make_account(users=[removed["id"], retained["id"]], capacity=2)
    record = claim(client, a).json()
    assert admin.delete(P + f"/users/{removed['id']}").status_code == 200
    dto = admin.get(P + f"/accounts/{a['id']}").json()
    assert dto["user_ids"] == [retained["id"]]
    assert (
        admin.patch(
            P + f"/accounts/{a['id']}",
            json={
                "capacity": 3,
                "user_ids": dto["user_ids"],
                "group_ids": dto["group_ids"],
            },
        ).status_code
        == 200
    )
    assert other.get(P + "/accounts").json()[0]["id"] == a["id"]
    with Session.begin() as db:
        assert db.get(Claim, record["id"]).return_kind == "user_deleted"
        assert (
            db.scalar(
                select(func.count())
                .select_from(UserGroup)
                .where(UserGroup.user_id == removed["id"])
            )
            == 0
        )
        # Legacy soft-deleted users may still have stale associations.
        db.add(AccountUser(account_id=a["id"], user_id=removed["id"]))
    assert admin.get(P + f"/accounts/{a['id']}").json()["user_ids"] == [retained["id"]]
    public = other.get(P + f"/accounts/{a['id']}/events").json()
    assert all("target_username" not in e and "account_email" not in e for e in public)
    assert (
        admin.request(
            "DELETE", P + f"/accounts/{a['id']}", json={"reason": "expired"}
        ).status_code
        == 200
    )
    events = admin.get(P + "/audit-events").json()
    assert any(
        e["kind"] == "account_deleted" and e["account_email"] == a["email"]
        for e in events
    )
    assert any(
        e["kind"] == "user_deleted" and e["target_username"] == "alice" for e in events
    )
    subjects = admin.get(P + "/audit-subjects").json()
    assert any(s["id"] == a["id"] and s["deleted"] for s in subjects["accounts"])
    assert any(s["id"] == removed["id"] and s["deleted"] for s in subjects["users"])
    assert other.get(P + "/audit-subjects").status_code == 403


def test_edit_group_members_atomic_authorization_and_existing_claim(
    admin, make_user, make_account
):
    removed, first = make_user()
    added, second = make_user("bob")
    unrelated = admin.post(
        P + "/groups", json={"name": "other", "user_ids": [removed["id"]]}
    ).json()
    group = admin.post(
        P + "/groups", json={"name": "members", "user_ids": [removed["id"]]}
    ).json()
    account = make_account(groups=[group["id"]], capacity=2)
    record = claim(first, account).json()
    assert second.get(P + "/accounts").json() == []
    assert (
        first.patch(
            P + f"/groups/{group['id']}",
            json={"name": "members", "user_ids": [added["id"]]},
        ).status_code
        == 403
    )
    assert (
        admin.patch(
            P + f"/groups/{group['id']}",
            json={"name": "renamed", "user_ids": [added["id"]]},
        ).status_code
        == 200
    )
    users = {u["id"]: u for u in admin.get(P + "/users").json()}
    assert users[removed["id"]]["group_ids"] == [unrelated["id"]]
    assert users[added["id"]]["group_ids"] == [group["id"]]
    assert second.get(P + "/accounts").json()[0]["id"] == account["id"]
    assert first.get(P + f"/accounts/{account['id']}/credentials").status_code == 200
    assert normal_return(first, record).status_code == 200
    assert claim(first, account).status_code == 404
    # Invalid membership must also roll back the group rename.
    assert (
        admin.patch(
            P + f"/groups/{group['id']}",
            json={"name": "bad-name", "user_ids": ["missing"]},
        ).status_code
        == 422
    )
    assert any(g["name"] == "renamed" for g in admin.get(P + "/groups").json())
    assert (
        admin.patch(
            P + f"/groups/{group['id']}", json={"name": "rename-only"}
        ).status_code
        == 200
    )
    assert next(u for u in admin.get(P + "/users").json() if u["id"] == added["id"])[
        "group_ids"
    ] == [group["id"]]
    assert (
        admin.patch(
            P + f"/groups/{group['id']}", json={"name": "empty", "user_ids": []}
        ).status_code
        == 200
    )
    assert (
        next(u for u in admin.get(P + "/users").json() if u["id"] == added["id"])[
            "group_ids"
        ]
        == []
    )
    audit = admin.get(P + "/audit-events").json()
    assert any(
        e["target_user_id"] == removed["id"]
        and e["details"].get("membership") == "removed"
        for e in audit
    )


def test_disable_preserves_claims_credentials_and_return_flow(
    admin, make_user, make_account
):
    owner, client = make_user("owner")
    other, other_client = make_user("other")
    a = make_account(users=[owner["id"], other["id"]], capacity=2)
    claim_record = claim(client, a).json()
    credentials_before = client.get(P + f"/accounts/{a['id']}/credentials").json()
    activation = P + f"/accounts/{a['id']}/activation"
    disabled = admin.put(activation, json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["disabled"] is True
    assert disabled.json()["user_ids"] == a["user_ids"]
    assert disabled.json()["created_at"] == a["created_at"]
    assert disabled.json()["active_count"] == 1
    assert disabled.json()["can_claim"] is False
    assert "已停用" in disabled.json()["blocked_reasons"]
    assert (
        client.get(P + f"/accounts/{a['id']}/credentials").json() == credentials_before
    )
    mine = client.get(P + "/claims").json()[0]
    assert mine["account"]["disabled"] is True and mine["invalidated_at"] is None
    assert client.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    assert claim(other_client, a).status_code == 409
    assert claim(admin, a).status_code == 409
    assert any(
        item["id"] == a["id"] for item in other_client.get(P + "/accounts").json()
    )
    counts = admin.get(P + "/overview").json()
    assert counts["disabled"] == 1 and counts["available"] == 0
    assert counts["active_claims"] == 1 and counts["pending_claims"] == 0
    assert (
        client.post(
            P + f"/accounts/{a['id']}/quota-reports", json={"quota": 42}
        ).status_code
        == 201
    )
    assert normal_return(client, claim_record, quota=42).status_code == 200
    assert client.get(P + "/auth/me").json()["user"]["claims_used"] == 0
    assert claim(other_client, a).status_code == 409
    assert admin.put(activation, json={"enabled": False}).status_code == 200
    assert admin.put(activation, json={"enabled": True}).json()["disabled"] is False
    assert admin.put(activation, json={"enabled": True}).status_code == 200
    restored = admin.get(P + f"/accounts/{a['id']}").json()
    assert restored["quota"] == 42 and restored["user_ids"] == a["user_ids"]
    assert claim(other_client, a).status_code == 201
    assert (
        other_client.get(P + f"/accounts/{a['id']}/credentials").json()
        == credentials_before
    )
    events = admin.get(P + f"/accounts/{a['id']}/events").json()
    assert sum(e["kind"] == "account_disabled" for e in events) == 1
    assert sum(e["kind"] == "account_enabled" for e in events) == 1
    assert all(e["kind"] != "claim_invalidated" for e in events)


def test_activation_permissions_validation_and_deleted_account(
    admin, make_user, make_account
):
    u, client = make_user()
    a = make_account(users=[u["id"]])
    endpoint = P + f"/accounts/{a['id']}/activation"
    assert client.put(endpoint, json={"enabled": False}).status_code == 403
    assert TestClient(app).put(endpoint, json={"enabled": False}).status_code == 401
    for payload in ({}, {"enabled": None}, {"enabled": "false"}, {"enabled": 0}):
        assert admin.put(endpoint, json=payload).status_code == 422
    assert admin.get(P + f"/accounts/{a['id']}").json()["disabled"] is False
    assert admin.put(endpoint, json={"enabled": False}).status_code == 200
    assert (
        admin.request(
            "DELETE", P + f"/accounts/{a['id']}", json={"reason": "测试删除"}
        ).status_code
        == 200
    )
    assert admin.put(endpoint, json={"enabled": True}).status_code == 404
    assert (
        admin.put(
            P + "/accounts/missing/activation", json={"enabled": True}
        ).status_code
        == 404
    )
    assert not admin.get(P + "/accounts").json()


def test_disable_and_claim_are_serialized(admin, make_user, make_account):
    u, client = make_user()
    a = make_account(users=[u["id"]])
    barrier = Barrier(2)

    def disable():
        barrier.wait()
        return admin.put(P + f"/accounts/{a['id']}/activation", json={"enabled": False})

    def acquire():
        barrier.wait()
        return claim(client, a)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stopped, acquired = pool.submit(disable), pool.submit(acquire)
        stopped, acquired = stopped.result(), acquired.result()
    assert stopped.status_code == 200
    assert acquired.status_code in {201, 409}
    final = admin.get(P + f"/accounts/{a['id']}").json()
    assert final["disabled"] is True and final["can_claim"] is False
    assert final["active_count"] == (1 if acquired.status_code == 201 else 0)
    assert claim(client, a).status_code == 409
    if acquired.status_code == 201:
        assert client.get(P + "/claims").json()[0]["invalidated_at"] is None
        assert normal_return(client, acquired.json()).status_code == 200


def test_automatic_quota_reset_does_not_reenable_disabled_account(admin, make_account):
    a = make_account()
    assert (
        admin.put(
            P + f"/accounts/{a['id']}/activation", json={"enabled": False}
        ).status_code
        == 200
    )
    with Session.begin() as db:
        account = db.get(Account, a["id"])
        account.quota = 0
        account.reset_at = now() - timedelta(minutes=1)
    run_clock(now())
    current = admin.get(P + f"/accounts/{a['id']}").json()
    assert current["quota"] == 100 and current["disabled"] is True
    assert current["can_claim"] is False
    assert claim(admin, a).status_code == 409


def test_hall_hides_disabled_and_expired_but_retains_owned_claim(
    admin, make_user, make_account
):
    u, client = make_user()
    visible = make_account(email="visible@example.test", users=[u["id"]])
    stopped = make_account(email="stopped@example.test", users=[u["id"]])
    expired = make_account(
        email="expired@example.test",
        users=[u["id"]],
        expires_at=(now() - timedelta(hours=1)).isoformat(),
    )
    private = make_account(email="private@example.test")
    record = claim(client, stopped).json()
    assert (
        admin.put(
            P + f"/accounts/{stopped['id']}/activation", json={"enabled": False}
        ).status_code
        == 200
    )
    assert {a["id"] for a in client.get(P + "/accounts?scope=hall").json()} == {
        visible["id"]
    }
    assert {a["id"] for a in admin.get(P + "/accounts?scope=hall").json()} == {
        visible["id"],
        private["id"],
    }
    assert len(admin.get(P + "/accounts").json()) == 4
    assert client.get(P + "/claims").json()[0]["account"]["id"] == stopped["id"]
    assert normal_return(client, record).status_code == 200
    assert (
        admin.put(
            P + f"/accounts/{stopped['id']}/activation", json={"enabled": True}
        ).status_code
        == 200
    )
    assert {a["id"] for a in client.get(P + "/accounts?scope=hall").json()} == {
        visible["id"],
        stopped["id"],
    }
    assert claim(client, expired).status_code == 409
    assert client.get(P + "/accounts?scope=invalid").status_code == 422


def test_claim_blocking_prioritizes_personal_limit_not_health_or_quota(
    admin, make_user, make_account
):
    first, client = make_user()
    other, other_client = make_user("other")
    occupied = make_account(
        email="occupied@example.test", users=[first["id"], other["id"]]
    )
    depleted = make_account(
        email="depleted@example.test", users=[first["id"]], capacity=2
    )
    assert claim(other_client, occupied).status_code == 201
    assert (
        admin.post(
            P + f"/accounts/{depleted['id']}/quota-reports", json={"quota": 0}
        ).status_code
        == 201
    )
    info = client.get(P + f"/accounts/{depleted['id']}").json()
    assert info["can_claim"] is True
    assert claim(client, depleted).status_code == 201
    full = client.get(P + f"/accounts/{occupied['id']}").json()
    assert full["blocked_reasons"][:2] == ["个人名额已满", "人数已满"]
    mine = client.get(P + f"/accounts/{depleted['id']}").json()
    assert mine["has_open_claim"] is True
    assert mine["blocked_reasons"][0] == "个人名额已满"
