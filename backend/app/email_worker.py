"""Bounded mailbox work, fenced by durable leases. Network IO has no DB lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import inspect
from uuid import uuid4

from sqlalchemy import select

from .config import cipher
from .db import Session, write_lock
from .models import Account, EmailCodeRun, MailOAuthCredential, now
from .mail import MailError, OAuthCredential, find_candidate, valid_code
from .plugins import get_mail_tool
from . import verification as v


def lease_next():
    with Session.begin() as db:
        write_lock(db)
        stamp = now()
        v.reconcile_runs(db, stamp)
        run = db.scalar(
            select(EmailCodeRun)
            .where(
                EmailCodeRun.status.in_(v.ACTIVE),
                EmailCodeRun.next_poll_at <= stamp,
                (EmailCodeRun.lease_until.is_(None))
                | (EmailCodeRun.lease_until <= stamp),
            )
            .order_by(EmailCodeRun.next_poll_at)
            .limit(1)
        )
        if run is None:
            return None
        run.lease_token = str(uuid4())
        run.lease_until = stamp + timedelta(seconds=120)
        return run.id, run.lease_token


def owned_run(db, run_id, token):
    run = db.get(EmailCodeRun, run_id)
    stamp = now()
    if (
        not run
        or run.lease_token != token
        or run.status not in v.ACTIVE
        or not run.lease_until
        or run.lease_until <= stamp
    ):
        return None
    if run.deadline <= stamp:
        v.finish(run, "timed_out")
        return None
    if not v.valid_owner(db, run, stamp):
        v.finish(run, "cancelled")
        return None
    return run


def snapshot(run_id, token):
    with Session.begin() as db:
        write_lock(db)
        run = owned_run(db, run_id, token)
        if run is None:
            return None
        account = db.get(Account, run.account_id)
        tool = get_mail_tool(run.mail_tool)
        oauth = None
        if tool and tool.backend.oauth_provider:
            credential = db.get(MailOAuthCredential, (account.id, tool.backend.id))
            if (
                not credential
                or credential.status != "active"
                or credential.authorized_email.casefold() != account.email.casefold()
            ):
                return None
            oauth = OAuthCredential(
                backend_id=tool.backend.id,
                client_id=cipher()
                .decrypt(credential.client_id_encrypted.encode())
                .decode(),
                client_secret=(
                    cipher().decrypt(credential.client_secret_encrypted.encode()).decode()
                    if credential.client_secret_encrypted
                    else None
                ),
                tenant=credential.tenant,
                refresh_token=cipher()
                .decrypt(credential.refresh_token_encrypted.encode())
                .decode(),
                version=credential.version,
            )
        return dict(
            account_id=account.id,
            mail_tool=run.mail_tool,
            mail_backend=run.mail_backend,
            tool_config_hash=run.tool_config_hash,
            email=account.email,
            password=cipher()
            .decrypt(account.auth_password_encrypted.encode())
            .decode(),
            oauth=oauth,
            since=run.since,
            deadline=run.deadline,
            message_id=run.message_id,
            excluded=set(
                db.scalars(
                    select(EmailCodeRun.message_id).where(
                        EmailCodeRun.account_id == account.id,
                        EmailCodeRun.mail_backend == run.mail_backend,
                        EmailCodeRun.message_id.is_not(None),
                        EmailCodeRun.id != run.id,
                    )
                )
            ),
        )


def save_candidate(run_id, token, candidate):
    with Session.begin() as db:
        write_lock(db)
        run = owned_run(db, run_id, token)
        if run is None:
            return False
        if (
            not candidate.received_at.tzinfo
            or not run.since <= candidate.received_at <= min(now(), run.deadline)
            or not valid_code(candidate.code)
        ):
            return False
        other = db.scalar(
            select(EmailCodeRun.id).where(
                EmailCodeRun.account_id == run.account_id,
                EmailCodeRun.mail_backend == run.mail_backend,
                EmailCodeRun.message_id == candidate.message_id,
                EmailCodeRun.id != run.id,
            )
        )
        if other:
            return False
        run.message_id = candidate.message_id
        run.received_at = candidate.received_at
        run.code_encrypted = cipher().encrypt(candidate.code.encode()).decode()
        run.status = "reading"
        return True


def complete(run_id, token, found=False, error=None, retry=True):
    with Session.begin() as db:
        write_lock(db)
        run = owned_run(db, run_id, token)
        if run is None:
            return
        if found:
            v.finish(run, "found")
        elif error and not retry:
            v.finish(run, "failed", error)
        else:
            run.next_poll_at = now() + timedelta(seconds=5)
            run.lease_token = run.lease_until = None
            run.error = error


def mark_oauth_reauthorize(data):
    oauth = data.get("oauth") if data else None
    if not oauth:
        return
    expected_version = data.get("oauth_version", oauth.version)
    with Session.begin() as db:
        write_lock(db)
        credential = db.get(
            MailOAuthCredential, (data["account_id"], oauth.backend_id)
        )
        if credential and credential.version == expected_version:
            credential.status = "reauthorize_required"
            credential.version += 1
            credential.updated_at = now()
            account = db.get(Account, data["account_id"])
            if account:
                account.mail_config_version += 1
                v.cancel_account_runs(db, account.id)


def persist_rotated_refresh(data, replacement):
    """Persist a provider-rotated token before the current Graph call returns."""
    oauth = data.get("oauth") if data else None
    if not oauth or not isinstance(replacement, str) or not replacement:
        return
    expected_version = data.get("oauth_version", oauth.version)
    try:
        with Session.begin() as db:
            write_lock(db)
            credential = db.get(
                MailOAuthCredential, (data["account_id"], oauth.backend_id)
            )
            # A changed version means an administrator reauthorized/revoked
            # the account while this worker was in flight. Never overwrite it.
            if credential and credential.version == expected_version:
                credential.refresh_token_encrypted = cipher().encrypt(
                    replacement.encode()
                ).decode()
                credential.version += 1
                credential.updated_at = now()
                data["oauth_version"] = credential.version
    except Exception:
        # The provider may invalidate the previous refresh token as soon as it
        # rotates it. A durable write is therefore part of this poll's success.
        raise MailError("network", True) from None


class EmailWorker:
    def __init__(self, factory=None, slots=4):
        # Optional client factory keeps worker lifecycle tests independent of plugins.
        self.factory = factory
        self.slots = slots
        self.pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="email")
        self.futures = {}
        self.clients = {}

    def close_client(self, run_id):
        item = self.clients.pop(run_id, None)
        if item:
            item[1].close()

    def create_client(self, factory, email, password, oauth):
        # Test/integration factories written against the old two-argument
        # contract remain usable; trusted plugins use the explicit third
        # argument so OAuth can never replace the mailbox password.
        try:
            signature = inspect.signature(factory)
            signature.bind(email, password, oauth)
        except (TypeError, ValueError):
            return factory(email, password)
        return factory(email, password, oauth)

    def process(self, run_id, token):
        data = None
        try:
            data = snapshot(run_id, token)
            if data is None:
                return
            tool = get_mail_tool(data["mail_tool"])
            if tool is None:
                raise MailError("tool_unavailable")
            if tool.template is None or tool.config_hash != data["tool_config_hash"]:
                raise MailError("configuration")
            oauth = data.get("oauth")
            if oauth:
                data["oauth_version"] = oauth.version
            identity = (
                tool.id,
                tool.config_hash,
                data["email"],
                data["password"],
                oauth.version if oauth else 0,
            )
            cached = self.clients.get(run_id)
            if cached and cached[0] != identity:
                self.close_client(run_id)
                cached = None
            if not cached:
                client = self.create_client(
                    self.factory or tool.backend.create,
                    data["email"],
                    data["password"],
                    oauth,
                )
                self.clients[run_id] = (identity, client)
            else:
                client = cached[1]
            if oauth:
                set_callback = getattr(client, "set_refresh_token_callback", None)
                if set_callback is not None:
                    # Cached clients must use this poll's version and snapshot,
                    # not a closure left over from their first polling cycle.
                    set_callback(
                        lambda replacement: persist_rotated_refresh(data, replacement)
                    )
            message_id = data["message_id"]
            if not message_id:
                candidate = find_candidate(
                    client,
                    [tool.template],
                    data["since"],
                    data["deadline"],
                    data["excluded"],
                )
                if candidate is None or not save_candidate(run_id, token, candidate):
                    rotated = getattr(client, "rotated_refresh_token", None)
                    if rotated and oauth:
                        persist_rotated_refresh(data, rotated)
                        client.rotated_refresh_token = None
                    complete(run_id, token)
                    return
                message_id = candidate.message_id
            # Check cancellation/authorization again immediately before the
            # external side effect. The persisted candidate makes crash recovery
            # idempotent, and another job cannot disclose the same message.
            if snapshot(run_id, token) is None:
                return
            client.mark_read(
                message_id,
                before_write=lambda: snapshot(run_id, token) is not None,
                deadline=data["deadline"],
            )
            rotated = getattr(client, "rotated_refresh_token", None)
            if rotated and oauth:
                persist_rotated_refresh(data, rotated)
                client.rotated_refresh_token = None
            complete(run_id, token, found=True)
        except MailError as exc:
            if exc.code == "authentication":
                mark_oauth_reauthorize(data)
            if data is not None:
                cached_client = self.clients.get(run_id, (None, None))[1]
                rotated = getattr(cached_client, "rotated_refresh_token", None)
                if rotated and data.get("oauth"):
                    try:
                        persist_rotated_refresh(data, rotated)
                    except MailError:
                        pass
            complete(run_id, token, error=exc.code, retry=exc.retryable)
            self.close_client(run_id)
        except Exception:
            # No exception strings, URLs, mailbox content or credentials in logs.
            if data is not None:
                client = self.clients.get(run_id, (None, None))[1]
                rotated = getattr(client, "rotated_refresh_token", None)
                if rotated and data.get("oauth"):
                    try:
                        persist_rotated_refresh(data, rotated)
                    except MailError:
                        pass
            complete(run_id, token, error="network", retry=True)
            self.close_client(run_id)

    def tick(self):
        for run_id, future in list(self.futures.items()):
            if future.done():
                del self.futures[run_id]
                # A DB outage can also interrupt error persistence. Expired
                # leases recover these jobs; a failed future must not stall all
                # later work in the process.
                if future.exception() is not None:
                    self.close_client(run_id)
        with Session.begin() as db:
            write_lock(db)
            v.reconcile_runs(db)
            active_ids = set(
                db.scalars(
                    select(EmailCodeRun.id).where(EmailCodeRun.status.in_(v.ACTIVE))
                )
            )
        for run_id in list(self.clients):
            if run_id not in active_ids and run_id not in self.futures:
                self.close_client(run_id)
        while len(self.futures) < self.slots:
            lease = lease_next()
            if lease is None:
                break
            run_id, token = lease
            if run_id in self.futures:
                # A slow provider call must not execute concurrently in-process.
                continue
            self.futures[run_id] = self.pool.submit(self.process, run_id, token)

    def close(self):
        self.pool.shutdown(wait=True)
        for run_id in list(self.clients):
            self.close_client(run_id)
