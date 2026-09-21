from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import default_account_options
from app.db import Session
from app.main import app
from app.models import Account, Event
from test_business import P, claim, normal_return, run_clock


def save(admin, cfg):
    response = admin.put(P + "/settings", json=cfg)
    assert response.status_code == 200, response.text
    return response.json()


def report(client, account, categories, note="", version=0):
    return client.post(
        P + f"/accounts/{account['id']}/health-reports",
        json={
            "action": "report",
            "version": version,
            "categories": categories,
            "note": note,
        },
    )


def test_catalog_permissions_defaults_and_old_clients(admin, make_user):
    _, client = make_user()
    cfg = admin.get(P + "/settings").json()
    assert cfg["account_options"] == default_account_options()
    public = client.get(P + "/account-options")
    assert public.status_code == 200
    assert public.json() == {**cfg["account_options"], "cooldown_hours": 24}
    assert public.headers["cache-control"] == "no-store"
    assert TestClient(app).get(P + "/account-options").status_code == 401
    assert client.get(P + "/settings").status_code == 403
    assert client.put(P + "/settings", json=cfg).status_code == 403
    cfg["account_options"]["tiers"].append({"name": "扩展档位"})
    stored = save(admin, cfg)["account_options"]
    assert stored["tiers"][-1]["id"] and stored["tiers"][-1]["enabled"]
    del cfg["account_options"]
    cfg["cooldown_hours"] = 30
    assert save(admin, cfg)["account_options"] == stored
    assert client.get(P + "/account-options").json() == {**stored, "cooldown_hours": 30}


def test_custom_tier_import_edit_rename_disable_and_history(
    admin, make_user, make_account
):
    user, client = make_user()
    old = make_account(users=[user["id"]])
    cfg = admin.get(P + "/settings").json()
    cfg["account_options"]["tiers"].append(
        {"id": "extended-tier", "name": "扩展档位", "enabled": True}
    )
    save(admin, cfg)
    payload = {
        "tier": "extended-tier",
        "text": "new@example.test----fictional----mailbox",
        "user_ids": [user["id"]],
    }
    preview = admin.post(P + "/account-imports/preview", json=payload)
    assert preview.json()["rows"][0]["tier_name"] == "扩展档位"
    assert admin.post(P + "/account-imports", json=payload).status_code == 201
    path = P + f"/accounts/{old['id']}"
    assert client.patch(path, json={"tier": "extended-tier"}).status_code == 403
    assert admin.patch(path, json={"tier": "extended-tier"}).status_code == 200
    record = claim(client, old).json()
    assert len(admin.get(P + "/claims?scope=all&tier=extended-tier").json()) == 1
    cfg["account_options"]["tiers"][-1].update(name="已改名档位", enabled=False)
    save(admin, cfg)
    assert client.get(path).json()["tier_name"] == "已改名档位"
    assert client.get(P + "/claims").json()[0]["account"]["tier_name"] == "已改名档位"
    # Keeping the same disabled tier must not block unrelated edits.
    assert (
        admin.patch(path, json={"tier": "extended-tier", "capacity": 3}).status_code
        == 200
    )
    payload["text"] = "blocked@example.test----p----m"
    for endpoint in ("/account-imports/preview", "/account-imports"):
        assert admin.post(P + endpoint, json=payload).status_code == 422
    assert normal_return(client, record).status_code == 200
    assert (
        client.get(P + "/claims?history=true").json()[0]["account"]["tier_name"]
        == "已改名档位"
    )
    assert admin.patch(path, json={"tier": "5x"}).status_code == 200
    assert admin.patch(path, json={"tier": "extended-tier"}).status_code == 422
    assert admin.patch(path, json={"tier": None}).status_code == 422
    assert len(admin.get(P + "/accounts").json()) == 2


def test_referenced_options_cannot_be_removed_and_unused_options_can(
    admin, make_account
):
    a = make_account()
    assert report(admin, a, ["at capacity"]).status_code == 201
    cfg = admin.get(P + "/settings").json()
    for kind in ("tiers", "anomaly_categories"):
        changed = deepcopy(cfg)
        changed["account_options"][kind].pop(0)
        changed["cooldown_hours"] = 99
        assert admin.put(P + "/settings", json=changed).status_code == 422
        assert admin.get(P + "/settings").json() == cfg
    assert (
        admin.request(
            "DELETE", P + f"/accounts/{a['id']}", json={"reason": "test"}
        ).status_code
        == 200
    )
    changed = deepcopy(cfg)
    changed["account_options"]["tiers"].pop(0)
    assert admin.put(P + "/settings", json=changed).status_code == 422
    cfg["account_options"]["tiers"].pop(1)
    cfg["account_options"]["anomaly_categories"].pop(1)
    save(admin, cfg)


def test_dynamic_anomalies_rename_disable_and_maintain_return(
    admin, make_user, make_account
):
    user, client = make_user()
    a = make_account(users=[user["id"]])
    record = claim(client, a).json()
    cfg = admin.get(P + "/settings").json()
    cfg["account_options"]["anomaly_categories"] = [
        {"id": "slow", "name": "响应缓慢", "enabled": True, "cooldown_hours": 48}
    ]
    save(admin, cfg)
    assert report(client, a, ["slow"]).status_code == 201
    cfg["account_options"]["anomaly_categories"][0].update(
        name="响应延迟", enabled=False
    )
    save(admin, cfg)
    current = client.get(P + f"/accounts/{a['id']}").json()
    assert current["health_categories"] == ["slow"]
    assert current["health_category_names"] == ["响应延迟"]
    assert report(client, a, ["slow"], version=1).status_code == 422
    assert (
        client.put(
            P + f"/claims/{record['id']}/return",
            json={"kind": "abnormal", "categories": ["slow"], "health_version": 1},
        ).status_code
        == 422
    )
    assert client.get(P + "/auth/me").json()["user"]["claims_used"] == 1
    assert (
        normal_return(
            client, record, health_version=1, health_action="maintain"
        ).status_code
        == 200
    )
    with Session() as db:
        events = db.scalars(
            select(Event).where(Event.kind == "health_reported").order_by(Event.id)
        ).all()
        assert [e.details["category_names"] for e in events] == [
            ["响应缓慢"],
            ["响应延迟"],
        ]
    # No enabled presets still permits free text feedback.
    assert report(admin, a, [], "自由描述", version=2).status_code == 201
    cfg["account_options"]["anomaly_categories"] = []
    save(admin, cfg)


@pytest.mark.parametrize(
    "categories,hours",
    [
        (["short"], 2),
        (["long"], 48),
        (["short", "long"], 48),
        (["short", "default"], 24),
        (["short", "short"], 2),
        ([], 24),
    ],
)
def test_recovery_uses_longest_effective_interval(
    admin, make_account, categories, hours
):
    a = make_account()
    cfg = admin.get(P + "/settings").json()
    cfg["account_options"]["anomaly_categories"] = [
        {"id": key, "name": key, "enabled": True, "cooldown_hours": interval}
        for key, interval in (("short", 2), ("long", 48), ("default", None))
    ]
    save(admin, cfg)
    # Extra free text must not add another global interval to selected categories.
    assert report(admin, a, categories, "补充说明").status_code == 201
    current = admin.get(P + f"/accounts/{a['id']}").json()
    start = datetime.fromisoformat(current["anomaly_since"])
    run_clock(start + timedelta(hours=hours, microseconds=-1))
    assert admin.get(P + f"/accounts/{a['id']}").json()["health"] == "abnormal"
    run_clock(start + timedelta(hours=hours))
    run_clock(start + timedelta(hours=hours, seconds=1))
    with Session() as db:
        stored = db.get(Account, a["id"])
        assert stored.health == "possibly_recovered"
        assert stored.anomaly_since == start
        events = db.scalars(select(Event).where(Event.kind == "health_possible")).all()
        assert len(events) == 1 and events[0].details["reason"] == "cooldown"


def test_live_interval_edits_preserve_anchor_and_observation_rule(
    admin, make_user, make_account
):
    user, client = make_user()
    a = make_account(users=[user["id"]])
    cfg = admin.get(P + "/settings").json()
    category = cfg["account_options"]["anomaly_categories"][0]
    category["cooldown_hours"] = 72
    save(admin, cfg)
    assert report(admin, a, [category["id"]]).status_code == 201
    start = datetime.fromisoformat(
        admin.get(P + f"/accounts/{a['id']}").json()["anomaly_since"]
    )
    run_clock(start + timedelta(hours=25))
    assert admin.get(P + f"/accounts/{a['id']}").json()["health"] == "abnormal"
    category["cooldown_hours"] = 2
    save(admin, cfg)
    run_clock(start + timedelta(hours=3))
    assert (
        admin.get(P + f"/accounts/{a['id']}").json()["health"] == "possibly_recovered"
    )
    category["cooldown_hours"] = 72
    save(admin, cfg)
    assert report(admin, a, [category["id"]], version=1).status_code == 201
    record = claim(client, a, True).json()
    run_clock(datetime.fromisoformat(record["claimed_at"]) + timedelta(hours=4))
    with Session() as db:
        assert db.get(Account, a["id"]).health == "possibly_recovered"
        assert (
            db.scalars(
                select(Event)
                .where(Event.kind == "health_possible")
                .order_by(Event.id.desc())
            )
            .first()
            .details["reason"]
            == "observation"
        )


def test_invalid_config_and_stale_options_are_rejected_atomically(admin, make_account):
    a = make_account()
    original = admin.get(P + "/settings").json()
    invalid = [None, {"tiers": [], "anomaly_categories": []}]
    for field, values in (
        ("name", ["", "  ", "a" * 81]),
        ("id", ["", "a" * 65, "all"]),
        ("enabled", [None, "false", 0]),
        ("cooldown_hours", [0, 8761, 2.5, True]),
    ):
        for value in values:
            options = deepcopy(original["account_options"])
            options["anomaly_categories"][0][field] = value
            invalid.append(options)
    for field in ("name", "id"):
        options = deepcopy(original["account_options"])
        options["tiers"][1][field] = options["tiers"][0][field]
        invalid.append(options)
    options = deepcopy(original["account_options"])
    for tier in options["tiers"]:
        tier["enabled"] = False
    invalid.append(options)
    for options in invalid:
        cfg = {**original, "cooldown_hours": 99, "account_options": options}
        assert admin.put(P + "/settings", json=cfg).status_code == 422
    assert admin.get(P + "/settings").json() == original
    assert report(admin, a, ["unknown"], "not enough").status_code == 422
    assert (
        admin.patch(
            P + f"/accounts/{a['id']}", json={"tier": "unknown", "capacity": 99}
        ).status_code
        == 422
    )
    current = admin.get(P + f"/accounts/{a['id']}").json()
    assert current["health_version"] == 0 and current["capacity"] is None
