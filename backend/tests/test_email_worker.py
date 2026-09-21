from datetime import timedelta
from uuid import uuid4

from app.config import cipher
from app.db import Session
from app.email_worker import EmailWorker, lease_next, save_candidate, snapshot
from app.mail import Candidate, MailError, Message
from test_email_templates import mail
from app.models import EmailCodeRun, TwoFactor, now


class Mailbox:
    def __init__(self, *args):
        self.finds = self.marks = 0
        self.fail_mark = False
        self.before_mark = lambda: None

    def iter_unread(self, since, deadline):
        self.finds += 1
        yield Message(
            "fictional-provider-id",
            "noreply@login.example.test",
            "ExampleService",
            now() - timedelta(seconds=1),
        )

    def read_raw(self, message_id):
        return mail()

    def mark_read(self, identity, *, before_write=None, deadline=None):
        self.before_mark()
        if before_write is not None and not before_write():
            raise MailError("cancelled")
        self.marks += 1
        if self.fail_mark:
            raise MailError("network", True)

    def close(self):
        pass


def start(admin, account):
    response = admin.post(
        f"/api/v1/accounts/{account['id']}/email-code-runs", json={"id": str(uuid4())}
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_removed_mail_rules_stop_existing_jobs_before_provider_access(
    admin, make_account, monkeypatch
):
    identity = start(admin, make_account())
    calls = []

    def factory(*args):
        calls.append(True)
        return Mailbox()

    monkeypatch.delenv("MAIL_CODE_SENDER")
    worker = EmailWorker(factory=factory)
    try:
        worker.tick()
    finally:
        worker.close()
    assert not calls
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "cancelled"
        assert run.code_encrypted is None


def test_result_persisted_before_mark_and_restart_resumes(admin, make_account):
    account = make_account()
    identity = start(admin, account)
    mailbox = Mailbox()
    mailbox.fail_mark = True
    worker = EmailWorker(factory=lambda *args: mailbox)

    def inspect():
        with Session() as db:
            run = db.get(EmailCodeRun, identity)
            assert run.status == "reading"
            assert cipher().decrypt(run.code_encrypted.encode()) == b"123456"

    mailbox.before_mark = inspect
    worker.process(*lease_next())
    worker.close()
    with Session.begin() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "reading" and run.error == "network"
        run.next_poll_at = now()
    # Reconstructed worker has no in-memory client; resume the persisted candidate.
    new_mailbox = Mailbox()
    new_mailbox.before_mark = inspect
    resumed = EmailWorker(factory=lambda *args: new_mailbox)
    resumed.process(*lease_next())
    resumed.close()
    with Session() as db:
        run = db.get(EmailCodeRun, identity)
        assert run.status == "found" and run.received_at is not None
    assert mailbox.finds == 1 and new_mailbox.finds == 0 and new_mailbox.marks == 1


def test_cancel_during_fetch_fences_late_result_and_allows_new_run(admin, make_account):
    account = make_account()
    identity = start(admin, account)
    lease = lease_next()
    base = f"/api/v1/accounts/{account['id']}/email-code-runs"
    assert admin.delete(base + "/" + identity).status_code == 200
    assert not save_candidate(*lease, Candidate("fictional-id", "123456", now()))
    assert snapshot(*lease) is None
    assert start(admin, account) != identity


def test_cancelled_candidate_cannot_be_disclosed_by_new_owner_run(admin, make_account):
    account = make_account()
    identity = start(admin, account)
    first = lease_next()
    candidate = Candidate("fictional-id", "123456", now())
    assert save_candidate(*first, candidate)
    assert (
        admin.delete(
            f"/api/v1/accounts/{account['id']}/email-code-runs/{identity}"
        ).status_code
        == 200
    )
    start(admin, account)
    second = lease_next()
    assert "fictional-id" in snapshot(*second)["excluded"]
    assert not save_candidate(*second, candidate)


def test_expired_lease_cannot_complete_and_code_time_window_is_enforced(
    admin, make_account
):
    account = make_account()
    identity = start(admin, account)
    first = lease_next()
    with Session.begin() as db:
        db.get(EmailCodeRun, identity).lease_until = now() - timedelta(seconds=1)
    second = lease_next()
    assert second[1] != first[1]
    assert snapshot(*first) is None
    assert not save_candidate(
        *second, Candidate("fictional-id", "123456", now() - timedelta(minutes=3))
    )
    assert not save_candidate(
        *second, Candidate("fictional-id", "123456", now() + timedelta(seconds=2))
    )
    assert save_candidate(*second, Candidate("fictional-id", "123456", now()))


def test_mail_polling_does_not_use_saved_mail_2fa(admin, make_account):
    account = make_account()
    user = admin.get("/api/v1/auth/me").json()["user"]
    with Session.begin() as db:
        db.add(
            TwoFactor(
                account_id=account["id"],
                kind="mail",
                uri_encrypted="legacy-configuration-must-not-be-decrypted",
                updated_by=user["id"],
            )
        )
    identity = start(admin, account)
    mailbox = Mailbox()
    received = []

    def factory(email, password, otp_uri=None):
        received.append(otp_uri)
        return mailbox

    worker = EmailWorker(factory=factory)
    try:
        worker.process(*lease_next())
    finally:
        worker.close()
    assert received == [None]
    with Session() as db:
        assert db.get(EmailCodeRun, identity).status == "found"
        assert db.get(TwoFactor, (account["id"], "mail")) is not None
