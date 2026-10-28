"""Filtered, transactional quota resets; operation time is distinct from occurrence."""

from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import Integer, and_, cast, func, insert, literal, select, update

from . import queries
from .models import Account, Event, Settings, now


def selection(db, user, data, stamp):
    if data.occurred_at > stamp:
        raise HTTPException(422, "实际重置发生时间不能晚于当前时间")
    cfg = db.get(Settings, 1)
    query = queries.filtered_accounts(user, cfg, stamp, **data.filters.model_dump())
    eligible = and_(
        Account.reset_at.is_not(None),
        Account.quota_updated_at.is_not(None),
        Account.quota_updated_at < data.occurred_at,
    )
    matched, count = db.execute(
        query.with_only_columns(func.count(), func.count().filter(eligible))
    ).one()
    return (
        cfg,
        query.where(eligible),
        {
            "matched_count": matched,
            "eligible_count": count,
            "skipped_count": matched - count,
        },
    )


def preview(db, user, data):
    return selection(db, user, data, now())[2]


def execute(db, user, data):
    # The authenticated write dependency holds the shared business transaction
    # lock, including while a retry checks its persisted operation identity.
    stamp = now()
    occurred = data.occurred_at.astimezone(timezone.utc)
    identity = f"quota-bulk:{data.request_id}"
    request = {
        "occurred_at": occurred.isoformat(),
        "filters": data.filters.model_dump(),
    }
    previous = db.scalar(select(Event).where(Event.automation_key == identity))
    if previous:
        if previous.actor_id != user.id or previous.details["request"] != request:
            raise HTTPException(409, "此请求标识已被其他操作使用，请重新预览")
        return {k: v for k, v in previous.details.items() if k != "request"}
    cfg, query, counts = selection(db, user, data, stamp)
    days = func.coalesce(
        Account.quota_reset_interval_days, cfg.quota_reset_interval_days
    )
    elapsed = func.extract("epoch", literal(stamp) - literal(occurred))
    cycles = cast(func.floor(elapsed / (days * 86400)) + 1, Integer)
    # Add calendar days in UTC, independent of the database session time zone.
    next_reset = func.timezone(
        "UTC",
        func.timezone("UTC", literal(occurred))
        + func.make_interval(0, 0, 0, days * cycles),
    )
    changed = db.execute(
        update(Account)
        .where(Account.id.in_(query.with_only_columns(Account.id)))
        .values(
            quota=100,
            quota_updated_at=occurred,
            quota_source="bulk_reset",
            reset_at=next_reset,
            quota_version=Account.quota_version + 1,
        )
        .returning(Account.id, Account.reset_at)
        .execution_options(synchronize_session=False)
    ).all()
    if changed:
        db.execute(
            insert(Event),
            [
                {
                    "account_id": account_id,
                    "actor_id": user.id,
                    "kind": "quota_updated",
                    "created_at": stamp,
                    "details": {
                        "source": "bulk_reset",
                        "quota": 100,
                        "occurred_at": occurred.isoformat(),
                        "next_reset_at": deadline.isoformat(),
                    },
                }
                for account_id, deadline in changed
            ],
        )
    result = {
        **counts,
        "reset_count": len(changed),
        "occurred_at": occurred.isoformat(),
        "performed_at": stamp.isoformat(),
    }
    db.add(
        Event(
            kind="quota_bulk_reset",
            actor_id=user.id,
            created_at=stamp,
            automation_key=identity,
            details={"request": request, **result},
        )
    )
    return result
