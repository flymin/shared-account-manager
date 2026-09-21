import hashlib
import secrets
from datetime import timedelta
from fastapi import Depends, HTTPException, Request, Response
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import select, delete
from . import config, login_limits
from .db import get_db, write_lock
from .models import User, LoginSession, LoginAttempt, Settings, now

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))
COOKIE = "am_session"


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError):
        return False


def authenticate(request: Request, db=Depends(get_db, scope="function")):
    return authenticate_request(request, db)


def authenticate_request(request: Request, db, *, lock_writes=True):
    token = request.cookies.get(COOKIE, "")
    session = db.get(LoginSession, digest(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, "请先登录")
    user = db.get(User, session.user_id)
    if not user or user.deleted:
        raise HTTPException(401, "登录已失效")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if lock_writes:
            write_lock(db)
        # A deletion/password reset might have committed while waiting for lock.
        db.expire_all()
        session = db.get(LoginSession, digest(token))
        if not session or session.expires_at <= now():
            raise HTTPException(401, "登录已失效")
        user = db.get(User, session.user_id)
        if user.deleted:
            raise HTTPException(401, "登录已失效")
        if not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session.csrf_token
        ):
            raise HTTPException(403, "请求校验失败，请刷新页面")
    request.state.login_session = session
    if user.must_change_password and request.url.path not in {
        "/api/v1/auth/me",
        "/api/v1/auth/password",
        "/api/v1/auth/logout",
    }:
        raise HTTPException(403, "请先修改初始密码")
    return user


def admin(user=Depends(authenticate)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def login(db, data, response: Response):
    if not login_limits.try_lock(db):
        return login_limits.reject(response, 1)
    stamp = now()
    key = digest(data.username.lower())
    attempt, retry = login_limits.admit(db, key, stamp)
    if retry is not None:
        return login_limits.reject(response, retry)
    user = db.scalar(
        select(User).where(
            User.username == data.username.lower(), User.deleted.is_(False)
        )
    )
    encoded = user.password_hash if user else DUMMY_HASH
    valid = check_password(encoded, data.password)
    if valid and user:
        # Password calculation must not hold up claims, returns or maintenance.
        # Recheck after locking: a reset/deletion can commit during verification.
        write_lock(db)
        db.refresh(user)
        valid = not user.deleted and user.password_hash == encoded
    if not valid or not user:
        if not attempt:
            attempt = LoginAttempt(key=key, attempts=0, since=stamp)
            db.add(attempt)
        attempt.attempts += 1
        return None, "用户名或密码错误", 401
    if attempt:
        db.delete(attempt)
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= stamp))
    token = secrets.token_urlsafe(40)
    settings = db.get(Settings, 1)
    session = LoginSession(
        token_hash=digest(token),
        user_id=user.id,
        csrf_token=secrets.token_hex(32),
        expires_at=stamp + timedelta(days=settings.session_days),
    )
    db.add(session)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=config.PUBLIC_ORIGIN.startswith("https://"),
        samesite="lax",
        path="/",
        max_age=settings.session_days * 86400,
    )
    return (user, session), None, 200
