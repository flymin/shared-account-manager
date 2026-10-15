import json
import gzip
import time
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.mail import Candidate, MailError, find_candidate
from app.plugins import get_mail_tool
from app.plugins.backends.gmxcom import GMXComClient
from app.plugins.backends.mailcom import MailComClient, receipt_time
from test_email_templates import mail


def find_code(client, *args):
    return find_candidate(client, [get_mail_tool("mailcom").template], *args)


class ProviderFixture:
    """Synthetic desktop protocol; never authenticates with a real provider."""

    def __init__(self, stamp, domain="mail.com"):
        self.stamp = stamp
        self.domain = domain
        self.read = False
        self.raw_calls = 0
        self.mark_calls = 0
        self.tokens = []
        self.internal_date = int(stamp.timestamp() * 1000)
        self.sender = "ExampleService <noreply@login.example.test>"
        self.subject = "Your ExampleService code"
        self.raw = mail(html=True)
        self.mark_succeeds = True

    def __call__(self, req):
        path = req.url.path
        if req.url.host == f"www.{self.domain}":
            return httpx.Response(
                200,
                text=f'<form action="https://login.{self.domain}/login"><input name="username"><input name="password"><input type="hidden" name="csrf" value="fictional-form-token"></form>',
            )
        if req.url.host == f"login.{self.domain}":
            fields = parse_qs(req.content.decode())
            assert fields["username"] == ["fixture@example.test"]
            assert fields["csrf"] == ["fictional-form-token"]
            assert req.headers["origin"] == f"https://www.{self.domain}"
            assert req.headers["referer"] == f"https://www.{self.domain}/"
            return httpx.Response(
                303, headers={"location": f"https://navigator.{self.domain}/login"}
            )
        if path == "/login":
            return httpx.Response(
                200,
                text=f'new FeatureDetection({{"serverTests":{{"browserversion":true,"notmobile":true}},"redirectUrl":"https://navigator.{self.domain}/halogin"}});',
            )
        if path == "/halogin":
            assert req.url.params["tz"] == "0"
            assert "detectionResult=" in req.headers["cookie"]
            return httpx.Response(
                302,
                headers={
                    "location": f"https://navigator.{self.domain}/?sid=fictional-session"
                },
            )
        if req.url.host == f"navigator.{self.domain}":
            config = {
                "thirdParty": {
                    "navigator": {
                        "apps": {
                            "mail": {
                                "url_map": {"default": f"https://webmailer.{self.domain}/"}
                            }
                        }
                    }
                }
            }
            return httpx.Response(
                200,
                text='<script id="application-config" type="application/json">'
                + json.dumps(config)
                + "</script>",
            )
        if req.url.host == f"webmailer.{self.domain}":
            config = {
                "fd": {
                    name: {"src": f"https://dl.{self.domain}/{name}/component.js"}
                    for name in ("list", "detail")
                }
            }
            return httpx.Response(
                200,
                text='<script id="global-config" type="application/json">'
                + json.dumps(config)
                + "</script>",
            )
        if req.url.host == f"dl.{self.domain}":
            config = {
                "authentication": {
                    "clientId": "fictional-public-client",
                    "clientSecret": "fictional-public-credential",
                    "oauthEndpointToken": f"https://oauth.{self.domain}/token",
                },
                "features": {
                    "mailList": {
                        "endpoints": {
                            "getMails": {
                                "url": f"https://maillist.{self.domain}/Mailbox/Mail"
                            }
                        }
                    },
                    "mailBox": {
                        "endpoints": {
                            "getRawMail": {
                                "url": f"https://webmail.{self.domain}/rawmail/{{mailId}}"
                            },
                            "updateMails": {
                                "url": f"https://webmail.{self.domain}/MailBatchUpdate"
                            },
                        }
                    },
                },
            }
            return httpx.Response(
                200, text="export const config = " + json.dumps(config) + ";"
            )
        if path == "/token":
            scope = parse_qs(req.content.decode())["scope"][0]
            self.tokens.append(scope)
            return httpx.Response(200, json={"access_token": scope, "expires_in": 300})
        if path == "/Mailbox/Mail":
            assert req.url.params["orderBy"] == "INTERNALDATE DESC"
            return httpx.Response(
                200,
                json={
                    "totalCount": 1,
                    "mailListElements": [
                        {
                            "type": "mail",
                            "rawData": {
                                "attribute": {
                                    "internalDate": self.internal_date,
                                    "read": self.read,
                                    "mailIdentifier": "fictional-id",
                                },
                                "mailHeader": {
                                    "date": int(self.stamp.timestamp() * 1000) - 120000,
                                    "from": self.sender,
                                    "subject": self.subject,
                                },
                            },
                        }
                    ],
                },
            )
        if path == "/rawmail/fictional-id":
            assert req.headers["authorization"] == "Bearer mail_mailbox_r"
            self.raw_calls += 1
            return httpx.Response(
                200, content=self.raw, headers={"content-type": "message/rfc822"}
            )
        if path == "/MailBatchUpdate":
            assert req.headers["authorization"] == "Bearer mail_mailbox_w"
            assert json.loads(req.content) == {
                "read": True,
                "mailURIs": ["/Mail/fictional-id"],
            }
            self.mark_calls += 1
            self.read = self.mark_succeeds
            return httpx.Response(200, json={"results": []})
        raise AssertionError("Unexpected synthetic provider route")


def client_for(provider, client_type=MailComClient):
    return client_type(
        "fixture@example.test",
        "fictional-mailbox-password",
        transport=httpx.MockTransport(provider),
    )


def test_full_bootstrap_receipt_metadata_raw_read_and_explicit_mark():
    stamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=5)
    provider = ProviderFixture(stamp)
    client = client_for(provider)
    try:
        value = find_code(
            client, stamp - timedelta(minutes=2), stamp + timedelta(minutes=5), set()
        )
        assert value == Candidate("fictional-id", "123456", stamp)
        assert (
            provider.raw_calls == 1 and not provider.read and provider.mark_calls == 0
        )
        client.mark_read(value.message_id)
        assert provider.read and provider.mark_calls == 1
        assert provider.tokens == ["mail_mailbox_r", "mail_mailbox_w"]
        assert (
            find_code(
                client,
                stamp - timedelta(minutes=2),
                stamp + timedelta(minutes=5),
                set(),
            )
            is None
        )
    finally:
        client.close()


def test_gmx_composes_with_code_template_and_marks_only_matching_mail():
    stamp = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=5)
    provider = ProviderFixture(stamp, domain="gmx.com")
    client = client_for(provider, GMXComClient)
    try:
        value = find_candidate(
            client,
            [get_mail_tool("gmxcom").template],
            stamp - timedelta(minutes=2),
            stamp + timedelta(minutes=5),
            set(),
        )
        assert value == Candidate("fictional-id", "123456", stamp)
        assert provider.raw_calls == 1 and not provider.read
        client.mark_read(value.message_id)
        assert provider.read and provider.mark_calls == 1
        assert provider.tokens == ["mail_mailbox_r", "mail_mailbox_w"]
    finally:
        client.close()


def test_gmx_rejects_cross_provider_urls():
    assert GMXComClient.safe_url("https://webmail.gmx.com/")
    for url in ("https://login.mail.com/", "https://gmx.com.example.test/"):
        with pytest.raises(MailError, match="protocol"):
            GMXComClient.safe_url(url)


@pytest.mark.parametrize(
    "case",
    [
        "old",
        "read",
        "excluded",
        "sender",
        "subject",
        "raw_sender",
        "raw_subject",
        "ambiguous",
    ],
)
def test_unrelated_messages_are_never_marked(case):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=2)
    provider = ProviderFixture(stamp)
    if case == "old":
        provider.internal_date -= 120001
    if case == "read":
        provider.read = True
    if case == "sender":
        provider.sender = "ExampleService <spoof@example.test>"
    if case == "subject":
        provider.subject = "Unrelated mail"
    if case == "raw_sender":
        provider.raw = mail(sender="spoof@example.test")
    if case == "raw_subject":
        provider.raw = mail(subject="Unrelated mail")
    if case == "ambiguous":
        provider.raw = mail("123456\n654321")
    client = client_for(provider)
    try:
        assert (
            find_code(
                client,
                stamp - timedelta(minutes=2),
                stamp + timedelta(minutes=5),
                {"fictional-id"} if case == "excluded" else set(),
            )
            is None
        )
        assert provider.mark_calls == 0
        assert provider.raw_calls == (
            1 if case in {"ambiguous", "raw_sender", "raw_subject"} else 0
        )
    finally:
        client.close()


def test_missing_received_timestamp_is_not_replaced_by_sent_date():
    stamp = datetime.now(timezone.utc)
    provider = ProviderFixture(stamp)
    provider.internal_date = None
    client = client_for(provider)
    try:
        with pytest.raises(MailError, match="received_time"):
            find_code(
                client,
                stamp - timedelta(minutes=2),
                stamp + timedelta(minutes=5),
                set(),
            )
    finally:
        client.close()


def test_mark_read_checks_postcondition():
    stamp = datetime.now(timezone.utc) - timedelta(seconds=2)
    provider = ProviderFixture(stamp)
    provider.mark_succeeds = False
    client = client_for(provider)
    try:
        find_code(
            client, stamp - timedelta(minutes=2), stamp + timedelta(minutes=5), set()
        )
        with pytest.raises(MailError, match="read_failed"):
            client.mark_read("fictional-id")
    finally:
        client.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.mail.com/",
        "https://mail.com.example.test/",
        "https://example.test/",
        "https://user:pass@mail.com/",
        "https://mail.com:8443/",
    ],
)
def test_rejects_unexpected_provider_urls(url):
    with pytest.raises(MailError, match="protocol"):
        MailComClient.safe_url(url)


def test_time_budget_and_timestamp_unit_validation():
    client = client_for(ProviderFixture(datetime.now(timezone.utc)))
    client.budget = time.monotonic() - 1
    with pytest.raises(MailError, match="network"):
        client.request("GET", "https://www.mail.com/")
    client.close()
    for value in (None, "1789737600000", True, 1789737600):
        with pytest.raises(MailError, match="received_time"):
            receipt_time(value)


def test_compressed_provider_response_is_decoded_once():
    client = MailComClient(
        "fixture@example.test",
        "fictional",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                content=gzip.compress(b'{"ok":true}'),
                headers={
                    "content-encoding": "gzip",
                    "content-type": "application/json",
                },
            )
        ),
    )
    client.budget = time.monotonic() + 10
    assert client.request("GET", "https://www.mail.com/").json() == {"ok": True}
    client.close()


def test_http_client_logs_do_not_expose_session_urls(caplog):
    caplog.set_level(logging.INFO)
    client = MailComClient(
        "fixture@example.test",
        "fictional",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok")),
    )
    client.budget = time.monotonic() + 10
    client.request(
        "GET", "https://navigator.mail.com/?sid=fictional-private-session-token"
    )
    client.close()
    assert "fictional-private-session-token" not in caplog.text
    assert "HTTP Request" not in caplog.text


def test_budget_exhaustion_during_body_read_is_retryable(monkeypatch):
    stamp = [1.0]

    def respond(req):
        stamp[0] = 11.0
        return httpx.Response(200, content=b"body")

    client = MailComClient(
        "fixture@example.test", "fictional", transport=httpx.MockTransport(respond)
    )
    monkeypatch.setattr("app.plugins.backends.mailcom.time.monotonic", lambda: stamp[0])
    client.budget = 10.0
    with pytest.raises(MailError) as error:
        client.request("GET", "https://www.mail.com/")
    assert error.value.code == "network" and error.value.retryable
    client.close()


def test_slow_permission_fence_cannot_send_past_deadline(monkeypatch):
    stamp, requests = [9.0], []

    def respond(req):
        requests.append(req)
        return httpx.Response(204)

    def fence():
        stamp[0] = 11.0
        return True

    client = MailComClient(
        "fixture@example.test", "fictional", transport=httpx.MockTransport(respond)
    )
    monkeypatch.setattr("app.plugins.backends.mailcom.time.monotonic", lambda: stamp[0])
    client.budget = 10.0
    with pytest.raises(MailError, match="network"):
        client.request(
            "POST", "https://webmail.mail.com/MailBatchUpdate", before_send=fence
        )
    assert not requests
    client.close()


def test_cancel_during_token_preflight_prevents_mark_request():
    stamp = datetime.now(timezone.utc) - timedelta(seconds=2)
    fixture = ProviderFixture(stamp)
    allowed = [True]

    def respond(req):
        if req.url.path == "/token" and parse_qs(req.content.decode()).get("scope") == [
            "mail_mailbox_w"
        ]:
            allowed[0] = False
        return fixture(req)

    client = client_for(respond)
    try:
        find_code(
            client, stamp - timedelta(minutes=2), stamp + timedelta(minutes=5), set()
        )
        with pytest.raises(MailError, match="cancelled"):
            client.mark_read(
                "fictional-id",
                before_write=lambda: allowed[0],
                deadline=stamp + timedelta(minutes=5),
            )
        assert fixture.mark_calls == 0 and not fixture.read
    finally:
        client.close()


def test_expired_job_cannot_start_a_mark_request():
    stamp = datetime.now(timezone.utc) - timedelta(seconds=2)
    fixture = ProviderFixture(stamp)
    client = client_for(fixture)
    try:
        find_code(
            client, stamp - timedelta(minutes=2), stamp + timedelta(minutes=5), set()
        )
        with pytest.raises(MailError, match="network"):
            client.mark_read("fictional-id", deadline=stamp)
        assert fixture.mark_calls == 0
    finally:
        client.close()


def test_only_authenticator_challenges_receive_totp():
    uri = "otpauth://totp/Fictional?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    posted = []

    def respond(req):
        posted.append(parse_qs(req.content.decode()))
        return httpx.Response(200, text="new FeatureDetection({});")

    client = MailComClient(
        "fixture@example.test", "fictional", uri, transport=httpx.MockTransport(respond)
    )
    client.budget = time.monotonic() + 10
    form = '<form action="/verify"><input name="code" autocomplete="one-time-code"><input name="csrf" type="hidden" value="fictional-csrf"></form>'

    def page(text):
        return httpx.Response(
            200,
            text=text,
            request=httpx.Request("GET", "https://login.mail.com/verify"),
        )

    for context in (
        "Enter the code sent to your email",
        "Enter the code sent by SMS",
        "Recovery code",
        "Enter your code",
    ):
        with pytest.raises(MailError, match="challenge"):
            client.handle_totp(page(context + form))
    assert posted == []
    client.handle_totp(page("Open your authenticator app" + form))
    assert len(posted) == 1 and len(posted[0]["code"][0]) == 6
    assert posted[0]["csrf"] == ["fictional-csrf"]
    client.otp_uri = None
    with pytest.raises(MailError, match="two_factor_required"):
        client.handle_totp(page("Open your authenticator app" + form))
    client.close()
