import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.mail import Candidate, MailError, OAuthCredential, find_candidate
from app.plugins.backends.outlook import (
    OutlookGraphClient,
    OutlookManualOAuthProvider,
    OutlookOAuthProvider,
    pkce_pair,
    received_time,
)
from app.plugins.templates.six_digit_code import SixDigitCodeTemplate
from test_email_templates import mail


class GraphFixture:
    def __init__(self, stamp):
        self.stamp = stamp
        self.read = False
        self.raw_calls = 0
        self.patch_calls = 0
        self.token_calls = 0

    def __call__(self, request):
        if request.url.host == "login.microsoftonline.com":
            self.token_calls += 1
            fields = parse_qs(request.content.decode())
            assert fields["grant_type"] == ["refresh_token"]
            assert fields["client_id"] == ["fictional-client"]
            return httpx.Response(
                200,
                json={
                    "access_token": "fictional-access",
                    "refresh_token": "fictional-rotated",
                    "expires_in": 300,
                },
            )
        assert request.url.host == "graph.microsoft.com"
        assert request.headers["authorization"] == "Bearer fictional-access"
        if request.url.path.endswith("/mailFolders/inbox/messages"):
            assert request.url.params["$filter"].startswith(
                "receivedDateTime ge "
            )
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "message-1",
                            "isRead": self.read,
                            "from": {
                                "emailAddress": {
                                    "name": "Example Service",
                                    "address": "noreply@login.example.test",
                                }
                            },
                            "subject": "ExampleService login code",
                            "receivedDateTime": self.stamp.isoformat().replace(
                                "+00:00", "Z"
                            ),
                        }
                    ]
                },
            )
        if request.url.path.endswith("/$value"):
            self.raw_calls += 1
            return httpx.Response(200, content=mail())
        if request.method == "PATCH":
            self.patch_calls += 1
            self.read = json.loads(request.content)["isRead"]
            return httpx.Response(204)
        if request.url.path.endswith("/messages/message-1"):
            return httpx.Response(200, json={"isRead": self.read})
        raise AssertionError(f"unexpected Graph request: {request.url}")


def make_client(fixture):
    return OutlookGraphClient(
        "fixture@outlook.com",
        "fictional-mailbox-password",
        OAuthCredential(
            "outlook", "fictional-client", None, "consumers", "fictional-refresh", 1
        ),
        transport=httpx.MockTransport(fixture),
    )


def test_graph_oauth_lists_reads_and_marks_unread_message():
    stamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    fixture = GraphFixture(stamp)
    client = make_client(fixture)
    rotated = []
    client.set_refresh_token_callback(rotated.append)
    template = SixDigitCodeTemplate(
        "noreply@login.example.test", "exampleservice"
    )
    try:
        candidate = find_candidate(
            client,
            [template],
            stamp - timedelta(minutes=2),
            stamp + timedelta(minutes=5),
            set(),
        )
        assert candidate == Candidate("message-1", "123456", stamp)
        assert fixture.raw_calls == 1 and fixture.patch_calls == 0
        client.mark_read(candidate.message_id, deadline=stamp + timedelta(minutes=5))
        assert fixture.patch_calls == 1 and fixture.read
        assert fixture.token_calls == 1
        assert rotated == ["fictional-rotated"]
    finally:
        client.close()


def test_plain_password_is_rejected_without_network():
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(500)

    client = OutlookGraphClient(
        "fixture@outlook.com", "ordinary-password", transport=httpx.MockTransport(transport)
    )
    client.budget = 1e20
    try:
        with pytest.raises(MailError, match="configuration"):
            list(
                client.iter_unread(
                    datetime.now(timezone.utc) - timedelta(minutes=1),
                    datetime.now(timezone.utc) + timedelta(minutes=1),
                )
            )
        assert calls == []
    finally:
        client.close()


def test_authorization_code_exchange_uses_pkce_and_checks_graph_identity():
    verifier, challenge = pkce_pair()
    seen = {}

    def transport(request):
        seen[request.url.host + request.url.path] = request
        if request.url.host == "login.microsoftonline.com":
            fields = parse_qs(request.content.decode())
            assert fields["grant_type"] == ["authorization_code"]
            assert fields["code_verifier"] == [verifier]
            assert fields["client_id"] == ["fictional-client"]
            return httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "refresh"},
            )
        assert request.url.path == "/v1.0/me"
        assert request.headers["authorization"] == "Bearer access"
        return httpx.Response(200, json={"mail": "fixture@outlook.com"})

    provider = OutlookOAuthProvider(transport=httpx.MockTransport(transport))
    url = provider.authorization_url(
        client_id="fictional-client",
        tenant="consumers",
        redirect_uri="https://example.test/api/v1/mail-oauth/callback",
        state="fictional-state",
        code_challenge=challenge,
    )
    assert "code_challenge=" + challenge in url
    token = provider.exchange_code(
        code="fictional-code",
        code_verifier=verifier,
        client_id="fictional-client",
        client_secret=None,
        tenant="consumers",
        redirect_uri="https://example.test/api/v1/mail-oauth/callback",
    )
    assert token.refresh_token == "refresh"
    assert token.authorized_email == "fixture@outlook.com"


def test_authorization_code_exchange_rejects_bad_redirect_uri():
    provider = OutlookOAuthProvider()
    with pytest.raises(MailError, match="configuration"):
        provider.authorization_url(
            client_id="fictional-client",
            tenant="consumers",
            redirect_uri="http://example.test/api/v1/mail-oauth/callback",
            state="state",
            code_challenge="challenge",
        )


def test_manual_authorization_uses_public_client_and_localhost_redirect():
    provider = OutlookManualOAuthProvider()
    url = provider.authorization_url(
        client_id="fictional-client",
        tenant="common",
        redirect_uri="https://localhost",
        state="fictional-state",
    )
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["fictional-client"]
    assert query["redirect_uri"] == ["https://localhost"]
    assert query["state"] == ["fictional-state"]
    assert query["response_type"] == ["code"]
    assert "code_challenge" not in query
    assert "https://graph.microsoft.com/User.Read" in query["scope"][0]
    assert "https://graph.microsoft.com/Mail.ReadWrite" in query["scope"][0]
    assert "offline_access" in query["scope"][0]


def test_manual_authorization_code_exchange_omits_secret_and_pkce():
    seen = {}

    def transport(request):
        seen[request.url.host + request.url.path] = request
        if request.url.host == "login.microsoftonline.com":
            fields = parse_qs(request.content.decode())
            assert fields["grant_type"] == ["authorization_code"]
            assert fields["client_id"] == ["fictional-client"]
            assert fields["redirect_uri"] == ["https://localhost"]
            assert "code_verifier" not in fields
            assert "client_secret" not in fields
            return httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "refresh"},
            )
        assert request.url.path == "/v1.0/me"
        return httpx.Response(200, json={"mail": "fixture@outlook.com"})

    provider = OutlookManualOAuthProvider(transport=httpx.MockTransport(transport))
    token = provider.exchange_code(
        code="fictional-code",
        client_id="fictional-client",
        tenant="common",
        redirect_uri="https://localhost",
    )
    assert token.refresh_token == "refresh"
    assert token.authorized_email == "fixture@outlook.com"


@pytest.mark.parametrize(
    "client_secret,tenant",
    [("secret", "common"), (None, "consumers")],
)
def test_manual_provider_rejects_confidential_or_noncommon_config(client_secret, tenant):
    with pytest.raises(MailError, match="configuration"):
        OutlookManualOAuthProvider.validate_config(
            "fictional-client", client_secret, tenant
        )


@pytest.mark.parametrize(
    "value",
    [None, "2026-09-28", "not-a-date", "2026-09-28T12:00:00"],
)
def test_received_time_requires_timezone(value):
    with pytest.raises(MailError, match="received_time"):
        received_time(value)


@pytest.mark.parametrize(
    "url",
    [
        "http://graph.microsoft.com/v1.0/me/messages",
        "https://graph.microsoft.com.evil.example/v1.0/me/messages",
        "https://user:pass@graph.microsoft.com/v1.0/me/messages",
        "https://graph.microsoft.com:8443/v1.0/me/messages",
    ],
)
def test_rejects_unexpected_graph_urls(url):
    with pytest.raises(MailError, match="protocol"):
        OutlookGraphClient.safe_url(url)
