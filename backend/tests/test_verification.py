"""All QR secrets, addresses, codes and passwords below are fictional fixtures."""

import base64
import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from threading import Event as ThreadEvent

import pytest
import httpx
import zxingcpp
from PIL import Image
from sqlalchemy import select

from app import totp, verification as v
from app.config import cipher
from app.db import Session, write_lock
from app.models import Claim, EmailCodeRun, Event, Settings, TwoFactor, User, now
from app.main import app

SECRET = base64.b32encode(b"12345678901234567890").decode()
URI = (
    f"otpauth://totp/Fictional:account%40example.test?secret={SECRET}&issuer=Fictional"
)


def qr_image(uri=URI):
    barcode = zxingcpp.create_barcode(uri, zxingcpp.BarcodeFormat.QRCode)
    out = io.BytesIO()
    Image.fromarray(barcode.to_image(scale=5)).save(out, "PNG")
    return out.getvalue()


def claim(client, account):
    response = client.post("/api/v1/claims", json={"account_id": account["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def start(client, account, identity=None):
    return client.post(
        f"/api/v1/accounts/{account['id']}/email-code-runs",
        json={"id": identity or str(uuid4())},
    )


def test_unconfigured_email_rules_cannot_start_a_job(admin, make_account, monkeypatch):
    account = make_account()
    monkeypatch.delenv("MAIL_CODE_SUBJECT_KEYWORD")
    response = start(admin, account)
    assert response.status_code == 503
    assert "匹配规则" in response.json()["detail"]
    with Session() as db:
        assert db.scalar(select(EmailCodeRun)) is None


@pytest.mark.parametrize(
    "algorithm,secret,expected",
    [
        ("SHA1", b"12345678901234567890", "94287082"),
        ("SHA256", b"12345678901234567890123456789012", "46119246"),
        (
            "SHA512",
            b"1234567890123456789012345678901234567890123456789012345678901234",
            "90693936",
        ),
    ],
)
def test_rfc6238_vectors_and_boundary(algorithm, secret, expected):
    uri = f"otpauth://totp/Test?secret={base64.b32encode(secret).decode()}&algorithm={algorithm}&digits=8&period=30"
    result = totp.current_code(uri, datetime.fromtimestamp(59, timezone.utc))
    assert result["code"] == expected
    assert result["valid_until"].timestamp() == 60
    assert (
        totp.current_code(uri, datetime.fromtimestamp(60, timezone.utc))["code"]
        != expected
    )


def test_only_admin_configures_while_claimants_can_read_encrypted_totp(
    admin, make_user, make_account
):
    alice, client = make_user()
    account = make_account(users=[alice["id"]])
    base = f"/api/v1/accounts/{account['id']}"
    assert (
        client.put(
            base + "/two-factor/service?version=0", content=qr_image()
        ).status_code
        == 403
    )
    claim(client, account)
    assert client.get(base + "/two-factor/service/code").status_code == 404
    assert (
        client.put(
            base + "/two-factor/service?version=0", content=qr_image()
        ).status_code
        == 403
    )
    response = admin.put(base + "/two-factor/service?version=0", content=qr_image())
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    status = client.get(base + "/verification").json()
    assert status["two_factor"]["service"]["configured"]
    assert set(status["two_factor"]) == {"service"}
    code = client.get(base + "/two-factor/service/code")
    assert code.status_code == 200 and len(code.json()["code"]) == 6
    assert SECRET not in code.text + response.text + str(status)
    assert admin.get(base + "/two-factor/service/code").status_code == 200
    assert (
        client.put(
            base + "/two-factor/service?version=1", content=qr_image()
        ).status_code
        == 403
    )
    assert client.delete(base + "/two-factor/service?version=1").status_code == 403
    _, outsider = make_user("outsider")
    assert outsider.get(base + "/two-factor/service/code").status_code == 403
    with Session.begin() as db:
        config = db.get(TwoFactor, (account["id"], "service"))
        assert SECRET not in config.uri_encrypted
        assert cipher().decrypt(config.uri_encrypted.encode()).decode() == URI
        events = list(
            db.scalars(select(Event).where(Event.kind == "two_factor_updated"))
        )
        assert len(events) == 1 and events[0].details == {"target": "service"}
    admin.headers["X-CSRF-Token"] = "wrong"
    assert (
        admin.put(
            base + "/two-factor/service?version=0", content=qr_image()
        ).status_code
        == 403
    )
    assert admin.delete(base + "/two-factor/service?version=1").status_code == 403


def test_removal_clears_secret_audits_and_preserves_versions(
    admin, make_user, make_account
):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    claim(client, account)
    base = f"/api/v1/accounts/{account['id']}"
    path = base + "/two-factor/service"
    assert admin.delete(path + "?version=0").json() == {
        "configured": False,
        "version": 0,
        "updated_at": None,
    }
    assert admin.put(path + "?version=0", content=qr_image()).status_code == 200
    assert admin.delete(path + "?version=0").status_code == 409
    removed = admin.delete(path + "?version=1")
    assert removed.status_code == 200
    assert removed.json()["configured"] is False
    assert removed.json()["version"] == 2
    assert admin.delete(path + "?version=2").json() == removed.json()
    assert (
        client.get(base + "/verification").json()["two_factor"]["service"]
        == removed.json()
    )
    for actor in (admin, client):
        assert actor.get(path + "/code").status_code == 404
    with Session() as db:
        saved = db.get(TwoFactor, (account["id"], "service"))
        assert saved.uri_encrypted == "" and saved.version == 2
        events = list(
            db.scalars(select(Event).where(Event.kind == "two_factor_removed"))
        )
        assert len(events) == 1 and events[0].details == {"target": "service"}
        assert events[0].actor_id == saved.updated_by
    for stale in (0, 1):
        assert (
            admin.put(path + f"?version={stale}", content=qr_image()).status_code == 409
        )
    assert admin.put(path + "?version=2", content=qr_image()).json()["version"] == 3
    assert admin.delete(path + "?version=1").status_code == 409
    assert client.get(path + "/code").json()["version"] == 3


def test_claimant_upload_rejected_before_image_decoding(
    make_user, make_account, monkeypatch
):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    claim(client, account)

    def forbidden_decode(_):
        pytest.fail("Non-admin uploads must not reach image decoding")

    monkeypatch.setattr(totp, "decode_image", forbidden_decode)
    path = f"/api/v1/accounts/{account['id']}/two-factor/service?version=0"
    for payload in (b"invalid", b"x" * (totp.MAX_IMAGE_BYTES + 1)):
        assert client.put(path, content=payload).status_code == 403


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_upload_rechecks_admin_and_version_after_decoding(
    admin, make_user, make_account, monkeypatch, change
):
    operator, client = make_user("operator", role="admin")
    account = make_account(users=[operator["id"]])
    claim(client, account)
    path = f"/api/v1/accounts/{account['id']}/two-factor/service"
    assert admin.put(path + "?version=0", content=qr_image()).status_code == 200
    entered, release = ThreadEvent(), ThreadEvent()
    original = totp.decode_image

    def slow_decode(data):
        entered.set()
        assert release.wait(5), "Image decoding held a database lock"
        return original(data)

    monkeypatch.setattr(totp, "decode_image", slow_decode)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.put, path + "?version=1", content=qr_image())
        try:
            assert entered.wait(2)
            if change == "demote":
                with Session.begin() as db:
                    write_lock(db)
                    db.get(User, operator["id"]).role = "user"
            else:
                assert admin.delete(path + "?version=1").status_code == 200
        finally:
            release.set()
        assert pending.result().status_code == (403 if change == "demote" else 409)
    with Session() as db:
        saved = db.get(TwoFactor, (account["id"], "service"))
        assert saved.version == (1 if change == "demote" else 2)
        assert bool(saved.uri_encrypted) == (change == "demote")


def test_mail_2fa_is_disabled_even_with_a_saved_configuration(
    admin, make_user, make_account
):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    claim(client, account)
    base = f"/api/v1/accounts/{account['id']}"
    encrypted = cipher().encrypt(URI.encode()).decode()
    with Session.begin() as db:
        db.add(
            TwoFactor(
                account_id=account["id"],
                kind="mail",
                uri_encrypted=encrypted,
                version=3,
                updated_by=user["id"],
            )
        )
    for actor in (admin, client):
        assert set(actor.get(base + "/verification").json()["two_factor"]) == {
            "service"
        }
        assert actor.get(base + "/two-factor/mail/code").status_code == 422
        assert (
            actor.put(
                base + "/two-factor/mail?version=3", content=qr_image()
            ).status_code
            == 422
        )
    with Session() as db:
        saved = db.get(TwoFactor, (account["id"], "mail"))
        assert saved.version == 3 and saved.uri_encrypted == encrypted


def test_invalid_qr_and_stale_replacement_preserve_existing_config(admin, make_account):
    account = make_account()
    path = f"/api/v1/accounts/{account['id']}/two-factor/service"
    assert admin.put(path + "?version=0", content=qr_image()).status_code == 200
    assert admin.put(path + "?version=0", content=qr_image()).status_code == 409
    assert admin.put(path + "?version=1", content=b"not an image").status_code == 422
    assert (
        admin.put(
            path + "?version=1", content=b"x" * (totp.MAX_IMAGE_BYTES + 1)
        ).status_code
        == 413
    )
    for uri in (
        URI.replace("totp/", "hotp/") + "&counter=0",
        URI + "&period=0",
        URI + "&secret=OTHER",
        "https://example.test/",
    ):
        response = admin.put(path + "?version=1", content=qr_image(uri))
        assert response.status_code == 422
        assert SECRET not in response.text
    with Session.begin() as db:
        assert db.get(TwoFactor, (account["id"], "service")).version == 1
    assert admin.put(path + "?version=1", content=qr_image()).json()["version"] == 2


def test_multiple_qrs_are_rejected():
    first = Image.open(io.BytesIO(qr_image()))
    second = Image.open(io.BytesIO(qr_image(URI.replace("Fictional", "Another"))))
    image = Image.new(
        "RGB",
        (first.width + second.width + 30, max(first.height, second.height)),
        "white",
    )
    image.paste(first, (0, 0))
    image.paste(second, (first.width + 30, 0))
    out = io.BytesIO()
    image.save(out, "PNG")
    with pytest.raises(totp.InvalidQR):
        totp.decode_image(out.getvalue())


def test_email_exclusivity_private_result_and_received_time(
    admin, make_user, make_account
):
    alice, a = make_user("alice")
    bob, b = make_user("bob")
    account = make_account(users=[alice["id"], bob["id"]], capacity=2)
    claim(a, account)
    claim(b, account)
    identity = str(uuid4())
    response = start(a, account, identity)
    assert response.status_code == 201
    assert start(a, account, identity).json()["id"] == identity
    assert start(b, account).status_code == 409
    base = f"/api/v1/accounts/{account['id']}"
    status = b.get(base + "/verification").json()["email"]
    assert status["owner"] == "alice" and not status["mine"] and status["id"] is None
    path = base + f"/email-code-runs/{identity}"
    assert b.get(path).status_code == 404
    assert admin.get(path).status_code == 404
    received = now() - timedelta(seconds=30)
    with Session.begin() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.started_at - run.since == timedelta(minutes=2)
        assert run.deadline - run.started_at == timedelta(minutes=5)
        run.status = "found"
        run.code_encrypted = cipher().encrypt(b"123456").decode()
        run.received_at = received
    result = a.get(path).json()
    assert result["code"] == "123456"
    assert (
        datetime.fromisoformat(result["received_at"].replace("Z", "+00:00")) == received
    )
    assert "123456" not in b.get(base + "/verification").text
    assert start(b, account).status_code == 201
    assert a.delete(path).json()["status"] == "cancelled"
    assert "code" not in a.get(path).json()


def test_concurrent_email_starts_have_one_winner(make_user, make_account):
    alice, a = make_user("alice")
    bob, b = make_user("bob")
    account = make_account(users=[alice["id"], bob["id"]], capacity=2)
    claim(a, account)
    claim(b, account)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda c: start(c, account), [a, b]))
    assert sorted(r.status_code for r in responses) == [201, 409]


def test_timeout_revocation_and_logout_release_and_clear(
    admin, make_user, make_account
):
    user, client = make_user()
    account = make_account(users=[user["id"]])
    owned = claim(client, account)
    base = f"/api/v1/accounts/{account['id']}"
    identity = start(client, account).json()["id"]
    with Session.begin() as db:
        run = db.get(EmailCodeRun, identity)
        run.status = "found"
        run.code_encrypted = cipher().encrypt(b"123456").decode()
        run.received_at = now()
        run.deadline = now() - timedelta(seconds=1)
    assert "code" not in client.get(base + f"/email-code-runs/{identity}").json()
    second = start(client, account).json()["id"]
    with Session.begin() as db:
        db.get(Claim, owned["id"]).invalidated_at = now()
    assert client.get(base + f"/email-code-runs/{second}").status_code == 403
    assert start(admin, account).status_code == 201
    with Session.begin() as db:
        assert db.get(EmailCodeRun, identity).code_encrypted is None
        assert db.get(EmailCodeRun, second).status == "cancelled"
    client.post("/api/v1/auth/logout")
    with Session.begin() as db:
        write_lock(db)
        v.reconcile_runs(db)


def test_timeout_setting_applies_only_to_new_jobs(admin, make_account):
    settings = admin.get("/api/v1/settings").json()
    assert settings["email_code_timeout_minutes"] == 5
    assert (
        admin.put(
            "/api/v1/settings", json={**settings, "email_code_timeout_minutes": 31}
        ).status_code
        == 422
    )
    assert (
        admin.put(
            "/api/v1/settings", json={**settings, "email_code_timeout_minutes": 1}
        ).status_code
        == 200
    )
    account = make_account()
    identity = start(admin, account).json()["id"]
    with Session.begin() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.deadline - run.started_at == timedelta(minutes=1)
        db.get(Settings, 1).email_code_timeout_minutes = 10
        assert run.deadline - run.started_at == timedelta(minutes=1)


def test_upload_database_wait_does_not_block_event_loop(
    admin, make_account, monkeypatch
):
    account = make_account()
    entered, release = ThreadEvent(), ThreadEvent()
    timed_out = []
    original = v.save_totp

    def slow_save(*args):
        entered.set()
        timed_out.append(not release.wait(3))
        return original(*args)

    monkeypatch.setattr(v, "save_totp", slow_save)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies=admin.cookies,
            headers=dict(admin.headers),
        ) as client:
            task = asyncio.create_task(
                client.put(
                    f"/api/v1/accounts/{account['id']}/two-factor/service?version=0",
                    content=qr_image(),
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert (
                    await asyncio.wait_for(client.get("/api/health"), 1)
                ).status_code == 200
            finally:
                release.set()
            assert (await task).status_code == 200

    asyncio.run(scenario())
    assert timed_out == [False]
