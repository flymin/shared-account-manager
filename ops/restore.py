#!/usr/bin/env python3
"""Restore a verified recovery bundle. Explicit --confirm is required."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def compose(*args, **kwargs):
    return subprocess.run(
        [str(ROOT / "ops/compose.sh"), *args], cwd=ROOT, check=True, **kwargs
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", type=Path)
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Replace the current database and encryption key",
    )
    args = p.parse_args()
    if not args.confirm:
        p.error("--confirm is required; this replaces current data")
    bundle = args.bundle.resolve()
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("format") != 1:
        p.error("Unsupported backup format")
    for name in ("database.dump", "credential_key"):
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != manifest[
            "sha256"
        ].get(name):
            p.error("Backup checksum mismatch; no changes made")
    # pg_restore commits atomically. Keep writers stopped if any operation fails.
    compose("stop", "web", "api", "worker")
    compose("up", "-d", "--wait", "db")
    with (bundle / "database.dump").open("rb") as source:
        compose(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "account_manager",
            "-d",
            "account_manager",
            "--clean",
            "--if-exists",
            "--single-transaction",
            stdin=source,
        )
    secret = ROOT / ".local/secrets/credential_key"
    os.chmod(secret, 0o600)
    secret.write_bytes((bundle / "credential_key").read_bytes())
    os.chmod(secret, 0o444)
    compose("run", "--rm", "init")
    compose("up", "-d", "--force-recreate", "api", "worker", "web")
    print(
        "Restore complete. Verify /api/ready and a known account before resuming use."
    )


if __name__ == "__main__":
    main()
