"""Small, isolated requests exercise anonymous admission; no external traffic."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import subprocess
import sys

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import auth, config, login_limits as limits
from app.db import Session, write_lock
from app.main import app
from app.models import LoginAttempt, LoginBudget, LoginSession, User, now
from app.schemas import LoginInput
from app.worker import tick
from conftest import PASSWORD

PATH = "/api/v1/auth/login"


def request(client, username="ghost", password="wrong", **headers):
    return client.post(
        PATH,
        headers={"X-Login-Request": "1", **headers},
        json={"username": username, "password": password},
    )


def counts():
    with Session() as db:
        return (
            db.scalar(select(func.count()).select_from(LoginAttempt)),
            db.scalar(select(func.count()).select_from(LoginBudget)),
        )


def test_random_names_and_forged_headers_share_budget_before_argon2(monkeypatch):
    stamp = now()
    calls = []
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(
        auth, "check_password", lambda encoded, password: calls.append(encoded) or False
    )
    for index in range(config.LOGIN_BURST + 25):
        # Independent clients/sessions cannot restart the durable budget.
        with TestClient(app) as client:
            response = request(
                client,
                username=f"random-{index}",
                **{
                    "X-Forwarded-For": f"192.0.2.{index + 1}",
                    "X-Real-IP": "203.0.113.1",
                },
            )
        assert response.status_code == (401 if index < config.LOGIN_BURST else 429)
        if response.status_code == 429:
            assert response.headers["Retry-After"] == "2"
    assert calls == [auth.DUMMY_HASH] * config.LOGIN_BURST
    assert counts() == (config.LOGIN_BURST, 1)
    with Session() as db:
        keys = set(db.scalars(select(LoginAttempt.key)))
    assert all(len(key) == 64 and "random" not in key for key in keys)


def test_budget_refills_gradually_and_is_not_reset_by_success(monkeypatch):
    stamp = now()
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(config, "LOGIN_BURST", 2)
    with TestClient(app) as client:
        assert request(client, "admin", PASSWORD).status_code == 200
        assert request(client, "admin", PASSWORD).status_code == 200
        blocked = request(client, "admin", PASSWORD)
        assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "2"
        stamp += timedelta(seconds=1)
        assert request(client, "other").headers["Retry-After"] == "1"
        stamp += timedelta(seconds=1)
        assert request(client, "admin", PASSWORD).status_code == 200
        assert request(client, "other").status_code == 429
    assert counts() == (0, 1)


def test_fresh_api_process_keeps_committed_budget(monkeypatch):
    stamp = now()
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(auth, "check_password", lambda *args: False)
    with TestClient(app) as client:
        for index in range(config.LOGIN_BURST):
            assert request(client, f"random-{index}").status_code == 401
    # A fresh interpreter has no inherited limiter/cache state. It shares only
    # PostgreSQL and still rejects the request before password verification.
    source = f"""
from datetime import datetime
from fastapi.testclient import TestClient
from app import auth, config
from app.db import engine
from app.main import app
assert engine.url.database.endswith("_test")
config.LOGIN_RATE_PER_MINUTE = {config.LOGIN_RATE_PER_MINUTE}
config.LOGIN_BURST = {config.LOGIN_BURST}
auth.now = lambda: datetime.fromisoformat({stamp.isoformat()!r})
def forbidden(*args):
    raise AssertionError("Rate-limited request reached password verifier")
auth.check_password = forbidden
with TestClient(app) as client:
    response = client.post({PATH!r}, headers={{"X-Login-Request": "1"}},
                           json={{"username": "new-process", "password": "wrong"}})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"
"""
    subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert counts() == (config.LOGIN_BURST, 1)


def test_success_only_clears_its_own_failure_counter(monkeypatch):
    stamp = now()
    monkeypatch.setattr(auth, "now", lambda: stamp)
    with TestClient(app) as client:
        assert request(client, "ADMIN").status_code == 401
        assert request(client, "ghost").status_code == 401
        assert request(client, "admin", PASSWORD).status_code == 200
    with Session() as db:
        assert db.get(LoginAttempt, auth.digest("admin")) is None
        assert db.get(LoginAttempt, auth.digest("ghost")).attempts == 1
        assert db.get(LoginBudget, 1).next_at == stamp + timedelta(seconds=6)


def test_username_lockout_survives_global_refill_and_expires(monkeypatch):
    stamp = now()
    first = stamp
    calls = []
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(
        auth, "check_password", lambda encoded, password: calls.append(encoded) or False
    )
    with TestClient(app) as client:
        for _ in range(10):
            assert request(client, "ADMIN").status_code == 401
            stamp += timedelta(seconds=2)
        blocked = request(client, "admin")
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "880"
        assert len(calls) == 10
        stamp = first + timedelta(minutes=15)
        assert request(client, "admin").status_code == 401
    with Session() as db:
        attempt = db.get(LoginAttempt, auth.digest("admin"))
        assert attempt.attempts == 1 and attempt.since == stamp


def test_login_cleanup_bounds_random_names_even_without_worker(monkeypatch):
    stamp = now()
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(auth, "check_password", lambda *args: False)
    # More than a full retention window; each call commits like a separate API.
    for index in range(40):
        with Session.begin() as db:
            result = auth.login(
                db, LoginInput(username=f"random-{index}", password="wrong"), Response()
            )
            assert result[2] == 401
        stamp += timedelta(seconds=30)
    assert counts() == (30, 1)


def test_counter_capacity_fails_closed_before_password_work(monkeypatch):
    stamp = now()
    with Session.begin() as db:
        db.add_all(
            LoginAttempt(key=auth.digest(f"random-{i}"), attempts=1, since=stamp)
            for i in range(limits.COUNTER_CAPACITY)
        )
    calls = []
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(
        auth, "check_password", lambda encoded, password: calls.append(encoded) or False
    )
    with TestClient(app) as client:
        assert request(client, "new-name").status_code == 429
        assert calls == [] and counts() == (limits.COUNTER_CAPACITY, 0)
        # Filling the table must not evict a live lockout or block known counters.
        assert request(client, "random-0").status_code == 401
        assert len(calls) == 1 and counts() == (limits.COUNTER_CAPACITY, 1)
        stamp += limits.FAILURE_WINDOW
        assert request(client, "new-name").status_code == 401
    assert counts()[0] == limits.COUNTER_CAPACITY - limits.CLEANUP_BATCH + 1


def test_worker_purges_legacy_backlog_in_batches_preserving_live_limits(monkeypatch):
    stamp = now()
    monkeypatch.setattr(limits, "now", lambda: stamp)
    with Session.begin() as db:
        db.add_all(
            LoginAttempt(
                key=auth.digest(f"expired-{i}"),
                attempts=10,
                since=stamp - limits.FAILURE_WINDOW,
            )
            for i in range(limits.CLEANUP_BATCH + 1)
        )
        db.add(LoginAttempt(key=auth.digest("live"), attempts=10, since=stamp))
        db.add(LoginBudget(id=1, next_at=stamp + timedelta(seconds=20)))
    tick()
    assert counts() == (2, 1)
    tick()
    assert counts() == (1, 1)
    with Session() as db:
        assert db.get(LoginAttempt, auth.digest("live")).attempts == 10
        assert db.get(LoginBudget, 1).next_at == stamp + timedelta(seconds=20)


def test_expired_named_counter_resets_even_with_large_legacy_backlog(monkeypatch):
    stamp = now()
    monkeypatch.setattr(auth, "now", lambda: stamp)
    monkeypatch.setattr(auth, "check_password", lambda *args: False)
    with Session.begin() as db:
        db.add_all(
            LoginAttempt(
                key=auth.digest(f"old-{i}"),
                attempts=10,
                since=stamp - timedelta(days=1),
            )
            for i in range(limits.CLEANUP_BATCH)
        )
        db.add(
            LoginAttempt(
                key=auth.digest("target"),
                attempts=10,
                since=stamp - limits.FAILURE_WINDOW,
            )
        )
    with TestClient(app) as client:
        assert request(client, "target").status_code == 401
    with Session() as db:
        attempt = db.get(LoginAttempt, auth.digest("target"))
        assert attempt.attempts == 1 and attempt.since == stamp


def test_concurrent_login_does_not_queue_argon2_or_block_business(admin, monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def slow_check(encoded, password):
        calls.append(encoded)
        entered.set()
        assert release.wait(10)
        return False

    monkeypatch.setattr(auth, "check_password", slow_check)
    with ThreadPoolExecutor(max_workers=1) as pool, TestClient(app) as client:
        pending = pool.submit(request, client)
        try:
            assert entered.wait(5)
            # Another independent session/process sees the shared lock immediately.
            with TestClient(app) as other:
                response = request(other, "different")
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "1"
            assert len(calls) == 1
            # This takes the business write lock; the slow hash must not hold it.
            assert (
                admin.post("/api/v1/groups", json={"name": "during-login"}).status_code
                == 201
            )
            # Worker maintenance skips the busy login lock and still reconciles.
            tick()
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    assert counts() == (1, 1)


@pytest.mark.parametrize("change", ["reset", "delete"])
def test_login_rechecks_user_changed_during_password_verification(monkeypatch, change):
    entered, release = Event(), Event()

    def slow_check(encoded, password):
        entered.set()
        assert release.wait(10)
        return True

    monkeypatch.setattr(auth, "check_password", slow_check)
    with ThreadPoolExecutor(max_workers=1) as pool, TestClient(app) as client:
        pending = pool.submit(request, client, "admin", PASSWORD)
        try:
            assert entered.wait(5)
            with Session.begin() as db:
                write_lock(db)
                user = db.scalar(select(User).where(User.username == "admin"))
                if change == "reset":
                    user.password_hash = "replaced-test-hash"
                else:
                    user.deleted = True
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(LoginSession)) == 0


def test_lock_released_after_transaction_failure(monkeypatch):
    def unavailable(*args):
        raise RuntimeError("test password verifier unavailable")

    monkeypatch.setattr(auth, "check_password", unavailable)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert request(client).status_code == 500
    monkeypatch.setattr(auth, "check_password", lambda *args: False)
    with TestClient(app) as client:
        assert request(client).status_code == 401


@pytest.mark.parametrize("value", ["0", "-1", "121", "not-an-integer"])
def test_invalid_login_rate_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv("LOGIN_RATE_PER_MINUTE", value)
    with pytest.raises(ValueError, match="LOGIN_RATE_PER_MINUTE"):
        config.bounded_int("LOGIN_RATE_PER_MINUTE", 30, 120)
