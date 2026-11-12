import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.config import cipher
from app.db import Session, engine
from app.models import Account, Event, TwoFactor, now
from app.main import app

P = "/api/v1"
# Public RFC 6238 test secret; never use it for an actual account.
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
TOTP_URI = f"otpauth://totp/example?secret={TOTP_SECRET}"


def test_admin_can_download_account_csv_with_2fa_state(admin, make_account, make_user):
    configured = make_account(email="configured@example.test")
    disabled = make_account(email="disabled@example.test")
    deleted = make_account(email="deleted@example.test")
    assert (
        admin.put(
            P + f"/accounts/{disabled['id']}/activation",
            json={"enabled": False},
        ).status_code
        == 200
    )
    assert (
        admin.request(
            "DELETE",
            P + f"/accounts/{deleted['id']}",
            json={"reason": "测试删除"},
        ).status_code
        == 200
    )
    admin_id = admin.get(P + "/auth/me").json()["user"]["id"]
    with Session.begin() as db:
        db.add(
            TwoFactor(
                account_id=configured["id"],
                kind="service",
                uri_encrypted=cipher().encrypt(TOTP_URI.encode()).decode(),
                updated_by=admin_id,
            )
        )

    response = admin.get(P + "/accounts/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert (
        'attachment; filename="account-export.csv"'
        in response.headers["content-disposition"]
    )
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    assert reader.fieldnames[:7] == [
        "账号邮箱",
        "登录密码",
        "邮箱密码",
        "账号类别",
        "是否停用",
        "是否设置 2FA",
        "到期时间（北京时间）",
    ]
    rows = list(reader)
    assert {row["账号邮箱"] for row in rows} == {
        "configured@example.test",
        "disabled@example.test",
    }
    by_email = {row["账号邮箱"]: row for row in rows}
    assert by_email["configured@example.test"] == {
        "账号邮箱": "configured@example.test",
        "登录密码": "fictional-password",
        "邮箱密码": "fictional-auth",
        "账号类别": "5x",
        "是否停用": "否",
        "是否设置 2FA": "是",
        "2FA 密钥": TOTP_SECRET,
        "2FA 配置链接": TOTP_URI,
        "到期时间（北京时间）": "",
    }
    assert by_email["disabled@example.test"]["是否停用"] == "是"
    assert by_email["disabled@example.test"]["是否设置 2FA"] == "否"
    assert by_email["disabled@example.test"]["2FA 密钥"] == ""
    assert by_email["disabled@example.test"]["2FA 配置链接"] == ""
    audit = admin.get(P + "/audit-events").json()
    assert any(event["kind"] == "accounts_exported" for event in audit)
    assert TOTP_SECRET not in str(audit) and TOTP_URI not in str(audit)

    _, user_client = make_user()
    assert user_client.get(P + "/accounts/export.csv").status_code == 403


def csv_rows(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_export_uses_same_filters_as_list_and_excludes_deleted(admin, make_account):
    group = admin.post(P + "/groups", json={"name": "Export test group"}).json()
    first = make_account(email="literal%_first@example.test", groups=[group["id"]])
    other_tier = make_account(email="literal%_other@example.test", groups=[group["id"]])
    disabled = make_account(email="disabled@example.test", groups=[group["id"]])
    expired = make_account(
        email="expired@example.test", expires_at=(now() - timedelta(days=1)).isoformat()
    )
    deleted = make_account(email="deleted@example.test", groups=[group["id"]])
    with Session.begin() as db:
        db.get(Account, other_tier["id"]).tier = "20x"
        db.get(Account, disabled["id"]).disabled = True
        db.get(Account, first["id"]).health = "abnormal"
        db.get(Account, deleted["id"]).deleted = True
    cases = [
        {},
        {"tier": "5x"},
        {"tier": "20x"},
        {"statuses": "disabled"},
        {"statuses": "abnormal,disabled"},
        {"statuses": ""},
        {"group_id": group["id"]},
        {"q": "literal%_"},
        {
            "q": "LITERAL%_",
            "tier": "5x",
            "group_id": group["id"],
            "statuses": "abnormal",
        },
        {"q": "no-results"},
        {"group_id": "not-a-group"},
    ]
    for params in cases:
        expected = admin.get(P + "/accounts", params=params).json()
        actual = csv_rows(admin.get(P + "/accounts/export.csv", params=params))
        assert {a["账号邮箱"] for a in actual} == {a["email"] for a in expected}
        assert "deleted@example.test" not in {a["账号邮箱"] for a in actual}
        with Session() as db:
            last = db.scalar(
                select(Event)
                .where(Event.kind == "accounts_exported")
                .order_by(Event.id.desc())
            )
            assert last.details["count"] == len(actual)
    assert expired["email"] in {
        a["账号邮箱"] for a in csv_rows(admin.get(P + "/accounts/export.csv"))
    }


def test_filtered_export_is_not_limited_to_the_current_page(admin, make_account):
    for i in range(23):
        make_account(email=f"selected{i}@example.test")
    make_account(email="outside@example.test")
    page = admin.get(
        P + "/accounts", params={"q": "selected", "offset": 20, "limit": 20}
    )
    assert len(page.json()) == 3
    reads = []

    def record(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        exported = csv_rows(
            admin.get(P + "/accounts/export.csv", params={"q": "selected"})
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(reads) <= 5
    assert len(exported) == 23
    assert all(row["账号邮箱"].startswith("selected") for row in exported)


@pytest.mark.parametrize(
    "params", [{"statuses": "invalid"}, {"q": "a" * 255}, {"tier": ""}]
)
def test_invalid_export_filters_do_not_record_a_successful_export(admin, params):
    assert admin.get(P + "/accounts/export.csv", params=params).status_code == 422
    assert not any(
        e["kind"] == "accounts_exported" for e in admin.get(P + "/audit-events").json()
    )


def test_anonymous_export_is_denied():
    assert TestClient(app).get(P + "/accounts/export.csv").status_code == 401


@pytest.mark.parametrize("encrypted", [False, True])
def test_invalid_2fa_outside_export_scope_is_not_loaded(admin, make_account, encrypted):
    make_account(email="selected@example.test")
    broken = make_account(email="outside@example.test")
    user_id = admin.get(P + "/auth/me").json()["user"]["id"]
    invalid = "otpauth://totp/invalid?secret=INVALID"
    with Session.begin() as db:
        db.add(
            TwoFactor(
                account_id=broken["id"],
                kind="service",
                updated_by=user_id,
                uri_encrypted=cipher().encrypt(invalid.encode()).decode()
                if encrypted
                else "invalid-ciphertext",
            )
        )
    assert (
        len(csv_rows(admin.get(P + "/accounts/export.csv", params={"q": "selected"})))
        == 1
    )
    response = admin.get(P + "/accounts/export.csv", params={"q": "outside"})
    assert response.status_code == 409
    assert invalid not in response.text and "invalid-ciphertext" not in response.text
    exports = [
        e
        for e in admin.get(P + "/audit-events").json()
        if e["kind"] == "accounts_exported"
    ]
    assert len(exports) == 1 and exports[0]["details"]["count"] == 1


def test_cleared_and_legacy_mailbox_2fa_are_not_exported(admin, make_account):
    account = make_account()
    user_id = admin.get(P + "/auth/me").json()["user"]["id"]
    with Session.begin() as db:
        db.add_all(
            [
                TwoFactor(
                    account_id=account["id"],
                    kind="service",
                    uri_encrypted="",
                    version=2,
                    updated_by=user_id,
                ),
                TwoFactor(
                    account_id=account["id"],
                    kind="mail",
                    uri_encrypted=cipher().encrypt(TOTP_URI.encode()).decode(),
                    updated_by=user_id,
                ),
            ]
        )
    row = csv_rows(admin.get(P + "/accounts/export.csv"))[0]
    assert (
        row["是否设置 2FA"] == "否"
        and row["2FA 密钥"] == ""
        and row["2FA 配置链接"] == ""
    )


def test_full_2fa_configuration_can_be_reimported_without_losing_parameters(
    admin, make_account
):
    from app import totp

    account = make_account()
    user_id = admin.get(P + "/auth/me").json()["user"]["id"]
    uri = TOTP_URI + "&algorithm=SHA256&digits=8&period=60"
    with Session.begin() as db:
        db.add(
            TwoFactor(
                account_id=account["id"],
                kind="service",
                uri_encrypted=cipher().encrypt(uri.encode()).decode(),
                updated_by=user_id,
            )
        )
    row = csv_rows(admin.get(P + "/accounts/export.csv"))[0]
    assert row["2FA 密钥"] == TOTP_SECRET
    restored = totp.parse_uri(
        totp.import_uri(row["2FA 配置链接"], "restored@example.test")
    )
    assert restored.interval == 60 and restored.digits == 8
    stamp = now()
    assert restored.at(stamp) == totp.parse_uri(uri).at(stamp)
