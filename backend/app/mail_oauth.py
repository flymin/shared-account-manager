"""Account-scoped OAuth lifecycle for mailbox backend plugins."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from . import auth, domain as d
from . import config
from .config import cipher
from .db import Session, write_lock
from .mail import MailError
from .models import (
    Account,
    MailOAuthCredential,
    MailOAuthState,
    LoginSession,
    User,
    now,
)
from .plugins import get_mail_tool
from .schemas import MailOAuthInput

STATE_TTL_SECONDS = 10 * 60


def redirect_uri():
    if not config.PUBLIC_ORIGIN:
        raise HTTPException(503, "服务尚未配置 OAuth 回调地址，请联系管理员")
    return f"{config.PUBLIC_ORIGIN}/api/v1/mail-oauth/callback"


def state_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _provider(account):
    tool = get_mail_tool(account.mail_tool)
    if not tool or not tool.backend.oauth_provider:
        raise HTTPException(409, "该账号未配置支持 OAuth 的邮箱取码工具")
    return tool.backend, tool


def _cleanup(db, stamp):
    db.execute(
        delete(MailOAuthState).where(
            (MailOAuthState.expires_at <= stamp) | MailOAuthState.used_at.is_not(None)
        )
    )


def client_id_mask(value):
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


def status(db, account, user):
    backend, _ = _provider(account)
    row = db.get(MailOAuthCredential, (account.id, backend.id))
    result = {
        "required": True,
        "configured": bool(
            row
            and row.status == "active"
            and row.authorized_email.casefold() == account.email.casefold()
        ),
        "status": row.status if row else "not_configured",
        "updated_at": row.updated_at if row else None,
        "version": row.version if row else 0,
    }
    if user.role == "admin" and row:
        result.update(
            client_id=client_id_mask(
                cipher().decrypt(row.client_id_encrypted.encode()).decode()
            ),
            tenant=row.tenant,
            authorized_email=row.authorized_email,
        )
    if user.role == "admin" and config.PUBLIC_ORIGIN:
        result["redirect_uri"] = redirect_uri()
    return result


def begin(db, user, session, account_id, data: MailOAuthInput):
    auth.admin(user)
    account = d.get_account(db, user, account_id)
    backend, tool = _provider(account)
    provider = backend.oauth_provider
    try:
        client_id, client_secret, tenant = provider.validate_config(
            data.client_id, data.client_secret, data.tenant
        )
    except MailError:
        raise HTTPException(422, "OAuth 应用配置无效") from None
    callback = redirect_uri()
    # PKCE is part of the generic OAuthProvider contract.  Do not fall back to
    # a provider-specific helper here: doing so would silently make a new
    # plugin use the wrong challenge implementation.
    try:
        verifier, challenge = provider.pkce_pair()
    except (MailError, TypeError, ValueError):
        raise HTTPException(503, "OAuth 工具暂时不可用，请联系管理员") from None
    stamp = now()
    _cleanup(db, stamp)
    db.execute(
        delete(MailOAuthState).where(
            MailOAuthState.account_id == account.id,
            MailOAuthState.admin_id == user.id,
        )
    )
    raw_state = secrets.token_urlsafe(32)
    row = MailOAuthState(
        state_hash=state_digest(raw_state),
        account_id=account.id,
        backend_id=backend.id,
        admin_id=user.id,
        session_hash=session.token_hash,
        client_id_encrypted=cipher().encrypt(client_id.encode()).decode(),
        client_secret_encrypted=(
            cipher().encrypt(client_secret.encode()).decode()
            if client_secret
            else None
        ),
        tenant=tenant,
        code_verifier_encrypted=cipher().encrypt(verifier.encode()).decode(),
        expires_at=stamp + timedelta(seconds=STATE_TTL_SECONDS),
    )
    db.add(row)
    db.flush()
    try:
        url = provider.authorization_url(
            client_id=client_id,
            tenant=tenant,
            redirect_uri=callback,
            state=raw_state,
            code_challenge=challenge,
        )
    except MailError:
        raise HTTPException(503, "OAuth 工具暂时不可用，请联系管理员") from None
    d.event(db, "mail_oauth_started", user, account, {"backend": backend.id})
    return {
        "authorization_url": url,
        "expires_at": row.expires_at,
        "redirect_uri": callback,
    }


def _result_redirect(account_id, result):
    base = config.PUBLIC_ORIGIN or "/"
    separator = "&" if "?" in base else "?"
    return RedirectResponse(
        f"{base}{separator}mail_oauth={quote(result)}&account_id={quote(account_id)}",
        status_code=303,
    )


def callback(request: Request, state_value, code, provider_error):
    """Consume a state in one transaction, then exchange it without a DB lock."""

    if not state_value:
        return _result_redirect("", "failed")
    try:
        with Session.begin() as auth_db:
            user = auth.authenticate_request(request, auth_db)
            auth.admin(user)
            write_lock(auth_db)
            # GET authentication happens before the advisory lock. Re-fetch
            # both rows after locking so logout, password reset, deletion, or
            # role changes cannot authorize a callback with stale request state.
            stamp = now()
            auth_db.expire_all()
            raw_token = request.cookies.get(auth.COOKIE, "")
            fresh_session = (
                auth_db.get(LoginSession, auth.digest(raw_token)) if raw_token else None
            )
            fresh_user = (
                auth_db.get(User, fresh_session.user_id) if fresh_session else None
            )
            if (
                not fresh_session
                or fresh_session.expires_at <= stamp
                or not fresh_user
                or fresh_user.deleted
                or fresh_user.role != "admin"
                or fresh_user.must_change_password
            ):
                return _result_redirect("", "login_required")
            row = auth_db.get(MailOAuthState, state_digest(state_value))
            if (
                not row
                or row.used_at is not None
                or row.expires_at <= stamp
                or row.admin_id != fresh_user.id
                or row.session_hash != fresh_session.token_hash
            ):
                return _result_redirect(row.account_id if row else "", "expired")
            account_id = row.account_id
            state_account = auth_db.get(Account, account_id)
            if not state_account or state_account.deleted:
                return _result_redirect(account_id, "changed")
            expected_config_version = state_account.mail_config_version
            client_id = cipher().decrypt(row.client_id_encrypted.encode()).decode()
            client_secret = (
                cipher().decrypt(row.client_secret_encrypted.encode()).decode()
                if row.client_secret_encrypted
                else None
            )
            verifier = cipher().decrypt(row.code_verifier_encrypted.encode()).decode()
            backend_id, tenant, admin_id = row.backend_id, row.tenant, fresh_user.id
            state_session_hash = row.session_hash
            # Delete the one-time state after copying its encrypted values into
            # local memory; replaying the callback must fail immediately.
            auth_db.delete(row)
    except HTTPException:
        return _result_redirect("", "login_required")
    if provider_error or not code or len(code) > 4096:
        return _result_redirect(account_id, "failed")

    with Session() as lookup_db:
        account = lookup_db.get(Account, account_id)
        if not account or account.deleted:
            return _result_redirect(account_id, "failed")
        tool = get_mail_tool(account.mail_tool)
        if not tool or tool.backend.id != backend_id or not tool.backend.oauth_provider:
            return _result_redirect(account_id, "changed")
        expected_email = account.email
    try:
        token = tool.backend.oauth_provider.exchange_code(
            code=code,
            code_verifier=verifier,
            client_id=client_id,
            client_secret=client_secret,
            tenant=tenant,
            redirect_uri=redirect_uri(),
        )
    except Exception:
        return _result_redirect(account_id, "failed")
    try:
        authorized_email = token.authorized_email.strip()
        refresh_token = token.refresh_token
    except AttributeError:
        return _result_redirect(account_id, "failed")
    if (
        not authorized_email
        or len(authorized_email) > 254
        or not isinstance(refresh_token, str)
        or not refresh_token
        or len(refresh_token) > 8192
    ):
        return _result_redirect(account_id, "failed")
    if authorized_email.casefold() != expected_email.casefold():
        return _result_redirect(account_id, "mismatch")
    with Session.begin() as exchange_db:
        # Serialize against role changes, account deletion, session revocation,
        # and OAuth cancellation before committing the exchanged credential.
        write_lock(exchange_db)
        account = exchange_db.get(Account, account_id)
        actor = exchange_db.get(User, admin_id)
        session = exchange_db.get(LoginSession, state_session_hash)
        if (
            not account
            or account.deleted
            or account.mail_config_version != expected_config_version
            or not actor
            or actor.deleted
            or actor.role != "admin"
            or actor.must_change_password
            or not session
            or session.user_id != admin_id
            or session.expires_at <= now()
        ):
            return _result_redirect(account_id, "failed")
        tool = get_mail_tool(account.mail_tool)
        if not tool or tool.backend.id != backend_id or not tool.backend.oauth_provider:
            return _result_redirect(account_id, "changed")
        current = exchange_db.get(MailOAuthCredential, (account.id, backend_id))
        if current is None:
            current = MailOAuthCredential(
                account_id=account.id,
                backend_id=backend_id,
                version=0,
                created_at=stamp,
            )
            exchange_db.add(current)
        current.client_id_encrypted = cipher().encrypt(client_id.encode()).decode()
        current.client_secret_encrypted = (
            cipher().encrypt(client_secret.encode()).decode()
            if client_secret
            else None
        )
        current.tenant = tenant
        current.refresh_token_encrypted = cipher().encrypt(refresh_token.encode()).decode()
        current.authorized_email = authorized_email.casefold()
        current.status = "active"
        current.version = (current.version or 0) + 1
        current.updated_by = admin_id
        current.updated_at = now()
        from .verification import cancel_account_runs

        cancel_account_runs(exchange_db, account.id)
        account.mail_config_version += 1
        d.event(exchange_db, "mail_oauth_authorized", actor, account, {"backend": backend_id})
    return _result_redirect(account_id, "success")


def revoke(db, user, account_id):
    auth.admin(user)
    account = d.get_account(db, user, account_id)
    backend, _ = _provider(account)
    row = db.get(MailOAuthCredential, (account.id, backend.id))
    if row:
        db.delete(row)
    # Always advance the account version, including when only a pending OAuth
    # state exists. This makes cancellation win a race with a callback that
    # already consumed its one-time state but has not committed the credential.
    from .verification import cancel_account_runs

    cancel_account_runs(db, account.id)
    account.mail_config_version += 1
    d.event(db, "mail_oauth_revoked", user, account, {"backend": backend.id})
    db.execute(delete(MailOAuthState).where(MailOAuthState.account_id == account.id))
    _cleanup(db, now())
    db.flush()
    return {"required": True, "configured": False, "status": "not_configured"}
