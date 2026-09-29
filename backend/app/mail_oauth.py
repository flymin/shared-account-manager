"""Account-scoped OAuth lifecycle for mailbox backend plugins."""

import hashlib
import os
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, quote, urlsplit

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
    provider = backend.oauth_provider
    row = db.get(MailOAuthCredential, (account.id, backend.id))
    result = {
        "required": True,
        "mode": getattr(provider, "mode", "callback"),
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
    if user.role == "admin":
        manual_redirect = getattr(provider, "redirect_uri", None)
        if manual_redirect:
            result["redirect_uri"] = manual_redirect
        elif config.PUBLIC_ORIGIN:
            result["redirect_uri"] = redirect_uri()
    return result


def _create_state(
    db,
    user,
    session,
    account,
    backend,
    *,
    client_id,
    client_secret,
    tenant,
    callback,
    verifier,
):
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
        redirect_uri=callback,
        expires_at=stamp + timedelta(seconds=STATE_TTL_SECONDS),
    )
    db.add(row)
    db.flush()
    return row, raw_state


def begin(db, user, session, account_id, data: MailOAuthInput):
    auth.admin(user)
    account = d.get_account(db, user, account_id)
    backend, _ = _provider(account)
    provider = backend.oauth_provider
    try:
        client_id, client_secret, tenant = provider.validate_config(
            data.client_id, data.client_secret, data.tenant
        )
    except MailError:
        raise HTTPException(422, "OAuth 应用配置无效") from None
    callback = redirect_uri()
    try:
        verifier, challenge = provider.pkce_pair()
    except (MailError, TypeError, ValueError):
        raise HTTPException(503, "OAuth 工具暂时不可用，请联系管理员") from None
    row, raw_state = _create_state(
        db,
        user,
        session,
        account,
        backend,
        client_id=client_id,
        client_secret=client_secret,
        tenant=tenant,
        callback=callback,
        verifier=verifier,
    )
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


def begin_manual(db, user, session, account_id):
    """Start the public-client flow; the browser result is pasted back later."""

    auth.admin(user)
    account = d.get_account(db, user, account_id)
    backend, _ = _provider(account)
    provider = backend.oauth_provider
    if getattr(provider, "mode", None) != "manual":
        raise HTTPException(409, "该账号未配置 Outlook manual 工具")
    client_id = os.getenv("OUTLOOK_MANUAL_CLIENT_ID", "").strip()
    callback = getattr(provider, "redirect_uri", None)
    if not callback or not client_id:
        raise HTTPException(503, "Outlook manual 尚未配置 Client ID")
    try:
        client_id, client_secret, tenant = provider.validate_config(
            client_id, None, "common"
        )
        row, raw_state = _create_state(
            db,
            user,
            session,
            account,
            backend,
            client_id=client_id,
            client_secret=client_secret,
            tenant=tenant,
            callback=callback,
            verifier="",
        )
        url = provider.authorization_url(
            client_id=client_id,
            tenant=tenant,
            redirect_uri=callback,
            state=raw_state,
        )
    except MailError:
        raise HTTPException(422, "Outlook manual Client ID 配置无效") from None
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


def _parse_manual_callback(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise HTTPException(422, "请粘贴完整的 https://localhost 跳转地址")
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        raise HTTPException(422, "跳转地址格式无效") from None
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(422, "跳转地址格式无效") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "localhost"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.fragment
    ):
        raise HTTPException(422, "跳转地址必须是 https://localhost")
    query = parse_qs(parsed.query, keep_blank_values=True)

    def one(name, *, required=True):
        values = query.get(name, [])
        if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
            raise HTTPException(422, "跳转地址缺少有效授权参数")
        if not values:
            return None
        if len(values[0]) > 4096:
            raise HTTPException(422, "授权参数过长")
        return values[0]

    state = one("state")
    error = one("error", required=False)
    code = one("code", required=not error)
    return state, code, error


def _complete(
    request: Request,
    state_value,
    code,
    provider_error,
    *,
    expected_redirect_uri=None,
    expected_account_id=None,
):
    """Consume one state and exchange its code; return (account_id, result)."""

    account_id = ""
    if not state_value:
        return account_id, "failed"
    try:
        with Session.begin() as auth_db:
            user = auth.authenticate_request(request, auth_db)
            auth.admin(user)
            write_lock(auth_db)
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
                return account_id, "login_required"
            row = auth_db.get(MailOAuthState, state_digest(state_value))
            if (
                not row
                or row.used_at is not None
                or row.expires_at <= stamp
                or row.admin_id != fresh_user.id
                or row.session_hash != fresh_session.token_hash
            ):
                return (row.account_id if row else account_id), "expired"
            account_id = row.account_id
            if expected_account_id and account_id != expected_account_id:
                return account_id, "changed"
            state_account = auth_db.get(Account, account_id)
            if not state_account or state_account.deleted:
                return account_id, "changed"
            state_redirect_uri = row.redirect_uri
            if not state_redirect_uri:
                state_redirect_uri = redirect_uri()
            if expected_redirect_uri and state_redirect_uri != expected_redirect_uri:
                return account_id, "changed"
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
            # Delete the one-time state before network I/O; replaying it fails.
            auth_db.delete(row)
    except HTTPException:
        return account_id, "login_required"
    if provider_error or not code or len(code) > 4096:
        return account_id, "failed"

    with Session() as lookup_db:
        account = lookup_db.get(Account, account_id)
        if not account or account.deleted:
            return account_id, "failed"
        tool = get_mail_tool(account.mail_tool)
        if not tool or tool.backend.id != backend_id or not tool.backend.oauth_provider:
            return account_id, "changed"
        expected_email = account.email
    try:
        token = tool.backend.oauth_provider.exchange_code(
            code=code,
            code_verifier=verifier or None,
            client_id=client_id,
            client_secret=client_secret,
            tenant=tenant,
            redirect_uri=state_redirect_uri,
        )
    except Exception:
        return account_id, "failed"
    try:
        authorized_email = token.authorized_email.strip()
        refresh_token = token.refresh_token
    except AttributeError:
        return account_id, "failed"
    if (
        not authorized_email
        or len(authorized_email) > 254
        or not isinstance(refresh_token, str)
        or not refresh_token
        or len(refresh_token) > 8192
    ):
        return account_id, "failed"
    if authorized_email.casefold() != expected_email.casefold():
        return account_id, "mismatch"
    with Session.begin() as exchange_db:
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
            return account_id, "failed"
        tool = get_mail_tool(account.mail_tool)
        if not tool or tool.backend.id != backend_id or not tool.backend.oauth_provider:
            return account_id, "changed"
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
    return account_id, "success"


def callback(request: Request, state_value, code, provider_error):
    """Consume a normal public callback in one transaction."""

    try:
        expected = redirect_uri()
    except HTTPException:
        return _result_redirect("", "failed")
    account_id, result = _complete(
        request,
        state_value,
        code,
        provider_error,
        expected_redirect_uri=expected,
    )
    return _result_redirect(account_id, result)


def complete_manual(request: Request, callback_url, account_id):
    state_value, code, provider_error = _parse_manual_callback(callback_url)
    account_id, result = _complete(
        request,
        state_value,
        code,
        provider_error,
        expected_redirect_uri="https://localhost",
        expected_account_id=account_id,
    )
    if result == "success":
        return {"status": result, "account_id": account_id}
    messages = {
        "login_required": "管理员登录状态已失效，请重新登录",
        "expired": "授权地址已过期或已使用，请重新生成授权地址",
        "changed": "账号或授权工具已变化，请重新生成授权地址",
        "mismatch": "授权邮箱与当前账号不一致",
        "failed": "Outlook 授权失败，请检查授权地址和应用权限",
    }
    codes = {
        "login_required": 401,
        "expired": 409,
        "changed": 409,
        "mismatch": 422,
        "failed": 502,
    }
    raise HTTPException(codes.get(result, 502), messages.get(result, "授权失败"))


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
