#!/usr/bin/env python3
"""Destructive resilience checks, restricted to an explicitly named test project."""

import argparse
import hashlib
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def call(self, path, method="GET", data=None):
        binary = isinstance(data, bytes)
        request = urllib.request.Request(
            self.base + "/api/v1" + path,
            method=method,
            data=data
            if binary
            else json.dumps(data).encode()
            if data is not None
            else None,
            headers={
                "Content-Type": "application/octet-stream"
                if binary
                else "application/json",
                "X-CSRF-Token": self.csrf,
                "X-Login-Request": "1",
            },
        )
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def login(self, username, password):
        self.csrf = self.call(
            "/auth/login", "POST", {"username": username, "password": password}
        )["csrf_token"]


def eventually(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Runtime check timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--password-file", required=True, type=Path)
    args = parser.parse_args()
    if not args.project.endswith(("-e2e", "-verify")):
        parser.error("Use a disposable Compose project ending in -e2e or -verify")
    env = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": args.project,
        "MAIL_CODE_SENDER": "noreply@login.example.test",
        "MAIL_CODE_SUBJECT_KEYWORD": "ExampleService",
    }
    # Keep the selected deployment's actual port when restore recreates its Web.
    from urllib.parse import urlparse

    address = urlparse(args.base_url)
    if address.scheme != "http" or address.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        parser.error("Runtime verification requires a local HTTP test deployment")
    env["HTTP_PORT"] = str(address.port or 80)
    binding = subprocess.run(
        [str(ROOT / "ops/compose.sh"), "port", "web", "80"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not binding or binding.rsplit(":", 1)[-1] != env["HTTP_PORT"]:
        parser.error("Base URL port does not match the selected Compose project")

    def compose(*command):
        return subprocess.run(
            [str(ROOT / "ops/compose.sh"), *command], cwd=ROOT, env=env, check=True
        )

    def script(name, *command):
        return subprocess.run(
            [sys.executable, str(ROOT / "ops" / name), *map(str, command)],
            cwd=ROOT,
            env=env,
            check=True,
        )

    # Apply synthetic mail rules only to the explicitly selected test deployment.
    # The worker is stopped before creating the job, then its deadline is expired.
    compose("up", "-d", "--no-build", "--wait")
    admin = Client(args.base_url)
    admin.login("admin", args.password_file.read_text().strip())
    unique = uuid.uuid4().hex[:12]
    password = "Fictional-Runtime-Test-Only!"
    u = admin.call(
        "/users",
        "POST",
        {
            "username": "verify" + unique,
            "display_name": "重启验收",
            "password": password,
        },
    )
    user = Client(args.base_url)
    user.login(u["username"], password)
    user.call(
        "/auth/password",
        "PUT",
        {"current_password": password, "new_password": password + "new"},
    )
    user.login(u["username"], password + "new")
    email = f"verify-{unique}@example.test"
    admin.call(
        "/account-imports",
        "POST",
        {
            "tier": "20x",
            "text": f"{email}----Runtime-Test-Secret----Runtime-Test-Auth",
            "user_ids": [u["id"]],
            "quota_reset_interval_days": 3,
        },
    )
    a = next(a for a in admin.call("/accounts") if a["email"] == email)
    default_backend = admin.call("/mail-backends")["default"]
    assert a["mail_backend"] == default_backend
    admin.call(
        "/account-imports",
        "POST",
        {
            "tier": "5x",
            "text": f"disabled-{email}----Runtime-Test-Secret----Runtime-Test-Auth",
            "mail_backend": None,
        },
    )
    disabled_account = next(
        item for item in admin.call("/accounts") if item["email"] == f"disabled-{email}"
    )

    def check_mail_backends():
        assert admin.call(f"/accounts/{a['id']}")["mail_backend"] == default_backend
        assert (
            admin.call(f"/accounts/{a['id']}/verification")["email_config_version"] == 0
        )
        assert admin.call(f"/accounts/{disabled_account['id']}")["mail_backend"] is None
        status = admin.call(f"/accounts/{disabled_account['id']}/verification")
        assert not status["email_available"] and status["email_config_version"] == 0

    c = user.call("/claims", "POST", {"account_id": a["id"]})
    user.call(
        f"/accounts/{a['id']}/quota-reports",
        "POST",
        {
            "quota": 0,
            "reset_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    user.call(
        f"/accounts/{a['id']}/health-reports",
        "POST",
        {"action": "report", "version": 0, "note": "重启与恢复验收"},
    )
    # Committed fixture contains a public RFC test key, never a real account key.
    qr = (ROOT / "frontend/e2e/totp-fixture.png").read_bytes()
    totp_path = f"/accounts/{a['id']}/two-factor/service"
    for method, payload in (("PUT", qr), ("DELETE", None)):
        try:
            user.call(totp_path + "?version=0", method, payload)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            exc.close()
        else:
            raise AssertionError("Ordinary users must not manage 2FA")
    admin.call(totp_path + "?version=0", "PUT", qr)

    def check_totp():
        status = user.call(f"/accounts/{a['id']}/verification")
        assert set(status["two_factor"]) == {"service"}
        assert status["two_factor"]["service"]["configured"]
        assert status["two_factor"]["service"]["version"] == 1
        assert len(user.call(totp_path + "/code")["code"]) == 6

    check_totp()
    check_mail_backends()
    secrets = ROOT / ".local/secrets"
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in secrets.iterdir()
        if p.is_file()
    }
    script("init.py")
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in secrets.iterdir()
        if p.is_file()
    }
    bundle = ROOT / ".local/backups" / ("verify-" + unique)
    script("backup.py", bundle)
    print("PASS: backup and idempotent initialization", flush=True)
    compose("stop", "worker")
    run = user.call(
        f"/accounts/{a['id']}/email-code-runs", "POST", {"id": str(uuid.uuid4())}
    )
    compose("stop", "api")
    # Committed overdue deadlines emulate time elapsed while all app processes stop.
    sql = (
        f"UPDATE accounts SET reset_at=NOW()-INTERVAL '1 hour', anomaly_since=NOW()-INTERVAL '25 hours' WHERE id='{a['id']}'; "
        f"UPDATE email_code_runs SET deadline=NOW()-INTERVAL '1 second' WHERE id='{run['id']}';"
    )
    compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "account_manager",
        "-d",
        "account_manager",
        "-c",
        sql,
    )
    compose("kill", "-s", "SIGKILL", "db")
    compose("up", "-d", "--wait", "db")
    compose("up", "-d", "--no-deps", "--force-recreate", "api", "worker")

    def recovered():
        account = user.call(f"/accounts/{a['id']}")
        return (
            account["quota"] == 100
            and account["health"] == "possibly_recovered"
            and account["reset_at"] is not None
            and datetime.fromisoformat(account["reset_at"]) > datetime.now(timezone.utc)
        )

    eventually(recovered)
    assert user.call("/auth/me")["user"]["claims_used"] == 1
    assert (
        user.call(f"/accounts/{a['id']}/credentials")["auth_password"]
        == "Runtime-Test-Auth"
    )
    check_totp()
    check_mail_backends()
    assert (
        user.call(f"/accounts/{a['id']}/email-code-runs/{run['id']}")["status"]
        == "timed_out"
    )
    assert user.call(f"/accounts/{a['id']}/verification")["email"] is None
    events = admin.call(f"/accounts/{a['id']}/events")
    assert sum(e["kind"] == "quota_reset" for e in events) == 1
    reset = next(e["details"] for e in events if e["kind"] == "quota_reset")
    assert reset["interval_days"] == 3
    next_reset = datetime.fromisoformat(reset["next_reset_at"])
    assert next_reset == datetime.fromisoformat(reset["reset_at"]) + timedelta(days=3)
    assert (
        datetime.fromisoformat(user.call(f"/accounts/{a['id']}")["reset_at"])
        == next_reset
    )
    assert sum(e["kind"] == "health_possible" for e in events) == 1
    print(
        "PASS: database SIGKILL, app recreation, retained session/claim/credentials/TOTP, expired email job and missed timers",
        flush=True,
    )
    user.call(f"/accounts/{a['id']}/quota-reports", "POST", {"quota": 17})
    removed = admin.call(totp_path + "?version=1", "DELETE")
    admin.call(f"/accounts/{a['id']}", "PATCH", {"mail_backend": None})
    admin.call(
        f"/accounts/{disabled_account['id']}",
        "PATCH",
        {"mail_backend": default_backend},
    )
    assert not removed["configured"] and removed["version"] == 2
    admin.call(f"/claims/{c['id']}/revocation", "PUT", {"reason": "恢复前的测试变更"})
    script("restore.py", bundle, "--confirm")
    eventually(lambda: user.call("/auth/me")["user"]["claims_used"] == 1)
    restored = user.call(f"/accounts/{a['id']}")
    assert restored["quota"] == 0 and restored["health"] == "abnormal"
    assert restored["quota_reset_interval_days"] == 3
    assert user.call("/claims")[0]["invalidated_at"] is None
    check_totp()
    check_mail_backends()
    assert (
        user.call(f"/accounts/{a['id']}/credentials")["password"]
        == "Runtime-Test-Secret"
    )
    assert not any(
        e["kind"] == "quota_reset" for e in admin.call(f"/accounts/{a['id']}/events")
    )
    print(
        "PASS: full restore rolls database, sessions, claims, timers, decryptable credentials, service TOTP and mailbox selections/versions back to backup",
        flush=True,
    )
    admin.call(f"/users/{u['id']}", "DELETE")
    admin.call(f"/accounts/{a['id']}", "DELETE", {"reason": "验收完成"})


if __name__ == "__main__":
    main()
