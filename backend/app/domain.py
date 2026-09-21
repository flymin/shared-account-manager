import re
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, delete, func, or_
from .config import cipher
from .models import (
    Account,
    AccountGroup,
    AccountUser,
    UserGroup,
    User,
    Group,
    Settings,
    Claim,
    Event,
    now,
)


def fail(message, status=409):
    raise HTTPException(status, message)


def settings(db):
    return db.get(Settings, 1)


def quota_depleted(quota, cfg):
    return quota is not None and quota < cfg.quota_depleted_threshold


def event(
    db,
    kind,
    actor=None,
    account=None,
    details=None,
    target_user=None,
    automation_key=None,
    at=None,
):
    db.add(
        Event(
            kind=kind,
            actor_id=actor.id if actor else None,
            account_id=account.id if account else None,
            details=details or {},
            target_user_id=target_user,
            automation_key=automation_key,
            created_at=at or now(),
        )
    )


def quota_used(db, user_id):
    return db.scalar(
        select(func.count())
        .select_from(Claim)
        .where(Claim.user_id == user_id, Claim.returned_at.is_(None))
    )


def active_claim(db, user_id, account_id):
    return db.scalar(
        select(Claim).where(
            Claim.user_id == user_id,
            Claim.account_id == account_id,
            Claim.returned_at.is_(None),
            Claim.invalidated_at.is_(None),
        )
    )


def user_dto(db, user):
    return dict(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
        claim_limit=user.claim_limit,
        effective_claim_limit=user.claim_limit or settings(db).user_claim_limit,
        claims_used=quota_used(db, user.id),
        group_ids=list(
            db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user.id))
        ),
    )


def visible_clause(user_id):
    groups = select(UserGroup.group_id).where(UserGroup.user_id == user_id)
    return or_(
        Account.id.in_(
            select(AccountGroup.account_id).where(AccountGroup.group_id.in_(groups))
        ),
        Account.id.in_(
            select(AccountUser.account_id).where(AccountUser.user_id == user_id)
        ),
    )


def can_view(db, user, account):
    if account.deleted:
        return False
    return user.role == "admin" or bool(
        db.scalar(
            select(Account.id).where(Account.id == account.id, visible_clause(user.id))
        )
    )


def get_account(db, user, account_id, feedback=False):
    account = db.get(Account, account_id)
    if not account or account.deleted:
        fail("账号不存在", 404)
    owned = active_claim(db, user.id, account.id)
    if feedback and user.role != "admin" and not owned:
        fail("只有当前领用者可以操作", 403)
    if not feedback and not can_view(db, user, account) and not owned:
        fail("账号不存在", 404)
    return account


def set_links(db, model, key, identity, ids, value_key, target):
    if ids is None:
        return
    ids = set(ids)
    found = set(db.scalars(select(target.id).where(target.id.in_(ids))))
    if target is User:
        found = set(
            db.scalars(select(User.id).where(User.id.in_(ids), User.deleted.is_(False)))
        )
    if found != ids:
        fail("选择的用户或用户组已不存在", 422)
    db.execute(delete(model).where(getattr(model, key) == identity))
    for value in ids:
        db.add(model(**{key: identity, value_key: value}))
    db.flush()


def group_dto(db, group):
    return {
        "id": group.id,
        "name": group.name,
        "user_ids": list(
            db.scalars(
                select(UserGroup.user_id)
                .join(User, User.id == UserGroup.user_id)
                .where(UserGroup.group_id == group.id, User.deleted.is_(False))
                .order_by(UserGroup.user_id)
            )
        ),
    }


def set_group_members(db, group, user_ids, actor):
    if user_ids is None:
        return
    previous = set(
        db.scalars(select(UserGroup.user_id).where(UserGroup.group_id == group.id))
    )
    set_links(db, UserGroup, "group_id", group.id, user_ids, "user_id", User)
    for user_id in previous.symmetric_difference(set(user_ids)):
        event(
            db,
            "user_updated",
            actor,
            details={
                "fields": ["group_ids"],
                "group_name": group.name,
                "membership": "added" if user_id in user_ids else "removed",
            },
            target_user=user_id,
        )


def set_acl(db, account, group_ids, user_ids):
    set_links(db, AccountGroup, "account_id", account.id, group_ids, "group_id", Group)
    set_links(db, AccountUser, "account_id", account.id, user_ids, "user_id", User)


def serialize_event(db, e, identities=False):
    actor = db.get(User, e.actor_id) if e.actor_id else None
    result = dict(
        id=e.id,
        account_id=e.account_id,
        actor=actor.display_name if actor else "系统",
        kind=e.kind,
        details=e.details,
        created_at=e.created_at,
        target_user_id=e.target_user_id,
    )
    if identities:
        account = db.get(Account, e.account_id) if e.account_id else None
        target = db.get(User, e.target_user_id) if e.target_user_id else None
        result.update(
            account_email=account.email if account else None,
            target_username=target.username if target else None,
            target_display_name=target.display_name if target else None,
        )
    return result


def account_dto(db, user, a, stamp=None):
    stamp = stamp or now()
    cfg = settings(db)
    active = list(
        db.scalars(
            select(Claim).where(
                Claim.account_id == a.id,
                Claim.returned_at.is_(None),
                Claim.invalidated_at.is_(None),
            )
        )
    )
    mine = db.scalar(
        select(Claim).where(
            Claim.account_id == a.id,
            Claim.user_id == user.id,
            Claim.returned_at.is_(None),
        )
    )
    capacity = a.capacity or cfg.account_capacity
    reasons = []
    if quota_used(db, user.id) >= (user.claim_limit or cfg.user_claim_limit):
        reasons.append("个人名额已满")
    if len(active) >= capacity:
        reasons.append("人数已满")
    if a.disabled:
        reasons.append("已停用")
    if a.expires_at and a.expires_at <= stamp:
        reasons.append("已过期")
    if mine:
        reasons.append("已领用，尚未归还")
    if not can_view(db, user, a):
        reasons.append("无新领用权限")
    result = dict(
        id=a.id,
        email=a.email,
        mail_backend=a.mail_backend,
        mail_backend_name=mail_backend_name(a.mail_backend),
        tier=a.tier,
        disabled=a.disabled,
        created_at=a.created_at,
        expires_at=a.expires_at,
        capacity=a.capacity,
        effective_capacity=capacity,
        quota=a.quota,
        quota_depleted=quota_depleted(a.quota, cfg),
        reset_at=a.reset_at,
        quota_reset_interval_days=a.quota_reset_interval_days,
        quota_updated_at=a.quota_updated_at,
        quota_source=a.quota_source,
        health=a.health,
        health_categories=a.health_categories,
        health_note=a.health_note,
        health_version=a.health_version,
        anomaly_since=a.anomaly_since,
        can_claim=not reasons,
        has_open_claim=bool(mine),
        blocked_reasons=reasons,
        active_count=len(active),
        current_users=[db.get(User, c.user_id).display_name for c in active],
    )
    result["recent_updates"] = [
        serialize_event(db, e)
        for e in db.scalars(
            select(Event)
            .where(
                Event.account_id == a.id,
                Event.kind.in_(["quota_updated", "quota_reset"]),
            )
            .order_by(Event.id.desc())
            .limit(3)
        )
    ]
    if user.role == "admin":
        result["group_ids"] = list(
            db.scalars(
                select(AccountGroup.group_id).where(AccountGroup.account_id == a.id)
            )
        )
        result["user_ids"] = list(
            db.scalars(
                select(AccountUser.user_id)
                .join(User, User.id == AccountUser.user_id)
                .where(AccountUser.account_id == a.id, User.deleted.is_(False))
            )
        )
    return result


def claim_dto(db, user, claim):
    a = db.get(Account, claim.account_id)
    result = dict(
        id=claim.id,
        claimed_at=claim.claimed_at,
        returned_at=claim.returned_at,
        return_kind=claim.return_kind,
        invalidated_at=claim.invalidated_at,
        invalidation_kind=claim.invalidation_kind,
        invalidation_reason=claim.invalidation_reason,
    )
    if user.role == "admin":
        result.update(
            user_id=claim.user_id,
            user_name=db.get(User, claim.user_id).display_name,
            account_id=a.id,
            account_email=a.email,
        )
    if not claim.invalidated_at and not a.deleted:
        if claim.returned_at:
            result["account"] = {"id": a.id, "email": a.email, "tier": a.tier}
        else:
            result["account"] = account_dto(db, user, a)
    else:
        result["account"] = None
    return result


def validate_reset(reset_at):
    if reset_at is not None and reset_at <= now():
        fail("重置时间必须晚于当前时间", 422)


def settle_quota_reset(db, a, stamp):
    if a.reset_at and a.reset_at <= stamp:
        deadline = a.reset_at
        interval = a.quota_reset_interval_days or settings(db).quota_reset_interval_days
        interval_delta = timedelta(days=interval)
        elapsed = stamp - deadline
        cycles = elapsed // interval_delta + 1
        next_reset = deadline + interval_delta * cycles
        a.quota, a.reset_at, a.quota_source, a.quota_updated_at = (
            100,
            next_reset,
            "system",
            stamp,
        )
        event(
            db,
            "quota_reset",
            account=a,
            details={
                "quota": 100,
                "reset_at": deadline.isoformat(),
                "next_reset_at": next_reset.isoformat(),
                "interval_days": interval,
            },
            automation_key=f"quota:{a.id}:{a.quota_version}",
            at=stamp,
        )
        a.quota_version += 1


def update_quota(db, a, actor, quota, reset_at, replace_reset=True):
    validate_reset(reset_at)
    stamp = now()
    settle_quota_reset(db, a, stamp)
    a.quota = quota
    if replace_reset:
        a.reset_at = reset_at
    a.quota_updated_at, a.quota_source = stamp, "user"
    a.quota_version += 1
    event(
        db,
        "quota_updated",
        actor,
        a,
        {"quota": quota, "reset_at": a.reset_at.isoformat() if a.reset_at else None},
    )


def health_report(db, a, actor, categories, note):
    note = note.strip()
    if not categories and not note:
        fail("请选择异常类别或填写异常问题", 422)
    a.health, a.health_categories, a.health_note = (
        "abnormal",
        sorted(set(categories)),
        note,
    )
    a.anomaly_since = now()
    a.health_version += 1
    event(
        db,
        "health_reported",
        actor,
        a,
        {"categories": a.health_categories, "note": note, "version": a.health_version},
    )


def clear_health(db, a, actor):
    a.health = "normal"
    a.health_categories, a.health_note, a.anomaly_since = [], "", None
    a.health_version += 1
    event(db, "health_cleared", actor, a)


def create_claim(db, user, data):
    a = get_account(db, user, data.account_id)
    db.refresh(a, with_for_update=True)
    db.refresh(user, with_for_update=True)
    info = account_dto(db, user, a)
    if not info["can_claim"]:
        fail("；".join(info["blocked_reasons"]))
    if a.health == "abnormal" and not data.acknowledge_warning:
        fail("请确认异常提醒后再领用")
    c = Claim(
        account_id=a.id,
        user_id=user.id,
        observed_health_version=a.health_version if a.health == "abnormal" else None,
    )
    db.add(c)
    db.flush()
    event(db, "claimed", user, a, {"claim_id": c.id})
    return c


def return_claim(db, user, claim_id, data):
    c = db.get(Claim, claim_id)
    if not c or c.user_id != user.id:
        fail("领用记录不存在", 404)
    if c.returned_at:
        return c
    a = db.get(Account, c.account_id)
    if c.invalidated_at:
        c.return_kind = "acknowledge"
    else:
        if data.kind == "acknowledge":
            fail("当前账号需填写归还信息", 422)
        if a.health in {"abnormal", "possibly_recovered"}:
            if data.health_version != a.health_version:
                fail("异常信息已更新，请刷新后归还")
            if data.health_action is None and data.kind == "normal":
                fail("请选择是否维持异常标记", 422)
        if data.kind == "normal":
            if data.quota is None or data.reset_at is None:
                fail("正常归还需要填写剩余额度和重置时间", 422)
            update_quota(db, a, user, data.quota, data.reset_at)
            if data.health_action == "clear":
                clear_health(db, a, user)
            elif data.health_action == "maintain" and a.health != "normal":
                health_report(db, a, user, a.health_categories, a.health_note)
        else:
            health_report(db, a, user, data.categories, data.note)
        c.return_kind = data.kind
    c.returned_at = now()
    event(db, "returned", user, a, {"claim_id": c.id, "kind": c.return_kind})
    db.flush()
    return c


def invalidate(db, c, actor, reason, kind="revoked"):
    if c.returned_at:
        return
    if c.invalidated_at and kind != "deleted":
        return
    c.invalidated_at, c.invalidation_kind, c.invalidation_reason = now(), kind, reason
    event(
        db,
        "claim_invalidated",
        actor,
        db.get(Account, c.account_id),
        {"claim_id": c.id, "kind": kind, "reason": reason},
        target_user=c.user_id,
    )


def validate_mail_backend(identity):
    from .plugins import get_mail_backend

    if identity is not None and get_mail_backend(identity) is None:
        fail("请选择已安装的邮箱后端插件，或选择不启用", 422)


def mail_backend_name(identity):
    from .plugins import get_mail_backend

    plugin = get_mail_backend(identity)
    return plugin.name if plugin else "不可用插件" if identity else None


def parse_import(db, data):
    validate_mail_backend(data.mail_backend)
    errors, rows, seen = [], [], set()
    lines = data.text.splitlines()
    if len(lines) > 1000:
        fail("每批最多 1000 行", 422)
    for line_num, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split("----")
        if len(parts) != 3:
            errors.append({"line": line_num, "message": "需要三段内容，以 ---- 分隔"})
            continue
        email, password, auth_password = parts
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            errors.append({"line": line_num, "message": "邮箱格式无效"})
        elif (
            not password
            or not auth_password
            or max(len(password), len(auth_password)) > 256
        ):
            errors.append(
                {"line": line_num, "message": "两段密码不能为空且不得超过 256 字符"}
            )
        elif email in seen or db.scalar(
            select(Account.id).where(Account.email == email)
        ):
            errors.append({"line": line_num, "message": "邮箱重复（包含已删除账号）"})
        else:
            rows.append((line_num, email, password, auth_password))
        seen.add(email)
    if not seen and not errors:
        errors.append({"line": 1, "message": "请至少输入一个账号"})
    return rows, errors


def import_accounts(db, user, data):
    rows, errors = parse_import(db, data)
    if errors:
        raise HTTPException(
            422, {"message": "请修正全部问题后重新导入", "errors": errors}
        )
    accounts = []
    for _, email, password, auth_password in rows:
        a = Account(
            email=email,
            mail_backend=data.mail_backend,
            tier=data.tier,
            expires_at=data.expires_at,
            capacity=data.capacity,
            quota_reset_interval_days=data.quota_reset_interval_days,
            password_encrypted=cipher().encrypt(password.encode()).decode(),
            auth_password_encrypted=cipher().encrypt(auth_password.encode()).decode(),
        )
        db.add(a)
        db.flush()
        set_acl(db, a, data.group_ids, data.user_ids)
        event(
            db,
            "account_created",
            user,
            a,
            {
                "tier": data.tier,
                "mail_backend": data.mail_backend,
                "quota_reset_interval_days": data.quota_reset_interval_days,
            },
        )
        accounts.append(a)
    return accounts


def reconcile(db, stamp=None):
    stamp = stamp or now()
    cfg = settings(db)
    if not cfg:
        return
    accounts = db.scalars(
        select(Account).where(
            Account.deleted.is_(False),
            or_(Account.reset_at <= stamp, Account.health == "abnormal"),
        )
    )
    for a in accounts:
        settle_quota_reset(db, a, stamp)
        if a.health != "abnormal" or not a.anomaly_since:
            continue
        cooled = a.anomaly_since + timedelta(hours=cfg.cooldown_hours) <= stamp
        observed = db.scalar(
            select(Claim.id).where(
                Claim.account_id == a.id,
                Claim.returned_at.is_(None),
                Claim.invalidated_at.is_(None),
                Claim.observed_health_version == a.health_version,
                Claim.claimed_at <= stamp - timedelta(hours=cfg.observation_hours),
            )
        )
        if cooled or observed:
            a.health = "possibly_recovered"
            event(
                db,
                "health_possible",
                account=a,
                details={"reason": "cooldown" if cooled else "observation"},
                automation_key=f"health:{a.id}:{a.health_version}",
                at=stamp,
            )
