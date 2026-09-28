import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app import config
from app.config import cipher
from app.db import Session
from app.email_worker import EmailWorker, lease_next
from app.models import EmailCodeRun, MailOAuthCredential, MailOAuthState
from app.plugins import MAIL_BACKENDS
from app.plugins.backends.outlook import OAuthToken, OutlookGraphClient
from test_email_worker import start


def outlook_tool(register_tool):
    register_tool(
        "outlook",
        backend="outlook",
        name="Example OAuth mailbox",
        options={"sender": "noreply@login.example.test", "subject_keyword": "Example"},
    )


def test_admin_authorizes_outlook_without_overwriting_mailbox_password(
    admin, make_account, register_tool, monkeypatch
):
    outlook_tool(register_tool)
    account = make_account(email="fixture@outlook.com", mail_tool="outlook")
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "https://example.test")
    provider = MAIL_BACKENDS["outlook"].oauth_provider
    assert provider is not None
    monkeypatch.setattr(
        provider,
        "exchange_code",
        lambda **kwargs: OAuthToken("fictional-refresh", "fixture@outlook.com"),
    )

    status = admin.get(f"/api/v1/accounts/{account['id']}/verification").json()
    assert status["email_available"] is False
    assert status["mail_oauth"]["configured"] is False

    response = admin.post(
        f"/api/v1/accounts/{account['id']}/mail-oauth/authorize",
        json={
            "client_id": "fictional-client",
            "client_secret": "fictional-secret",
            "tenant": "consumers",
        },
    )
    assert response.status_code == 200, response.text
    authorization_url = response.json()["authorization_url"]
    query = parse_qs(urlsplit(authorization_url).query)
    state = query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    callback = admin.get(
        "/api/v1/mail-oauth/callback",
        params={"state": state, "code": "fictional-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "mail_oauth=success" in callback.headers["location"]

    with Session() as db:
        credential = db.get(MailOAuthCredential, (account["id"], "outlook"))
        assert credential and credential.status == "active"
        assert cipher().decrypt(credential.refresh_token_encrypted.encode()) == b"fictional-refresh"
        assert cipher().decrypt(credential.client_id_encrypted.encode()) == b"fictional-client"
        assert cipher().decrypt(credential.client_secret_encrypted.encode()) == b"fictional-secret"
        assert not db.scalar(
            select(MailOAuthState).where(
                MailOAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()
            )
        )

    assert admin.get(f"/api/v1/accounts/{account['id']}/verification").json()[
        "email_available"
    ] is True
    credentials = admin.get(f"/api/v1/accounts/{account['id']}/credentials").json()
    assert credentials["auth_password"] == "fictional-auth"


def test_oauth_requires_admin_and_identity_match(
    admin, make_user, make_account, register_tool, monkeypatch
):
    outlook_tool(register_tool)
    user, client = make_user("viewer")
    account = make_account(
        email="fixture@outlook.com", users=[user["id"]], mail_tool="outlook"
    )
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "https://example.test")
    assert client.post(
        f"/api/v1/accounts/{account['id']}/mail-oauth/authorize",
        json={"client_id": "fictional-client"},
    ).status_code == 403
    provider = MAIL_BACKENDS["outlook"].oauth_provider
    monkeypatch.setattr(
        provider,
        "exchange_code",
        lambda **kwargs: OAuthToken("refresh", "other@outlook.com"),
    )
    started = admin.post(
        f"/api/v1/accounts/{account['id']}/mail-oauth/authorize",
        json={"client_id": "fictional-client"},
    )
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    result = admin.get(
        "/api/v1/mail-oauth/callback",
        params={"state": state, "code": "code"},
        follow_redirects=False,
    )
    assert "mail_oauth=mismatch" in result.headers["location"]
    with Session() as db:
        assert db.get(MailOAuthCredential, (account["id"], "outlook")) is None


def test_cancel_oauth_clears_credential_but_not_original_password(
    admin, make_account, register_tool, monkeypatch
):
    outlook_tool(register_tool)
    account = make_account(email="fixture@outlook.com", mail_tool="outlook")
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "https://example.test")
    with Session.begin() as db:
        from app.models import User

        user = db.query(User).filter(User.username == "admin").one()
        db.add(
            MailOAuthCredential(
                account_id=account["id"],
                backend_id="outlook",
                client_id_encrypted=cipher().encrypt(b"client").decode(),
                client_secret_encrypted=None,
                tenant="consumers",
                refresh_token_encrypted=cipher().encrypt(b"refresh").decode(),
                authorized_email=account["email"],
                status="active",
                version=1,
                updated_by=user.id,
            )
        )
    response = admin.delete(f"/api/v1/accounts/{account['id']}/mail-oauth")
    assert response.status_code == 200
    with Session() as db:
        assert db.get(MailOAuthCredential, (account["id"], "outlook")) is None
    assert admin.get(f"/api/v1/accounts/{account['id']}/credentials").json()[
        "auth_password"
    ] == "fictional-auth"


def test_revoke_invalidates_pending_state_and_callback_race(
    admin, make_account, register_tool, monkeypatch
):
    outlook_tool(register_tool)
    account = make_account(email="fixture@outlook.com", mail_tool="outlook")
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "https://example.test")
    provider = MAIL_BACKENDS["outlook"].oauth_provider
    started = admin.post(
        f"/api/v1/accounts/{account['id']}/mail-oauth/authorize",
        json={"client_id": "fictional-client"},
    )
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    with Session() as db:
        assert db.get(MailOAuthState, hashlib.sha256(state.encode()).hexdigest())

    def exchange_then_revoke(**kwargs):
        response = admin.delete(f"/api/v1/accounts/{account['id']}/mail-oauth")
        assert response.status_code == 200
        return OAuthToken("refresh", "fixture@outlook.com")

    monkeypatch.setattr(provider, "exchange_code", exchange_then_revoke)
    callback = admin.get(
        "/api/v1/mail-oauth/callback",
        params={"state": state, "code": "code"},
        follow_redirects=False,
    )
    assert "mail_oauth=failed" in callback.headers["location"]
    with Session() as db:
        assert db.get(MailOAuthCredential, (account["id"], "outlook")) is None
        assert not db.get(
            MailOAuthState, hashlib.sha256(state.encode()).hexdigest()
        )


@pytest.mark.parametrize("outcome", ["empty", "network", "cancel"])
def test_rotation_is_durable_before_further_mailbox_work(
    admin, make_account, register_tool, outcome
):
    outlook_tool(register_tool)
    account = make_account(email="fixture@outlook.com", mail_tool="outlook")
    actor = admin.get("/api/v1/auth/me").json()["user"]
    with Session.begin() as db:
        db.add(
            MailOAuthCredential(
                account_id=account["id"],
                backend_id="outlook",
                client_id_encrypted=cipher().encrypt(b"fictional-client").decode(),
                client_secret_encrypted=None,
                tenant="consumers",
                refresh_token_encrypted=cipher().encrypt(b"original-refresh").decode(),
                authorized_email=account["email"],
                status="active",
                version=1,
                updated_by=actor["id"],
            )
        )
    identity = start(admin, account)
    requests = []

    def transport(request):
        requests.append(request.url.host)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "fictional-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 300,
                },
            )
        # A provider request after the token response must already see the
        # new durable token, even when this poll never finds a code.
        with Session() as db:
            saved = db.get(MailOAuthCredential, (account["id"], "outlook"))
            assert saved.version == 2
            assert cipher().decrypt(saved.refresh_token_encrypted.encode()) == b"new-refresh"
        if outcome == "network":
            return httpx.Response(503)
        if outcome == "cancel":
            assert admin.delete(
                f"/api/v1/accounts/{account['id']}/email-code-runs/{identity}"
            ).status_code == 200
        return httpx.Response(200, json={"value": []})

    worker = EmailWorker(
        factory=lambda email, password, oauth: OutlookGraphClient(
            email, password, oauth, transport=httpx.MockTransport(transport)
        )
    )
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert requests == ["login.microsoftonline.com", "graph.microsoft.com"]
    with Session() as db:
        saved = db.get(MailOAuthCredential, (account["id"], "outlook"))
        assert saved.version == 2 and saved.status == "active"
        run = db.get(EmailCodeRun, identity)
        assert run.status == ("cancelled" if outcome == "cancel" else "pending")
        assert run.code_encrypted is None
