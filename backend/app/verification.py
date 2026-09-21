"""Verification permissions and durable email job state; no network calls here."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select

from . import auth, domain as d
from .config import cipher
from .plugins import configured_templates, get_mail_backend
from .models import Account, EmailCodeRun, LoginSession, TwoFactor, User, now

ACTIVE = ("pending", "reading")
ERRORS = {
    "configuration": "邮箱验证码匹配规则未配置或无效，请联系管理员",
    "backend_disabled": "未启用自动获取邮箱验证码",
    "backend_unavailable": "邮箱后端插件不可用，请联系管理员",
    "authentication": "邮箱登录失败，请检查邮箱密码",
    "challenge": "邮箱要求额外登录验证，请先在邮箱网站完成验证",
    "two_factor_required": "暂不支持邮箱 2FA，请在邮箱网站手动查看验证码",
    "two_factor_invalid": "暂不支持邮箱 2FA，请在邮箱网站手动查看验证码",
    "protocol": "邮箱页面或接口发生变化，暂时无法获取验证码",
    "received_time": "无法可靠获取邮件接收时间，请稍后重试或联系管理员",
    "network": "邮箱暂时无法连接，请稍后重试",
    "read_failed": "无法确认邮件已标记为已读，请重试",
}


def valid_owner(db, run, stamp):
    user = db.get(User, run.user_id)
    account = db.get(Account, run.account_id)
    session = db.get(LoginSession, run.session_hash)
    return bool(
        user
        and not user.deleted
        and not user.must_change_password
        and account
        and not account.deleted
        and account.mail_backend == run.mail_backend
        and get_mail_backend(run.mail_backend) is not None
        and session
        and session.user_id == user.id
        and session.expires_at > stamp
        and (user.role == "admin" or d.active_claim(db, user.id, account.id))
    )


def clear_result(run):
    run.code_encrypted = None
    run.received_at = None


def finish(run, status, error=None):
    run.status, run.error = status, error
    run.lease_token = run.lease_until = None
    if status != "found":
        clear_result(run)


def cancel_account_runs(db, account_id):
    for run in db.scalars(
        select(EmailCodeRun).where(
            EmailCodeRun.account_id == account_id,
            EmailCodeRun.status.in_((*ACTIVE, "found")),
        )
    ):
        finish(run, "cancelled")


def email_unavailable(account):
    if account.mail_backend is None:
        return "backend_disabled"
    if get_mail_backend(account.mail_backend) is None:
        return "backend_unavailable"
    if not configured_templates():
        return "configuration"
    return None


def reconcile_runs(db, stamp=None):
    stamp = stamp or now()
    for run in db.scalars(
        select(EmailCodeRun).where(EmailCodeRun.status.in_((*ACTIVE, "found")))
    ):
        if run.deadline <= stamp:
            finish(run, "timed_out")
        elif not valid_owner(db, run, stamp):
            finish(run, "cancelled")
    # Retain message IDs for a day to prevent another job consuming the same
    # message after a cancelled/failed mark-read. Never retain expired codes.
    db.execute(
        delete(EmailCodeRun).where(EmailCodeRun.deadline < stamp - timedelta(days=1))
    )
    db.flush()


def run_dto(run, stamp=None):
    stamp = stamp or now()
    result = dict(
        id=run.id,
        status=run.status,
        deadline=run.deadline,
        server_time=stamp,
        error=ERRORS.get(run.error),
    )
    if run.status == "found" and run.deadline > stamp and run.code_encrypted:
        result.update(
            code=cipher().decrypt(run.code_encrypted.encode()).decode(),
            received_at=run.received_at,
        )
    return result


def status(db, user, account_id):
    account = d.get_account(db, user, account_id, feedback=True)
    stamp = now()
    result = {"server_time": stamp, "two_factor": {}, "email": None}
    unavailable = email_unavailable(account)
    result.update(
        email_config_version=account.mail_config_version,
        email_available=unavailable is None,
        email_unavailable_reason=ERRORS.get(unavailable),
    )
    for kind in ("service",):
        config = db.get(TwoFactor, (account_id, kind))
        result["two_factor"][kind] = {
            "configured": bool(config and config.uri_encrypted),
            "version": config.version if config else 0,
            "updated_at": config.updated_at if config else None,
        }
    run = db.scalar(
        select(EmailCodeRun).where(
            EmailCodeRun.account_id == account_id,
            EmailCodeRun.status.in_(ACTIVE),
            EmailCodeRun.deadline > stamp,
        )
    )
    if run and valid_owner(db, run, stamp):
        owner = db.get(User, run.user_id)
        result["email"] = {
            "owner": owner.display_name,
            "mine": run.user_id == user.id,
            "deadline": run.deadline,
            "id": run.id if run.user_id == user.id else None,
        }
    return result


def start_run(db, user, session, account_id, run_id):
    account = d.get_account(db, user, account_id, feedback=True)
    stamp = now()
    reconcile_runs(db, stamp)
    existing = db.get(EmailCodeRun, run_id)
    if existing:
        if (
            existing.user_id == user.id
            and existing.account_id == account.id
            and existing.session_hash == session.token_hash
        ):
            return run_dto(existing, stamp)
        raise HTTPException(409, "请求标识已使用，请重试")
    unavailable = email_unavailable(account)
    if unavailable:
        raise HTTPException(
            409 if unavailable == "backend_disabled" else 503, ERRORS[unavailable]
        )
    active = db.scalar(
        select(EmailCodeRun).where(
            EmailCodeRun.account_id == account.id, EmailCodeRun.status.in_(ACTIVE)
        )
    )
    if active:
        raise HTTPException(409, "此账号正在获取邮箱验证码，请等待或取消当前任务")
    run = EmailCodeRun(
        id=run_id,
        account_id=account.id,
        user_id=user.id,
        session_hash=session.token_hash,
        mail_backend=account.mail_backend,
        started_at=stamp,
        since=stamp - timedelta(minutes=2),
        deadline=stamp + timedelta(minutes=d.settings(db).email_code_timeout_minutes),
        next_poll_at=stamp,
        status="pending",
    )
    db.add(run)
    d.event(db, "email_code_started", user, account)
    db.flush()
    return run_dto(run, stamp)


def get_run(db, user, account_id, run_id):
    d.get_account(db, user, account_id, feedback=True)
    run = db.get(EmailCodeRun, run_id)
    # Even admins cannot read another user's code/result.
    if not run or run.account_id != account_id or run.user_id != user.id:
        raise HTTPException(404, "取码任务不存在")
    return run


def read_run(db, user, account_id, run_id):
    run = get_run(db, user, account_id, run_id)
    stamp = now()
    if not valid_owner(db, run, stamp):
        return dict(
            id=run.id,
            status="cancelled",
            deadline=run.deadline,
            server_time=stamp,
            error=None,
        )
    if run.deadline <= stamp:
        return dict(
            id=run.id,
            status="timed_out",
            deadline=run.deadline,
            server_time=stamp,
            error=None,
        )
    return run_dto(run, stamp)


def cancel_run(db, user, account_id, run_id):
    run = get_run(db, user, account_id, run_id)
    finish(run, "cancelled")
    return run_dto(run)


def save_totp(db, user, account_id, kind, uri, version):
    auth.admin(user)
    if kind != "service":
        raise HTTPException(422, "仅支持账号 2FA")
    account = d.get_account(db, user, account_id, feedback=True)
    config = db.get(TwoFactor, (account_id, kind))
    if version != (config.version if config else 0):
        raise HTTPException(409, "2FA 配置已被更新，请刷新后重新上传")
    if config is None:
        config = TwoFactor(account_id=account_id, kind=kind, version=0)
        db.add(config)
    config.uri_encrypted = cipher().encrypt(uri.encode()).decode()
    config.version += 1
    config.updated_by, config.updated_at = user.id, now()
    d.event(db, "two_factor_updated", user, account, {"target": kind})
    db.flush()
    return {
        "configured": True,
        "version": config.version,
        "updated_at": config.updated_at,
    }


def remove_totp(db, user, account_id, kind, version):
    auth.admin(user)
    if kind != "service":
        raise HTTPException(422, "仅支持账号 2FA")
    account = d.get_account(db, user, account_id, feedback=True)
    config = db.get(TwoFactor, (account_id, kind))
    if version != (config.version if config else 0):
        raise HTTPException(409, "2FA 配置已被更新，请刷新后重试")
    if config and config.uri_encrypted:
        # Keep a versioned tombstone so stale uploads/deletes cannot affect a
        # later configuration. The empty value contains no recoverable secret.
        config.uri_encrypted = ""
        config.version += 1
        config.updated_by, config.updated_at = user.id, now()
        d.event(db, "two_factor_removed", user, account, {"target": kind})
        db.flush()
    return {
        "configured": False,
        "version": config.version if config else 0,
        "updated_at": config.updated_at if config else None,
    }
