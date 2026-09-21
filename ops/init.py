#!/usr/bin/env python3
"""Generate local secrets once. Never overwrite existing secret files."""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
local = root / ".local"
local.mkdir(mode=0o700, exist_ok=True)
local.chmod(0o700)
folder = local / "secrets"
folder.mkdir(mode=0o700, exist_ok=True)
for name, value in {
    "db_password": secrets.token_urlsafe(32),
    "credential_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "bootstrap_password": secrets.token_urlsafe(18),
}.items():
    path = folder / name
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as f:
            f.write(value + "\n")
print("Secrets ready. Initial admin password: .local/secrets/bootstrap_password")
