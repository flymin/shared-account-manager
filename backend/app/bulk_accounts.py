"""Explicit account selections and atomic administrator edits, with preview fencing."""

import hashlib
import json
from itertools import islice
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import load_only

from . import domain as d, queries
from .models import (
    Account,
    AccountGroup,
    AccountNote,
    AccountUser,
    EmailCodeRun,
    Event,
    Group,
    MailOAuthCredential,
    MailOAuthState,
    User,
    now,
)
from .verification import ACTIVE, finish


LINK_BATCH_SIZE = 1000


def digest(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, default=encode).encode()
    ).hexdigest()


def resolve_selection(db, user, data):
    query = queries.filtered_accounts(
        user, d.settings(db), now(), **data.filters.model_dump()
    )
    if data.account_ids is not None:
        query = query.where(Account.id.in_(set(data.account_ids)))
    ids = list(
        db.scalars(
            query.with_only_columns(Account.id).order_by(Account.id).limit(10001)
        )
    )
    if len(ids) > 10000:
        raise HTTPException(422, "单次最多选择 10000 个账号，请缩小筛选范围")
    return {"account_ids": ids, "count": len(ids)}


def inspect(db, user, data):
    ids = sorted(set(data.account_ids))
    cfg = d.settings(db)
    rows = list(
        db.scalars(
            queries.account_columns(
                queries.filtered_accounts(user, cfg, now(), **data.filters.model_dump())
                .where(Account.id.in_(ids))
                .order_by(Account.id)
            ).options(load_only(Account.quota_version))
        )
    )
    if len(rows) != len(ids):
        raise HTTPException(409, "部分账号已删除或不再符合筛选，请重新核对选择并预览")
    values = {
        key: value
        for key, value in data.changes.model_dump().items()
        if key in data.changes.model_fields_set
    }
    if not values:
        raise HTTPException(422, "请至少选择一项要修改的内容")
    if "tier" in values:
        for account in rows:
            d.validate_tier(db, values["tier"], current=account.tier)
    if "mail_tool" in values:
        d.validate_mail_tool(values["mail_tool"])
    if "reset_at" in values:
        d.validate_reset(values["reset_at"])
    if "health" in values and values["health"]["action"] == "report":
        d.validate_categories(db, values["health"]["categories"])
        if not values["health"]["categories"] and not values["health"]["note"].strip():
            raise HTTPException(422, "请选择异常类别或填写异常问题")
    for field, model in (("group_ids", Group), ("user_ids", User)):
        if field in values:
            wanted = set(values[field])
            query = select(model.id).where(model.id.in_(wanted))
            if model is User:
                query = query.where(User.deleted.is_(False))
            if set(db.scalars(query)) != wanted:
                raise HTTPException(422, "选择的用户或用户组已不存在")
    links = {}
    for model, field in ((AccountGroup, "group_id"), (AccountUser, "user_id")):
        links[field] = sorted(
            tuple(row)
            for row in db.execute(
                select(model.account_id, getattr(model, field)).where(
                    model.account_id.in_(ids)
                )
            )
        )
    # Public account state and the latest event fence all account edits, including
    # credentials and notes, without loading or returning encrypted credentials.
    events = list(
        db.execute(
            select(Event.account_id, func.max(Event.id))
            .where(Event.account_id.in_(ids))
            .group_by(Event.account_id)
        )
    )
    public = [{name: getattr(a, name) for name in queries.ACCOUNT_FIELDS} for a in rows]
    revision = digest(
        {
            "accounts": public,
            "links": links,
            "events": sorted(tuple(r) for r in events),
            "settings": {
                "options": cfg.account_options,
                "interval": cfg.quota_reset_interval_days,
                "capacity": cfg.account_capacity,
                "threshold": cfg.quota_depleted_threshold,
            },
        }
    )
    return rows, values, revision, cfg


def preview(db, user, data):
    rows, values, revision, cfg = inspect(db, user, data)
    return {"count": len(rows), "fields": sorted(values), "revision": revision}


def execute(db, user, data):
    request_hash = digest(
        {
            "ids": sorted(set(data.account_ids)),
            "filters": data.filters.model_dump(),
            "changes": data.changes.model_dump(exclude_unset=True),
            "revision": data.revision,
        }
    )
    key = f"account-bulk:{data.request_id}"
    previous = db.scalar(select(Event).where(Event.automation_key == key))
    if previous:
        if (
            previous.actor_id != user.id
            or previous.details["request_hash"] != request_hash
        ):
            raise HTTPException(409, "请求标识已被其他操作使用，请重新预览")
        return previous.details["result"]
    rows, values, revision, cfg = inspect(db, user, data)
    if revision != data.revision:
        raise HTTPException(409, "账号数据已发生变化，本次未修改任何账号，请重新预览")
    ids = [a.id for a in rows]
    stamp = now()
    events = []

    def log(kind, account_id, details=None):
        events.append(
            {
                "kind": kind,
                "account_id": account_id,
                "actor_id": user.id,
                "created_at": stamp,
                "details": details or {},
            }
        )

    # Settle overdue quota with the old configuration before applying explicit
    # quota/schedule edits. Metadata-only edits do not touch quota state.
    if "quota" in values or "reset_at" in values:
        for a in rows:
            d.settle_quota_reset(db, a, stamp)
        db.flush()
    scalar = {
        k: v
        for k, v in values.items()
        if k
        in {
            "tier",
            "capacity",
            "quota_reset_interval_days",
            "expires_at",
            "mail_tool",
            "reset_at",
        }
    }
    if "enabled" in values:
        scalar["disabled"] = not values["enabled"]
    if "quota" in values:
        scalar.update(
            quota=values["quota"], quota_updated_at=stamp, quota_source="user"
        )
    if "quota" in values or "reset_at" in values:
        scalar["quota_version"] = Account.quota_version + 1
    changed_tools = [
        a.id
        for a in rows
        if "mail_tool" in values and a.mail_tool != values["mail_tool"]
    ]
    if changed_tools:
        runs = db.scalars(
            select(EmailCodeRun).where(
                EmailCodeRun.account_id.in_(changed_tools),
                EmailCodeRun.status.in_((*ACTIVE, "found")),
            )
        )
        for run in runs:
            finish(run, "cancelled")
        for model in (MailOAuthCredential, MailOAuthState):
            db.execute(delete(model).where(model.account_id.in_(changed_tools)))
        db.execute(
            update(Account)
            .where(Account.id.in_(changed_tools))
            .values(mail_config_version=Account.mail_config_version + 1)
            .execution_options(synchronize_session=False)
        )
    if scalar:
        db.execute(
            update(Account)
            .where(Account.id.in_(ids))
            .values(**scalar)
            .execution_options(synchronize_session=False)
        )
    for field, model, target in (
        ("group_ids", AccountGroup, "group_id"),
        ("user_ids", AccountUser, "user_id"),
    ):
        if field in values:
            db.execute(delete(model).where(model.account_id.in_(ids)))
            targets = sorted(set(values[field]))
            links = (
                {"account_id": identity, target: value}
                for identity in ids
                for value in targets
            )
            while chunk := list(islice(links, LINK_BATCH_SIZE)):
                db.execute(insert(model), chunk)
    if "health" in values:
        health = values["health"]
        for a in rows:
            if health["action"] == "clear":
                d.clear_health(db, a, user)
            else:
                d.health_report(db, a, user, health["categories"], health["note"])
    if "note" in values:
        added = db.execute(
            insert(AccountNote).returning(AccountNote.id, AccountNote.account_id),
            [
                {
                    "account_id": identity,
                    "author_id": user.id,
                    "content": values["note"],
                    "created_at": stamp,
                }
                for identity in ids
            ],
        )
        for note_id, identity in added:
            log("account_note_added", identity, {"note_id": note_id})
    regular = sorted(set(values) - {"health", "note", "enabled", "quota", "reset_at"})
    for a in rows:
        if regular:
            log("account_updated", a.id, {"fields": regular, "bulk": True})
        if "enabled" in values and a.disabled == values["enabled"]:
            log("account_enabled" if values["enabled"] else "account_disabled", a.id)
        if "quota" in values:
            deadline = values.get("reset_at", a.reset_at)
            log(
                "quota_updated",
                a.id,
                {
                    "quota": values["quota"],
                    "reset_at": deadline.isoformat() if deadline else None,
                },
            )
        elif "reset_at" in values:
            log(
                "quota_schedule_updated",
                a.id,
                {
                    "next_reset_at": values["reset_at"].isoformat()
                    if values["reset_at"]
                    else None
                },
            )
    if events:
        db.execute(insert(Event), events)
    result = {"count": len(ids), "fields": sorted(values)}
    db.add(
        Event(
            kind="accounts_bulk_updated",
            actor_id=user.id,
            created_at=stamp,
            automation_key=key,
            details={
                "request_hash": request_hash,
                "result": result,
                "count": len(ids),
                "fields": sorted(values),
            },
        )
    )
    return result
