import csv
import io

from app.config import cipher
from app.db import Session
from app.models import TwoFactor

P = "/api/v1"


def test_admin_can_download_account_csv_with_2fa_state(
    admin, make_account, make_user
):
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
                uri_encrypted=cipher().encrypt(b"otpauth://totp/example").decode(),
                updated_by=admin_id,
            )
        )

    response = admin.get(P + "/accounts/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert 'attachment; filename="account-export.csv"' in response.headers[
        "content-disposition"
    ]
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
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
        "到期时间（北京时间）": "",
    }
    assert by_email["disabled@example.test"]["是否停用"] == "是"
    assert by_email["disabled@example.test"]["是否设置 2FA"] == "否"
    audit = admin.get(P + "/audit-events").json()
    assert any(event["kind"] == "accounts_exported" for event in audit)

    _, user_client = make_user()
    assert user_client.get(P + "/accounts/export.csv").status_code == 403
