from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import plugins
from app.db import Session
from app.email_worker import EmailWorker, lease_next, save_candidate, snapshot
from app.mail import Candidate, find_candidate
from app.models import Account, EmailCodeRun, Event, now
from app.plugins import EmailTemplatePlugin, MailBackendPlugin
from test_email_worker import Mailbox, start


@pytest.fixture
def alternate(monkeypatch):
    mailbox = Mailbox()
    calls = []

    def factory(email, password):
        calls.append((email, password))
        return mailbox

    monkeypatch.setitem(
        plugins.MAIL_BACKENDS,
        "fixture",
        MailBackendPlugin("fixture", "Fixture Mail", factory),
    )
    return mailbox, calls


def test_catalog_and_write_permissions(admin, make_user, make_account, alternate):
    _, user = make_user()
    account = make_account()
    catalog = admin.get("/api/v1/mail-backends")
    assert catalog.status_code == 200
    assert catalog.json() == {
        "default": "mailcom",
        "items": [
            {"id": "mailcom", "name": "mail.com"},
            {"id": "fixture", "name": "Fixture Mail"},
        ],
    }
    assert user.get("/api/v1/mail-backends").status_code == 403
    assert (
        user.patch(
            f"/api/v1/accounts/{account['id']}", json={"mail_backend": None}
        ).status_code
        == 403
    )
    data = {
        "tier": "5x",
        "text": "new@example.test----fictional----fictional",
        "mail_backend": "fixture",
    }
    for path in ("/account-imports/preview", "/account-imports"):
        assert user.post("/api/v1" + path, json=data).status_code == 403


@pytest.mark.parametrize("backend", ["mailcom", "fixture", None])
def test_batch_import_preview_and_individual_edit(admin, alternate, backend):
    data = {
        "tier": "20x",
        "text": "first@example.test----fictional----fictional\nsecond@example.test----fictional----fictional",
        "mail_backend": backend,
    }
    preview = admin.post("/api/v1/account-imports/preview", json=data)
    assert preview.status_code == 200
    assert len(preview.json()["rows"]) == 2
    assert all(row["mail_backend"] == backend for row in preview.json()["rows"])
    assert admin.post("/api/v1/account-imports", json=data).json() == {"count": 2}
    accounts = admin.get("/api/v1/accounts").json()
    assert all(a["mail_backend"] == backend for a in accounts)
    target = accounts[0]
    path = f"/api/v1/accounts/{target['id']}"
    assert admin.patch(path, json={"tier": "5x"}).json()["mail_backend"] == backend
    for selected in (None, "fixture", "mailcom"):
        result = admin.patch(path, json={"mail_backend": selected})
        assert result.status_code == 200
        assert result.json()["mail_backend"] == selected
        assert admin.get(path).json()["mail_backend"] == selected
    assert (
        admin.get(f"/api/v1/accounts/{accounts[1]['id']}").json()["mail_backend"]
        == backend
    )
    with Session() as db:
        events = list(db.scalars(select(Event).where(Event.kind == "account_updated")))
        assert events[-1].details["fields"] == ["mail_backend"]


def test_omitted_backend_uses_registered_default(admin):
    data = {"tier": "5x", "text": "default@example.test----fictional----fictional"}
    assert (
        admin.post("/api/v1/account-imports/preview", json=data).json()["rows"][0][
            "mail_backend"
        ]
        == "mailcom"
    )
    assert admin.post("/api/v1/account-imports", json=data).status_code == 201
    assert admin.get("/api/v1/accounts").json()[0]["mail_backend"] == "mailcom"


@pytest.mark.parametrize(
    "backend", ["unknown", "", "app.plugins.mailcom", "https://example.test/plugin"]
)
def test_invalid_plugin_ids_reject_whole_batch_and_edit(admin, make_account, backend):
    account = make_account()
    data = {
        "tier": "5x",
        "text": "new@example.test----fictional----fictional",
        "mail_backend": backend,
    }
    for path in ("/account-imports/preview", "/account-imports"):
        assert admin.post("/api/v1" + path, json=data).status_code == 422
    path = f"/api/v1/accounts/{account['id']}"
    assert (
        admin.patch(path, json={"mail_backend": backend, "tier": "20x"}).status_code
        == 422
    )
    assert admin.get(path).json()["tier"] == "5x"
    assert len(admin.get("/api/v1/accounts").json()) == 1


def test_disabled_backend_keeps_credentials_and_service_totp(
    admin, make_user, make_account
):
    person, user = make_user()
    account = make_account(users=[person["id"]], mail_backend=None)
    assert (
        user.post("/api/v1/claims", json={"account_id": account["id"]}).status_code
        == 201
    )
    base = f"/api/v1/accounts/{account['id']}"
    for client in (admin, user):
        status = client.get(base + "/verification").json()
        assert status["email_available"] is False
        assert "未启用" in status["email_unavailable_reason"]
        assert "service" in status["two_factor"]
        assert client.get(base + "/credentials").status_code == 200
        assert (
            client.post(
                base + "/email-code-runs", json={"id": str(uuid4())}
            ).status_code
            == 409
        )
    with Session() as db:
        assert not list(db.scalars(select(EmailCodeRun)))


def test_worker_dispatches_registered_provider(admin, make_account, alternate):
    mailbox, calls = alternate
    account = make_account(mail_backend="fixture")
    identity = start(admin, account)
    worker = EmailWorker()
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert calls == [(account["email"], "fictional-auth")]
    assert mailbox.marks == 1
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.mail_backend == "fixture" and run.status == "found"


@pytest.mark.parametrize(
    "patch",
    [
        {"mail_backend": None},
        {"mail_backend": "fixture"},
        {"auth_password": "changed-fictional-password"},
    ],
)
@pytest.mark.parametrize("found", [False, True])
def test_admin_change_cancels_jobs_clears_results_and_fences_old_lease(
    admin, make_account, alternate, patch, found
):
    account = make_account()
    identity = start(admin, account)
    lease = lease_next()
    assert save_candidate(*lease, Candidate("shared-id", "123456", now()))
    if found:
        with Session.begin() as db:
            db.get(EmailCodeRun, identity).status = "found"
    base = f"/api/v1/accounts/{account['id']}"
    assert admin.patch(base, json=patch).status_code == 200
    assert admin.get(base + "/verification").json()["email_config_version"] == 1
    assert snapshot(*lease) is None
    result = admin.get(base + "/email-code-runs/" + identity).json()
    assert result["status"] == "cancelled" and "code" not in result
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.code_encrypted is None and run.received_at is None
    # Returning to the same backend must not revive an old job or candidate.
    assert admin.patch(base, json={"mail_backend": "mailcom"}).status_code == 200
    assert (
        admin.get(base + "/email-code-runs/" + identity).json()["status"] == "cancelled"
    )


def test_switch_during_mark_preflight_cannot_write_to_old_backend(
    admin, make_account, alternate
):
    mailbox, _ = alternate
    account = make_account(mail_backend="fixture")
    identity = start(admin, account)
    mailbox.before_mark = lambda: admin.patch(
        f"/api/v1/accounts/{account['id']}", json={"mail_backend": None}
    )
    worker = EmailWorker()
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert mailbox.marks == 0
    with Session() as db:
        assert db.get(EmailCodeRun, identity).status == "cancelled"


def test_message_deduplication_is_scoped_to_backend(admin, make_account, alternate):
    account = make_account()
    first = start(admin, account)
    candidate = Candidate("shared-id", "123456", now())
    assert save_candidate(*lease_next(), candidate)
    assert (
        admin.patch(
            f"/api/v1/accounts/{account['id']}", json={"mail_backend": "fixture"}
        ).status_code
        == 200
    )
    start(admin, account)
    lease = lease_next()
    assert "shared-id" not in snapshot(*lease)["excluded"]
    assert save_candidate(*lease, candidate)
    with Session() as db:
        assert db.get(EmailCodeRun, first).status == "cancelled"


def test_removed_plugin_fails_closed_without_fallback(
    admin, make_account, alternate, monkeypatch
):
    mailbox, calls = alternate
    account = make_account(mail_backend="fixture")
    identity = start(admin, account)
    monkeypatch.delitem(plugins.MAIL_BACKENDS, "fixture")
    base = f"/api/v1/accounts/{account['id']}"
    assert admin.get(base + "/verification").json()["email_available"] is False
    assert (
        admin.post(base + "/email-code-runs", json={"id": str(uuid4())}).status_code
        == 503
    )
    worker = EmailWorker()
    try:
        worker.tick()
    finally:
        worker.close()
    assert calls == [] and mailbox.marks == 0
    with Session() as db:
        assert db.get(EmailCodeRun, identity).status == "cancelled"
        assert db.get(Account, account["id"]).mail_backend == "fixture"


def test_new_template_runs_without_core_or_provider_changes(
    admin, make_account, alternate, monkeypatch
):
    class TemplateB:
        def matches(self, sender, subject):
            return subject == "ExampleService"

        def extract_code(self, raw):
            return "ABCD-1234" if b"123456" in raw else None

    monkeypatch.delenv("MAIL_CODE_SENDER")
    monkeypatch.setitem(
        plugins.EMAIL_TEMPLATES,
        "fixture_b",
        EmailTemplatePlugin("fixture_b", "Fixture B", TemplateB),
    )
    account = make_account(mail_backend="fixture")
    identity = start(admin, account)
    worker = EmailWorker()
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert (
        admin.get(
            f"/api/v1/accounts/{account['id']}/email-code-runs/{identity}"
        ).json()["code"]
        == "ABCD-1234"
    )


def test_overlapping_templates_cannot_publish_conflicting_codes():
    class Template:
        def __init__(self, code):
            self.code = code

        def matches(self, sender, subject):
            return True

        def extract_code(self, raw):
            return self.code

    mailbox = Mailbox()
    stamp = now()
    assert (
        find_candidate(
            mailbox,
            [Template("first"), Template("second")],
            stamp - timedelta(minutes=2),
            stamp + timedelta(minutes=5),
            set(),
        )
        is None
    )
    assert mailbox.marks == 0
