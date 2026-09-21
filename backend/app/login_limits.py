"""Database-backed admission control for anonymous password verification.

One nonblocking transaction lock bounds concurrent login work across API
processes. It is deliberately independent of the business write lock. No client
IP or forwarded header is trusted, and rejected requests allocate no counters.
"""

import math
from datetime import timedelta

from sqlalchemy import delete, select, text

from . import config
from .models import LoginAttempt, LoginBudget, now

LOGIN_LOCK = 71820420
FAILURE_WINDOW = timedelta(minutes=15)
FAILURE_LIMIT = 10
COUNTER_CAPACITY = 1024
CLEANUP_BATCH = 256


def try_lock(db):
    return db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOGIN_LOCK}
    )


def prune_attempts(db, stamp):
    # The timestamp index and batch limit also bound cleanup of a legacy backlog.
    expired = (
        select(LoginAttempt.key)
        .where(LoginAttempt.since <= stamp - FAILURE_WINDOW)
        .order_by(LoginAttempt.since)
        .limit(CLEANUP_BATCH)
    )
    db.execute(delete(LoginAttempt).where(LoginAttempt.key.in_(expired)))


def cleanup(db):
    # Never queue behind password verification or invert business/login locks.
    if try_lock(db):
        prune_attempts(db, now())


def reject(response, seconds):
    seconds = max(1, math.ceil(seconds))
    response.headers["Retry-After"] = str(seconds)
    return None, f"登录请求过于频繁，请 {seconds} 秒后重试", 429


def admit(db, key, stamp):
    """Return (counter, retry seconds); caller already holds the login lock.

    GCRA stores the next scheduled verification time in one durable row. Its
    burst allowance refills gradually, avoiding fixed-window boundary bursts.
    Successful logins consume the same budget and cannot reset it.
    """
    interval = timedelta(seconds=60 / config.LOGIN_RATE_PER_MINUTE)
    budget = db.get(LoginBudget, 1)
    next_at = max(budget.next_at, stamp) if budget else stamp
    retry = (next_at - (config.LOGIN_BURST - 1) * interval - stamp).total_seconds()
    if retry > 0:
        return None, retry

    prune_attempts(db, stamp)
    attempt = db.get(LoginAttempt, key)
    if attempt and attempt.since <= stamp - FAILURE_WINDOW:
        # A large pre-upgrade backlog can outlive this cleanup batch.
        attempt.since, attempt.attempts = stamp, 0
    if attempt and attempt.attempts >= FAILURE_LIMIT:
        return None, (attempt.since + FAILURE_WINDOW - stamp).total_seconds()
    if (
        attempt is None
        and db.scalar(
            select(LoginAttempt.key)
            .order_by(LoginAttempt.key)
            .offset(COUNTER_CAPACITY - 1)
            .limit(1)
        )
        is not None
    ):
        # Do not evict live counters: that would let rotating names unlock an
        # account under attack. Existing counters remain usable at capacity.
        return None, 30

    if budget is None:
        budget = LoginBudget(id=1, next_at=stamp)
        db.add(budget)
    budget.next_at = next_at + interval
    return attempt, None
