"""Bounded, permission-scoped reads; serializers never fetch individual rows.

Each batch owns its data only until its response has been assembled. In particular,
never reuse a batch across a write, a lock acquisition, or an external network call.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import any_, case, cast, func, literal_column, or_, select, true
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import aliased, load_only

from .models import (
    Account,
    AccountGroup,
    AccountNote,
    AccountUser,
    Claim,
    Event,
    Settings,
    User,
    UserGroup,
    now,
)
from .plugins import get_mail_tool


ACCOUNT_FIELDS = (
    "id",
    "email",
    "mail_tool",
    "tier",
    "created_at",
    "expires_at",
    "capacity",
    "deleted",
    "disabled",
    "quota",
    "reset_at",
    "quota_reset_interval_days",
    "quota_updated_at",
    "quota_source",
    "health",
    "health_categories",
    "health_note",
    "health_version",
    "anomaly_since",
)
USER_FIELDS = (
    "id",
    "username",
    "display_name",
    "role",
    "must_change_password",
    "claim_limit",
    "deleted",
    "created_at",
)
STATUSES = {"disabled", "abnormal", "possibly_recovered", "empty", "full", "normal"}


def account_columns(query):
    return query.options(
        load_only(*(getattr(Account, name) for name in ACCOUNT_FIELDS), raiseload=True)
    )


def user_columns(query):
    return query.options(
        load_only(*(getattr(User, name) for name in USER_FIELDS), raiseload=True)
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


def page(db, query, offset=0, limit=None):
    """An omitted limit retains the existing full-list contract."""
    total = None
    if offset or limit is not None:
        total = db.scalar(
            query.order_by(None).with_only_columns(
                func.count(), maintain_column_froms=True
            )
        )
    rows = list(db.scalars(query.offset(offset).limit(limit)))
    return rows, len(rows) if total is None else total


def occupancy(account_ids=None):
    query = select(Claim.account_id, func.count().label("active_count")).where(
        Claim.returned_at.is_(None), Claim.invalidated_at.is_(None)
    )
    if account_ids is not None:
        query = query.where(Claim.account_id.in_(account_ids))
    return query.group_by(Claim.account_id).subquery()


def account_status(cfg, active_count):
    return case(
        (Account.disabled, "disabled"),
        (Account.health == "abnormal", "abnormal"),
        (Account.health == "possibly_recovered", "possibly_recovered"),
        (Account.quota < cfg.quota_depleted_threshold, "empty"),
        (active_count >= func.coalesce(Account.capacity, cfg.account_capacity), "full"),
        else_="normal",
    )


def account_page(
    db,
    user,
    *,
    scope="all",
    q="",
    tier=None,
    group_id=None,
    statuses=None,
    sort_by=None,
    order="asc",
    offset=0,
    limit=None,
):
    stamp = now()
    cfg = db.get(Settings, 1)
    query = select(Account).where(Account.deleted.is_(False))
    if scope == "hall":
        query = query.where(
            Account.disabled.is_(False),
            or_(Account.expires_at.is_(None), Account.expires_at > stamp),
        )
    if user.role != "admin":
        query = query.where(visible_clause(user.id))
    if q:
        query = query.where(
            func.lower(Account.email).contains(q.lower(), autoescape=True)
        )
    if tier is not None:
        query = query.where(Account.tier == tier)
    if group_id is not None:
        query = query.where(
            Account.id.in_(
                select(AccountGroup.account_id).where(AccountGroup.group_id == group_id)
            )
        )
    if statuses is not None:
        chosen = set(statuses.split(",")) if statuses else set()
        if not chosen <= STATUSES:
            raise HTTPException(422, "账号状态无效")
        unrestricted = chosen == STATUSES or (
            scope == "hall" and chosen | {"disabled"} == STATUSES
        )
        counts = (
            occupancy(query.with_only_columns(Account.id).correlate(None))
            if not unrestricted
            else None
        )
        if not unrestricted:
            query = query.outerjoin(counts, counts.c.account_id == Account.id).where(
                account_status(cfg, func.coalesce(counts.c.active_count, 0)).in_(chosen)
            )
    email = Account.email.collate("account_email_natural")
    if sort_by == "email":
        ordering = [email.asc() if order == "asc" else email.desc()]
    elif sort_by == "quota":
        ordering = [
            (
                Account.quota.asc() if order == "asc" else Account.quota.desc()
            ).nulls_last(),
            email.asc(),
        ]
    elif sort_by == "reset_at":
        ordering = [
            Account.reset_at.asc().nulls_first()
            if order == "asc"
            else Account.reset_at.desc().nulls_last(),
            email.asc(),
        ]
    else:
        ordering = [Account.created_at.desc()]
    rows, total = page(
        db, account_columns(query.order_by(*ordering, Account.id)), offset, limit
    )
    return accounts_dto(db, user, rows, cfg=cfg, stamp=stamp), total


def event_dtos(db, events, identities=False):
    events = list(events)
    if not events:
        return []
    user_ids = {e.actor_id for e in events if e.actor_id}
    if identities:
        user_ids |= {e.target_user_id for e in events if e.target_user_id}
    users = (
        {
            row.id: row
            for row in db.execute(
                select(User.id, User.username, User.display_name).where(
                    User.id.in_(user_ids)
                )
            )
        }
        if user_ids
        else {}
    )
    account_ids = {e.account_id for e in events if e.account_id}
    emails = (
        dict(
            db.execute(
                select(Account.id, Account.email).where(Account.id.in_(account_ids))
            ).all()
        )
        if identities and account_ids
        else {}
    )
    result = []
    for e in events:
        actor = users.get(e.actor_id)
        item = dict(
            id=e.id,
            account_id=e.account_id,
            actor=actor.display_name if actor else "系统",
            kind=e.kind,
            details=e.details,
            created_at=e.created_at,
            target_user_id=e.target_user_id,
        )
        if identities:
            target = users.get(e.target_user_id)
            item.update(
                account_email=emails.get(e.account_id),
                target_username=target.username if target else None,
                target_display_name=target.display_name if target else None,
            )
        result.append(item)
    return result


@dataclass
class AccountBatch:
    cfg: Settings
    stamp: datetime
    quota_used: int
    visible: set
    active: dict
    mine: set
    notes: dict
    recent: dict
    groups: dict
    users: dict
    tools: dict
    labels: dict


def recent_quota_events(account_ids):
    selected = (
        select(Account.id.label("account_id"))
        .where(Account.id == any_(cast(list(account_ids), ARRAY(Account.id.type))))
        .subquery()
    )
    latest = (
        select(Event)
        .where(
            Event.account_id == any_(array([selected.c.account_id])),
            # Fixed predicates also permit the partial index in generic plans.
            Event.kind.in_(
                [literal_column("'quota_updated'"), literal_column("'quota_reset'")]
            ),
        )
        # Retain the account prefix in the index ordering. An ordinary equality
        # lets PostgreSQL discard that key and favor a global primary-key scan
        # for LIMIT 3, badly misestimating long stretches of non-quota events.
        .order_by(Event.account_id, Event.id.desc())
        .limit(3)
        .lateral()
    )
    return (
        select(aliased(Event, latest))
        .select_from(selected)
        .join(latest, true())
        .order_by(latest.c.id.desc())
    )


def account_batch(db, user, accounts, cfg=None, stamp=None):
    ids = {a.id for a in accounts}
    cfg = cfg or db.get(Settings, 1)
    used = db.scalar(
        select(func.count())
        .select_from(Claim)
        .where(Claim.user_id == user.id, Claim.returned_at.is_(None))
    )
    visible = (
        ids
        if user.role == "admin"
        else set(
            db.scalars(
                select(Account.id).where(Account.id.in_(ids), visible_clause(user.id))
            )
        )
    )
    active, mine = defaultdict(list), set()
    for claim, name in db.execute(
        select(Claim, User.display_name)
        .join(User, User.id == Claim.user_id)
        .where(
            Claim.account_id.in_(ids),
            Claim.returned_at.is_(None),
            or_(Claim.invalidated_at.is_(None), Claim.user_id == user.id),
        )
        .order_by(Claim.claimed_at, Claim.id)
    ):
        if claim.invalidated_at is None:
            active[claim.account_id].append(name)
        if claim.user_id == user.id:
            mine.add(claim.account_id)
    notes = dict(
        db.execute(
            select(AccountNote.account_id, func.count())
            .where(AccountNote.account_id.in_(ids))
            .group_by(AccountNote.account_id)
        ).all()
    )
    events = list(db.scalars(recent_quota_events(ids)))
    recent = defaultdict(list)
    for item in event_dtos(db, events):
        recent[item["account_id"]].append(item)
    groups, users = defaultdict(list), defaultdict(list)
    if user.role == "admin":
        for aid, gid in db.execute(
            select(AccountGroup.account_id, AccountGroup.group_id).where(
                AccountGroup.account_id.in_(ids)
            )
        ):
            groups[aid].append(gid)
        for aid, uid in db.execute(
            select(AccountUser.account_id, AccountUser.user_id)
            .join(User, User.id == AccountUser.user_id)
            .where(AccountUser.account_id.in_(ids), User.deleted.is_(False))
        ):
            users[aid].append(uid)
    tools = {}
    for identity in {a.mail_tool for a in accounts}:
        tool = get_mail_tool(identity)
        tools[identity] = tool.name if tool else "不可用工具" if identity else None
    return AccountBatch(
        cfg,
        stamp or now(),
        used,
        visible,
        active,
        mine,
        notes,
        recent,
        groups,
        users,
        tools,
        {
            kind: {item["id"]: item["name"] for item in cfg.account_options[kind]}
            for kind in ("tiers", "anomaly_categories")
        },
    )


def blocked_reasons(user, a, cfg, stamp, used, active_count, mine, visible):
    reasons = []
    if used >= (user.claim_limit or cfg.user_claim_limit):
        reasons.append("个人名额已满")
    if active_count >= (a.capacity or cfg.account_capacity):
        reasons.append("人数已满")
    if a.disabled:
        reasons.append("已停用")
    if a.expires_at and a.expires_at <= stamp:
        reasons.append("已过期")
    if mine:
        reasons.append("已领用，尚未归还")
    if a.deleted or not visible:
        reasons.append("无新领用权限")
    return reasons


def claim_gate(db, user, account):
    """Claim validation needs counts and rights, not notes or event history."""
    cfg = db.get(Settings, 1)
    used = db.scalar(
        select(func.count())
        .select_from(Claim)
        .where(Claim.user_id == user.id, Claim.returned_at.is_(None))
    )
    count, mine = db.execute(
        select(
            func.count().filter(Claim.invalidated_at.is_(None)),
            func.bool_or(Claim.user_id == user.id),
        ).where(Claim.account_id == account.id, Claim.returned_at.is_(None))
    ).one()
    visible = user.role == "admin" or bool(
        db.scalar(
            select(Account.id).where(Account.id == account.id, visible_clause(user.id))
        )
    )
    return blocked_reasons(user, account, cfg, now(), used, count, bool(mine), visible)


def account_dto(user, a, batch):
    """Pure response assembly. No Session is accepted here."""
    cfg = batch.cfg
    capacity = a.capacity or cfg.account_capacity
    active = batch.active.get(a.id, [])
    reasons = blocked_reasons(
        user,
        a,
        cfg,
        batch.stamp,
        batch.quota_used,
        len(active),
        a.id in batch.mine,
        a.id in batch.visible,
    )
    labels = batch.labels
    result = {
        name: getattr(a, name)
        for name in (
            "id",
            "email",
            "mail_tool",
            "tier",
            "disabled",
            "created_at",
            "expires_at",
            "capacity",
            "quota",
            "reset_at",
            "quota_reset_interval_days",
            "quota_updated_at",
            "quota_source",
            "health",
            "health_categories",
            "health_note",
            "health_version",
            "anomaly_since",
        )
    }
    result.update(
        note_count=batch.notes.get(a.id, 0),
        mail_tool_name=batch.tools[a.mail_tool],
        tier_name=labels["tiers"].get(a.tier, a.tier),
        effective_capacity=capacity,
        quota_depleted=a.quota is not None and a.quota < cfg.quota_depleted_threshold,
        health_category_names=[
            labels["anomaly_categories"].get(identity, identity)
            for identity in a.health_categories
        ],
        can_claim=not reasons,
        has_open_claim=a.id in batch.mine,
        blocked_reasons=reasons,
        active_count=len(active),
        current_users=active,
        recent_updates=batch.recent.get(a.id, []),
    )
    if user.role == "admin":
        result.update(
            group_ids=batch.groups.get(a.id, []), user_ids=batch.users.get(a.id, [])
        )
    return result


def accounts_dto(db, user, accounts, cfg=None, stamp=None):
    accounts = list(accounts)
    if not accounts:
        return []
    batch = account_batch(db, user, accounts, cfg, stamp)
    return [account_dto(user, a, batch) for a in accounts]


def users_dto(db, users):
    users = list(users)
    if not users:
        return []
    ids = {u.id for u in users}
    cfg = db.get(Settings, 1)
    counts = dict(
        db.execute(
            select(Claim.user_id, func.count())
            .where(Claim.user_id.in_(ids), Claim.returned_at.is_(None))
            .group_by(Claim.user_id)
        ).all()
    )
    groups = defaultdict(list)
    for uid, gid in db.execute(
        select(UserGroup.user_id, UserGroup.group_id).where(UserGroup.user_id.in_(ids))
    ):
        groups[uid].append(gid)
    return [
        dict(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            must_change_password=u.must_change_password,
            claim_limit=u.claim_limit,
            effective_claim_limit=u.claim_limit or cfg.user_claim_limit,
            claims_used=counts.get(u.id, 0),
            group_ids=groups.get(u.id, []),
        )
        for u in users
    ]


def groups_dto(db, groups):
    groups = list(groups)
    if not groups:
        return []
    members = defaultdict(list)
    for gid, uid in db.execute(
        select(UserGroup.group_id, UserGroup.user_id)
        .join(User, User.id == UserGroup.user_id)
        .where(UserGroup.group_id.in_([g.id for g in groups]), User.deleted.is_(False))
        .order_by(UserGroup.user_id)
    ):
        members[gid].append(uid)
    return [dict(id=g.id, name=g.name, user_ids=members.get(g.id, [])) for g in groups]


def claims_dto(db, user, claims):
    claims = list(claims)
    if not claims:
        return []
    accounts = {
        a.id: a
        for a in db.scalars(
            account_columns(
                select(Account).where(Account.id.in_({c.account_id for c in claims}))
            )
        )
    }
    full_ids = {
        c.account_id
        for c in claims
        if not c.invalidated_at
        and not c.returned_at
        and not accounts[c.account_id].deleted
    }
    full = {
        item["id"]: item
        for item in accounts_dto(db, user, [accounts[aid] for aid in full_ids])
    }
    cfg = (
        db.get(Settings, 1)
        if any(
            c.returned_at
            and not c.invalidated_at
            and not accounts[c.account_id].deleted
            for c in claims
        )
        else None
    )
    labels = (
        {item["id"]: item["name"] for item in cfg.account_options["tiers"]}
        if cfg
        else {}
    )
    names = (
        dict(
            db.execute(
                select(User.id, User.display_name).where(
                    User.id.in_({c.user_id for c in claims})
                )
            ).all()
        )
        if user.role == "admin"
        else {}
    )
    result = []
    for c in claims:
        a = accounts[c.account_id]
        item = {
            name: getattr(c, name)
            for name in (
                "id",
                "claimed_at",
                "returned_at",
                "return_kind",
                "invalidated_at",
                "invalidation_kind",
                "invalidation_reason",
            )
        }
        if user.role == "admin":
            item.update(
                user_id=c.user_id,
                user_name=names.get(c.user_id),
                account_id=a.id,
                account_email=a.email,
            )
        if c.invalidated_at or a.deleted:
            item["account"] = None
        elif c.returned_at:
            item["account"] = dict(
                id=a.id,
                email=a.email,
                tier=a.tier,
                tier_name=labels.get(a.tier, a.tier),
            )
        else:
            item["account"] = full[a.id]
        result.append(item)
    return result


def overview(db):
    cfg, stamp = db.get(Settings, 1), now()
    counts = occupancy()
    available = (
        Account.disabled.is_(False)
        & (
            func.coalesce(counts.c.active_count, 0)
            < func.coalesce(Account.capacity, cfg.account_capacity)
        )
        & or_(Account.expires_at.is_(None), Account.expires_at > stamp)
    )
    row = db.execute(
        select(
            func.count().label("accounts"),
            func.count().filter(available).label("available"),
            func.count().filter(Account.health == "abnormal").label("abnormal"),
            func.count().filter(Account.disabled).label("disabled"),
            func.count()
            .filter(Account.quota < cfg.quota_depleted_threshold)
            .label("depleted"),
            func.count().filter(Account.expires_at <= stamp).label("expired"),
        )
        .select_from(Account)
        .outerjoin(counts, counts.c.account_id == Account.id)
        .where(Account.deleted.is_(False))
    ).one()
    claims = db.execute(
        select(
            func.count().filter(Claim.invalidated_at.is_(None)).label("active_claims"),
            func.count()
            .filter(Claim.invalidated_at.is_not(None))
            .label("pending_claims"),
        ).where(Claim.returned_at.is_(None))
    ).one()
    return {**row._mapping, **claims._mapping}
