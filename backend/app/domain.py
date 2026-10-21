import re
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, delete, func, or_
from .config import cipher
from . import totp, queries
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
    TwoFactor,
    now,
    uid,
)


def fail(message, status=409):
    raise HTTPException(status, message)


def settings(db):
    return db.get(Settings, 1)


def option_name(cfg, kind, identity):
    return next(
        (item["name"] for item in cfg.account_options[kind] if item["id"] == identity),
        identity,
    )


def validate_tier(db, identity, current=None):
    if identity is not None and identity == current:
        return  # Existing accounts may retain a disabled tier.
    if not any(
        item["id"] == identity and item["enabled"]
        for item in settings(db).account_options["tiers"]
    ):
        fail("请选择当前启用的账号类型", 422)


def validate_categories(db, categories):
    enabled = {
        item["id"]
        for item in settings(db).account_options["anomaly_categories"]
        if item["enabled"]
    }
    if not set(categories) <= enabled:
        fail("异常类别已变更，请选择当前启用的类别或填写异常描述", 422)


def validate_options_update(db, options):
    # Preserve references, including soft-deleted accounts and inactive anomalies.
    tiers = {item["id"] for item in options["tiers"]}
    if db.scalar(select(Account.id).where(Account.tier.not_in(tiers)).limit(1)):
        fail("不能删除已被账号使用的类型，请改为停用", 422)
    categories = {item["id"] for item in options["anomaly_categories"]}
    if db.scalar(
        select(Account.id)
        .where(~Account.health_categories.contained_by(sorted(categories)))
        .limit(1)
    ):
        fail("不能删除仍被异常记录使用的类别，请改为停用", 422)


def anomaly_cooldown_hours(cfg, categories):
    intervals = {
        item["id"]: item["cooldown_hours"] or cfg.cooldown_hours
        for item in cfg.account_options["anomaly_categories"]
    }
    return max(
        (intervals.get(identity, cfg.cooldown_hours) for identity in categories),
        default=cfg.cooldown_hours,
    )


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
    return queries.users_dto(db, [user])[0]


def visible_clause(user_id):
    return queries.visible_clause(user_id)


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
    if user.role == "admin":
        return account
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
    query = select(target.id).where(target.id.in_(ids))
    if target is User:
        query = query.where(User.deleted.is_(False))
    found = set(db.scalars(query)) if ids else set()
    if found != ids:
        fail("选择的用户或用户组已不存在", 422)
    db.execute(delete(model).where(getattr(model, key) == identity))
    for value in ids:
        db.add(model(**{key: identity, value_key: value}))
    db.flush()


def group_dto(db, group):
    return queries.groups_dto(db, [group])[0]


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
    return queries.event_dtos(db, [e], identities)[0]


def account_dto(db, user, a, stamp=None):
    return queries.accounts_dto(db, user, [a], stamp=stamp)[0]


def claim_dto(db, user, claim):
    return queries.claims_dto(db, user, [claim])[0]


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


def health_report(db, a, actor, categories, note, *, maintain=False):
    if not maintain:
        validate_categories(db, categories)
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
    cfg = settings(db)
    event(
        db,
        "health_reported",
        actor,
        a,
        {
            "categories": a.health_categories,
            "category_names": [
                option_name(cfg, "anomaly_categories", identity)
                for identity in a.health_categories
            ],
            "note": note,
            "version": a.health_version,
        },
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
    reasons = queries.claim_gate(db, user, a)
    if reasons:
        fail("；".join(reasons))
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
                health_report(
                    db, a, user, a.health_categories, a.health_note, maintain=True
                )
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


def validate_mail_tool(identity):
    from .plugins import get_mail_tool

    if identity is not None and get_mail_tool(identity) is None:
        fail("请选择已注册的邮箱取码工具，或选择不启用", 422)


def mail_tool_name(identity):
    from .plugins import get_mail_tool

    plugin = get_mail_tool(identity)
    return plugin.name if plugin else "不可用工具" if identity else None


def parse_import(db, data):
    validate_tier(db, data.tier)
    validate_mail_tool(data.mail_tool)
    errors, rows, seen = [], [], set()
    lines = data.text.splitlines()
    if len(lines) > 1000:
        fail("每批最多 1000 行", 422)
    emails = {
        line.split("----", 1)[0].strip().lower() for line in lines if "----" in line
    }
    existing = (
        set(db.scalars(select(Account.email).where(Account.email.in_(emails))))
        if emails
        else set()
    )
    for line_num, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split("----")
        if len(parts) == 2:
            email, auth_password = parts
            # A two-part row uses the mailbox password for both services when
            # the account has no separate target-service password.
            password = auth_password
            two_factor = None
        elif len(parts) == 3:
            email, password, auth_password = parts
            two_factor = None
        elif len(parts) == 4:
            email, password, auth_password, two_factor = parts
        else:
            errors.append(
                {"line": line_num, "message": "需要两段、三段或四段内容，以 ---- 分隔"}
            )
            continue
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
        elif email in seen or email in existing:
            errors.append({"line": line_num, "message": "邮箱重复（包含已删除账号）"})
        else:
            try:
                uri = (
                    totp.import_uri(two_factor, email)
                    if two_factor is not None
                    else None
                )
            except totp.InvalidQR as exc:
                errors.append({"line": line_num, "message": str(exc)})
            else:
                rows.append((line_num, email, password, auth_password, uri))
        seen.add(email)
    if not seen and not errors:
        errors.append({"line": 1, "message": "请至少输入一个账号"})
    return rows, errors


def import_accounts(db, user, data):
    cfg = settings(db)
    rows, errors = parse_import(db, data)
    if errors:
        raise HTTPException(
            422, {"message": "请修正全部问题后重新导入", "errors": errors}
        )
    group_ids, user_ids = set(data.group_ids), set(data.user_ids)
    if (
        group_ids
        and set(db.scalars(select(Group.id).where(Group.id.in_(group_ids))))
        != group_ids
    ) or (
        user_ids
        and set(
            db.scalars(
                select(User.id).where(User.id.in_(user_ids), User.deleted.is_(False))
            )
        )
        != user_ids
    ):
        fail("选择的用户或用户组已不存在", 422)
    accounts = []
    for _, email, password, auth_password, two_factor_uri in rows:
        a = Account(
            id=uid(),
            email=email,
            mail_tool=data.mail_tool,
            tier=data.tier,
            expires_at=data.expires_at,
            capacity=data.capacity,
            quota_reset_interval_days=data.quota_reset_interval_days,
            password_encrypted=cipher().encrypt(password.encode()).decode(),
            auth_password_encrypted=cipher().encrypt(auth_password.encode()).decode(),
        )
        accounts.append(a)
    db.add_all(accounts)
    db.flush()
    for a, row in zip(accounts, rows):
        two_factor_uri = row[4]
        db.add_all(
            AccountGroup(account_id=a.id, group_id=gid) for gid in sorted(group_ids)
        )
        db.add_all(
            AccountUser(account_id=a.id, user_id=uid) for uid in sorted(user_ids)
        )
        event(
            db,
            "account_created",
            user,
            a,
            {
                "tier": data.tier,
                "tier_name": option_name(cfg, "tiers", data.tier),
                "mail_tool": data.mail_tool,
                "quota_reset_interval_days": data.quota_reset_interval_days,
            },
        )
        if two_factor_uri:
            db.add(
                TwoFactor(
                    account_id=a.id,
                    kind="service",
                    uri_encrypted=cipher().encrypt(two_factor_uri.encode()).decode(),
                    version=1,
                    updated_by=user.id,
                )
            )
            event(db, "two_factor_updated", user, a, {"target": "service"})
    db.flush()
    return accounts


def reconcile(db, stamp=None):
    stamp = stamp or now()
    cfg = settings(db)
    if not cfg:
        return
    accounts = list(
        db.scalars(
            select(Account).where(
                Account.deleted.is_(False),
                or_(Account.reset_at <= stamp, Account.health == "abnormal"),
            )
        )
    )
    observed_ids = (
        set(
            db.scalars(
                select(Claim.account_id)
                .join(Account, Account.id == Claim.account_id)
                .where(
                    Claim.account_id.in_([a.id for a in accounts]),
                    Claim.returned_at.is_(None),
                    Claim.invalidated_at.is_(None),
                    Claim.observed_health_version == Account.health_version,
                    Claim.claimed_at <= stamp - timedelta(hours=cfg.observation_hours),
                )
                .distinct()
            )
        )
        if accounts
        else set()
    )
    for a in accounts:
        settle_quota_reset(db, a, stamp)
        if a.health != "abnormal" or not a.anomaly_since:
            continue
        cooled = (
            a.anomaly_since
            + timedelta(hours=anomaly_cooldown_hours(cfg, a.health_categories))
            <= stamp
        )
        observed = a.id in observed_ids
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
